#!/usr/bin/env python3
"""CosmOS's own login screen - direct requirement ("I want to make my own light
greeter too"), a real GTK3 app matching cosmos-bar's own dark theme (same BG/FG/
ACCENT/BORDER, same power-menu-style entry/button CSS classes).

It runs as the greetd greeter: greetd starts `cosmos-wm -s cosmos-greeter` as the
unprivileged `greeter` user, so this is an ordinary Wayland client on a full-screen
window. The conversation with greetd (cosmos_login.py: create_session ->
password / password-change prompts -> start_session) is what actually does the
privileged PAM work; this process only ever sees the documented prompt/message
exchange and hands the typed password to greetd - the same trust boundary every
greeter has. It replaces the LightDM greeter this file used to be, since LightDM's
greeter needs an X server and CosmOS is Wayland-only.

Deliberately NOT the thing this project's own lock screen replaces - see
config/scripts/cosmos-lockscreen.py's own docstring for why a greeter and a lock
screen are two genuinely different privilege/API surfaces despite looking similar
on screen.
"""
import os

# Set before anything opens a display connection (the gi/Gdk import below does
# exactly that) - this greeter is started by greetd, never through cosmos-session,
# so it never inherits that script's own XCURSOR_THEME export. Real, live bug this
# fixes (reported: default-looking cursor outside the login card): the env var
# itself, not just the gtk-cursor-theme-name GtkSetting below, because cursor
# themes are resolved the way any Xcursor client does (env var read at connection
# time), and doing both is cheap insurance.
os.environ.setdefault("XCURSOR_THEME", "Bibata-Modern-Classic")
os.environ.setdefault("XCURSOR_SIZE", "24")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402
import cosmos_display  # noqa: E402

import cosmos_login as login

BG = "#0a0e16"
FG = "#e8ecf4"
ACCENT = "#ff0090"
BORDER = "#2a3a56"
# The plain, unmodified NASA/ESA Hubble photo (M51, the Whirlpool
# Galaxy) - NOT cosmos-session's own desktop wallpaper file
# (cosmos-galaxy.jpg), which has the "CosmOS" logo and a dark
# gradient composited directly into its own pixels for lightdm-gtk-
# greeter's sake (see the Containerfile's own comment on that file).
# Direct request ("just use the base image from nasa without the logo
# in login") - this greeter draws its own card/UI on top already, so
# it never needed that pre-baked branding/gradient in the first place.
BACKGROUND_IMAGE = "/usr/share/backgrounds/galaxy-nasa.jpg"
LOGO_PATH = "/opt/astronar-studio/launcher/icons/cosmos_logo.png"
AVATAR_SIZE = cosmos_display.px(96)

CSS = f"""
window.cosmos-greeter {{
    background-color: {BG};
}}
.cosmos-greeter-card {{
    background-color: {BG};
    border: 2px solid {BORDER};
    border-radius: 6px;
}}
.cosmos-greeter-label {{
    color: {FG};
    font-weight: bold;
}}
.cosmos-greeter-hint {{
    color: {FG};
    opacity: 0.6;
}}
.cosmos-greeter-shadow {{
    background-color: rgba(0, 0, 0, 0.45);
}}
/* The failure banner: deliberately hard to miss - a filled accent-coloured box with big
   bold text and a headline, not one dim line under the field (reported: "login failed"
   went by unnoticed and gave no reason). */
.cosmos-greeter-error {{
    color: {BG};
    background-color: {ACCENT};
    font-weight: bold;
    font-size: 15px;
    padding: 10px 14px;
    border-radius: 6px;
}}
/* Direct request ("make it more obvious") after a real report: the
   password-expired step used to only swap the entry's placeholder
   text, which is small, dim, and disappears the instant anyone starts
   typing - easy to miss entirely, which is exactly what happened (a
   blank second Enter got submitted as the new password and failed).
   This banner sits above the password field itself and stays visible
   (real text, not a placeholder) for the whole step. */
.cosmos-greeter-status {{
    color: {ACCENT};
    font-weight: bold;
    font-size: 14px;
}}
.cosmos-greeter-entry {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    caret-color: {FG};
    border: 1px solid {BORDER};
    box-shadow: none;
    font-weight: bold;
    padding: 10px 12px;
}}
/* Direct request: no dropdown arrow on the user combo - GTK3 draws it
   as an "arrow" CSS node inside the combobox, collapsed to nothing here
   rather than just hidden (opacity alone would leave its layout space
   reserved, still visibly off-centering the text next to it).
   -gtk-icon-size is NOT a valid GTK3 CSS property (confirmed live: a
   real, fatal bug this fixes - GLib.GError from an invalid property
   name in this same CSS block crashed the greeter outright on load,
   which the display manager treats as fatal (no login screen at all),
   not just a cosmetic miss) - min-width/min-height/padding
   alone are enough to collapse the arrow's own reserved space to zero. */
.cosmos-greeter-entry arrow {{
    min-width: 0;
    min-height: 0;
    padding: 0;
    opacity: 0;
}}
.cosmos-greeter-entry:focus {{
    border-color: {ACCENT};
    box-shadow: none;
}}
.cosmos-greeter-button {{
    background-color: {BG};
    background-image: none;
    color: {FG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-greeter-button:hover {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border-color: {ACCENT};
}}
.cosmos-greeter-button-accent {{
    background-color: {ACCENT};
    background-image: none;
    color: {BG};
    border: 1px solid {ACCENT};
    box-shadow: none;
    font-weight: bold;
    padding: 8px 14px;
}}
.cosmos-greeter-button-accent:hover {{
    background-color: {FG};
    background-image: none;
    border-color: {FG};
}}
"""


# Session keys = the wayland-sessions file names minus ".desktop"
# (see the Containerfile's own session entries).
DESKTOP_SESSION = "cosmos"
GAMEBOX_SESSION = "gamebox"


class CosmosGreeter(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        cosmos_display.fit_on_show(self, scroll=False)      # text wraps and fixed sizes are capped, so it can never be wider than the screen
        self.set_title("cosmos-greeter")
        self.get_style_context().add_class("cosmos-greeter")
        self.fullscreen()
        self.connect("destroy", Gtk.main_quit)

        # Only meaningful once an authenticate() conversation is actually
        # running (see start_authentication()'s own reset and
        # on_show_prompt's own comment) - initialized here too just so
        # it's never undefined if a prompt somehow fired before that.
        self._first_secret_prompt = True
        self.changing_password = False   # the forced first-login password change is underway
        self.last_pam_error = ""         # the last error PAM sent (why a new password was refused)
        self.last_typed = ""             # what was last submitted, to tell "empty" from "wrong"

        self.greeter = login.Conversation(
            login.GreetdConnection(),
            lambda text, kind: self.on_show_prompt(None, text, kind),
            lambda text, kind: self.on_show_message(None, text, kind),
            lambda ok: self.on_authentication_complete(None),
        )

        self.users = login.list_users()
        self.sessions = login.list_sessions()

        overlay = Gtk.Overlay()
        self.add(overlay)

        background = Gtk.Image()
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(BACKGROUND_IMAGE)
            background.set_from_pixbuf(pixbuf)
        except GLib.Error:
            pass
        overlay.add(background)

        # Direct request: a shadow overlay over the wallpaper - drawn as
        # its own layer here, not baked into the image itself (unlike
        # cosmos-session's own desktop wallpaper file, cosmos-galaxy.jpg,
        # which DOES have one pre-baked in for lightdm-gtk-greeter's
        # sake - see the Containerfile's own comment on that). Sized to
        # the real screen via the same get-child-position technique as
        # center/hint_label below, not plain halign/valign FILL -
        # GtkOverlay's own documented default behavior positions
        # automatic (non-custom-positioned) overlay children against the
        # MAIN WIDGET's own allocation, which here is the background
        # image's own native, unscaled size, not the overlay's or the
        # real screen's - the same underlying reason center/hint_label
        # needed this too (see their own comment below for the fuller
        # explanation). Added right after the background and before
        # everything else so it darkens the wallpaper without covering
        # any of the actual UI on top of it.
        shadow = Gtk.Box()
        shadow.get_style_context().add_class("cosmos-greeter-shadow")
        overlay.add_overlay(shadow)

        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top_bar.set_halign(Gtk.Align.END)
        top_bar.set_valign(Gtk.Align.START)
        # Direct request: Restart/Shutdown shouldn't touch the screen
        # edges either.
        top_bar.set_margin_top(24)
        top_bar.set_margin_end(24)

        self.clock_label = Gtk.Label(label="")
        self.clock_label.get_style_context().add_class("cosmos-greeter-label")
        top_bar.pack_start(self.clock_label, False, False, 12)

        # "(R)estart"/"(S)hutdown" labels - direct request, mnemonic-style
        # labels indicating which letter goes with each, reached via Tab
        # (no mouse needed - see main()'s own blank-cursor comment on why
        # that matters here specifically) rather than a bare, unmodified
        # R/S keypress: this window's password entry has focus by
        # default (see main()'s own grab_focus() call), and a global R/S
        # hotkey would trigger a restart/shutdown mid-keystroke for
        # anyone whose actual password happens to contain either letter.
        # Session mode switch - direct request ("give the login screen
        # a way to swap between the desktop and the gamebox mode"). The
        # two modes are just two sessions (wayland-sessions entries
        # "cosmos" = the normal desktop, "gamebox" = the GameBox
        # frontend, see gamebox-session), so this is only a picker for
        # which one start_session_sync() gets; shown only if the
        # gamebox session is actually installed. Reached by F2 from
        # the password entry (a function key can't collide with a
        # password character the way a bare letter could - same
        # reasoning as Restart/Shutdown above) or by Tab to this button,
        # since this screen has no visible mouse pointer.
        self.session_keys = set(self.sessions)
        self.mode = DESKTOP_SESSION
        self.mode_button = Gtk.Button()
        self.mode_button.get_style_context().add_class("cosmos-greeter-button")
        self.mode_button.connect("clicked", lambda _b: self.toggle_mode())
        self.update_mode_button()
        if GAMEBOX_SESSION in self.session_keys:
            top_bar.pack_start(self.mode_button, False, False, 0)

        if login.can_power():
            restart_button = Gtk.Button(label="(R)estart")
            restart_button.get_style_context().add_class("cosmos-greeter-button")
            restart_button.connect("clicked", lambda _b: login.restart())
            top_bar.pack_start(restart_button, False, False, 0)
            shutdown_button = Gtk.Button(label="(S)hutdown")
            shutdown_button.get_style_context().add_class("cosmos-greeter-button")
            shutdown_button.connect("clicked", lambda _b: login.shutdown())
            top_bar.pack_start(shutdown_button, False, False, 0)

        overlay.add_overlay(top_bar)

        # halign/valign CENTER removed here - see position_overlay_child()
        # below for why plain halign/valign can't be trusted against
        # this particular overlay at all (direct request: "I would like
        # the card to be dead center of the screen", confirming this was
        # a real, live, visibly-off-center bug here too, not just for
        # hint_label below).
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)

        # Direct request: the diamond avatar moved from beside the form
        # to its own row at the top of the card, right-aligned ("put it
        # in the top right of the box") - a card is now VERTICAL
        # (avatar row, then form), not HORIZONTAL (avatar beside form).
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.get_style_context().add_class("cosmos-greeter-card")
        card.set_border_width(32)
        # Explicit per-widget margins, not just border_width above -
        # real, live bug this fixes (confirmed via a real screenshot on
        # cosmos-lockscreen.py's own card - the Unlock button's own
        # left/right/bottom edges exactly matched the card's own border
        # pixel-for-pixel, zero visible gap, despite border_width being
        # set there too). Same fix applied here for the same reason.
        CARD_MARGIN = 24

        # Diamond-clipped avatar, same technique and same reasoning as
        # cosmos-bar.py's own build_avatar_widget() (direct request:
        # "use the diamond in login and lock like in the bottom bar") -
        # GTK3 has no built-in image-masking widget, so this is a plain
        # Gtk.DrawingArea painting the pixbuf through a real Cairo clip
        # path (a 45-degree-rotated square) rather than a rectangular
        # Gtk.Image, kept as self.avatar_pixbuf/self.avatar_area rather
        # than one static Gtk.Image.set_from_pixbuf() call so
        # update_avatar() can redraw it when the selected user changes
        # (see that method's own body below).
        self.avatar_pixbuf = None
        self.avatar_scale = cosmos_display.monitor_scale()      # the avatar is loaded at this scale times its size, so it is sharp on a scaled output
        self.avatar_area = Gtk.DrawingArea()
        self.avatar_area.set_size_request(AVATAR_SIZE, AVATAR_SIZE)
        self.avatar_area.connect("draw", self.on_draw_avatar)

        # A row of its own, avatar centered - direct request ("top
        # center" - an earlier draft right-aligned it instead, see this
        # section's own history). set_center_widget() is the real
        # GtkBox API for centering exactly one child regardless of the
        # row's own overall width, rather than halign on a widget that
        # would otherwise just fill the row.
        avatar_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        avatar_row.set_margin_top(CARD_MARGIN)
        avatar_row.set_center_widget(self.avatar_area)
        card.pack_start(avatar_row, False, False, 0)

        # Direct request: "space between everything so it's not so
        # crowded and looks professional" - the dropdown/entry/button
        # were all touching each other at spacing=8.
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        form.set_margin_start(CARD_MARGIN)
        form.set_margin_end(CARD_MARGIN)
        form.set_margin_bottom(CARD_MARGIN)
        card.pack_start(form, True, True, 0)

        self.user_combo = Gtk.ComboBoxText()
        self.user_combo.get_style_context().add_class("cosmos-greeter-entry")
        for user in self.users:
            self.user_combo.append(user.get_name(), user.get_display_name())
        if self.users:
            self.user_combo.set_active(0)
        self.user_combo.connect("changed", self.on_user_changed)
        # Direct request: no dropdown arrow, name centered instead - the
        # arrow itself is hidden via CSS (see the "arrow" node rule in
        # CSS above), and GtkComboBoxText's own internal cell renderer
        # (the actual thing drawing the username text) left-aligns by
        # default - a CSS class alone can't reach into that, it's a real
        # CellRenderer property, so it's set directly here instead.
        # set_property("xalign", ...), not the deprecated set_alignment()
        # - confirmed via an isolated local render (not guessed) that
        # set_alignment() alone has no visible effect at all here; the
        # cell also needs "expand" set on the combo's own CellArea, or
        # xalign has no extra room to center within in the first place
        # (its allocation stays exactly its own natural text width).
        area = self.user_combo.get_area()
        for cell in self.user_combo.get_cells():
            cell.set_property("xalign", 0.5)
            area.cell_set_property(cell, "expand", True)
        form.pack_start(self.user_combo, False, False, 0)

        # Hidden (set_no_show_all, not just empty text) until there's
        # actually a password-change step to announce - appearing out of
        # nowhere right above the password field is exactly the point,
        # see set_status()'s own comment.
        self.status_label = Gtk.Label(label="")
        self.status_label.get_style_context().add_class("cosmos-greeter-status")
        self.status_label.set_xalign(0.5)
        self.status_label.set_justify(Gtk.Justification.CENTER)
        self.status_label.set_line_wrap(True)
        self.status_label.set_no_show_all(True)
        form.pack_start(self.status_label, False, False, 0)

        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_placeholder_text("Password")
        self.password_entry.get_style_context().add_class("cosmos-greeter-entry")
        self.password_entry.connect("activate", self.on_login_clicked)
        # Direct request: switch users with Up/Down instead of needing
        # to click the combo box, with the password field kept focused
        # the whole time rather than requiring an extra click into it
        # first (see main()'s own grab_focus() call). Up/Down rather
        # than Left/Right (an earlier draft of this) specifically
        # because a single-line GtkEntry has no default binding for
        # Up/Down at all, unlike Left/Right's built-in
        # moves-the-cursor behavior - nothing to pre-empt or guard
        # around, safe to intercept unconditionally regardless of
        # whether the field already has text in it.
        self.password_entry.connect("key-press-event", self.on_password_key_press)
        form.pack_start(self.password_entry, False, False, 0)

        self.error_label = Gtk.Label(label="")
        self.error_label.get_style_context().add_class("cosmos-greeter-error")
        self.error_label.set_xalign(0.5)
        self.error_label.set_justify(Gtk.Justification.CENTER)
        self.error_label.set_line_wrap(True)
        self.error_label.set_max_width_chars(34)
        self.error_label.set_no_show_all(True)
        form.pack_start(self.error_label, False, False, 0)

        login_button = Gtk.Button(label="Log In")
        login_button.get_style_context().add_class("cosmos-greeter-button-accent")
        login_button.connect("clicked", self.on_login_clicked)
        form.pack_start(login_button, False, False, 0)

        center.pack_start(card, False, False, 0)

        # Direct request: hints at the bottom center to make login
        # easier - the Up/Down-switches-users and always-focused
        # password field behavior (see the password entry's own
        # key-press-event connect() and main()'s grab_focus() call)
        # aren't things anyone would otherwise guess are there.
        hint_text = "↑/↓ to switch users  ·  Enter to log in"
        if GAMEBOX_SESSION in self.session_keys:
            hint_text += "  ·  F2 / Guide to switch Desktop / GameBox"
        hint_label = Gtk.Label(label=hint_text)
        hint_label.get_style_context().add_class("cosmos-greeter-hint")

        # get-child-position, not plain halign/valign - real, live bug
        # this fixes for BOTH overlay children above (confirmed via an
        # isolated Xvfb reproduction, not guessed; also independently
        # confirmed live - "I would like the card to be dead center of
        # the screen"): Gtk.Overlay's automatic halign/valign positions
        # an overlay child against the OVERLAY WIDGET'S OWN allocated
        # size, which here is driven by its main child - the background
        # image above, loaded at its own native resolution
        # (GdkPixbuf.Pixbuf.new_from_file, not _at_scale) rather than
        # scaled to the real screen size. That left center's own
        # CENTER/CENTER visibly off true screen-center whenever the
        # image's native size didn't exactly match the real display, and
        # fully hid hint_label below the visible viewport entirely (its
        # valign=END anchored it to the bottom of the image's own
        # geometry, not the real screen). Same fix cosmos-bar.py's own
        # build_top_bar() already needed for its centered clock, for the
        # exact same underlying reason (see that function's own
        # comment) - computed here against the real screen size instead.
        def position_overlay_child(_overlay, widget, allocation):
            screen = Gdk.Screen.get_default()
            if widget is shadow:
                allocation.x = 0
                allocation.y = 0
                allocation.width = screen.get_width()
                allocation.height = screen.get_height()
                return True
            _min_size, natural_size = widget.get_preferred_size()
            allocation.width = natural_size.width
            allocation.height = natural_size.height
            allocation.x = (screen.get_width() - natural_size.width) // 2
            if widget is center:
                allocation.y = (screen.get_height() - natural_size.height) // 2
            elif widget is hint_label:
                allocation.y = screen.get_height() - natural_size.height - 24
            else:
                return False
            return True

        overlay.connect("get-child-position", position_overlay_child)
        overlay.add_overlay(center)
        overlay.add_overlay(hint_label)

        self.update_avatar()
        self.apply_user_mode()
        self.update_clock()
        GLib.timeout_add_seconds(1, self.update_clock)

        # Deliberately NOT starting authentication here. This used to
        # call self.start_authentication() eagerly on load, which is
        # the actual root cause of "Incorrect password" showing before
        # anyone had typed anything: it opens a PAM conversation
        # immediately, show-prompt fires for the password, and
        # on_show_prompt responds with whatever's in password_entry at
        # that instant - empty, at startup - so it fails right away.
        # Authentication now only ever starts from an explicit submit
        # (see on_login_clicked), same reasoning as
        # on_authentication_complete's own comment on not auto-retrying.

    def update_clock(self):
        import time
        self.clock_label.set_text(time.strftime("%H:%M"))
        return True

    def selected_user(self):
        idx = self.user_combo.get_active()
        if idx < 0 or idx >= len(self.users):
            return None
        return self.users[idx]

    def update_avatar(self):
        user = self.selected_user()
        path = user.get_image() if user else None
        try:
            if not path:
                raise GLib.Error("no avatar")
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, AVATAR_SIZE * self.avatar_scale, AVATAR_SIZE * self.avatar_scale, False)
        except GLib.Error:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(LOGO_PATH, AVATAR_SIZE * self.avatar_scale, AVATAR_SIZE * self.avatar_scale, True)
            except GLib.Error:
                pixbuf = None
        self.avatar_pixbuf = pixbuf
        self.avatar_area.queue_draw()

    def on_draw_avatar(self, _widget, cr):
        if self.avatar_pixbuf is None:
            return False
        half = AVATAR_SIZE / 2
        cr.move_to(half, 0)
        cr.line_to(AVATAR_SIZE, half)
        cr.line_to(half, AVATAR_SIZE)
        cr.line_to(0, half)
        cr.close_path()
        cr.clip()
        cosmos_display.paint_pixbuf(cr, self.avatar_pixbuf, self.avatar_scale)
        return False

    def show_error(self, text):
        """Shows (or, for empty text, hides) the big failure banner."""
        self.error_label.set_text(text)
        self.error_label.set_visible(bool(text))

    def set_status(self, text):
        # The obvious, can't-miss version of a password-change step -
        # real text sitting above the password field the whole time,
        # not a placeholder that vanishes the moment someone starts
        # typing (see the CSS class's own comment on the bug this
        # replaces).
        self.status_label.set_text(text)
        self.status_label.set_visible(bool(text))

    def update_mode_button(self):
        self.mode_button.set_label(
            "Mode: GameBox (F2)" if self.mode == GAMEBOX_SESSION else "Mode: Desktop (F2)"
        )

    def toggle_mode(self):
        if GAMEBOX_SESSION not in self.session_keys:
            return
        self.mode = DESKTOP_SESSION if self.mode == GAMEBOX_SESSION else GAMEBOX_SESSION
        self.update_mode_button()

    def apply_user_mode(self):
        """Starts each user in whichever mode they last logged into - remembered per
        user by cosmos_login (greetd has no such memory of its own), so the switch
        doesn't reset to the desktop on every boot for someone who normally uses
        GameBox."""
        user = self.selected_user()
        remembered = login.remembered_session(user.get_name()) if user else None
        self.mode = remembered if remembered in (DESKTOP_SESSION, GAMEBOX_SESSION) and remembered in self.session_keys else DESKTOP_SESSION
        self.update_mode_button()

    def on_user_changed(self, _combo):
        # Cancel first, not just start a new one on top - real, live
        # bug (reported: "when the second number is typed it erases
        # the field") traced to this same pattern: starting a new
        # authenticate() call while a previous conversation might still
        # be alive races whichever one's show-prompt fires next against
        # whatever the entry currently holds, not necessarily what the
        # user meant to submit.
        if self.greeter.in_authentication:
            self.greeter.cancel()
        self.update_avatar()
        self.apply_user_mode()
        self.password_entry.set_text("")
        self.password_entry.set_placeholder_text("Password")
        self.show_error("")
        self.set_status("")

    def switch_user(self, direction):
        if len(self.users) < 2:
            return
        idx = self.user_combo.get_active()
        idx = (idx + direction) % len(self.users)
        self.user_combo.set_active(idx)

    def on_password_key_press(self, _widget, event):
        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self.switch_user(-1 if event.keyval == Gdk.KEY_Up else 1)
            return True
        # F2, or a controller's Guide/Home button (cosmos-input sends it as
        # Help) - neither can collide with a character typed into a password.
        if event.keyval in (Gdk.KEY_F2, Gdk.KEY_Help):
            self.toggle_mode()
            return True
        if event.keyval == Gdk.KEY_Escape:
            # Backs out of a login attempt in progress (wrong PIN typed, wrong profile picked) instead of leaving you stuck watching it fail -
            # same cancel-and-reset on_user_changed already does when you switch profiles mid-attempt. There is nothing "behind" the login
            # screen to return to (this is the first thing greetd starts), so this cancels the attempt, not the screen itself.
            if self.greeter.in_authentication:
                self.greeter.cancel()
            self.password_entry.set_text("")
            self.password_entry.set_placeholder_text("Password")
            self.show_error("")
            self.set_status("")
            return True
        return False

    def start_authentication(self):
        user = self.selected_user()
        if user:
            # Reset for the new conversation - see on_show_prompt's own
            # comment on why only the FIRST secret prompt of a
            # conversation auto-responds with whatever's already typed.
            self._first_secret_prompt = True
            self.changing_password = False
            self.last_pam_error = ""
            self.set_status("")
            self.greeter.authenticate(user.get_name())

    def on_show_prompt(self, _greeter, text, prompt_type):
        # SECRET prompts (the password) are the only ones this greeter
        # actually has UI for - a QUESTION prompt (some PAM modules use
        # these for extra, non-secret info) has nowhere to go here, so
        # it's answered with an empty response rather than left
        # hanging, matching how minimal greeters like this one are
        # expected to degrade rather than needing a UI for every
        # conceivable PAM module's own custom prompts.
        if prompt_type != login.PROMPT_SECRET:
            self.greeter.respond("")
            return

        # Decide what this SECRET prompt actually IS by its own text,
        # not by counting - a real, live bug this replaces (confirmed
        # via a headless serial-console reproduction, not guessed): for
        # THIS account specifically (empty stored hash, see the
        # Containerfile's own `usermod -p ''` comment), pam_unix's auth
        # step needs no password at all with nullok set and never emits
        # a SECRET prompt for one - the very first, and only, SECRET
        # prompt in the whole conversation is already "New password:",
        # straight from the forced-expiry chauthtok flow. The old
        # "the first secret prompt is always the login password"
        # assumption auto-answered that with the login field's stale
        # blank contents, instantly failing with "BAD PASSWORD: No
        # password supplied" before anyone could type a real one - and
        # since that's also the ONLY prompt, this happened on every
        # single login attempt, unconditionally.
        lowered = text.strip().lower()
        is_change_prompt = any(w in lowered for w in ("new", "retype", "confirm", "again"))

        if self._first_secret_prompt and not is_change_prompt:
            # A genuine login-password prompt (a different account with
            # a real password hash would still get one) - the person
            # already typed this and submitted (see on_login_clicked)
            # before this signal ever fires, so responding immediately
            # with whatever's already in the field is correct here.
            self._first_secret_prompt = False
            self.last_typed = self.password_entry.get_text()
            self.greeter.respond(self.last_typed)
            return
        self._first_secret_prompt = False
        self.changing_password = True

        # A password-change step (whether it's literally the first
        # SECRET prompt of this conversation, as above, or a later one
        # after a real login password on a different account) - direct
        # requirement (the account this greeter authenticates ships
        # with its password pre-expired, see the Containerfile's own
        # `passwd -e user` comment, forcing PAM's real chauthtok flow:
        # "New password:" then "Retype new password:", PAM's own
        # get-and-verify helper already requires it be typed twice and
        # rejects a mismatch on its own - nothing extra needed here for
        # that part). Nothing has been typed FOR this specific question
        # yet - auto-responding with the old field contents would just
        # resend stale text as the "new" password. Show what PAM is
        # actually asking for and wait for an explicit fresh submit
        # instead; on_login_clicked's own get_in_authentication()
        # branch already responds to whatever comes next, no separate
        # handling needed here.
        #
        # set_status() below is the other real fix this same report
        # asked for ("make it more obvious"): this used to only change
        # the entry's placeholder, which is small, dim, and gone the
        # instant anyone types - easy to miss, and missing it means
        # submitting a still-blank "new password", which pam_pwquality
        # rejects outright, surfacing as a generic "Login failed" with
        # no clue why. A big banner that stays on screen names the
        # actual step instead.
        if "retype" in lowered or "confirm" in lowered or "again" in lowered:
            self.set_status("Type the new password again to confirm it")
        else:
            self.set_status("Your password has expired - enter a new one below")
        self.password_entry.set_placeholder_text(text)
        self.password_entry.set_text("")
        self.show_error("")
        self.password_entry.grab_focus()

    def on_show_message(self, _greeter, text, message_type):
        if message_type == login.MESSAGE_ERROR:
            self.last_pam_error = text     # kept: the failure banner below explains it
        else:
            # PAM's own heads-up that the account is expired
            # ("You are required to change your password immediately...")
            # arrives as an INFO message, not a prompt, right after the
            # blank-password login succeeds - used to be silently
            # dropped here (only ERROR was ever shown), so the very
            # first signal that anything was about to change was the
            # placeholder text swap this whole banner replaces. Showing
            # it gives the earliest possible warning, on the same
            # screen, before the new-password prompt even arrives.
            self.set_status(text)

    def on_authentication_complete(self, _greeter):
        if self.greeter.authenticated:
            # The picked mode; the desktop whenever the gamebox session isn't installed.
            session = self.mode if GAMEBOX_SESSION in self.session_keys else DESKTOP_SESSION
            command = self.sessions.get(session) or self.sessions.get(DESKTOP_SESSION)
            try:
                self.greeter.start_session(command)
            except (login.GreetdError, TypeError) as exc:
                self.show_error(f"Couldn't start the session\n{exc}")
                return
            user = self.selected_user()
            if user:
                login.remember_session(user.get_name(), session)
            # greetd stops this greeter once the session is up; leave nothing behind.
            Gtk.main_quit()
        else:
            # No automatic start_authentication() here anymore - a
            # real, live bug (reported: "when the second number is
            # typed it erases the field"), root-caused by this exact
            # line. This used to immediately open a new conversation
            # right as this line ran, which opens a new show-prompt
            # that can land in the middle of the next few keystrokes
            # someone's already typing (this signal fires the instant
            # PAM finishes, with no way to know a person is mid-type at
            # that exact moment) - whatever partial text happened to be
            # in the entry at that instant gets submitted as if it were
            # the whole password, fails, clears the field, and repeats,
            # which is exactly what looked like "the second digit
            # erases everything." Waiting for the next explicit submit
            # (Enter/Log In - see on_login_clicked) instead means a
            # fresh authenticate() only ever starts right when someone
            # has genuinely finished typing and asked to log in.
            #
            # Generic wording, not "Incorrect password" - this same
            # failure path also covers a rejected forced password
            # change (mismatched confirmation, or pam_pwquality
            # rejecting it as too weak/too similar), where "incorrect
            # password" would be actively wrong. Placeholder reset back
            # to "Password" so a retry starts the whole conversation
            # (login prompt first) with the right label again, not
            # whatever the failed attempt last showed.
            headline, detail = login.describe_failure(
                self.changing_password, self.last_pam_error, typed_something=bool(self.last_typed))
            self.show_error(f"{headline}\n{detail}")
            self.changing_password = False
            self.last_pam_error = ""
            self.set_status("")
            self.password_entry.set_text("")
            self.password_entry.set_placeholder_text("Password")
            self.password_entry.grab_focus()

    def on_login_clicked(self, _widget):
        self.show_error("")
        # Same real bug this whole method's redesign traces back to
        # (see on_authentication_complete's own comment): this used to
        # only ever respond() to an authentication already left running
        # from __init__'s own eager, on-load authenticate() call - the
        # actual root cause, since that call fired before anyone had
        # typed anything at all, then kept re-arming itself on every
        # failure regardless of what someone was doing at that exact
        # moment. Now nothing starts a conversation until this handler
        # itself does, right when someone submits, so the password this
        # sends is always whatever they'd actually finished typing.
        if self.greeter.in_authentication:
            self.last_typed = self.password_entry.get_text()
            self.greeter.respond(self.last_typed)
        else:
            self.start_authentication()


def main():
    # This greeter is started by greetd, never through cosmos-session, so it never
    # inherits that script's XCURSOR_THEME export. Real, live bug this fixes
    # (reported: default cursor outside the login card) - setting it directly as a
    # GtkSetting makes GDK's own cursor lookups resolve correctly regardless of what
    # environment greetd launched this process with.
    settings = Gtk.Settings.get_default()
    settings.set_property("gtk-cursor-theme-name", "Bibata-Modern-Classic")
    settings.set_property("gtk-cursor-theme-size", 24)

    provider = Gtk.CssProvider()
    provider.load_from_data(cosmos_display.scale_css(CSS).encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
    )
    # Controller-only login: cosmos-input turns a gamepad into keyboard/mouse
    # for this screen too (d-pad = switch users, A = log in, Select = on-screen
    # keyboard for the password, Guide = Desktop/GameBox). Best effort - if it
    # isn't installed or can't open /dev/uinput as the greeter user, the
    # greeter works exactly as before with a real keyboard.
    input_proc = None
    try:
        import subprocess
        input_proc = subprocess.Popen(["cosmos-input"])
        import atexit
        atexit.register(lambda: input_proc.terminate())
    except OSError:
        pass
    win = CosmosGreeter()
    win.show_all()
    # Direct request: the password field should always have focus, not
    # need an extra click into it first - GTK's own default focus-on-show
    # behavior would otherwise land on whatever's first in tab order
    # (the user combo), same reason Up/Down needs to be handled on the
    # entry itself rather than the combo (see that connect() call's own
    # comment).
    win.password_entry.grab_focus()
    # Direct request: no mouse cursor at all on this screen - a real,
    # invisible Gdk.Cursor (BLANK_CURSOR), not just hiding it via theme
    # tricks, applied to the window's own GdkWindow once it actually has
    # one (only true after show_all() realizes it). Everything here is
    # meant to be reachable by keyboard alone (Up/Down for users, Tab
    # for Restart/Shutdown, Enter to submit - see this file's own
    # comments on each), so a visible pointer has nothing left to do on
    # this particular screen.
    blank_cursor = Gdk.Cursor.new_for_display(Gdk.Display.get_default(), Gdk.CursorType.BLANK_CURSOR)
    win.get_window().set_cursor(blank_cursor)
    Gtk.main()


if __name__ == "__main__":
    main()
