/*
 * cosmos-wm: CosmOS's Wayland compositor (wlroots). Wayland-only - there is
 * no X11 window manager, session or tray anywhere in CosmOS's own code any
 * more; third-party X11 programs (Wine, Steam, the JetBrains IDEs, GameBox as
 * it stands today) run through a hidden XWayland shim (see the XWayland
 * section) and nothing else.
 *
 * It keeps the window model of the X11 cosmos-wm it replaces: exactly one
 * ordinary window is ever visible, maximized to whatever screen area the
 * panels leave over, and switching between open windows is a "swappable
 * list". What changes is how the pieces talk to the compositor:
 *
 *   - Panels and the desktop use wlr-layer-shell instead of X11 dock
 *     windows + _NET_WM_STRUT: cosmos-bar's three bars anchor to the edges
 *     with exclusive zones (the usable area shrinks for windows), and the
 *     launcher lives on the BACKGROUND layer - it is permanently open behind
 *     every window and is what you see when nothing else is open or after
 *     "show desktop". When no window has focus, keyboard focus falls back to
 *     the top-most background/bottom layer surface that accepts it, which is
 *     how the launcher gets its controller/keyboard input.
 *   - Window switching behaves exactly as the X11 cosmos-wm did: holding Alt
 *     and tapping Tab opens a centered list overlay of every open window
 *     (most recently used first) and cycles the highlight; releasing Alt
 *     commits, Escape cancels; Alt+Shift+Tab cycles backwards; Alt+X closes
 *     the active window. The list is windows only. The Windows (Super) key -
 *     and a controller's Guide button - toggles the gridscreen between the
 *     top and the bottom of the stack: a tap raises it over every window (which
 *     are not minimized or moved), the next tap puts it behind them again with
 *     your window back in front. Other processes (cosmos-bar's Apps button, the
 *     launcher, the bar's favorites) reach the same actions through a small
 *     line-based control socket ($XDG_RUNTIME_DIR/cosmos-wm.sock:
 *     "windows", "activate ID", "close ID", "desktop", "output", "geometry", "modes", "scale [VALUE|auto]", "mode WxH", "animations [on|off]", "lock", "unlock", "quit") - the
 *     Wayland stand-in for _NET_CLIENT_LIST / _NET_ACTIVE_WINDOW.
 *   - Dialogs (xdg toplevels with a parent) float centered over their parent
 *     at their own size and stay out of the switch list.
 *   - Server-side decorations are requested but nothing draws them: a
 *     maximized single-window desktop has no use for a titlebar.
 *
 * Tested headless (WLR_BACKENDS=headless) - see config/wm/test_cosmos_wm.sh.
 */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <getopt.h>
#include <math.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
#include <wayland-server-core.h>
#include "output_scale.h"
#include "motion.h"
#include <wlr/backend.h>
#include <wlr/backend/libinput.h>
#include <wlr/interfaces/wlr_keyboard.h>
#include <wlr/render/allocator.h>
#include <wlr/render/wlr_renderer.h>
#include <wlr/types/wlr_compositor.h>
#include <wlr/types/wlr_cursor.h>
#include <wlr/types/wlr_data_control_v1.h>
#include <wlr/types/wlr_data_device.h>
#include <wlr/types/wlr_fractional_scale_v1.h>
#include <wlr/types/wlr_input_device.h>
#include <wlr/types/wlr_keyboard.h>
#include <wlr/types/wlr_layer_shell_v1.h>
#include <wlr/types/wlr_output.h>
#include <wlr/types/wlr_output_layout.h>
#include <wlr/types/wlr_pointer.h>
#include <wlr/types/wlr_presentation_time.h>
#include <wlr/types/wlr_primary_selection.h>
#include <wlr/types/wlr_primary_selection_v1.h>
#include <wlr/types/wlr_scene.h>
#include <wlr/types/wlr_screencopy_v1.h>
#include <wlr/types/wlr_seat.h>
#include <wlr/types/wlr_single_pixel_buffer_v1.h>
#include <wlr/types/wlr_subcompositor.h>
#include <wlr/types/wlr_viewporter.h>
#include <wlr/types/wlr_virtual_keyboard_v1.h>
#include <wlr/types/wlr_xcursor_manager.h>
#include <wlr/types/wlr_server_decoration.h>
#include <wlr/types/wlr_xdg_decoration_v1.h>
#include <wlr/types/wlr_xdg_output_v1.h>
#include <wlr/types/wlr_xdg_shell.h>
#include <wlr/util/log.h>
#include <wlr/xwayland/xwayland.h>
#include <xkbcommon/xkbcommon.h>
#include <cairo.h>
#include <pango/pangocairo.h>
#include <drm_fourcc.h>
#include <wlr/interfaces/wlr_buffer.h>

/* Layer order in the scene graph, bottom to top:
 * background < bottom < windows < home < top < fullscreen < home-over-fullscreen < overlay
 * The gridscreen (the launcher) normally sits in "background". The Windows key moves it up to "home" (over the windows, under the
 * bar) or, if a fullscreen window is showing, to "home-over-fullscreen"; pressing again puts it back down. */
enum {
	SCENE_BACKGROUND, SCENE_BOTTOM, SCENE_WINDOWS, SCENE_HOME, SCENE_TOP, SCENE_FULLSCREEN, SCENE_HOME_FULLSCREEN, SCENE_OVERLAY,
	SCENE_COUNT,
};

struct server;

struct output {
	struct wl_list link;
	struct server *server;
	struct wlr_output *wlr_output;
	struct wlr_scene_output *scene_output;
	struct wl_list layers[4]; /* struct layer, per zwlr_layer_shell_v1_layer */
	struct wlr_box full_area;
	struct wlr_box usable_area;
	struct wl_listener frame;
	struct wl_listener request_state;
	struct wl_listener destroy;
};

struct window {
	struct wl_list link; /* server->windows, most recently used first */
	struct server *server;
	uint32_t id;
	struct wlr_xdg_toplevel *xdg_toplevel; /* exactly one of these two is set */
	struct wlr_xwayland_surface *xsurface;
	struct wlr_scene_tree *scene_tree;
	bool dialog;
	bool minimized;
	bool mapped;
	bool fullscreen;
	struct wlr_xdg_toplevel_decoration_v1 *deco;
	struct wl_listener map;
	struct wl_listener unmap;
	struct wl_listener commit;
	struct wl_listener destroy;
	struct wl_listener request_maximize;
	struct wl_listener request_fullscreen;
	struct wl_listener associate; /* xwayland only */
	struct wl_listener dissociate;
	struct wl_listener request_configure;
};

struct layer {
	struct wl_list link; /* output->layers[layer] */
	struct server *server;
	struct output *output;
	struct wlr_layer_surface_v1 *layer_surface;
	struct wlr_scene_layer_surface_v1 *scene;
	bool mapped;
	struct wl_listener map;
	struct wl_listener unmap;
	struct wl_listener commit;
	struct wl_listener destroy;
	struct wl_listener new_popup;
};

struct popup {
	struct wlr_xdg_popup *xdg_popup;
	struct wl_listener commit;
	struct wl_listener destroy;
};

struct keyboard {
	struct wl_list link;
	struct server *server;
	struct wlr_keyboard *wlr_keyboard;
	struct wl_listener modifiers;
	struct wl_listener key;
	struct wl_listener destroy;
};

struct ipc_client {
	struct wl_event_source *source;
	struct server *server;
	int fd;
	char buf[512];
	size_t len;
	/* a connection that said "subscribe keys" stays open: it is sent the keys typed while its window has focus (which no window then
	 * gets), and it may inject touch points into that window. Used by the Android button mapper. */
	struct wl_list link;
	bool subscribed;
	char app_id[64]; /* only while a window with this app id is focused; empty = whenever */
	uint16_t touches_down; /* bit per touch id this connection put down, released when it goes away */
};

struct server {
	struct wl_display *wl_display;
	struct wlr_backend *backend;
	struct wlr_renderer *renderer;
	struct wlr_allocator *allocator;
	struct wlr_scene *scene;
	struct wlr_scene_output_layout *scene_layout;
	struct wlr_scene_tree *tree[SCENE_COUNT];

	struct wlr_compositor *compositor;
	struct wlr_xwayland *xwayland;
	struct wl_listener new_xwayland_surface;
	struct wlr_xdg_shell *xdg_shell;
	struct wl_listener new_xdg_toplevel;
	struct wl_listener new_xdg_popup;
	struct wlr_xdg_decoration_manager_v1 *decoration_mgr;
	struct wl_listener new_decoration;
	struct wlr_layer_shell_v1 *layer_shell;
	struct wl_listener new_layer_surface;

	struct wl_list windows; /* MRU order */
	uint32_t next_window_id;
	struct window *active; /* window holding keyboard focus, or NULL */
	struct layer *home_raised; /* the gridscreen while it is raised over the windows, else NULL */
	struct wlr_scene_tree *home_origin; /* the scene tree it goes back to */

	struct wlr_cursor *cursor;
	struct wlr_xcursor_manager *cursor_mgr;
	struct wl_listener cursor_motion;
	struct wl_listener cursor_motion_absolute;
	struct wl_listener cursor_button;
	struct wl_listener cursor_axis;
	struct wl_listener cursor_frame;

	struct wlr_seat *seat;
	struct wl_listener new_input;
	struct wl_listener request_cursor;
	struct wl_listener pointer_focus_change;
	struct wl_listener request_set_selection;
	struct wl_listener request_set_primary_selection;
	struct wl_list keyboards;
	struct wlr_virtual_keyboard_manager_v1 *virtual_keyboard_mgr;
	struct wl_listener new_virtual_keyboard;

	struct wlr_output_layout *output_layout;
	struct wl_list outputs;
	struct wl_listener new_output;

	bool locked; /* screen lock: only the lock screen (namespace cosmos-lockscreen*) is shown or focusable */
	struct wl_event_source *lock_timer;
	int ipc_fd;
	char ipc_path[sizeof(((struct sockaddr_un *)0)->sun_path)];
	struct wl_event_source *ipc_source;
};

static struct server server;

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

static struct output *primary_output(void) {
	if (wl_list_empty(&server.outputs)) {
		return NULL;
	}
	struct output *out = wl_container_of(server.outputs.next, out, link);
	return out;
}

/* ------------------------------------------------------------------ */
/* animations                                                          */
/* ------------------------------------------------------------------ */

/* Windows and layer surfaces (the bar, the launcher, the lock screen, the gridscreen) fade in from transparent over ANIM_MS when they appear; the
 * Alt+Tab switcher additionally fades OUT the same way when it closes, its destruction held until the fade finishes - the one place a fade-out is
 * both visible (nothing occludes it) and safe to build (it is a buffer the compositor itself draws, with no client or protocol connection of its
 * own that a bug here could ever put at risk). A window that is merely covered by another, or a layer reparented behind the stack, is not faded
 * out - it would be invisible the instant something opaque is drawn over it regardless of its own opacity, so there is nothing for a fade to show.
 *
 * Memory safety, on purpose, after the crash the first attempt at this caused: a fade snapshots the wlr_scene_buffer pointers under its node ONCE,
 * when it starts (one tree walk, not one every 60fps tick), each with its OWN destroy listener - a subsurface that closes mid-fade just drops out
 * of the snapshot, nothing else is touched. The anim is always fully detached (every listener removed) BEFORE its on_done runs, so on_done is free
 * to destroy the very node the fade was for without the anim's own, by-then-stale destroy listener ever firing again on it. Starting a new fade on
 * a node that is already fading cancels the old one outright (its on_done, if any, never runs) rather than trying to merge two in-flight fades.
 * `animations = off` in display.conf, COSMOS_ANIMATIONS=0, or the control socket's `animations off` skip the ramp and jump straight to the end
 * state, running on_done at once. */

#define ANIM_MAX_BUFFERS 32

struct anim_buffer_slot {
	struct wlr_scene_buffer *buffer;    /* NULL once its own destroy fires, or the slot is unused */
	struct wl_listener destroy;
};

struct anim {
	struct wl_list link;                /* anims */
	struct anim_buffer_slot slots[ANIM_MAX_BUFFERS];
	int slot_count;
	struct wlr_scene_node *node;        /* only ever compared, never dereferenced (the node may already be gone by the time on_done runs) */
	struct wl_listener node_destroy;
	int64_t start_ms;
	int duration_ms;
	float from, to;
	void (*on_done)(void *);
	void *on_done_data;
};

static struct wl_list anims;
static struct wl_event_source *anim_timer;
static bool animations_on = true;
static float anim_time_scale = 1.0f;

static int64_t mono_ms(void) {
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void anim_apply(struct anim_buffer_slot *slots, int count, float opacity) {
	for (int i = 0; i < count; i++) {
		if (slots[i].buffer) {
			wlr_scene_buffer_set_opacity(slots[i].buffer, opacity);
		}
	}
}

static void anim_buffer_destroyed(struct wl_listener *listener, void *data) {
	struct anim_buffer_slot *slot = wl_container_of(listener, slot, destroy);
	wl_list_remove(&slot->destroy.link);
	slot->buffer = NULL;
}

/* Detaches every listener the anim owns and takes it off the list. Always called before on_done, so on_done can safely destroy the node (or
 * anything under it) without anim_node_destroyed firing again on an anim that is already gone. Does not call on_done and does not free `a`. */
static void anim_detach(struct anim *a) {
	wl_list_remove(&a->link);
	wl_list_remove(&a->node_destroy.link);
	for (int i = 0; i < a->slot_count; i++) {
		if (a->slots[i].buffer) {
			wl_list_remove(&a->slots[i].destroy.link);
		}
	}
}

static void anim_node_destroyed(struct wl_listener *listener, void *data) {
	/* The node went away for some OTHER reason mid-fade (its window closed while fading in, say) - never our own on_done, which always detaches
	 * first. Nothing to finish; just stop tracking it. */
	struct anim *a = wl_container_of(listener, a, node_destroy);
	anim_detach(a);
	free(a);
}

static struct anim *anim_find(struct wlr_scene_node *node) {
	struct anim *a;
	wl_list_for_each(a, &anims, link) {
		if (a->node == node) {
			return a;
		}
	}
	return NULL;
}

static void anim_collect_cb(struct wlr_scene_buffer *buffer, int sx, int sy, void *data) {
	struct anim *a = data;
	if (a->slot_count >= ANIM_MAX_BUFFERS) {
		wlr_log(WLR_DEBUG, "cosmos-wm: an animated node has more than %d buffers; the rest won't fade", ANIM_MAX_BUFFERS);
		return;
	}
	struct anim_buffer_slot *slot = &a->slots[a->slot_count++];
	slot->buffer = buffer;
	slot->destroy.notify = anim_buffer_destroyed;
	wl_signal_add(&buffer->node.events.destroy, &slot->destroy);
}

static int anim_tick(void *data) {
	int64_t now = mono_ms();
	struct anim *a, *tmp;
	wl_list_for_each_safe(a, tmp, &anims, link) {
		float t = (float)(now - a->start_ms) / (float)a->duration_ms;
		if (t >= 1.0f) {
			anim_apply(a->slots, a->slot_count, a->to);
			void (*on_done)(void *) = a->on_done;
			void *on_done_data = a->on_done_data;
			anim_detach(a);
			free(a);                                       /* detached and freed BEFORE on_done runs - see anim_detach's own comment */
			if (on_done) {
				on_done(on_done_data);
			}
		} else {
			anim_apply(a->slots, a->slot_count, lerp_float(a->from, a->to, ease_out_cubic(t)));
		}
	}
	if (!wl_list_empty(&anims)) {
		wl_event_source_timer_update(anim_timer, 16);
	}
	return 0;
}

/* Starts a fade on `node` from `from` to `to` opacity over ANIM_MS, running on_done (if given) once it reaches `to` - after the anim is fully torn
 * down, so on_done may destroy the node itself. A fade already running on this node is cancelled first (its own on_done, if any, never runs).
 * With animations off, or no node, the end state is applied immediately (a one-off buffer walk, no anim kept around) and on_done runs at once, so
 * a caller never has to treat "animations off" as a separate case. */
static void anim_fade(struct wlr_scene_node *node, float from, float to, void (*on_done)(void *), void *on_done_data) {
	struct anim *existing = node ? anim_find(node) : NULL;
	if (existing) {
		anim_detach(existing);
		free(existing);
	}
	if (!node) {
		if (on_done) {
			on_done(on_done_data);
		}
		return;
	}
	if (!animations_on) {
		struct anim throwaway = {0};
		wlr_scene_node_for_each_buffer(node, anim_collect_cb, &throwaway);
		anim_apply(throwaway.slots, throwaway.slot_count, to);
		for (int i = 0; i < throwaway.slot_count; i++) {
			if (throwaway.slots[i].buffer) {
				wl_list_remove(&throwaway.slots[i].destroy.link);
			}
		}
		if (on_done) {
			on_done(on_done_data);
		}
		return;
	}
	struct anim *a = calloc(1, sizeof(*a));
	if (!a) {
		if (on_done) {
			on_done(on_done_data);
		}
		return;
	}
	a->node = node;
	a->from = from;
	a->to = to;
	a->start_ms = mono_ms();
	a->duration_ms = (int)(ANIM_MS * anim_time_scale);
	a->on_done = on_done;
	a->on_done_data = on_done_data;
	a->node_destroy.notify = anim_node_destroyed;
	wl_signal_add(&node->events.destroy, &a->node_destroy);
	wlr_scene_node_for_each_buffer(node, anim_collect_cb, a);
	anim_apply(a->slots, a->slot_count, from);
	wl_list_insert(&anims, &a->link);
	if (!anim_timer) {
		anim_timer = wl_event_loop_add_timer(wl_display_get_event_loop(server.wl_display), anim_tick, NULL);
	}
	wl_event_source_timer_update(anim_timer, 1);
}

static void anim_fade_in(struct wlr_scene_node *node) {
	anim_fade(node, 0.0f, 1.0f, NULL, NULL);
}

static void anims_set(bool on) {
	animations_on = on;
	if (!on) {                                                        /* settle everything that is mid-fade */
		struct anim *a, *tmp;
		wl_list_for_each_safe(a, tmp, &anims, link) {
			anim_apply(a->slots, a->slot_count, a->to);
			void (*on_done)(void *) = a->on_done;
			void *on_done_data = a->on_done_data;
			anim_detach(a);
			free(a);
			if (on_done) {
				on_done(on_done_data);
			}
		}
	}
}

/* Double-fork so the child is reparented to init and never needs reaping
 * here. SIGCHLD must stay at its default: SIG_IGN is inherited by Xwayland,
 * where it makes waiting on xkbcomp fail and Xwayland refuse to start. */
static void spawn(const char *command) {
	pid_t pid = fork();
	if (pid == 0) {
		if (fork() == 0) {
			setsid();
			execl("/bin/sh", "/bin/sh", "-c", command, (void *)NULL);
			_exit(127);
		}
		_exit(0);
	} else if (pid > 0) {
		waitpid(pid, NULL, 0);
	}
}

static struct wlr_surface *window_surface(struct window *w) {
	return w->xsurface ? w->xsurface->surface : w->xdg_toplevel->base->surface;
}

static void surface_set_activated(struct wlr_surface *surface, bool activated) {
	struct wlr_xdg_toplevel *top = wlr_xdg_toplevel_try_from_wlr_surface(surface);
	if (top) {
		wlr_xdg_toplevel_set_activated(top, activated);
		return;
	}
	struct wlr_xwayland_surface *xs = wlr_xwayland_surface_try_from_wlr_surface(surface);
	if (xs && !xs->override_redirect) {
		wlr_xwayland_surface_activate(xs, activated);
	}
}

/* ------------------------------------------------------------------ */
/* focus                                                               */
/* ------------------------------------------------------------------ */

/* ---- screen lock -------------------------------------------------
 * While locked every window, panel and popup is hidden (disabled in the scene,
 * so they are neither drawn nor hit-tested) and keyboard focus is pinned to the
 * lock screen's layer surface. The lock screen is an ordinary layer-shell
 * client (namespace "cosmos-lockscreen") on the OVERLAY layer; it authenticates
 * against PAM itself and asks for "unlock" over the control socket. If it dies
 * while locked the compositor stays locked and starts a new one. */
static bool layer_is_lock(struct layer *l) {
	const char *ns = l->layer_surface->namespace;
	return ns && !strncmp(ns, "cosmos-lockscreen", 17);
}

/* The on-screen keyboard stays usable while locked (typing the password with a
 * controller): it never takes keyboard focus - it only types into whatever has
 * it, which is the lock screen. */
static bool layer_shown_while_locked(struct layer *l) {
	const char *ns = l->layer_surface->namespace;
	return layer_is_lock(l) || (ns && !strcmp(ns, "cosmos-osk"));
}

static struct layer *lock_layer(void) {
	struct output *o;
	wl_list_for_each(o, &server.outputs, link) {
		struct layer *l;
		wl_list_for_each(l, &o->layers[ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY], link) {
			if (l->mapped && layer_is_lock(l)) {
				return l;
			}
		}
	}
	return NULL;
}

static void apply_lock(void) {
	wlr_scene_node_set_enabled(&server.tree[SCENE_WINDOWS]->node, !server.locked);
	wlr_scene_node_set_enabled(&server.tree[SCENE_FULLSCREEN]->node, !server.locked);
	wlr_scene_node_set_enabled(&server.tree[SCENE_HOME]->node, !server.locked);
	wlr_scene_node_set_enabled(&server.tree[SCENE_HOME_FULLSCREEN]->node, !server.locked);
	struct output *o;
	wl_list_for_each(o, &server.outputs, link) {
		for (int i = 0; i < 4; i++) {
			struct layer *l;
			wl_list_for_each(l, &o->layers[i], link) {
				wlr_scene_node_set_enabled(&l->scene->tree->node, !server.locked || layer_shown_while_locked(l));
			}
		}
	}
}

/* $XDG_RUNTIME_DIR/cosmos-home-shown exists while the gridscreen is what is on screen (no
 * window is active). The bar and launcher hide themselves while GameBox runs - unless
 * this file says the person chose to come back to the gridscreen (Windows key / Guide). */
static void set_home_shown(bool shown) {
	const char *rt = getenv("XDG_RUNTIME_DIR");
	if (!rt) {
		return;
	}
	char path[512];
	snprintf(path, sizeof(path), "%s/cosmos-home-shown", rt);
	if (shown) {
		int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
		if (fd >= 0) {
			close(fd);
		}
	} else {
		unlink(path);
	}
}

static void seat_focus_surface(struct wlr_surface *surface) {
	struct wlr_seat *seat = server.seat;
	if (server.locked) {
		struct layer *ll = lock_layer();
		surface = ll ? ll->layer_surface->surface : NULL;
	}
	struct wlr_surface *prev = seat->keyboard_state.focused_surface;
	if (prev == surface) {
		return;
	}
	if (prev) {
		surface_set_activated(prev, false);
	}
	if (surface == NULL) {
		wlr_seat_keyboard_notify_clear_focus(seat);
		return;
	}
	surface_set_activated(surface, true);
	struct wlr_keyboard *kb = wlr_seat_get_keyboard(seat);
	if (kb) {
		wlr_seat_keyboard_notify_enter(seat, surface, kb->keycodes, kb->num_keycodes, &kb->modifiers);
	} else {
		wlr_seat_keyboard_notify_enter(seat, surface, NULL, 0, NULL);
	}
}

/* The surface that should get focus when no window claims it: the top-most
 * background/bottom layer surface that wants keyboard input - the launcher. */
static struct wlr_surface *default_focus_surface(void) {
	struct output *out = primary_output();
	if (!out) {
		return NULL;
	}
	for (int layer = ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM; layer >= ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND; layer--) {
		struct layer *l;
		wl_list_for_each(l, &out->layers[layer], link) {
			if (l->mapped && l->layer_surface->current.keyboard_interactive != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE) {
				return l->layer_surface->surface;
			}
		}
	}
	return NULL;
}

static bool seat_has_focus_visible(void) {
	return server.seat->keyboard_state.focused_surface != NULL;
}

static struct window *next_visible_window(void) {
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		if (w->mapped && !w->minimized) {
			return w;
		}
	}
	return NULL;
}

static void focus_default(void) {
	if (server.locked) {
		seat_focus_surface(NULL); /* redirected to the lock screen */
		return;
	}
	if (server.home_raised) {
		/* The gridscreen is up over whatever else is still open - closing the window that had focus (Settings, say) must not silently
		 * hand the keyboard to some OTHER window still hidden behind the grid. You would be typing into something you cannot see,
		 * and the grid itself would stop responding to a single keystroke, indistinguishable from having died. */
		seat_focus_surface(server.home_raised->layer_surface->surface);
		return;
	}
	struct window *w = next_visible_window();
	if (w) {
		server.active = w;
		wlr_scene_node_raise_to_top(&w->scene_tree->node);
		seat_focus_surface(window_surface(w));
		return;
	}
	server.active = NULL;
	set_home_shown(true);
	seat_focus_surface(default_focus_surface());
}

static void window_activate(struct window *w);

/* The gridscreen: the mapped background/bottom layer surface that takes keyboard input (the launcher). */
static struct layer *home_layer(void) {
	struct output *out = primary_output();
	if (!out) {
		return NULL;
	}
	for (int layer = ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM; layer >= ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND; layer--) {
		struct layer *l;
		wl_list_for_each(l, &out->layers[layer], link) {
			if (l->mapped && l->layer_surface->current.keyboard_interactive != ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_NONE) {
				return l;
			}
		}
	}
	return NULL;
}

/* Put the gridscreen back behind the windows. With refocus, the window you were in gets the keyboard back. */
static void home_lower(bool refocus) {
	struct layer *l = server.home_raised;
	if (!l) {
		return;
	}
	server.home_raised = NULL;
	wlr_scene_node_reparent(&l->scene->tree->node, server.home_origin ? server.home_origin : server.tree[SCENE_BACKGROUND]);
	server.home_origin = NULL;
	if (server.active != NULL) {
		set_home_shown(false);
	}
	if (refocus) {
		focus_default();
	}
}

/* Bring the gridscreen up over everything you have open (windows are not minimized or moved). */
static void home_raise(void) {
	struct layer *l = home_layer();
	if (!l || server.locked) {
		return;
	}
	bool over_fullscreen = false;
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		if (w->mapped && !w->minimized && w->fullscreen) {
			over_fullscreen = true;
		}
	}
	server.home_raised = l;
	server.home_origin = l->scene->tree->node.parent;
	wlr_scene_node_reparent(&l->scene->tree->node, server.tree[over_fullscreen ? SCENE_HOME_FULLSCREEN : SCENE_HOME]);
	wlr_scene_node_raise_to_top(&l->scene->tree->node);
	anim_fade_in(&l->scene->tree->node); /* the gridscreen comes up over your windows */
	set_home_shown(true);
	seat_focus_surface(l->layer_surface->surface);
}

/* The Windows key (and a controller's Guide button): the gridscreen goes to the top of the stack, or, if it is already there, back to the
 * bottom with your window in front of it again. With nothing showing but minimized windows, it brings back the last one. */
static void home_toggle(void) {
	if (server.home_raised) {
		home_lower(true);
		return;
	}
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		if (w->mapped && !w->minimized && !w->dialog) {
			home_raise();
			return;
		}
	}
	wl_list_for_each(w, &server.windows, link) {
		if (w->mapped && !w->dialog) {
			window_activate(w);
			return;
		}
	}
}

/* ------------------------------------------------------------------ */
/* window geometry                                                     */
/* ------------------------------------------------------------------ */

static void window_arrange(struct window *w) {
	struct output *out = primary_output();
	if (!out || !w->mapped) {
		return;
	}
	struct wlr_box area = w->fullscreen ? out->full_area : out->usable_area;
	if (w->xsurface) {
		struct wlr_xwayland_surface *xs = w->xsurface;
		if (w->dialog) {
			int x = out->usable_area.x + (out->usable_area.width - xs->width) / 2;
			int y = out->usable_area.y + (out->usable_area.height - xs->height) / 2;
			wlr_scene_node_set_position(&w->scene_tree->node, x, y);
			wlr_xwayland_surface_configure(xs, x, y, xs->width, xs->height);
		} else {
			wlr_scene_node_set_position(&w->scene_tree->node, area.x, area.y);
			wlr_xwayland_surface_configure(xs, area.x, area.y, area.width, area.height);
			wlr_xwayland_surface_set_maximized(xs, true, true);
			wlr_xwayland_surface_set_fullscreen(xs, w->fullscreen);
		}
		return;
	}
	if (w->dialog) {
		/* floats centered at its own size over the usable area */
		struct wlr_box geo = w->xdg_toplevel->base->geometry;
		int x = out->usable_area.x + (out->usable_area.width - geo.width) / 2;
		int y = out->usable_area.y + (out->usable_area.height - geo.height) / 2;
		wlr_scene_node_set_position(&w->scene_tree->node, x - geo.x, y - geo.y);
		return;
	}
	wlr_scene_node_set_position(&w->scene_tree->node, area.x, area.y);
	if (w->xdg_toplevel->base->initialized) {
		wlr_xdg_toplevel_set_size(w->xdg_toplevel, area.width, area.height);
		wlr_xdg_toplevel_set_maximized(w->xdg_toplevel, true);
		wlr_xdg_toplevel_set_fullscreen(w->xdg_toplevel, w->fullscreen);
		wlr_xdg_toplevel_set_tiled(w->xdg_toplevel, WLR_EDGE_TOP | WLR_EDGE_BOTTOM | WLR_EDGE_LEFT | WLR_EDGE_RIGHT);
	}
}

static void window_reparent(struct window *w) {
	wlr_scene_node_reparent(&w->scene_tree->node, server.tree[w->fullscreen ? SCENE_FULLSCREEN : SCENE_WINDOWS]);
}

static void arrange_windows(void) {
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		window_arrange(w);
	}
}

/* ------------------------------------------------------------------ */
/* layer-shell arrangement                                             */
/* ------------------------------------------------------------------ */

static void arrange_layers(struct output *out) {
	struct wlr_box full = {0};
	wlr_output_layout_get_box(server.output_layout, out->wlr_output, &full);
	struct wlr_box usable = full;
	for (int layer = ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY; layer >= ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND; layer--) {
		for (int pass = 0; pass < 2; pass++) { /* exclusive-zone surfaces first */
			struct layer *l;
			wl_list_for_each(l, &out->layers[layer], link) {
				bool exclusive = l->layer_surface->current.exclusive_zone > 0;
				if ((pass == 0) != exclusive) {
					continue;
				}
				wlr_scene_layer_surface_v1_configure(l->scene, &full, &usable);
			}
		}
	}
	out->full_area = full;
	out->usable_area = usable;
	arrange_windows();
}

/* ------------------------------------------------------------------ */
/* windows: xdg-shell                                                  */
/* ------------------------------------------------------------------ */

static void window_activate(struct window *w) {
	if (!w->mapped) {
		return;
	}
	bool was_front = server.active == w && !server.home_raised && !w->minimized;
	if (w->minimized) {
		w->minimized = false;
		wlr_scene_node_set_enabled(&w->scene_tree->node, true);
	}
	home_lower(false); /* anything that brings a window forward (Alt+Tab, a launch, the bar) puts the gridscreen back behind it */
	wl_list_remove(&w->link);
	wl_list_insert(&server.windows, &w->link);
	server.active = w;
	if (!w->dialog) {
		set_home_shown(false);
	}
	wlr_scene_node_raise_to_top(&w->scene_tree->node);
	window_arrange(w);
	seat_focus_surface(window_surface(w));
	if (!was_front) {
		anim_fade_in(&w->scene_tree->node); /* a window that opens, or is switched/unminimized back to, fades in over what was there */
	}
}

static void window_minimize(struct window *w) {
	w->minimized = true;
	wlr_scene_node_set_enabled(&w->scene_tree->node, false);
}

static bool window_is_dialog(struct window *w) {
	if (w->xsurface) {
		struct wlr_xwayland_surface *xs = w->xsurface;
		return xs->parent != NULL || xs->modal ||
			wlr_xwayland_surface_has_window_type(xs, WLR_XWAYLAND_NET_WM_WINDOW_TYPE_DIALOG) ||
			wlr_xwayland_surface_has_window_type(xs, WLR_XWAYLAND_NET_WM_WINDOW_TYPE_UTILITY) ||
			wlr_xwayland_surface_has_window_type(xs, WLR_XWAYLAND_NET_WM_WINDOW_TYPE_SPLASH);
	}
	return w->xdg_toplevel->parent != NULL;
}

static bool window_wants_fullscreen(struct window *w) {
	return w->xsurface ? w->xsurface->fullscreen : w->xdg_toplevel->requested.fullscreen;
}

static void window_map(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, map);
	w->mapped = true;
	w->dialog = window_is_dialog(w);
	w->fullscreen = window_wants_fullscreen(w) && !w->dialog;
	window_reparent(w);
	wl_list_remove(&w->link);
	wl_list_insert(&server.windows, &w->link);
	window_activate(w);
}

static void window_unmap(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, unmap);
	w->mapped = false;
	if (server.active == w) {
		server.active = NULL;
		focus_default();
	}
}

static void window_commit(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, commit);
	if (w->xdg_toplevel->base->initial_commit) {
		if (w->deco) {
			wlr_xdg_toplevel_decoration_v1_set_mode(w->deco, WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
		}
		struct output *out = primary_output();
		w->dialog = w->xdg_toplevel->parent != NULL;
		if (out && !w->dialog) {
			struct wlr_box a = out->usable_area;
			wlr_xdg_toplevel_set_size(w->xdg_toplevel, a.width, a.height);
			wlr_xdg_toplevel_set_maximized(w->xdg_toplevel, true);
			wlr_xdg_toplevel_set_tiled(w->xdg_toplevel, WLR_EDGE_TOP | WLR_EDGE_BOTTOM | WLR_EDGE_LEFT | WLR_EDGE_RIGHT);
		} else {
			wlr_xdg_toplevel_set_size(w->xdg_toplevel, 0, 0);
		}
	} else if (w->mapped && w->dialog) {
		window_arrange(w); /* re-center as the dialog resizes itself */
	}
}

static void window_destroy(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, destroy);
	wl_list_remove(&w->map.link);
	wl_list_remove(&w->unmap.link);
	wl_list_remove(&w->commit.link);
	wl_list_remove(&w->destroy.link);
	wl_list_remove(&w->request_maximize.link);
	wl_list_remove(&w->request_fullscreen.link);
	if (w->xsurface) {
		wl_list_remove(&w->associate.link);
		wl_list_remove(&w->dissociate.link);
		wl_list_remove(&w->request_configure.link);
	}
	wl_list_remove(&w->link);
	if (server.active == w) {
		server.active = NULL;
		focus_default();
	}
	free(w);
}

static void window_request_maximize(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, request_maximize);
	if (w->xdg_toplevel->base->initialized) {
		wlr_xdg_surface_schedule_configure(w->xdg_toplevel->base);
	}
}

static void window_request_fullscreen(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, request_fullscreen);
	if (w->dialog) {
		return;
	}
	w->fullscreen = window_wants_fullscreen(w);
	if (w->mapped) {
		window_reparent(w);
		window_arrange(w);
	} else if (w->xdg_toplevel && w->xdg_toplevel->base->initialized) {
		wlr_xdg_surface_schedule_configure(w->xdg_toplevel->base);
	}
}

static void new_xdg_toplevel(struct wl_listener *listener, void *data) {
	struct wlr_xdg_toplevel *xdg_toplevel = data;
	struct window *w = calloc(1, sizeof(*w));
	w->server = &server;
	w->id = ++server.next_window_id;
	w->xdg_toplevel = xdg_toplevel;
	w->scene_tree = wlr_scene_xdg_surface_create(server.tree[SCENE_WINDOWS], xdg_toplevel->base);
	w->scene_tree->node.data = w;
	xdg_toplevel->base->data = w->scene_tree;
	wl_list_insert(&server.windows, &w->link);

	w->map.notify = window_map;
	wl_signal_add(&xdg_toplevel->base->surface->events.map, &w->map);
	w->unmap.notify = window_unmap;
	wl_signal_add(&xdg_toplevel->base->surface->events.unmap, &w->unmap);
	w->commit.notify = window_commit;
	wl_signal_add(&xdg_toplevel->base->surface->events.commit, &w->commit);
	w->destroy.notify = window_destroy;
	wl_signal_add(&xdg_toplevel->events.destroy, &w->destroy);
	w->request_maximize.notify = window_request_maximize;
	wl_signal_add(&xdg_toplevel->events.request_maximize, &w->request_maximize);
	w->request_fullscreen.notify = window_request_fullscreen;
	wl_signal_add(&xdg_toplevel->events.request_fullscreen, &w->request_fullscreen);
}

static void popup_commit(struct wl_listener *listener, void *data) {
	struct popup *p = wl_container_of(listener, p, commit);
	if (p->xdg_popup->base->initial_commit) {
		wlr_xdg_surface_schedule_configure(p->xdg_popup->base);
	}
}

static void popup_destroy(struct wl_listener *listener, void *data) {
	struct popup *p = wl_container_of(listener, p, destroy);
	wl_list_remove(&p->commit.link);
	wl_list_remove(&p->destroy.link);
	free(p);
}

static void add_popup(struct wlr_xdg_popup *xdg_popup, struct wlr_scene_tree *parent_tree) {
	struct popup *p = calloc(1, sizeof(*p));
	p->xdg_popup = xdg_popup;
	xdg_popup->base->data = wlr_scene_xdg_surface_create(parent_tree, xdg_popup->base);
	p->commit.notify = popup_commit;
	wl_signal_add(&xdg_popup->base->surface->events.commit, &p->commit);
	p->destroy.notify = popup_destroy;
	wl_signal_add(&xdg_popup->events.destroy, &p->destroy);
}

static void new_xdg_popup(struct wl_listener *listener, void *data) {
	struct wlr_xdg_popup *xdg_popup = data;
	if (xdg_popup->parent == NULL) {
		return; /* parented to a layer surface later; that layer's new_popup adds it */
	}
	struct wlr_xdg_surface *parent = wlr_xdg_surface_try_from_wlr_surface(xdg_popup->parent);
	if (parent == NULL) {
		return;
	}
	add_popup(xdg_popup, parent->data);
}


/* ------------------------------------------------------------------ */
/* XWayland shim                                                       */
/* ------------------------------------------------------------------ */
/* Nothing of CosmOS's own is X11 any more; this only exists so third-party
 * X programs (GameBox as it stands, Wine, Steam, JetBrains IDEs) keep
 * working. XWayland starts lazily on the first X client, its windows go
 * through the very same window list / maximize / Alt+Tab model as native
 * ones, and override-redirect surfaces (X menus, tooltips) are placed
 * where the client asked, above everything but the switcher. */

static void xwindow_associate(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, associate);
	struct wlr_surface *surface = w->xsurface->surface;
	w->scene_tree = wlr_scene_subsurface_tree_create(server.tree[SCENE_WINDOWS], surface);
	w->scene_tree->node.data = w;
	w->map.notify = window_map;
	wl_signal_add(&surface->events.map, &w->map);
	w->unmap.notify = window_unmap;
	wl_signal_add(&surface->events.unmap, &w->unmap);
}

static void xwindow_dissociate(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, dissociate);
	if (w->mapped) {
		window_unmap(&w->unmap, NULL);
	}
	wl_list_remove(&w->map.link);
	wl_list_init(&w->map.link);
	wl_list_remove(&w->unmap.link);
	wl_list_init(&w->unmap.link);
	if (w->scene_tree) {
		wlr_scene_node_destroy(&w->scene_tree->node);
		w->scene_tree = NULL;
	}
}

static void xwindow_request_configure(struct wl_listener *listener, void *data) {
	struct window *w = wl_container_of(listener, w, request_configure);
	if (w->mapped) {
		window_arrange(w); /* geometry is ours to decide */
	} else {
		struct wlr_xwayland_surface_configure_event *e = data;
		wlr_xwayland_surface_configure(w->xsurface, e->x, e->y, e->width, e->height);
	}
}

struct xunmanaged {
	struct wlr_xwayland_surface *xsurface;
	struct wlr_scene_tree *tree;
	struct wl_listener associate, dissociate, map, unmap, request_configure, set_geometry, destroy;
};

static void xu_map(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, map);
	u->tree = wlr_scene_subsurface_tree_create(server.tree[SCENE_FULLSCREEN], u->xsurface->surface);
	wlr_scene_node_set_position(&u->tree->node, u->xsurface->x, u->xsurface->y);
	if (wlr_xwayland_surface_override_redirect_wants_focus(u->xsurface)) {
		seat_focus_surface(u->xsurface->surface);
	}
}

static void xu_unmap(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, unmap);
	if (u->tree) {
		wlr_scene_node_destroy(&u->tree->node);
		u->tree = NULL;
	}
	if (server.seat->keyboard_state.focused_surface == u->xsurface->surface) {
		focus_default();
	}
}

static void xu_associate(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, associate);
	u->map.notify = xu_map;
	wl_signal_add(&u->xsurface->surface->events.map, &u->map);
	u->unmap.notify = xu_unmap;
	wl_signal_add(&u->xsurface->surface->events.unmap, &u->unmap);
}

static void xu_dissociate(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, dissociate);
	wl_list_remove(&u->map.link);
	wl_list_init(&u->map.link);
	wl_list_remove(&u->unmap.link);
	wl_list_init(&u->unmap.link);
}

static void xu_request_configure(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, request_configure);
	struct wlr_xwayland_surface_configure_event *e = data;
	wlr_xwayland_surface_configure(u->xsurface, e->x, e->y, e->width, e->height);
}

static void xu_set_geometry(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, set_geometry);
	if (u->tree) {
		wlr_scene_node_set_position(&u->tree->node, u->xsurface->x, u->xsurface->y);
	}
}

static void xu_destroy(struct wl_listener *listener, void *data) {
	struct xunmanaged *u = wl_container_of(listener, u, destroy);
	wl_list_remove(&u->associate.link);
	wl_list_remove(&u->dissociate.link);
	wl_list_remove(&u->map.link);
	wl_list_remove(&u->unmap.link);
	wl_list_remove(&u->request_configure.link);
	wl_list_remove(&u->set_geometry.link);
	wl_list_remove(&u->destroy.link);
	free(u);
}

static void new_xwayland_surface(struct wl_listener *listener, void *data) {
	struct wlr_xwayland_surface *xs = data;
	if (xs->override_redirect) {
		struct xunmanaged *u = calloc(1, sizeof(*u));
		u->xsurface = xs;
		wl_list_init(&u->map.link);
		wl_list_init(&u->unmap.link);
		u->associate.notify = xu_associate;
		wl_signal_add(&xs->events.associate, &u->associate);
		u->dissociate.notify = xu_dissociate;
		wl_signal_add(&xs->events.dissociate, &u->dissociate);
		u->request_configure.notify = xu_request_configure;
		wl_signal_add(&xs->events.request_configure, &u->request_configure);
		u->set_geometry.notify = xu_set_geometry;
		wl_signal_add(&xs->events.set_geometry, &u->set_geometry);
		u->destroy.notify = xu_destroy;
		wl_signal_add(&xs->events.destroy, &u->destroy);
		return;
	}
	struct window *w = calloc(1, sizeof(*w));
	w->server = &server;
	w->id = ++server.next_window_id;
	w->xsurface = xs;
	wl_list_insert(&server.windows, &w->link);
	wl_list_init(&w->map.link);
	wl_list_init(&w->unmap.link);
	wl_list_init(&w->commit.link);
	wl_list_init(&w->request_maximize.link);
	w->associate.notify = xwindow_associate;
	wl_signal_add(&xs->events.associate, &w->associate);
	w->dissociate.notify = xwindow_dissociate;
	wl_signal_add(&xs->events.dissociate, &w->dissociate);
	w->request_configure.notify = xwindow_request_configure;
	wl_signal_add(&xs->events.request_configure, &w->request_configure);
	w->request_fullscreen.notify = window_request_fullscreen;
	wl_signal_add(&xs->events.request_fullscreen, &w->request_fullscreen);
	w->destroy.notify = window_destroy;
	wl_signal_add(&xs->events.destroy, &w->destroy);
}

static void decoration_request_mode(struct wl_listener *listener, void *data) {
	struct wlr_xdg_toplevel_decoration_v1 *deco = data;
	if (deco->toplevel->base->initialized) {
		wlr_xdg_toplevel_decoration_v1_set_mode(deco, WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
	}
}

struct decoration {
	struct wlr_xdg_toplevel_decoration_v1 *deco;
	struct wl_listener request_mode;
	struct wl_listener destroy;
};

static void decoration_destroy(struct wl_listener *listener, void *data) {
	struct decoration *d = wl_container_of(listener, d, destroy);
	struct wlr_scene_tree *t = d->deco->toplevel->base->data;
	if (t && t->node.data) {
		((struct window *)t->node.data)->deco = NULL;
	}
	wl_list_remove(&d->request_mode.link);
	wl_list_remove(&d->destroy.link);
	free(d);
}

static void new_decoration(struct wl_listener *listener, void *data) {
	struct wlr_xdg_toplevel_decoration_v1 *deco = data;
	struct decoration *d = calloc(1, sizeof(*d));
	d->request_mode.notify = decoration_request_mode;
	wl_signal_add(&deco->events.request_mode, &d->request_mode);
	d->destroy.notify = decoration_destroy;
	wl_signal_add(&deco->events.destroy, &d->destroy);
	d->deco = deco;
	struct window *w = ((struct wlr_scene_tree *)deco->toplevel->base->data)->node.data;
	w->deco = deco;
	if (deco->toplevel->base->initialized) {
		wlr_xdg_toplevel_decoration_v1_set_mode(deco, WLR_XDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
	}
}

/* ------------------------------------------------------------------ */
/* layer-shell                                                         */
/* ------------------------------------------------------------------ */

static void layer_map(struct wl_listener *listener, void *data) {
	struct layer *l = wl_container_of(listener, l, map);
	l->mapped = true;
	apply_lock();
	arrange_layers(l->output);
	anim_fade_in(&l->scene->tree->node); /* the bar, the launcher, the lock screen */
	int kb = l->layer_surface->current.keyboard_interactive;
	if (kb == ZWLR_LAYER_SURFACE_V1_KEYBOARD_INTERACTIVITY_EXCLUSIVE &&
	    l->layer_surface->current.layer >= ZWLR_LAYER_SHELL_V1_LAYER_TOP) {
		seat_focus_surface(l->layer_surface->surface);
	} else if (server.active == NULL && !seat_has_focus_visible()) {
		focus_default();
	}
}

static void layer_unmap(struct wl_listener *listener, void *data) {
	struct layer *l = wl_container_of(listener, l, unmap);
	if (server.home_raised == l) {
		home_lower(false);
	}
	l->mapped = false;
	arrange_layers(l->output);
	if (server.seat->keyboard_state.focused_surface == l->layer_surface->surface) {
		focus_default();
	}
}

static void layer_commit(struct wl_listener *listener, void *data) {
	struct layer *l = wl_container_of(listener, l, commit);
	if (l->layer_surface->initial_commit || l->mapped) {
		arrange_layers(l->output);
	}
}

static void lock_respawn(void);

static void layer_destroy(struct wl_listener *listener, void *data) {
	struct layer *l = wl_container_of(listener, l, destroy);
	struct output *out = l->output;
	bool was_lock = server.locked && layer_is_lock(l);
	if (server.home_raised == l) {
		server.home_raised = NULL;
		server.home_origin = NULL;
	}
	wl_list_remove(&l->map.link);
	wl_list_remove(&l->unmap.link);
	wl_list_remove(&l->commit.link);
	wl_list_remove(&l->destroy.link);
	wl_list_remove(&l->new_popup.link);
	wl_list_remove(&l->link);
	free(l);
	if (out) {
		arrange_layers(out);
	}
	if (was_lock) {
		lock_respawn();
	}
}

static void layer_new_popup(struct wl_listener *listener, void *data) {
	struct layer *l = wl_container_of(listener, l, new_popup);
	add_popup(data, l->scene->tree);
}

static void new_layer_surface(struct wl_listener *listener, void *data) {
	struct wlr_layer_surface_v1 *ls = data;
	struct output *out = NULL;
	if (ls->output) {
		struct output *o;
		wl_list_for_each(o, &server.outputs, link) {
			if (o->wlr_output == ls->output) {
				out = o;
			}
		}
	}
	if (!out) {
		out = primary_output();
	}
	if (!out) {
		wlr_layer_surface_v1_destroy(ls);
		return;
	}
	ls->output = out->wlr_output;

	struct layer *l = calloc(1, sizeof(*l));
	l->server = &server;
	l->output = out;
	l->layer_surface = ls;
	enum zwlr_layer_shell_v1_layer layer = ls->pending.layer;
	l->scene = wlr_scene_layer_surface_v1_create(server.tree[
		layer == ZWLR_LAYER_SHELL_V1_LAYER_BACKGROUND ? SCENE_BACKGROUND :
		layer == ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM ? SCENE_BOTTOM :
		layer == ZWLR_LAYER_SHELL_V1_LAYER_TOP ? SCENE_TOP : SCENE_OVERLAY], ls);
	/* Append: surfaces are arranged in creation order, so a panel built after
	 * the top and bottom bars (the left bar) is sized against what they left. */
	wl_list_insert(out->layers[layer].prev, &l->link);
	apply_lock();

	l->map.notify = layer_map;
	wl_signal_add(&ls->surface->events.map, &l->map);
	l->unmap.notify = layer_unmap;
	wl_signal_add(&ls->surface->events.unmap, &l->unmap);
	l->commit.notify = layer_commit;
	wl_signal_add(&ls->surface->events.commit, &l->commit);
	l->destroy.notify = layer_destroy;
	wl_signal_add(&ls->events.destroy, &l->destroy);
	l->new_popup.notify = layer_new_popup;
	wl_signal_add(&ls->events.new_popup, &l->new_popup);
}

/* ------------------------------------------------------------------ */
/* window list + Alt+Tab switcher                                      */
/* ------------------------------------------------------------------ */

#define MAX_CLIENTS 256
#define ALT_TAB_ROW_HEIGHT 36
#define ALT_TAB_WIDTH 420
#define ALT_TAB_PADDING 12

/* the swappable list: mapped, non-dialog windows, most recent first */
static int client_list(struct window **out) {
	int n = 0;
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		if (w->mapped && !w->dialog && n < MAX_CLIENTS) {
			out[n++] = w;
		}
	}
	return n;
}

static const char *window_title(struct window *w) {
	const char *t = w->xsurface ? w->xsurface->title : w->xdg_toplevel->title;
	return (t && *t) ? t : "(untitled window)";
}

static struct {
	bool active;
	int selected;
	struct window *list[MAX_CLIENTS + 1]; /* windows only, most recently used first */
	int count;
	struct wlr_scene_buffer *node;
} switcher;

struct cairo_buffer {
	struct wlr_buffer base;
	cairo_surface_t *surface;
};

static void cairo_buffer_destroy(struct wlr_buffer *b) {
	struct cairo_buffer *cb = wl_container_of(b, cb, base);
	cairo_surface_destroy(cb->surface);
	free(cb);
}

static bool cairo_buffer_begin(struct wlr_buffer *b, uint32_t flags, void **data, uint32_t *format, size_t *stride) {
	struct cairo_buffer *cb = wl_container_of(b, cb, base);
	if (flags & WLR_BUFFER_DATA_PTR_ACCESS_WRITE) {
		return false;
	}
	*data = cairo_image_surface_get_data(cb->surface);
	*format = DRM_FORMAT_ARGB8888;
	*stride = (size_t)cairo_image_surface_get_stride(cb->surface);
	return true;
}

static void cairo_buffer_end(struct wlr_buffer *b) {}

static const struct wlr_buffer_impl cairo_buffer_impl = {
	.destroy = cairo_buffer_destroy,
	.begin_data_ptr_access = cairo_buffer_begin,
	.end_data_ptr_access = cairo_buffer_end,
};

static void switcher_hide(void) {
	if (switcher.node) {
		wlr_scene_node_destroy(&switcher.node->node);
		switcher.node = NULL;
	}
}

/* Same literal BG/FG/ACCENT colours as every other piece of CosmOS's UI. */
static void switcher_draw(void) {
	struct output *out = primary_output();
	if (!out || switcher.count == 0) {
		return;
	}
	int height = switcher.count * ALT_TAB_ROW_HEIGHT;
	struct cairo_buffer *cb = calloc(1, sizeof(*cb));
	cb->surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, ALT_TAB_WIDTH, height);
	cairo_t *cr = cairo_create(cb->surface);
	cairo_set_source_rgb(cr, 0x0a / 255.0, 0x0e / 255.0, 0x16 / 255.0);
	cairo_paint(cr);
	PangoLayout *layout = pango_cairo_create_layout(cr);
	PangoFontDescription *fd = pango_font_description_from_string("Rajdhani Medium 14");
	pango_layout_set_font_description(layout, fd);
	pango_layout_set_width(layout, (ALT_TAB_WIDTH - 2 * ALT_TAB_PADDING) * PANGO_SCALE);
	pango_layout_set_ellipsize(layout, PANGO_ELLIPSIZE_END);
	for (int i = 0; i < switcher.count; i++) {
		int y = i * ALT_TAB_ROW_HEIGHT;
		bool sel = i == switcher.selected;
		if (sel) {
			cairo_set_source_rgb(cr, 1.0, 0.0, 0x90 / 255.0);
			cairo_rectangle(cr, 0, y, ALT_TAB_WIDTH, ALT_TAB_ROW_HEIGHT);
			cairo_fill(cr);
			cairo_set_source_rgb(cr, 0x0a / 255.0, 0x0e / 255.0, 0x16 / 255.0);
		} else {
			cairo_set_source_rgb(cr, 0xe8 / 255.0, 0xec / 255.0, 0xf4 / 255.0);
		}
		pango_layout_set_text(layout, switcher.list[i] ? window_title(switcher.list[i]) : "", -1);
		int tw, th;
		pango_layout_get_pixel_size(layout, &tw, &th);
		cairo_move_to(cr, ALT_TAB_PADDING, y + (ALT_TAB_ROW_HEIGHT - th) / 2.0);
		pango_cairo_show_layout(cr, layout);
	}
	pango_font_description_free(fd);
	g_object_unref(layout);
	cairo_destroy(cr);
	cairo_surface_flush(cb->surface);
	wlr_buffer_init(&cb->base, &cairo_buffer_impl, ALT_TAB_WIDTH, height);

	switcher_hide();
	switcher.node = wlr_scene_buffer_create(server.tree[SCENE_OVERLAY], &cb->base);
	wlr_buffer_drop(&cb->base);
	wlr_scene_node_set_position(&switcher.node->node,
		out->full_area.x + (out->full_area.width - ALT_TAB_WIDTH) / 2,
		out->full_area.y + (out->full_area.height - height) / 2);
}

static void switcher_enter(bool reverse) {
	int windows = client_list(switcher.list);
	if (windows < 1 || (windows < 2 && server.active != NULL)) {
		return; /* nothing to switch to: with one window and no other, Alt+Tab has nowhere to go */
	}
	/* Windows only. The gridscreen is not an entry any more - the Windows key goes there. When
	 * the gridscreen is what is showing (no active window) the first Tab selects the most
	 * recently used window, so Alt+Tab from the grid brings your last window back. */
	switcher.count = windows;
	switcher.active = true;
	if (server.active == NULL) {
		switcher.selected = reverse ? windows - 1 : 0;
	} else {
		switcher.selected = reverse ? windows - 1 : 1;
	}
	switcher_draw();
	if (switcher.node) {
		anim_fade_in(&switcher.node->node);
	}
}

static void switcher_advance(bool reverse) {
	switcher.selected = reverse ? (switcher.selected - 1 + switcher.count) % switcher.count
	                            : (switcher.selected + 1) % switcher.count;
	switcher_draw();
}

static bool window_alive(struct window *w) {
	struct window *it;
	wl_list_for_each(it, &server.windows, link) {
		if (it == w) {
			return w->mapped;
		}
	}
	return false;
}

static void switcher_fade_out_done(void *data) {
	struct wlr_scene_buffer *node = data;
	wlr_scene_node_destroy(&node->node);
}

static void switcher_end(bool commit) {
	if (!switcher.active) {
		return;
	}
	switcher.active = false;
	struct wlr_scene_buffer *node = switcher.node;
	switcher.node = NULL; /* ownership moves to the fade; switcher_draw's own switcher_hide() must not also try to destroy it */
	if (node) {
		anim_fade(&node->node, 1.0f, 0.0f, switcher_fade_out_done, node);
	}
	struct window *chosen = switcher.selected < switcher.count ? switcher.list[switcher.selected] : NULL;
	if (commit && chosen && window_alive(chosen)) {
		window_activate(chosen);
	}
}

static void window_close(struct window *w) {
	if (w) {
		if (w->xsurface) {
			wlr_xwayland_surface_close(w->xsurface);
		} else {
			wlr_xdg_toplevel_send_close(w->xdg_toplevel);
		}
	}
}

static void lock_spawn(void) {
	const char *cmd = getenv("COSMOS_LOCK_CMD");
	spawn(cmd && *cmd ? cmd : "cosmos-lockscreen --locked");
}

static int lock_timer_fire(void *data) {
	if (server.locked && !lock_layer()) {
		lock_spawn();
	}
	return 0;
}

/* The lock screen went away while still locked: stay locked, start another. */
static void lock_respawn(void) {
	if (!server.lock_timer) {
		server.lock_timer = wl_event_loop_add_timer(wl_display_get_event_loop(server.wl_display), lock_timer_fire, NULL);
	}
	wl_event_source_timer_update(server.lock_timer, 500);
}

static void screen_lock(void) {
	if (server.locked) {
		return;
	}
	switcher_end(false);
	home_lower(false);
	server.locked = true;
	apply_lock();
	focus_default();
	lock_spawn();
}

static void screen_unlock(void) {
	if (!server.locked) {
		return;
	}
	server.locked = false;
	apply_lock();
	focus_default();
}

/* Minimize every window so the background layer (the launcher) shows. */
static void show_desktop(void) {
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		if (w->mapped && !w->minimized) {
			window_minimize(w);
		}
	}
	server.active = NULL;
	focus_default();
}

/* ------------------------------------------------------------------ */
/* keyboard                                                            */
/* ------------------------------------------------------------------ */

/* Only the desktop session: in the login screen the Guide button (Help) belongs to
 * the greeter (Desktop/GameBox switch), and in GameBox mode to GameBox. */
static bool home_on_guide;
/* A Super key is down and nothing else has been pressed since: releasing it now is a "tap". */
static bool super_pending;

static bool key_taken_by_subscriber(uint32_t keycode, bool pressed);

static bool handle_keybinding(struct keyboard *kb, uint32_t keycode, bool pressed) {
	if (server.locked) {
		return false; /* no shortcuts, no switcher: keys go to the lock screen */
	}
	struct wlr_keyboard *wk = kb->wlr_keyboard;
	const xkb_keysym_t *syms;
	int n = xkb_state_key_get_syms(wk->xkb_state, keycode + 8, &syms);
	uint32_t mods = wlr_keyboard_get_modifiers(wk);
	bool alt = mods & WLR_MODIFIER_ALT;
	bool handled = false;
	for (int i = 0; i < n; i++) {
		xkb_keysym_t sym = syms[i];
		bool is_super = sym == XKB_KEY_Super_L || sym == XKB_KEY_Super_R;
		if (pressed && !is_super) {
			super_pending = false; /* Super+something is a shortcut for the app, not a tap */
		}
		if (is_super && home_on_guide && !switcher.active) {
			/* Tap the Windows key: gridscreen <-> your window. The key itself is still passed on. */
			if (pressed) {
				super_pending = true;
			} else if (super_pending) {
				super_pending = false;
				home_toggle();
			}
		}
		if (switcher.active) {
			/* the switcher owns the whole keyboard while it is up */
			handled = true;
			if (pressed && (sym == XKB_KEY_Tab || sym == XKB_KEY_ISO_Left_Tab)) {
				switcher_advance(mods & WLR_MODIFIER_SHIFT);
			} else if (pressed && sym == XKB_KEY_Escape) {
				switcher_end(false);
			} else if (!pressed && (sym == XKB_KEY_Alt_L || sym == XKB_KEY_Alt_R)) {
				switcher_end(true);
			}
		} else if (pressed && alt && (sym == XKB_KEY_Tab || sym == XKB_KEY_ISO_Left_Tab)) {
			switcher_enter(mods & WLR_MODIFIER_SHIFT || sym == XKB_KEY_ISO_Left_Tab);
			handled = true;
		} else if (pressed && alt && (sym == XKB_KEY_x || sym == XKB_KEY_X) && !server.home_raised) {
			/* Only while a real window is actually what's on screen - not while the gridscreen is raised over it (server.active still
			 * names whatever window is behind the grid, so this used to close it invisibly, with nothing on screen to show what just
			 * happened). Handled either way: Alt+X is never meant to reach a client underneath the grid. */
			window_close(server.active);
			handled = true;
		} else if (pressed && alt && (sym == XKB_KEY_x || sym == XKB_KEY_X)) {
			handled = true;
		} else if (pressed && !alt && sym == XKB_KEY_Help && home_on_guide) {
			/* a controller's Guide button (cosmos-input sends it as Help): the same as the Windows key */
			home_toggle();
			handled = true;
		}
	}
	return handled;
}

static void keyboard_handle_modifiers(struct wl_listener *listener, void *data) {
	struct keyboard *kb = wl_container_of(listener, kb, modifiers);
	if (switcher.active && !(wlr_keyboard_get_modifiers(kb->wlr_keyboard) & WLR_MODIFIER_ALT)) {
		switcher_end(true); /* Alt released */
		return;
	}
	if (switcher.active) {
		return;
	}
	wlr_seat_set_keyboard(server.seat, kb->wlr_keyboard);
	wlr_seat_keyboard_notify_modifiers(server.seat, &kb->wlr_keyboard->modifiers);
}

static void keyboard_handle_key(struct wl_listener *listener, void *data) {
	struct keyboard *kb = wl_container_of(listener, kb, key);
	struct wlr_keyboard_key_event *event = data;
	bool pressed = event->state == WL_KEYBOARD_KEY_STATE_PRESSED;
	if (handle_keybinding(kb, event->keycode, pressed)) {
		return;
	}
	if (key_taken_by_subscriber(event->keycode, pressed)) {
		return;
	}
	wlr_seat_set_keyboard(server.seat, kb->wlr_keyboard);
	wlr_seat_keyboard_notify_key(server.seat, event->time_msec, event->keycode, event->state);
}

static void keyboard_handle_destroy(struct wl_listener *listener, void *data) {
	struct keyboard *kb = wl_container_of(listener, kb, destroy);
	wl_list_remove(&kb->modifiers.link);
	wl_list_remove(&kb->key.link);
	wl_list_remove(&kb->destroy.link);
	wl_list_remove(&kb->link);
	free(kb);
}

static void server_new_keyboard(struct wlr_input_device *device, bool is_virtual) {
	struct wlr_keyboard *wk = wlr_keyboard_from_input_device(device);
	struct keyboard *kb = calloc(1, sizeof(*kb));
	kb->server = &server;
	kb->wlr_keyboard = wk;
	if (!is_virtual) { /* virtual keyboards bring their own keymap */
		struct xkb_context *ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
		struct xkb_keymap *keymap = xkb_keymap_new_from_names(ctx, NULL, XKB_KEYMAP_COMPILE_NO_FLAGS);
		wlr_keyboard_set_keymap(wk, keymap);
		xkb_keymap_unref(keymap);
		xkb_context_unref(ctx);
		wlr_keyboard_set_repeat_info(wk, 25, 600);
	}
	kb->modifiers.notify = keyboard_handle_modifiers;
	wl_signal_add(&wk->events.modifiers, &kb->modifiers);
	kb->key.notify = keyboard_handle_key;
	wl_signal_add(&wk->events.key, &kb->key);
	kb->destroy.notify = keyboard_handle_destroy;
	wl_signal_add(&device->events.destroy, &kb->destroy);
	wlr_seat_set_keyboard(server.seat, wk);
	wl_list_insert(&server.keyboards, &kb->link);
}

/* ------------------------------------------------------------------ */
/* pointer                                                             */
/* ------------------------------------------------------------------ */

static void process_cursor_motion(uint32_t time) {
	double sx, sy;
	struct wlr_surface *surface = NULL;
	struct wlr_scene_node *node = wlr_scene_node_at(&server.scene->tree.node, server.cursor->x, server.cursor->y, &sx, &sy);
	if (node && node->type == WLR_SCENE_NODE_BUFFER) {
		struct wlr_scene_surface *ss = wlr_scene_surface_try_from_buffer(wlr_scene_buffer_from_node(node));
		if (ss) {
			surface = ss->surface;
		}
	}
	if (!surface) {
		wlr_cursor_set_xcursor(server.cursor, server.cursor_mgr, "left_ptr");
		wlr_seat_pointer_clear_focus(server.seat);
		return;
	}
	/* pointer focus only - keyboard focus stays where the WM put it */
	wlr_seat_pointer_notify_enter(server.seat, surface, sx, sy);
	wlr_seat_pointer_notify_motion(server.seat, time, sx, sy);
}

static void cursor_motion(struct wl_listener *l, void *data) {
	struct wlr_pointer_motion_event *e = data;
	wlr_cursor_move(server.cursor, &e->pointer->base, e->delta_x, e->delta_y);
	process_cursor_motion(e->time_msec);
}

static void cursor_motion_absolute(struct wl_listener *l, void *data) {
	struct wlr_pointer_motion_absolute_event *e = data;
	wlr_cursor_warp_absolute(server.cursor, &e->pointer->base, e->x, e->y);
	process_cursor_motion(e->time_msec);
}

static void cursor_button(struct wl_listener *l, void *data) {
	struct wlr_pointer_button_event *e = data;
	wlr_seat_pointer_notify_button(server.seat, e->time_msec, e->button, e->state);
}

static void cursor_axis(struct wl_listener *l, void *data) {
	struct wlr_pointer_axis_event *e = data;
	wlr_seat_pointer_notify_axis(server.seat, e->time_msec, e->orientation, e->delta, e->delta_discrete, e->source, e->relative_direction);
}

static void cursor_frame(struct wl_listener *l, void *data) {
	wlr_seat_pointer_notify_frame(server.seat);
}

static void new_input(struct wl_listener *listener, void *data) {
	struct wlr_input_device *device = data;
	switch (device->type) {
	case WLR_INPUT_DEVICE_KEYBOARD:
		server_new_keyboard(device, false);
		break;
	case WLR_INPUT_DEVICE_POINTER:
		wlr_cursor_attach_input_device(server.cursor, device);
		break;
	default:
		break;
	}
	uint32_t caps = WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_TOUCH; /* touch: injected over the control socket, no device needed */
	if (!wl_list_empty(&server.keyboards)) {
		caps |= WL_SEAT_CAPABILITY_KEYBOARD;
	}
	wlr_seat_set_capabilities(server.seat, caps);
}

static void new_virtual_keyboard(struct wl_listener *listener, void *data) {
	struct wlr_virtual_keyboard_v1 *vk = data;
	server_new_keyboard(&vk->keyboard.base, true);
	wlr_seat_set_capabilities(server.seat, WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_KEYBOARD | WL_SEAT_CAPABILITY_TOUCH);
}

static void request_cursor(struct wl_listener *listener, void *data) {
	struct wlr_seat_pointer_request_set_cursor_event *e = data;
	if (server.seat->pointer_state.focused_client == e->seat_client) {
		wlr_cursor_set_surface(server.cursor, e->surface, e->hotspot_x, e->hotspot_y);
	}
}

static void request_set_selection(struct wl_listener *listener, void *data) {
	struct wlr_seat_request_set_selection_event *e = data;
	wlr_seat_set_selection(server.seat, e->source, e->serial);
}

static void request_set_primary_selection(struct wl_listener *listener, void *data) {
	struct wlr_seat_request_set_primary_selection_event *e = data;
	wlr_seat_set_primary_selection(server.seat, e->source, e->serial);
}


/* ------------------------------------------------------------------ */
/* output scale                                                        */
/* ------------------------------------------------------------------ */

static double scale_for_output(struct wlr_output *wo, int width, int height) {
	double v = configured_scale(wo->name);
	return v > 0 ? v : auto_scale(width, height, wo->phys_width, wo->phys_height);
}

static void arrange_layers(struct output *out);

/* The video mode to start an output in (see pick_mode in output_scale.h for why the "preferred" flag is not taken on trust). */
static struct wlr_output_mode *choose_mode(struct wlr_output *wo) {
	struct mode_info infos[256];
	struct wlr_output_mode *modes[256];
	int n = 0;
	struct wlr_output_mode *m;
	wl_list_for_each(m, &wo->modes, link) {
		if (n == 256) {
			break;
		}
		infos[n] = (struct mode_info){m->width, m->height, m->refresh, m->preferred};
		modes[n++] = m;
	}
	int want_w = 0, want_h = 0;
	configured_mode(wo->name, &want_w, &want_h);
	int i = pick_mode(infos, n, want_w, want_h);
	return i >= 0 ? modes[i] : wlr_output_preferred_mode(wo);
}

static bool output_set_mode(struct output *o, int width, int height) {
	struct wlr_output_mode *best = NULL, *m;
	wl_list_for_each(m, &o->wlr_output->modes, link) {
		if (m->width == width && m->height == height && (!best || abs(m->refresh - 60000) < abs(best->refresh - 60000))) {
			best = m;
		}
	}
	if (!best) {
		return false;
	}
	struct wlr_output_state state;
	wlr_output_state_init(&state);
	wlr_output_state_set_mode(&state, best);
	bool ok = wlr_output_commit_state(o->wlr_output, &state);
	wlr_output_state_finish(&state);
	if (ok) {
		arrange_layers(o);
	}
	return ok;
}

static bool output_set_scale(struct output *o, double scale) {
	struct wlr_output_state state;
	wlr_output_state_init(&state);
	wlr_output_state_set_scale(&state, (float)scale);
	bool ok = wlr_output_commit_state(o->wlr_output, &state);
	wlr_output_state_finish(&state);
	if (ok) {
		wlr_xcursor_manager_load(server.cursor_mgr, (float)scale);
		arrange_layers(o);
	}
	return ok;
}

/* ------------------------------------------------------------------ */
/* outputs                                                             */
/* ------------------------------------------------------------------ */

static void output_frame(struct wl_listener *listener, void *data) {
	struct output *o = wl_container_of(listener, o, frame);
	struct wlr_scene_output *so = o->scene_output;
	wlr_scene_output_commit(so, NULL);
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	wlr_scene_output_send_frame_done(so, &now);
}

static void output_request_state(struct wl_listener *listener, void *data) {
	struct output *o = wl_container_of(listener, o, request_state);
	const struct wlr_output_event_request_state *e = data;
	wlr_output_commit_state(o->wlr_output, e->state);
	arrange_layers(o);
}

static void output_destroy(struct wl_listener *listener, void *data) {
	struct output *o = wl_container_of(listener, o, destroy);
	wl_list_remove(&o->frame.link);
	wl_list_remove(&o->request_state.link);
	wl_list_remove(&o->destroy.link);
	wl_list_remove(&o->link);
	free(o);
}

static void new_output(struct wl_listener *listener, void *data) {
	struct wlr_output *wo = data;
	wlr_output_init_render(wo, server.allocator, server.renderer);
	struct wlr_output_state state;
	wlr_output_state_init(&state);
	wlr_output_state_set_enabled(&state, true);
	struct wlr_output_mode *mode = choose_mode(wo);
	if (mode) {
		wlr_output_state_set_mode(&state, mode);
	}
	int pixel_w = mode ? mode->width : wo->width, pixel_h = mode ? mode->height : wo->height;   /* headless outputs have a custom mode only */
	double scale = scale_for_output(wo, pixel_w, pixel_h);
	wlr_output_state_set_scale(&state, (float)scale);
	wlr_output_commit_state(wo, &state);
	wlr_output_state_finish(&state);
	wlr_xcursor_manager_load(server.cursor_mgr, (float)scale);
	wlr_log(WLR_INFO, "output %s: %dx%d (%dx%d mm), scale %.2f", wo->name, pixel_w, pixel_h, wo->phys_width, wo->phys_height, scale);

	struct output *o = calloc(1, sizeof(*o));
	o->server = &server;
	o->wlr_output = wo;
	for (int i = 0; i < 4; i++) {
		wl_list_init(&o->layers[i]);
	}
	o->frame.notify = output_frame;
	wl_signal_add(&wo->events.frame, &o->frame);
	o->request_state.notify = output_request_state;
	wl_signal_add(&wo->events.request_state, &o->request_state);
	o->destroy.notify = output_destroy;
	wl_signal_add(&wo->events.destroy, &o->destroy);
	wl_list_insert(&server.outputs, &o->link);

	struct wlr_output_layout_output *lo = wlr_output_layout_add_auto(server.output_layout, wo);
	o->scene_output = wlr_scene_output_create(server.scene, wo);
	wlr_scene_output_layout_add_output(server.scene_layout, lo, o->scene_output);
	arrange_layers(o);
}

/* ------------------------------------------------------------------ */
/* control socket                                                      */
/* ------------------------------------------------------------------ */

static struct window *window_by_id(uint32_t id) {
	struct window *w;
	wl_list_for_each(w, &server.windows, link) {
		if (w->id == id && w->mapped) {
			return w;
		}
	}
	return NULL;
}

/* ---- keys and touch for the Android button mapper ------------------------------------------------------------------------------------- */

static struct wl_list ipc_subscribers;

static const char *window_app_id(struct window *w) {
	const char *id = w->xsurface ? w->xsurface->class : w->xdg_toplevel->app_id;
	return id ? id : "";
}

static uint32_t now_msec(void) {
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

/* A key typed while a subscriber's window has focus goes to that subscriber ("key CODE 1|0") and to nobody else. The compositor's own shortcuts
 * ran first, and nothing is taken while the screen is locked, the switcher is up or the gridscreen is raised. */
static bool key_taken_by_subscriber(uint32_t keycode, bool pressed) {
	if (server.locked || switcher.active || server.home_raised || !server.active || !server.active->mapped) {
		return false;
	}
	const char *app = window_app_id(server.active);
	bool taken = false;
	struct ipc_client *c;
	wl_list_for_each(c, &ipc_subscribers, link) {
		if (c->app_id[0] && strcmp(c->app_id, app) != 0) {
			continue;
		}
		char buf[48];
		int n = snprintf(buf, sizeof(buf), "key %u %d\n", keycode, pressed ? 1 : 0);
		ssize_t r = write(c->fd, buf, (size_t)n);
		(void)r;
		taken = true;
	}
	return taken;
}

/* Where a point given as fractions (0..1) of the focused window lands, in that window's surface coordinates. */
static struct wlr_surface *touch_target(double fx, double fy, double *sx, double *sy) {
	struct window *w = server.active;
	if (!w || !w->mapped || server.locked || server.home_raised) {
		return NULL;
	}
	struct wlr_surface *surface = window_surface(w);
	int width = surface->current.width, height = surface->current.height;
	double ox = 0, oy = 0;
	if (w->xdg_toplevel && w->xdg_toplevel->base->geometry.width > 0) {
		struct wlr_box geo = w->xdg_toplevel->base->geometry;
		width = geo.width;
		height = geo.height;
		ox = geo.x;
		oy = geo.y;
	}
	if (width <= 0 || height <= 0) {
		return NULL;
	}
	fx = fx < 0 ? 0 : fx > 1 ? 1 : fx;
	fy = fy < 0 ? 0 : fy > 1 ? 1 : fy;
	*sx = ox + fx * (width - 1);
	*sy = oy + fy * (height - 1);
	return surface;
}

static bool touch_id_ok(long id) {
	return id >= 0 && id < 10;
}

static void touch_release(struct ipc_client *c, int id) {
	if (c->touches_down & (1u << id)) {
		c->touches_down &= (uint16_t)~(1u << id);
		wlr_seat_touch_notify_up(server.seat, now_msec(), id);
		wlr_seat_touch_notify_frame(server.seat);
	}
}

/* "touch down|move ID X Y" (X, Y in 0..1 of the focused window), "touch up ID", "touch cancel". Only subscribed connections may touch. */
static const char *ipc_touch(struct ipc_client *c, char *args) {
	if (!c->subscribed) {
		return "error: subscribe first\n";
	}
	char verb[8];
	long id = -1;
	double x = 0, y = 0;
	int n = sscanf(args, "%7s %ld %lf %lf", verb, &id, &x, &y);
	if (n >= 1 && !strcmp(verb, "cancel")) {
		for (int i = 0; i < 10; i++) {
			touch_release(c, i);
		}
		return "ok\n";
	}
	if (n < 2 || !touch_id_ok(id)) {
		return "error: bad touch\n";
	}
	if (!strcmp(verb, "up")) {
		touch_release(c, (int)id);
		return "ok\n";
	}
	if (n != 4 || (strcmp(verb, "down") && strcmp(verb, "move"))) {
		return "error: bad touch\n";
	}
	double sx, sy;
	struct wlr_surface *surface = touch_target(x, y, &sx, &sy);
	if (!surface) {
		return "error: no window\n";
	}
	if (!strcmp(verb, "down")) {
		if (c->touches_down & (1u << id)) {
			return "ok\n";
		}
		wlr_seat_touch_notify_down(server.seat, surface, now_msec(), (int32_t)id, sx, sy);
		c->touches_down |= (uint16_t)(1u << id);
	} else {
		if (!(c->touches_down & (1u << id))) {
			return "error: not down\n";
		}
		wlr_seat_touch_notify_motion(server.seat, now_msec(), (int32_t)id, sx, sy);
	}
	wlr_seat_touch_notify_frame(server.seat);
	return "ok\n";
}

static void ipc_reply(int fd, const char *s) {
	ssize_t r = write(fd, s, strlen(s));
	(void)r;
}

/* Test hook (COSMOS_WM_TEST=1): a real single-keymap keyboard the control socket
 * can type on ("key EVDEV_CODE 1|0"), since headless has no input devices and
 * virtual keyboards bring a fresh keymap each time. Never enabled in a session. */
static struct wlr_keyboard test_keyboard;
static const struct wlr_keyboard_impl test_keyboard_impl = {.name = "cosmos-test-keyboard"};
static bool test_keyboard_on;

static void ipc_command(struct ipc_client *c, char *line) {
	int fd = c->fd;
	if (!strncmp(line, "subscribe keys", 14)) {
		/* "subscribe keys [APP_ID]": keep this connection; see key_taken_by_subscriber and ipc_touch */
		const char *app = line + 14;
		while (*app == ' ') {
			app++;
		}
		snprintf(c->app_id, sizeof(c->app_id), "%s", app);
		if (!c->subscribed) {
			c->subscribed = true;
			wl_list_insert(&ipc_subscribers, &c->link);
		}
		ipc_reply(fd, "ok\n");
	} else if (!strncmp(line, "touch ", 6)) {
		ipc_reply(fd, ipc_touch(c, line + 6));
	} else if (!strcmp(line, "windows")) {
		/* one line per swappable window, most recent first:
		 * ID<TAB>active(0/1)<TAB>title */
		struct window *list[MAX_CLIENTS];
		int n = client_list(list);
		for (int i = 0; i < n; i++) {
			char buf[640];
			snprintf(buf, sizeof(buf), "%u\t%d\t%s\n", list[i]->id, list[i] == server.active, window_title(list[i]));
			ipc_reply(fd, buf);
		}
	} else if (!strcmp(line, "geometry")) {
		/* one line per window, "ID<TAB>WIDTHxHEIGHT<TAB>fits|clipped<TAB>TITLE": the size the window has actually committed against the area it is
		 * given. A window whose own minimum size is bigger than the screen shows up as "clipped" - the automated check that no app runs off the edge. */
		struct output *o = primary_output();
		struct window *w;
		wl_list_for_each(w, &server.windows, link) {
			if (!w->mapped || !o) {
				continue;
			}
			int gw = w->xsurface ? w->xsurface->width : w->xdg_toplevel->base->geometry.width;
			int gh = w->xsurface ? w->xsurface->height : w->xdg_toplevel->base->geometry.height;
			char buf[700];
			snprintf(buf, sizeof(buf), "%u\t%dx%d\t%s\t%s\n", w->id, gw, gh, (gw <= o->usable_area.width && gh <= o->usable_area.height) ? "fits" : "clipped", window_title(w));
			ipc_reply(fd, buf);
		}
	} else if (test_keyboard_on && !strncmp(line, "key ", 4)) {
		unsigned code = 0, state = 0;
		if (sscanf(line + 4, "%u %u", &code, &state) == 2) {
			struct wlr_keyboard_key_event e = {.time_msec = 0, .keycode = code, .update_state = true,
				.state = state ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED};
			wlr_keyboard_notify_key(&test_keyboard, &e);
		}
		ipc_reply(fd, "ok\n");
	} else if (test_keyboard_on && !strncmp(line, "stress-configure ", 17)) {
		/* Test hook (COSMOS_WM_TEST=1): "stress-configure N" fires N raw arrange_layers() calls
		 * back to back with no yield to the event loop in between - the exact call chain that
		 * sends a fresh zwlr_layer_surface_v1.configure to every layer client on every commit.
		 * This is what a real burst of that message type looks like on the wire (each
		 * arrange_layers() call re-sends a configure to every mapped layer, gridscreen included),
		 * so it is how test_buffer_size_guard.sh proves the gridscreen's own connection survives
		 * one - the exact way "closing Settings kills the gridscreen" turned out to fail before
		 * wl_display_set_default_max_buffer_size was set at startup: the 4096-byte default
		 * libwayland gives every client's connection buffer is easy to exceed with a burst of
		 * real, valid protocol traffic (not a bug in whatever client is sending it), and once
		 * exceeded that client's connection is simply killed - fatal for any Wayland client. */
		unsigned count = 0;
		sscanf(line + 17, "%u", &count);
		struct layer *l = home_layer();
		if (l) {
			for (unsigned i = 0; i < count; i++) {
				arrange_layers(l->output);
			}
		}
		ipc_reply(fd, "ok\n");
	} else if (!strcmp(line, "focused")) {
		/* "APP_ID<TAB>TITLE" of the focused window, or an empty line */
		char buf[640] = "\n";
		if (server.active && server.active->mapped) {
			snprintf(buf, sizeof(buf), "%s\t%s\n", window_app_id(server.active), window_title(server.active));
		}
		ipc_reply(fd, buf);
	} else if (!strcmp(line, "home")) {
		home_toggle();
		ipc_reply(fd, "ok\n");
	} else if (!strcmp(line, "output")) {
		struct output *o = primary_output();
		char buf[64];
		snprintf(buf, sizeof(buf), "%dx%d\n", o ? o->full_area.width : 0, o ? o->full_area.height : 0);
		ipc_reply(fd, buf);
	} else if (!strcmp(line, "animations") || !strncmp(line, "animations ", 11)) {
		/* "animations": "on" or "off"; "animations on|off" changes it until the compositor restarts (display.conf keeps it) */
		if (line[10] == ' ') {
			anims_set(parse_switch(line + 11, animations_on) != 0);
		}
		ipc_reply(fd, animations_on ? "on\n" : "off\n");
	} else if (!strcmp(line, "modes")) {
		/* the first output's video modes, one per line, "WIDTHxHEIGHT<TAB>REFRESH_HZ<TAB>current|preferred|" (Settings > Display lists these) */
		struct output *o = primary_output();
		struct wlr_output_mode *m;
		if (o) {
			wl_list_for_each(m, &o->wlr_output->modes, link) {
				char buf[96];
				snprintf(buf, sizeof(buf), "%dx%d\t%.2f\t%s\n", m->width, m->height, m->refresh / 1000.0,
				         m == o->wlr_output->current_mode ? "current" : (m->preferred ? "preferred" : ""));
				ipc_reply(fd, buf);
			}
		}
	} else if (!strncmp(line, "mode ", 5)) {
		/* "mode WIDTHxHEIGHT": switches the first output to that size (it must be one the output lists); replies like "scale" */
		struct output *o = primary_output();
		int w = 0, h = 0;
		if (!o || !parse_mode(line + 5, &w, &h) || !output_set_mode(o, w, h)) {
			ipc_reply(fd, "error: no such mode on this output\n");
		} else {
			struct output *each;
			wl_list_for_each(each, &server.outputs, link) {
				char buf[160];
				snprintf(buf, sizeof(buf), "%s %dx%d %.2f %dx%d\n", each->wlr_output->name, each->wlr_output->width, each->wlr_output->height,
				         each->wlr_output->scale, each->full_area.width, each->full_area.height);
				ipc_reply(fd, buf);
			}
		}
	} else if (!strcmp(line, "scale") || !strncmp(line, "scale ", 6)) {
		/* "scale": one line per output, "NAME PIXELSxPIXELS SCALE LOGICALxLOGICAL"; "scale VALUE" or "scale auto" sets the first output's scale */
		struct output *o = primary_output();
		if (o && line[5] == ' ') {
			const char *arg = line + 6;
			double v = !strcmp(arg, "auto") ? auto_scale(o->wlr_output->width, o->wlr_output->height, o->wlr_output->phys_width, o->wlr_output->phys_height)
			                                : parse_scale(arg);
			if (v <= 0 || !output_set_scale(o, v)) {
				ipc_reply(fd, "error: scale must be auto or a number from 0.5 to 4\n");
				goto scale_done;
			}
		}
		struct output *each;
		wl_list_for_each(each, &server.outputs, link) {
			char buf[160];
			snprintf(buf, sizeof(buf), "%s %dx%d %.2f %dx%d\n", each->wlr_output->name, each->wlr_output->width, each->wlr_output->height,
			         each->wlr_output->scale, each->full_area.width, each->full_area.height);
			ipc_reply(fd, buf);
		}
scale_done:;
	} else if (!strcmp(line, "lock")) {
		screen_lock();
		ipc_reply(fd, "ok\n");
	} else if (!strcmp(line, "unlock")) {
		screen_unlock();
		ipc_reply(fd, "ok\n");
	} else if (server.locked && (!strncmp(line, "activate ", 9) || !strncmp(line, "close ", 6) || !strcmp(line, "desktop"))) {
		ipc_reply(fd, "error: locked\n");
	} else if (!strncmp(line, "activate ", 9)) {
		struct window *w = window_by_id((uint32_t)strtoul(line + 9, NULL, 10));
		if (w) {
			window_activate(w);
			ipc_reply(fd, "ok\n");
		} else {
			ipc_reply(fd, "error: no such window\n");
		}
	} else if (!strncmp(line, "close ", 6)) {
		struct window *w = window_by_id((uint32_t)strtoul(line + 6, NULL, 10));
		if (w) {
			window_close(w);
			ipc_reply(fd, "ok\n");
		} else {
			ipc_reply(fd, "error: no such window\n");
		}
	} else if (!strcmp(line, "desktop")) {
		show_desktop();
		ipc_reply(fd, "ok\n");
	} else if (!strcmp(line, "quit")) {
		ipc_reply(fd, "ok\n");
		wl_display_terminate(server.wl_display);
	} else {
		ipc_reply(fd, "error: unknown command\n");
	}
}

static void ipc_client_close(struct ipc_client *c) {
	if (c->subscribed) {
		for (int i = 0; i < 10; i++) {
			touch_release(c, i); /* a mapper that dies must not leave a finger on the screen */
		}
		wl_list_remove(&c->link);
	}
	wl_event_source_remove(c->source);
	close(c->fd);
	free(c);
}

static int ipc_client_readable(int fd, uint32_t mask, void *data) {
	struct ipc_client *c = data;
	ssize_t r = mask & WL_EVENT_READABLE ? read(fd, c->buf + c->len, sizeof(c->buf) - c->len - 1) : 0;
	if (r <= 0) {
		ipc_client_close(c);
		return 0;
	}
	c->len += (size_t)r;
	c->buf[c->len] = 0;
	for (;;) {
		char *nl = strchr(c->buf, '\n');
		if (!nl) {
			if (c->len >= sizeof(c->buf) - 1) {
				ipc_client_close(c); /* a line too long to be a command */
			}
			return 0;
		}
		*nl = 0;
		ipc_command(c, c->buf);
		if (!c->subscribed) {
			ipc_client_close(c); /* an ordinary command: one line, one answer, done */
			return 0;
		}
		size_t used = (size_t)(nl - c->buf) + 1;
		memmove(c->buf, c->buf + used, c->len - used);
		c->len -= used;
		c->buf[c->len] = 0;
	}
}

static int ipc_accept(int fd, uint32_t mask, void *data) {
	int cfd = accept4(fd, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
	if (cfd < 0) {
		return 0;
	}
	struct ipc_client *c = calloc(1, sizeof(*c));
	c->server = &server;
	c->fd = cfd;
	c->source = wl_event_loop_add_fd(wl_display_get_event_loop(server.wl_display), cfd, WL_EVENT_READABLE, ipc_client_readable, c);
	return 0;
}

static void ipc_init(void) {
	wl_list_init(&ipc_subscribers);
	const char *rt = getenv("XDG_RUNTIME_DIR");
	if (!rt) {
		return;
	}
	if (snprintf(server.ipc_path, sizeof(server.ipc_path), "%s/cosmos-wm.sock", rt) >= (int)sizeof(server.ipc_path)) {
		wlr_log(WLR_ERROR, "XDG_RUNTIME_DIR too long for a control socket path; control socket disabled");
		server.ipc_path[0] = 0;
		return;
	}
	unlink(server.ipc_path);
	int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
	struct sockaddr_un addr = {.sun_family = AF_UNIX};
	memcpy(addr.sun_path, server.ipc_path, sizeof(addr.sun_path));
	if (fd < 0 || bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0 || listen(fd, 8) < 0) {
		wlr_log(WLR_ERROR, "control socket %s unavailable: %s", server.ipc_path, strerror(errno));
		server.ipc_path[0] = 0;
		if (fd >= 0) {
			close(fd);
		}
		return;
	}
	server.ipc_fd = fd;
	server.ipc_source = wl_event_loop_add_fd(wl_display_get_event_loop(server.wl_display), fd, WL_EVENT_READABLE, ipc_accept, NULL);
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

/* Listeners must be gone before their emitters are destroyed (wlroots asserts) - on
 * every exit path, including a failed start-up. */
static void remove_server_listeners(void) {
	wl_list_remove(&server.new_output.link);
	wl_list_remove(&server.new_xdg_toplevel.link);
	wl_list_remove(&server.new_xdg_popup.link);
	wl_list_remove(&server.new_decoration.link);
	wl_list_remove(&server.new_layer_surface.link);
	if (server.xwayland) {
		wl_list_remove(&server.new_xwayland_surface.link);
	}
	wl_list_remove(&server.new_virtual_keyboard.link);
	wl_list_remove(&server.cursor_motion.link);
	wl_list_remove(&server.cursor_motion_absolute.link);
	wl_list_remove(&server.cursor_button.link);
	wl_list_remove(&server.cursor_axis.link);
	wl_list_remove(&server.cursor_frame.link);
	wl_list_remove(&server.new_input.link);
	wl_list_remove(&server.request_cursor.link);
	wl_list_remove(&server.request_set_selection.link);
	wl_list_remove(&server.request_set_primary_selection.link);
}

int main(int argc, char *argv[]) {
	wlr_log_init(getenv("WLR_DEBUG") ? WLR_DEBUG : WLR_INFO, NULL);
	char *startup_cmd = NULL;
	int c;
	while ((c = getopt(argc, argv, "s:h")) != -1) {
		switch (c) {
		case 's': startup_cmd = optarg; break;
		default:
			printf("Usage: %s [-s startup command]\n", argv[0]);
			return c == 'h' ? 0 : 1;
		}
	}

	signal(SIGPIPE, SIG_IGN);
	{
		const char *kind = getenv("COSMOS_SESSION_KIND");
		home_on_guide = kind && !strcmp(kind, "desktop");
	}

	server.wl_display = wl_display_create();
	/* libwayland has hard-capped every client's connection buffer at 4096 bytes - the historical
	 * default - since forever; wl_display_set_default_max_buffer_size() (added in 1.22.90
	 * specifically so compositors could raise it) is the only way off that floor. Left uncalled,
	 * any client whose commit legitimately needs to queue more than 4096 bytes before the socket
	 * gets a chance to flush (a redraw with many separate damage regions, say - exactly what more
	 * frequent redraws, like an animation, makes more likely) gets its connection killed outright:
	 * "[wayland] error in client communication" / "Data too big for buffer (4080 + 20 > 4096)",
	 * fatal for that client (confirmed live: this is what silently killed the gridscreen). 1 MiB
	 * costs nothing per idle client and gives real headroom for a burst that is otherwise valid
	 * protocol traffic, not a bug in the client sending it. */
	wl_display_set_default_max_buffer_size(server.wl_display, 1024 * 1024);
	server.backend = wlr_backend_autocreate(wl_display_get_event_loop(server.wl_display), NULL);
	if (!server.backend) {
		wlr_log(WLR_ERROR, "failed to create backend");
		return 1;
	}
	server.renderer = wlr_renderer_autocreate(server.backend);
	if (!server.renderer) {
		wlr_log(WLR_ERROR, "failed to create renderer");
		return 1;
	}
	wlr_renderer_init_wl_display(server.renderer, server.wl_display);
	server.allocator = wlr_allocator_autocreate(server.backend, server.renderer);

	server.compositor = wlr_compositor_create(server.wl_display, 6, server.renderer);
	wlr_subcompositor_create(server.wl_display);
	wlr_data_device_manager_create(server.wl_display);
	wlr_data_control_manager_v1_create(server.wl_display);
	wlr_primary_selection_v1_device_manager_create(server.wl_display);
	wlr_viewporter_create(server.wl_display);
	wlr_single_pixel_buffer_manager_v1_create(server.wl_display);
	wlr_fractional_scale_manager_v1_create(server.wl_display, 1);
	wlr_presentation_create(server.wl_display, server.backend, 2);
	wlr_screencopy_manager_v1_create(server.wl_display);

	server.output_layout = wlr_output_layout_create(server.wl_display);
	wl_list_init(&server.outputs);
	wl_list_init(&server.windows);
	wl_list_init(&anims);
	animations_on = animations_configured() != 0;
	anim_time_scale = animation_time_scale();
	wl_list_init(&server.keyboards);
	server.new_output.notify = new_output;
	wl_signal_add(&server.backend->events.new_output, &server.new_output);

	server.scene = wlr_scene_create();
	for (int i = 0; i < SCENE_COUNT; i++) {
		server.tree[i] = wlr_scene_tree_create(&server.scene->tree);
	}
	server.scene_layout = wlr_scene_attach_output_layout(server.scene, server.output_layout);
	wlr_xdg_output_manager_v1_create(server.wl_display, server.output_layout);

	server.xdg_shell = wlr_xdg_shell_create(server.wl_display, 6);
	server.new_xdg_toplevel.notify = new_xdg_toplevel;
	wl_signal_add(&server.xdg_shell->events.new_toplevel, &server.new_xdg_toplevel);
	server.new_xdg_popup.notify = new_xdg_popup;
	wl_signal_add(&server.xdg_shell->events.new_popup, &server.new_xdg_popup);

	/* GTK3 ignores xdg-decoration and only honours KDE's older server-decoration protocol:
	 * without it every GTK3 window draws its own title bar. Server-side is what we say
	 * (and nothing draws it), so windows come out undecorated, as they did on X11. */
	struct wlr_server_decoration_manager *kde_decoration_mgr = wlr_server_decoration_manager_create(server.wl_display);
	wlr_server_decoration_manager_set_default_mode(kde_decoration_mgr, WLR_SERVER_DECORATION_MANAGER_MODE_SERVER);
	server.decoration_mgr = wlr_xdg_decoration_manager_v1_create(server.wl_display);
	server.new_decoration.notify = new_decoration;
	wl_signal_add(&server.decoration_mgr->events.new_toplevel_decoration, &server.new_decoration);

	server.layer_shell = wlr_layer_shell_v1_create(server.wl_display, 4);
	server.new_layer_surface.notify = new_layer_surface;
	wl_signal_add(&server.layer_shell->events.new_surface, &server.new_layer_surface);

	server.cursor = wlr_cursor_create();
	wlr_cursor_attach_output_layout(server.cursor, server.output_layout);
	server.cursor_mgr = wlr_xcursor_manager_create(NULL, 24);
	server.cursor_motion.notify = cursor_motion;
	wl_signal_add(&server.cursor->events.motion, &server.cursor_motion);
	server.cursor_motion_absolute.notify = cursor_motion_absolute;
	wl_signal_add(&server.cursor->events.motion_absolute, &server.cursor_motion_absolute);
	server.cursor_button.notify = cursor_button;
	wl_signal_add(&server.cursor->events.button, &server.cursor_button);
	server.cursor_axis.notify = cursor_axis;
	wl_signal_add(&server.cursor->events.axis, &server.cursor_axis);
	server.cursor_frame.notify = cursor_frame;
	wl_signal_add(&server.cursor->events.frame, &server.cursor_frame);

	server.seat = wlr_seat_create(server.wl_display, "seat0");
	server.new_input.notify = new_input;
	wl_signal_add(&server.backend->events.new_input, &server.new_input);
	server.request_cursor.notify = request_cursor;
	wl_signal_add(&server.seat->events.request_set_cursor, &server.request_cursor);
	server.request_set_selection.notify = request_set_selection;
	wl_signal_add(&server.seat->events.request_set_selection, &server.request_set_selection);
	server.request_set_primary_selection.notify = request_set_primary_selection;
	wl_signal_add(&server.seat->events.request_set_primary_selection, &server.request_set_primary_selection);

	/* hidden XWayland shim - lazy: Xwayland only starts for the first X client */
	server.xwayland = wlr_xwayland_create(server.wl_display, server.compositor, true);
	if (server.xwayland) {
		server.new_xwayland_surface.notify = new_xwayland_surface;
		wl_signal_add(&server.xwayland->events.new_surface, &server.new_xwayland_surface);
		wlr_xwayland_set_seat(server.xwayland, server.seat);
		setenv("DISPLAY", server.xwayland->display_name, true);
	} else {
		wlr_log(WLR_ERROR, "XWayland unavailable; X11 programs will not run");
	}

	server.virtual_keyboard_mgr = wlr_virtual_keyboard_manager_v1_create(server.wl_display);
	server.new_virtual_keyboard.notify = new_virtual_keyboard;
	wl_signal_add(&server.virtual_keyboard_mgr->events.new_virtual_keyboard, &server.new_virtual_keyboard);

	if (getenv("COSMOS_WM_TEST")) {
		wlr_keyboard_init(&test_keyboard, &test_keyboard_impl, "cosmos-test-keyboard");
		server_new_keyboard(&test_keyboard.base, false);
		wlr_seat_set_capabilities(server.seat, WL_SEAT_CAPABILITY_POINTER | WL_SEAT_CAPABILITY_KEYBOARD | WL_SEAT_CAPABILITY_TOUCH);
		test_keyboard_on = true;
	}

	const char *socket = wl_display_add_socket_auto(server.wl_display);
	if (!socket) {
		wlr_log(WLR_ERROR, "could not open a Wayland socket");
		remove_server_listeners();
		wlr_backend_destroy(server.backend);
		return 1;
	}
	if (!wlr_backend_start(server.backend)) {
		wlr_log(WLR_ERROR, "failed to start the backend (no usable display/GPU?)");
		remove_server_listeners();
		wlr_backend_destroy(server.backend);
		wl_display_destroy(server.wl_display);
		return 1;
	}
	setenv("WAYLAND_DISPLAY", socket, true);
	setenv("XDG_CURRENT_DESKTOP", "CosmOS", true);
	ipc_init();
	set_home_shown(true);
	if (startup_cmd) {
		spawn(startup_cmd);
	}
	wlr_log(WLR_INFO, "cosmos-wm running on WAYLAND_DISPLAY=%s DISPLAY=%s", socket, getenv("DISPLAY") ? getenv("DISPLAY") : "(none)");
	wl_display_run(server.wl_display);

	if (server.ipc_path[0]) {
		unlink(server.ipc_path);
	}
	wl_display_destroy_clients(server.wl_display);
	remove_server_listeners();
	/* Deliberately no wlr_backend_destroy()/wl_display_destroy(): the process is about to
	 * exit, which releases the DRM device, seat and sockets, and tearing the backends down
	 * by hand trips a wlroots assertion on the DRM+libinput backend (seen for real: the
	 * greeter's compositor aborted with SIGABRT on exit). Clients are already gone. */
	return 0;
}
