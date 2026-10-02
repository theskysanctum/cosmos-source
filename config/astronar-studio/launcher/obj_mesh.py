"""Wavefront .obj (+ .mtl) loading and writing, shared between
astronar_studio.py's own live preview and every bodypart plugin
astronar_studio.py generates - the real bridge that lets someone sculpt
an Astronar hairstyle/item as an actual mesh in Blender (export as
plain .obj) instead of hand-writing box math the way every existing
avatar_plugins_builtin/bodypart/*/bodypart.py entry does.

Deliberately NOT a general-purpose OBJ importer: no texture coordinates
(this avatar generator is flat-shaded per-vertex/per-face color, never
textured - see avatar3d.py's own "Minecraft-blocky" docstring), no
multi-object files, no smoothing groups. Just enough to turn a
Blender-exported single-mesh .obj (with an optional .mtl for per-face
color via each material's Kd) into the exact same (vertex, normal,
color, tris, idx) -> idx Panda3D buffer-writing contract every built-in
_box()/_disc_x() primitive in avatar3d.py already uses - so a plugin
built from an imported mesh slots into the real static-portrait and
live-rig render paths with no special-casing anywhere else."""
from panda3d.core import NodePath, PandaNode, Point3, Vec3

DEFAULT_COLOR = (0.6, 0.6, 0.6, 1.0)


def hex_to_rgba(hex_color, alpha=1.0):
	h = hex_color.lstrip("#")
	r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
	return (r, g, b, alpha)


def _parse_mtl(path):
	"""{material name: rgba} from a .mtl file's own Kd (diffuse) lines -
	the one property this flat-shaded generator actually cares about."""
	colors = {}
	name = None
	if not path.is_file():
		return colors
	for line in path.read_text().splitlines():
		parts = line.split()
		if not parts:
			continue
		if parts[0] == "newmtl":
			name = parts[1]
		elif parts[0] == "Kd" and name is not None:
			r, g, b = (float(x) for x in parts[1:4])
			colors[name] = (r, g, b, 1.0)
	return colors


def _face_indices(token, n_verts):
	"""One face-vertex token ("v", "v/vt", "v/vt/vn", or "v//vn") -> the
	1-based vertex index it names, resolved to 0-based, negative
	(relative-from-end, a real part of the OBJ spec some exporters use)
	handled the same way positive indices are."""
	v_idx = int(token.split("/")[0])
	return (v_idx - 1) if v_idx > 0 else (n_verts + v_idx)


def load_obj(path):
	"""Parses a Wavefront .obj at `path` (a Path or str) into a flat list
	of triangles, each `((x,y,z), (nx,ny,nz), (r,g,b,a))` per vertex (3
	per triangle) - fan-triangulated for any face with more than 3
	vertices (correct for the convex polygons a "Triangulate Faces"
	Blender export already produces, and a reasonable approximation
	otherwise). A face with no `vn` entries gets one flat normal per
	triangle (cross product of its own two edges) rather than crashing -
	plenty of real Blender OBJ exports omit normals entirely. Face color
	comes from the most recent `usemtl` (looked up in the same-named
	.mtl next to this .obj, or `<name>.mtl` per any `mtllib` line) -
	DEFAULT_COLOR when no material applies at all, never a crash over a
	missing swatch."""
	from pathlib import Path
	path = Path(path)
	verts = []
	normals = []
	materials = {}
	current_color = DEFAULT_COLOR
	triangles = []

	for line in path.read_text().splitlines():
		parts = line.split()
		if not parts:
			continue
		tag = parts[0]
		if tag == "v":
			verts.append(tuple(float(x) for x in parts[1:4]))
		elif tag == "vn":
			normals.append(tuple(float(x) for x in parts[1:4]))
		elif tag == "mtllib":
			materials.update(_parse_mtl(path.parent / parts[1]))
		elif tag == "usemtl":
			current_color = materials.get(parts[1], DEFAULT_COLOR)
		elif tag == "f":
			face_tokens = parts[1:]
			idxs = [_face_indices(tok, len(verts)) for tok in face_tokens]
			vn_idxs = []
			for tok in face_tokens:
				pieces = tok.split("/")
				if len(pieces) >= 3 and pieces[2]:
					vn_i = int(pieces[2])
					vn_idxs.append((vn_i - 1) if vn_i > 0 else (len(normals) + vn_i))
				else:
					vn_idxs.append(None)
			for i in range(1, len(idxs) - 1):
				tri_idx = (idxs[0], idxs[i], idxs[i + 1])
				tri_vn = (vn_idxs[0], vn_idxs[i], vn_idxs[i + 1])
				p0, p1, p2 = (verts[j] for j in tri_idx)
				if all(n is not None for n in tri_vn):
					tri_normals = [normals[n] for n in tri_vn]
				else:
					e1 = tuple(p1[k] - p0[k] for k in range(3))
					e2 = tuple(p2[k] - p0[k] for k in range(3))
					fn = (
						e1[1] * e2[2] - e1[2] * e2[1],
						e1[2] * e2[0] - e1[0] * e2[2],
						e1[0] * e2[1] - e1[1] * e2[0],
					)
					mag = sum(c * c for c in fn) ** 0.5 or 1.0
					fn = tuple(c / mag for c in fn)
					tri_normals = [fn, fn, fn]
				triangles.append(tuple(
					(p, n, current_color) for p, n in zip((p0, p1, p2), tri_normals)
				))
	return triangles


def _transform_matrix(pos, hpr, scale):
	"""A throwaway NodePath purely to borrow Panda3D's own
	pos/hpr/scale -> matrix math (LMatrix4.xformPoint/xformVec below) -
	guarantees this matches every other NodePath transform in the app
	exactly (same H/P/R axis/order convention), rather than a
	hand-rolled trig reimplementation that could subtly disagree with
	it."""
	holder = NodePath(PandaNode("astronar_studio_xform"))
	holder.setPos(*pos)
	holder.setHpr(*hpr)
	holder.setScale(scale)
	return holder.getMat()


def write_mesh_into_buffers(vertex, normal, color, tris, idx, triangles, pos=(0.0, 0.0, 0.0), hpr=(0.0, 0.0, 0.0), scale=1.0, override_color=None):
	"""Writes `triangles` (load_obj()'s own return shape) into the same
	shared vertex/normal/color/tris Panda3D writers every _box()-style
	primitive in avatar3d.py already writes through, after applying a
	pos/hpr/scale transform - the exact `(vertex, normal, color, tris,
	idx) -> idx` calling convention every bodypart plugin's
	build_portrait/build_animated already needs to follow. `override_color`
	(an (r,g,b,a) tuple), when given, replaces EVERY triangle's own
	baked-in .mtl color - how a "recolorable" generated plugin re-tints
	its whole mesh to the customizer's currently-picked swatch instead
	of always rendering Blender's original material color."""
	mat = _transform_matrix(pos, hpr, scale)
	for tri in triangles:
		for v, n, c in tri:
			wp = mat.xformPoint(Point3(*v))
			wn = mat.xformVec(Vec3(*n))
			wn.normalize()
			vertex.addData3(wp[0], wp[1], wp[2])
			normal.addData3(wn[0], wn[1], wn[2])
			color.addData4(*(override_color if override_color is not None else c))
		tris.addVertices(idx, idx + 1, idx + 2)
		idx += 3
	return idx
