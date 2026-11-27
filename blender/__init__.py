# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""msh2classA for Blender - EXPERIMENTAL, NOT TESTED YET.

Scans are linked references: read from their files, never written, never
saved into the .blend (only the path and the position). Positions come from
the same scan_setups.json as the Rhino version.

A patch = two objects:
  "Patch.NNN"     the exact single-span surface, drawn as a fine mesh
  "Patch.NNN_CV"  its control points (a grid mesh) - edit these with any
                  mesh tool; the patch follows when you leave Edit Mode
"""
import json
import math
import os

import bpy
from bpy.app.handlers import persistent
from bpy.props import CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from .core import builder as m2b
from .core import iges as m2iges
from .core import legal
from .core import meshio
from .core import setups as m2setups

bl_info = {
    "name": "msh2classA (experimental)",
    "author": "Marcin Bartoszewski, 3dPARTdesign",
    "version": (0, 1, 1),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > msh2classA",
    "description": "Scans and AI meshes to Class-A NURBS patches. EXPERIMENTAL - NOT TESTED YET",
    "doc_url": "https://3dPARTdesign.com",
    "category": "Mesh",
}

SCAN_COLL = "msh2classA Scans/meshes"
PATCH_COLL = "msh2classA Patches"
PLACEHOLDER = "M2CA_placeholder"
HERE = os.path.dirname(os.path.abspath(__file__))
MATCAP = os.path.join(HERE, "assets", "reflection_matcap_3dPARTdesign_com.png")
CONT_ITEMS = [("0", "G0", "position"), ("1", "G1", "tangent"), ("2", "G2", "curvature")]


# =============================================================================
# units
# =============================================================================

def model_mm(scene):
    """mm per Blender unit (Scene > Units > Unit Scale)."""
    us = scene.unit_settings
    v = float(us.scale_length) * 1000.0
    return max(1e-9, float("%.6g" % v))         # Blender keeps the scale as float32 (0.001 -> 1.0000000475)


# =============================================================================
# scans: references to files, never saved into the .blend
# =============================================================================

def scan_objects(visible_only=False):
    out = []
    for o in bpy.data.objects:
        if o.get("m2ca_scan") and o.type == "MESH":
            if visible_only and not o.visible_get():
                continue
            out.append(o)
    return out


def _collection(name):
    c = bpy.data.collections.get(name)
    if c is None:
        c = bpy.data.collections.new(name)
    if c.name not in bpy.context.scene.collection.children:
        try:
            bpy.context.scene.collection.children.link(c)
        except Exception:
            pass
    return c


def _fill_mesh(me, verts, tris):
    me.clear_geometry()
    try:
        import numpy as np
        v = np.asarray(verts, dtype=np.float32).reshape(-1)
        t = np.asarray(tris, dtype=np.int32).reshape(-1)
        nv, nt = len(v) // 3, len(t) // 3
        me.vertices.add(nv)
        me.vertices.foreach_set("co", v)
        me.loops.add(nt * 3)
        me.loops.foreach_set("vertex_index", t)
        me.polygons.add(nt)
        me.polygons.foreach_set("loop_start", np.arange(0, nt * 3, 3, dtype=np.int32))
        me.update(calc_edges=True)
        me.validate(clean_customdata=False)
    except Exception:
        me.clear_geometry()
        me.from_pydata([tuple(p) for p in verts], [], [tuple(f) for f in tris])
        me.update()


def _read_scan(path, factor):
    verts, tris = meshio.read_mesh(path)
    if factor != 1.0:
        verts = [(x * factor, y * factor, z * factor) for (x, y, z) in verts]
    return verts, tris


def load_scan_mesh(obj, log=print):
    """(Re)read the scan file of obj into its mesh. The file is only read."""
    path = obj.get("m2ca_scan")
    if not path or not os.path.isfile(path):
        log("[msh2classA] scan/mesh file not found: {}".format(path))
        return False
    factor = float(obj.get("m2ca_scan_mm", 1.0)) / model_mm(bpy.context.scene)
    verts, tris = _read_scan(path, factor)
    me = bpy.data.meshes.get("M2CA_scan_" + obj.name)
    if me is None or me.users > 1:
        me = bpy.data.meshes.new("M2CA_scan_" + obj.name)
    _fill_mesh(me, verts, tris)
    obj.data = me
    want = obj.get("m2ca_sha256")
    if want:
        got = m2setups.file_sha256(path)
        if got != want:
            log("[msh2classA] WARNING: {} CHANGED since it was loaded (SHA-256 differs)".format(os.path.basename(path)))
    _BVH.clear()
    return True


def add_scan(path, scan_mm=1.0):
    path = os.path.abspath(path)
    name = m2setups.mesh_name(path)
    obj = bpy.data.objects.new(name, bpy.data.meshes.new("M2CA_scan_" + name))
    _collection(SCAN_COLL).objects.link(obj)
    obj["m2ca_scan"] = path
    obj["m2ca_scan_mm"] = float(scan_mm)
    obj["m2ca_sha256"] = m2setups.file_sha256(path)
    load_scan_mesh(obj)
    obj.hide_select = True                      # a reference: not picked, not edited
    obj.color = (0.62, 0.62, 0.62, 1.0)
    return obj


def _placeholder():
    me = bpy.data.meshes.get(PLACEHOLDER)
    if me is None:
        me = bpy.data.meshes.new(PLACEHOLDER)
    return me


_SWAPPED = {}


@persistent
def _save_pre(*_args):
    """Before saving: the scan meshes leave the objects (0 users = not saved)."""
    _SWAPPED.clear()
    ph = _placeholder()
    for o in scan_objects():
        if o.data is not None and o.data.name != PLACEHOLDER:
            _SWAPPED[o.name] = o.data.name
            o.data = ph


@persistent
def _save_post(*_args):
    for oname, mname in list(_SWAPPED.items()):
        o = bpy.data.objects.get(oname); me = bpy.data.meshes.get(mname)
        if o is not None and me is not None:
            o.data = me
        elif o is not None:
            load_scan_mesh(o)
    _SWAPPED.clear()


@persistent
def _load_post(*_args):
    """After opening a .blend: read every scan again from its file."""
    for o in scan_objects():
        if o.data is None or o.data.name == PLACEHOLDER or len(o.data.vertices) == 0:
            load_scan_mesh(o)
    _BVH.clear()


# =============================================================================
# the scan as one BVH tree (world coordinates) for the patch builder
# =============================================================================

_BVH = {}


class BlenderMesh(object):
    """closest / ray for core.builder on all visible scans."""

    def __init__(self, objs):
        key = tuple((o.name, len(o.data.vertices), tuple(round(x, 9) for r in o.matrix_world for x in r)) for o in objs)
        if _BVH.get("key") != key:
            verts, polys = [], []
            for o in objs:
                mw = o.matrix_world
                base = len(verts)
                verts.extend((mw @ v.co)[:] for v in o.data.vertices)
                polys.extend([base + i for i in p.vertices] for p in o.data.polygons)
            _BVH["key"] = key
            _BVH["tree"] = BVHTree.FromPolygons(verts, polys, all_triangles=False) if polys else None
        self.tree = _BVH["tree"]

    def closest(self, p):
        if self.tree is None:
            return None
        loc, nrm, idx, dist = self.tree.find_nearest(Vector(p))
        if loc is None:
            return None
        return tuple(loc), tuple(nrm.normalized()), idx

    def ray(self, o, d):
        if self.tree is None:
            return None
        dv = Vector(d)
        L = dv.length
        if L < 1e-300:
            return None
        loc, nrm, idx, dist = self.tree.ray_cast(Vector(o), dv / L)
        if loc is None:
            return None
        return dist / L, tuple(nrm.normalized()), idx


# =============================================================================
# patches: exact surface (fine mesh) + CV net (editable grid)
# =============================================================================

def patch_objects():
    return [o for o in bpy.data.objects if o.get("m2ca_patch")]


def cv_object(patch):
    for c in patch.children:
        if c.get("m2ca_cvnet"):
            return c
    return None


def patch_cvs(patch):
    """World CVs of a patch (from its CV net)."""
    cv = cv_object(patch)
    if cv is None:
        return None
    mw = cv.matrix_world
    return [tuple(mw @ v.co) for v in cv.data.vertices]


def as_core_patch(o):
    P = patch_cvs(o)
    pu, pv = int(o["m2ca_pu"]), int(o["m2ca_pv"])
    if P is None or len(P) != (pu + 1) * (pv + 1):
        return None
    try:
        lv = dict((int(k), v) for k, v in json.loads(o.get("m2ca_levels", "{}")).items())
    except Exception:
        lv = {}
    return m2b.Patch(pu, pv, P, lv, o.name)


def _tess_into(me, pu, pv, P, n=24):
    verts, quads = m2b.tessellate(pu, pv, P, n)
    me.clear_geometry()
    me.from_pydata(verts, [], quads)
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()


def retessellate(patch):
    P = patch_cvs(patch)
    if P is None:
        return
    pu, pv = int(patch["m2ca_pu"]), int(patch["m2ca_pv"])
    if len(P) != (pu + 1) * (pv + 1):
        print("[msh2classA] {}: the CV net lost points - not updated (Undo it)".format(patch.name))
        return
    inv = patch.matrix_world.inverted()
    _tess_into(patch.data, pu, pv, [tuple(inv @ Vector(q)) for q in P])


def make_patch(res, scene):
    pu, pv, P = res["pu"], res["pv"], res["P"]
    nu, nv = pu + 1, pv + 1
    coll = _collection(PATCH_COLL)
    me = bpy.data.meshes.new("Patch")
    obj = bpy.data.objects.new("Patch", me)
    coll.objects.link(obj)
    obj["m2ca_patch"] = 1
    obj["m2ca_pu"] = pu
    obj["m2ca_pv"] = pv
    obj["m2ca_levels"] = json.dumps(res.get("levels", {}))
    obj.color = (0.85, 0.85, 0.9, 1.0)
    _tess_into(me, pu, pv, P)
    edges = []
    for i in range(nu):
        for j in range(nv):
            a = i * nv + j
            if j + 1 < nv: edges.append((a, a + 1))
            if i + 1 < nu: edges.append((a, a + nv))
    cme = bpy.data.meshes.new(obj.name + "_CV")
    cme.from_pydata(P, edges, [])
    cme.update()
    cv = bpy.data.objects.new(obj.name + "_CV", cme)
    coll.objects.link(cv)
    cv["m2ca_cvnet"] = 1
    cv.parent = obj
    cv.show_in_front = True
    cv.color = (1.0, 0.25, 0.6, 1.0)
    return obj


_PENDING = set()


def _run_pending():
    names = list(_PENDING); _PENDING.clear()
    for n in names:
        o = bpy.data.objects.get(n)
        if o is not None and o.get("m2ca_patch"):
            try:
                retessellate(o)
            except Exception as e:
                print("[msh2classA] update failed:", e)
    return None


@persistent
def _depsgraph_post(scene, depsgraph):
    for up in depsgraph.updates:
        idb = up.id
        if isinstance(idb, bpy.types.Object) and idb.get("m2ca_cvnet") and idb.parent is not None:
            if up.is_updated_geometry or up.is_updated_transform:
                _PENDING.add(idb.parent.name)
    if _PENDING and not bpy.app.timers.is_registered(_run_pending):
        bpy.app.timers.register(_run_pending, first_interval=0.05)


# =============================================================================
# settings
# =============================================================================

class M2CA_Settings(bpy.types.PropertyGroup):
    cont: EnumProperty(name="Continuity", items=CONT_ITEMS, default="2",
                       description="Continuity of a new side to the patch it joins")
    tol_mm: FloatProperty(name="Scan/mesh tol (mm)", default=0.5, min=0.001, soft_max=10.0,
                          description="Allowed deviation from the scan/mesh (CV count, report)")
    snap_px: IntProperty(name="Snap (px)", default=12, min=2, max=60,
                         description="Clicks this close (pixels) to a patch corner / edge go onto it")
    scan_mm: FloatProperty(name="Scan/mesh units (mm)", default=1.0, min=1e-6,
                           description="mm per unit of the scan/mesh FILES (1 = the scans/meshes are in mm)")
    setup_name: StringProperty(name="Name", default="front")
    last_report: StringProperty(default="")


def _setup_items(self, context):
    paths = [o["m2ca_scan"] for o in scan_objects()]
    names = sorted(m2setups.read_setups(paths, log=lambda s: None).keys()) if paths else []
    return [(n, n, "") for n in names] or [("", "(no setups)", "")]


# =============================================================================
# operators: scans
# =============================================================================

class M2CA_OT_load_scans(bpy.types.Operator, ImportHelper):
    """Link scan meshes (STL / OBJ / PLY). The files are only read and never saved into the .blend"""
    bl_idname = "m2ca.load_scans"
    bl_label = "Load scans/meshes"
    filter_glob: StringProperty(default="*.stl;*.obj;*.ply", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        st = context.scene.m2ca
        names = [f.name for f in self.files if f.name] or [os.path.basename(self.filepath)]
        folder = self.directory or os.path.dirname(self.filepath)
        n = 0
        for nm in names:
            p = os.path.join(folder, nm)
            if os.path.splitext(p)[1].lower() not in m2setups.SCAN_EXT:
                continue
            try:
                add_scan(p, st.scan_mm); n += 1
            except Exception as e:
                self.report({"ERROR"}, "{}: {}".format(nm, e))
        self.report({"INFO"}, "{} scan(s)/mesh(es) linked (files untouched)".format(n))
        return {"FINISHED"}


class M2CA_OT_reload_scans(bpy.types.Operator):
    """Read every scan again from its file"""
    bl_idname = "m2ca.reload_scans"
    bl_label = "Reload scans/meshes"

    def execute(self, context):
        n = sum(1 for o in scan_objects() if load_scan_mesh(o, log=lambda s: self.report({"WARNING"}, s)))
        self.report({"INFO"}, "{} scan(s)/mesh(es) read again".format(n))
        return {"FINISHED"}


class M2CA_OT_check_scans(bpy.types.Operator):
    """Are the scan files still the ones you loaded? (SHA-256)"""
    bl_idname = "m2ca.check_scans"
    bl_label = "Check scan/mesh files"

    def execute(self, context):
        bad = []
        for o in scan_objects():
            p = o["m2ca_scan"]
            try:
                if m2setups.file_sha256(p) != o.get("m2ca_sha256"):
                    bad.append(os.path.basename(p))
            except Exception:
                bad.append(os.path.basename(p) + " (missing)")
        if bad:
            self.report({"WARNING"}, "CHANGED since loaded: " + ", ".join(bad))
        else:
            self.report({"INFO"}, "All scan/mesh files unchanged")
        return {"FINISHED"}


class M2CA_OT_apply_setup(bpy.types.Operator):
    """Put the scans where a saved setup has them (scan_setups.json next to the scans)"""
    bl_idname = "m2ca.apply_setup"
    bl_label = "Apply setup"
    bl_property = "setup"
    setup: EnumProperty(name="Setup", items=_setup_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"FINISHED"}

    def execute(self, context):
        objs = scan_objects()
        data = m2setups.read_setups([o["m2ca_scan"] for o in objs])
        st = data.get(self.setup)
        if not st:
            self.report({"WARNING"}, "no setup '{}'".format(self.setup)); return {"CANCELLED"}
        mm = model_mm(context.scene)
        n = 0; ref = None; missing = []
        for o in objs:
            rec = m2setups.find_record(st, o["m2ca_scan"])
            if rec is None:
                missing.append(o); continue
            w = m2setups.check_file(rec, o["m2ca_scan"])
            if w:
                self.report({"WARNING"}, w)
            m = m2setups.scaled_matrix(rec, st, mm)
            M = Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])
            if ref is None:
                ref = M @ o.matrix_world.inverted()
            o.matrix_world = M
            n += 1
        for o in missing:                          # not in this setup: moves with the others
            if ref is not None:
                o.matrix_world = ref @ o.matrix_world
        _BVH.clear()
        self.report({"INFO"}, "setup '{}' applied to {} scan(s)/mesh(es)".format(self.setup, n))
        return {"FINISHED"}


class M2CA_OT_save_setup(bpy.types.Operator):
    """Save the present scan positions under a name, in scan_setups.json next to the scans (the same file Rhino uses)"""
    bl_idname = "m2ca.save_setup"
    bl_label = "Save setup"

    def execute(self, context):
        objs = scan_objects()
        if not objs:
            self.report({"WARNING"}, "no scans/meshes"); return {"CANCELLED"}
        name = context.scene.m2ca.setup_name.strip() or "front"
        mm = model_mm(context.scene)
        units = "mm"                                # setups from Blender: moves in mm
        f = mm                                      # Blender units -> mm
        recs = []
        for o in objs:
            M = o.matrix_world
            m = [M[i][j] for i in range(4) for j in range(4)]
            for i in (3, 7, 11):
                m[i] *= f
            recs.append((o["m2ca_scan"], m, None))
        paths = [o["m2ca_scan"] for o in objs]
        data = m2setups.read_setups(paths)
        data[name] = m2setups.make_setup(recs, units)
        where = m2setups.write_setups(paths, data)
        self.report({"INFO"}, "setup '{}' saved in {}".format(name, ", ".join(where) or "-"))
        return {"FINISHED"}


# =============================================================================
# operator: new patch (click 4 corners)
# =============================================================================

def _shader(kind):
    import gpu
    for nm in ((kind,) if kind else ()) + ("UNIFORM_COLOR", "3D_UNIFORM_COLOR"):
        try:
            return gpu.shader.from_builtin(nm)
        except Exception:
            continue
    return None


class M2CA_OT_new_patch(bpy.types.Operator):
    """Click 4 corners on the scan: a single-span patch is fitted, G0/G1/G2 to the patches it touches. Esc = finish"""
    bl_idname = "m2ca.new_patch"
    bl_label = "New patch"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if context.area is None or context.area.type != "VIEW_3D":
            self.report({"WARNING"}, "run it in a 3D Viewport"); return {"CANCELLED"}
        if not scan_objects(visible_only=True):
            self.report({"WARNING"}, "load a scan/mesh first"); return {"CANCELLED"}
        self.mesh = BlenderMesh(scan_objects(visible_only=True))
        self.pts = []
        self.hover = None
        self._h = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), "WINDOW", "POST_VIEW")
        context.window_manager.modal_handler_add(self)
        self._status(context)
        return {"RUNNING_MODAL"}

    def _status(self, context):
        context.area.header_text_set("msh2classA (experimental): click corner {} of 4 on the scan/mesh   "
                                     "Backspace = undo point   Esc / right click = finish".format(len(self.pts) + 1))

    def _finish(self, context):
        try:
            bpy.types.SpaceView3D.draw_handler_remove(self._h, "WINDOW")
        except Exception:
            pass
        context.area.header_text_set(None)
        context.area.tag_redraw()

    def _draw(self):
        import gpu
        from gpu_extras.batch import batch_for_shader
        pts = [tuple(p) for p in self.pts]
        line = pts + ([tuple(self.hover)] if self.hover is not None and len(pts) < 4 else [])
        try:
            gpu.state.depth_test_set("NONE")
            if len(line) >= 2:
                sh = _shader("UNIFORM_COLOR")
                if sh:
                    sh.bind(); sh.uniform_float("color", (1.0, 0.8, 0.0, 1.0))
                    gpu.state.line_width_set(2.0)
                    batch_for_shader(sh, "LINE_STRIP", {"pos": line}).draw(sh)
            if pts:
                sh = _shader("POINT_UNIFORM_COLOR")
                if sh:
                    sh.bind(); sh.uniform_float("color", (1.0, 0.2, 0.2, 1.0))
                    gpu.state.point_size_set(10.0)
                    batch_for_shader(sh, "POINTS", {"pos": pts}).draw(sh)
        except Exception:
            pass

    def _pick(self, context, event):
        """Screen snap to patch corners / edges first, else the scan under the mouse."""
        region, rv3d = context.region, context.region_data
        co = (event.mouse_region_x, event.mouse_region_y)
        snap = context.scene.m2ca.snap_px
        best = None
        for o in patch_objects():
            pc = as_core_patch(o)
            if pc is None:
                continue
            sp = pc.spatch()
            for k in range(4):
                for i, t in enumerate([x / 40.0 for x in range(41)]):
                    q = sp.val(k, t, "pt")
                    s2 = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(q))
                    if s2 is None:
                        continue
                    d = math.hypot(s2.x - co[0], s2.y - co[1])
                    corner = i == 0
                    lim = snap * (1.0 if corner else 0.7)
                    if d <= lim and (best is None or (corner, -d) > (best[0], -best[1])):
                        best = (corner, d, q)
        if best is not None:
            return Vector(best[2])
        o3 = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
        d3 = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
        if self.mesh.tree is None:
            return None
        loc, nrm, idx, dist = self.mesh.tree.ray_cast(o3, d3)
        return loc

    def modal(self, context, event):
        context.area.tag_redraw()
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN", "TRACKPADZOOM",
                          "NDOF_MOTION"} or event.type.startswith("NUMPAD"):
            return {"PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            try:
                self.hover = self._pick(context, event)
            except Exception:
                self.hover = None
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._finish(context)
            return {"FINISHED"}
        if event.type == "BACK_SPACE" and event.value == "PRESS":
            if self.pts:
                self.pts.pop()
            self._status(context)
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            p = self._pick(context, event)
            if p is None:
                self.report({"WARNING"}, "no scan/mesh under the mouse")
                return {"RUNNING_MODAL"}
            self.pts.append(Vector(p))
            if len(self.pts) == 4:
                self._build(context)
                self.pts = []
            self._status(context)
            return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def _build(self, context):
        st = context.scene.m2ca
        mm = model_mm(context.scene)
        pats = [p for p in (as_core_patch(o) for o in patch_objects()) if p is not None]
        b = m2b.PatchBuilder(self.mesh, pats, cont=int(st.cont), tol=st.tol_mm / mm, model_mm=mm, log=print)
        context.window.cursor_set("WAIT")
        try:
            res = b.build([tuple(p) for p in self.pts])
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, "patch failed: {}".format(e))
            return
        finally:
            context.window.cursor_set("DEFAULT")
        obj = make_patch(res, context.scene)
        dev = res.get("deviation")
        txt = "{}: {}x{} CV".format(obj.name, res["pu"] + 1, res["pv"] + 1)
        if dev:
            txt += ", scan/mesh deviation P90 {:.3f} max {:.3f} mm".format(dev[0] * mm, dev[1] * mm)
        st.last_report = txt
        self.report({"INFO"}, txt)


# =============================================================================
# operators: patches, export, view, CAD services
# =============================================================================

class M2CA_OT_update_patches(bpy.types.Operator):
    """Redraw every patch from its CV net (after editing the CVs)"""
    bl_idname = "m2ca.update_patches"
    bl_label = "Update patches"

    def execute(self, context):
        for o in patch_objects():
            retessellate(o)
        return {"FINISHED"}


class M2CA_OT_export_iges(bpy.types.Operator, ExportHelper):
    """Export the patches as exact NURBS surfaces (IGES, mm). Selected patches, or all if none is selected"""
    bl_idname = "m2ca.export_iges"
    bl_label = "Export IGES"
    filename_ext = ".igs"
    filter_glob: StringProperty(default="*.igs;*.iges", options={"HIDDEN"})

    def execute(self, context):
        sel = set()
        for o in context.selected_objects:
            if o.get("m2ca_patch"): sel.add(o)
            elif o.get("m2ca_cvnet") and o.parent is not None: sel.add(o.parent)
        objs = sorted(sel or patch_objects(), key=lambda o: o.name)
        pats = [as_core_patch(o) for o in objs]
        pats = [p for p in pats if p is not None]
        if not pats:
            self.report({"WARNING"}, "no patches"); return {"CANCELLED"}
        m2iges.write_iges(self.filepath, [(p.pu, p.pv, p.P) for p in pats], units="MM",
                          scale=model_mm(context.scene))
        self.report({"INFO"}, "{} patch(es) -> {}".format(len(pats), self.filepath))
        return {"FINISHED"}


class M2CA_OT_matcap(bpy.types.Operator):
    """Show the viewport with the 3dPARTdesign reflection matcap (Solid mode)"""
    bl_idname = "m2ca.matcap"
    bl_label = "Reflection matcap"

    def execute(self, context):
        if not os.path.isfile(MATCAP):
            self.report({"WARNING"}, "matcap image missing"); return {"CANCELLED"}
        lights = context.preferences.studio_lights
        name = os.path.basename(MATCAP)
        sl = next((l for l in lights if l.name == name), None)
        if sl is None:
            sl = lights.load(MATCAP, "MATCAP")
        for area in context.screen.areas if context.screen else []:
            if area.type == "VIEW_3D":
                sh = area.spaces.active.shading
                sh.type = "SOLID"; sh.light = "MATCAP"; sh.studio_light = sl.name
        return {"FINISHED"}


class M2CA_OT_cad_services(bpy.types.Operator):
    """Who made msh2classA and how to reach him"""
    bl_idname = "m2ca.cad_services"
    bl_label = "CAD Services"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        for q in legal.CAD_SERVICES_QUESTIONS:
            col.label(text=q)
        words = legal.CAD_SERVICES_TEXT.split(); line = ""
        for w in words:
            if len(line) + len(w) > 70:
                col.label(text=line); line = w
            else:
                line = (line + " " + w).strip()
        if line:
            col.label(text=line)
        col.separator()
        col.label(text="E-mail: " + legal.CAD_SERVICES_EMAIL)
        row = col.row()
        row.operator("m2ca.copy_email")
        row.operator("wm.url_open", text="Open website").url = legal.CAD_SERVICES_URL

    def execute(self, context):
        return {"FINISHED"}


class M2CA_OT_copy_email(bpy.types.Operator):
    bl_idname = "m2ca.copy_email"
    bl_label = "Copy e-mail"

    def execute(self, context):
        context.window_manager.clipboard = legal.CAD_SERVICES_EMAIL
        self.report({"INFO"}, "e-mail copied")
        return {"FINISHED"}


# =============================================================================
# panel
# =============================================================================

class M2CA_PT_main(bpy.types.Panel):
    bl_label = "msh2classA (experimental)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "msh2classA"

    def draw(self, context):
        st = context.scene.m2ca
        lay = self.layout
        box = lay.box()
        box.label(text="EXPERIMENTAL - NOT TESTED YET", icon="ERROR")
        box.label(text="Check every result before you use it.")

        col = lay.column(align=True)
        col.label(text="Scans/meshes (files never changed)")
        col.prop(st, "scan_mm")
        col.operator("m2ca.load_scans", icon="IMPORT")
        row = col.row(align=True)
        row.operator("m2ca.reload_scans")
        row.operator("m2ca.check_scans")
        col.separator()
        col.operator("m2ca.apply_setup", icon="EMPTY_AXIS")
        row = col.row(align=True)
        row.prop(st, "setup_name", text="")
        row.operator("m2ca.save_setup")

        col = lay.column(align=True)
        col.label(text="Patches")
        col.prop(st, "cont", expand=True)
        col.prop(st, "tol_mm")
        col.prop(st, "snap_px")
        col.operator("m2ca.new_patch", icon="SURFACE_NSURFACE")
        col.operator("m2ca.update_patches")
        if st.last_report:
            col.label(text=st.last_report)

        col = lay.column(align=True)
        col.operator("m2ca.export_iges", icon="EXPORT")
        col.operator("m2ca.matcap", icon="SHADING_SOLID")

        lay.separator()
        lay.operator("m2ca.cad_services", icon="USER")
        lay.label(text="(C) 2026 3dPARTdesign - GPL-3.0, NO WARRANTY")


CLASSES = (M2CA_Settings, M2CA_OT_load_scans, M2CA_OT_reload_scans, M2CA_OT_check_scans, M2CA_OT_apply_setup,
           M2CA_OT_save_setup, M2CA_OT_new_patch, M2CA_OT_update_patches, M2CA_OT_export_iges, M2CA_OT_matcap,
           M2CA_OT_cad_services, M2CA_OT_copy_email, M2CA_PT_main)

HANDLERS = ((bpy.app.handlers.save_pre, _save_pre), (bpy.app.handlers.save_post, _save_post),
            (bpy.app.handlers.load_post, _load_post), (bpy.app.handlers.depsgraph_update_post, _depsgraph_post))


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.m2ca = bpy.props.PointerProperty(type=M2CA_Settings)
    for lst, fn in HANDLERS:
        if fn not in lst:
            lst.append(fn)
    for line in legal.banner("Blender"):
        print(line)


def unregister():
    for lst, fn in HANDLERS:
        if fn in lst:
            lst.remove(fn)
    del bpy.types.Scene.m2ca
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
