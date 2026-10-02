#!/usr/bin/env python3
"""How CosmOS apps follow the screen they are on.

Two things decide how big an app looks, and they are kept apart:

  * The compositor (cosmos-wm) chooses the OUTPUT SCALE: 1x for an ordinary monitor, 2x for a 4K TV, whole quarters in between, from the monitor's
    density (or its height for a TV) or from ~/.config/cosmos/display.conf. GTK then draws everything at that scale by itself, and the size Gdk
    reports for a monitor is the LOGICAL size (pixels divided by the scale), so an app never divides by the scale itself.
  * Each app fits itself into the logical size it is given: a window is never larger than the screen, and on a logical screen bigger than the
    1920x1080 the layouts were drawn for, windows and the on-screen keyboard grow with it (`factor`).

Images that are drawn by hand (avatars, icons, previews) must be loaded at the monitor's scale times their size and drawn at 1/scale, or they are
soft on a scaled output: `load_pixbuf`, `paint_pixbuf` and `image_from_pixbuf` do that.

Everything that needs a display imports Gdk only when called, so the arithmetic below (`fit_size`, `ui_factor`) can be tested without one."""

REFERENCE_WIDTH = 1920
REFERENCE_HEIGHT = 1080
FALLBACK_SIZE = (REFERENCE_WIDTH, REFERENCE_HEIGHT)
SCREEN_SHARE = 0.92            # a window is at most this share of the screen on each axis, so it never covers the bar or runs off an edge
MAX_FACTOR = 3.0


def ui_factor(width, height):
    """How much larger than the 1920x1080 layouts a logical screen is (never below 1, so a small screen fits by clamping, not by shrinking text)."""
    if width <= 0 or height <= 0:
        return 1.0
    return min(MAX_FACTOR, max(1.0, min(width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT)))


def fit_size(width, height, screen_width, screen_height, share=SCREEN_SHARE):
    """The window size to ask for: the wanted size grown by ui_factor, but at most `share` of the screen on each axis. A height of -1 (natural
    size) stays -1."""
    factor = ui_factor(screen_width, screen_height)
    fitted_w = min(int(round(width * factor)), int(screen_width * share)) if width > 0 else width
    fitted_h = min(int(round(height * factor)), int(screen_height * share)) if height > 0 else height
    return fitted_w, fitted_h


def _monitor(widget=None):
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is None:
        return None
    monitor = None
    window = widget.get_window() if widget is not None and hasattr(widget, "get_window") else None
    if window is not None:
        monitor = display.get_monitor_at_window(window)
    return monitor or display.get_primary_monitor() or (display.get_monitor(0) if display.get_n_monitors() else None)


def logical_size(widget=None):
    """(width, height) of the screen the widget is on (or the primary one), in logical pixels."""
    monitor = _monitor(widget)
    if monitor is None:
        return FALLBACK_SIZE
    geometry = monitor.get_geometry()
    return geometry.width, geometry.height


def monitor_scale(widget=None):
    """The whole-number scale GTK draws at on that monitor (a 1.5x output reports 2)."""
    monitor = _monitor(widget)
    if monitor is None:
        return 1
    return max(1, monitor.get_scale_factor())


def factor(widget=None):
    return ui_factor(*logical_size(widget))


def px(value, widget=None):
    """A layout size drawn for 1920x1080, grown for a bigger logical screen."""
    return max(1, int(round(value * factor(widget))))


_PX = None


def scale_css(css, widget=None):
    """CSS with every `Npx` length multiplied by the UI factor (str in, str out; bytes in, bytes out). Apps write their CSS for 1920x1080 and pass it
    through this before loading it."""
    import re
    global _PX
    if isinstance(css, bytes):
        return scale_css(css.decode("utf-8"), widget).encode("utf-8")
    multiplier = factor(widget)
    if multiplier == 1.0:
        return css
    if _PX is None:
        _PX = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)px\b")
    def grow(match):
        value = float(match.group(1)) * multiplier
        return f"{int(round(value))}px" if value >= 1 or float(match.group(1)) == 0 else "1px"
    return _PX.sub(grow, css)


def screen_fraction(fraction, widget=None):
    """A width that is `fraction` of the screen's logical width."""
    return int(logical_size(widget)[0] * fraction)


def size_window(window, width, height):
    """set_default_size() that fits the screen: use this instead of a fixed size. It also makes the window itself unable to be wider or taller than the
    screen (`make_fit`, run when the window is first shown), so no app runs off the edge whatever its content."""
    window.set_default_size(*fit_size(width, height, *logical_size(window)))
    fit_on_show(window)


def fit_on_show(window, scroll=True):
    """Runs make_fit on the window the first time it is shown. `scroll=False` leaves the content as it is (for full-screen windows and layer-shell
    surfaces whose layout is their own) and only wraps text and caps fixed sizes."""
    if not getattr(window, "_cosmos_fit_hooked", False):
        window._cosmos_fit_hooked = True
        window.connect("show", lambda w: make_fit(w, scroll=scroll))


def make_fit(window, share=SCREEN_SHARE, scroll=True):
    """Takes away everything that keeps a window from being smaller than the screen, once, when it is first shown:
      * text that can't wrap forces the window as wide as one line of it, so every label that isn't already wrapping or shortened with an ellipsis
        is set to wrap;
      * a fixed size request bigger than `share` of the screen is capped to it;
      * the window's content goes in a scrolled window (unless it already is one), so what still doesn't fit scrolls instead of being cut off."""
    if getattr(window, "_cosmos_fitted", False):
        return
    window._cosmos_fitted = True
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Pango
    screen_w, screen_h = logical_size(window)
    limit_w, limit_h = int(screen_w * share), int(screen_h * share)

    def walk(widget):
        yield widget
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                yield from walk(child)

    for widget in walk(window):
        if isinstance(widget, Gtk.Label):
            if not widget.get_line_wrap() and widget.get_ellipsize() == Pango.EllipsizeMode.NONE:
                widget.set_line_wrap(True)
                widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        want_w, want_h = widget.get_size_request()
        if want_w > limit_w or want_h > limit_h:
            widget.set_size_request(min(want_w, limit_w) if want_w > 0 else want_w, min(want_h, limit_h) if want_h > 0 else want_h)
    child = window.get_child()
    if scroll and child is not None and not isinstance(child, Gtk.ScrolledWindow):
        window.remove(child)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_shadow_type(Gtk.ShadowType.NONE)
        scroller.set_propagate_natural_width(True)
        scroller.set_propagate_natural_height(True)
        scroller.add(child)
        window.add(scroller)
        scroller.show()


# ---- crisp images on scaled outputs -------------------------------------------------------------------------------------------------------

def load_pixbuf(path, size, widget=None, preserve_aspect=False):
    """A pixbuf for a `size` x `size` (logical) slot, loaded at the monitor's scale so it is sharp; pass the scale to paint_pixbuf/image_from_pixbuf."""
    from gi.repository import GdkPixbuf
    scale = monitor_scale(widget)
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size * scale, size * scale, preserve_aspect), scale


def paint_pixbuf(cr, pixbuf, scale, x=0, y=0):
    """Draws a pixbuf that was loaded at `scale` times its logical size at logical (x, y)."""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    cr.save()
    cr.scale(1.0 / scale, 1.0 / scale)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, x * scale, y * scale)
    cr.paint()
    cr.restore()


def set_image_from_file(image, path, width, height, preserve_aspect=True):
    """Shows an image file in a Gtk.Image at (at most) width x height logical pixels, loaded at the monitor's scale so it is sharp. Raises GLib.Error
    like GdkPixbuf does when the file can't be read."""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf
    scale = monitor_scale(image)
    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width * scale, height * scale, preserve_aspect)
    image.set_from_surface(Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None))


def image_from_pixbuf(pixbuf, scale):
    """A Gtk.Image showing a pixbuf that was loaded at `scale` times its logical size."""
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk
    surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)
    return Gtk.Image.new_from_surface(surface)


# ---- for the session script (no display connection needed) ------------------------------------------------------------------------------

def compositor_factor():
    """ui_factor of the compositor's primary output, asked over its control socket; 1.0 when there is no compositor to ask."""
    import os
    import socket
    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "cosmos-wm.sock")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(path)
            sock.sendall(b"output\n")
            reply = sock.recv(64).decode().strip()
        width, height = (int(v) for v in reply.split("x"))
        return ui_factor(width, height)
    except (OSError, ValueError):
        return 1.0


def main(argv):
    """`cosmos_display.py dpi-scale` prints the text scale (GDK_DPI_SCALE) the session should use for the compositor's screen."""
    import sys
    if argv == ["dpi-scale"]:
        print(f"{compositor_factor():.2f}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
