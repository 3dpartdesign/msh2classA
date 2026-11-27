# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Scans as references: a FreeCAD object that keeps only the PATH of the scan
file, its SHA-256 and its Placement. The mesh is read from the file when the
document opens and drawn directly (Coin) - it is never stored in the .FCStd
and the file is never written."""
import os

import FreeCAD as App

from . import ICON
from .core import meshio
from .core import setups as m2setups
from .core.meshgrid import TriMesh

_MESH = {}          # object name -> (verts, tris) in the object's own coordinates
_TREE = {}


def is_scan(obj):
    return getattr(getattr(obj, "Proxy", None), "M2CA_SCAN", False)


def scan_objects(doc, visible_only=False):
    out = []
    for o in doc.Objects:
        if is_scan(o):
            if visible_only and App.GuiUp and not o.ViewObject.Visibility:
                continue
            out.append(o)
    return out


def read_scan(path, factor):
    """(verts, tris) of a scan file - read only. FreeCAD's own reader if it can."""
    try:
        import Mesh
        m = Mesh.Mesh(path)
        pts, facets = m.Topology
        verts = [(p.x * factor, p.y * factor, p.z * factor) for p in pts]
        tris = [tuple(f) for f in facets]
        if tris:
            return verts, tris
    except Exception:
        pass
    verts, tris = meshio.read_mesh(path)
    if factor != 1.0:
        verts = [(x * factor, y * factor, z * factor) for (x, y, z) in verts]
    return verts, tris


def mesh_of(obj, reload=False):
    if reload or obj.Name not in _MESH:
        path = obj.File
        if not path or not os.path.isfile(path):
            App.Console.PrintWarning("[msh2classA] scan/mesh file not found: {}\n".format(path))
            _MESH[obj.Name] = ([], [])
        else:
            _MESH[obj.Name] = read_scan(path, float(obj.ScanMM) * scan_scale(obj))   # FreeCAD works in mm
            if obj.Sha256 and m2setups.file_sha256(path) != obj.Sha256:
                App.Console.PrintWarning("[msh2classA] WARNING: {} CHANGED since it was loaded (SHA-256 "
                                         "differs)\n".format(os.path.basename(path)))
        _TREE.clear()
    return _MESH[obj.Name]


def scan_scale(obj):
    """The uniform SIZE of the scan/mesh (Align Scale / MeshScale in Rhino):
    a FreeCAD Placement cannot scale, so it lives here and in the mesh."""
    return float(getattr(obj, "ScanScale", 1.0) or 1.0)


def _ensure_scale_prop(obj):
    if not hasattr(obj, "ScanScale"):
        obj.addProperty("App::PropertyFloat", "ScanScale", "Scan",
                        "Uniform size of the scan/mesh (AI / photogrammetry meshes scaled in a setup)")
        obj.ScanScale = 1.0


class ScanRef(object):
    M2CA_SCAN = True

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyString", "File", "Scan", "Scan/mesh file (only read, never written)")
        obj.addProperty("App::PropertyString", "Sha256", "Scan", "SHA-256 of the scan/mesh file when it was loaded")
        obj.addProperty("App::PropertyFloat", "ScanMM", "Scan", "mm per unit of the scan/mesh file")
        obj.addProperty("App::PropertyPlacement", "Placement", "Base", "Position of the scan/mesh")
        obj.ScanMM = 1.0
        _ensure_scale_prop(obj)
        for p in ("File", "Sha256"):
            obj.setEditorMode(p, 1)                 # read only in the property view

    def onDocumentRestored(self, obj):
        _ensure_scale_prop(obj)
        mesh_of(obj, reload=True)

    def onChanged(self, obj, prop):
        if prop in ("File", "ScanMM", "ScanScale"):
            _MESH.pop(getattr(obj, "Name", ""), None)
        if prop in ("File", "ScanMM", "ScanScale", "Placement"):
            _TREE.clear()

    def execute(self, obj):
        pass

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps
    __setstate__ = loads


class ViewProviderScanRef(object):
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        from pivy import coin
        self.Object = vobj.Object
        self.root = coin.SoSeparator()
        self.xf = coin.SoTransform()
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(0.62, 0.62, 0.62)
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        hints.creaseAngle = 0.6
        self.coords = coin.SoCoordinate3()
        self.faces = coin.SoIndexedFaceSet()
        for n in (self.xf, mat, hints, self.coords, self.faces):
            self.root.addChild(n)
        vobj.addDisplayMode(self.root, "Shaded")
        self.refresh()
        self.place()

    def refresh(self):
        verts, tris = mesh_of(self.Object)
        self.coords.point.setValues(0, len(verts), [list(v) for v in verts])
        idx = []
        for t in tris:
            idx.extend((t[0], t[1], t[2], -1))
        self.faces.coordIndex.setValues(0, len(idx), idx)

    def place(self):
        pl = self.Object.Placement
        b = pl.Base
        self.xf.translation.setValue(b.x, b.y, b.z)
        q = pl.Rotation.Q
        self.xf.rotation.setValue(q[0], q[1], q[2], q[3])

    def updateData(self, fp, prop):
        if prop == "Placement":
            self.place()
        elif prop in ("File", "ScanMM", "ScanScale"):
            self.refresh()

    def getDisplayModes(self, vobj):
        return ["Shaded"]

    def getDefaultDisplayMode(self):
        return "Shaded"

    def setDisplayMode(self, mode):
        return mode

    def onChanged(self, vobj, prop):
        pass

    def getIcon(self):
        return ICON

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps
    __setstate__ = loads


def add_scan(doc, path, scan_mm=1.0):
    path = os.path.abspath(path)
    obj = doc.addObject("App::FeaturePython", m2setups.mesh_name(path).replace(" ", "_"))
    ScanRef(obj)
    obj.Label = m2setups.mesh_name(path)
    obj.ScanMM = float(scan_mm)
    obj.Sha256 = m2setups.file_sha256(path)
    obj.File = path
    mesh_of(obj, reload=True)
    if App.GuiUp:
        ViewProviderScanRef(obj.ViewObject)
    return obj


def world_mesh(objs):
    """One TriMesh of the scans in world coordinates (cached)."""
    key = tuple((o.Name, o.File, float(o.ScanMM), scan_scale(o), tuple(o.Placement.toMatrix().A)) for o in objs)
    if _TREE.get("key") != key:
        V, T = [], []
        for o in objs:
            verts, tris = mesh_of(o)
            m = o.Placement.toMatrix().A           # 16 numbers, row-major
            base = len(V)
            for (x, y, z) in verts:
                V.append((m[0] * x + m[1] * y + m[2] * z + m[3],
                          m[4] * x + m[5] * y + m[6] * z + m[7],
                          m[8] * x + m[9] * y + m[10] * z + m[11]))
            T.extend((a + base, b + base, c + base) for (a, b, c) in tris)
        _TREE["key"] = key
        _TREE["mesh"] = TriMesh(V, T) if T else None
    return _TREE["mesh"]


# ---------------------------------------------------------------- setups
def apply_setup(doc, name, log=App.Console.PrintMessage):
    objs = scan_objects(doc)
    data = m2setups.read_setups([o.File for o in objs], log=lambda s: log(s + "\n"))
    st = data.get(name)
    if not st:
        log("[msh2classA] no setup '{}'\n".format(name)); return 0
    n = 0; ref = None; missing = []
    for o in objs:
        rec = m2setups.find_record(st, o.File)
        if rec is None:
            missing.append(o); continue
        w = m2setups.check_file(rec, o.File)
        if w:
            App.Console.PrintWarning("[msh2classA] " + w + "\n")
        m = list(m2setups.scaled_matrix(rec, st, 1.0))    # FreeCAD: mm
        det = (m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8]) +
               m[2] * (m[4] * m[9] - m[5] * m[8]))
        s_ = abs(det) ** (1.0 / 3.0) if det else 1.0       # a size in the setup (AI mesh): ScanScale
        for i in (0, 1, 2, 4, 5, 6, 8, 9, 10):
            m[i] /= s_
        new = App.Placement(App.Matrix(*m))
        _ensure_scale_prop(o)
        if abs(scan_scale(o) - s_) > 1e-12:
            o.ScanScale = s_
        if ref is None:
            ref = new.multiply(o.Placement.inverse())
        o.Placement = new
        n += 1
    for o in missing:
        if ref is not None:
            o.Placement = ref.multiply(o.Placement)
    _TREE.clear()
    log("[msh2classA] setup '{}' applied to {} scan(s)/mesh(es)\n".format(name, n))
    return n


def save_setup(doc, name, log=App.Console.PrintMessage):
    objs = scan_objects(doc)
    if not objs:
        return []
    units = "mm"                                     # FreeCAD works in mm
    f = 1.0
    recs = []
    for o in objs:
        m = list(o.Placement.toMatrix().A)
        s_ = scan_scale(o)
        for i in (0, 1, 2, 4, 5, 6, 8, 9, 10):
            m[i] *= s_                               # the size back into the 4x4
        for i in (3, 7, 11):
            m[i] *= f
        recs.append((o.File, m, None))
    paths = [o.File for o in objs]
    data = m2setups.read_setups(paths)
    data[name] = m2setups.make_setup(recs, units)
    where = m2setups.write_setups(paths, data)
    log("[msh2classA] setup '{}' saved in {}\n".format(name, ", ".join(where) or "-"))
    return where


def setup_names(doc):
    paths = [o.File for o in scan_objects(doc)]
    return sorted(m2setups.read_setups(paths, log=lambda s: None).keys()) if paths else []
