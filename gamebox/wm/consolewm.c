/*
 * consolewm: a minimal, non-reparenting, non-decorating X11 window
 * manager. Not a compositor (no rendering, no embedding) - it only does
 * event routing: map new windows, track/set focus, and answer the
 * handful of EWMH bits (_NET_ACTIVE_WINDOW, _NET_SUPPORTING_WM_CHECK)
 * that tools like `xdotool windowactivate`/`getactivewindow` need a
 * real WM present to work at all.
 *
 * Override-redirect windows (our GTK POPUP-based bezel/filter/pause
 * overlays) are never touched here - that's true regardless of which
 * WM is running, or none: the X11 spec has override-redirect windows
 * bypass window management entirely. This WM exists for the *other*
 * kind of window - the actual game/emulator - which needs its
 * MapRequest serviced and its focus tracked correctly once a WM is
 * present to redirect substructure events at all.
 *
 * On hot-reload and verbose debugging (the project's two standing
 * requirements going forward, per plans/plan.txt): verbose debugging is
 * here, gated by the CONSOLE_OS_DEBUG env var exactly like the Python
 * side's debuglog.py (unset or "1" = verbose, "0" = quiet) so behavior
 * can be inspected the same way across the whole project. Hot-reload
 * genuinely does not apply to this file the way it does to the Python
 * daemons: this is a compiled X11 event-loop process holding live
 * window-manager state (which window is focused, EWMH properties it
 * owns on the root window) that every mapped client window depends on
 * for the life of the X session - the only way to load new code here
 * without a real dlopen-based plugin architecture (a different project)
 * is to restart the whole process, which drops every client it was
 * managing. That's a real, structural difference from the Python
 * daemons, not an oversight.
 */
#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static Display *dpy;
static Window root;
static Window active_window = None;
/* The first top-level window ever mapped - in this project that's always
 * the GTK launcher itself (see handle_map_request()'s own comment): every
 * other window (a game, an emulator, Brave, Kodi) is a subprocess the
 * launcher spawns and this WM manages one at a time, and the launcher
 * stays mapped underneath it the whole time, just occluded. Tracked so a
 * closing app/game/browser window can hand focus back to it explicitly. */
static Window launcher_window = None;
static Atom net_supported;
static Atom net_supporting_wm_check;
static Atom net_active_window;
static Atom net_wm_name;
static Atom net_wm_state;
static Atom net_wm_state_fullscreen;
static Atom utf8_string;

static int wm_detected = 0;
static int debug_enabled = 1;

/* Fullscreen state (_NET_WM_STATE_FULLSCREEN) needs *some* per-window
 * memory - restoring a window's pre-fullscreen geometry on toggle-off
 * requires remembering what it was. A small fixed-size table rather than
 * a real list: this WM only ever manages a handful of windows at once
 * (the launcher, one game/app at a time), consistent with no dynamic
 * data structures used anywhere else in this file. */
#define MAX_FULLSCREEN_WINDOWS 16

typedef struct {
	Window window;
	int x, y;
	unsigned int width, height;
} SavedGeometry;

static void set_fullscreen(Window w, int enable);

static SavedGeometry fullscreen_windows[MAX_FULLSCREEN_WINDOWS];
static int fullscreen_count = 0;

#define NET_WM_STATE_REMOVE 0
#define NET_WM_STATE_ADD 1
#define NET_WM_STATE_TOGGLE 2

static void
debug_log(const char *fmt, ...)
{
	if (!debug_enabled) {
		return;
	}
	va_list args;
	va_start(args, fmt);
	fprintf(stderr, "consolewm: DEBUG: ");
	vfprintf(stderr, fmt, args);
	fprintf(stderr, "\n");
	va_end(args);
}

static int
on_wm_detect_error(Display *d, XErrorEvent *e)
{
	(void) d;
	if (e->error_code == BadAccess) {
		wm_detected = 1;
	}
	return 0;
}

static int
on_error(Display *d, XErrorEvent *e)
{
	/* A minimal WM must not die on ordinary races (a window closing
	 * right as we try to configure/focus it, etc.) - log and move on. */
	char msg[256];
	XGetErrorText(d, e->error_code, msg, sizeof(msg));
	fprintf(stderr, "consolewm: X error: %s (request %d)\n", msg, e->request_code);
	return 0;
}

static void
focus_window(Window w)
{
	if (w == None) {
		return;
	}
	debug_log("focus_window: 0x%lx (was 0x%lx)", w, active_window);
	XSetInputFocus(dpy, w, RevertToPointerRoot, CurrentTime);
	XRaiseWindow(dpy, w);
	XChangeProperty(dpy, root, net_active_window, XA_WINDOW, 32, PropModeReplace, (unsigned char *) &w, 1);
	active_window = w;
}

static void
clear_active_window(void)
{
	debug_log("clear_active_window: was 0x%lx", active_window);
	active_window = None;
	XSetInputFocus(dpy, PointerRoot, RevertToPointerRoot, CurrentTime);
	XDeleteProperty(dpy, root, net_active_window);
}

static void
init_ewmh(void)
{
	net_supported = XInternAtom(dpy, "_NET_SUPPORTED", False);
	net_supporting_wm_check = XInternAtom(dpy, "_NET_SUPPORTING_WM_CHECK", False);
	net_active_window = XInternAtom(dpy, "_NET_ACTIVE_WINDOW", False);
	net_wm_name = XInternAtom(dpy, "_NET_WM_NAME", False);
	net_wm_state = XInternAtom(dpy, "_NET_WM_STATE", False);
	net_wm_state_fullscreen = XInternAtom(dpy, "_NET_WM_STATE_FULLSCREEN", False);
	utf8_string = XInternAtom(dpy, "UTF8_STRING", False);

	/* _NET_WM_STATE_FULLSCREEN must be advertised here, not just handled
	 * in handle_client_message() below - GDK checks _NET_SUPPORTED before
	 * ever sending the request at all, so without this a client's
	 * .fullscreen() call is silently never even attempted (confirmed
	 * directly: the launcher's own window stayed at its natural content
	 * size, un-fullscreened, with no error and no ClientMessage logged,
	 * until this was added). */
	Atom supported[] = {net_active_window, net_supporting_wm_check, net_wm_state, net_wm_state_fullscreen};
	XChangeProperty(dpy, root, net_supported, XA_ATOM, 32, PropModeReplace, (unsigned char *) supported, 4);

	/* A small, never-mapped window whose existence + _NET_SUPPORTING_WM_CHECK
	 * pointing at itself is the standard way EWMH-aware tools confirm a
	 * real window manager is present. */
	Window check = XCreateSimpleWindow(dpy, root, 0, 0, 1, 1, 0, 0, 0);
	XChangeProperty(dpy, check, net_supporting_wm_check, XA_WINDOW, 32, PropModeReplace, (unsigned char *) &check, 1);
	XChangeProperty(dpy, root, net_supporting_wm_check, XA_WINDOW, 32, PropModeReplace, (unsigned char *) &check, 1);
	XChangeProperty(dpy, check, net_wm_name, utf8_string, 8, PropModeReplace, (unsigned char *) "consolewm", 9);
	debug_log("EWMH initialized, check window 0x%lx", check);
}

/* A client requesting fullscreen *before* its first map (e.g. calling
 * .fullscreen() in a GTK window's __init__, before show()/show_all() -
 * true of every fullscreen window in this project) doesn't send a
 * _NET_WM_STATE ClientMessage at all - per the EWMH spec, that form is
 * only for changing the state of an *already-mapped* window. The initial
 * state instead comes as a plain _NET_WM_STATE property the client sets
 * on its own (still withdrawn) window before mapping, which the WM is
 * expected to read at MapRequest time. Confirmed directly: without this,
 * a window whose .fullscreen() was called pre-map never sent any
 * ClientMessage at all and stayed at its natural content size. */
static int
window_requests_fullscreen(Window w)
{
	Atom actual_type;
	int actual_format;
	unsigned long nitems, bytes_after;
	unsigned char *prop = NULL;
	int result = 0;

	if (XGetWindowProperty(
	        dpy, w, net_wm_state, 0, 32, False, XA_ATOM, &actual_type, &actual_format, &nitems, &bytes_after, &prop
	    ) == Success && prop) {
		Atom *atoms = (Atom *) prop;
		for (unsigned long i = 0; i < nitems; i++) {
			if (atoms[i] == net_wm_state_fullscreen) {
				result = 1;
				break;
			}
		}
		XFree(prop);
	}
	return result;
}

static void
handle_map_request(XMapRequestEvent *ev)
{
	debug_log("MapRequest: window 0x%lx", ev->window);
	if (launcher_window == None) {
		debug_log("MapRequest: 0x%lx is the first window ever mapped - tracking it as the launcher", ev->window);
		launcher_window = ev->window;
	}
	XMapWindow(dpy, ev->window);
	focus_window(ev->window);
	if (window_requests_fullscreen(ev->window)) {
		debug_log("MapRequest: window 0x%lx requested initial _NET_WM_STATE_FULLSCREEN", ev->window);
		set_fullscreen(ev->window, 1);
	}
}

static void
handle_configure_request(XConfigureRequestEvent *ev)
{
	/* Grant whatever geometry was asked for - this WM never imposes its
	 * own layout (no tiling, no forced sizes); our own tooling (xdotool
	 * windowmove/windowsize) positions the one window that needs it. */
	debug_log(
	    "ConfigureRequest: window 0x%lx -> %dx%d+%d+%d",
	    ev->window, ev->width, ev->height, ev->x, ev->y
	);
	XWindowChanges changes;
	changes.x = ev->x;
	changes.y = ev->y;
	changes.width = ev->width;
	changes.height = ev->height;
	changes.border_width = ev->border_width;
	changes.sibling = ev->above;
	changes.stack_mode = ev->detail;
	XConfigureWindow(dpy, ev->window, ev->value_mask, &changes);
}

static SavedGeometry *
find_saved_geometry(Window w)
{
	for (int i = 0; i < fullscreen_count; i++) {
		if (fullscreen_windows[i].window == w) {
			return &fullscreen_windows[i];
		}
	}
	return NULL;
}

static void
forget_saved_geometry(SavedGeometry *saved)
{
	int index = (int) (saved - fullscreen_windows);
	fullscreen_windows[index] = fullscreen_windows[--fullscreen_count];
}

static void
set_fullscreen(Window w, int enable)
{
	SavedGeometry *saved = find_saved_geometry(w);
	if (enable) {
		if (saved) {
			return; /* already fullscreen */
		}
		if (fullscreen_count >= MAX_FULLSCREEN_WINDOWS) {
			debug_log("set_fullscreen: too many fullscreen windows already tracked, ignoring 0x%lx", w);
			return;
		}
		XWindowAttributes attrs;
		if (!XGetWindowAttributes(dpy, w, &attrs)) {
			return;
		}
		SavedGeometry *slot = &fullscreen_windows[fullscreen_count++];
		slot->window = w;
		slot->x = attrs.x;
		slot->y = attrs.y;
		slot->width = attrs.width;
		slot->height = attrs.height;

		int screen_width = DisplayWidth(dpy, DefaultScreen(dpy));
		int screen_height = DisplayHeight(dpy, DefaultScreen(dpy));
		debug_log(
		    "set_fullscreen: 0x%lx -> %dx%d+0+0 (was %dx%d+%d+%d)",
		    w, screen_width, screen_height, slot->width, slot->height, slot->x, slot->y
		);
		XMoveResizeWindow(dpy, w, 0, 0, screen_width, screen_height);
		XChangeProperty(
		    dpy, w, net_wm_state, XA_ATOM, 32, PropModeReplace, (unsigned char *) &net_wm_state_fullscreen, 1
		);
	} else {
		if (!saved) {
			return; /* wasn't fullscreen */
		}
		debug_log(
		    "set_fullscreen: restoring 0x%lx -> %dx%d+%d+%d",
		    w, saved->width, saved->height, saved->x, saved->y
		);
		XMoveResizeWindow(dpy, w, saved->x, saved->y, saved->width, saved->height);
		XDeleteProperty(dpy, w, net_wm_state);
		forget_saved_geometry(saved);
	}
}

static void
handle_client_message(XClientMessageEvent *ev)
{
	if (ev->message_type == net_active_window) {
		debug_log("ClientMessage: _NET_ACTIVE_WINDOW for 0x%lx", ev->window);
		focus_window(ev->window);
		return;
	}
	if (ev->message_type == net_wm_state) {
		long action = ev->data.l[0];
		Atom prop1 = (Atom) ev->data.l[1];
		Atom prop2 = (Atom) ev->data.l[2];
		if (prop1 != net_wm_state_fullscreen && prop2 != net_wm_state_fullscreen) {
			return; /* only fullscreen is implemented - no maximize/minimize/etc. */
		}
		int enable;
		if (action == NET_WM_STATE_ADD) {
			enable = 1;
		} else if (action == NET_WM_STATE_REMOVE) {
			enable = 0;
		} else { /* NET_WM_STATE_TOGGLE */
			enable = (find_saved_geometry(ev->window) == NULL);
		}
		debug_log("ClientMessage: _NET_WM_STATE fullscreen=%d for 0x%lx", enable, ev->window);
		set_fullscreen(ev->window, enable);
	}
}

static void
handle_destroy_or_unmap(Window w)
{
	debug_log("DestroyNotify/UnmapNotify: window 0x%lx", w);
	SavedGeometry *saved = find_saved_geometry(w);
	if (saved) {
		forget_saved_geometry(saved);
	}
	if (w == launcher_window) {
		launcher_window = None;
	}
	if (w != active_window) {
		return;
	}
	/* A real, confirmed bug this replaces: clear_active_window() alone
	 * reverts focus to PointerRoot and stops there - fine as a last
	 * resort, but every real entry in this project (a game, an emulator,
	 * Brave, Kodi) runs as a subprocess the launcher spawns and remains
	 * mapped underneath the whole time, so closing one should hand
	 * keyboard focus straight back to it. Left as PointerRoot instead,
	 * keyboard/gamepad-emulated-as-keyboard input goes wherever the
	 * pointer happens to be sitting - usually not over the launcher at
	 * all on this gamepad-first, no-real-pointer OS - which reads as
	 * "navigation is completely broken" until something else (a real
	 * mouse move) incidentally fixes it. */
	if (launcher_window != None && w != launcher_window) {
		focus_window(launcher_window);
	} else {
		clear_active_window();
	}
}

int
main(void)
{
	const char *debug_env = getenv("CONSOLE_OS_DEBUG");
	debug_enabled = (debug_env == NULL || debug_env[0] != '0');

	/* The X socket file can exist slightly before the server is actually
	 * ready to accept new connections - retry briefly rather than fail
	 * outright on a connection made right as the server just started. */
	for (int attempt = 0; !dpy && attempt < 20; attempt++) {
		dpy = XOpenDisplay(NULL);
		if (!dpy) {
			debug_log("XOpenDisplay attempt %d failed, retrying", attempt);
			usleep(100000);
		}
	}
	if (!dpy) {
		fprintf(stderr, "consolewm: cannot open display\n");
		return 1;
	}
	root = DefaultRootWindow(dpy);
	debug_log("connected to display, root window 0x%lx", root);

	XSetErrorHandler(on_wm_detect_error);
	XSelectInput(dpy, root, SubstructureRedirectMask | SubstructureNotifyMask);
	XSync(dpy, False);
	if (wm_detected) {
		fprintf(stderr, "consolewm: another window manager is already running on this display\n");
		return 1;
	}
	XSetErrorHandler(on_error);

	init_ewmh();
	XSync(dpy, False);

	fprintf(stdout, "consolewm: running\n");
	fflush(stdout);

	for (;;) {
		XEvent ev;
		XNextEvent(dpy, &ev);
		switch (ev.type) {
		case MapRequest:
			handle_map_request(&ev.xmaprequest);
			break;
		case ConfigureRequest:
			handle_configure_request(&ev.xconfigurerequest);
			break;
		case ClientMessage:
			handle_client_message(&ev.xclient);
			break;
		case DestroyNotify:
			handle_destroy_or_unmap(ev.xdestroywindow.window);
			break;
		case UnmapNotify:
			handle_destroy_or_unmap(ev.xunmap.window);
			break;
		default:
			break;
		}
	}
}
