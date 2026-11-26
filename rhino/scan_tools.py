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


def api_insert(path, name, layer_index):
    """STL / OBJ / PLY without -Insert: inside -Insert those formats ask their
    import options in the middle of the command and the rest of a scripted
    answer is lost (it kept asking 'Insert as', point, scale, rotation).
    Here: read the file with default options (no questions), make a block
    definition at 0,0,0 from it, mark it LINKED to the file, place one
    instance with identity transform. The file itself is only read."""
    before = set(o.Id for o in sc.doc.Objects.GetObjectList(ro.ObjectEnumeratorSettings()))
    is_obj = path.lower().endswith(".obj")
    if is_obj:
        # OBJ: its importer ALWAYS asks 'Choose OBJ option' when run from a
        # script. Rhino remembers the LAST used options, so every option is
        # set here explicitly (MapYtoZ=Yes would turn the scan 90 deg),
        # then Enter.
        try:
            rs.Command('_-Import "{}" {} _Enter'.format(path, OBJ_IMPORT_OPTS), False)
        except Exception:
            pass
    else:
        # STL / PLY: batch import, default options, no questions
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
            print("  [ERROR] {} is a POINT CLOUD, not a mesh - mesh it first (e.g. MeshFromPoints / "
                  "the scanner software), then load the mesh".format(os.path.basename(path)))
        else:
            print("  [ERROR] {} contains no mesh ({})".format(os.path.basename(path), ", ".join(sorted(skipped)) or "empty"))
        return None
    if skipped:
        print("  [INFO] {}: only the mesh(es) kept, ignored: {}".format(os.path.basename(path), ", ".join(sorted(skipped))))
    dname = name; k = 2
    while sc.doc.InstanceDefinitions.Find(dname) is not None:
        dname = "{} ({})".format(name, k); k += 1
    idx = sc.doc.InstanceDefinitions.Add(dname, "mesh: " + path, rg.Point3d.Origin, geos, ats)
    if idx < 0:
        print("  [ERROR] could not make a block from {}".format(path)); return None
    linked = False
    try:
        linked = bool(sc.doc.InstanceDefinitions.ModifySourceFilePath(
            idx, path, ro.InstanceDefinitionUpdateType.Linked))
    except Exception:
        try:
            fr = Rhino.FileIO.FileReference.CreateFromFullPath(path)
            linked = bool(sc.doc.InstanceDefinitions.ModifySourceFilePath(
                idx, fr, ro.InstanceDefinitionUpdateType.Linked, True))
        except Exception:
            linked = False
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
    files = rs.OpenFileNames("Load meshes as linked blocks",
                             "Meshes (*.stl;*.obj;*.ply)|*.stl;*.obj;*.ply|All files (*.*)|*.*||")
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
    try: Rhino.UI.StatusBar.ShowProgressMeter(0, len(files), "Loading meshes", True, True)
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
            busy = show_busy("Loading mesh {}/{}: {} ...".format(n + 1, len(files), name))
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
        print("[LoadMeshes] STOPPED by Esc - {} of {} mesh(s) loaded, the rest skipped".format(len(done), len(files)))
    else:
        print("[LoadMeshes] {} of {} mesh(s) loaded as blocks under layer '{}' (only the last one shown)".format(
            len(done), len(files), SCAN_ROOT))




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
    busy = show_busy("{}: preparing the meshes for clicking (first time only, big meshes take a few seconds)...".format(tool))
    try:
        prefetch_meshes(insts)
        for o0 in insts:
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
    finally:
        hide_busy(busy)


def view_constrain(gp):
    """Clicks on the mesh: the point comes from OUR ray on the meshes, Rhino
    only has to give a point on the cursor line. Unconstrained it uses the
    construction plane - above the horizon in Perspective there is none and
    Rhino shows the 'no entry' cursor and takes no click. A plane through the
    view target, facing the camera, is hit everywhere on the screen."""
    try:
        vp = sc.doc.Views.ActiveView.ActiveViewport
        gp.Constrain(rg.Plane(vp.CameraTarget, vp.CameraDirection), False)
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
    busy = show_busy("Reading mesh {} (once)...".format(idef.Name))
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


def is_click(res):
    return res == Rhino.Input.GetResult.Point or res == Rhino.Input.GetResult.Point2d


def pick_on_scans(gp, insts):
    """Point under the click on the VISIBLE meshes (front-most). The mesh tools
    ask Rhino only for the WINDOW point of the click (Get(.., True)): Rhino
    then needs no 3D point, so it never shows the 'no entry' cursor (above
    the horizon, after zooming in) - the ray is our own, through that pixel."""
    try:
        vp = gp.View().ActiveViewport
        p2 = gp.Point2d()
        r = vp.GetFrustumLine(float(p2.X), float(p2.Y))
        if isinstance(r, tuple):
            if not r[0]: return None
            line = r[1]
        else:
            line = r
        d0 = line.To - line.From
        return ray_hit_scans(rg.Point3d(line.From), d0, insts)
    except Exception:
        try:
            return ray_on_scans(gp.View(), gp.Point(), insts)
        except Exception:
            return None


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


class _ScanDraw(object):
    """Dynamic drawing while clicking: tags as pins, the mirrored copy (blue
    points) of the visible meshes, and (Fine) the gumball."""
    def __init__(self, insts, axis):
        self.insts = insts; self.axis = axis; self.dots = []; self.lines = []
        self.show_mirror = axis is not None
        self.pins_fn = None          # -> ([(point, text, colour, leader)], [(a, b) links])
        self.sph_fn = None           # -> (centres, radius): the AutoFit spheres
        self.sph_until = 0.0         # shown until this time (after a radius change) ...
        self.sph_on = False          # ... or all the time (SpherePreview)
        self.extra = None            # f(display, viewport, m): e.g. the Fine gumball

    def draw(self, sender, e):
        d = e.Display
        if self.show_mirror:
            objs = [sc.doc.Objects.FindId(obj.Id) for obj in self.insts]
            draw_mirror_points(d, self.axis, [o for o in objs if o is not None and scan_visible(o)])
        self.draw_spheres(d)
        try:
            m = xf_tuple(e.Viewport)
        except Exception:
            return
        try:
            pins, links = self.pins_fn() if self.pins_fn else ([], [])
            for (a, b) in links:
                xa, ya = to_screen(m, T(a)); xb, yb = to_screen(m, T(b))
                d.Draw2dLine(_pf(xa, ya), _pf(xb, yb), sd.Color.FromArgb(*GHOST_COL), 1.0)
            for (a, b) in self.lines:
                d.DrawDottedLine(a, b, sd.Color.FromArgb(255, 120, 0))
            for (p, txt, colr, lead) in pins:
                draw_pin(d, m, p, txt, colr, lead)
            for (p, txt) in self.dots:
                draw_pin(d, m, p, txt, PIN_COL)
            if self.extra:
                self.extra(d, e.Viewport, m)
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
                                "pairs), Enter = done, Esc = cancel all")
        else:
            gp.SetCommandPrompt("Center: pair {} - click the {} point ({} pair(s); then Fine / AutoFit), Enter = done, "
                                "Esc = cancel)".format(len(pairs) + 1, "LEFT" if st["left"] is None else "RIGHT", len(pairs)))
        gp.AcceptNothing(True)
        o_undo = o_sym = o_reset = -99
        if not locked:
            o_undo = gp.AddOption("UndoPair")
        o_cancel = gp.AddOption("Cancel")
        o_mir = gp.AddOption("MirrorPreview"); o_fine = gp.AddOption("Fine")
        o_auto = gp.AddOption("AutoFit")
        o_radv = ric.OptionDouble(autofit_radius(), 0.0, 1e6); o_rad = gp.AddOptionDouble("AutoFitRadius", o_radv)
        o_sph = gp.AddOptionToggle("SpherePreview", ric.OptionToggle(drw.sph_on, "Off", "On"))
        if locked:
            o_reset = gp.AddOption("ResetFine")
        else:
            o_sym = add_sym_option(gp)
        gp.DynamicDraw += drw.draw
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = gp.Get(False, True)                       # window point only - our own ray
        if res == Rhino.Input.GetResult.Option:
            oi = gp.Option().Index
            if oi == o_cancel:
                _apply(insts, applied, rg.Transform.Identity); sc.doc.Views.Redraw()
                print("[Center] cancelled - meshes back where they were"); return
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
                drw.show_mirror = not drw.show_mirror
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
                print("  click ON a visible mesh"); continue
            ok, inv = total().TryGetInverse()
            pb = rg.Point3d(hit[0]); pb.Transform(inv)       # back to base coordinates
            if st["left"] is None:
                st["left"] = pb
            else:
                pairs.append((st["left"], pb)); st["left"] = None
                st["T"] = solve(); commit()
                print("  pair {}: meshes turned {:.2f} deg and moved onto the symmetry plane".format(len(pairs), st["tilt"]))
                print_devs()
                if st["tilt"] > 15.0:
                    print("  [WARN] that is a big turn: are LEFT/RIGHT really across the car and is MirrorPlane "
                          "right? (UndoPair / Cancel)")
            sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Cancel:          # Esc = cancel, like the Cancel option
            _apply(insts, applied, rg.Transform.Identity); sc.doc.Views.Redraw()
            print("[Center] cancelled (Esc) - meshes back where they were"); return
        break
    if pairs:
        print_devs()
    if pairs or not _xf_is_identity(st["F"]):
        save_setup_prompt(insts, "Center")
    print("[Center] done ({} pair(s){})".format(len(pairs), "" if _xf_is_identity(st["F"]) else " + Fine"))


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
        d = lq.DistanceTo(mp.Point)
        if best is None or d < best[0]:
            n = rg.Vector3d(mesh.FaceNormals[mp.FaceIndex]); n.Transform(xf); n.Unitize()
            c = rg.Point3d(mp.Point); c.Transform(xf)
            best = (d, c, n)
    return None if best is None else (best[1], best[2])


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
            r2 = radius * radius
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
        return None, ("too few mesh points inside the tag spheres - larger AutoFitRadius?" if spheres
                      else "too few mesh points (are the meshes visible?)")
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
            return None, "the mirrored mesh does not land on the mesh (centre the pairs first)"
        med = median([r[3] for r in rows])
        keep = [r for r in rows if r[3] <= max(3.0 * med, 1e-6)]
        used = len(keep)
        if first is None: first = med
        last = med
        cx = sum(r[0].X for r in keep) / len(keep); cy = sum(r[0].Y for r in keep) / len(keep)
        cz = sum(r[0].Z for r in keep) / len(keep)
        cen = rg.Point3d(cx, cy, cz)
        # small move w (turn) + t (shift):  ((q-cen) x n).w + n.t = -(q-c).n
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
        A = D * A
        if w.Length < 1e-8 and t.Length < 1e-6 * max(1.0, med): break
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
    txt = ("[{}] ".format("spheres r={:g} around {} tags".format(radius, len(centres)) if spheres else "whole mesh") +
           "mirror-to-mesh distance (median) {} -> {}, {} of {} points used; "
           "meshes turned {:.4f} deg, centre moved {}".format(fl(first), fl(last), used, len(base), ang,
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


def _fine(insts, axis, drw, st, commit, print_devs, pairs, world, fit_fn=None):
    """Fine: drag an ARROW = move along that world axis, a RING = turn about
    that world axis, around a point on the symmetry plane. Every pixel of the
    drag is MovePerPx model units / DegPerPx degrees - very small steps.
    All chosen meshes move, also the hidden ones. Enter / Back = keep,
    Esc = undo the Fine moves of this round. fit_fn: the tool's own Fit
    (Align); without it AutoFit is the mirror fit of Center."""
    F0 = st["F"]
    st["fine"] = True                            # tags: r = L mirrored (with distance), R grey
    P = _fine_pivot(insts, axis, pairs, world)
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
                H["hot"] = _fine_handle_at(ev.Viewport, P, sx, sy)
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
          "Steps per pixel: MovePerPx / DegPerPx. Enter = keep, Esc = undo this Fine round.")
    try:
        while True:
            gp = ric.GetPoint()
            gp.SetCommandPrompt("Fine: drag an arrow (move) or a ring (turn)  - Enter = keep, Esc = undo Fine")
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
            gp.DynamicDraw += drw.draw
            gp.MouseDown += on_down; gp.MouseMove += on_move
            gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0); gp.PermitObjectSnap(False)
            view_constrain(gp)                           # plane facing the camera, made fresh every click
            H["drag"] = None
            res = gp.Get(True)                           # mouse button UP (the drag needs the normal get)
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
    busy = show_busy("AutoFit: fitting the mirror to the mesh...")
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
ALIGN_FIT_ITERS = 15


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


def _xf_from_rigid(R, t):
    X = rg.Transform(1.0)
    for i in range(3):
        for j in range(3):
            X[i, j] = R[i][j]
        X[i, 3] = t[i]
    return X


def _update_setups_after_align(moved_ids):
    """Every saved setup keeps the alignment: the moved meshes get, in each
    setup, the place they now have relative to a mesh that did not move."""
    data = _read_setups()
    if not data: return
    cur = dict((str(o.Id), o) for o in all_scan_insts())
    def mat(rec):
        m = rec["matrix_row_major"]; X = rg.Transform(1.0)
        for i in range(4):
            for j in range(4):
                X[i, j] = m[i * 4 + j]
        return X
    changed = False
    for name, S in data.items():
        if name.startswith("_"): continue
        ref = None
        for sid, o in cur.items():
            if sid in moved_ids: continue
            rec = _setup_record(S, o)
            if rec:
                ok, inv = o.InstanceXform.TryGetInverse()
                if ok:
                    ref = mat(rec) * inv; break
        if ref is None: continue
        for sid in moved_ids:
            o = cur.get(sid)
            if o is None: continue
            x = ref * o.InstanceXform
            S[_scan_name(o)] = {"layer": "", "matrix_row_major": [x[i, j] for i in range(4) for j in range(4)],
                                "block": o.InstanceDefinition.Name, "file": os.path.basename(obj_src(o))}
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
        print("[Align] needs two meshes at least"); return
    prepare_scans(insts, "Align")
    mir_axis = pause_scanmirror()
    try:
        _scan_align(insts)
    finally:
        resume_scanmirror(mir_axis)


def _scan_align(insts):
    tag = "[Align]"
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
        R, t = best_rigid(src, dst)
        return _xf_from_rigid(R, t)

    def devs():
        return [world(m_).DistanceTo(f_) for (f_, m_) in pairs]

    def print_devs(short=False):
        if not pairs: return
        d = devs()
        print("  pairs off by: " + "  ".join("{} {}".format(k + 1, fl(x)) for k, x in enumerate(d)) +
              "   worst {}".format(fl(max(d))))

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
            for (f_, m_) in pairs:
                lc = world(m_); lc.Transform(inv)              # the pair in this scan's own coordinates
                near = [q for q in lp if (q.X - lc.X) ** 2 + (q.Y - lc.Y) ** 2 + (q.Z - lc.Z) ** 2 <= r2]
                step = max(1, len(near) // AUTOFIT_PER_TAG)
                for q0 in near[::step]:
                    q = rg.Point3d(q0); q.Transform(geo[1]); q.Transform(tinv)
                    pts.append(q)                              # base coordinates of the moving scans
        if len(pts) < 30:
            print("  too few mesh points inside the spheres - larger AutoFitRadius?"); return
        busy = show_busy("Align: Fit...")
        keep = []
        try:
            for it in range(ALIGN_FIT_ITERS):
                src = []; dst = []; ds = []
                for pb in pts:
                    w = world(pb)
                    q = _scan_closest(geos, w)
                    if q is None: continue
                    q = q[0]
                    src.append(T(w)); dst.append(T(q)); ds.append(w.DistanceTo(q))
                if len(src) < 10: break
                lim = 3.0 * sorted(ds)[len(ds) // 2] + 1e-9           # the overlap only (not the free ends)
                keep = [(a, b) for a, b, d in zip(src, dst, ds) if d <= lim]
                if len(keep) < 10: break
                R, t = best_rigid([a for a, b in keep], [b for a, b in keep])
                st["F"] = _xf_from_rigid(R, t) * st["F"]
                if max(abs(R[i][j] - (1.0 if i == j else 0.0)) for i in range(3) for j in range(3)) < 1e-7 \
                        and _len(t) < 1e-6 * max(1.0, max(abs(c) for c in t)):
                    break
            commit()
        finally:
            hide_busy(busy)
        print("  Fit done ({} points in {} spheres of {})".format(len(keep), len(pairs), fl(r)))
        print_devs()

    while True:
        gp = ric.GetPoint()
        locked = not _xf_is_identity(st["F"])
        if locked:
            gp.SetCommandPrompt("Align: Fit / Fine done <right click = done, ResetFine, Esc = cancel>")
        else:
            gp.SetCommandPrompt("Align pair {}: point on the mesh that {} <right click = done, Esc = cancel>".format(
                len(pairs) + 1, "STAYS" if st["first"] is None else "MOVES"))
        gp.AcceptNothing(True)
        o_undo = o_fit = o_fine = o_reset = -99
        if not locked:
            o_undo = gp.AddOption("UndoPair")
        o_rad = o_sph = -99
        if len(pairs) >= 3:
            o_fit = gp.AddOption("Fit"); o_fine = gp.AddOption("Fine")
            o_radv = ric.OptionDouble(autofit_radius(), 0.0, 1e6); o_rad = gp.AddOptionDouble("AutoFitRadius", o_radv)
            o_sph = gp.AddOptionToggle("SpherePreview", ric.OptionToggle(drw.sph_on, "Off", "On"))
        if locked:
            o_reset = gp.AddOption("ResetFine")
        gp.DynamicDraw += drw.draw
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = gp.Get(False, True)
        if res == Rhino.Input.GetResult.Option:
            oi = gp.Option().Index
            if oi == o_undo:
                if st["first"] is not None: st["first"] = None
                elif pairs: pairs.pop()
                if not pairs:
                    st["fixed"] = st["moving"] = None
                st["T"] = solve(); commit(); print_devs()
            elif oi == o_fit:
                fit()
            elif oi == o_fine:
                _fine(st["set"], 0, drw, st, commit, print_devs, [], world, fit_fn=fit)
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
            hit = pick_on_scans(gp, insts)
            if hit is None:
                print("  click ON a visible mesh"); continue
            p, obj = hit
            if st["first"] is None:                            # a point on the scan that stays
                if st["fixed"] is not None and str(obj.Id) != st["fixed"]:
                    print("  the first point of a pair goes on the mesh that STAYS"); continue
                st["first"] = rg.Point3d(p)
                if st["fixed"] is None: st["fixed"] = str(obj.Id); st["fixed_obj"] = obj
            else:                                              # the same point on the scan that moves
                if str(obj.Id) == st["fixed"]:
                    print("  the second point goes on the mesh that MOVES"); continue
                if st["moving"] is None:
                    branch = align_branch(obj.Id)
                    if st["fixed"] in branch:
                        print("  this mesh carries the one that stays (it was aligned to it) - align the other way"); continue
                    st["moving"] = str(obj.Id); st["ids"] = branch; st["moving_obj"] = obj
                    st["set"] = [o for o in insts if str(o.Id) in branch]
                elif str(obj.Id) not in st["ids"]:
                    print("  the second point goes on the same moving mesh as before"); continue
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
            print(tag + " cancelled - meshes back where they were"); return
        break
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
    _update_setups_after_align(st["ids"])
    print_devs()
    print(tag + " done: {} mesh(s) moved{}".format(len(st["set"]), "" if len(st["set"]) == 1 else
                                                    " (the moved mesh carries the ones aligned to it)"))



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
        resume_scanmirror(mir_axis)


def _scan_floor(axis, insts):
    pts = []
    drw = _ScanDraw(insts, None)
    while True:
        gp = ric.GetPoint()
        gp.SetCommandPrompt("Floor: point {} on the floor (at least 3, Enter = done)".format(len(pts) + 1))
        gp.AcceptNothing(True)
        o_sym = add_sym_option(gp, ("YZ", "XZ"))
        gp.DynamicDraw += drw.draw
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = gp.Get(False, True)
        if res == Rhino.Input.GetResult.Option:
            if gp.Option().Index == o_sym:
                axis = set_sym_from_option(gp, ("YZ", "XZ"))
            continue
        if is_click(res):
            hit = pick_on_scans(gp, insts)
            if hit is None: print("  click ON a visible mesh"); continue
            pts.append(hit[0]); drw.dots.append((hit[0], "F{}".format(len(pts)))); sc.doc.Views.Redraw()
            continue
        break
    if len(pts) < 3:
        print("[Floor] needs at least 3 points - nothing changed"); return
    # the points must span an AREA (e.g. both sills), not one line
    dmax = max(a.DistanceTo(b) for a in pts for b in pts)
    amax = 0.0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            for k in range(j + 1, len(pts)):
                amax = max(amax, rg.Vector3d.CrossProduct(pts[j] - pts[i], pts[k] - pts[i]).Length)
    if dmax < 1e-9 or amax < 0.05 * dmax * dmax:
        print("[Floor] the points lie almost on ONE line - click the floor on BOTH sides of the car "
              "(left and right sill, front and rear). Nothing changed."); return
    ref = None
    gp = ric.GetPoint()
    gp.SetCommandPrompt("Floor: click the point that becomes front/back 0 (Enter = keep front/back)")
    gp.AcceptNothing(True); gp.DynamicDraw += drw.draw; gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
    if is_click(gp.Get(False, True)):
        hit = pick_on_scans(gp, insts)
        if hit is not None: ref = hit[0]
    fit = rg.Plane.FitPlaneToPoints(pts)
    plane = fit[1] if isinstance(fit, tuple) else fit
    m = plane.Normal
    up = rg.Vector3d(0, 0, 1)
    if m * up < 0: m = -m
    e = [0.0, 0.0, 0.0]; e[axis] = 1.0; E = rg.Vector3d(e[0], e[1], e[2])
    mp = m - E * (m * E)                         # the part a turn about E can fix
    roll = math.degrees(math.asin(max(-1.0, min(1.0, m * E))))
    if mp.Length < 0.5:                          # floor plane more than 60 deg off level
        print("[Floor] the clicked points do not look like a floor (plane {:.0f} deg off level) - "
              "check the points / MirrorPlane. Nothing changed.".format(math.degrees(math.acos(min(1.0, mp.Length)))))
        return
    mp.Unitize()
    ang = math.atan2((rg.Vector3d.CrossProduct(mp, up)) * E, mp * up)
    g = plane.Origin
    rot = rg.Transform.Rotation(ang, E, g)
    g2 = rg.Point3d(g); g2.Transform(rot)
    T = rg.Transform.Translation(-up * g2.Z) * rot
    fwd = rg.Vector3d.CrossProduct(up, E); fwd.Unitize()
    if ref is not None:
        r2 = rg.Point3d(ref); r2.Transform(T)
        T = rg.Transform.Translation(-fwd * (r2.X * fwd.X + r2.Y * fwd.Y + r2.Z * fwd.Z)) * T
    for obj in insts:
        sc.doc.Objects.Transform(obj.Id, T, True)
    sc.doc.Views.Redraw()
    print("[Floor] tilted {:.3f} deg about the axis across the symmetry plane, floor at 0{}".format(
        math.degrees(ang), "" if ref is None else ", front/back 0 at the clicked point"))
    if abs(roll) > 0.05:
        print("  [INFO] the floor also leans {:.2f} deg sideways - NOT corrected (it would break the symmetry); "
              "check the Center pairs or the meshes".format(roll))
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
        print("  (plain meshes carry their position in the mesh itself - setups are for mesh blocks only)")
        return
    dflt = sc.sticky.get("M2CA_setup", "front")
    gs = ric.GetString()
    gs.SetCommandPrompt("{} done. Remember these mesh positions under a NAME (e.g. front, rear) to bring them back "
                        "later with MeshSetup - type a name + Enter, Enter alone = '{}'".format(what, dflt))
    gs.AcceptNothing(True)
    gs.AddOption("DontSave")
    res = gs.Get()
    if res == Rhino.Input.GetResult.Nothing:
        name = dflt
    elif res == Rhino.Input.GetResult.String:
        name = (gs.StringResult() or "").strip() or dflt
    else:                                          # DontSave / Esc
        print("  positions not saved as a setup (the meshes stay where they are)"); return
    sc.sticky["M2CA_setup"] = name
    data = _read_setups()
    st = {"_saved": time.time()}
    for (o, lp) in scan_instances():
        x = o.InstanceXform
        st[_scan_name(o)] = {"layer": lp, "matrix_row_major": [x[i, j] for i in range(4) for j in range(4)],
                             "block": o.InstanceDefinition.Name, "file": os.path.basename(obj_src(o))}
    data[name] = st
    if not _setup_files():
        print("  [Setup] not saved: these mesh blocks do not know their mesh files (load them with LoadMeshes)")
        return
    where = _write_setups(data)
    n = len([k for k in st if not k.startswith("_")])
    print("[Setup] '{}' saved: {} mesh(s), in {}".format(name, n, " and ".join(where) or "-"))


def scan_setup():
    """MeshSetup: apply a saved setup (all mesh blocks jump there), save the
    present positions under a name, or remove a setup."""
    data = _read_setups()
    names = sorted(data.keys())
    SAVE = "<save the present positions as ...>"; REMOVE = "<remove a setup ...>"
    items = names + [SAVE] + ([REMOVE] if names else [])
    choice = rs.ListBox(items, "Mesh setups (positions of the mesh blocks)", "MeshSetup")
    if choice is None: return
    if choice == SAVE:
        save_setup_prompt([], "Setup"); return
    if choice == REMOVE:
        gone = rs.ListBox(names, "Remove which setup?", "MeshSetup - remove")
        if not gone: return
        if rs.MessageBox("Remove the setup '{}'?\n(The meshes stay where they are.)".format(gone), 4 | 32,
                         "MeshSetup - remove") != 6:
            return
        del data[gone]
        where = _write_setups(data)
        print("[Setup] '{}' removed from {}".format(gone, " and ".join(where) or "-"))
        return
    st = data[choice]; n = 0; missing = []
    moves = []; ref = None
    for (o, lp) in scan_instances():
        rec = _setup_record(st, o)
        ok, inv = o.InstanceXform.TryGetInverse()
        if not ok: continue
        if not rec:
            missing.append(o); continue
        m = rec["matrix_row_major"]
        X = rg.Transform(1.0)
        for i in range(4):
            for j in range(4):
                X[i, j] = m[i * 4 + j]
        moves.append((o, X * inv))
        if ref is None: ref = X * inv
    for o in missing:                     # not in this setup (loaded later): moves with the structure
        if ref is not None: moves.append((o, ref))
    for (o, D) in moves:
        sc.doc.Objects.Transform(o.Id, D, True); n += 1
    missing = [o.InstanceDefinition.Name for o in missing] if ref is None else []
    sc.sticky["M2CA_setup"] = choice
    sc.doc.Views.Redraw()
    print("[Setup] '{}' applied to {} mesh(s){}".format(choice, n,
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
    print("[MeshMirror] {} mesh(s) mirrored, shown as blue points (MeshMirror again = off)".format(n))


SCAN_TOOL_LABEL = {"LoadMeshes": "Load meshes", "MeshAlign": "Align", "MeshCenter": "Center", "MeshFloor": "Floor",
                   "MeshSetup": "Setups", "MeshMirror": "Mirror on/off"}
SCAN_TOOLS = (("LoadMeshes", load_scans), ("MeshAlign", scan_align), ("MeshCenter", scan_center),
              ("MeshFloor", scan_floor), ("MeshSetup", scan_setup), ("MeshMirror", scan_mirror))


