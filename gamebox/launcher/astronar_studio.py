#!/usr/bin/env python3
"""Astronar Studio - everything an Astronar is capable of, can be
extended with, and can be modified, in one visual tool with a single
shared live preview (see this module's own _tick()), four tabs:

- Item: turns a hair/item mesh into a real "bodypart" avatar plugin
  (see avatar_plugin_backend.py's own module docstring for the plugin
  format, and obj_mesh.py for the mesh-loading/buffer-writing bridge
  this and every plugin it generates share). Two ways to author the
  mesh, both ending up as the same `triangles` list (obj_mesh.py's own
  shape) so everything downstream (position/rotate/scale, which joint,
  customizer fields, export) works identically either way:
    - "Build from shapes" (the default, no external tool needed - a
      direct request: "Blender is more advanced than necessary for the
      Astronar Meshes") - stack/scale/color box/pyramid/prism/cylinder/
      sphere primitives directly on the live rig (this whole avatar
      generator is deliberately "Minecraft-blocky" - see avatar3d.py's
      own docstring - so a small primitive set fits its own established
      visual language, not a limitation next to it).
    - "Import Mesh (.obj)" - for something sculpted elsewhere, for
      anyone who wants more than these primitives.
- Motion: poses/animations, authored as a closed loop of keyframes
  (motion_keyframes.py) set directly on the live rig - also no external
  tool needed, exports a real "motion" plugin.
- Character: EVERY avatar3d_viewer.CUSTOM_FIELDS entry - core body/
  face/clothing/accessibility/pet traits and every built-in/installed
  bodypart plugin's own contributed fields alike - editable live
  against the one shared rig every other tab also previews against, so
  an item/motion can be checked against a real, specific character
  instead of only a flat neutral one. Literally "everything an Astronar
  is capable of", not just the plugin-authoring half of that.
- Manage: browse every installed AND built-in bodypart/motion plugin;
  Load for Editing reloads an installed one's real data back into the
  Item/Motion tab (mesh.obj + POS/HPR/JOINT/SCALE for bodyparts,
  KEYFRAMES for motions - both literally read back off the generated
  module, not re-derived by guesswork); Delete calls avatar_plugin_
  backend.uninstall() directly. Built-in features are listed for
  visibility only - never editable/deletable here, the same real
  protection uninstall() itself already enforces.

Same GTK3 + Panda3D-offscreen-render-to-GdkPixbuf technique
avatar3d_viewer.py's own live preview already uses - not a second,
differently-built preview pipeline."""
import shutil
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debuglog  # noqa: E402

import avatar3d  # noqa: E402
import avatar3d_anim  # noqa: E402
import avatar3d_pet  # noqa: E402
import avatar3d_pet_anim  # noqa: E402
import avatar3d_pet_rig  # noqa: E402
import avatar3d_rig  # noqa: E402
import avatar3d_viewer  # noqa: E402
import avatar_plugin_backend  # noqa: E402
import motion_keyframes  # noqa: E402
import obj_mesh  # noqa: E402

log = debuglog.setup("astronar_studio")

PREVIEW_SIZE = 480

# Every Rig field (see avatar3d_rig.Rig) that's a single, real,
# attachable NodePath - fingers_l/fingers_r are excluded (a list of 4,
# not one pivot) and eyes_closed/mouth_open are excluded (overlay nodes
# with their own special show/hide lifecycle, not general attachment
# points). "neck" is first and the default - it's also where every
# existing head-region built-in (glasses/hat/horns/halo/...) already
# attaches, exactly the same joint a hairstyle/head item needs.
JOINTS = [
	"neck", "waist", "root",
	"shoulder_l", "shoulder_r", "elbow_l", "elbow_r", "wrist_l", "wrist_r", "thumb_l", "thumb_r",
	"hip_l", "hip_r", "knee_l", "knee_r", "ankle_l", "ankle_r",
	"wheel_l", "wheel_r",
]

# Motion mode's own joint list - every JOINTS entry except "root",
# which gets its own position (not rotation) controls, matching
# avatar3d_anim.py's own "root setX/setY/setZ stand in for torso
# sway/lean/bob" convention (see motion_keyframes.py's docstring).
MOTION_JOINTS = [j for j in JOINTS if j != "root"]
DEFAULT_LOOP_SECONDS = 2.0

_DEFAULT_SWATCHES = {
	"Hair": avatar3d.HAIR_COLORS,
}
_GENERIC_SWATCHES = ["#1a1a1a", "#f4f7ff", "#5c1414", "#3355cc", "#1e3d2f", "#6b4423"]

# "Build from boxes" mode's real shape library - box/cube (a cube is
# just a box with dx==dy==dz, no separate case needed), triangular
# prism, (rectangular) pyramid, cylinder, sphere - a deliberately small
# set of primitives, not a modeling toolkit, matching how basic this
# avatar system's own geometry already is (avatar3d.py's own built-in
# body parts are ALL boxes - see its "Minecraft-blocky" docstring; this
# just adds a few more basic solids on top for items/hair that don't
# have to be pure boxes).
#
# Every _*_triangles(cx, cy, cz, dx, dy, dz, color) function here takes
# the exact same (center, size, color) shape - `size` means whatever is
# most natural for that shape (box: full width/depth/height; cylinder/
# sphere: diameter along each axis, so a non-cube `size` gives an
# elliptical cylinder/ellipsoid, not just a uniformly-scaled one) - and
# returns triangles in obj_mesh.load_obj()'s own return format ((pos,
# normal, color) per vertex, 3 per triangle) so every shape ends up as
# the same data an imported .obj's faces already are: one shared
# triangle representation the whole rest of this file (preview,
# export) never needs to know the original shape of.
CYLINDER_SEGMENTS = 16
SPHERE_RINGS, SPHERE_SEGMENTS = 8, 16


def _tri_normal(a, b, c):
	e1 = tuple(b[i] - a[i] for i in range(3))
	e2 = tuple(c[i] - a[i] for i in range(3))
	n = (
		e1[1] * e2[2] - e1[2] * e2[1],
		e1[2] * e2[0] - e1[0] * e2[2],
		e1[0] * e2[1] - e1[1] * e2[0],
	)
	mag = sum(v * v for v in n) ** 0.5 or 1.0
	return tuple(v / mag for v in n)


def _tri(p0, p1, p2, color, normal=None):
	n = normal or _tri_normal(p0, p1, p2)
	return [((p0, n, color), (p1, n, color), (p2, n, color))]


def _quad(p0, p1, p2, p3, color, normal):
	return _tri(p0, p1, p2, color, normal) + _tri(p0, p2, p3, color, normal)


def _box_triangles(cx, cy, cz, dx, dy, dz, color):
	x0, x1 = cx - dx / 2, cx + dx / 2
	y0, y1 = cy - dy / 2, cy + dy / 2
	z0, z1 = cz - dz / 2, cz + dz / 2
	faces = [
		((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
		((0, 0, -1), [(x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)]),
		((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
		((0, -1, 0), [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)]),
		((1, 0, 0), [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)]),
		((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
	]
	triangles = []
	for normal, pts in faces:
		triangles.extend(_quad(pts[0], pts[1], pts[2], pts[3], color, normal))
	return triangles


def _pyramid_triangles(cx, cy, cz, dx, dy, dz, color):
	"""A rectangular-base pyramid filling the same (cx,cy,cz)+(dx,dy,dz)
	box a _box_triangles() call would - base at the box's bottom face,
	apex centered at its top."""
	x0, x1 = cx - dx / 2, cx + dx / 2
	y0, y1 = cy - dy / 2, cy + dy / 2
	z0, z1 = cz - dz / 2, cz + dz / 2
	apex = (cx, cy, z1)
	p0, p1, p2, p3 = (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)
	triangles = list(_quad(p3, p2, p1, p0, color, (0, 0, -1)))
	for a, b in ((p0, p1), (p1, p2), (p2, p3), (p3, p0)):
		triangles.extend(_tri(a, b, apex, color))
	return triangles


def _prism_triangles(cx, cy, cz, dx, dy, dz, color):
	"""A triangular prism - a ridge/wedge cross-section (base corners at
	the box's bottom-front/back edges, a single top ridge running along
	Y at the box's top-center) extruded the box's full depth (dy)."""
	x0, x1 = cx - dx / 2, cx + dx / 2
	y0, y1 = cy - dy / 2, cy + dy / 2
	z0, z1 = cz - dz / 2, cz + dz / 2
	a0, b0, c0 = (x0, y0, z0), (x1, y0, z0), (cx, y0, z1)
	a1, b1, c1 = (x0, y1, z0), (x1, y1, z0), (cx, y1, z1)
	triangles = []
	triangles.extend(_tri(b0, a0, c0, color))  # front cap (outward = -Y)
	triangles.extend(_tri(a1, b1, c1, color))  # back cap (outward = +Y)
	triangles.extend(_quad(a0, b0, b1, a1, color, (0, 0, -1)))  # bottom
	triangles.extend(_tri(a0, a1, c1, color) + _tri(a0, c1, c0, color))  # left slant
	triangles.extend(_tri(b1, b0, c0, color) + _tri(b1, c0, c1, color))  # right slant
	return triangles


def _cylinder_triangles(cx, cy, cz, dx, dy, dz, color):
	"""Axis along Z (matching every other shape's own "height = dz"
	convention) - dx/dy are the two diameters, so a non-equal dx/dy
	gives an elliptical cylinder, not just a stretched circular one."""
	import math
	rx, ry, hz = dx / 2, dy / 2, dz / 2
	z0, z1 = cz - hz, cz + hz
	n = CYLINDER_SEGMENTS
	ring0, ring1 = [], []
	for i in range(n):
		a = 2 * math.pi * i / n
		x, y = cx + rx * math.cos(a), cy + ry * math.sin(a)
		ring0.append((x, y, z0))
		ring1.append((x, y, z1))
	triangles = []
	for i in range(n):
		j = (i + 1) % n
		wall_normal = _tri_normal(ring0[i], ring1[i], ring1[j])
		triangles.extend(_quad(ring0[i], ring1[i], ring1[j], ring0[j], color, wall_normal))
	for i in range(n):
		j = (i + 1) % n
		triangles.extend(_tri((cx, cy, z1), ring1[i], ring1[j], color, (0, 0, 1)))
		triangles.extend(_tri((cx, cy, z0), ring0[j], ring0[i], color, (0, 0, -1)))
	return triangles


def _sphere_triangles(cx, cy, cz, dx, dy, dz, color):
	"""A UV-sphere sized as an ellipsoid by dx/dy/dz (each a full
	diameter along its own axis) - fine detail (SPHERE_RINGS/SEGMENTS)
	is fixed rather than exposed as a control, matching every other
	shape's own "position/size/color only" simplicity."""
	import math
	rx, ry, rz = dx / 2, dy / 2, dz / 2

	def point(lat, lon):
		x = math.sin(lat) * math.cos(lon)
		y = math.sin(lat) * math.sin(lon)
		z = math.cos(lat)
		return (cx + rx * x, cy + ry * y, cz + rz * z)

	triangles = []
	for i in range(SPHERE_RINGS):
		lat0 = math.pi * i / SPHERE_RINGS
		lat1 = math.pi * (i + 1) / SPHERE_RINGS
		for j in range(SPHERE_SEGMENTS):
			lon0 = 2 * math.pi * j / SPHERE_SEGMENTS
			lon1 = 2 * math.pi * (j + 1) / SPHERE_SEGMENTS
			p00, p01 = point(lat0, lon0), point(lat0, lon1)
			p10, p11 = point(lat1, lon0), point(lat1, lon1)
			if i == 0:
				triangles.extend(_tri(p00, p10, p11, color))
			elif i == SPHERE_RINGS - 1:
				triangles.extend(_tri(p00, p10, p01, color))
			else:
				triangles.extend(_tri(p00, p10, p11, color) + _tri(p00, p11, p01, color))
	return triangles


SHAPES = {
	"box": _box_triangles,
	"pyramid": _pyramid_triangles,
	"prism": _prism_triangles,
	"cylinder": _cylinder_triangles,
	"sphere": _sphere_triangles,
}


def _part_triangles(part):
	fn = SHAPES.get(part.get("shape", "box"), _box_triangles)
	cx, cy, cz = part["pos"]
	dx, dy, dz = part["size"]
	return fn(cx, cy, cz, dx, dy, dz, part["color"])


def _parts_to_triangles(parts):
	triangles = []
	for part in parts:
		triangles.extend(_part_triangles(part))
	return triangles


def _parts_to_obj_mtl_text(parts):
	"""Renders `parts` as real Wavefront .obj + .mtl text (WITH explicit
	`vn` lines this time, unlike a hand-authored box-only version might
	skip - several of these shapes, e.g. sphere, have per-vertex normals
	that a flat per-face cross-product recompute on load couldn't
	recover correctly) - so "Build from boxes" goes through EXACTLY the
	same export path (a real mesh.obj file, loaded back at runtime by
	obj_mesh.load_obj() the same as any imported mesh) as the Import
	Mesh path, rather than needing its own separate bodypart.py
	generator to keep in sync with obj_mesh.py's own contract."""
	verts, normals = [], []
	obj_lines = ["mtllib mesh.mtl"]
	mtl_lines = []
	mtl_by_color = {}
	for part in parts:
		color = tuple(round(c, 4) for c in part["color"][:3])
		if color not in mtl_by_color:
			mtl_name = f"mat{len(mtl_by_color)}"
			mtl_by_color[color] = mtl_name
			mtl_lines.append(f"newmtl {mtl_name}")
			mtl_lines.append(f"Kd {color[0]} {color[1]} {color[2]}")
		obj_lines.append(f"usemtl {mtl_by_color[color]}")
		for triangle in _part_triangles(part):
			face_idxs = []
			for pos, normal, _color in triangle:
				verts.append(pos)
				normals.append(normal)
				face_idxs.append((len(verts), len(normals)))
			obj_lines.append("f " + " ".join(f"{v}//{n}" for v, n in face_idxs))
	vert_lines = [f"v {x} {y} {z}" for x, y, z in verts]
	normal_lines = [f"vn {x} {y} {z}" for x, y, z in normals]
	return "\n".join(vert_lines + normal_lines + obj_lines) + "\n", "\n".join(mtl_lines) + "\n"


def _slugify(name):
	out = []
	for ch in name.strip().lower():
		if ch.isalnum():
			out.append(ch)
		elif out and out[-1] != "_":
			out.append("_")
	return "".join(out).strip("_") or "item"


class AstronarStudio(Gtk.Window):
	def __init__(self):
		super().__init__(title="Astronar Studio")
		self.set_default_size(980, 620)
		self.connect("destroy", Gtk.main_quit)

		self.triangles = []
		self.mesh_source_mode = "build"  # "build" (boxes, no external tool) or "import" (.obj)
		self.mesh_source_dir = None
		self.mesh_source_name = None
		self.parts = []
		self.current_part = -1
		self.item_np = None
		self._t0 = time.time()

		# Motion mode state - a closed loop of poses (see
		# motion_keyframes.py), edited one keyframe/joint at a time and
		# played back live via the exact same interpolation an exported
		# motion plugin uses.
		self.keyframes = [{}]
		self.current_kf = 0
		self.motion_joint = "neck"
		self.motion_playing = False
		self._motion_t0 = time.time()

		# Character mode state - the FULL real trait dict (every
		# avatar3d_viewer.CUSTOM_FIELDS entry: body/face/clothing/
		# accessories/creature features/pet/accessibility - literally
		# everything an Astronar can be, core traits and every built-in
		# AND installed bodypart plugin's own contributed fields alike),
		# not just NEUTRAL_TRAITS - Item/Motion tabs preview against
		# WHATEVER this is currently set to, so an item/motion can be
		# checked against a real, specific character instead of only a
		# flat neutral one.
		avatar3d_viewer.refresh_custom_fields()
		self.character_traits = dict(avatar3d_viewer.NEUTRAL_TRAITS)
		self.pet_rig = None
		self.rig = None
		self._rebuild_character()

		root_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin=12)
		self.add(root_box)

		self.image = Gtk.Image()
		preview_frame = Gtk.Frame()
		preview_frame.add(self.image)
		root_box.pack_start(preview_frame, False, False, 0)

		self.notebook = Gtk.Notebook()
		root_box.pack_start(self.notebook, True, True, 0)

		form = Gtk.Grid(row_spacing=8, column_spacing=8, margin=8)
		self.notebook.append_page(form, Gtk.Label(label="Item (hair/accessory)"))
		row = 0

		mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
		self.build_radio = Gtk.RadioButton.new_with_label_from_widget(None, "Build from shapes")
		self.import_radio = Gtk.RadioButton.new_with_label_from_widget(self.build_radio, "Import Mesh (.obj)")
		self.build_radio.set_active(True)
		self.build_radio.connect("toggled", self._on_mode_toggled)
		mode_box.pack_start(self.build_radio, False, False, 0)
		mode_box.pack_start(self.import_radio, False, False, 0)
		form.attach(mode_box, 0, row, 2, 1)
		row += 1

		self.mesh_stack = Gtk.Stack()
		form.attach(self.mesh_stack, 0, row, 2, 1)
		row += 1

		self.mesh_stack.add_titled(self._build_shapes_page(), "build", "Build")
		self.mesh_stack.add_titled(self._build_import_page(), "import", "Import")
		self.mesh_stack.set_visible_child_name("build")

		form.attach(Gtk.Label(label="Attach to joint:", xalign=0), 0, row, 1, 1)
		self.joint_combo = Gtk.ComboBoxText()
		for j in JOINTS:
			self.joint_combo.append_text(j)
		self.joint_combo.set_active(0)
		self.joint_combo.connect("changed", self._on_transform_changed)
		form.attach(self.joint_combo, 1, row, 1, 1)
		row += 1

		# The real top-of-head height, not LEG_H_ADULT+TORSO_H_ADULT+
		# HEAD_S_ADULT - confirmed live that approximation lands
		# mid-head (the head bundle's actual bounds run a bit taller
		# than a bare HEAD_S cube once eyes/ears/hairstyle bumps are
		# included), which buries a default-positioned item inside the
		# skull instead of sitting on top of it.
		_neck_lo, _neck_hi = self.rig.neck.getTightBounds(self.rig.root)
		default_z = _neck_hi.z
		self.pos_x = self._add_spin(form, row, "Position X:", -3, 3, 0.01, 0.0); row += 1
		self.pos_y = self._add_spin(form, row, "Position Y:", -3, 3, 0.01, 0.0); row += 1
		self.pos_z = self._add_spin(form, row, "Position Z:", -3, 5, 0.01, default_z); row += 1
		self.rot_h = self._add_spin(form, row, "Rotation H:", -180, 180, 1, 0.0); row += 1
		self.rot_p = self._add_spin(form, row, "Rotation P:", -180, 180, 1, 0.0); row += 1
		self.rot_r = self._add_spin(form, row, "Rotation R:", -180, 180, 1, 0.0); row += 1
		self.scale = self._add_spin(form, row, "Scale:", 0.01, 5, 0.01, 1.0); row += 1

		form.attach(Gtk.Separator(), 0, row, 2, 1); row += 1

		form.attach(Gtk.Label(label="Item name:", xalign=0), 0, row, 1, 1)
		self.name_entry = Gtk.Entry()
		self.name_entry.connect("changed", self._on_name_changed)
		form.attach(self.name_entry, 1, row, 1, 1)
		row += 1

		self.id_label = Gtk.Label(label="id: (type a name above)", xalign=0)
		form.attach(self.id_label, 0, row, 2, 1)
		row += 1

		form.attach(Gtk.Label(label="Description:", xalign=0), 0, row, 1, 1)
		self.desc_entry = Gtk.Entry()
		form.attach(self.desc_entry, 1, row, 1, 1)
		row += 1

		form.attach(Gtk.Label(label="Category:", xalign=0), 0, row, 1, 1)
		self.category_combo = Gtk.ComboBoxText.new_with_entry()
		for cat in ["Hair"] + [c for c in avatar3d_viewer.CATEGORIES if c != "Hair"]:
			self.category_combo.append_text(cat)
		self.category_combo.set_active(0)
		form.attach(self.category_combo, 1, row, 1, 1)
		row += 1

		self.enabled_check = Gtk.CheckButton(label="Worn by default")
		self.enabled_check.set_active(True)
		form.attach(self.enabled_check, 0, row, 2, 1)
		row += 1

		self.recolor_check = Gtk.CheckButton(label="Recolorable (adds a color swatch field)")
		self.recolor_check.set_active(True)
		form.attach(self.recolor_check, 0, row, 2, 1)
		row += 1

		form.attach(Gtk.Label(label="Swatch colors (comma-separated hex, blank = a sensible default):", xalign=0), 0, row, 2, 1)
		row += 1
		self.swatches_entry = Gtk.Entry()
		form.attach(self.swatches_entry, 0, row, 2, 1)
		row += 1

		export_btn = Gtk.Button(label="Export Plugin")
		export_btn.connect("clicked", self._on_export_clicked)
		form.attach(export_btn, 0, row, 2, 1)
		row += 1

		self.status_label = Gtk.Label(label="", xalign=0)
		self.status_label.set_line_wrap(True)
		form.attach(self.status_label, 0, row, 2, 1)

		for spin in (self.pos_x, self.pos_y, self.pos_z, self.rot_h, self.rot_p, self.rot_r, self.scale):
			spin.connect("value-changed", self._on_transform_changed)

		self._build_motion_tab()
		self._build_character_tab()
		self._build_manage_tab()
		self._refresh_part_controls()

		GLib.timeout_add(1000 // 30, self._tick)
		self.show_all()

	def _build_motion_tab(self):
		mform = Gtk.Grid(row_spacing=8, column_spacing=8, margin=8)
		self.notebook.append_page(mform, Gtk.Label(label="Motion (poses/animation)"))
		row = 0

		kf_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
		self.kf_label = Gtk.Label(label="Keyframe 1 of 1")
		kf_box.pack_start(self.kf_label, True, True, 0)
		for label, handler in (("<", self._on_kf_prev), (">", self._on_kf_next), ("Add", self._on_kf_add), ("Remove", self._on_kf_remove)):
			btn = Gtk.Button(label=label)
			btn.connect("clicked", handler)
			kf_box.pack_start(btn, False, False, 0)
		mform.attach(kf_box, 0, row, 2, 1)
		row += 1

		mform.attach(Gtk.Label(label="Posing joint:", xalign=0), 0, row, 1, 1)
		self.motion_joint_combo = Gtk.ComboBoxText()
		self.motion_joint_combo.append_text("root (torso sway/lean/bob)")
		for j in MOTION_JOINTS:
			self.motion_joint_combo.append_text(j)
		self.motion_joint_combo.set_active(1)  # "neck"
		self.motion_joint_combo.connect("changed", self._on_motion_joint_changed)
		mform.attach(self.motion_joint_combo, 1, row, 1, 1)
		row += 1

		self.mrot_h = self._add_spin(mform, row, "Rotation H:", -180, 180, 1, 0.0); row += 1
		self.mrot_p = self._add_spin(mform, row, "Rotation P:", -180, 180, 1, 0.0); row += 1
		self.mrot_r = self._add_spin(mform, row, "Rotation R:", -180, 180, 1, 0.0); row += 1
		self.mpos_x = self._add_spin(mform, row, "Root offset X:", -1, 1, 0.01, 0.0); row += 1
		self.mpos_y = self._add_spin(mform, row, "Root offset Y:", -1, 1, 0.01, 0.0); row += 1
		self.mpos_z = self._add_spin(mform, row, "Root offset Z:", -1, 1, 0.01, 0.0); row += 1
		for spin in (self.mrot_h, self.mrot_p, self.mrot_r, self.mpos_x, self.mpos_y, self.mpos_z):
			spin.connect("value-changed", self._on_motion_value_changed)
		self._update_motion_joint_visibility()

		self.play_toggle = Gtk.ToggleButton(label="Play Loop")
		self.play_toggle.connect("toggled", self._on_play_toggled)
		mform.attach(self.play_toggle, 0, row, 2, 1)
		row += 1

		mform.attach(Gtk.Separator(), 0, row, 2, 1); row += 1

		mform.attach(Gtk.Label(label="Motion name:", xalign=0), 0, row, 1, 1)
		self.motion_name_entry = Gtk.Entry()
		self.motion_name_entry.connect("changed", self._on_motion_name_changed)
		mform.attach(self.motion_name_entry, 1, row, 1, 1)
		row += 1

		self.motion_id_label = Gtk.Label(label="id: (type a name above)", xalign=0)
		mform.attach(self.motion_id_label, 0, row, 2, 1)
		row += 1

		mform.attach(Gtk.Label(label="Description:", xalign=0), 0, row, 1, 1)
		self.motion_desc_entry = Gtk.Entry()
		mform.attach(self.motion_desc_entry, 1, row, 1, 1)
		row += 1

		motion_export_btn = Gtk.Button(label="Export Motion")
		motion_export_btn.connect("clicked", self._on_export_motion_clicked)
		mform.attach(motion_export_btn, 0, row, 2, 1)
		row += 1

		self.motion_status_label = Gtk.Label(label="", xalign=0)
		self.motion_status_label.set_line_wrap(True)
		mform.attach(self.motion_status_label, 0, row, 2, 1)

	def _build_import_page(self):
		page = Gtk.Grid(row_spacing=6, column_spacing=6)
		import_btn = Gtk.Button(label="Import Mesh (.obj)...")
		import_btn.connect("clicked", self._on_import_clicked)
		page.attach(import_btn, 0, 0, 2, 1)
		self.mesh_label = Gtk.Label(label="No mesh loaded", xalign=0)
		page.attach(self.mesh_label, 0, 1, 2, 1)
		return page

	def _build_shapes_page(self):
		page = Gtk.Grid(row_spacing=6, column_spacing=6)
		row = 0

		hint = Gtk.Label(
			label="Shape X/Y/Z are local to the item itself (stack shapes near 0,0,0) -\nuse Position X/Y/Z below to place the whole item on the rig.",
			xalign=0,
		)
		hint.get_style_context().add_class("dim-label")
		page.attach(hint, 0, row, 2, 1)
		row += 1

		part_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
		self.part_label = Gtk.Label(label="No shapes yet")
		part_box.pack_start(self.part_label, True, True, 0)
		for label, handler in (("<", self._on_part_prev), (">", self._on_part_next), ("Remove", self._on_remove_part)):
			btn = Gtk.Button(label=label)
			btn.connect("clicked", handler)
			part_box.pack_start(btn, False, False, 0)
		page.attach(part_box, 0, row, 2, 1)
		row += 1

		add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
		add_box.pack_start(Gtk.Label(label="Add:"), False, False, 0)
		for shape_id, label in (("box", "Box/Cube"), ("pyramid", "Pyramid"), ("prism", "Prism"), ("cylinder", "Cylinder"), ("sphere", "Sphere")):
			btn = Gtk.Button(label=label)
			btn.connect("clicked", self._on_add_part, shape_id)
			add_box.pack_start(btn, True, True, 0)
		page.attach(add_box, 0, row, 2, 1)
		row += 1

		self.part_pos_x = self._add_spin(page, row, "Shape X:", -2, 2, 0.01, 0.0); row += 1
		self.part_pos_y = self._add_spin(page, row, "Shape Y:", -2, 2, 0.01, 0.0); row += 1
		self.part_pos_z = self._add_spin(page, row, "Shape Z:", -2, 2, 0.01, 0.0); row += 1
		self.part_size_x = self._add_spin(page, row, "Width (X):", 0.005, 1, 0.01, 0.08); row += 1
		self.part_size_y = self._add_spin(page, row, "Depth (Y):", 0.005, 1, 0.01, 0.08); row += 1
		self.part_size_z = self._add_spin(page, row, "Height (Z):", 0.005, 1, 0.01, 0.08); row += 1
		for spin in (self.part_pos_x, self.part_pos_y, self.part_pos_z, self.part_size_x, self.part_size_y, self.part_size_z):
			spin.connect("value-changed", self._on_part_value_changed)

		color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
		color_box.pack_start(Gtk.Label(label="Shape color:"), False, False, 0)
		self.part_color_btn = Gtk.ColorButton()
		self.part_color_btn.connect("color-set", self._on_part_value_changed)
		color_box.pack_start(self.part_color_btn, False, False, 0)
		page.attach(color_box, 0, row, 2, 1)
		return page

	def _update_part_label(self):
		if not self.parts:
			self.part_label.set_text("No shapes yet")
		else:
			shape = self.parts[self.current_part].get("shape", "box")
			self.part_label.set_text(f"Shape {self.current_part + 1} of {len(self.parts)} ({shape})")

	def _refresh_part_controls(self):
		spins = (self.part_pos_x, self.part_pos_y, self.part_pos_z, self.part_size_x, self.part_size_y, self.part_size_z)
		sensitive = bool(self.parts)
		for spin in spins:
			spin.set_sensitive(sensitive)
		self.part_color_btn.set_sensitive(sensitive)
		if not self.parts:
			return
		part = self.parts[self.current_part]
		values = list(part["pos"]) + list(part["size"])
		for spin, value in zip(spins, values):
			spin.handler_block_by_func(self._on_part_value_changed)
			spin.set_value(value)
			spin.handler_unblock_by_func(self._on_part_value_changed)
		rgba = Gdk.RGBA(*part["color"])
		self.part_color_btn.handler_block_by_func(self._on_part_value_changed)
		self.part_color_btn.set_rgba(rgba)
		self.part_color_btn.handler_unblock_by_func(self._on_part_value_changed)

	def _on_mode_toggled(self, radio):
		if not radio.get_active():
			return
		self.mesh_source_mode = "build" if radio is self.build_radio else "import"
		self.mesh_stack.set_visible_child_name(self.mesh_source_mode)
		if self.mesh_source_mode == "build":
			self.triangles = _parts_to_triangles(self.parts)
		self._rebuild_item()

	def _on_add_part(self, _button, shape_id):
		default_size = {"box": (0.08, 0.08, 0.08), "pyramid": (0.1, 0.1, 0.12), "prism": (0.12, 0.08, 0.08), "cylinder": (0.06, 0.06, 0.12), "sphere": (0.08, 0.08, 0.08)}[shape_id]
		self.parts.append({"shape": shape_id, "pos": (0.0, 0.0, 0.0), "size": default_size, "color": (0.6, 0.6, 0.6, 1.0)})
		self.current_part = len(self.parts) - 1
		self._update_part_label()
		self._refresh_part_controls()
		self.triangles = _parts_to_triangles(self.parts)
		self._rebuild_item()

	def _on_remove_part(self, _button):
		if not self.parts:
			return
		del self.parts[self.current_part]
		self.current_part = min(self.current_part, len(self.parts) - 1)
		self._update_part_label()
		self._refresh_part_controls()
		self.triangles = _parts_to_triangles(self.parts)
		self._rebuild_item()

	def _on_part_prev(self, _button):
		if self.parts:
			self.current_part = (self.current_part - 1) % len(self.parts)
			self._update_part_label()
			self._refresh_part_controls()

	def _on_part_next(self, _button):
		if self.parts:
			self.current_part = (self.current_part + 1) % len(self.parts)
			self._update_part_label()
			self._refresh_part_controls()

	def _on_part_value_changed(self, _widget):
		if not self.parts:
			return
		part = self.parts[self.current_part]
		part["pos"] = (self.part_pos_x.get_value(), self.part_pos_y.get_value(), self.part_pos_z.get_value())
		part["size"] = (self.part_size_x.get_value(), self.part_size_y.get_value(), self.part_size_z.get_value())
		rgba = self.part_color_btn.get_rgba()
		part["color"] = (rgba.red, rgba.green, rgba.blue, rgba.alpha)
		self.triangles = _parts_to_triangles(self.parts)
		self._rebuild_item()

	def _add_spin(self, form, row, label, lo, hi, step, default):
		# `row_label` is stashed ON the spin button itself (not looked up
		# via spin.get_parent()) specifically so _update_motion_joint_
		# visibility() can toggle this ONE row's label+spin pair - a
		# real, confirmed-live bug in this method's first version:
		# spin.get_parent() is the shared Grid every row's widgets are
		# attached to, not a per-row container, so hiding "the parent"
		# hid the ENTIRE Motion tab page at once, which made GTK's
		# Notebook fall back to showing the next visible page (Item) -
		# looked exactly like picking a joint from the dropdown randomly
		# kicked the whole app back to the Item tab.
		row_label = Gtk.Label(label=label, xalign=0)
		form.attach(row_label, 0, row, 1, 1)
		adj = Gtk.Adjustment(value=default, lower=lo, upper=hi, step_increment=step)
		spin = Gtk.SpinButton(adjustment=adj, digits=2, climb_rate=step)
		spin.row_label = row_label
		form.attach(spin, 1, row, 1, 1)
		return spin

	def _on_name_changed(self, _entry):
		name = self.name_entry.get_text()
		self.id_label.set_text(f"id: {_slugify(name)}" if name.strip() else "id: (type a name above)")

	def _on_import_clicked(self, _button):
		dialog = Gtk.FileChooserDialog(
			title="Import mesh", parent=self, action=Gtk.FileChooserAction.OPEN,
		)
		dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
		obj_filter = Gtk.FileFilter()
		obj_filter.set_name("Wavefront OBJ (*.obj)")
		obj_filter.add_pattern("*.obj")
		dialog.add_filter(obj_filter)
		if dialog.run() == Gtk.ResponseType.OK:
			path = Path(dialog.get_filename())
			try:
				self.triangles = obj_mesh.load_obj(path)
				self.mesh_source_dir = path.parent
				self.mesh_source_name = path.name
				n_tris = len(self.triangles)
				self.mesh_label.set_text(f"Loaded {path.name} ({n_tris} triangle{'s' if n_tris != 1 else ''})")
				self._rebuild_item()
			except Exception as exc:
				log.exception(f"failed to load {path}")
				self.mesh_label.set_text(f"Failed to load {path.name}: {exc}")
		dialog.destroy()

	def _current_transform(self):
		joint_name = self.joint_combo.get_active_text() or "neck"
		pos = (self.pos_x.get_value(), self.pos_y.get_value(), self.pos_z.get_value())
		hpr = (self.rot_h.get_value(), self.rot_p.get_value(), self.rot_r.get_value())
		scale = self.scale.get_value()
		return joint_name, pos, hpr, scale

	def _on_transform_changed(self, _widget):
		self._rebuild_item()

	def _rebuild_item(self):
		if self.item_np is not None:
			self.item_np.removeNode()
			self.item_np = None
		if not self.triangles:
			return
		joint_name, pos, hpr, scale = self._current_transform()
		joint = getattr(self.rig, joint_name, None)
		if joint is None:
			self.status_label.set_text(f"'{joint_name}' doesn't exist on the preview rig (missing limb/no wheelchair) - pick another joint.")
			return
		vdata, vertex, normal, color, tris = avatar3d_rig._new_buffers()
		obj_mesh.write_mesh_into_buffers(vertex, normal, color, tris, 0, self.triangles, pos, hpr, scale)
		self.item_np = avatar3d_rig._finish(vdata, tris, "astronar_studio_preview")
		self.item_np.reparentTo(joint)
		self.item_np.setPos(self.rig.root, 0.0, 0.0, 0.0)

	def _rebuild_character(self):
		"""Rebuilds self.rig (and self.pet_rig) from self.character_traits
		- the exact same avatar3d_rig.build_rig()/avatar3d_pet_rig.
		build_pet_rig() calls AvatarCustomizePage._rebuild_rig() makes in
		the real app, including the same pet_species/pet_color pop (see
		its own docstring on why build_rig() can't take those two).
		Called on startup and every single Character tab field edit -
		Item/Motion tabs preview against WHATEVER this currently is, so
		this also reattaches the in-progress item (its old parent joint
		NodePath was just destroyed along with the old rig) and leaves
		Motion mode's keyframe editing untouched (poses are re-applied
		fresh every _tick() regardless of which rig instance is live)."""
		if self.rig is not None:
			self.rig.root.removeNode()
		if self.pet_rig is not None:
			self.pet_rig.root.removeNode()
			self.pet_rig = None
		human_traits = {k: v for k, v in self.character_traits.items() if k not in ("pet_species", "pet_color")}
		self.rig = avatar3d_rig.build_rig(**human_traits)
		self.rig.root.reparentTo(avatar3d.get_renderer().render)
		pet_species = self.character_traits.get("pet_species", "none")
		if pet_species != "none":
			self.pet_rig = avatar3d_pet_rig.build_pet_rig(species=pet_species, color=self.character_traits.get("pet_color", "#8b5e3c"))
			if self.pet_rig is not None:
				self.pet_rig.root.reparentTo(avatar3d.get_renderer().render)
		if hasattr(self, "triangles"):
			self._rebuild_item()

	def _choice_display(self, option):
		if isinstance(option, list):
			return ", ".join(o.replace("_", " ") for o in option) if option else "none"
		return str(option).replace("_", " ")

	def _make_field_widget(self, field):
		kind = field["kind"]
		if kind == "bool":
			widget = Gtk.CheckButton()
			widget.set_active(bool(self.character_traits.get(field["key"], False)))
			widget.connect("toggled", lambda w, f=field: self._on_character_field_changed(f["key"], w.get_active()))
			return widget
		if kind == "multi_bool":
			widget = Gtk.CheckButton()
			widget.set_active(field["item"] in self.character_traits.get(field["key"], []))
			widget.connect("toggled", lambda w, f=field: self._on_character_multi_bool_changed(f, w.get_active()))
			return widget
		if kind == "slider":
			lo, hi, step = field["range"]
			current = self.character_traits.get(field["key"], lo)
			adj = Gtk.Adjustment(value=current, lower=lo, upper=hi, step_increment=step)
			widget = Gtk.SpinButton(adjustment=adj, digits=2, climb_rate=step)
			widget.connect("value-changed", lambda w, f=field: self._on_character_field_changed(f["key"], w.get_value()))
			return widget
		if kind in ("color", "color_opt", "choice"):
			combo = Gtk.ComboBoxText()
			options = list(field["options"])
			if kind == "color_opt":
				options = [None] + options
				combo.append_text(field.get("none_label", "match"))
				for opt in field["options"]:
					combo.append_text(opt)
			else:
				for opt in options:
					combo.append_text(self._choice_display(opt))
			current = self.character_traits.get(field["key"])
			try:
				idx = options.index(current)
			except ValueError:
				idx = 0
			combo.set_active(idx)
			combo.connect("changed", lambda w, f=field, opts=options: self._on_character_field_changed(
				f["key"], opts[w.get_active()] if w.get_active() >= 0 else opts[0],
			))
			return combo
		return Gtk.Label(label="(unsupported field kind)")

	def _on_character_field_changed(self, key, value):
		self.character_traits[key] = value
		self._rebuild_character()

	def _on_character_multi_bool_changed(self, field, active):
		key = field["key"]
		lst = list(self.character_traits.get(key, []))
		item = field["item"]
		if active and item not in lst:
			lst.append(item)
		elif not active and item in lst:
			lst.remove(item)
		self.character_traits[key] = lst
		self._rebuild_character()

	def _on_character_reset(self, _button):
		self.character_traits = dict(avatar3d_viewer.NEUTRAL_TRAITS)
		self._rebuild_character_tab_fields()
		self._rebuild_character()

	def _build_character_tab(self):
		"""Every avatar3d_viewer.CUSTOM_FIELDS entry - core body/face/
		clothing/accessibility/pet traits AND every built-in/installed
		bodypart plugin's own contributed fields alike (glasses, hat,
		wings, tail, ... - CUSTOM_FIELDS is already the full merged
		list) - grouped by category, editable live against the one
		shared rig every other tab also previews against. Literally
		"everything an Astronar is capable of" in one place, per direct
		request, rather than this app only covering the plugin-authoring
		half of that."""
		outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
		self.notebook.append_page(outer, Gtk.Label(label="Character (full customizer)"))

		reset_btn = Gtk.Button(label="Reset to Neutral")
		reset_btn.connect("clicked", self._on_character_reset)
		outer.pack_start(reset_btn, False, False, 0)

		self.character_scroller = Gtk.ScrolledWindow()
		self.character_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		outer.pack_start(self.character_scroller, True, True, 0)
		self._rebuild_character_tab_fields()

	def _rebuild_character_tab_fields(self):
		"""Rebuilds the Character tab's own field grid from CURRENT
		avatar3d_viewer.CUSTOM_FIELDS - called at Character tab
		construction and again after exporting a new item/motion, since
		a freshly-exported bodypart plugin's own customizer fields
		(e.g. a new recolorable hair item's "worn"/"color" fields) won't
		exist in CUSTOM_FIELDS until avatar3d_viewer.refresh_custom_fields()
		re-scans avatar_plugin_backend - so a session that builds an
		item AND wants to immediately toggle/recolor it from the
        Character tab sees it show up with no restart needed."""
		child = self.character_scroller.get_child()
		if child is not None:
			self.character_scroller.remove(child)
		avatar3d_viewer.refresh_custom_fields()
		form = Gtk.Grid(row_spacing=4, column_spacing=8, margin=4)
		self.character_scroller.add(form)
		row = 0
		current_category = None
		for field in avatar3d_viewer.CUSTOM_FIELDS:
			if field["category"] != current_category:
				current_category = field["category"]
				header = Gtk.Label(label=f"<b>{current_category}</b>", xalign=0, use_markup=True)
				form.attach(header, 0, row, 2, 1)
				row += 1
			form.attach(Gtk.Label(label=field["label"], xalign=0), 0, row, 1, 1)
			form.attach(self._make_field_widget(field), 1, row, 1, 1)
			row += 1
		form.show_all()

	def _update_kf_label(self):
		self.kf_label.set_text(f"Keyframe {self.current_kf + 1} of {len(self.keyframes)}")

	def _update_motion_joint_visibility(self):
		is_root = self.motion_joint == "root"
		for spin in (self.mrot_h, self.mrot_p, self.mrot_r):
			spin.set_visible(not is_root)
			spin.row_label.set_visible(not is_root)
		for spin in (self.mpos_x, self.mpos_y, self.mpos_z):
			spin.set_visible(is_root)
			spin.row_label.set_visible(is_root)

	def _refresh_motion_controls_from_state(self):
		"""Loads the current keyframe's value for whichever joint is
		selected into the spin buttons, WITHOUT re-triggering
		_on_motion_value_changed (which would immediately overwrite that
		same keyframe entry with whatever the spin buttons already
		happened to show) - the same "block the handler while syncing
		widget <- state" pattern any two-way-bound GTK control needs."""
		kf = self.keyframes[self.current_kf]
		if self.motion_joint == "root":
			x, y, z = kf.get("root", (0.0, 0.0, 0.0))
			spins_values = ((self.mpos_x, x), (self.mpos_y, y), (self.mpos_z, z))
		else:
			h, p, r = kf.get(self.motion_joint, (0.0, 0.0, 0.0))
			spins_values = ((self.mrot_h, h), (self.mrot_p, p), (self.mrot_r, r))
		for spin, value in spins_values:
			spin.handler_block_by_func(self._on_motion_value_changed)
			spin.set_value(value)
			spin.handler_unblock_by_func(self._on_motion_value_changed)

	def _on_motion_joint_changed(self, _combo):
		text = self.motion_joint_combo.get_active_text() or "root"
		self.motion_joint = "root" if text.startswith("root") else text
		self._update_motion_joint_visibility()
		self._refresh_motion_controls_from_state()

	def _on_motion_value_changed(self, _widget):
		kf = self.keyframes[self.current_kf]
		if self.motion_joint == "root":
			kf["root"] = (self.mpos_x.get_value(), self.mpos_y.get_value(), self.mpos_z.get_value())
		else:
			kf[self.motion_joint] = (self.mrot_h.get_value(), self.mrot_p.get_value(), self.mrot_r.get_value())

	def _on_kf_prev(self, _button):
		self.current_kf = (self.current_kf - 1) % len(self.keyframes)
		self._update_kf_label()
		self._refresh_motion_controls_from_state()

	def _on_kf_next(self, _button):
		self.current_kf = (self.current_kf + 1) % len(self.keyframes)
		self._update_kf_label()
		self._refresh_motion_controls_from_state()

	def _on_kf_add(self, _button):
		"""Duplicates the current keyframe (a real starting point to
		tweak into the next pose, rather than a blank rest pose every
		time) and inserts it right after - the new keyframe becomes
		current."""
		self.keyframes.insert(self.current_kf + 1, dict(self.keyframes[self.current_kf]))
		self.current_kf += 1
		self._update_kf_label()
		self._refresh_motion_controls_from_state()

	def _on_kf_remove(self, _button):
		if len(self.keyframes) <= 1:
			self.motion_status_label.set_text("A motion needs at least one keyframe.")
			return
		del self.keyframes[self.current_kf]
		self.current_kf = min(self.current_kf, len(self.keyframes) - 1)
		self._update_kf_label()
		self._refresh_motion_controls_from_state()

	def _on_play_toggled(self, toggle):
		self.motion_playing = toggle.get_active()
		self._motion_t0 = time.time()

	def _on_motion_name_changed(self, _entry):
		name = self.motion_name_entry.get_text()
		self.motion_id_label.set_text(f"id: {_slugify(name)}" if name.strip() else "id: (type a name above)")

	def _on_export_motion_clicked(self, _button):
		name = self.motion_name_entry.get_text().strip()
		if not name:
			self.motion_status_label.set_text("Give the motion a name first.")
			return
		plugin_id = _slugify(name)
		out_dir = avatar_plugin_backend.PLUGINS_ROOT / "motion" / plugin_id
		if out_dir.exists():
			self.motion_status_label.set_text(f"A motion called '{plugin_id}' already exists at {out_dir} - pick a different name.")
			return
		description = self.motion_desc_entry.get_text().strip()
		try:
			out_dir.mkdir(parents=True, exist_ok=False)
			manifest = {"name": name, "description": description}
			(out_dir / "motion.json").write_text(_json_dumps(manifest))
			(out_dir / "motion.py").write_text(_render_motion_py(self.keyframes, MOTION_JOINTS))
			self.motion_status_label.set_text(f"Exported to {out_dir} - it'll show up wherever motions are picked next time it opens.")
			log.info(f"exported astronar studio motion {plugin_id!r} to {out_dir}")
			self._rebuild_manage_list()
		except Exception as exc:
			log.exception(f"failed to export motion {plugin_id!r}")
			shutil.rmtree(out_dir, ignore_errors=True)
			self.motion_status_label.set_text(f"Export failed: {exc}")

	def _on_export_clicked(self, _button):
		name = self.name_entry.get_text().strip()
		if not name:
			self.status_label.set_text("Give the item a name first.")
			return
		if not self.triangles:
			hint = "Add at least one shape first." if self.mesh_source_mode == "build" else "Import a mesh first."
			self.status_label.set_text(hint)
			return
		plugin_id = _slugify(name)
		out_dir = avatar_plugin_backend.PLUGINS_ROOT / "bodypart" / plugin_id
		if out_dir.exists():
			self.status_label.set_text(f"A plugin called '{plugin_id}' already exists at {out_dir} - pick a different name.")
			return

		joint_name, pos, hpr, scale = self._current_transform()
		category = self.category_combo.get_active_text().strip() or "Hair"
		description = self.desc_entry.get_text().strip()
		enabled_default = self.enabled_check.get_active()
		recolorable = self.recolor_check.get_active()

		swatches_text = self.swatches_entry.get_text().strip()
		if swatches_text:
			swatches = [s.strip() for s in swatches_text.split(",") if s.strip()]
		else:
			swatches = list(_DEFAULT_SWATCHES.get(category, _GENERIC_SWATCHES))

		try:
			out_dir.mkdir(parents=True, exist_ok=False)
			if self.mesh_source_mode == "build":
				obj_text, mtl_text = _parts_to_obj_mtl_text(self.parts)
				(out_dir / "mesh.obj").write_text(obj_text)
				(out_dir / "mesh.mtl").write_text(mtl_text)
			else:
				shutil.copyfile(self.mesh_source_dir / self.mesh_source_name, out_dir / "mesh.obj")
				mtl_path = self.mesh_source_dir / (Path(self.mesh_source_name).stem + ".mtl")
				if mtl_path.is_file():
					shutil.copyfile(mtl_path, out_dir / "mesh.mtl")

			worn_key = "worn"
			fields = [{
				"category": category, "key": worn_key, "label": f"Wear {name}", "kind": "bool", "default": enabled_default,
			}]
			if recolorable:
				fields.append({
					"category": category, "key": "color", "label": f"{name} color", "kind": "color",
					"options": swatches, "default": swatches[0],
				})
			manifest = {"name": name, "description": description, "fields": fields}
			(out_dir / "bodypart.json").write_text(_json_dumps(manifest))
			(out_dir / "bodypart.py").write_text(_render_bodypart_py(
				plugin_id=plugin_id, joint=joint_name, pos=pos, hpr=hpr, scale=scale, recolorable=recolorable,
			))
			self.status_label.set_text(f"Exported to {out_dir} - now toggleable from the Character tab's '{category}' section.")
			log.info(f"exported astronar studio plugin {plugin_id!r} to {out_dir}")
			self._rebuild_character_tab_fields()
			self._rebuild_manage_list()
		except Exception as exc:
			log.exception(f"failed to export plugin {plugin_id!r}")
			shutil.rmtree(out_dir, ignore_errors=True)
			self.status_label.set_text(f"Export failed: {exc}")

	def _pose_for_preview(self):
		"""Resets the rig to rest, then - only on the Motion tab - poses
		it either at the current keyframe (paused) or at the live
		interpolated point in the loop (Play Loop on), via the exact
		same motion_keyframes.pose_at() an exported motion plugin uses.
		Always resetting to rest first (matching avatar3d_anim.play()'s
		own real contract) means switching back to the Item tab never
		leaves a joint stuck from whatever pose Motion editing last set -
		item positioning should always be judged against a neutral rig."""
		avatar3d_anim._rest_pose(self.rig)
		if self.notebook.get_current_page() != 1:
			return
		if self.motion_playing:
			t = ((time.time() - self._motion_t0) % DEFAULT_LOOP_SECONDS) / DEFAULT_LOOP_SECONDS
			motion_keyframes.pose_at(self.rig, self.keyframes, MOTION_JOINTS, t)
		else:
			motion_keyframes.pose_at(self.rig, [self.keyframes[self.current_kf]], MOTION_JOINTS, 0.0)

	def _build_manage_tab(self):
		"""Every installed AND built-in bodypart/motion plugin, browsable
		in one place - "can be modified" covers real installed content
		(Load for Editing reloads it back into the Item/Motion tab;
		Delete calls avatar_plugin_backend.uninstall()), while built-in
		features are listed too (for visibility - this app didn't create
		them, so it has no contract-following data to reload) but never
		get either button, the same real protection avatar_plugin_
		backend.uninstall() itself already enforces."""
		outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
		self.notebook.append_page(outer, Gtk.Label(label="Manage (browse/edit/delete)"))

		refresh_btn = Gtk.Button(label="Refresh List")
		refresh_btn.connect("clicked", lambda _b: self._rebuild_manage_list())
		outer.pack_start(refresh_btn, False, False, 0)

		self.manage_scroller = Gtk.ScrolledWindow()
		self.manage_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
		outer.pack_start(self.manage_scroller, True, True, 0)

		self.manage_status_label = Gtk.Label(label="", xalign=0)
		self.manage_status_label.set_line_wrap(True)
		outer.pack_start(self.manage_status_label, False, False, 0)

		self._rebuild_manage_list()

	def _rebuild_manage_list(self):
		child = self.manage_scroller.get_child()
		if child is not None:
			self.manage_scroller.remove(child)
		listbox = Gtk.ListBox()
		self.manage_scroller.add(listbox)
		for plugin_type in ("bodypart", "motion"):
			for entry in avatar_plugin_backend.list_installed(plugin_type):
				listbox.add(self._make_manage_row(plugin_type, entry))
		listbox.show_all()

	def _make_manage_row(self, plugin_type, entry):
		row = Gtk.ListBoxRow()
		box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin=4)
		row.add(box)
		label = Gtk.Label(label=f"[{plugin_type}] {entry['name']} ({entry['source']})", xalign=0)
		box.pack_start(label, True, True, 0)
		if entry["source"] == "installed":
			edit_btn = Gtk.Button(label="Load for Editing")
			edit_btn.connect("clicked", self._on_manage_edit, plugin_type, entry["id"])
			box.pack_start(edit_btn, False, False, 0)
			delete_btn = Gtk.Button(label="Delete")
			delete_btn.connect("clicked", self._on_manage_delete, plugin_type, entry["id"])
			box.pack_start(delete_btn, False, False, 0)
		return row

	def _on_manage_delete(self, _button, plugin_type, plugin_id):
		avatar_plugin_backend.uninstall(plugin_type, plugin_id)
		self.manage_status_label.set_text(f"Deleted {plugin_type} '{plugin_id}'.")
		self._rebuild_manage_list()
		if plugin_type == "bodypart":
			self._rebuild_character_tab_fields()

	def _on_manage_edit(self, _button, plugin_type, plugin_id):
		try:
			module = avatar_plugin_backend._load_module(plugin_type, plugin_id, "installed")
		except Exception as exc:
			log.exception(f"failed to load {plugin_type} {plugin_id!r} for editing")
			self.manage_status_label.set_text(f"Couldn't load '{plugin_id}': {exc}")
			return
		if plugin_type == "motion":
			self._load_motion_for_editing(plugin_id, module)
		else:
			self._load_bodypart_for_editing(plugin_id, module)

	def _load_motion_for_editing(self, plugin_id, module):
		keyframes = getattr(module, "KEYFRAMES", None)
		if keyframes is None:
			self.manage_status_label.set_text(f"'{plugin_id}' isn't editable here - it has no Astronar Studio KEYFRAMES data (likely hand-coded).")
			return
		self.keyframes = [dict(kf) for kf in keyframes]
		self.current_kf = 0
		manifest_path = avatar_plugin_backend.PLUGINS_ROOT / "motion" / plugin_id / "motion.json"
		try:
			import json
			manifest = json.loads(manifest_path.read_text())
			self.motion_name_entry.set_text(manifest.get("name", plugin_id))
			self.motion_desc_entry.set_text(manifest.get("description", ""))
		except (OSError, ValueError):
			pass
		self._update_kf_label()
		self._refresh_motion_controls_from_state()
		self.notebook.set_current_page(1)
		self.manage_status_label.set_text(f"Loaded '{plugin_id}' into the Motion tab - re-export will need a different name (or delete this one first to reuse it).")

	def _load_bodypart_for_editing(self, plugin_id, module):
		plugin_dir = avatar_plugin_backend.PLUGINS_ROOT / "bodypart" / plugin_id
		mesh_path = plugin_dir / "mesh.obj"
		if not mesh_path.is_file():
			self.manage_status_label.set_text(f"'{plugin_id}' isn't editable here - it has no mesh.obj (likely hand-coded procedural geometry).")
			return
		try:
			self.triangles = obj_mesh.load_obj(mesh_path)
		except Exception as exc:
			log.exception(f"failed to load mesh for {plugin_id!r}")
			self.manage_status_label.set_text(f"Couldn't load '{plugin_id}' mesh: {exc}")
			return
		self.mesh_source_mode = "import"
		self.import_radio.set_active(True)
		self.mesh_source_dir = plugin_dir
		self.mesh_source_name = "mesh.obj"
		self.mesh_label.set_text(f"Loaded {plugin_id}/mesh.obj ({len(self.triangles)} triangles)")

		joint = getattr(module, "JOINT", None)
		pos = getattr(module, "POS", None)
		hpr = getattr(module, "HPR", None)
		scale = getattr(module, "SCALE", None)
		if pos is not None:
			self.pos_x.set_value(pos[0])
			self.pos_y.set_value(pos[1])
			self.pos_z.set_value(pos[2])
		if hpr is not None:
			self.rot_h.set_value(hpr[0])
			self.rot_p.set_value(hpr[1])
			self.rot_r.set_value(hpr[2])
		if scale is not None:
			self.scale.set_value(scale)
		if joint is not None:
			for i, text in enumerate(JOINTS):
				if text == joint:
					self.joint_combo.set_active(i)
					break

		manifest_path = plugin_dir / "bodypart.json"
		try:
			import json
			manifest = json.loads(manifest_path.read_text())
			self.name_entry.set_text(manifest.get("name", plugin_id))
			self.desc_entry.set_text(manifest.get("description", ""))
			fields = manifest.get("fields", [])
			worn_field = next((f for f in fields if f.get("key") == "worn"), None)
			self.enabled_check.set_active(bool(worn_field.get("default", True)) if worn_field else True)
			color_field = next((f for f in fields if f.get("key") == "color"), None)
			self.recolor_check.set_active(color_field is not None)
			if color_field:
				self.swatches_entry.set_text(", ".join(color_field.get("options", [])))
			if fields:
				for i in range(self.category_combo.get_model().iter_n_children(None)):
					if self.category_combo.get_model()[i][0] == fields[0]["category"]:
						self.category_combo.set_active(i)
						break
		except (OSError, ValueError):
			pass

		self._rebuild_item()
		self.notebook.set_current_page(0)
		self.manage_status_label.set_text(f"Loaded '{plugin_id}' into the Item tab - re-export will need a different name (or delete this one first to reuse it).")

	def _tick(self):
		self._pose_for_preview()
		if self.pet_rig is not None:
			t = (time.time() - self._t0) % 2.0 / 2.0
			avatar3d_pet_anim.play(self.pet_rig, "idle", t)
		renderer = avatar3d.get_renderer()
		_buffer, tex, _cam = renderer.buffer_for_size(PREVIEW_SIZE)
		renderer.graphicsEngine.renderFrame()
		renderer.graphicsEngine.renderFrame()
		raw = bytes(tex.getRamImageAs("RGBA"))
		pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
			GLib.Bytes.new(raw), GdkPixbuf.Colorspace.RGB, True, 8, PREVIEW_SIZE, PREVIEW_SIZE, PREVIEW_SIZE * 4,
		)
		self.image.set_from_pixbuf(pixbuf.flip(False))
		return True


def _json_dumps(obj):
	import json
	return json.dumps(obj, indent=4)


def _render_bodypart_py(plugin_id, joint, pos, hpr, scale, recolorable):
	color_key = f"plugin_{plugin_id}_color"
	worn_key = f"plugin_{plugin_id}_worn"
	if recolorable:
		color_fn = f'''def _color(traits):
	hexval = traits.get("{color_key}")
	return obj_mesh.hex_to_rgba(hexval) if hexval else None'''
	else:
		color_fn = '''def _color(traits):
	return None'''
	return f'''"""Generated by Astronar Studio - do not hand-edit the geometry math
below if this file gets regenerated; re-export from Astronar Studio
(or edit mesh.obj in Blender) instead."""
from pathlib import Path

import obj_mesh

MESH_PATH = Path(__file__).resolve().parent / "mesh.obj"
JOINT = {joint!r}
POS = {pos!r}
HPR = {hpr!r}
SCALE = {scale!r}


{color_fn}


def build_portrait(vertex, normal, color, tris, idx, box, outlined_box, traits, metrics):
	if not traits.get("{worn_key}", True):
		return idx
	triangles = obj_mesh.load_obj(MESH_PATH)
	return obj_mesh.write_mesh_into_buffers(vertex, normal, color, tris, idx, triangles, POS, HPR, SCALE, override_color=_color(traits))


def build_animated(rig, traits, metrics):
	if not traits.get("{worn_key}", True):
		return
	if rig.{joint} is None:
		return
	import avatar3d_rig as a3d_rig
	triangles = obj_mesh.load_obj(MESH_PATH)
	vdata, vertex, normal, color, tris = a3d_rig._new_buffers()
	idx = obj_mesh.write_mesh_into_buffers(vertex, normal, color, tris, 0, triangles, POS, HPR, SCALE, override_color=_color(traits))
	item = a3d_rig._finish(vdata, tris, "{plugin_id}")
	item.reparentTo(rig.{joint})
	item.setPos(rig.root, 0.0, 0.0, 0.0)
'''


def _render_motion_py(keyframes, joints):
	# Only the non-empty keyframes actually matter at playback (an
	# untouched joint/root already defaults to rest via
	# motion_keyframes.pose_at()'s own per-entry .get(..., (0,0,0))) -
	# stripped here purely to keep the generated file's own literal
	# short and readable, not for correctness.
	trimmed = [{k: v for k, v in kf.items()} for kf in keyframes]
	return f'''"""Generated by Astronar Studio - do not hand-edit the keyframe
data below if this file gets regenerated; re-export from Astronar
Studio instead."""
import avatar3d_anim
import motion_keyframes

JOINTS = {joints!r}
KEYFRAMES = {trimmed!r}


def pose(rig, t):
	motion_keyframes.pose_at(rig, KEYFRAMES, JOINTS, t)
	avatar3d_anim._blink(rig, t)
	avatar3d_anim._mouth_flap(rig, t)
'''


if __name__ == "__main__":
	AstronarStudio()
	Gtk.main()
