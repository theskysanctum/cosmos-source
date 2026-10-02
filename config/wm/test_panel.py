import gi, sys
gi.require_version("Gtk","3.0"); gi.require_version("GtkLayerShell","0.1")
from gi.repository import Gtk, GtkLayerShell as L
kind=sys.argv[1]
w=Gtk.Window(); L.init_for_window(w)
if kind=="bar":
    L.set_layer(w,L.Layer.TOP); L.set_anchor(w,L.Edge.TOP,True); L.set_anchor(w,L.Edge.LEFT,True); L.set_anchor(w,L.Edge.RIGHT,True)
    w.set_size_request(-1,40); L.auto_exclusive_zone_enable(w)
else:
    L.set_layer(w,L.Layer.BACKGROUND)
    for e in (L.Edge.TOP,L.Edge.BOTTOM,L.Edge.LEFT,L.Edge.RIGHT): L.set_anchor(w,e,True)
    L.set_keyboard_mode(w,L.KeyboardMode.ON_DEMAND)
    L.set_exclusive_zone(w,-1)
w.add(Gtk.Label(label=kind)); w.show_all(); Gtk.main()
