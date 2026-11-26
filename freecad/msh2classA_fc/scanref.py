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
            App.Console.PrintWarning("[msh2classA] scan file not found: {}\n".format(path))
            _MESH[obj.Name] = ([], [])
        else:
            _MESH[obj.Name] = read_scan(path, float(obj.ScanMM))     # FreeCAD works in mm
            if obj.Sha256 and m2setups.file_sha256(path) != obj.Sha256:
                App.Console.PrintWarning("[msh2classA] WARNING: {} CHANGED since it was loaded (SHA-256 "
                                         "differs)\n".format(os.path.basename(path)))
        _TREE.clear()
    return _MESH[obj.Name]


class ScanRef(object):
    M2CA_SCAN = True

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyString", "File", "Scan", "Scan file (only read, never written)")
        obj.addProperty("App::PropertyString", "Sha256", "Scan", "SHA-256 of the scan file when it was loaded")
        obj.addProperty("App::PropertyFloat", "ScanMM", "Scan", "mm per unit of the scan file")
        obj.addProperty("App::PropertyPlacement", "Placement", "Base", "Position of the scan")
        obj.ScanMM = 1.0
        for p in ("File", "Sha256"):
            obj.setEditorMode(p, 1)                 # read only in the property view

    def onDocumentRestored(self, obj):
        mesh_of(obj, reload=True)

    def onChanged(self, obj, prop):
        if prop in ("File", "ScanMM"):
            _MESH.pop(getattr(obj, "Name", ""), None)
        if prop in ("File", "ScanMM", "Placement"):
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
        elif prop in ("File", "ScanMM"):
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
    key = tuple((o.Name, o.File, float(o.ScanMM), tuple(o.Placement.toMatrix().A)) for o in objs)
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
    want_mm = m2setups.unit_mm(st.get("_units"))
    n = 0; ref = None; missing = []
    for o in objs:
        rec = m2setups.find_record(st, o.File)
        if rec is None:
            missing.append(o); continue
        w = m2setups.check_file(rec, o.File)
        if w:
            App.Console.PrintWarning("[msh2classA] " + w + "\n")
        if abs(float(o.ScanMM) - want_mm) > 1e-12:
            o.ScanMM = want_mm
        m = m2setups.scaled_matrix(rec, st, 1.0)          # FreeCAD: mm
        new = App.Placement(App.Matrix(*m))
        if ref is None:
            ref = new.multiply(o.Placement.inverse())
        o.Placement = new
        n += 1
    for o in missing:
        if ref is not None:
            o.Placement = ref.multiply(o.Placement)
    _TREE.clear()
    log("[msh2classA] setup '{}' applied to {} scan(s)\n".format(name, n))
    return n


def save_setup(doc, name, log=App.Console.PrintMessage):
    objs = scan_objects(doc)
    if not objs:
        return []
    scan_mm = float(objs[0].ScanMM)
    unit = [k for k, v in m2setups.UNIT_MM.items() if abs(v - scan_mm) < 1e-12]
    units = unit[0] if unit else "mm"
    f = 1.0 / m2setups.unit_mm(units)
    recs = []
    for o in objs:
        m = list(o.Placement.toMatrix().A)
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
