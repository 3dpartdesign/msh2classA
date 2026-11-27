# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# SCAN (mesh or block), cached between runs
# -----------------------------------------------------------------------------

# =============================================================================
#                      SCAN SETUP (before picking the scan)
# Load scans as linked blocks, Center (symmetry), Floor, named setups, mirror
# check. Only the BLOCK INSTANCES are moved / turned - the scan files from the
# customer are never touched, and the scale never changes.
# =============================================================================

SCAN_ROOT = "Reference Meshes- Msh2classA"   # parent layer of all scan layers


def in_scan_root(fp, below=False):
    """Layer full path fp is the mesh root (or below it)."""
    return (fp == SCAN_ROOT and not below) or fp.startswith(SCAN_ROOT + "::")
SCAN_KEY = "msh2classA_scan"          # user text on scan instances (source file)


def scan_palette(n):
    """n clearly different colours (golden-angle hues)."""
    out = []
    for i in range(n):
        h = (i * 137.508) % 360.0
        out.append(sd.Color.FromArgb(*_hsv(h, 0.55, 0.85)))
    return out


def _hsv(h, s, v):
    c = v * s; x = c * (1 - abs((h / 60.0) % 2 - 1)); m = v - c
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return int(255 * (r + m)), int(255 * (g + m)), int(255 * (b + m))


def scan_instances():
    """[(InstanceObject, layer full path)] of the mesh blocks (under 'Reference Meshes- Msh2classA')."""
    s = ro.ObjectEnumeratorSettings()
    s.HiddenObjects = True; s.LockedObjects = True; s.ObjectTypeFilter = ro.ObjectType.InstanceReference
    out = []
    for obj in sc.doc.Objects.GetObjectList(s):
        lay = sc.doc.Layers[obj.Attributes.LayerIndex]
        if in_scan_root(lay.FullPath) or obj.Attributes.GetUserString(SCAN_KEY):
            out.append((obj, lay.FullPath))
    return out


def _idef_has_mesh(idef):
    try:
        for i in range(idef.ObjectCount):
            o = idef.Object(i)
            if o is not None and (isinstance(o.Geometry, rg.Mesh) or isinstance(o, ro.InstanceObject)):
                return True
    except Exception:
        pass
    return False


def scan_objects():
    """[(block, layer full path)] the mesh tools work on: the mesh blocks
    (LoadMeshes, layer 'Reference Meshes- Msh2classA') and any other BLOCK with a mesh inside.
    Plain meshes are not used (keep meshes as blocks - the file stays small)."""
    s = ro.ObjectEnumeratorSettings()
    s.HiddenObjects = True; s.LockedObjects = True
    out = []; seen = set()
    for (o, lp) in scan_instances():
        out.append((o, lp)); seen.add(o.Id)
    for obj in sc.doc.Objects.GetObjectList(s):
        if obj.Id in seen or is_preview(obj): continue
        if not isinstance(obj, ro.InstanceObject) or not _idef_has_mesh(obj.InstanceDefinition):
            continue
        out.append((obj, sc.doc.Layers[obj.Attributes.LayerIndex].FullPath))
    return out


def scan_xform(obj):
    """Block: its instance transform. Plain mesh: identity (it IS in place)."""
    return obj.InstanceXform if isinstance(obj, ro.InstanceObject) else rg.Transform.Identity


def scan_geo(obj, build=True):
    """(mesh in its own coordinates, transform to the world) or None.
    Blocks: the definition's mesh (read once). Plain meshes: the mesh itself."""
    if isinstance(obj, ro.InstanceObject):
        idef = obj.InstanceDefinition
        m = local_mesh(idef) if build else _LOCAL_MESH.get(idef.Id)
        return None if m is None else (m, obj.InstanceXform)
    g = obj.Geometry
    if not isinstance(g, rg.Mesh): return None
    try:
        if g.FaceNormals.Count != g.Faces.Count: g.FaceNormals.ComputeFaceNormals()
    except Exception:
        pass
    return g, rg.Transform.Identity




def scan_visible(obj):
    lay = sc.doc.Layers[obj.Attributes.LayerIndex]
    return lay.IsVisible and obj.Visible


def ensure_layer(path, color=None):
    idx = sc.doc.Layers.FindByFullPath(path, -1)
    if idx >= 0: return idx
    parent = -1; full = ""
    for part in path.split("::"):
        full = part if not full else full + "::" + part
        idx = sc.doc.Layers.FindByFullPath(full, -1)
        if idx < 0:
            L_ = Rhino.DocObjects.Layer(); L_.Name = part
            if parent >= 0: L_.ParentLayerId = sc.doc.Layers[parent].Id
            if color is not None and full == path: L_.Color = color
            idx = sc.doc.Layers.Add(L_)
        parent = idx
    return idx


def _new_object_ids(before):
    out = []
    for o in sc.doc.Objects.GetObjectList(ro.ObjectEnumeratorSettings()):
        if o.Id not in before: out.append(o.Id)
    return out


OBJ_IMPORT_OPTS = ("_ImportObjGroupsAs=_Nothing _ImportObjectsAs=_Nothing _IgnoreTextures=_No "
                   "_SetDisplayColorFromObjMaterial=_No _MapYtoZ=_No _MorphTargetOnly=_No "
                   "_ReverseGroupOrder=_No _Split32BitTextures=_No")
# document units the PLY importer's ModelUnits option knows (else: its default)
PLY_UNITS = ("Millimeters", "Centimeters", "Meters", "Inches", "Feet")


# ---- PLY / OBJ: linked through a small .3dm in msh2classA_cache ------------
# A block linked straight to a PLY (or OBJ) makes Rhino run that format's
# importer whenever the project is opened - and those importers ASK their
# options (PLY: ModelUnits) every time; with the API Rhino even refuses the
# link and the scan ends up EMBEDDED in the project (big file, very slow save).
# So the mesh read from the PLY / OBJ is written ONCE to <scan folder>/
# msh2classA_cache/<name>.3dm and the block is linked to that file: Rhino reads
# its own format fast and without questions. The original scan is only read;
# setups keep pointing to the original file.
CACHE_DIR = "msh2classA_cache"
CACHED_EXT = (".ply", ".obj", ".stl")   # every scan: linked through its .3dm (same scale on every opening)


def _sha256(path):
    return file_sha256(path)


def cache_path(path):
    """<scan folder>/msh2classA_cache/<file name>.3dm (home folder if the scan folder is read-only)."""
    for base in (os.path.dirname(os.path.abspath(path)), os.path.expanduser("~")):
        d = os.path.join(base, CACHE_DIR)
        try:
            if not os.path.isdir(d): os.makedirs(d)
            return os.path.join(d, os.path.basename(path) + ".3dm")
        except Exception:
            continue
    return None


def _cache_info(cp):
    try:
        with open(cp + ".json") as f: return json.loads(f.read())
    except Exception:
        return None


def read_cache(path):
    """Meshes from the cache of a PLY / OBJ if it is still the same file (SHA-256), else None."""
    cp = cache_path(path)
    info = _cache_info(cp) if cp else None
    if not info or not os.path.isfile(cp): return None
    try:
        if info.get("size") != os.path.getsize(path) or info.get("sha256") != _sha256(path): return None
        if abs(float(info.get("scan_mm", 1.0)) - scan_mm()) > 1e-12: return None   # ScanMeshUnits changed
        f3 = Rhino.FileIO.File3dm.Read(cp)
        out = [o.Geometry.Duplicate() for o in f3.Objects if isinstance(o.Geometry, rg.Mesh)]
        k = float(info.get("unit_mm", _mm_per_unit())) / _mm_per_unit()     # cache written in other units
        if abs(k - 1.0) > 1e-12:
            for g in out:
                g.Transform(rg.Transform.Scale(rg.Point3d.Origin, k))
        return out or None
    except Exception:
        return None


CACHE_READABLE_LAYER = "scan/mesh (linked - not saved in the project, do not edit)"


def _cache_layer(f3, layer_path):
    """Layer (with its parents) in a File3dm -> its index."""
    import System
    parent = None; idx = -1
    for part in layer_path.split("::"):
        L_ = Rhino.DocObjects.Layer(); L_.Name = part
        L_.Id = System.Guid.NewGuid()
        if parent is not None: L_.ParentLayerId = parent
        f3.AllLayers.Add(L_)
        idx = f3.AllLayers.Count - 1
        parent = L_.Id
    return idx


def write_cache(path, geos, layer_path=CACHE_READABLE_LAYER):
    """The meshes of a PLY / OBJ to its cache .3dm, on layer layer_path -> its path or None."""
    cp = cache_path(path)
    if cp is None: return None
    try:
        f3 = Rhino.FileIO.File3dm()
        f3.Settings.ModelUnitSystem = sc.doc.ModelUnitSystem
        li = -1
        try: li = _cache_layer(f3, layer_path)
        except Exception: li = -1
        for g in geos:
            a = ro.ObjectAttributes()
            a.ColorSource = ro.ObjectColorSource.ColorFromParent      # the colour of the block instance
            if li is not None and li >= 0: a.LayerIndex = li
            f3.Objects.AddMesh(g, a)
        ok = False
        try: ok = f3.Write(cp, 0)
        except Exception: pass
        if not ok:
            try: ok = f3.Write(cp, Rhino.FileIO.File3dmWriteOptions())
            except Exception: ok = False
        if not ok: return None
        with open(cp + ".json", "w") as f:
            f.write(json.dumps({"source": os.path.basename(path), "size": os.path.getsize(path),
                                "sha256": _sha256(path), "units": str(sc.doc.ModelUnitSystem),
                                "unit_mm": _mm_per_unit(), "scan_mm": scan_mm(),
                                "layer": layer_path}, indent=1))
        return cp
    except Exception:
        return None


def _cache_in_use(cp):
    """Is a block of this document already linked to the cache file cp?"""
    for d in sc.doc.InstanceDefinitions:
        try:
            if d is not None and not d.IsDeleted and d.SourceArchive and \
                    os.path.normcase(os.path.abspath(d.SourceArchive)) == os.path.normcase(os.path.abspath(cp)):
                return True
        except Exception:
            continue
    return False


def _cache_for(path, geos, layer_path, from_cache):
    """Cache .3dm of path with its mesh on layer_path (written again if the scan
    was read from its file, or the cache has another layer) -> path or None.
    A cache another block of this document is linked to is never rewritten
    (Rhino would ask to update that block)."""
    cp = cache_path(path)
    info = _cache_info(cp) if cp else None
    if cp and os.path.isfile(cp) and info and _cache_in_use(cp):
        return cp
    if from_cache and info and info.get("layer") == layer_path and os.path.isfile(cp):
        return cp
    return write_cache(path, geos, layer_path)


def _all_ids():
    s_ = ro.ObjectEnumeratorSettings(); s_.HiddenObjects = True; s_.LockedObjects = True
    return set(o.Id for o in sc.doc.Objects.GetObjectList(s_))


_PLY_FMT = {"char": "b", "int8": "b", "uchar": "B", "uint8": "B", "short": "h", "int16": "h",
            "ushort": "H", "uint16": "H", "int": "i", "int32": "i", "uint": "I", "uint32": "I",
            "float": "f", "float32": "f", "double": "d", "float64": "d"}


def ply_first_vertex(path):
    """(vertex count, (x, y, z) of the first vertex) read straight from the PLY
    file - to check what Rhino's PLY reader made of it. None if unsure."""
    import struct
    try:
        with open(path, "rb") as f:
            if f.readline().strip() != b"ply": return None
            fmt = None; elems = []
            while True:
                line = f.readline()
                if not line: return None
                p = line.decode("ascii", "replace").split()
                if not p: continue
                if p[0] == "end_header": break
                if p[0] == "format": fmt = p[1]
                elif p[0] == "element": elems.append([p[1], int(p[2]), []])
                elif p[0] == "property" and elems: elems[-1][2].append(p)
            if not elems or elems[0][0] != "vertex": return None
            n, props = elems[0][1], elems[0][2]
            if any(q[1] == "list" for q in props) or n < 1: return None
            names = [q[2] for q in props]
            if fmt == "ascii":
                vals = f.readline().split()
                return n, tuple(float(vals[names.index(k)]) for k in ("x", "y", "z"))
            end = "<" if fmt == "binary_little_endian" else ">"
            sf = end + "".join(_PLY_FMT[q[1]] for q in props)
            vals = struct.unpack(sf, f.read(struct.calcsize(sf)))
            return n, tuple(float(vals[names.index(k)]) for k in ("x", "y", "z"))
    except Exception:
        return None


def _ply_api_read(path):
    """PLY through RhinoCommon's FilePly.Read: no 'Set PLY import options'
    question. Kept only if the mesh has the file's coordinates (checked on the
    first vertex; a unit scaling is taken back) -> True, else the import is
    undone and False (the command with explicit options is used instead)."""
    ref = ply_first_vertex(path)
    if ref is None: return False
    before = set(o.Id for o in sc.doc.Objects.GetObjectList(ro.ObjectEnumeratorSettings()))
    try:
        try: opts = Rhino.FileIO.FilePlyReadOptions()
        except Exception: opts = Rhino.FileIO.FilePlyReadOptions(Rhino.FileIO.FileReadOptions())
        if not Rhino.FileIO.FilePly.Read(path, sc.doc, opts): return False
    except Exception:
        return False
    new_ids = _new_object_ids(before)
    meshes = [sc.doc.Objects.FindId(i) for i in new_ids]
    meshes = [o for o in meshes if o is not None and isinstance(o.Geometry, rg.Mesh)]
    ok = len(meshes) == 1 and meshes[0].Geometry.Vertices.Count == ref[0]   # its scale: normalize_scale
    if not ok:
        for i in new_ids:
            sc.doc.Objects.Delete(i, True)
    return ok


def raw_vertices(path, n=3):
    """Up to n vertices exactly as written in the scan file (STL / OBJ / PLY),
    to see what scale Rhino's importer used. [] if unsure."""
    import struct
    ext = os.path.splitext(path)[1].lower()
    out = []
    try:
        if ext == ".ply":
            r = ply_first_vertex(path)
            return [r[1]] if r else []
        if ext == ".obj":
            with open(path, "r") as f:
                for line in f:
                    if line.startswith("v "):
                        p = line.split(); out.append((float(p[1]), float(p[2]), float(p[3])))
                        if len(out) >= n: break
            return out
        if ext == ".stl":
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                head = f.read(84)
                cnt = struct.unpack("<I", head[80:84])[0] if len(head) == 84 else -1
                if cnt > 0 and size == 84 + 50 * cnt:
                    v = struct.unpack("<12f", f.read(48))
                    return [(v[3], v[4], v[5]), (v[6], v[7], v[8]), (v[9], v[10], v[11])]
            with open(path, "r") as f:
                for line in f:
                    s_ = line.strip()
                    if s_.startswith("vertex"):
                        p = s_.split(); out.append((float(p[1]), float(p[2]), float(p[3])))
                        if len(out) >= n: break
            return out
    except Exception:
        return []
    return out


IMPORT_SCALES = (1.0, 25.4, 1 / 25.4, 10.0, 0.1, 1000.0, 0.001, 304.8, 1 / 304.8, 100.0, 0.01,
                 2.54, 1 / 2.54, 12.0, 1 / 12.0, 1000.0 / 25.4, 25.4 / 1000.0, 3.28084, 1 / 3.28084)


def detect_import_scale(path, geos):
    """Factor Rhino's importer applied to the file's numbers (1 = none), or None."""
    raw = raw_vertices(path)
    raw = [p for p in raw if max(abs(c) for c in p) > 1e-9]
    if not raw or not geos: return None if not raw else 1.0
    p = max(raw, key=lambda q: abs(q[0]) + abs(q[1]) + abs(q[2]))
    for f in IMPORT_SCALES:
        q = rg.Point3d(p[0] * f, p[1] * f, p[2] * f)
        tol = 1e-4 * max(1.0, abs(q.X) + abs(q.Y) + abs(q.Z))
        for g in geos:
            try:
                c = g.ClosestPoint(q)
                if c is not None and c.DistanceTo(q) <= tol:
                    return f
            except Exception:
                continue
    return None


def normalize_scale(path, geos):
    """The meshes in DOCUMENT units, the file read as ScanMeshUnits (default mm):
    whatever scale Rhino's importer used (it depends on its own settings) is
    replaced by file units -> document units. The same rule as Blender and FreeCAD."""
    want = scan_mm() / _mm_per_unit()
    got = detect_import_scale(path, geos)
    if got is None:
        print("  [WARN] {}: could not check the scale of Rhino's importer - taken as it came".format(
            os.path.basename(path)))
        return
    k = want / got
    if abs(k - 1.0) > 1e-9:
        for g in geos:
            g.Transform(rg.Transform.Scale(rg.Point3d.Origin, k))
    if abs(want - 1.0) > 1e-9:
        print("  (scan/mesh file in {}, document in {}: scaled x{:g})".format(
            sc.sticky.get("M2CA_scan_units", "mm"), doc_unit_name(), want))


def api_insert(path, name, layer_index):
    """STL / OBJ / PLY without -Insert: inside -Insert those formats ask their
    import options in the middle of the command and the rest of a scripted
    answer is lost (it kept asking 'Insert as', point, scale, rotation).
    Here: read the file with default options (no questions), make a block
    definition at 0,0,0 from it, mark it LINKED to the file, place one
    instance with identity transform. The file itself is only read.
    PLY / OBJ: linked through msh2classA_cache/<name>.3dm (see CACHE_DIR)."""
    cached = os.path.splitext(path)[1].lower() in CACHED_EXT
    if cached:
        geos = read_cache(path)                      # read before: no importer, no questions
        if geos is not None:
            print("  (from msh2classA_cache - the scan/mesh file is the same, SHA-256)")
            ats = []
            for g in geos:
                a = ro.ObjectAttributes(); a.LayerIndex = layer_index
                a.ColorSource = ro.ObjectColorSource.ColorFromParent
                ats.append(a)
            return _make_scan_block(path, name, layer_index, geos, ats, from_cache=True)
    before = set(o.Id for o in sc.doc.Objects.GetObjectList(ro.ObjectEnumeratorSettings()))
    is_obj = path.lower().endswith(".obj")
    is_ply = path.lower().endswith(".ply")
    if is_ply and _ply_api_read(path):
        pass                                         # read without any question, coordinates checked
    elif is_ply:
        # PLY: its importer asks 'Set PLY import options (ModelUnits=...)' for
        # EVERY file, even in batch mode. Answered here like OBJ: ModelUnits =
        # the document's units, so the file's numbers are taken as they are
        # (no scaling - as STL, and as the Blender / FreeCAD versions read it),
        # then Enter.
        try:
            unit = str(sc.doc.ModelUnitSystem)
            opt = "_ModelUnits=_{} ".format(unit) if unit in PLY_UNITS else ""
            rs.Command('_-Import "{}" {}_Enter'.format(path, opt), False)
        except Exception:
            pass
    elif is_obj:
        # OBJ: its importer ALWAYS asks 'Choose OBJ option' when run from a
        # script. Rhino remembers the LAST used options, so every option is
        # set here explicitly (MapYtoZ=Yes would turn the scan 90 deg),
        # then Enter.
        try:
            rs.Command('_-Import "{}" {} _Enter'.format(path, OBJ_IMPORT_OPTS), False)
        except Exception:
            pass
    else:
        # STL: batch import, default options, no questions
        try:
            ro_ = Rhino.FileIO.FileReadOptions()
            ro_.ImportMode = True
            ro_.BatchMode = True
            Rhino.RhinoDoc.ReadFile(path, ro_)
        except Exception:
            pass
    new_ids = _new_object_ids(before)
    if not new_ids and is_obj:                       # OBJ reader with default options
        try:
            ro_ = Rhino.FileIO.FileReadOptions(); ro_.ImportMode = True; ro_.BatchMode = True
            Rhino.FileIO.FileObj.Read(path, sc.doc, Rhino.FileIO.FileObjReadOptions(ro_))
        except Exception:
            pass
        new_ids = _new_object_ids(before)
    if not new_ids:                                  # last resort: normal import (Enter = defaults if it asks)
        try:
            sc.doc.Import(path)
        except Exception:
            pass
        new_ids = _new_object_ids(before)
    if not new_ids:
        print("  [ERROR] could not read {}".format(path)); return None
    geos = []; ats = []; skipped = set()
    for oid in new_ids:
        o = sc.doc.Objects.FindId(oid)
        if o is None: continue
        if not isinstance(o.Geometry, rg.Mesh):          # point cloud, curves, ... : not a scan mesh
            skipped.add(o.Geometry.GetType().Name); continue
        geos.append(o.Geometry.Duplicate())
        a = o.Attributes.Duplicate()
        a.LayerIndex = layer_index
        a.ColorSource = ro.ObjectColorSource.ColorFromParent
        ats.append(a)
    for oid in new_ids:
        sc.doc.Objects.Delete(oid, True)
    if not geos:
        if "PointCloud" in skipped:
            print("  [ERROR] {} is a POINT CLOUD, not a scan/mesh - mesh it first (e.g. MeshFromPoints / "
                  "the scanner software), then load the scan/mesh".format(os.path.basename(path)))
        else:
            print("  [ERROR] {} contains no scan/mesh ({})".format(os.path.basename(path), ", ".join(sorted(skipped)) or "empty"))
        return None
    if skipped:
        print("  [INFO] {}: only the scan(s)/mesh(es) kept, ignored: {}".format(os.path.basename(path), ", ".join(sorted(skipped))))
    normalize_scale(path, geos)
    return _make_scan_block(path, name, layer_index, geos, ats)


def rhino9_layer_style():
    """RhinoCommon can set the layer style of a linked block only from Rhino 9 on."""
    try:
        return int(str(Rhino.RhinoApp.ExeVersion)) >= 9 and hasattr(ro, "InstanceDefinitionLayerStyle")
    except Exception:
        return False


def link_block(idx, path, active=False):
    """Block definition idx -> LINKED to the file path (nothing of it saved in
    the project). RhinoCommon: InstanceDefinitionTable.ModifySourceArchive
    (up to the first test builds the script called a name that does not exist,
    so every scan was embedded). active=True (Rhino 9 only): layer style Active -
    the file's layers are the model's own, no grey reference layers."""
    tbl = sc.doc.InstanceDefinitions
    lk = ro.InstanceDefinitionUpdateType.Linked
    fr = lambda: Rhino.FileIO.FileReference.CreateFromFullPath(path)
    tries = []
    if active:
        tries.append(lambda: tbl.ModifySourceArchive(idx, fr(), lk, ro.InstanceDefinitionLayerStyle.Active, True))
    tries += [lambda: tbl.ModifySourceArchive(idx, fr(), lk, True),
              lambda: tbl.ModifySourceArchive(idx, path, lk, True)]
    for f in tries:
        try:
            if f():
                idef = tbl[idx]
                if idef is None or idef.UpdateType == lk:
                    return True
        except Exception:
            continue
    return False


def _make_scan_block(path, name, layer_index, geos, ats, from_cache=False):
    """Block definition at 0,0,0 from the meshes, LINKED (STL: to the scan file;
    PLY / OBJ: to its cache .3dm), one instance with identity transform."""
    link = path
    dname = name; k = 2
    while sc.doc.InstanceDefinitions.Find(dname) is not None:
        dname = "{} ({})".format(name, k); k += 1
    active = False
    if os.path.splitext(path)[1].lower() in CACHED_EXT:
        # Rhino 9: layer style Active - the cache mesh on the scan's own layer, no grey reference layers.
        # Rhino 8: Rhino's default style Reference - a grey layer, named so it says what it is.
        active = rhino9_layer_style()
        layer_path = sc.doc.Layers[layer_index].FullPath if active else CACHE_READABLE_LAYER
        cp = _cache_for(path, geos, layer_path, from_cache)
        if cp is None:
            print("  [WARN] could not write the cache .3dm of {} - the block will be EMBEDDED "
                  "(bigger file)".format(os.path.basename(path)))
        else:
            link = cp
    idx = sc.doc.InstanceDefinitions.Add(dname, "scan/mesh: " + path, rg.Point3d.Origin, geos, ats)
    if idx < 0:
        print("  [ERROR] could not make a block from {}".format(path)); return None
    linked = False
    if os.path.splitext(link)[1].lower() in CACHED_EXT:
        link = None                                  # never link straight to a PLY / OBJ (see CACHE_DIR)
    if link is not None:
        linked = link_block(idx, link, active)
    if not linked:
        print("  [INFO] block of {} is embedded (Rhino refused the link) - the file is untouched".format(dname))
    at = ro.ObjectAttributes(); at.LayerIndex = layer_index
    iid = sc.doc.Objects.AddInstanceObject(idx, rg.Transform.Identity, at)
    if iid is None or str(iid) == "00000000-0000-0000-0000-000000000000":
        print("  [ERROR] could not place the block of {}".format(path)); return None
    return iid


def load_scans():
    """LoadMeshes: pick many STL / OBJ / PLY files; each one becomes a LINKED
    block (embedded if Rhino refuses the link) at its own coordinates, on its
    own layer under 'Reference Meshes- Msh2classA', with its own colour. Each is hidden after
    loading - only the last one stays visible. Files are only read."""
    files = rs.OpenFileNames("Load scans/meshes as linked blocks",
                             "Scans/meshes (*.stl;*.obj;*.ply)|*.stl;*.obj;*.ply|All files (*.*)|*.*||")
    if not files: return
    single_view()
    n_old = len(scan_instances())
    cols = scan_palette(len(files) + n_old)[n_old:]
    root = ensure_layer(SCAN_ROOT)
    old_layer = sc.doc.Layers.CurrentLayerIndex
    # Rhino never hides the CURRENT layer: while loading, the current layer
    # must not be one of the scan layers (the parent 'Reference Meshes- Msh2classA' is fine)
    if in_scan_root(sc.doc.Layers[old_layer].FullPath, below=True):
        sc.doc.Layers.SetCurrentLayerIndex(root, True)
    try: Rhino.UI.StatusBar.ShowProgressMeter(0, len(files), "Loading scans/meshes", True, True)
    except Exception: pass
    done = []; hidden = set()
    stopped = False
    try:
        for n, path in enumerate(files):
            if stopped or esc_pressed():
                stopped = True; break
            name = os.path.splitext(os.path.basename(path))[0]
            if os.path.splitext(path)[1].lower() not in (".stl", ".obj", ".ply"):
                print("  [SKIP] {} - only STL / OBJ / PLY".format(path)); continue
            busy = show_busy("Loading scan/mesh {}/{}: {} ...".format(n + 1, len(files), name), esc="stop loading after this scan/mesh")
            print("  loading {}/{}: {}".format(n + 1, len(files), path))
            li = None
            try:
                li = ensure_layer(SCAN_ROOT + "::" + name, cols[n])
                rs.UnselectAllObjects()
                iid = api_insert(path, name, li)
                if esc_pressed():                     # Esc during the import -> stop after this file
                    stopped = True
                obj = sc.doc.Objects.FindId(iid) if iid is not None else None
                if not isinstance(obj, ro.InstanceObject):
                    print("  [ERROR] could not load {}".format(path)); li = None; continue
                at = obj.Attributes.Duplicate()
                at.LayerIndex = li
                at.ColorSource = ro.ObjectColorSource.ColorFromObject
                at.ObjectColor = cols[n]
                at.SetUserString(SCAN_KEY, path)
                sc.doc.Objects.ModifyAttributes(iid, at, True)
                done.append(li)
            finally:
                hide_busy(busy)
                try: Rhino.UI.StatusBar.UpdateProgressMeter(n + 1, True)
                except Exception: pass
            if n < len(files) - 1 and not stopped and li is not None:   # hidden - only the last one stays visible
                lay = sc.doc.Layers[li]; lay.IsVisible = False; lay.CommitChanges()
                hidden.add(li)
    finally:
        try: Rhino.UI.StatusBar.HideProgressMeter()
        except Exception: pass
        if old_layer not in hidden:                   # never make a just-hidden layer current again
            sc.doc.Layers.SetCurrentLayerIndex(old_layer, True)
        sc.doc.Views.Redraw()
    if stopped:
        print("[LoadMeshes] STOPPED by Esc - {} of {} scan(s)/mesh(es) loaded, the rest skipped".format(len(done), len(files)))
    else:
        print("[LoadMeshes] {} of {} scan(s)/mesh(es) loaded as blocks under layer '{}' (only the last one shown)".format(
            len(done), len(files), SCAN_ROOT))
    if done:
        refresh_layer_panel()


def refresh_layer_panel():
    """The Layers panel (Rhino 9 at least) shows layers a running script made
    only when a COMMAND ends - and msh2classA is one command until you leave
    it, so the loaded scans/meshes stayed out of the panel till then. A tiny
    command that ends at once (-Layer, Enter) lets the panel show them now."""
    try:
        Rhino.RhinoApp.RunScript("_-Layer _Enter", False)
    except Exception:
        pass




def prefetch_meshes(insts):
    """Read the meshes (and the mirror points) of the visible meshes NOW,
    never for the first time inside a drawing / mouse event. After a plain
    mesh was moved its points are made again here."""
    for o in insts:
        cur = sc.doc.Objects.FindId(o.Id)
        if cur is not None and scan_visible(cur):
            scan_cloud(cur, True)


_RAY_READY = set()


def prepare_scans(insts, tool):
    """Everything slow BEFORE the first click, behind a 'please wait': the
    meshes are read, the mirror points made and the ray search tree of every
    visible mesh built (that tree made the first click take ~10 s)."""
    _SAID.clear()
    busy = show_busy("{}: preparing the scans/meshes for clicking (first time only, big scans/meshes take a few seconds)...".format(tool),
                     esc="cancel {}".format(tool))
    try:
        # the prompt says 'Esc = stop': Esc is checked between the meshes, so
        # it really stops the tool (before, it was only remembered and then
        # cancelled the first prompt of the tool - the tool seemed to do nothing)
        check_esc()
        prefetch_meshes(insts)
        for o0 in insts:
            check_esc()
            o = sc.doc.Objects.FindId(o0.Id)
            if o is None or not scan_visible(o): continue
            geo = scan_geo(o)
            if geo is None or id(geo[0]) in _RAY_READY: continue
            bb = geo[0].GetBoundingBox(False)
            try:
                rg.Intersect.Intersection.MeshRay(geo[0], rg.Ray3d(bb.Center + rg.Vector3d(0, 0, bb.Diagonal.Length),
                                                                    rg.Vector3d(0, 0, -1)))
                geo[0].ClosestMeshPoint(bb.Center, 0.0)          # AutoFit / closest point search tree
            except Exception:
                pass
            _RAY_READY.add(id(geo[0]))
        check_esc()
    finally:
        hide_busy(busy)


def click_plane(vp):
    """A plane facing the camera that every cursor ray meets INSIDE the
    view's depth range (between its near and far clipping planes). Through
    the view target was not enough: zoomed in near a mesh - or with the
    mirror / spheres making the scene bigger in Center - the target can lie
    outside that range, and Rhino shows the 'no entry' cursor again."""
    d = None
    try:
        r = vp.GetFrustum()
        if r[0]:
            n_, f_ = float(r[5]), float(r[6])
            if 0.0 < n_ < f_:
                d = math.sqrt(n_ * f_) if vp.IsPerspectiveProjection else 0.5 * (n_ + f_)
    except Exception:
        d = None
    if d is None:
        return rg.Plane(vp.CameraTarget, vp.CameraX, vp.CameraY)
    dv = rg.Vector3d(vp.CameraDirection); dv.Unitize()
    return rg.Plane(vp.CameraLocation + dv * d, vp.CameraX, vp.CameraY)


def view_constrain(gp):
    """Clicks on the mesh: the point comes from OUR ray on the meshes, Rhino
    only has to give a point on the cursor line. Unconstrained it uses the
    construction plane - above the horizon in Perspective there is none and
    Rhino shows the 'no entry' cursor and takes no click (see CLICK_PLANE).
    The plane is made again on EVERY mouse move from the view as it is then:
    the wheel zooms TO the object under the cursor (Rhino 8), the camera moves
    during the prompt, and a plane made once at the start ended up behind the
    camera - 'no entry' over the mesh (and over the blue mirror points, which
    the zoom also aims at) until the next prompt."""
    try:
        gp.Constrain(click_plane(sc.doc.Views.ActiveView.ActiveViewport), False)
    except Exception:
        pass

    def follow(sender, e):
        try:
            vp = e.Viewport if getattr(e, "Viewport", None) is not None else sc.doc.Views.ActiveView.ActiveViewport
            gp.Constrain(click_plane(vp), False)
        except Exception:
            pass
    try:
        gp.MouseMove += follow
    except Exception:
        pass


def pause_scanmirror():
    """MeshMirror (left on, maybe from an earlier run of the script) would draw
    a second mirror that the MirrorPreview option cannot hide - off while a
    mesh tool runs. -> its plane axis, or None."""
    cur = sc.sticky.get("M2CA_scanmir")
    if cur is None: return None
    try: cur.off()
    except Exception: pass
    sc.sticky["M2CA_scanmir"] = None
    sc.doc.Views.Redraw()
    return getattr(cur, "axis", None)


def resume_scanmirror(axis):
    if axis is None: return
    objs = [o for (o, lp) in scan_objects() if scan_visible(o)]
    prefetch_meshes(objs)
    c = _ScanMirrorDraw(axis, [o.Id for o in objs]); c.on()
    sc.sticky["M2CA_scanmir"] = c
    sc.doc.Views.Redraw()


_LOCAL_MESH = {}


def local_mesh(idef):
    """The block's mesh in block coordinates (once per definition)."""
    m = _LOCAL_MESH.get(idef.Id)
    if m is not None: return m
    busy = show_busy("Reading scan/mesh {} (once)...".format(idef.Name), esc="stop - cancels what needs this scan/mesh")
    joined = rg.Mesh()

    def collect(d, x):
        for i in range(d.ObjectCount):
            check_esc()
            piece = d.Object(i)
            if piece is None: continue
            if isinstance(piece.Geometry, rg.Mesh):
                mm = piece.Geometry.Duplicate(); mm.Transform(x); joined.Append(mm)
            elif isinstance(piece, ro.InstanceObject):
                collect(piece.InstanceDefinition, x * piece.InstanceXform)
    try:
        collect(idef, rg.Transform.Identity)
        joined.Compact(); joined.FaceNormals.ComputeFaceNormals()
    finally:
        hide_busy(busy)
    _LOCAL_MESH[idef.Id] = joined
    return joined


# CLICK_UP: the mesh tools take a click on mouse UP. Taken on mouse DOWN, the
# next prompt started while the button was still down; the release went to it
# and Rhino drew no preview (tags, mirror points) until the prompt started
# again (e.g. after clicking an option).
#
# CLICK_PLANE: without object snap a GetPoint looks for the point on the
# CPlane. Where the cursor ray misses it (above the horizon, views from below,
# zoomed in) Rhino shows the NO-ENTRY cursor and the point gets stuck where it
# last was - the click then lands off the cursor, not on the mesh under it.
# A window-point Get (get2dPoint) did not avoid that. So every mesh-tool click
# is constrained to a plane FACING the camera through its target: every cursor
# ray meets it, Rhino always has the point, and that point lies exactly on the
# ray through the cursor - the ray we shoot onto the meshes. The plane sits in
# the middle of the view's depth range (click_plane), never outside it.
def scan_get(gp):
    """Get one click for the mesh tools (see CLICK_UP, CLICK_PLANE)."""
    try:
        gp.PermitObjectSnap(False)               # an osnap would pull the point off the cursor ray
    except Exception:
        pass
    view_constrain(gp)
    return tracked_get(gp)


# The GetPoint now waiting for a mesh-tool click: its plane is made again
# whenever the view changes, HOWEVER it changes - wheel, Ctrl / Shift + right
# drag, Page Up / Down, touchpad, 3D mouse, a Zoom typed in: every one of them
# redraws the view, and the tools' display conduit (_ScanDraw) then hands the
# new viewport to _reconstrain. A mouse move and RhinoView.Modified do the same.
_CLICK_GP = []
_CLICK_KEY = {}


def _reconstrain(vp):
    if not _CLICK_GP or vp is None: return
    gp = _CLICK_GP[-1]
    try:
        L = vp.CameraLocation; D = vp.CameraDirection
        r = vp.GetFrustum()
        key = (round(L.X, 6), round(L.Y, 6), round(L.Z, 6), round(D.X, 9), round(D.Y, 9), round(D.Z, 9),
               round(float(r[5]), 6) if r[0] else 0, round(float(r[6]), 3) if r[0] else 0)
    except Exception:
        key = None
    if key is not None and _CLICK_KEY.get(id(gp)) == key: return
    try:
        gp.Constrain(click_plane(vp), False)
        _CLICK_KEY[id(gp)] = key
    except Exception:
        pass


def tracked_get(gp):
    """gp.Get(True) with its click plane kept up to date (see above)."""
    def modified(sender, e):
        try: _reconstrain(e.View.ActiveViewport)
        except Exception: pass
    hooked = False
    try:
        Rhino.Display.RhinoView.Modified += modified; hooked = True
    except Exception:
        pass
    _CLICK_GP.append(gp)
    try:
        return gp.Get(True)
    finally:
        if gp in _CLICK_GP: _CLICK_GP.remove(gp)
        _CLICK_KEY.pop(id(gp), None)
        if hooked:
            try: Rhino.Display.RhinoView.Modified -= modified
            except Exception: pass


def is_click(res):
    return res == Rhino.Input.GetResult.Point or res == Rhino.Input.GetResult.Point2d


def click_ray(gp):
    """(start point, direction) of the ray through the cursor at the click,
    or None. From the 3D point Rhino gives (on the camera-facing plane, see
    CLICK_PLANE); the window point only if that fails."""
    try:
        vp = gp.View().ActiveViewport
        P = gp.Point()
        if P.IsValid:
            if vp.IsPerspectiveProjection:
                a_ = rg.Point3d(vp.CameraLocation); d0 = P - a_
                if d0 * vp.CameraDirection < 0: d0 = -d0
            else:
                d0 = rg.Vector3d(vp.CameraDirection); d0.Unitize()
                a_ = P - d0 * (10.0 * max(1.0, vp.CameraLocation.DistanceTo(P)) + 1e6)
            if d0.Length > 1e-12:
                return a_, d0
    except Exception:
        pass
    try:
        vp = gp.View().ActiveViewport
        p2 = gp.Point2d()
        r = vp.GetFrustumLine(float(p2.X), float(p2.Y))
        if isinstance(r, tuple):
            if not r[0]: return None
            line = r[1]
        else:
            line = r
        return rg.Point3d(line.From), line.To - line.From
    except Exception:
        return None


def pick_on_scans(gp, insts):
    """Point under the click on the VISIBLE meshes (front-most) -> (point, object) or None."""
    ray = click_ray(gp)
    if ray is None: return None
    return ray_hit_scans(ray[0], ray[1], insts)


def pick_meant(gp, insts, cand):
    """The click on one of the meshes cand (the mesh the prompt asks for).
    -> ((point, object), None) or (None, why). The front-most mesh under the
    cursor counts; a cand mesh BEHIND another one only where both lie on each
    other (overlap after pairs, within ~3 mm) - a click clearly on the other
    mesh is refused, never moved onto a mesh behind it."""
    ray = click_ray(gp)
    if ray is None:
        return None, "nothing under the cursor"
    a_, d0 = ray
    ids = set(str(o.Id) for o in cand)
    front = ray_hit_scans(a_, d0, insts)
    want = ray_hit_scans(a_, d0, cand)
    if want is None:
        return None, "the scan/mesh asked for is not under the cursor"
    if front is None or str(front[1].Id) in ids:
        return _on_each_other(a_, d0, cand, want), None
    # through the mesh in front only where both really lie on each other (3 mm,
    # a bit more far away) - 1 % of the eye distance (3-5 cm on a whole car)
    # put points visibly off the mesh you clicked
    tol = max(3.0 / _mm_per_unit(), 0.002 * a_.DistanceTo(front[0]))
    if front[0].DistanceTo(want[0]) <= tol:
        return _on_each_other(a_, d0, cand, want), None      # the two meshes lie on each other here
    return None, "that is the OTHER scan/mesh (in front)"


_SAID = set()


def _on_each_other(a_, d0, cand, want):
    """Several cand meshes lie EXACTLY on each other at the click (aligned
    before): which one was hit first was chance - and a mesh taken alone
    leaves behind the ones it carries. Take the one that carries the others
    (the root of them in the Align tree) and say so."""
    tol = 0.5 / _mm_per_unit()
    here = []
    for o in cand:
        h = ray_hit_scans(a_, d0, [o])
        if h is not None and h[0].DistanceTo(want[0]) <= tol:
            here.append(h)
    if len(here) < 2: return want
    ids = [str(h[1].Id) for h in here]
    best = want; best_n = -1
    for h in here:
        br = align_branch(h[1].Id)
        n = sum(1 for i in ids if i in br)
        if n > best_n or (n == best_n and h[1].Id == want[1].Id):
            best, best_n = h, n
    names = sorted(_scan_name(h[1]) for h in here)
    msg = ("  {} lie on each other here - took {}{} (to click only one of them, hide the other)".format(
        ", ".join(names), _scan_name(best[1]),
        " (it carries the others)" if best_n == len(here) else ""))
    if msg not in _SAID:
        _SAID.add(msg); print(msg)
    return best


def ray_on_scans(view, P, insts):
    """Ray from the eye through P (a point Rhino reports on the cursor line)
    -> (hit, object) on the front-most visible mesh. Blocks: ray in the
    block's own coordinates (no mesh copies); plain meshes: as they are."""
    try:
        vp = view.ActiveViewport
        big = 10.0 * max(1.0, vp.CameraLocation.DistanceTo(P)) + 1e6
        if vp.IsPerspectiveProjection:
            a_ = rg.Point3d(vp.CameraLocation); d0 = P - a_
            if d0 * vp.CameraDirection < 0: d0 = -d0          # P behind the eye: same line
        else:
            d0 = rg.Vector3d(vp.CameraDirection); d0.Unitize()
            a_ = P - d0 * big
        if d0.Length < 1e-12: return None
    except Exception:
        return None
    return ray_hit_scans(a_, d0, insts)


def ray_hit_scans(a_, d0, insts):
    """Ray a_ + t*d0 (t > 0) -> (hit, object) on the front-most visible mesh."""
    d0 = rg.Vector3d(d0)
    if d0.Length < 1e-12: return None
    d0.Unitize()
    big = 1e7
    b_ = a_ + d0 * big
    best = None
    for obj0 in insts:
        # after a move Rhino replaces the object (same id): always the current one
        obj = sc.doc.Objects.FindId(obj0.Id)
        if obj is None or not scan_visible(obj): continue
        geo = scan_geo(obj)
        if geo is None: continue
        mesh, xf = geo
        ok, inv = xf.TryGetInverse()
        if not ok: continue
        la = rg.Point3d(a_); lb = rg.Point3d(b_); la.Transform(inv); lb.Transform(inv)
        d = lb - la; d.Unitize()
        t = rg.Intersect.Intersection.MeshRay(mesh, rg.Ray3d(la, d))
        if t is None or t < 0: continue
        hit = la + d * t; hit.Transform(xf)
        dd = hit.DistanceTo(a_)
        if best is None or dd < best[0]: best = (dd, hit, obj)
    return None if best is None else (best[1], best[2])


SYM_DEFAULT = "YZ"          # the car's centre plane: the vertical middle line of the Front view (x = 0)
SYM_TEXT = {"YZ": "YZ (x = 0, the middle line seen in the Front view)",
            "XZ": "XZ (y = 0, the middle line seen in the Right view)",
            "XY": "XY (z = 0)"}


def mirror_normal(tool="Center", allowed=("YZ", "XZ", "XY")):
    """Symmetry plane for the mesh tools = MirrorPlane. Off: the default YZ
    (the middle line of the Front view) is taken - no question. Change it
    with the SymPlane option of MeshCenter / MeshFloor (or MirrorPlane)."""
    cur = opt_get("mirror", "Off")
    if cur not in allowed:
        cur = SYM_DEFAULT if SYM_DEFAULT in allowed else allowed[0]
        opt_set("mirror", cur)
        print("[{}] symmetry plane = {} - change it with the SymPlane option".format(tool, SYM_TEXT[cur]))
    return MIRROR_AXIS[cur]


def add_sym_option(gp, allowed=("YZ", "XZ", "XY")):
    """SymPlane list option on a mesh-tool prompt -> its index."""
    cur = opt_get("mirror", SYM_DEFAULT)
    return gp.AddOptionList("SymPlane", list(allowed), list(allowed).index(cur) if cur in allowed else 0)


def set_sym_from_option(gp, allowed=("YZ", "XZ", "XY")):
    key = list(allowed)[gp.Option().CurrentListOptionIndex]
    opt_set("mirror", key)
    print("  symmetry plane = {}".format(SYM_TEXT[key]))
    return MIRROR_AXIS[key]


def single_view():
    """One maximized Perspective view: every viewport keeps its own copy of
    the big meshes for display, 4 views are much slower than one."""
    try:
        views = list(sc.doc.Views.GetViewList(True, False))
        per = None
        for v in views:
            if v.ActiveViewport.Name.lower() == "perspective": per = v; break
        if per is None:
            for v in views:
                if v.ActiveViewport.IsPerspectiveProjection: per = v; break
        if per is None and views: per = views[0]
        if per is None: return
        sc.doc.Views.ActiveView = per
        if not per.Maximized: per.Maximized = True
        sc.doc.Views.Redraw()
    except Exception:
        pass


def _xf_is_identity(X, tol=1e-12):
    for i in range(4):
        for j in range(4):
            if abs(X[i, j] - (1.0 if i == j else 0.0)) > tol: return False
    return True


def _apply(insts, applied, T):
    """Bring every chosen mesh to T * (its place at the start of the tool).
    applied = {id: transform given so far} - works the same for blocks and
    plain meshes; nothing is touched when T did not change."""
    for obj in insts:
        cur = sc.doc.Objects.FindId(obj.Id)
        if cur is None: continue
        prev = applied.get(obj.Id, rg.Transform.Identity)
        ok, inv = prev.TryGetInverse()
        if not ok: continue
        delta = T * inv
        if _xf_is_identity(delta): continue
        sc.doc.Objects.Transform(obj.Id, delta, True)
        applied[obj.Id] = T                       # (a struct: stored as a copy)
    prefetch_meshes(insts)                       # moved plain meshes: new mirror points


PIN_COL = (255, 170, 0)          # tags on the scan
GHOST_COL = (0, 200, 255)        # where the LEFT tag lands after the mirror
HIDDEN_COL = (150, 150, 150)     # Fine: the clicked R tag, only as a small grey cross
AX_COL = ((225, 40, 40), (40, 170, 60), (40, 100, 235))     # world X / Y / Z
FINE_ARROW_PX = 95               # gumball size on screen
FINE_RING = 0.62                 # ring radius / arrow length


def _pf(x, y):
    return sd.PointF(float(x), float(y))


def draw_pin(d, m, p, text, colour, leader=True, up=34):
    """A tag you can place exactly: a cross-hair with a hole AT the point
    (2D, always on top), a leader line up and the label above it - like a
    map pin. leader=False: only the cross-hair (ghost)."""
    x, y = to_screen(m, T(p))
    c = sd.Color.FromArgb(*colour); k = sd.Color.FromArgb(20, 20, 20)
    segs = [((x - 9, y), (x - 3, y)), ((x + 3, y), (x + 9, y)), ((x, y - 9), (x, y - 3)), ((x, y + 3), (x, y + 9))]
    if leader:
        segs.append(((x, y - 9), (x, y - up + 9)))
    for (a, b) in segs:
        d.Draw2dLine(_pf(*a), _pf(*b), k, 3.5)
    for (a, b) in segs:
        d.Draw2dLine(_pf(*a), _pf(*b), c, 1.5)
    if leader and text:
        d.DrawDot(float(x), float(y - up), text, c, k)


_ACTIVE_DRAWS = []


class _ScanDraw(object):
    """What the mesh tools show while you click: tags as pins, the mirrored copy
    (blue points) of the visible meshes, the AutoFit spheres and (Fine) the
    gumball. Drawn by Rhino's display pipeline (like MeshMirror) on EVERY
    redraw - not by the GetPoint's DynamicDraw: the tools ask only for a window
    point (no 3D point), and then Rhino calls DynamicDraw irregularly or not at
    all (tags appeared only after some pairs, SpherePreview did nothing).
    On from creation; off() / _ScanDraw.off_all() when the tool ends."""
    def __init__(self, insts, axis):
        self.insts = insts; self.axis = axis; self.dots = []; self.lines = []
        self.show_mirror = axis is not None
        self.pins_fn = None          # -> ([(point, text, colour, leader)], [(a, b) links])
        self.sph_fn = None           # -> (centres, radius): the AutoFit spheres
        self.sph_until = 0.0         # shown until this time (after a radius change) ...
        self.sph_on = False          # ... or all the time (SpherePreview)
        self.extra = None            # f(display, viewport, m): e.g. the Fine gumball
        self._on = False
        self._hb = self._bbox; self._h3 = self._post; self._h2 = self._fore   # same handlers for += and -=
        self.on()

    def on(self):
        if self._on: return
        Rhino.Display.DisplayPipeline.CalculateBoundingBox += self._hb
        Rhino.Display.DisplayPipeline.PostDrawObjects += self._h3
        Rhino.Display.DisplayPipeline.DrawForeground += self._h2
        self._on = True
        _ACTIVE_DRAWS.append(self)

    def off(self):
        try: Rhino.Display.DisplayPipeline.CalculateBoundingBox -= self._hb
        except Exception: pass
        try: Rhino.Display.DisplayPipeline.PostDrawObjects -= self._h3
        except Exception: pass
        try: Rhino.Display.DisplayPipeline.DrawForeground -= self._h2
        except Exception: pass
        self._on = False
        if self in _ACTIVE_DRAWS: _ACTIVE_DRAWS.remove(self)

    @staticmethod
    def off_all():
        for dw in list(_ACTIVE_DRAWS):
            dw.off()
        try: sc.doc.Views.Redraw()
        except Exception: pass

    def _visible(self):
        objs = [sc.doc.Objects.FindId(obj.Id) for obj in self.insts]
        return [o for o in objs if o is not None and scan_visible(o)]

    def _bbox(self, sender, e):
        """Mirror points and spheres may lie outside the meshes: keep them in the view's depth range."""
        try:
            if self.show_mirror and self.axis is not None:
                nrm = [0.0, 0.0, 0.0]; nrm[self.axis] = 1.0
                M_ = rg.Transform.Mirror(rg.Point3d(0, 0, 0), rg.Vector3d(nrm[0], nrm[1], nrm[2]))
                for o in self._visible():
                    bb = o.Geometry.GetBoundingBox(True); bb.Transform(M_); e.IncludeBoundingBox(bb)
            if self.sph_fn:
                centres, r = self.sph_fn()
                for c in centres or []:
                    e.IncludeBoundingBox(rg.BoundingBox(c - rg.Vector3d(r, r, r), c + rg.Vector3d(r, r, r)))
        except Exception:
            pass

    def _post(self, sender, e):
        try: self.draw3d(e.Display)
        except Exception: pass

    def _fore(self, sender, e):
        try: _reconstrain(e.Viewport)               # the view changed (zoom, pan, turn): a fresh click plane
        except Exception: pass
        try: self.draw2d(e.Display, e.Viewport)
        except Exception: pass

    def draw3d(self, d):
        if self.show_mirror and self.axis is not None:
            draw_mirror_points(d, self.axis, self._visible())
        self.draw_spheres(d)
        for (a, b) in self.lines:
            d.DrawDottedLine(a, b, sd.Color.FromArgb(255, 120, 0))

    def draw2d(self, d, vp):
        m = xf_tuple(vp)
        pins, links = self.pins_fn() if self.pins_fn else ([], [])
        for (a, b) in links:
            xa, ya = to_screen(m, T(a)); xb, yb = to_screen(m, T(b))
            d.Draw2dLine(_pf(xa, ya), _pf(xb, yb), sd.Color.FromArgb(*GHOST_COL), 1.0)
        for (p, txt, colr, lead) in pins:
            draw_pin(d, m, p, txt, colr, lead)
        for (p, txt) in self.dots:
            draw_pin(d, m, p, txt, PIN_COL)
        if self.extra:
            self.extra(d, vp, m)

    def draw(self, sender, e):
        """(old DynamicDraw entry, kept for safety: draws the same)"""
        try:
            self.draw3d(e.Display); self.draw2d(e.Display, e.Viewport)
        except Exception:
            pass


def _draw_spheres(self, d):
    """AutoFit spheres, see-through, fading out after a radius change."""
    if not self.sph_fn: return
    now = time.time()
    if not self.sph_on and now > self.sph_until: return
    try:
        centres, r = self.sph_fn()
    except Exception:
        return
    if not centres or r <= 0: return
    tr = 0.55 if self.sph_on else 0.55 + 0.4 * max(0.0, 1.0 - (self.sph_until - now) / SPHERE_FLASH)
    mat = Rhino.Display.DisplayMaterial(sd.Color.FromArgb(255, 170, 0), min(tr, 0.95))
    for c in centres:
        try:
            ms = rg.Mesh.CreateFromSphere(rg.Sphere(c, r), 24, 16)
            d.DrawMeshShaded(ms, mat)
        except Exception:
            pass


SPHERE_FLASH = 3.0               # seconds the AutoFit spheres show after a radius change
_ScanDraw.draw_spheres = _draw_spheres


def scan_center():
    """Center: pairs LEFT / RIGHT on the same features; after every pair the
    chosen meshes (hidden ones too) are turned and moved so the symmetry plane
    of the pairs is the MirrorPlane. Blue points = the mirrored meshes.
    Fine: a gumball in world axes (move / turn only, very fine steps) to
    tune by eye with the live mirror and the tags."""
    single_view()
    axis = mirror_normal("Center")
    if axis is None: return
    insts = all_scan_insts()                    # all scans together: the alignment stays
    if not insts: return
    prepare_scans(insts, "Center")
    mir_axis = pause_scanmirror()
    try:
        _scan_center(axis, insts)
    finally:
        _ScanDraw.off_all()                      # tags / mirror / spheres off
        resume_scanmirror(mir_axis)


def _scan_center(axis, insts):
    applied = {}                                 # id -> transform given so far
    def Ev():
        e = [0.0, 0.0, 0.0]; e[st["axis"]] = 1.0
        return rg.Vector3d(e[0], e[1], e[2])
    pairs = []                                   # (left, right) in BASE coordinates (scans as at the start)
    drw = _ScanDraw(insts, axis)
    st = {"T": rg.Transform.Identity,            # from the pairs
          "F": rg.Transform.Identity,            # Fine moves on top
          "tilt": 0.0, "left": None, "axis": axis}

    def total():
        return st["F"] * st["T"]

    def world(pb):
        q = rg.Point3d(pb); q.Transform(total()); return q

    def solve():
        st["tilt"] = 0.0
        E = Ev()
        if not pairs: return rg.Transform.Identity
        n = rg.Vector3d(0, 0, 0); c = rg.Point3d(0, 0, 0)
        for (L_, R_) in pairs:
            v = R_ - L_
            if v.Length > 1e-12:
                v.Unitize()
                if v * E < 0: v = -v
                n += v
            c += (L_ + R_) * 0.5
        c = c / float(len(pairs))
        if n.Length < 1e-12:
            print("  [WARN] the pairs have no left-right direction (points on top of each other) - nothing turned")
            return rg.Transform.Translation(-E * (c.X * E.X + c.Y * E.Y + c.Z * E.Z))
        n.Unitize()
        st["tilt"] = math.degrees(math.acos(max(-1.0, min(1.0, n * E))))
        rot = rg.Transform.Rotation(n, E, c)
        cc = rg.Point3d(c); cc.Transform(rot)
        off = cc.X * E.X + cc.Y * E.Y + cc.Z * E.Z
        return rg.Transform.Translation(-E * off) * rot

    def pair_dev(k):
        """Pair k now: (left, right, left MIRRORED, distance mirrored-left to
        right, off the plane part, along / up part)."""
        a = world(pairs[k][0]); b = world(pairs[k][1])
        E = Ev(); ax = st["axis"]
        am = [a.X, a.Y, a.Z]; am[ax] = -am[ax]
        am = rg.Point3d(am[0], am[1], am[2])
        dv = am - b
        across = abs(dv * E)
        return a, b, am, dv.Length, across, math.sqrt(max(0.0, dv.Length ** 2 - across ** 2))

    def pins():
        out = []; links = []
        for k in range(len(pairs)):
            a, b, am, dist, _, _ = pair_dev(k)
            out.append((a, "L{}".format(k + 1), PIN_COL, True))
            if st.get("fine"):
                # Fine: r = L mirrored in the present centre, with its distance to
                # the clicked R; R itself only as a small grey cross (direction)
                out.append((am, "r{}  {}".format(k + 1, fl(dist)), GHOST_COL, True))
                out.append((b, "", HIDDEN_COL, False))
            else:
                out.append((b, "R{}  {}".format(k + 1, fl(dist)), PIN_COL, True))
                out.append((am, "", GHOST_COL, False))  # where L lands after the mirror
            links.append((am, b))
        if st["left"] is not None:
            out.append((world(st["left"]), "L{}".format(len(pairs) + 1), PIN_COL, True))
        return out, links
    drw.pins_fn = pins
    # the mirror of a scan not centred yet shows nothing useful: preview off
    # until the first pair is done, then on (unless MirrorPreview was clicked)
    drw.show_mirror = False
    st["mir_user"] = False

    def print_devs(short=False):
        if not pairs: return
        rows = [pair_dev(k) for k in range(len(pairs))]
        worst = max(r[3] for r in rows)
        if short:
            print("    r to R: " + "  ".join("r{} {}".format(k + 1, fl(r[3])) for k, r in enumerate(rows)) +
                  "   worst {}".format(fl(worst)))
            return
        print("  mirrored partner off by (total | off the plane | along/up):")
        for k, r in enumerate(rows):
            print("    pair {}: {} | {} | {}".format(k + 1, fl(r[3]), fl(r[4]), fl(r[5])))
        print("    worst {}".format(fl(worst)))

    def commit():
        _apply(insts, applied, total())

    def tag_centres():
        """Tags (left and right) where they are now - the AutoFit spheres."""
        out = []
        for (L_, R_) in pairs:
            out += [world(L_), world(R_)]
        if st["left"] is not None: out.append(world(st["left"]))
        return out
    st["tag_centres"] = tag_centres
    drw.sph_fn = lambda: (tag_centres(), autofit_radius())

    while True:
        gp = ric.GetPoint()
        locked = not _xf_is_identity(st["F"])         # after Fine / AutoFit: no more pairs
        if locked:
            gp.SetCommandPrompt("Center: tuned with Fine / AutoFit - pairs are locked now (ResetFine = back to the "
                                "pairs), Enter = done, Esc = cancel Center (scans/meshes back, Fine / AutoFit too)")
        else:
            gp.SetCommandPrompt("Center: pair {} - click the {} point ({} pair(s); then Fine / AutoFit), Enter = done, "
                                "Esc = cancel Center, scans/meshes back)".format(len(pairs) + 1, "LEFT" if st["left"] is None else "RIGHT", len(pairs)))
        gp.AcceptNothing(True)
        o_undo = o_sym = o_reset = -99
        if not locked:
            o_undo = gp.AddOption("UndoPair")
        o_cancel = gp.AddOption("Cancel")
        o_mir = gp.AddOption("MirrorPreview"); o_fine = gp.AddOption("Fine")
        o_auto = gp.AddOption("AutoFit")
        o_radv = ric.OptionDouble(autofit_radius(), 0.0, 1e6); o_rad = gp.AddOptionDouble("AutoFitRadius", o_radv)
        o_sph = gp.AddOptionToggle("SpherePreview", ric.OptionToggle(drw.sph_on, "Off", "On"))
        o_scale = gp.AddOption("ScaleByCurve")
        if locked:
            o_reset = gp.AddOption("ResetFine")
        else:
            o_sym = add_sym_option(gp)
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = scan_get(gp)                              # see CLICK_UP, CLICK_PLANE
        if res == Rhino.Input.GetResult.Option:
            oi = gp.Option().Index
            if oi == o_scale:                           # = Enter (Center done), then MeshScale
                st["then_scale"] = True
                if st["left"] is not None:
                    print("  (the unpaired LEFT tag is dropped)"); st["left"] = None
                break
            if oi == o_cancel:
                _apply(insts, applied, rg.Transform.Identity); sc.doc.Views.Redraw()
                print("[Center] cancelled - scans/meshes back where they were"); return
            if oi == o_fine:
                if st["left"] is not None:
                    print("  (the unpaired LEFT tag is dropped - pairs are locked after Fine)"); st["left"] = None
                _fine(insts, st["axis"], drw, st, commit, print_devs, pairs, world)
                continue
            if oi == o_auto:
                if st["left"] is not None:
                    print("  (the unpaired LEFT tag is dropped - pairs are locked after AutoFit)"); st["left"] = None
                run_autofit(insts, st, commit, print_devs, tag_centres())
                continue
            if oi == o_rad:
                opt_set("fit_radius", o_radv.CurrentValue)
                drw.sph_until = time.time() + SPHERE_FLASH; sc.doc.Views.Redraw()
                continue
            if oi == o_sph:
                drw.sph_on = not drw.sph_on; sc.doc.Views.Redraw()
                continue
            if oi == o_reset:
                st["F"] = rg.Transform.Identity; commit(); sc.doc.Views.Redraw()
                print("  Fine / AutoFit undone - back to the result of the pairs; pairs unlocked")
                print_devs(short=True)
                continue
            if oi == o_sym:
                st["axis"] = set_sym_from_option(gp); drw.axis = st["axis"]
                st["T"] = solve(); commit(); print_devs(); sc.doc.Views.Redraw()
                continue
            if oi == o_undo:
                if st["left"] is not None: st["left"] = None
                elif pairs: pairs.pop()
                st["T"] = solve(); commit(); print_devs()
            if oi == o_mir:
                drw.show_mirror = not drw.show_mirror; st["mir_user"] = True
                print("  mirror preview {}".format("ON" if drw.show_mirror else "off"))
            sc.doc.Views.Redraw()
            continue
        if is_click(res):
            if locked:
                print("  pairs are locked after Fine / AutoFit (a new pair would throw that work away). "
                      "Fine / AutoFit again, Enter = done, or ResetFine = back to the pairs.")
                continue
            hit = pick_on_scans(gp, insts)
            if hit is None:
                print("  click ON a visible scan/mesh"); continue
            ok, inv = total().TryGetInverse()
            pb = rg.Point3d(hit[0]); pb.Transform(inv)       # back to base coordinates
            st.setdefault("picked", set()).add(str(hit[1].Id))
            if st["left"] is None:
                st["left"] = pb
            else:
                pairs.append((st["left"], pb)); st["left"] = None
                st["T"] = solve(); commit()
                if len(pairs) == 1 and not st["mir_user"] and not drw.show_mirror:
                    drw.show_mirror = True
                    print("  mirror preview ON (blue points = the mirrored scans/meshes; MirrorPreview = off)")
                print("  pair {}: scans/meshes turned {:.2f} deg and moved onto the symmetry plane".format(len(pairs), st["tilt"]))
                print_devs()
                if st["tilt"] > 15.0:
                    print("  [WARN] that is a big turn: are LEFT/RIGHT really across the car and is MirrorPlane "
                          "right? (UndoPair / Cancel)")
            sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Cancel:          # Esc = cancel, like the Cancel option
            _apply(insts, applied, rg.Transform.Identity); sc.doc.Views.Redraw()
            print("[Center] cancelled (Esc) - scans/meshes back where they were"); return
        break
    if pairs:
        print_devs()
    changed = bool(pairs) or not _xf_is_identity(st["F"])
    if changed:
        fr_ = frame_get()
        picked = list(st.get("picked") or [str(o.Id) for o in insts])
        comps = []
        for pid in picked:
            if not any(pid in c_ for c_ in comps): comps.append(align_group([pid]))
        if len(comps) > 1:
            print("  [WARN] the Center clicks are on scans/meshes NOT aligned to each other - the symmetry plane "
                  "mixes them and fits none exactly. Align them first (or click on one structure only).")
        frame_part_set(fr_, "center", picked, {"axis": st["axis"]})
        frame_set(fr_)
        relevel_after_center(insts, st["axis"])         # Floor done before: the floor is level again
    print("[Center] done ({} pair(s){})".format(len(pairs), "" if _xf_is_identity(st["F"]) else " + Fine"))
    if changed and not st.get("then_scale"):
        save_setup_prompt(insts, "Center")
    if st.get("then_scale"):
        remember_positions()                        # Center's own moves are not hand moves
        _scan_scale_raw()


# ---- AutoFit: the mirror fitted to the scan itself (ICP) --------------------
AUTOFIT_SAMPLES = 3000           # scan points used (whole scan)
AUTOFIT_PER_TAG = 300            # scan points used per tag sphere
AUTOFIT_ITERS = 12


def autofit_radius():
    """Sphere radius around every tag for AutoFit (model units); 0 = whole mesh."""
    return float(opt_get("fit_radius", 50.0 / _mm_per_unit()))


def _scan_closest(geos, q):
    """Nearest point of the visible meshes to world point q -> (point, normal) or None."""
    best = None
    for (mesh, xf, inv) in geos:
        lq = rg.Point3d(q); lq.Transform(inv)
        mp = mesh.ClosestMeshPoint(lq, 0.0)
        if mp is None: continue
        c = rg.Point3d(mp.Point); c.Transform(xf)
        d = c.DistanceTo(q)                        # in the world: blocks may have different sizes
        if best is None or d < best[0]:
            n = rg.Vector3d(mesh.FaceNormals[mp.FaceIndex]); n.Transform(xf); n.Unitize()
            best = (d, c, n)
    return None if best is None else (best[1], best[2])


def p2plane_step(keep, scale=False):
    """One point-to-plane ICP step: the small rigid move that brings points q
    onto the tangent planes (c, n) of their closest mesh points.
    keep: [(q, c, n, d)] -> (transform, turn angle, shift length).
    Solves ((q - cen) x n).w + n.t = -(q - c).n in least squares (w turn, t shift).
    scale: also a size change ds about cen (+ ds (q - cen).n) - Align Scale=On."""
    if scale:
        return _p2plane_step_scaled(keep)
    cx = sum(r[0].X for r in keep) / len(keep); cy = sum(r[0].Y for r in keep) / len(keep)
    cz = sum(r[0].Z for r in keep) / len(keep)
    cen = rg.Point3d(cx, cy, cz)
    G = [[0.0] * 6 for _ in range(6)]; B = [[0.0] for _ in range(6)]
    for (q, c, n, d) in keep:
        qc = q - cen
        cr = rg.Vector3d.CrossProduct(qc, n)
        row = (cr.X, cr.Y, cr.Z, n.X, n.Y, n.Z)
        rhs = -((q - c) * n)
        for i in range(6):
            B[i][0] += row[i] * rhs
            for j in range(6):
                G[i][j] += row[i] * row[j]
    for i in range(6): G[i][i] += 1e-9 * (1.0 + G[i][i])
    x = [r_[0] for r_ in gauss_solve(G, B)]
    w = rg.Vector3d(x[0], x[1], x[2]); t = rg.Vector3d(x[3], x[4], x[5])
    D = rg.Transform.Translation(t)
    if w.Length > 1e-15:
        D = D * rg.Transform.Rotation(w.Length, w, cen)
    return D, w.Length, t.Length


def _p2plane_step_scaled(keep):
    cx = sum(r[0].X for r in keep) / len(keep); cy = sum(r[0].Y for r in keep) / len(keep)
    cz = sum(r[0].Z for r in keep) / len(keep)
    cen = rg.Point3d(cx, cy, cz)
    G = [[0.0] * 7 for _ in range(7)]; B = [[0.0] for _ in range(7)]
    for (q, c, n, d) in keep:
        qc = q - cen
        cr = rg.Vector3d.CrossProduct(qc, n)
        row = (cr.X, cr.Y, cr.Z, n.X, n.Y, n.Z, qc * n)
        rhs = -((q - c) * n)
        for i in range(7):
            B[i][0] += row[i] * rhs
            for j in range(7):
                G[i][j] += row[i] * row[j]
    for i in range(7): G[i][i] += 1e-9 * (1.0 + G[i][i])
    x = [r_[0] for r_ in gauss_solve(G, B)]
    w = rg.Vector3d(x[0], x[1], x[2]); t = rg.Vector3d(x[3], x[4], x[5]); ds = x[6]
    D = rg.Transform.Translation(t)
    if w.Length > 1e-15:
        D = D * rg.Transform.Rotation(w.Length, w, cen)
    D = D * rg.Transform.Scale(cen, 1.0 + ds)
    return D, w.Length + abs(ds), t.Length


def best_similar(src, dst):
    """Rotation + move + ONE size factor (Umeyama): the rotation is the rigid
    one (it does not depend on the size), then s = sum (b-cb).R(a-ca) / sum |a-ca|^2."""
    R, t = best_rigid(src, dst)
    n = float(len(src))
    ca = [sum(p[i] for p in src) / n for i in range(3)]
    cb = [sum(p[i] for p in dst) / n for i in range(3)]
    num = 0.0; den = 0.0
    for a, b in zip(src, dst):
        ra = [sum(R[i][j] * (a[j] - ca[j]) for j in range(3)) for i in range(3)]
        num += sum((b[i] - cb[i]) * ra[i] for i in range(3))
        den += sum((a[i] - ca[i]) ** 2 for i in range(3))
    sc_ = num / den if den > 1e-18 else 1.0
    Rs = [[sc_ * R[i][j] for j in range(3)] for i in range(3)]
    ts = [cb[i] - sum(Rs[i][j] * ca[j] for j in range(3)) for i in range(3)]
    return Rs, ts, sc_


def xf_scale(X):
    """The uniform size factor in a transform (cube root of the 3x3 determinant)."""
    d = (X[0, 0] * (X[1, 1] * X[2, 2] - X[1, 2] * X[2, 1]) - X[0, 1] * (X[1, 0] * X[2, 2] - X[1, 2] * X[2, 0]) +
         X[0, 2] * (X[1, 0] * X[2, 1] - X[1, 1] * X[2, 0]))
    return math.copysign(abs(d) ** (1.0 / 3.0), d)


def autofit_mirror(insts, axis, centres=None, radius=0.0):
    """Find the mesh's OWN symmetry plane: mesh points are mirrored in the world
    plane and pulled onto the mesh (point-to-plane ICP); points that land far
    from it (holes, parts scanned on one side only, asymmetric details) are
    left out. -> (transform that puts that plane onto the world plane,
    report text) or (None, reason).
    centres + radius: only mesh points inside spheres around the tags (the
    places you marked as symmetric) - fast on any mesh size and nothing
    asymmetric gets in."""
    geos = []; pts = []
    spheres = bool(centres) and radius > 0
    for o0 in insts:
        o = sc.doc.Objects.FindId(o0.Id)
        if o is None or not scan_visible(o): continue
        geo = scan_geo(o); cl = scan_cloud(o, True)
        if geo is None or cl is None: continue
        ok, inv = geo[1].TryGetInverse()
        if not ok: continue
        geos.append((geo[0], geo[1], inv))
        lp = cl.GetPoints()
        if spheres:
            r2 = (radius / abs(xf_scale(geo[1]) or 1.0)) ** 2     # the sphere in the block (a scaled block too)
            for c in centres:                      # tag in the scan's own coordinates
                lc = rg.Point3d(c); lc.Transform(inv)
                near = [q for q in lp if (q.X - lc.X) ** 2 + (q.Y - lc.Y) ** 2 + (q.Z - lc.Z) ** 2 <= r2]
                step = max(1, len(near) // AUTOFIT_PER_TAG)
                for q0 in near[::step]:
                    q = rg.Point3d(q0); q.Transform(geo[1]); pts.append(q)
            continue
        step = max(1, len(lp) // max(1, AUTOFIT_SAMPLES // max(1, len(insts))))
        for i in range(0, len(lp), step):
            q = rg.Point3d(lp[i]); q.Transform(geo[1]); pts.append(q)
    if len(pts) < 50 or not geos:
        return None, ("too few scan/mesh points inside the tag spheres - larger AutoFitRadius?" if spheres
                      else "too few scan/mesh points (are the scans/meshes visible?)")
    nrm = [0.0, 0.0, 0.0]; nrm[axis] = 1.0
    E = rg.Vector3d(nrm[0], nrm[1], nrm[2])
    M = rg.Transform.Mirror(rg.Point3d(0, 0, 0), E)
    base = []
    for p in pts:
        q = rg.Point3d(p); q.Transform(M); base.append(q)
    A = rg.Transform.Identity
    first = last = None; used = 0
    for it in range(AUTOFIT_ITERS):
        check_esc()
        rows = []
        for q0 in base:
            q = rg.Point3d(q0); q.Transform(A)
            hit = _scan_closest(geos, q)
            if hit is None: continue
            c, n = hit
            rows.append((q, c, n, q.DistanceTo(c)))
        if len(rows) < 30:
            return None, "the mirrored scan/mesh does not land on the scan/mesh (centre the pairs first)"
        med = median([r[3] for r in rows])
        keep = [r for r in rows if r[3] <= max(3.0 * med, 1e-6)]
        used = len(keep)
        if first is None: first = med
        last = med
        D, wl, tl = p2plane_step(keep)
        A = D * A
        if wl < 1e-8 and tl < 1e-6 * max(1.0, med): break
    # A * M = a reflection in the scan's own symmetry plane -> that plane
    H = A * M
    L = [[H[i, j] for j in range(3)] for i in range(3)]
    Mn = [[(1.0 if i == j else 0.0) * 0.5 - 0.5 * L[i][j] for j in range(3)] for i in range(3)]   # = n n^T
    col = max(range(3), key=lambda j: sum(Mn[i][j] ** 2 for i in range(3)))
    n = rg.Vector3d(Mn[0][col], Mn[1][col], Mn[2][col]); n.Unitize()
    if n * E < 0: n = -n
    tv = rg.Vector3d(H[0, 3], H[1, 3], H[2, 3])
    o = rg.Point3d(0, 0, 0) + n * (0.5 * (tv * n))           # a point on that plane
    rot = rg.Transform.Rotation(n, E, o)
    oo = rg.Point3d(o); oo.Transform(rot)
    move = rg.Transform.Translation(-E * (oo.X * E.X + oo.Y * E.Y + oo.Z * E.Z)) * rot
    ang = math.degrees(math.acos(max(-1.0, min(1.0, n * E))))
    txt = ("[{}] ".format("spheres r={:g} around {} tags".format(radius, len(centres)) if spheres else "whole scan/mesh") +
           "mirror-to-scan/mesh distance (median) {} -> {}, {} of {} points used; "
           "scans/meshes turned {:.4f} deg, centre moved {}".format(fl(first), fl(last), used, len(base), ang,
                                                                  fl(abs(o.X * E.X + o.Y * E.Y + o.Z * E.Z))))
    return move, txt


# ---- Fine: a small gumball in WORLD axes (move / turn only) -----------------
_AXES = (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 1, 0), rg.Vector3d(0, 0, 1)) if IN_RHINO else ()


def _fine_pivot(insts, axis, pairs, world):
    """Turning point: the middle of the tags, put ON the symmetry plane; no
    tags: the middle of the visible meshes."""
    if pairs:
        c = rg.Point3d(0, 0, 0)
        for (L_, R_) in pairs:
            c += (world(L_) + world(R_)) * 0.5
        c = c / float(len(pairs))
        q = [c.X, c.Y, c.Z]; q[axis] = 0.0
        return rg.Point3d(q[0], q[1], q[2])
    bb = rg.BoundingBox.Empty
    for o0 in insts:
        o = sc.doc.Objects.FindId(o0.Id)
        if o is None or not scan_visible(o): continue
        geo = scan_geo(o)
        if geo: bb.Union(geo[0].GetBoundingBox(geo[1]))
    return bb.Center if bb.IsValid else rg.Point3d(0, 0, 0)


FINE_CORNER = (150, 150)         # gumball centre: this many pixels from the RIGHT and BOTTOM edge of the view


def _fine_frame(vp, P):
    """The gumball is pinned to the bottom right corner of the view and turns
    with the view (its arms are the world axes as seen now). Its size is
    taken at the view target (always in front of the camera).
    -> {m, Lw (arm length in model units), ref, o (ref on screen), a (anchor)}."""
    m = xf_tuple(vp)
    ref = rg.Point3d(vp.CameraTarget)
    x0, y0 = to_screen(m, T(ref))
    r = rg.Vector3d(vp.CameraX); r.Unitize()
    x1, y1 = to_screen(m, T(ref + r))
    ppu = max(math.hypot(x1 - x0, y1 - y0), 1e-9)          # pixels per model unit
    try:
        W, Hh = vp.Size.Width, vp.Size.Height
    except Exception:
        W, Hh = 1200, 800
    return {"m": m, "Lw": FINE_ARROW_PX / ppu, "ref": ref, "o": (x0, y0),
            "a": (W - FINE_CORNER[0], Hh - FINE_CORNER[1])}


def _fs(fr, v):
    """Screen position of the gumball point at world offset v (from its centre)."""
    x, y = to_screen(fr["m"], T(fr["ref"] + v))
    return fr["a"][0] + x - fr["o"][0], fr["a"][1] + y - fr["o"][1]


def _ring_vecs(k, R, n=48):
    """Offsets of a circle about world axis k (0 X, 1 Y, 2 Z), radius R."""
    a = _AXES[k]; u = _AXES[(k + 1) % 3]
    v = rg.Vector3d.CrossProduct(a, u)
    return [u * (R * math.cos(2 * math.pi * i / n)) + v * (R * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]


def _seg_dist(sx, sy, p0, p1):
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0; l2 = dx * dx + dy * dy
    f = 0.0 if l2 < 1e-9 else max(0.0, min(1.0, ((sx - x0) * dx + (sy - y0) * dy) / l2))
    return math.hypot(x0 + f * dx - sx, y0 + f * dy - sy), f


def _fine_handle_at(vp, P, sx, sy):
    """('t', axis) arrow, ('r', axis, offset on the ring) ring, or None."""
    fr = _fine_frame(vp, P); Lw = fr["Lw"]
    best = None
    for k, a in enumerate(_AXES):
        dd, f = _seg_dist(sx, sy, _fs(fr, a * (0.15 * Lw)), _fs(fr, a * Lw))
        if dd < 8 and (best is None or dd < best[0]): best = (dd, ("t", k))
    if best is not None: return best[1]
    for k in range(3):
        vs = _ring_vecs(k, FINE_RING * Lw)
        scr = [_fs(fr, v) for v in vs]
        for i in range(len(scr) - 1):
            dd, f = _seg_dist(sx, sy, scr[i], scr[i + 1])
            if dd < 7 and (best is None or dd < best[0]): best = (dd, ("r", k, vs[i] + (vs[i + 1] - vs[i]) * f))
    return None if best is None else best[1]


def _fine_draw(d, vp, P, hot):
    fr = _fine_frame(vp, P); Lw = fr["Lw"]
    k_ = sd.Color.FromArgb(20, 20, 20)
    ax, ay = fr["a"]
    for k in range(3):
        on = hot and hot[0] == "r" and hot[1] == k
        c = sd.Color.FromArgb(255, 215, 0) if on else sd.Color.FromArgb(*AX_COL[k])
        scr = [_fs(fr, v) for v in _ring_vecs(k, FINE_RING * Lw)]
        for i in range(len(scr) - 1):
            d.Draw2dLine(_pf(*scr[i]), _pf(*scr[i + 1]), c, 3.0 if on else 1.6)
    for k, a in enumerate(_AXES):
        on = hot and hot[0] == "t" and hot[1] == k
        c = sd.Color.FromArgb(255, 215, 0) if on else sd.Color.FromArgb(*AX_COL[k])
        x0, y0 = _fs(fr, a * 0.0); x1, y1 = _fs(fr, a * Lw)
        d.Draw2dLine(_pf(x0, y0), _pf(x1, y1), k_, 5.0)
        d.Draw2dLine(_pf(x0, y0), _pf(x1, y1), c, 3.0)
        L = math.hypot(x1 - x0, y1 - y0)
        if L > 12:                                       # arrow head + axis letter
            ux, uy = (x1 - x0) / L, (y1 - y0) / L
            for s_ in (1, -1):
                d.Draw2dLine(_pf(x1, y1), _pf(x1 - 10 * ux + 5 * s_ * uy, y1 - 10 * uy - 5 * s_ * ux), c, 3.0)
            try: d.Draw2dText("XYZ"[k], c, rg.Point2d(x1 + 9 * ux, y1 + 9 * uy), True, 13)
            except Exception: pass
    # where it really turns: the pivot ON the symmetry plane
    px, py = to_screen(fr["m"], T(P))
    for (a_, b_) in (((px - 7, py), (px + 7, py)), ((px, py - 7), (px, py + 7))):
        d.Draw2dLine(_pf(*a_), _pf(*b_), k_, 3.0)
        d.Draw2dLine(_pf(*a_), _pf(*b_), sd.Color.FromArgb(255, 215, 0), 1.5)


def _fine_xform(dr, sx, sy, mv, dg):
    """Mouse drag (pixels from the start) -> the small move / turn."""
    fr, P, h = dr["fr"], dr["P"], dr["h"]; Lw = fr["Lw"]
    dx, dy = sx - dr["x0"], sy - dr["y0"]
    a = _AXES[h[1]]
    if h[0] == "t":
        x0, y0 = _fs(fr, a * 0.0); x1, y1 = _fs(fr, a * Lw)
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 3:                                        # arrow points at you: use screen up
            ux, uy = 0.0, -1.0
        else:
            ux, uy = (x1 - x0) / L, (y1 - y0) / L
        px = dx * ux + dy * uy
        return rg.Transform.Translation(a * (px * mv)), px * mv
    qv = h[2]
    t3 = rg.Vector3d.CrossProduct(a, qv); t3.Unitize()
    x0, y0 = _fs(fr, qv); x1, y1 = _fs(fr, qv + t3 * (0.2 * Lw))
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1e-6: return rg.Transform.Identity, 0.0
    px = dx * (x1 - x0) / L + dy * (y1 - y0) / L
    ang = px * dg
    return rg.Transform.Rotation(math.radians(ang), a, P), ang


def _fine(insts, axis, drw, st, commit, print_devs, pairs, world, fit_fn=None, pivot=None):
    """Fine: drag an ARROW = move along that world axis, a RING = turn about
    that world axis, around a point on the symmetry plane. Every pixel of the
    drag is MovePerPx model units / DegPerPx degrees - very small steps.
    All chosen meshes move, also the hidden ones. Enter / Back = keep,
    Esc = undo the Fine moves of this round. fit_fn: the tool's own Fit
    (Align); without it AutoFit is the mirror fit of Center."""
    F0 = st["F"]
    st["fine"] = True                            # tags: r = L mirrored (with distance), R grey
    P = pivot if pivot is not None else _fine_pivot(insts, axis, pairs, world)
    H = {"hot": None, "drag": None, "last": None, "t": 0.0}
    names = "XYZ"

    def extra(d, vp, m):
        _fine_draw(d, vp, P, H["drag"]["h"] if H["drag"] else H["hot"])
    drw.extra = extra

    def on_down(sender, ev):
        try:
            if not ev.LeftButtonDown: return
            vp = ev.Viewport; sx, sy = ev.WindowPoint.X, ev.WindowPoint.Y
            h = _fine_handle_at(vp, P, sx, sy)
            if h is None: return
            H["drag"] = {"h": h, "x0": sx, "y0": sy, "F": st["F"], "fr": _fine_frame(vp, P), "P": P}
            H["last"] = (sx, sy)
        except Exception:
            H["drag"] = None

    def on_move(sender, ev):
        try:
            sx, sy = ev.WindowPoint.X, ev.WindowPoint.Y
            dr = H["drag"]
            if dr is None:
                hot = _fine_handle_at(ev.Viewport, P, sx, sy)
                if hot != H["hot"]:
                    H["hot"] = hot; ev.View.Redraw()          # the handle under the mouse lights up
                return
            H["last"] = (sx, sy)
            now = time.time()
            if now - H["t"] < 0.06: return               # live, but not on every pixel
            H["t"] = now
            D, _ = _fine_xform(dr, sx, sy, fine_opt("mv"), fine_opt("dg"))
            st["F"] = D * dr["F"]; commit()
            ev.View.Redraw()
        except Exception:
            pass

    print("[Fine] drag an ARROW = move along X / Y / Z, a RING = turn about it (red X, green Y, blue Z). "
          "Steps per pixel: MovePerPx / DegPerPx. Enter / Back = keep, Esc = undo this Fine round (back to the tool).")
    try:
        while True:
            gp = ric.GetPoint()
            gp.SetCommandPrompt("Fine: drag an arrow (move) or a ring (turn)  - Enter = keep, Esc = undo this Fine round")
            gp.AcceptNothing(True)
            o_mv = ric.OptionDouble(fine_opt("mv"), 1e-6, 1e3); gp.AddOptionDouble("MovePerPx", o_mv)
            o_dg = ric.OptionDouble(fine_opt("dg"), 1e-6, 10.0); gp.AddOptionDouble("DegPerPx", o_dg)
            o_reset = gp.AddOption("ResetFine"); o_back = gp.AddOption("Back")
            o_mir = -99
            if fit_fn is None:
                o_mir = gp.AddOption("MirrorPreview")
            o_auto = gp.AddOption("AutoFit" if fit_fn is None else "Fit")
            o_radv = ric.OptionDouble(autofit_radius(), 0.0, 1e6); o_rad = gp.AddOptionDouble("AutoFitRadius", o_radv)
            o_sph = gp.AddOptionToggle("SpherePreview", ric.OptionToggle(drw.sph_on, "Off", "On"))
            gp.MouseDown += on_down; gp.MouseMove += on_move
            gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0); gp.PermitObjectSnap(False)
            view_constrain(gp)                           # plane facing the camera, made fresh every click
            H["drag"] = None
            res = tracked_get(gp)                        # mouse button UP (the drag needs the normal get)
            if is_click(res):
                dr = H["drag"]; H["drag"] = None
                if dr is None:
                    continue                             # a click beside the gumball
                sx, sy = H["last"] if H["last"] else (dr["x0"], dr["y0"])
                D, amount = _fine_xform(dr, sx, sy, fine_opt("mv"), fine_opt("dg"))
                st["F"] = D * dr["F"]; commit(); sc.doc.Views.Redraw()
                if dr["h"][0] == "t":
                    print("  moved {}{} along {}".format("+" if amount >= 0 else "", fl(amount), names[dr["h"][1]]))
                else:
                    print("  turned {:+.4f} deg about {}".format(amount, names[dr["h"][1]]))
                print_devs(short=True)
                continue
            if res == Rhino.Input.GetResult.Option:
                oi = gp.Option().Index
                opt_set("fine_mv", o_mv.CurrentValue); sc.sticky["M2CA_fine_dg"] = o_dg.CurrentValue
                if oi == o_reset:
                    st["F"] = rg.Transform.Identity; commit(); sc.doc.Views.Redraw()
                    print("  Fine moves reset (back to the result of the pairs)"); print_devs(short=True)
                elif oi == o_back:
                    break
                elif oi == o_mir:
                    drw.show_mirror = not drw.show_mirror; sc.doc.Views.Redraw()
                elif oi == o_auto:
                    if fit_fn is not None: fit_fn()
                    else: run_autofit(insts, st, commit, print_devs, st["tag_centres"]())
                elif oi == o_rad:
                    opt_set("fit_radius", o_radv.CurrentValue)
                    drw.sph_until = time.time() + SPHERE_FLASH; sc.doc.Views.Redraw()
                elif oi == o_sph:
                    drw.sph_on = not drw.sph_on; sc.doc.Views.Redraw()
                continue
            if res == Rhino.Input.GetResult.Cancel:
                st["F"] = F0; commit(); sc.doc.Views.Redraw()
                print("  Fine: this round undone"); print_devs(short=True)
                break
            break                                        # Enter
    finally:
        drw.extra = None
        H["drag"] = None
        st["fine"] = False


def run_autofit(insts, st, commit, print_devs, centres=None):
    busy = show_busy("AutoFit: fitting the mirror to the scan/mesh...", esc="stop AutoFit")
    try:
        move, txt = autofit_mirror(insts, st["axis"], centres, autofit_radius())
    except UserAbort:
        move, txt = None, "stopped by Esc"
    finally:
        hide_busy(busy)
    if move is None:
        print("  [AutoFit] nothing changed: " + txt); return
    st["F"] = move * st["F"]; commit(); sc.doc.Views.Redraw()
    print("  [AutoFit] " + txt)
    print_devs(short=True)


def fine_opt(key):
    if key == "mv":                                  # 0.02 mm per pixel, in model units
        return float(opt_get("fine_mv", 0.02 / _mm_per_unit()))
    return float(sc.sticky.get("M2CA_fine_dg", 0.005))


# ---- Align: scans to each other (a tree) ------------------------------------
ALIGN_KEY = "msh2classA_align_to"        # user text on a scan block: the id of the scan it was aligned to
ALIGN_FIT_ITERS = 40


def all_scan_insts():
    """Every mesh block: once meshes are aligned to each other they move as
    ONE structure (Center, Floor, Setup never break the alignment)."""
    return [o for (o, lp) in scan_instances()]


def align_parent_map():
    out = {}
    for o in all_scan_insts():
        try:
            p = o.Attributes.GetUserString(ALIGN_KEY)
        except Exception:
            p = None
        if p: out[str(o.Id)] = p
    return out


def align_branch(root_id):
    """The mesh root_id and every mesh aligned to it, directly or further down."""
    par = align_parent_map()
    out = {str(root_id)}
    grew = True
    while grew:
        grew = False
        for c, p in par.items():
            if p in out and c not in out:
                out.add(c); grew = True
    return out


def align_group(ids):
    """Every mesh connected to the meshes ids by Align (both ways: parents,
    children, siblings) - the meshes that really lie on each other."""
    par = align_parent_map()
    nb = {}
    for c, p in par.items():
        nb.setdefault(c, set()).add(p); nb.setdefault(p, set()).add(c)
    out = set(str(i) for i in ids)
    todo = list(out)
    while todo:
        k = todo.pop()
        for j in nb.get(k, ()):
            if j not in out:
                out.add(j); todo.append(j)
    return out


# ---- the CAR FRAME: what Center / Floor have set ---------------------------
# Kept in the .3dm (document text) AND in every saved setup ('_frame'):
#   members = the scan/mesh blocks Center / Floor were done on (and the ones
#             aligned to them) - Align never moves them onto a scan/mesh outside
#   center  = {axis}            Center done with this symmetry plane
#   floor   = {axis, pts, ref}  the Floor clicks, as (block, x, y, z) IN THE
#             BLOCK - so Center can level the floor again by itself: the order
#             Floor -> Center gives exactly the same as Center -> Floor
FRAME_KEY = "msh2classA_frame"


def frame_get():
    try:
        s = sc.doc.Strings.GetValue(FRAME_KEY)
        d = json.loads(s) if s else {}
    except Exception:
        d = {}
    return d if isinstance(d, dict) else {}


def frame_set(d):
    try:
        if d:
            sc.doc.Strings.SetString(FRAME_KEY, json.dumps(d, sort_keys=True))
        else:
            sc.doc.Strings.Delete(FRAME_KEY)
    except Exception:
        print("  [WARN] could not store the Center / Floor marks in this file")


FRAME_PARTS = ("center", "floor")


def _norm_frame(d):
    """The marks per tool: d['center'] / d['floor'] each with 'core' (the
    scans/meshes clicked on) and 'members' (those that really are centred /
    levelled now: the core and everything aligned to it). Older marks (one
    list for both) are read as both. d['members'] / d['core'] = the union."""
    if not isinstance(d, dict): return {}
    for k in FRAME_PARTS:
        p_ = d.get(k)
        if isinstance(p_, dict) and "members" not in p_:
            p_["members"] = list(d.get("members", []))
            p_["core"] = list(d.get("core", d.get("members", [])))
    _frame_union(d)
    return d


def _frame_union(d):
    mem = set(); core = set()
    for k in FRAME_PARTS:
        p_ = d.get(k)
        if isinstance(p_, dict):
            mem |= set(p_.get("members", [])); core |= set(p_.get("core", []))
    d["members"] = sorted(mem); d["core"] = sorted(core)


def frame_members():
    return set(_norm_frame(frame_get()).get("members", []))


def frame_core():
    """The scans/meshes Center / Floor were clicked on."""
    return set(_norm_frame(frame_get()).get("core", []))


def frame_part_set(d, part, objs_ids, extra):
    """Center / Floor just done with clicks on objs_ids: they are this part's
    core; they and every scan/mesh aligned to them are its members - and ONLY
    those (Center moves everything: whatever was centred before and is not
    aligned to these is not centred any more)."""
    _norm_frame(d)
    grp = align_group(objs_ids)
    ids = set(str(i) for i in objs_ids)
    p_ = dict(extra)
    p_["members"] = sorted(_scan_name(o) for o in all_scan_insts() if str(o.Id) in grp)
    p_["core"] = sorted(_scan_name(o) for o in all_scan_insts() if str(o.Id) in ids)
    d[part] = p_
    _frame_union(d)
    return d


def frame_refusal(d, moving, onto):
    """Moving the scans/meshes 'moving' onto 'onto' (they stay) would lose a
    centre / floor? -> (name, why) or None."""
    d = _norm_frame(d)
    moving = set(moving); onto = set(onto)
    for k, what in (("center", "centred by Center"), ("floor", "levelled by Floor")):
        p_ = d.get(k)
        if not isinstance(p_, dict): continue
        mem = set(p_.get("members", [])); core = set(p_.get("core", []))
        if (moving & core) and not (onto & core):
            return sorted(moving & core)[0], "holds the {} (MeshCenter / MeshFloor were clicked on it)".format(
                "centre" if k == "center" else "floor")
        if (moving & mem) and not (onto & mem):
            return sorted(moving & mem)[0], "is " + what
    return None


def frame_moved(d, moved, onto):
    """moved now lies exactly on onto: per part it is centred / levelled
    exactly when onto is. -> the names that joined a part."""
    d = _norm_frame(d)
    joined = []
    moved = set(moved); onto = set(onto)
    for k in FRAME_PARTS:
        p_ = d.get(k)
        if not isinstance(p_, dict): continue
        mem = set(p_.get("members", []))
        if onto & mem:
            joined += sorted(moved - mem); mem |= moved
        else:
            mem -= moved
            p_["core"] = sorted(set(p_.get("core", [])) - moved)
        p_["members"] = sorted(mem)
    _frame_union(d)
    return sorted(set(joined))


def _block_by_name():
    return dict((_scan_name(o), o) for o in all_scan_insts())


def to_block(pt, obj):
    """World point -> [block name, x, y, z in the block, in MM] (mm: the same
    in an inch and a mm document, the scan/mesh file is the same)."""
    ok, inv = obj.InstanceXform.TryGetInverse()
    q = rg.Point3d(pt)
    if ok: q.Transform(inv)
    k = _mm_per_unit()
    return [_scan_name(obj), q.X * k, q.Y * k, q.Z * k]


def from_block(rec, blocks):
    """[block name, x, y, z mm] -> world point now (None: block not loaded)."""
    o = blocks.get(rec[0])
    if o is None: return None
    k = 1.0 / _mm_per_unit()
    q = rg.Point3d(rec[1] * k, rec[2] * k, rec[3] * k); q.Transform(o.InstanceXform)
    return q


def floor_xf(pts, ref, axis, free=False):
    """The Floor move: turn ONLY about the axis across the symmetry plane
    (so the centre is never touched), floor plane to 0, optional point ref
    to front/back 0. free (no centre yet - nothing to keep): the floor plane
    is turned flat whatever way it leans (Center later turns only about the
    vertical... and levels again by itself).
    -> (transform, tilt deg, sideways lean deg) or (None, why)."""
    dmax = max(a.DistanceTo(b) for a in pts for b in pts)
    amax = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            for k in range(j + 1, len(pts)):
                amax = max(amax, rg.Vector3d.CrossProduct(pts[j] - pts[i], pts[k] - pts[i]).Length)
    if dmax < 1e-9 or amax < 0.05 * dmax * dmax:
        return None, ("the points lie almost on ONE line - click the floor on BOTH sides of the car "
                      "(left and right sill, front and rear)")
    fit = rg.Plane.FitPlaneToPoints(pts)
    plane = fit[1] if isinstance(fit, tuple) else fit
    m = plane.Normal
    up = rg.Vector3d(0, 0, 1)
    if m * up < 0: m = -m
    e = [0.0, 0.0, 0.0]; e[axis] = 1.0; E = rg.Vector3d(e[0], e[1], e[2])
    g = plane.Origin
    if free:
        ang = math.degrees(math.acos(max(-1.0, min(1.0, m * up))))
        if m * up < 0.5:
            return None, "the clicked points do not look like a floor (plane {:.0f} deg off level)".format(ang)
        rot = rg.Transform.Rotation(m, up, g)
        roll = 0.0
        ang = math.radians(ang)
    else:
        mp = m - E * (m * E)                     # the part a turn about E can fix
        roll = math.degrees(math.asin(max(-1.0, min(1.0, m * E))))
        if mp.Length < 0.5:                      # floor plane more than 60 deg off level
            return None, ("the clicked points do not look like a floor (plane {:.0f} deg off level) - check the "
                          "points / SymPlane".format(math.degrees(math.acos(min(1.0, mp.Length)))))
        mp.Unitize()
        ang = math.atan2((rg.Vector3d.CrossProduct(mp, up)) * E, mp * up)
        rot = rg.Transform.Rotation(ang, E, g)
    g2 = rg.Point3d(g); g2.Transform(rot)
    T = rg.Transform.Translation(-up * g2.Z) * rot
    fwd = rg.Vector3d.CrossProduct(up, E); fwd.Unitize()
    if ref is not None:
        r2 = rg.Point3d(ref); r2.Transform(T)
        T = rg.Transform.Translation(-fwd * (r2.X * fwd.X + r2.Y * fwd.Y + r2.Z * fwd.Z)) * T
    return (T, math.degrees(ang), roll), None


def relevel_after_center(insts, axis):
    """Floor was done before this Center: level the floor again with the SAME
    Floor clicks (they move with their scan/mesh). Turns only about the axis
    across the new symmetry plane - the centre just found stays exact."""
    d = frame_get()
    _norm_frame(d)
    fl_ = d.get("floor")
    if not fl_: return
    if axis not in (0, 1):
        print("  [WARN] SymPlane XY: the floor cannot be kept - MeshFloor again (with SymPlane YZ or XZ)")
        d.pop("floor", None); _frame_union(d); frame_set(d); return
    blocks = _block_by_name()
    pts = [from_block(r, blocks) for r in fl_.get("pts", [])]
    ref = from_block(fl_["ref"], blocks) if fl_.get("ref") else None
    if any(p is None for p in pts) or len(pts) < 3 or (fl_.get("ref") and ref is None):
        print("  [WARN] the scan/mesh of the Floor points is not loaded - the floor is NOT level any more: "
              "MeshFloor again")
        d.pop("floor", None); _frame_union(d); frame_set(d); return
    cmem = set((d.get("center") or {}).get("members", []))
    fnames = set(r[0] for r in fl_.get("pts", [])) | (set([fl_["ref"][0]]) if fl_.get("ref") else set())
    if not fnames <= cmem:
        print("  [WARN] the Floor points are on {} - NOT centred by this Center (not aligned to the scan/mesh you "
              "clicked): the floor is NOT level any more - MeshFloor again".format(", ".join(sorted(fnames - cmem))))
        d.pop("floor", None); _frame_union(d); frame_set(d); return
    res, why = floor_xf(pts, ref, axis)
    if res is None:
        print("  [WARN] floor not levelled again ({}) - MeshFloor again".format(why))
        d.pop("floor", None); _frame_union(d); frame_set(d); return
    T, ang, roll = res
    for obj in insts:
        sc.doc.Objects.Transform(obj.Id, T, True)
    fl_["axis"] = axis
    ids = [str(blocks[r[0]].Id) for r in fl_.get("pts", [])] + ([str(blocks[fl_["ref"][0]].Id)] if fl_.get("ref") else [])
    frame_part_set(d, "floor", ids, dict((k, v) for k, v in fl_.items() if k not in ("members", "core")))
    frame_set(d)
    sc.doc.Views.Redraw()
    print("[Floor] kept: levelled again with the earlier Floor points ({:.3f} deg about the axis across the "
          "symmetry plane - the centre is not touched){}".format(ang, "" if not fl_.get("ref") else
                                                                ", front/back 0 kept"))


def _xf_from_rigid(R, t):
    X = rg.Transform(1.0)
    for i in range(3):
        for j in range(3):
            X[i, j] = R[i][j]
        X[i, 3] = t[i]
    return X


def _update_setups_after_align(moved_ids, onto=()):
    """Every saved setup keeps the alignment: the moved meshes get, in each
    setup, the place they now have relative to the mesh they were aligned
    ONTO (onto: the names that stayed). Not relative to any other mesh: that
    one may have moved against the 'onto' one since the setup was saved, and
    the moved mesh would land off in that setup. Per setup the moved ones
    are centred / levelled there exactly when the 'onto' one is."""
    data = _read_setups()
    if not data: return
    cur = dict((str(o.Id), o) for o in all_scan_insts())
    moved_names = [_scan_name(o) for sid, o in cur.items() if sid in moved_ids]
    onto = list(onto)
    order = sorted(cur.items(), key=lambda kv: (_scan_name(kv[1]) not in onto,
                                                onto.index(_scan_name(kv[1])) if _scan_name(kv[1]) in onto else 0))
    changed = False
    for name, S in data.items():
        if name.startswith("_"): continue
        ref = None; ref_name = None
        for sid, o in order:
            if sid in moved_ids: continue
            rec = _setup_record(S, o)
            if rec:
                ok, inv = o.InstanceXform.TryGetInverse()
                if ok:
                    ref = xf_from_setup(rec["matrix_row_major"], S) * inv; ref_name = _scan_name(o); break
        if ref is None: continue
        fr_ = S.get("_frame")
        if isinstance(fr_, dict):
            frame_moved(fr_, moved_names, [ref_name] if ref_name in onto else [])
        for sid in moved_ids:
            o = cur.get(sid)
            if o is None: continue
            x = ref * o.InstanceXform
            rec = dict(_setup_record(S, o) or {})       # keep what the record had (layer, sha256, ... of other programs)
            rec.update({"matrix_row_major": xf_to_setup(x, S),
                        "block": o.InstanceDefinition.Name, "file": os.path.basename(obj_src(o))})
            rec.setdefault("layer", "")
            S[_scan_name(o)] = rec
            changed = True
    if changed:
        _write_setups(data)


def scan_align():
    """Align: click a point on the mesh that STAYS, then the same point on the
    mesh that MOVES - at least 3 such pairs. The moving mesh (and every mesh
    aligned to it before) jumps onto the other one after each pair. Fit =
    automatic on the overlap, Fine = gumball. Enter = done, Esc = cancel."""
    single_view()
    insts = all_scan_insts()
    if len(insts) < 2:
        print("[Align] needs two scans/meshes at least"); return
    prepare_scans(insts, "Align")
    mir_axis = pause_scanmirror()
    try:
        _scan_align(insts)
    finally:
        _ScanDraw.off_all()                      # tags / mirror / spheres off
        resume_scanmirror(mir_axis)


ALIGN_STAY_COL = (230, 40, 40)     # the mesh that STAYS while aligning
ALIGN_MOVE_COL = (40, 190, 60)     # the mesh that MOVES (and the ones aligned to it before)


class _Paint(object):
    """Temporary object colours (restored at the end, whatever happens)."""
    def __init__(self):
        self.saved = {}

    def paint(self, objs, rgb):
        for o0 in objs:
            o = sc.doc.Objects.FindId(o0.Id)
            if o is None: continue
            if o.Id not in self.saved:
                self.saved[o.Id] = (o.Attributes.ColorSource, o.Attributes.ObjectColor)
            a = o.Attributes.Duplicate()
            a.ColorSource = ro.ObjectColorSource.ColorFromObject
            a.ObjectColor = sd.Color.FromArgb(*rgb)
            sc.doc.Objects.ModifyAttributes(o.Id, a, True)
        sc.doc.Views.Redraw()

    def restore(self, ids=None):
        for oid in list(self.saved.keys()):
            if ids is not None and oid not in ids: continue
            o = sc.doc.Objects.FindId(oid)
            if o is not None:
                src, col_ = self.saved[oid]
                a = o.Attributes.Duplicate(); a.ColorSource = src; a.ObjectColor = col_
                sc.doc.Objects.ModifyAttributes(oid, a, True)
            del self.saved[oid]
        sc.doc.Views.Redraw()


def _scan_align(insts):
    paint = _Paint()
    try:
        _scan_align_run(insts, paint)
    finally:
        paint.restore()                     # the meshes get their own colours back


def _scan_align_run(insts, paint):
    tag = "[Align]"
    print(tag + " pair = 2 clicks: 1st on the scan/mesh that STAYS (it turns RED), 2nd on the same place of the scan/mesh "
          "that MOVES (it turns GREEN). After every pair the GREEN scan/mesh jumps onto the RED one (its tag M lands on S).")
    applied = {}
    pairs = []                         # (point on the fixed scan - world, point on the moving scan - base)
    st = {"T": rg.Transform.Identity, "F": rg.Transform.Identity, "first": None,
          "fixed": None, "moving": None, "set": [], "ids": set()}
    drw = _ScanDraw(insts, None)
    drw.show_mirror = False

    def total():
        return st["F"] * st["T"]

    def world(pb):
        q = rg.Point3d(pb); q.Transform(total()); return q

    def solve():
        if not pairs: return rg.Transform.Identity
        src = [T(m_) for (f_, m_) in pairs]; dst = [T(f_) for (f_, m_) in pairs]
        if len(pairs) < 3:                                    # too few for a turn: only move
            n = float(len(pairs))
            d = tuple(sum(b[i] - a[i] for a, b in zip(src, dst)) / n for i in range(3))
            return rg.Transform.Translation(rg.Vector3d(d[0], d[1], d[2]))
        if st.get("scale"):
            Rs, ts, sc_ = best_similar(src, dst)
            return _xf_from_rigid(Rs, ts)
        R, t = best_rigid(src, dst)
        return _xf_from_rigid(R, t)

    def devs():
        return [world(m_).DistanceTo(f_) for (f_, m_) in pairs]

    def print_devs(short=False):
        if not pairs: return
        d = devs()
        print("  pairs off by: " + "  ".join("{} {}".format(k + 1, fl(x)) for k, x in enumerate(d)) +
              "   worst {}".format(fl(max(d))) +
              ("   SCALE x{:.5f}".format(xf_scale(total())) if st.get("scale") else ""))

    def commit():
        _apply(st["set"], applied, total())

    def pins():
        out = []
        for k, (f_, m_) in enumerate(pairs):
            out.append((f_, "S{}".format(k + 1), PIN_COL, True))
            out.append((world(m_), "M{}  {}".format(k + 1, fl(world(m_).DistanceTo(f_))), GHOST_COL, True))
        if st["first"] is not None:
            out.append((st["first"], "S{}".format(len(pairs) + 1), PIN_COL, True))
        return out, []
    drw.pins_fn = pins

    def centres():
        return [rg.Point3d(f_) for (f_, m_) in pairs]

    drw.sph_fn = lambda: (centres(), autofit_radius())

    def fit():
        """ICP only inside the spheres (AutoFitRadius) around the pairs, like
        AutoFit of Center: moving-mesh points there -> closest points of the
        mesh that stays. The rest of the meshes (extra material, free ends)
        is never used."""
        print("  Fit = the GREEN scan/mesh is pulled onto the RED one BY ITSELF, after your pairs placed it roughly: "
              "only where the two overlap inside the spheres (AutoFitRadius) around the pairs; your clicks need not "
              "be exact. Fit again = finer, Esc = stop (back to before this Fit)")
        fixed = sc.doc.Objects.FindId(st["fixed_obj"].Id) if st.get("fixed_obj") else None
        g = scan_geo(fixed) if fixed is not None else None
        if not g: return
        ok, ginv = g[1].TryGetInverse()
        if not ok: return
        geos = [(g[0], g[1], ginv)]
        ok, tinv = total().TryGetInverse()
        if not ok: return
        r = autofit_radius()
        if r <= 0:
            print("  Fit needs AutoFitRadius > 0 (the spheres around the pairs)"); return
        r2 = r * r
        pts = []
        for o0 in st["set"]:
            o = sc.doc.Objects.FindId(o0.Id)
            if o is None: continue
            geo = scan_geo(o); cl = scan_cloud(o, True)
            if geo is None or cl is None: continue
            ok, inv = geo[1].TryGetInverse()
            if not ok: continue
            lp = cl.GetPoints()
            rl2 = r2 / (xf_scale(geo[1]) ** 2 or 1.0)          # the sphere in the block (a scaled block too)
            for (f_, m_) in pairs:
                lc = world(m_); lc.Transform(inv)              # the pair in this scan's own coordinates
                near = [q for q in lp if (q.X - lc.X) ** 2 + (q.Y - lc.Y) ** 2 + (q.Z - lc.Z) ** 2 <= rl2]
                step = max(1, len(near) // AUTOFIT_PER_TAG)
                for q0 in near[::step]:
                    q = rg.Point3d(q0); q.Transform(geo[1]); q.Transform(tinv)
                    pts.append(q)                              # base coordinates of the moving scans
        if len(pts) < 30:
            print("  too few scan/mesh points inside the spheres - larger AutoFitRadius?"); return
        busy = show_busy("Align: Fit...", esc="stop Fit - Align goes on")
        keep = []
        F0 = st["F"]
        try:
            # point-to-plane ICP (as AutoFit): converges where point-to-point slid on smooth
            # surfaces and stopped centimetres short
            for it in range(ALIGN_FIT_ITERS):
                check_esc()
                rows = []
                for pb in pts:
                    w = world(pb)
                    hit = _scan_closest(geos, w)
                    if hit is None: continue
                    rows.append((w, hit[0], hit[1], w.DistanceTo(hit[0])))
                if len(rows) < 10: break
                med = median([r[3] for r in rows])
                keep = [r for r in rows if r[3] <= max(3.0 * med, 1e-6)]   # the overlap only (not the free ends)
                if len(keep) < 10: break
                D, wl, tl = p2plane_step(keep, st.get("scale", False))
                st["F"] = D * st["F"]
                if wl < 1e-9 and tl < 1e-6 * max(1.0, med):
                    break
            commit()
        except UserAbort:
            st["F"] = F0; commit(); sc.doc.Views.Redraw()
            print("  Fit stopped (Esc) - back to before Fit; Align goes on"); return
        finally:
            hide_busy(busy)
        print("  Fit done ({} points in {} spheres of {})".format(len(keep), len(pairs), fl(r)))
        print_devs()

    while True:
        gp = ric.GetPoint()
        locked = not _xf_is_identity(st["F"])
        if locked:
            gp.SetCommandPrompt("Align: Fit / Fine done <Enter / right click = done, ResetFine, Esc = cancel Align, scans/meshes back>")
        else:
            if st["first"] is None:
                which = "the scan/mesh that STAYS (it turns RED)" if st["fixed"] is None else "the RED scan/mesh (stays)"
            else:
                which = "the scan/mesh that MOVES (it turns GREEN)" if st["moving"] is None else "the GREEN scan/mesh (moves)"
            gp.SetCommandPrompt("Align pair {}: point on {} <Enter / right click = done, Esc = cancel Align, scans/meshes back>".format(
                len(pairs) + 1, which))
        gp.AcceptNothing(True)
        o_undo = o_fit = o_fine = o_reset = -99
        o_scl = -99
        if not locked:
            o_undo = gp.AddOption("UndoPair")
            o_scl = gp.AddOptionToggle("Scale", ric.OptionToggle(bool(st.get("scale")), "Off", "On"))
        o_rad = o_sph = -99
        if len(pairs) >= 3:
            o_fit = gp.AddOption("Fit"); o_fine = gp.AddOption("Fine")
            o_radv = ric.OptionDouble(autofit_radius(), 0.0, 1e6); o_rad = gp.AddOptionDouble("AutoFitRadius", o_radv)
            o_sph = gp.AddOptionToggle("SpherePreview", ric.OptionToggle(drw.sph_on, "Off", "On"))
        if locked:
            o_reset = gp.AddOption("ResetFine")
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = scan_get(gp)
        if res == Rhino.Input.GetResult.Option:
            oi = gp.Option().Index
            if oi == o_undo:
                if st["first"] is not None: st["first"] = None
                elif pairs: pairs.pop()
                st["T"] = solve(); commit(); print_devs()     # (no pairs: the moving meshes go back first)
                if not pairs:                                # nothing left: STAY / MOVE chosen again
                    st["fixed"] = st["moving"] = None; st["ids"] = set(); st["set"] = []
                    paint.restore()
            elif oi == o_scl:
                st["scale"] = not st.get("scale")
                print("  Scale {}: {}".format("ON" if st["scale"] else "off",
                      "the moving scan/mesh also changes its SIZE (3+ pairs, Fit) - for AI / photogrammetry meshes "
                      "without a true size" if st["scale"] else "turn + move only"))
                st["T"] = solve(); commit(); print_devs()
            elif oi == o_fit:
                fit()
            elif oi == o_fine:
                piv = rg.Point3d(0, 0, 0)                   # turn about the middle of the pairs, not of the scan
                for (f_, m_) in pairs: piv += f_
                _fine(st["set"], 0, drw, st, commit, print_devs, [], world, fit_fn=fit, pivot=piv / float(len(pairs)))
            elif oi == o_reset:
                st["F"] = rg.Transform.Identity; commit(); print_devs()
            elif oi == o_rad:
                opt_set("fit_radius", o_radv.CurrentValue)
                drw.sph_until = time.time() + SPHERE_FLASH
            elif oi == o_sph:
                drw.sph_on = not drw.sph_on
            sc.doc.Views.Redraw()
            continue
        if is_click(res):
            if locked:
                print("  pairs are locked after Fit / Fine (ResetFine = back to the pairs)"); continue
            # the click goes ONLY onto the mesh it is meant for (another mesh in
            # front of it - e.g. the new scan over the old one - is looked through)
            if st["first"] is None:
                cand = [o for o in insts if str(o.Id) == st["fixed"]] if st["fixed"] is not None else insts
            elif st["moving"] is not None:
                cand = st["set"]
            else:
                cand = [o for o in insts if str(o.Id) != st["fixed"]]
            hit, why = pick_meant(gp, insts, cand)
            if hit is None:
                if st["first"] is None:
                    print("  {} - click ON the {}".format(why, "RED scan/mesh (it stays)" if st["fixed"] else "scan/mesh that stays"))
                else:
                    print("  {} - click ON the {}".format(why, "GREEN scan/mesh (it moves)" if st["moving"] else "scan/mesh that moves"))
                continue
            p, obj = hit
            if st["first"] is None:                            # a point on the scan that stays
                st["first"] = rg.Point3d(p)
                if st["fixed"] is None:
                    st["fixed"] = str(obj.Id); st["fixed_obj"] = obj
                    paint.paint([obj], ALIGN_STAY_COL)
                    print("  RED = the scan/mesh that stays ({})".format(_scan_name(obj) if isinstance(obj, ro.InstanceObject) else "mesh"))
            else:                                              # the same point on the scan that moves
                if st["moving"] is None:
                    branch = align_branch(obj.Id)
                    if st["fixed"] in branch:
                        print("  this scan/mesh carries the one that stays (it was aligned to it) - align the other way"); continue
                    core = frame_core()
                    bnames = set(_scan_name(o) for o in insts if str(o.Id) in branch)
                    fname = _scan_name(st["fixed_obj"])
                    bad = frame_refusal(frame_get(), bnames, [fname])
                    if bad:
                        print("  REFUSED: {} {} - moving it onto {} would throw that away. Align the other way: "
                              "UndoPair, then click {} FIRST (it stays, RED) and {} second (it moves, GREEN)."
                              .format(bad[0], bad[1], fname, bad[0], fname))
                        continue
                    st["in_frame"] = bool(bnames & core)         # a scan/mesh that holds the centre moves
                    st["moving"] = str(obj.Id); st["ids"] = branch; st["moving_obj"] = obj
                    st["set"] = [o for o in insts if str(o.Id) in branch]
                    paint.paint(st["set"], ALIGN_MOVE_COL)
                    print("  GREEN = the scan/mesh that moves{}".format(
                        "" if len(st["set"]) == 1 else " + {} scan(s)/mesh(es) aligned to it before (they move with it)".format(
                            len(st["set"]) - 1)))
                ok, inv = total().TryGetInverse()
                pb = rg.Point3d(p); pb.Transform(inv)
                pairs.append((st["first"], pb)); st["first"] = None
                st["T"] = solve(); commit(); print_devs()
                if len(pairs) == 3:
                    print("  3 pairs: turned and moved. More pairs = better, or Fit / Fine")
            sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Cancel:
            if st["set"]:
                _apply(st["set"], applied, rg.Transform.Identity)
            sc.doc.Views.Redraw()
            print(tag + " cancelled - scans/meshes back where they were"); return
        break
    if st["first"] is not None:
        print("  (the unpaired point S{} is dropped)".format(len(pairs) + 1))
    if not pairs:
        print(tag + " nothing aligned"); return
    if len(pairs) < 3 and _xf_is_identity(st["F"]):
        print(tag + " [WARN] fewer than 3 pairs: only moved, not turned")
    mov = sc.doc.Objects.FindId(st["moving_obj"].Id)
    if mov is not None:
        try:
            a = mov.Attributes.Duplicate(); a.SetUserString(ALIGN_KEY, st["fixed"])
            sc.doc.Objects.ModifyAttributes(mov, a, True)
        except Exception:
            pass
    markers_add([to_block(f_, st["fixed_obj"]) for (f_, m_) in pairs])     # spheres for AutoAlign later
    fr_ = frame_get()
    moved_names = sorted(_scan_name(o) for o in st["set"])
    joined = frame_moved(fr_, moved_names, [_scan_name(st["fixed_obj"])]) if fr_ else []
    if fr_: frame_set(fr_)
    _update_setups_after_align(st["ids"], [_scan_name(st["fixed_obj"])])
    print_devs()
    if joined:
        print("  {} now in the centred set (Center / Floor done): Align will not move it away from it".format(
            ", ".join(joined)))
    if st.get("in_frame"):
        print("  [WARN] a scan/mesh Center / Floor were clicked on moved (onto another one of them): "
              "check MeshCenter (MirrorPreview) and MeshFloor again")
    print(tag + " done: {} scan(s)/mesh(es) moved{}".format(len(st["set"]), "" if len(st["set"]) == 1 else
                                                    " (the moved scan/mesh carries the ones aligned to it)"))



# ---- markers: places where scans/meshes overlap (for AutoAlign) -----------
# Every Align pair leaves its point on the scan/mesh that stayed, kept IN that
# block (mm) - in the .3dm and in every saved setup ('_markers').
MARKER_KEY = "msh2classA_markers"


def markers_get():
    try:
        s_ = sc.doc.Strings.GetValue(MARKER_KEY)
        d = json.loads(s_) if s_ else []
    except Exception:
        d = []
    return [m for m in d if isinstance(m, list) and len(m) == 4] if isinstance(d, list) else []


def _markers_union(a, b):
    out = list(a)
    for m in b:
        if not any(m[0] == o[0] and abs(m[1] - o[1]) + abs(m[2] - o[2]) + abs(m[3] - o[3]) < 1.0 for o in out):
            out.append(list(m))                    # (the same place twice: once)
    return out


def markers_set(lst):
    try:
        if lst: sc.doc.Strings.SetString(MARKER_KEY, json.dumps(lst))
        else: sc.doc.Strings.Delete(MARKER_KEY)
    except Exception:
        pass


def markers_add(recs):
    """New markers -> the .3dm and every saved setup."""
    if not recs: return
    markers_set(_markers_union(markers_get(), recs))
    data = _read_setups()
    changed = False
    for name, S in data.items():
        if name.startswith("_") or not isinstance(S, dict): continue
        S["_markers"] = _markers_union(S.get("_markers") or [], recs); changed = True
    if changed: _write_setups(data)


AUTOALIGN_ITERS = 30


def scan_autoalign():
    """AutoAlign: many scans/meshes pulled together at once, ONLY inside the
    spheres (AutoFitRadius) around the markers (the Align pairs made before
    + points you click now). Two lists: which ones MOVE, which ones STAY -
    hidden ones too. The ones that stay (e.g. the centred front) never move;
    every moving one is pulled onto the ones that stay AND onto the other
    moving ones, all together (point-to-plane, as Fit / AutoFit)."""
    single_view()
    insts = all_scan_insts()
    if len(insts) < 2:
        print("[AutoAlign] needs two scans/meshes at least"); return
    by = _block_by_name()
    names = sorted(by.keys())
    mv = rs.MultiListBox(names, "AutoAlign 1/2: tick the scans/meshes that MOVE (hidden ones are listed too)",
                         "msh2classA AutoAlign - MOVE")
    if not mv:
        print("[AutoAlign] nothing chosen to move - nothing changed"); return
    rest = [n for n in names if n not in mv]
    if not rest:
        print("[AutoAlign] at least one scan/mesh must STAY - nothing changed"); return
    mem = frame_members()
    dflt = [n for n in rest if n in mem]
    stay = rs.MultiListBox(rest, "AutoAlign 2/2: tick the scans/meshes that STAY (they hold the others; e.g. the "
                           "centred front)", "msh2classA AutoAlign - STAY", dflt or None)
    if not stay:
        print("[AutoAlign] nothing chosen to stay - nothing changed"); return
    # every scan/mesh NOT on a list moves with the nearest listed one it hangs on (Align tree), if that one moves
    par = align_parent_map()
    id2name = dict((str(o.Id), _scan_name(o)) for o in insts)
    listed = set(mv) | set(stay)
    owner = {}
    for n in names:
        if n in listed: continue
        cur = str(by[n].Id); seen = set()
        while cur in par and cur not in seen:
            seen.add(cur); cur = par[cur]
            pn = id2name.get(cur)
            if pn in listed:
                if pn in mv: owner[n] = pn
                break
    bodies = []                                    # [listed name, [names moving with it]]
    for n in mv:
        bodies.append((n, [n] + sorted(k for k, v in owner.items() if v == n)))
    moving_names = set(k for (n, grp) in bodies for k in grp)
    bad = frame_refusal(frame_get(), moving_names, stay)
    if bad:
        print("[AutoAlign] REFUSED: {} {} and nothing that stays is - moving it would throw that away. Let it STAY "
              "(the others are pulled onto it). Nothing changed.".format(bad[0], bad[1]))
        return
    prepare_scans(insts, "AutoAlign")
    mir_axis = pause_scanmirror()
    try:
        _scan_autoalign(insts, by, bodies, list(stay), moving_names, mem)
    finally:
        _ScanDraw.off_all()
        resume_scanmirror(mir_axis)


def _scan_autoalign(insts, by, bodies, stay, moving_names, mem):
    tag = "[AutoAlign]"
    involved = set(moving_names) | set(stay)
    clicked = []                                   # markers clicked now
    old = [m for m in markers_get() if m[0] in involved]
    start = dict((str(by[n].Id), _xf_copy(by[n].InstanceXform)) for n in moving_names)
    drw = _ScanDraw(insts, None); drw.show_mirror = False; drw.sph_on = True
    st = {"fitted": False, "rows": {}, "scale": False}

    def centres():
        b_ = _block_by_name()
        out = []
        for m in old + clicked:
            q = from_block(m, b_)
            if q is not None: out.append(q)
        return out

    def pins():
        return [(c, "A{}".format(k + 1), PIN_COL, True) for k, c in enumerate(centres())], []
    drw.pins_fn = pins
    drw.sph_fn = lambda: (centres(), autofit_radius())

    def restore():
        for sid, X0 in start.items():
            o = _find(sid)
            if o is None: continue
            ok, inv = o.InstanceXform.TryGetInverse()
            if ok: sc.doc.Objects.Transform(o.Id, X0 * inv, True)
        sc.doc.Views.Redraw()

    print(tag + " {} moving ({}), {} staying ({}); {} marker(s) from earlier Align pairs".format(
        len(bodies), ", ".join(sorted(moving_names)), len(stay), ", ".join(stay), len(old)))
    while True:
        gp = ric.GetPoint()
        gp.SetCommandPrompt("AutoAlign: {} sphere(s) of AutoFitRadius - click more markers on the overlaps, Fit = "
                            "pull together, Enter = done, Esc = cancel AutoAlign (scans/meshes back)".format(
                                len(centres())))
        gp.AcceptNothing(True)
        o_fit = gp.AddOption("Fit")
        o_undo = gp.AddOption("UndoPt") if clicked else -99
        o_radv = ric.OptionDouble(autofit_radius(), 0.0, 1e6); o_rad = gp.AddOptionDouble("AutoFitRadius", o_radv)
        o_sph = gp.AddOptionToggle("SpherePreview", ric.OptionToggle(drw.sph_on, "Off", "On"))
        o_scl = gp.AddOptionToggle("Scale", ric.OptionToggle(st["scale"], "Off", "On"))
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = scan_get(gp)
        if res == Rhino.Input.GetResult.Option:
            oi = gp.Option().Index
            if oi == o_scl:
                st["scale"] = not st["scale"]
                print("  Scale {}".format("ON: the moving scans/meshes also change their SIZE (each its own factor)"
                                          if st["scale"] else "off: turn + move only"))
            elif oi == o_fit:
                _autoalign_fit(by, bodies, stay, centres(), autofit_radius(), st)
            elif oi == o_undo and clicked:
                clicked.pop()
            elif oi == o_rad:
                opt_set("fit_radius", o_radv.CurrentValue); drw.sph_until = time.time() + SPHERE_FLASH
            elif oi == o_sph:
                drw.sph_on = not drw.sph_on
            sc.doc.Views.Redraw()
            continue
        if is_click(res):
            hit = pick_on_scans(gp, [by[n] for n in involved])
            if hit is None:
                print("  click ON a visible scan/mesh that moves or stays"); continue
            clicked.append(to_block(hit[0], hit[1])); sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Cancel:
            restore()
            print(tag + " cancelled (Esc) - scans/meshes back where they were"); return
        break
    markers_add(clicked)
    if not st["fitted"]:
        print(tag + " no Fit done - nothing moved"); return
    # each listed moving one remembers the scan/mesh it was pulled onto most (Align tree; never a loop)
    for (n, grp) in bodies:
        o = by[n]
        cnt = st["rows"].get(n, {})
        order = sorted(cnt.items(), key=lambda kv: (kv[0] not in stay, -kv[1]))
        for (other, k) in order:
            if other in grp: continue
            br = align_branch(o.Id)
            if str(by[other].Id) in br: continue
            cur = sc.doc.Objects.FindId(o.Id)
            try:
                a = cur.Attributes.Duplicate(); a.SetUserString(ALIGN_KEY, str(by[other].Id))
                sc.doc.Objects.ModifyAttributes(cur, a, True)
            except Exception:
                pass
            break
    fr_ = frame_get()
    joined = frame_moved(fr_, moving_names, stay) if fr_ else []
    if fr_: frame_set(fr_)
    _update_setups_after_align(set(str(by[n].Id) for n in moving_names), list(stay))
    if joined:
        print("  {} now in the centred set (Center / Floor done)".format(", ".join(joined)))
    if moving_names & frame_core():
        print("  [WARN] a scan/mesh Center / Floor were clicked on moved: check MeshCenter (MirrorPreview) and MeshFloor")
    print(tag + " done: {} scan(s)/mesh(es) moved, saved setups keep the new places".format(len(moving_names)))


def _xf_copy(X):
    Y = rg.Transform(1.0)
    for i in range(4):
        for j in range(4):
            Y[i, j] = X[i, j]
    return Y


def _find(sid):
    for o in all_scan_insts():
        if str(o.Id) == sid: return o
    return None


def _autoalign_fit(by, bodies, stay, centres, r, st):
    """All moving bodies at once: inside every sphere, points of each moving
    scan/mesh -> the closest point of the OTHER scans/meshes there (staying or
    moving), n.(q + dq - c - dc) = 0 linearised for every body, one least-
    squares system. Bodies with no rows do not move; the staying ones never."""
    if r <= 0:
        print("  AutoAlign needs AutoFitRadius > 0"); return
    if not centres:
        print("  no markers: click points on the overlaps first (or Align some pairs before)"); return
    names_body = {}
    for bi, (n, grp) in enumerate(bodies):
        for k in grp: names_body[k] = bi
    objs = sorted(set(names_body) | set(stay))
    r2 = r * r
    # samples IN their blocks, per sphere (the spheres stay where they are at the start of Fit)
    samp = {}
    for nm in objs:
        o = sc.doc.Objects.FindId(by[nm].Id)
        geo = scan_geo(o) if o is not None else None
        cl = scan_cloud(o, True) if o is not None else None
        if geo is None or cl is None: continue
        ok, inv = o.InstanceXform.TryGetInverse()
        if not ok: continue
        lp = cl.GetPoints()
        rl2 = r2 / (xf_scale(o.InstanceXform) ** 2 or 1.0)     # the sphere in the block (a scaled block too)
        for si, c in enumerate(centres):
            lc = rg.Point3d(c); lc.Transform(inv)
            near = [q for q in lp if (q.X - lc.X) ** 2 + (q.Y - lc.Y) ** 2 + (q.Z - lc.Z) ** 2 <= rl2]
            if near:
                step = max(1, len(near) // AUTOFIT_PER_TAG)
                samp.setdefault(si, {})[nm] = [rg.Point3d(q) for q in near[::step]]
    spheres = [si for si, d in samp.items() if len(d) >= 2 and any(k in names_body for k in d)]
    if not spheres:
        print("  no sphere holds a moving AND another scan/mesh - markers on the overlaps, or a larger AutoFitRadius")
        return
    K = len(bodies)
    U = 7 if st.get("scale") else 6                 # unknowns per body: turn, move (+ size)
    busy = show_busy("AutoAlign: Fit...", esc="stop Fit - AutoAlign goes on")
    start = dict((nm, _xf_copy(sc.doc.Objects.FindId(by[nm].Id).InstanceXform)) for nm in names_body)
    med0 = None; med = None; rows_n = 0; cnt = {}
    try:
        for it in range(AUTOALIGN_ITERS):
            check_esc()
            cur = dict((nm, sc.doc.Objects.FindId(by[nm].Id)) for nm in objs)
            geos = {}
            for nm, o in cur.items():
                g = scan_geo(o) if o is not None else None
                if g is None: continue
                ok, gi = g[1].TryGetInverse()
                if ok: geos[nm] = (g[0], g[1], gi)
            rows = []
            for si in spheres:
                c = centres[si]
                here = [k for k in samp[si] if k in geos]
                for a in here:
                    if a not in names_body: continue
                    ia = names_body[a]
                    xa = geos[a][1]
                    for q0 in samp[si][a]:
                        q = rg.Point3d(q0); q.Transform(xa)
                        best = None
                        for b in here:
                            if names_body.get(b, -1) == ia: continue
                            mesh, xf, gi = geos[b]
                            lq = rg.Point3d(q); lq.Transform(gi)
                            mp = mesh.ClosestMeshPoint(lq, 2.0 * r / (abs(xf_scale(xf)) or 1.0))
                            if mp is None: continue
                            cp = rg.Point3d(mp.Point); cp.Transform(xf)
                            if cp.DistanceTo(c) > 1.2 * r: continue          # only inside the sphere
                            d = q.DistanceTo(cp)
                            if best is None or d < best[0]:
                                nv = rg.Vector3d(mesh.FaceNormals[mp.FaceIndex]); nv.Transform(xf); nv.Unitize()
                                best = (d, b, cp, nv)
                        if best is not None:
                            rows.append((a, ia, best[1], names_body.get(best[1], -1), q, best[2], best[3], best[0]))
            if len(rows) < 10: break
            med = median([x[7] for x in rows])
            if med0 is None: med0 = med
            keep = [x for x in rows if x[7] <= max(3.0 * med, 1e-6)]
            # body centroids (turn about the middle of each body's points)
            cen = []
            for bi in range(K):
                ps = [x[4] for x in keep if x[1] == bi]
                cen.append(rg.Point3d(sum(p_.X for p_ in ps) / len(ps), sum(p_.Y for p_ in ps) / len(ps),
                                      sum(p_.Z for p_ in ps) / len(ps)) if ps else rg.Point3d(0, 0, 0))
            N = U * K
            G = [[0.0] * N for _ in range(N)]; B = [[0.0] for _ in range(N)]
            for (a, ia, b, ib, q, cp, nv, d) in keep:
                row = {}
                ca = rg.Vector3d.CrossProduct(q - cen[ia], nv)
                va = [ca.X, ca.Y, ca.Z, nv.X, nv.Y, nv.Z] + ([(q - cen[ia]) * nv] if U == 7 else [])
                for j, v in enumerate(va): row[U * ia + j] = v
                if ib >= 0:
                    cb = rg.Vector3d.CrossProduct(cp - cen[ib], nv)
                    vb = [cb.X, cb.Y, cb.Z, nv.X, nv.Y, nv.Z] + ([(cp - cen[ib]) * nv] if U == 7 else [])
                    for j, v in enumerate(vb):
                        row[U * ib + j] = row.get(U * ib + j, 0.0) - v
                rhs = -((q - cp) * nv)
                items = list(row.items())
                for (i1, v1) in items:
                    B[i1][0] += v1 * rhs
                    for (i2, v2) in items:
                        G[i1][i2] += v1 * v2
            for i in range(N):
                G[i][i] += 1e-9 * (1.0 + G[i][i]) + (1.0 if G[i][i] == 0.0 else 0.0)
            x = [r_[0] for r_ in gauss_solve(G, B)]
            biggest = 0.0
            for bi, (n, grp) in enumerate(bodies):
                w = rg.Vector3d(x[U * bi], x[U * bi + 1], x[U * bi + 2])
                t = rg.Vector3d(x[U * bi + 3], x[U * bi + 4], x[U * bi + 5])
                D = rg.Transform.Translation(t)
                if w.Length > 1e-15: D = D * rg.Transform.Rotation(w.Length, w, cen[bi])
                if U == 7:
                    D = D * rg.Transform.Scale(cen[bi], 1.0 + x[U * bi + 6])
                    biggest = max(biggest, abs(x[U * bi + 6]) * max(r, 1.0))
                for k in grp:
                    sc.doc.Objects.Transform(by[k].Id, D, True)
                biggest = max(biggest, w.Length * max(r, 1.0), t.Length)
            rows_n = len(keep)
            cnt = {}
            for (a, ia, b, ib, q, cp, nv, d) in keep:
                ln = bodies[ia][0]
                cnt.setdefault(ln, {}); cnt[ln][b] = cnt[ln].get(b, 0) + 1
            if biggest < 1e-6 * max(1.0, med):
                break
    except UserAbort:
        for nm, X0 in start.items():
            o = sc.doc.Objects.FindId(by[nm].Id)
            ok, inv = o.InstanceXform.TryGetInverse()
            if ok: sc.doc.Objects.Transform(o.Id, X0 * inv, True)
        sc.doc.Views.Redraw()
        print("  Fit stopped (Esc) - back to before this Fit; AutoAlign goes on"); return
    finally:
        hide_busy(busy)
    sc.doc.Views.Redraw()
    st["fitted"] = True
    for ln, d_ in cnt.items():
        acc = st["rows"].setdefault(ln, {})
        for b, k in d_.items(): acc[b] = acc.get(b, 0) + k
    print("  Fit done: {} sphere(s), {} point pairs; distance on the overlaps (median) {} -> {}".format(
        len(spheres), rows_n, fl(med0 or 0.0), fl(med or 0.0)))
    if st.get("scale"):
        print("  sizes now: " + ", ".join("{} x{:.5f}".format(n, xf_scale(sc.doc.Objects.FindId(by[n].Id).InstanceXform))
                                          for (n, grp) in bodies))


def scan_floor():
    """Floor: 3+ points on the floor / sill. Turns ONLY about the axis
    across the symmetry plane and moves only up/down and front/back, so the
    symmetry from Center stays. Optional: one point to become front/back 0."""
    single_view()
    axis = mirror_normal("Floor", ("YZ", "XZ"))
    if axis is None: return
    insts = all_scan_insts()                    # all scans together: the alignment stays
    if not insts: return
    prepare_scans(insts, "Floor")
    mir_axis = pause_scanmirror()
    try:
        _scan_floor(axis, insts)
    finally:
        _ScanDraw.off_all()                      # tags / mirror / spheres off
        resume_scanmirror(mir_axis)


def _scan_floor(axis, insts):
    pts = []; pobjs = []
    drw = _ScanDraw(insts, None)
    while True:
        gp = ric.GetPoint()
        gp.SetCommandPrompt("Floor: point {} on the floor (at least 3) <Enter = done, Esc = cancel Floor, nothing "
                            "changes>".format(len(pts) + 1))
        gp.AcceptNothing(True)
        o_sym = add_sym_option(gp, ("YZ", "XZ"))
        o_undo = gp.AddOption("UndoPt") if pts else -99
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = scan_get(gp)
        if res == Rhino.Input.GetResult.Option:
            if gp.Option().Index == o_sym:
                axis = set_sym_from_option(gp, ("YZ", "XZ"))
            elif gp.Option().Index == o_undo and pts:
                pts.pop(); pobjs.pop(); drw.dots.pop(); sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Cancel:          # Esc: nothing changes (it used to work like Enter)
            print("[Floor] cancelled (Esc) - nothing changed"); return
        if is_click(res):
            hit = pick_on_scans(gp, insts)
            if hit is None: print("  click ON a visible scan/mesh"); continue
            pts.append(hit[0]); pobjs.append(hit[1])
            drw.dots.append((hit[0], "F{}".format(len(pts)))); sc.doc.Views.Redraw()
            continue
        break
    if len(pts) < 3:
        print("[Floor] needs at least 3 points - nothing changed"); return
    fr_ = frame_get()
    c_ax = (fr_.get("center") or {}).get("axis")
    if c_ax is not None and c_ax != axis:
        print("[Floor] Center was done with SymPlane {} - Floor must use the same one (it turns only about the axis "
              "across it; another one would move the centre). Nothing changed.".format(
                  [k for k, v in MIRROR_AXIS.items() if v == c_ax][0] if c_ax in MIRROR_AXIS.values() else c_ax))
        return
    cmem = set(((_norm_frame(fr_).get("center")) or {}).get("members", []))
    free = not cmem                                  # no centre yet: nothing to keep, level it fully
    on = set(_scan_name(o_) for o_ in pobjs)
    if cmem and not on <= cmem:
        print("[Floor] REFUSED: the points are on {} - not centred (MeshCenter was done on {}). Levelling it would "
              "move that centre. Align it to the centred ones first, or MeshCenter on it. Nothing changed.".format(
                  ", ".join(sorted(on - cmem)), ", ".join(sorted(cmem))))
        return
    res, why = floor_xf(pts, None, axis, free)
    if res is None:
        print("[Floor] {}. Nothing changed.".format(why)); return
    ref = None; ref_obj = None
    gp = ric.GetPoint()
    gp.SetCommandPrompt("Floor: click the point that becomes front/back 0 <Enter = keep front/back, Esc = cancel "
                        "Floor, nothing changes>")
    gp.AcceptNothing(True); gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
    res0 = scan_get(gp)
    if res0 == Rhino.Input.GetResult.Cancel:
        print("[Floor] cancelled (Esc) - nothing changed"); return
    if is_click(res0):
        hit = pick_on_scans(gp, insts)
        if hit is not None: ref, ref_obj = hit[0], hit[1]
    if ref is not None and cmem and _scan_name(ref_obj) not in cmem:
        print("  the front/back point is on {} - not centred: front/back kept as it was".format(_scan_name(ref_obj)))
        ref = None; ref_obj = None
    (T, ang, roll), _ = floor_xf(pts, ref, axis, free)
    # the clicks IN their blocks (before the move): Center can level again with them
    rec = {"axis": axis, "pts": [to_block(p_, o_) for (p_, o_) in zip(pts, pobjs)],
           "ref": to_block(ref, ref_obj) if ref is not None else None}
    for obj in insts:
        sc.doc.Objects.Transform(obj.Id, T, True)
    sc.doc.Views.Redraw()
    ids = [str(o_.Id) for o_ in pobjs] + ([str(ref_obj.Id)] if ref_obj is not None else [])
    if len(set(frozenset(align_group([i])) for i in ids)) > 1:
        print("  [WARN] the Floor points are on scans/meshes NOT aligned to each other - Align them first")
    frame_part_set(fr_, "floor", ids, rec)
    frame_set(fr_)
    print("[Floor] tilted {:.3f} deg {}, floor at 0{}".format(
        ang, "(no centre yet: turned flat whatever way it leaned)" if free else "about the axis across the symmetry plane",
        "" if ref is None else ", front/back 0 at the clicked point"))
    if abs(roll) > 0.05:
        print("  [INFO] the floor also leans {:.2f} deg sideways - NOT corrected (it would break the symmetry); "
              "check the Center pairs or the scans/meshes".format(roll))
    if c_ax is None:
        print("  (Center can come later: it levels this floor again by itself, the order does not matter)")
    save_setup_prompt(insts, "Floor")


# ---- named setups ------------------------------------------------------------
# A setup = the position of every scan block under a name (front, rear ...).
# Saved ONLY with the scans: 'scan_setups.json' in the folder of the scan
# files, written at once. Nothing is stored in the .3dm - any file finds the
# setups again after LoadMeshes of the same scans.
SETUP_JSON = "scan_setups.json"


def _setup_files():
    """scan_setups.json in every folder the loaded meshes come from."""
    out = []
    for (o, lp) in scan_instances():
        src = obj_src(o)
        if src:
            f = os.path.join(os.path.dirname(src), SETUP_JSON)
            if f not in out: out.append(f)
    return out


def _read_setups():
    """All setups from scan_setups.json in the mesh folders (newest copy of a
    name wins when two folders have it)."""
    data = {}
    for f in _setup_files():
        if not os.path.isfile(f): continue
        try:
            with open(f) as fh: d = json.loads(fh.read())
        except Exception:
            print("  [WARN] could not read {}".format(f)); continue
        for k, v in (d or {}).items():
            if not isinstance(v, dict): continue
            if k not in data or v.get("_saved", 0) >= data[k].get("_saved", 0):
                data[k] = v
    return data


def _write_setups(data):
    """Write to scan_setups.json in every mesh folder. -> files written."""
    txt = json.dumps(data, indent=1, sort_keys=True)
    done = []
    for f in _setup_files():
        try:
            with open(f, "w") as fh: fh.write(txt)
            done.append(f)
        except Exception:
            print("  [WARN] could not write {} (folder read-only?)".format(f))
    return done


def _scan_name(obj):
    """Key of a mesh in a setup: its block name (= the mesh file name)."""
    return obj.InstanceDefinition.Name


def obj_src(obj):
    return obj.Attributes.GetUserString(SCAN_KEY) or ""


def _setup_record(st, o):
    """The entry of mesh block o in setup st: by block name, else by the mesh
    file name (a new .3dm after LoadMeshes), else by the old full-path key."""
    rec = st.get(_scan_name(o))
    if rec: return rec
    fn = os.path.basename(obj_src(o)).lower()
    if fn:
        for k, v in st.items():
            if isinstance(v, dict) and v.get("file", "").lower() == fn: return v
    return st.get(obj_src(o))


def save_setup_prompt(insts, what):
    if insts and not any(isinstance(o, ro.InstanceObject) for o in insts):
        print("  (plain scans/meshes carry their position in the scan/mesh itself - setups are for scan/mesh blocks only)")
        return
    dflt = sc.sticky.get("M2CA_setup", "front")
    gs = ric.GetString()
    gs.SetCommandPrompt("{} done. Remember these scan/mesh positions under a NAME (e.g. front, rear) to bring them back "
                        "later with MeshSetup - type a name + Enter, Enter alone = '{}'".format(what, dflt))
    gs.AcceptNothing(True)
    gs.AddOption("DontSave")
    res = gs.Get()
    if res == Rhino.Input.GetResult.Nothing:
        name = dflt
    elif res == Rhino.Input.GetResult.String:
        name = (gs.StringResult() or "").strip() or dflt
    else:                                          # DontSave / Esc
        print("  positions not saved as a setup (the scans/meshes stay where they are)"); return
    sc.sticky["M2CA_setup"] = name
    data = _read_setups()
    st = {"_saved": time.time(), "_units": doc_unit_name(), "_mm_per_unit": _mm_per_unit(),
          "_format": "msh2classA-setup/1"}
    fr_ = frame_get()
    if fr_: st["_frame"] = fr_                     # what Center / Floor set (MeshSetup brings it back)
    mk = markers_get()
    if mk: st["_markers"] = mk                     # AutoAlign spheres
    st.update(photo_records(st))                   # the photos' corners (MeshPhoto)
    for (o, lp) in scan_instances():
        rec = {"layer": lp, "matrix_row_major": xf_to_setup(o.InstanceXform, st),
               "block": o.InstanceDefinition.Name, "file": os.path.basename(obj_src(o))}
        src = obj_src(o)
        if src and os.path.isfile(src):
            try:
                rec["sha256"] = file_sha256(src); rec["size"] = os.path.getsize(src)
            except Exception:
                pass
        st[_scan_name(o)] = rec
    data[name] = st
    if not _setup_files():
        print("  [Setup] not saved: these scan/mesh blocks do not know their scan/mesh files (load them with LoadMeshes)")
        return
    where = _write_setups(data)
    n = len([k for k in st if not k.startswith("_") and not k.startswith("photo:")])
    n_ph = len([k for k in st if k.startswith("photo:")])
    print("[Setup] '{}' saved: {} scan(s)/mesh(es){}, in {}".format(
        name, n, " + {} photo(s)".format(n_ph) if n_ph else "", " and ".join(where) or "-"))


def scan_setup():
    """MeshSetup: apply a saved setup (all mesh blocks jump there), save the
    present positions under a name, or remove a setup."""
    data = _read_setups()
    names = sorted(data.keys())
    SAVE = "<save the present positions as ...>"; REMOVE = "<remove a setup ...>"
    items = names + [SAVE] + ([REMOVE] if names else [])
    choice = rs.ListBox(items, "Scan/mesh setups (positions of the scan/mesh blocks)", "MeshSetup")
    if choice is None: return
    if choice == SAVE:
        save_setup_prompt([], "Setup"); return
    if choice == REMOVE:
        gone = rs.ListBox(names, "Remove which setup?", "MeshSetup - remove")
        if not gone: return
        if rs.MessageBox("Remove the setup '{}'?\n(The scans/meshes stay where they are.)".format(gone), 4 | 32,
                         "MeshSetup - remove") != 6:
            return
        del data[gone]
        where = _write_setups(data)
        print("[Setup] '{}' removed from {}".format(gone, " and ".join(where) or "-"))
        return
    st = data[choice]; n = 0; missing = []
    moves = []; ref = None
    if not st.get("_units") and not st.get("_mm_per_unit") and abs(_mm_per_unit() - 1.0) > 1e-9:
        print("  [WARN] setup '{}' has no units (older file) - taken as mm; the document is in {}".format(
            choice, doc_unit_name()))
    for (o, lp) in scan_instances():
        rec = _setup_record(st, o)
        ok, inv = o.InstanceXform.TryGetInverse()
        if not ok: continue
        if not rec:
            missing.append(o); continue
        src = obj_src(o)
        if rec.get("sha256") and src and os.path.isfile(src) and file_sha256(src) != rec["sha256"]:
            print("  [WARN] {} CHANGED since the setup was saved (SHA-256 differs) - is it still the customer's "
                  "scan?".format(os.path.basename(src)))
        X = xf_from_setup(rec["matrix_row_major"], st)
        moves.append((o, X * inv))
        if ref is None: ref = X * inv
    for o in missing:                     # not in this setup (loaded later): moves with the structure
        if ref is not None: moves.append((o, ref))
    for (o, D) in moves:
        sc.doc.Objects.Transform(o.Id, D, True); n += 1
    if st.get("_markers"):
        markers_set(_markers_union(markers_get(), st["_markers"]))
    n_ph = apply_photo_records(st)
    if n_ph: print("  {} photo(s) back where they were in this setup".format(n_ph))
    had = frame_get()
    fr_ = st.get("_frame")
    frame_set(dict(fr_) if isinstance(fr_, dict) else {})   # Center / Floor marks as they were in this setup
    if had and not isinstance(fr_, dict):
        print("  [INFO] setup '{}' has no Center / Floor marks (older, or saved by Blender / FreeCAD): the "
              "scans/meshes count as NOT centred now - Align will not guard them".format(choice))
    missing = [o.InstanceDefinition.Name for o in missing] if ref is None else []
    sc.sticky["M2CA_setup"] = choice
    sc.doc.Views.Redraw()
    print("[Setup] '{}' applied to {} scan(s)/mesh(es){}".format(choice, n,
          "" if not missing else "; not in this setup (left as they are): " + ", ".join(missing)))


MIRROR_PT_COLOR = (0, 60, 255)             # strong blue, opaque - the mirrored scan as points
MIRROR_PT_SIZE = 2                         # pixels
MIRROR_PT_MAX = 400000                     # points drawn per scan at most (big scans: every n-th vertex)
MIRROR_PT_LIFT = 0.003                     # (only if MIRROR_PT_ON_TOP is False)
MIRROR_PT_ON_TOP = True                    # mirror points always visible (not hidden by the scan); MirrorPreview = on/off
_MIRROR_HIDE = [False]                     # (kept for old callers; the point mirror is never hidden)
_LOCAL_CLOUD = {}


def _cloud_key(obj):
    if isinstance(obj, ro.InstanceObject): return obj.InstanceDefinition.Id
    return ("mesh", obj.Id, obj.RuntimeSerialNumber)   # a moved mesh is a new object (new serial)


def scan_cloud(obj, build=True):
    """Vertices of the mesh as a point cloud in its own coordinates, thinned
    to MIRROR_PT_MAX. build=False: only what was made before (drawing)."""
    key = _cloud_key(obj)
    c = _LOCAL_CLOUD.get(key)
    if c is not None or not build: return c
    geo = scan_geo(obj)
    if geo is None: return None
    pts = geo[0].Vertices.ToPoint3dArray()
    step = max(1, int(math.ceil(len(pts) / float(MIRROR_PT_MAX))))
    c = rg.PointCloud([pts[i] for i in range(0, len(pts), step)] if step > 1 else pts)
    if not isinstance(obj, ro.InstanceObject):         # forget the clouds of this mesh before it moved
        for k_ in [k_ for k_ in _LOCAL_CLOUD if isinstance(k_, tuple) and k_[1] == obj.Id]:
            del _LOCAL_CLOUD[k_]
    _LOCAL_CLOUD[key] = c
    return c


def draw_mirror_points(d, axis, objs):
    """Mirrored copies of the mesh instances objs as opaque points (drawn
    only, nothing added to the file). Lifted a little towards the eye -
    same place on screen - so points lying ON the real mesh are not hidden
    by it; points clearly behind it still are (no see-through clutter)."""
    if not objs: return
    nrm = [0.0, 0.0, 0.0]; nrm[axis] = 1.0
    M_ = rg.Transform.Mirror(rg.Point3d(0, 0, 0), rg.Vector3d(nrm[0], nrm[1], nrm[2]))
    lift = rg.Transform.Identity
    try:
        vp = d.Viewport
        if vp.IsPerspectiveProjection:          # scale about the eye: same screen position, smaller depth
            lift = rg.Transform.Scale(vp.CameraLocation, 1.0 - MIRROR_PT_LIFT)
        else:
            dist = vp.CameraLocation.DistanceTo(vp.CameraTarget)
            cd = rg.Vector3d(vp.CameraDirection); cd.Unitize()
            lift = rg.Transform.Translation(-cd * (MIRROR_PT_LIFT * dist))
    except Exception:
        pass
    colour = sd.Color.FromArgb(*MIRROR_PT_COLOR)
    depth_off = False
    if MIRROR_PT_ON_TOP:
        lift = rg.Transform.Identity
        try:
            d.PushDepthTesting(False); depth_off = True
        except Exception:
            try: d.EnableDepthTesting(False); depth_off = True
            except Exception: pass
    try:
        for o in objs:
            c = scan_cloud(o, False)
            if c is None: continue               # made before drawing (prefetch), never inside it
            d.PushModelTransform(lift * M_ * scan_xform(o))
            try: d.DrawPointCloud(c, MIRROR_PT_SIZE, colour)
            except Exception: pass
            d.PopModelTransform()
    finally:
        if depth_off:
            try: d.PopDepthTesting()
            except Exception:
                try: d.EnableDepthTesting(True)
                except Exception: pass


def draw_mirrored_scans(d, axis, mat=None, ids=None):
    """MeshMirror: mirrored copies of the visible meshes as points."""
    if ids is None:
        objs = [o for (o, lp) in scan_objects()]
    else:
        objs = [sc.doc.Objects.FindId(i) for i in ids]
    draw_mirror_points(d, axis, [o for o in objs if o is not None and scan_visible(o)])


class _ScanMirrorDraw(object):
    """Pipeline events (no conduit subclass - same way as the rest of the
    script); kept in sticky so the next MeshMirror can switch it off."""
    def __init__(self, axis, ids=None):
        self.axis = axis; self.ids = ids              # scans to mirror (ids; None = all)
        self._b = self.bbox; self._d = self.draw      # same handler objects for += and -=

    def bbox(self, sender, e):
        try:
            nrm = [0.0, 0.0, 0.0]; nrm[self.axis] = 1.0
            M_ = rg.Transform.Mirror(rg.Point3d(0, 0, 0), rg.Vector3d(nrm[0], nrm[1], nrm[2]))
            objs = [o for (o, lp) in scan_objects()] if self.ids is None else \
                [sc.doc.Objects.FindId(i) for i in self.ids]
            for o in objs:
                if o is None or not scan_visible(o): continue
                bb = o.Geometry.GetBoundingBox(True); bb.Transform(M_)
                e.IncludeBoundingBox(bb)
        except Exception: pass

    def draw(self, sender, e):
        try: draw_mirrored_scans(e.Display, self.axis, None, self.ids)
        except Exception: pass

    def on(self):
        Rhino.Display.DisplayPipeline.CalculateBoundingBox += self._b
        Rhino.Display.DisplayPipeline.PostDrawObjects += self._d

    def off(self):
        try: Rhino.Display.DisplayPipeline.CalculateBoundingBox -= self._b
        except Exception: pass
        try: Rhino.Display.DisplayPipeline.PostDrawObjects -= self._d
        except Exception: pass


def scan_mirror():
    """MeshMirror: mirrored copies of the VISIBLE mesh blocks as blue points, drawn only (nothing added to the file). Again =
    off. Switched off while MeshCenter / MeshFloor run, back on after."""
    cur = sc.sticky.get("M2CA_scanmir")
    if cur is not None:
        try: cur.off()
        except Exception: pass
        sc.sticky["M2CA_scanmir"] = None
        sc.doc.Views.Redraw(); print("[MeshMirror] off"); return
    axis = mirror_normal("MeshMirror")
    if axis is None: return
    objs = [o for (o, lp) in scan_objects() if scan_visible(o)]
    prefetch_meshes(objs)
    n = len(objs)
    c = _ScanMirrorDraw(axis, [o.Id for o in objs]); c.on()
    sc.sticky["M2CA_scanmir"] = c
    sc.doc.Views.Redraw()
    print("[MeshMirror] {} scan(s)/mesh(es) mirrored, shown as blue points (MeshMirror again = off)".format(n))


SCAN_TOOL_LABEL = {"LoadMeshes": "Load scans/meshes", "MeshAlign": "Align", "MeshAutoAlign": "AutoAlign (many)",
                   "MeshCenter": "Center", "MeshFloor": "Floor", "MeshScale": "Scale (curve)",
                   "MeshSetup": "Setups", "MeshMirror": "Mirror on/off"}
# ---- moved BY HAND (outside msh2classA) ------------------------------------
# Allowed: you may nudge a scan/mesh block yourself (gumball, Move, Rotate...).
# Every scan/mesh tool first compares the blocks with where msh2classA last
# left them and asks what to do with a hand move:
#   Keep     = keep it there; the saved setups follow (the scan/mesh keeps its
#              new place relative to the one it is aligned to) - like an Align
#   KeepOnly = keep it there, the saved setups stay as they were
#   Back     = put it back where msh2classA left it
# Center / Floor marks: a hand move of a scan/mesh they were CLICKED on drops
# that mark (the centre / floor moved: MeshCenter / MeshFloor again); a whole
# set moved together keeps them only if the move keeps the plane / the floor.
SEEN_KEY = "msh2classA_seen"


def _xf_list(X):
    return [X[i, j] for i in range(4) for j in range(4)]


def _xf_of(lst):
    X = rg.Transform(1.0)
    for i in range(4):
        for j in range(4):
            X[i, j] = lst[4 * i + j]
    return X


def remember_positions():
    d = dict((_scan_name(o), _xf_list(o.InstanceXform)) for o in all_scan_insts())
    try:
        sc.doc.Strings.SetString(SEEN_KEY, json.dumps(d))
    except Exception:
        pass


def _seen():
    try:
        s_ = sc.doc.Strings.GetValue(SEEN_KEY)
        return json.loads(s_) if s_ else None
    except Exception:
        return None


def _xf_close(A, B, tol):
    for i in range(3):
        for j in range(3):
            if abs(A[i, j] - B[i, j]) > 1e-7: return False
        if abs(A[i, 3] - B[i, 3]) > tol: return False
    return True


def _keeps_plane(D, axis):
    """D maps the symmetry plane (x = 0 for YZ...) onto itself, sides kept."""
    for p in ((0.0, 0.0, 0.0), (0.0, 1000.0, 0.0), (0.0, 0.0, 1000.0), (1000.0, 0.0, 0.0), (0.0, 700.0, 300.0)):
        q = [0.0, 0.0, 0.0]
        q[axis] = p[0]; q[(axis + 1) % 3] = p[1]; q[(axis + 2) % 3] = p[2]
        a = rg.Point3d(q[0], q[1], q[2]); a.Transform(D)
        m = rg.Point3d(q[0], q[1], q[2]); m_ = [m.X, m.Y, m.Z]; m_[axis] = -m_[axis]
        b = rg.Point3d(m_[0], m_[1], m_[2]); b.Transform(D)
        av = [a.X, a.Y, a.Z]; bv = [b.X, b.Y, b.Z]
        tol = 1e-4 / _mm_per_unit()
        if abs(av[axis] + bv[axis]) > tol: return False
        for k in range(3):
            if k != axis and abs(av[k] - bv[k]) > tol: return False
        if p[0] > 0 and av[axis] <= 0: return False
    return True


def _floor_still_level(d):
    fl_ = d.get("floor")
    if not isinstance(fl_, dict): return True
    blocks = _block_by_name()
    tol = 1e-4 / _mm_per_unit()
    for r in fl_.get("pts", []):
        q = from_block(r, blocks)
        if q is None or abs(q.Z) > tol: return False
    if fl_.get("ref"):
        q = from_block(fl_["ref"], blocks)
        axis = fl_.get("axis", 0)
        fwd = [0.0, 0.0, 0.0]
        e = [0.0, 0.0, 0.0]; e[axis] = 1.0
        fv = rg.Vector3d.CrossProduct(rg.Vector3d(0, 0, 1), rg.Vector3d(e[0], e[1], e[2]))
        if q is None or abs(q.X * fv.X + q.Y * fv.Y + q.Z * fv.Z) > tol: return False
    return True


def hand_moves_check(tool):
    """-> True: go on with the tool; False: Esc (the tool does not start)."""
    seen = _seen()
    insts = all_scan_insts()
    if seen is None:
        remember_positions(); return True
    tol = 1e-6 / _mm_per_unit()
    moved = []
    for o in insts:
        n = _scan_name(o)
        if n in seen and not _xf_close(o.InstanceXform, _xf_of(seen[n]), tol):
            moved.append(o)
    if not moved:
        remember_positions(); return True
    common = [o for o in insts if _scan_name(o) in seen]
    deltas = []
    for o in moved:
        ok, inv = _xf_of(seen[_scan_name(o)]).TryGetInverse()
        deltas.append(o.InstanceXform * inv)
    whole = len(moved) == len(common) and all(_xf_close(D, deltas[0], 1e-4 / _mm_per_unit()) for D in deltas)
    lines = []
    for o, D in zip(moved, deltas):
        try:
            g = scan_geo(o); c = g[0].GetBoundingBox(True).Center; c.Transform(o.InstanceXform)
        except Exception:
            c = rg.Point3d(0, 0, 0); c.Transform(o.InstanceXform)
        ok, inv = D.TryGetInverse(); c0 = rg.Point3d(c); c0.Transform(inv)
        tr = (D[0, 0] + D[1, 1] + D[2, 2]) / max(1e-12, abs(xf_scale(D)))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, (tr - 1.0) / 2.0))))
        sz = xf_scale(D)
        lines.append("{} {} / {:.3f} deg{}".format(_scan_name(o), fl(c.DistanceTo(c0)), ang,
                                                   "" if abs(sz - 1.0) < 1e-9 else " / size x{:.5f}".format(sz)))
    print("[{}] moved BY HAND since msh2classA last saw {}: {}".format(
        tool, "them" if len(moved) > 1 else "it", "; ".join(lines)))
    if whole:
        print("  (all scans/meshes together - the alignment between them is the same)")
    gs = ric.GetString()
    if whole:
        gs.SetCommandPrompt("Moved by hand: Keep = keep there (all together), Back = put back <Enter = Keep, Esc = "
                            "do not start {}>".format(tool))
    else:
        gs.SetCommandPrompt("Moved by hand: Keep = keep there, the saved setups follow (new place against the "
                            "scan/mesh it is aligned to), KeepOnly = keep there, setups as they were, Back = put back "
                            "<Enter = Keep, Esc = do not start {}>".format(tool))
    gs.AcceptNothing(True)
    o_keep = gs.AddOption("Keep")
    o_only = gs.AddOption("KeepOnly") if not whole else -99
    o_back = gs.AddOption("Back")
    res = gs.Get()
    if res == Rhino.Input.GetResult.Cancel:
        print("  {} not started - the hand move is asked again next time".format(tool)); return False
    choice = "keep"
    if res == Rhino.Input.GetResult.Option:
        oi = gs.Option().Index
        choice = "back" if oi == o_back else ("only" if oi == o_only else "keep")
    if choice == "back":
        for o in moved:
            cur = sc.doc.Objects.FindId(o.Id)
            ok, inv = cur.InstanceXform.TryGetInverse()
            if ok: sc.doc.Objects.Transform(cur.Id, _xf_of(seen[_scan_name(o)]) * inv, True)
        sc.doc.Views.Redraw()
        print("  put back where msh2classA left {}".format("them" if len(moved) > 1 else "it"))
        remember_positions(); return True
    names = set(_scan_name(o) for o in moved)
    d = _norm_frame(frame_get())
    if d:
        for k, what in (("center", "MeshCenter"), ("floor", "MeshFloor")):
            p_ = d.get(k)
            if not isinstance(p_, dict): continue
            if whole:
                ok_ = _keeps_plane(deltas[0], (d.get("center") or {}).get("axis", 0)) if k == "center" \
                    else _floor_still_level(d)
            else:
                ok_ = not (names & set(p_.get("core", [])))
            if not ok_:
                d.pop(k, None)
                print("  [WARN] {} - the {} is gone: {} again".format(
                    "the move turned / moved the set off the symmetry plane" if (whole and k == "center") else
                    "the floor is not level any more" if whole else
                    "{} ({} was clicked on it) moved".format(", ".join(sorted(names & set(p_.get("core", [])))), what),
                    "centre" if k == "center" else "floor", what))
        _frame_union(d); frame_set(d)
    if choice == "keep" and not whole:
        par = align_parent_map()
        ids = dict((str(o.Id), o) for o in insts)
        groups = {}
        mids_all = set(str(o.Id) for o in moved)
        for o in moved:
            pid = par.get(str(o.Id)); seen_ = set()
            while pid and pid in mids_all and pid not in seen_:     # carried by a moved one: up to an unmoved one
                seen_.add(pid); pid = par.get(pid)
            if pid and pid in ids and pid not in mids_all:
                groups.setdefault(pid, []).append(str(o.Id))
        for pid, mids in groups.items():
            _update_setups_after_align(set(mids), [_scan_name(ids[pid])])
        lone = [_scan_name(o) for o in moved if not any(str(o.Id) in v for v in groups.values())]
        if groups:
            print("  saved setups follow: {}".format(", ".join(sorted(
                _scan_name(ids[m]) for v in groups.values() for m in v))))
        if lone:
            print("  {} not aligned to an unmoved scan/mesh - saved setups keep {} as before".format(
                ", ".join(sorted(lone)), "them" if len(lone) > 1 else "it"))
    else:
        print("  kept there; saved setups as they were")
    remember_positions()
    return True


def _with_hand_check(fn, tool):
    def run(*a, **k):
        if not hand_moves_check(tool): return None
        try:
            return fn(*a, **k)
        finally:
            remember_positions()
    run.__name__ = fn.__name__
    return run


# ---- MeshScale: the true size from a curve ---------------------------------
def scan_scale():
    """AI / photogrammetry meshes have no true size. Draw a curve on the
    scan/mesh along something you measured (lamp to lamp over the bonnet, with
    a tape), give its true length: ALL scans/meshes (hidden ones too) and the
    curve are scaled by true / curve length - about the world origin when
    MeshCenter is done (the centre, the floor and front/back 0 stay where they
    are), else about the middle of the curve. The alignment between the
    scans/meshes stays (all get the same factor); saved setups get it at the
    next save (MeshSetup), the size is written in the Center marks."""
    insts = all_scan_insts()
    if not insts:
        print("[MeshScale] no scan/mesh blocks - LoadMeshes first"); return
    go = ric.GetObject()
    go.SetCommandPrompt("MeshScale: pick the CURVE you drew along the measured length <Esc = cancel MeshScale>")
    go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
    go.EnablePreSelect(True, True)
    if go.Get() != Rhino.Input.GetResult.Object:
        print("[MeshScale] cancelled - nothing changed"); return
    crv_ref = go.Object(0); crv = crv_ref.Curve()
    if crv is None:
        print("[MeshScale] that is not a curve - nothing changed"); return
    L = crv.GetLength()
    if L <= 1e-9:
        print("[MeshScale] the curve has no length - nothing changed"); return
    gn = ric.GetNumber()
    gn.SetCommandPrompt("True length of the curve (now {}) in {} <Esc = cancel MeshScale>".format(fl(L), doc_unit_name()))
    gn.SetDefaultNumber(L); gn.SetLowerLimit(1e-9, True)
    if gn.Get() != Rhino.Input.GetResult.Number:
        print("[MeshScale] cancelled - nothing changed"); return
    target = gn.Number()
    f = target / L
    if abs(f - 1.0) < 1e-12:
        print("[MeshScale] already that size - nothing changed"); return
    d = _norm_frame(frame_get())
    centred = bool((d.get("center") or {}).get("members"))
    if centred:
        piv = rg.Point3d(0, 0, 0); where = "the world origin (on the symmetry plane and the floor - both stay)"
    else:
        piv = crv.PointAtNormalizedLength(0.5); where = "the middle of the curve (no MeshCenter yet)"
    S = rg.Transform.Scale(piv, f)
    for o in insts:
        sc.doc.Objects.Transform(o.Id, S, True)
    sc.doc.Objects.Transform(crv_ref.ObjectId, S, True)    # the curve stays on the scan/mesh, now true length
    if d:
        sz = d.get("size") or {}
        d["size"] = {"factor": float(sz.get("factor", 1.0)) * f, "curve": fl(target), "units": doc_unit_name()}
        if not centred and d.get("floor"):
            print("  [WARN] scaled about the curve, not on the floor: MeshFloor again")
            d.pop("floor", None)
        _frame_union(d); frame_set(d)
    else:
        frame_set({"size": {"factor": f, "curve": fl(target), "units": doc_unit_name()}})
    sc.doc.Views.Redraw()
    print("[MeshScale] all {} scan(s)/mesh(es) and the curve scaled x{:.6f} about {} - curve now {}".format(
        len(insts), f, where, fl(target)))
    save_setup_prompt(insts, "MeshScale")


_scan_scale_raw = scan_scale
load_scans = _with_hand_check(load_scans, "LoadMeshes")
scan_align = _with_hand_check(scan_align, "MeshAlign")
scan_autoalign = _with_hand_check(scan_autoalign, "MeshAutoAlign")
scan_center = _with_hand_check(scan_center, "MeshCenter")
scan_floor = _with_hand_check(scan_floor, "MeshFloor")
scan_setup = _with_hand_check(scan_setup, "MeshSetup")
scan_scale = _with_hand_check(scan_scale, "MeshScale")


# in the order of the work: the main scan/mesh first (Center, Floor, its true
# size), then the others aligned to it - the guard keeps that centre
SCAN_TOOLS = (("LoadMeshes", load_scans), ("MeshCenter", scan_center), ("MeshFloor", scan_floor),
              ("MeshScale", scan_scale), ("MeshAlign", scan_align), ("MeshAutoAlign", scan_autoalign),
              ("MeshSetup", scan_setup), ("MeshMirror", scan_mirror))


