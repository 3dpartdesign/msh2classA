# >>> dev-only
from rhino.scan_tools import *  # noqa: F401,F403
# <<< dev-only
# ---- PHOTOS: front / rear / side / top pictures as guides -------------------
# A photo is a Rhino picture frame on its own layer under PHOTO_ROOT. Its
# place, turn and size are always read back from the frame's corners, so you
# may move or scale a photo by hand at any time - nothing to keep in step.
#
# What photos are for (the scan/mesh size from a measured curve - MeshScale -
# always comes first; a photo is only fitted to the car):
#   * scans/meshes with gaps: front, rear and a spoiler scanned apart, nothing
#     between - the side photo shows where the parts sit (MeshToPhoto)
#   * scans/meshes without a floor: the photo's tyre contact gives it
#   * a photo is centred (front / rear / top: LEFT/RIGHT pairs), levelled
#     (tyre contact), scaled (two points + a known distance) or fitted onto
#     the scans/meshes (PhotoToMesh: point on the photo -> same point on the
#     scan/mesh, the photo is turned / moved / scaled in its own plane)
# Saved setups keep the photos too (their corners).
PHOTO_ROOT = "Reference Photos- Msh2classA"
PHOTO_KEY = "msh2classA_photo"            # user text on the frame: source file
PHOTO_ROLE_KEY = "msh2classA_photo_role"  # Front / Rear / Left / Right / Top
PHOTO_SIZE_KEY = "msh2classA_photo_size"  # "W H": the frame as made (its own units)
PHOTO_STATE_KEY = "msh2classA_photo_state"  # "centred,levelled"
PHOTO_ROLES = ("Front", "Rear", "Right", "Left", "Top")
# where a new photo goes: BEHIND the car seen from that view (a blueprint),
# its bottom on the floor (z = 0), across the middle
#   role: (middle of the picture's bottom edge (mm), picture x axis, picture y axis, width mm)
#   Front view looks along +Y, Back along -Y, Right along -X, Left along +X, Top down
PHOTO_PLACE = {"Front": ((0.0, 3500.0, 0.0), (1, 0, 0), (0, 0, 1), 2000.0),
               "Rear": ((0.0, -3500.0, 0.0), (-1, 0, 0), (0, 0, 1), 2000.0),
               "Right": ((-2500.0, 0.0, 0.0), (0, 1, 0), (0, 0, 1), 5000.0),
               "Left": ((2500.0, 0.0, 0.0), (0, -1, 0), (0, 0, 1), 5000.0),
               "Top": ((0.0, 0.0, -600.0), (1, 0, 0), (0, 1, 0), 5000.0)}


def image_size(path):
    """(width, height) in pixels from the PNG / JPEG header - no image library
    (the same on Windows and Mac). None: not readable."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                import struct
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:2] == b"\xff\xd8":
                import struct
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff": b = f.read(1)
                    while b == b"\xff": b = f.read(1)
                    if not b: return None
                    marker = ord(b) if not isinstance(b, int) else b
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return int(w), int(h)
                    ln = struct.unpack(">H", f.read(2))[0]
                    f.seek(ln - 2, 1)
    except Exception:
        return None
    return None


def photo_objects():
    """[(object, layer full path)] of the photos."""
    s = ro.ObjectEnumeratorSettings()
    s.HiddenObjects = True; s.LockedObjects = True
    out = []
    for obj in sc.doc.Objects.GetObjectList(s):
        try:
            if obj.Attributes.GetUserString(PHOTO_KEY):
                out.append((obj, sc.doc.Layers[obj.Attributes.LayerIndex].FullPath))
        except Exception:
            pass
    return out


def photo_name(obj):
    return sc.doc.Layers[obj.Attributes.LayerIndex].Name


def _photo_size(obj):
    try:
        w, h = [float(v) for v in obj.Attributes.GetUserString(PHOTO_SIZE_KEY).split()]
        return w, h
    except Exception:
        return 1.0, 1.0


def photo_frame(obj):
    """(O, X, Y): world corners of the picture - lower-left, lower-right,
    upper-left - read from the frame itself (a hand move is fine)."""
    g = obj.Geometry
    try:
        srf = g.Faces[0].UnderlyingSurface() if hasattr(g, "Faces") else g
        du = srf.Domain(0); dv = srf.Domain(1)
        O = srf.PointAt(du.Min, dv.Min); X = srf.PointAt(du.Max, dv.Min); Y = srf.PointAt(du.Min, dv.Max)
        return O, X, Y
    except Exception:
        pass
    m = g                                                # a frame made as a mesh: vertices 0, 1, 3
    return rg.Point3d(m.Vertices[0]), rg.Point3d(m.Vertices[1]), rg.Point3d(m.Vertices[3])


def photo_axes(obj):
    """(O, u, v, n, px): origin, unit axes of the picture, its normal, and
    the size of one 'frame unit' now (the scale)."""
    O, X, Y = photo_frame(obj)
    u = X - O; v = Y - O
    w, h = _photo_size(obj)
    sx = u.Length / w if w else 1.0
    u.Unitize(); v.Unitize()
    n = rg.Vector3d.CrossProduct(u, v); n.Unitize()
    return O, u, v, n, sx


def photo_state(obj):
    s_ = obj.Attributes.GetUserString(PHOTO_STATE_KEY) or ""
    return set(x for x in s_.split(",") if x)


def _set_photo_state(obj, st_):
    o = sc.doc.Objects.FindId(obj.Id)
    a = o.Attributes.Duplicate(); a.SetUserString(PHOTO_STATE_KEY, ",".join(sorted(st_)))
    sc.doc.Objects.ModifyAttributes(o, a, True)


def photo_role(obj):
    return obj.Attributes.GetUserString(PHOTO_ROLE_KEY) or "Front"


def photo_hit(gp, photos):
    """The click on a photo (its plane, inside the picture) -> (world point,
    photo object) or None. The scans/meshes in front of a photo do not hide it:
    the picture is behind the car on purpose."""
    ray = click_ray(gp)
    if ray is None: return None
    a_, d0 = ray
    best = None
    for (o0, lp) in photos:
        o = sc.doc.Objects.FindId(o0.Id)
        if o is None or not o.Visible or not sc.doc.Layers[o.Attributes.LayerIndex].IsVisible: continue
        O, u, v, n, sx = photo_axes(o)
        dn = d0 * n
        if abs(dn) < 1e-12: continue
        t = ((O - a_) * n) / dn
        if t <= 0: continue
        p = a_ + d0 * t
        O_, X_, Y_ = photo_frame(o)                    # the edges as they are (whatever the frame's own units)
        x = (p - O) * u; y = (p - O) * v
        tl = 1e-6 * max(1.0, O_.DistanceTo(X_))
        if -tl <= x <= O_.DistanceTo(X_) + tl and -tl <= y <= O_.DistanceTo(Y_) + tl:
            if best is None or t < best[0]: best = (t, p, o)
    return None if best is None else (best[1], best[2])


def _move_photo(o, D):
    sc.doc.Objects.Transform(o.Id, D, True)


# ---- LoadPhotos -------------------------------------------------------------
def load_photos():
    """LoadPhotos: pick photos (PNG / JPEG); each asks its view (Front, Rear,
    Right, Left, Top) and becomes a picture frame on its own layer under
    'Reference Photos- Msh2classA', BEHIND the car in that view, roughly the
    size of a car (MeshPhoto Scale / PhotoToMesh give the true size)."""
    files = rs.OpenFileNames("Load photos (front / rear / side / top)",
                             "Photos (*.jpg;*.jpeg;*.png)|*.jpg;*.jpeg;*.png|All files (*.*)|*.*||")
    if not files: return
    old_layer = sc.doc.Layers.CurrentLayerIndex      # back at the end: never a photo layer for what you draw next
    root = ensure_layer(PHOTO_ROOT)
    made = 0
    for path in files:
        wh = image_size(path)
        if wh is None:
            print("[LoadPhotos] {}: not a PNG / JPEG I can read - skipped".format(os.path.basename(path))); continue
        role = rs.ListBox(list(PHOTO_ROLES), "Which view is {}?".format(os.path.basename(path)),
                          "msh2classA LoadPhotos", PHOTO_ROLES[0])
        if not role:
            print("[LoadPhotos] {}: skipped".format(os.path.basename(path))); continue
        mid, ax, ay, width = PHOTO_PLACE[role]
        k = 1.0 / _mm_per_unit()
        W = width * k; H = W * wh[1] / float(wh[0])
        X = rg.Vector3d(*ax); Y = rg.Vector3d(*ay)
        o = rg.Point3d(*[c * k for c in mid]) - X * (W / 2.0)       # lower-left corner
        if role == "Top": o = o - Y * (H / 2.0)
        pl = rg.Plane(o, X, Y)
        name = os.path.splitext(os.path.basename(path))[0]
        li = ensure_layer(PHOTO_ROOT + "::" + name)
        sc.doc.Layers.SetCurrentLayerIndex(li, True)
        try:
            fid = sc.doc.Objects.AddPictureFrame(pl, path, False, W, H, True, False)
        except Exception as e:
            print("[LoadPhotos] {}: Rhino could not make a picture frame ({})".format(name, e)); continue
        obj = sc.doc.Objects.FindId(fid)
        if obj is None:
            print("[LoadPhotos] {}: Rhino could not make a picture frame".format(name)); continue
        a = obj.Attributes.Duplicate()
        a.LayerIndex = li
        a.SetUserString(PHOTO_KEY, path); a.SetUserString(PHOTO_ROLE_KEY, role)
        a.SetUserString(PHOTO_SIZE_KEY, "{} {}".format(repr(W), repr(H)))
        sc.doc.Objects.ModifyAttributes(obj, a, True)
        made += 1
        print("[LoadPhotos] {} ({} x {} px) as the {} photo".format(name, wh[0], wh[1], role))
    try: sc.doc.Layers.SetCurrentLayerIndex(old_layer, True)
    except Exception: sc.doc.Layers.SetCurrentLayerIndex(root, True)
    sc.doc.Views.Redraw()
    if made:
        refresh_layer_panel()
        print("  next: MeshPhoto - Scale (two points + a known distance) or PhotoToMesh (onto the scans/meshes), "
              "then Center (front / rear / top) and Floor (tyre contact)")


# ---- in-plane fits -------------------------------------------------------------
def _in_plane(p, O, u, v):
    return ((p - O) * u, (p - O) * v)


def _sim2(src, dst, scale):
    """2D similarity src -> dst (lists of (x, y)): (cos*s, sin*s, tx, ty)."""
    n = float(len(src))
    if n == 1:
        return 1.0, 0.0, dst[0][0] - src[0][0], dst[0][1] - src[0][1]
    ca = [sum(p[i] for p in src) / n for i in (0, 1)]; cb = [sum(p[i] for p in dst) / n for i in (0, 1)]
    sxx = sxy = den = 0.0
    for a, b in zip(src, dst):
        ax_, ay_ = a[0] - ca[0], a[1] - ca[1]; bx_, by_ = b[0] - cb[0], b[1] - cb[1]
        sxx += ax_ * bx_ + ay_ * by_; sxy += ax_ * by_ - ay_ * bx_; den += ax_ * ax_ + ay_ * ay_
    ang = math.atan2(sxy, sxx)
    s = (math.hypot(sxx, sxy) / den) if (scale and den > 1e-18) else 1.0
    c, si = math.cos(ang) * s, math.sin(ang) * s
    tx = cb[0] - (c * ca[0] - si * ca[1]); ty = cb[1] - (si * ca[0] + c * ca[1])
    return c, si, tx, ty


def _plane_xf(O, u, v, n, sim):
    """The 3D transform of a 2D similarity in the plane (O, u, v)."""
    c, si, tx, ty = sim
    # world = O + u*x + v*y + n*z ; in-plane map (x, y) -> (c x - si y + tx, si x + c y + ty), z kept
    B = rg.Transform(1.0); Bi = rg.Transform(1.0)
    for i, (vec) in enumerate((u, v, n)):
        B[0, i] = vec.X; B[1, i] = vec.Y; B[2, i] = vec.Z
        Bi[i, 0] = vec.X; Bi[i, 1] = vec.Y; Bi[i, 2] = vec.Z
    B[0, 3] = O.X; B[1, 3] = O.Y; B[2, 3] = O.Z
    ToL = Bi * rg.Transform.Translation(-rg.Vector3d(O))
    S = rg.Transform(1.0)
    S[0, 0] = c; S[0, 1] = -si; S[1, 0] = si; S[1, 1] = c; S[0, 3] = tx; S[1, 3] = ty
    S[2, 2] = math.hypot(c, si)                        # a scaled photo stays a flat picture
    return B * S * ToL


def _sym_axis():
    """The symmetry plane of MeshCenter (else YZ: x = 0)."""
    d = _norm_frame(frame_get())
    ax = (d.get("center") or {}).get("axis")
    return ax if ax in (0, 1, 2) else 0


# ---- MeshPhoto ---------------------------------------------------------------
def photo_tools():
    """MeshPhoto: Scale / Center / Floor / PhotoToMesh / MeshToPhoto (see the help)."""
    photos = photo_objects()
    if not photos:
        print("[MeshPhoto] no photos - LoadPhotos first"); return
    single_view()
    gs = ric.GetString()
    gs.SetCommandPrompt("MeshPhoto: Scale = two points + a known distance, Center = LEFT/RIGHT pairs (front / rear / "
                        "top photo), Floor = tyre contact points, PhotoToMesh = photo onto the scans/meshes, MeshToPhoto = "
                        "a scan/mesh onto the photo (parts with gaps) <Esc = end MeshPhoto>")
    names = ("Scale", "Center", "Floor", "PhotoToMesh", "MeshToPhoto")
    idx = dict((gs.AddOption(n), n) for n in names)
    res = gs.Get()
    if res != Rhino.Input.GetResult.Option:
        print("[MeshPhoto] ended"); return
    what = idx.get(gs.Option().Index)
    insts = all_scan_insts()
    if what in ("PhotoToMesh", "MeshToPhoto"):
        if not insts:
            print("[MeshPhoto] no scan/mesh blocks - LoadMeshes first"); return
        prepare_scans(insts, "MeshPhoto")
    drw = _ScanDraw(insts, None); drw.show_mirror = False
    try:
        {"Scale": _photo_scale, "Center": _photo_center, "Floor": _photo_floor,
         "PhotoToMesh": _photo_to_mesh, "MeshToPhoto": _mesh_to_photo}[what](photos, insts, drw)
    finally:
        _ScanDraw.off_all()


def _pick_photo_points(photos, drw, prompt, need, most=99, one_photo=None, extra=()):
    """Clicks on ONE photo -> (photo, [points]) or None (Esc)."""
    pts = []; ph = one_photo
    pins = []
    drw.pins_fn = lambda: (pins, [])
    while True:
        gp = ric.GetPoint()
        gp.SetCommandPrompt(prompt.format(n=len(pts) + 1) + (" <Enter = done" if len(pts) >= need else " <") +
                            ", UndoPt, Esc = cancel, nothing changes>")
        gp.AcceptNothing(True)
        o_undo = gp.AddOption("UndoPt") if pts else -99
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = scan_get(gp)
        if res == Rhino.Input.GetResult.Cancel: return None
        if res == Rhino.Input.GetResult.Option:
            if gp.Option().Index == o_undo and pts:
                pts.pop(); pins.pop()
                if not pts and one_photo is None: ph = None
                sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Nothing:
            if len(pts) >= need: return ph, pts
            print("  {} point(s) needed".format(need)); continue
        if is_click(res):
            hit = photo_hit(gp, [(ph, None)] if ph is not None else photos)
            if hit is None:
                print("  click ON the photo{}".format("" if ph is None else " " + photo_name(ph))); continue
            ph = hit[1]
            pts.append(hit[0]); pins.append((hit[0], "P{}".format(len(pts)), PIN_COL, True)); sc.doc.Views.Redraw()
            if len(pts) >= most: return ph, pts


def _photo_scale(photos, insts, drw):
    r = _pick_photo_points(photos, drw, "Photo Scale: point {n} of 2 on the photo", 2, 2)
    if r is None:
        print("[MeshPhoto] Scale cancelled - nothing changed"); return
    ph, (p1, p2) = r
    L = p1.DistanceTo(p2)
    gn = ric.GetNumber()
    gn.SetCommandPrompt("True distance between the two points (now {}) in {} <Esc = cancel>".format(fl(L), doc_unit_name()))
    gn.SetDefaultNumber(L); gn.SetLowerLimit(1e-9, True)
    if gn.Get() != Rhino.Input.GetResult.Number:
        print("[MeshPhoto] Scale cancelled - nothing changed"); return
    f = gn.Number() / L
    O, u, v, n, sx = photo_axes(ph)
    st_ = photo_state(ph)
    piv = p1
    if "centred" in st_ or "levelled" in st_:
        # keep what Center / Floor set: scale about the point of the picture's plane on the symmetry plane / floor
        piv = rg.Point3d(p1)
        if "levelled" in st_: piv.Z = 0.0
        if "centred" in st_:
            ax = _sym_axis(); c = [piv.X, piv.Y, piv.Z]; c[ax] = 0.0; piv = rg.Point3d(c[0], c[1], c[2])
    _move_photo(ph, rg.Transform.Scale(piv, f))
    sc.doc.Views.Redraw()
    print("[MeshPhoto] {} scaled x{:.6f} - the two points are {} apart now".format(photo_name(ph), f, fl(gn.Number())))


def _photo_center(photos, insts, drw):
    r = _pick_photo_points(photos, drw, "Photo Center: {n}. point - click LEFT, RIGHT, LEFT, RIGHT ... (pairs of the "
                           "same feature, e.g. mirrors, lamps)", 2)
    if r is None:
        print("[MeshPhoto] Center cancelled - nothing changed"); return
    ph, pts = r
    if len(pts) % 2:
        print("  (the last, unpaired point is dropped)"); pts = pts[:-1]
    ax = _sym_axis()
    E = rg.Vector3d(*[1.0 if i == ax else 0.0 for i in range(3)])
    O, u, v, n, sx = photo_axes(ph)
    if abs(n * E) > 0.5:
        print("[MeshPhoto] the {} photo looks along the symmetry plane's normal - a side photo has no LEFT/RIGHT: use "
              "Floor / PhotoToMesh. Nothing changed.".format(photo_name(ph))); return
    # turn about the normal so every LEFT->RIGHT line runs across the car (along E), then move the middles onto 0
    e2 = _in_plane(rg.Point3d(0, 0, 0) + E, rg.Point3d(0, 0, 0), u, v)
    sw = cw = 0.0
    for i in range(0, len(pts), 2):
        a = _in_plane(pts[i], O, u, v); b = _in_plane(pts[i + 1], O, u, v)
        dx, dy = b[0] - a[0], b[1] - a[1]
        phi = math.atan2(e2[1], e2[0]) - math.atan2(dy, dx)
        w = math.hypot(dx, dy)
        sw += w * math.sin(2 * phi); cw += w * math.cos(2 * phi)
    th = 0.5 * math.atan2(sw, cw)
    mids = [rg.Point3d((pts[i].X + pts[i + 1].X) / 2, (pts[i].Y + pts[i + 1].Y) / 2, (pts[i].Z + pts[i + 1].Z) / 2)
            for i in range(0, len(pts), 2)]
    mc = rg.Point3d(sum(p.X for p in mids) / len(mids), sum(p.Y for p in mids) / len(mids),
                    sum(p.Z for p in mids) / len(mids))
    R = rg.Transform.Rotation(th, n, mc)
    mc2 = rg.Point3d(mc); mc2.Transform(R)
    off = [mc2.X, mc2.Y, mc2.Z][ax]
    T = rg.Transform.Translation(-E * off) * R
    st_ = photo_state(ph)
    if "levelled" in st_ and abs(th) > 1e-9:
        print("  the turn moved the photo's floor - Floor again (Center first, then Floor: only up / down)")
        st_.discard("levelled")
    _move_photo(ph, T)
    st_.add("centred"); _set_photo_state(ph, st_)
    sc.doc.Views.Redraw()
    worst = 0.0
    for m in mids:
        q = rg.Point3d(m); q.Transform(T); worst = max(worst, abs([q.X, q.Y, q.Z][ax]))
    print("[MeshPhoto] {} centred: turned {:.3f} deg, middles of the {} pair(s) on the symmetry plane (worst {})".format(
        photo_name(ph), math.degrees(th), len(mids), fl(worst)))


def _photo_floor(photos, insts, drw):
    r = _pick_photo_points(photos, drw, "Photo Floor: point {n} where a tyre touches the floor", 2)
    if r is None:
        print("[MeshPhoto] Floor cancelled - nothing changed"); return
    ph, pts = r
    O, u, v, n, sx = photo_axes(ph)
    up = rg.Vector3d(0, 0, 1)
    if abs(n * up) > 0.5:
        print("[MeshPhoto] a top photo has no floor line - nothing changed"); return
    st_ = photo_state(ph)
    T = rg.Transform.Identity
    th = 0.0
    if "centred" not in st_:
        # turn about the normal so the contact line is level (least-squares line through the points)
        P = [_in_plane(p, O, u, v) for p in pts]
        cx = sum(p[0] for p in P) / len(P); cy = sum(p[1] for p in P) / len(P)
        sxx = sum((p[0] - cx) ** 2 for p in P); sxy = sum((p[0] - cx) * (p[1] - cy) for p in P)
        syy = sum((p[1] - cy) ** 2 for p in P)
        line = 0.5 * math.atan2(2 * sxy, sxx - syy)                 # direction of the line in the plane
        h2 = _in_plane(rg.Point3d(0, 0, 0) + rg.Vector3d.CrossProduct(up, n), rg.Point3d(0, 0, 0), u, v)
        th = math.atan2(h2[1], h2[0]) - line
        while th > math.pi / 2: th -= math.pi
        while th < -math.pi / 2: th += math.pi
        c = rg.Point3d(sum(p.X for p in pts) / len(pts), sum(p.Y for p in pts) / len(pts), sum(p.Z for p in pts) / len(pts))
        T = rg.Transform.Rotation(th, n, c)
    zs = []
    for p in pts:
        q = rg.Point3d(p); q.Transform(T); zs.append(q.Z)
    T = rg.Transform.Translation(rg.Vector3d(0, 0, -sum(zs) / len(zs))) * T
    _move_photo(ph, T)
    st_.add("levelled"); _set_photo_state(ph, st_)
    sc.doc.Views.Redraw()
    spread = (max(zs) - min(zs)) if "centred" in st_ and th == 0.0 else 0.0
    print("[MeshPhoto] {} levelled: {}floor at 0{}".format(
        photo_name(ph), "" if th == 0.0 else "turned {:.3f} deg, ".format(math.degrees(th)),
        "" if not spread else " (centred: only moved up/down - the points differ by {} in height)".format(fl(spread))))


def _pairs_session(photos, insts, drw, photo_first, title):
    """Pairs photo <-> scan/mesh. -> (photo, [(photo pt, scan pt, scan obj)], scale) or None."""
    pairs = []; ph = None; first = None; obj_m = None
    st = {"scale": photo_first}
    pins = []
    drw.pins_fn = lambda: (pins + ([(first[0], "1st", PIN_COL, True)] if first else []), [])
    while True:
        gp = ric.GetPoint()
        if first is None:
            what = "a point on the PHOTO" if photo_first else "a point on the SCAN/MESH that moves"
        else:
            what = "the same point on the SCAN/MESH" if photo_first else "where it belongs on the PHOTO"
        gp.SetCommandPrompt("{}: pair {} - {} <Enter = done, UndoPair, Scale, Esc = cancel, nothing changes>".format(
            title, len(pairs) + 1, what))
        gp.AcceptNothing(True)
        o_undo = gp.AddOption("UndoPair")
        o_scl = gp.AddOptionToggle("Scale", ric.OptionToggle(st["scale"], "Off", "On"))
        gp.PermitOrthoSnap(False); gp.PermitElevatorMode(0)
        res = scan_get(gp)
        if res == Rhino.Input.GetResult.Cancel: return None
        if res == Rhino.Input.GetResult.Nothing:
            if pairs: return ph, pairs, st["scale"]
            return None
        if res == Rhino.Input.GetResult.Option:
            oi = gp.Option().Index
            if oi == o_undo:
                if first is not None: first = None
                elif pairs: pairs.pop(); pins.pop(); pins.pop()
            elif oi == o_scl:
                st["scale"] = not st["scale"]
            sc.doc.Views.Redraw(); continue
        if not is_click(res): continue
        want_photo = (first is None) == photo_first
        if want_photo:
            hit = photo_hit(gp, [(ph, None)] if ph is not None else photos)
            if hit is None:
                print("  click ON the photo"); continue
            ph = hit[1]
            p = (hit[0], None)
        else:
            cand = [o for o in insts if obj_m is None or str(o.Id) == str(obj_m.Id)]
            hit, why = pick_meant(gp, insts, cand) if obj_m is not None else (pick_on_scans(gp, insts), None)
            if hit is None:
                print("  click ON the scan/mesh"); continue
            if not photo_first and obj_m is None: obj_m = hit[1]
            p = (hit[0], hit[1])
        if first is None:
            first = p; continue
        ppt, spt = (first, p) if photo_first else (p, first)
        pairs.append((ppt[0], spt[0], spt[1])); first = None
        pins.append((ppt[0], "P{}".format(len(pairs)), PIN_COL, True))
        pins.append((spt[0], "S{}".format(len(pairs)), GHOST_COL, True))
        sc.doc.Views.Redraw()


def _photo_to_mesh(photos, insts, drw):
    """The photo is turned / moved / scaled in ITS OWN plane so its points
    land on the scan/mesh points (seen along the photo's view)."""
    r = _pairs_session(photos, insts, drw, True, "PhotoToMesh")
    if r is None:
        print("[MeshPhoto] PhotoToMesh cancelled - nothing changed"); return
    ph, pairs, scale = r
    O, u, v, n, sx = photo_axes(ph)
    src = [_in_plane(p, O, u, v) for (p, s_, o) in pairs]
    dst = [_in_plane(s_, O, u, v) for (p, s_, o) in pairs]
    sim = _sim2(src, dst, scale and len(pairs) >= 2)
    _move_photo(ph, _plane_xf(O, u, v, n, sim))
    _set_photo_state(ph, set())                    # it follows the car now
    sc.doc.Views.Redraw()
    O2, u2, v2, n2, sx2 = photo_axes(ph)
    d = []
    for (p, s_, o) in pairs:
        q = rg.Point3d(p); q.Transform(_plane_xf(O, u, v, n, sim))
        a = _in_plane(q, O2, u2, v2); b = _in_plane(s_, O2, u2, v2)
        d.append(math.hypot(a[0] - b[0], a[1] - b[1]))
    print("[MeshPhoto] {} fitted onto the scans/meshes ({} pair(s){}): pairs off by {} (worst {})".format(
        photo_name(ph), len(pairs), ", size x{:.5f}".format(math.hypot(sim[0], sim[1])) if scale and len(pairs) >= 2 else "",
        " ".join(fl(x) for x in d), fl(max(d))))


def _mesh_to_photo(photos, insts, drw):
    """A scan/mesh (with what is aligned to it) is moved in the photo's plane
    so its points land where the photo shows them: e.g. the spoiler onto the
    side photo - up/down, front/back and turned about the side view's axis.
    Across the view it does not move."""
    r = _pairs_session(photos, insts, drw, False, "MeshToPhoto")
    if r is None:
        print("[MeshPhoto] MeshToPhoto cancelled - nothing changed"); return
    ph, pairs, scale = r
    obj = pairs[0][2]
    branch = align_branch(obj.Id)
    moving = [o for o in insts if str(o.Id) in branch]
    names = [_scan_name(o) for o in moving]
    bad = frame_refusal(frame_get(), names, [])
    if bad:
        print("[MeshPhoto] REFUSED: {} {} - moving it onto a photo would throw that away (move the other parts to it "
              "with Align, or the photo with PhotoToMesh). Nothing changed.".format(bad[0], bad[1])); return
    O, u, v, n, sx = photo_axes(ph)
    src = [_in_plane(s_, O, u, v) for (p, s_, o) in pairs]
    dst = [_in_plane(p, O, u, v) for (p, s_, o) in pairs]
    sim = _sim2(src, dst, scale and len(pairs) >= 2)
    D = _plane_xf(O, u, v, n, sim)
    if not (scale and len(pairs) >= 2):
        # a scan/mesh keeps its size: only the turn and the move
        c, si = sim[0], sim[1]; s_ = math.hypot(c, si)
        if abs(s_ - 1.0) > 1e-12:
            D = _plane_xf(O, u, v, n, (c / s_, si / s_, sim[2], sim[3]))
    for o in moving:
        sc.doc.Objects.Transform(o.Id, D, True)
    fr_ = frame_get()
    if fr_:
        frame_moved(fr_, names, []); frame_set(fr_)
    _update_setups_after_align(set(str(o.Id) for o in moving), [])
    sc.doc.Views.Redraw()
    print("[MeshPhoto] {} moved onto the {} photo ({} pair(s); in the photo's plane only){}".format(
        ", ".join(names), photo_name(ph), len(pairs),
        "" if len(moving) == 1 else " - with what is aligned to it"))


# ---- photos in the setups ------------------------------------------------------
def photo_records(st):
    """For save_setup_prompt: the photos' corners (setup units)."""
    k = setup_mm(st) and (_mm_per_unit() / setup_mm(st)) or 1.0
    out = {}
    for (o, lp) in photo_objects():
        O, X, Y = photo_frame(o)
        out["photo:" + photo_name(o)] = {"photo": os.path.basename(o.Attributes.GetUserString(PHOTO_KEY) or ""),
                                         "corners": [[c * k for c in (p.X, p.Y, p.Z)] for p in (O, X, Y)],
                                         "state": o.Attributes.GetUserString(PHOTO_STATE_KEY) or ""}
    return out


def _affine3(src, dst):
    """The transform taking the 3 corners src (O, X, Y) onto dst (a similarity
    for a picture: the normal follows the two edges)."""
    def basis(P):
        O, X, Y = P
        u = X - O; v = Y - O
        n = rg.Vector3d.CrossProduct(u, v); n.Unitize(); n = n * math.sqrt(u.Length * v.Length)
        M = rg.Transform(1.0)
        for i, vec in enumerate((u, v, n)):
            M[0, i] = vec.X; M[1, i] = vec.Y; M[2, i] = vec.Z
        M[0, 3] = O.X; M[1, 3] = O.Y; M[2, 3] = O.Z
        return M
    A = basis(src); B = basis(dst)
    ok, Ai = A.TryGetInverse()
    return B * Ai if ok else rg.Transform.Identity


def apply_photo_records(st):
    k = (setup_mm(st) / _mm_per_unit()) if setup_mm(st) else 1.0
    n = 0
    for (o, lp) in photo_objects():
        rec = st.get("photo:" + photo_name(o))
        if not isinstance(rec, dict) or not rec.get("corners"): continue
        dst = [rg.Point3d(*[c * k for c in p]) for p in rec["corners"]]
        _move_photo(o, _affine3(photo_frame(o), dst))
        cur = sc.doc.Objects.FindId(o.Id)
        a = cur.Attributes.Duplicate(); a.SetUserString(PHOTO_STATE_KEY, rec.get("state", ""))
        sc.doc.Objects.ModifyAttributes(cur, a, True)
        n += 1
    return n


photo_tools = _with_hand_check(photo_tools, "MeshPhoto")
SCAN_TOOLS = SCAN_TOOLS + (("LoadPhotos", load_photos), ("MeshPhoto", photo_tools))
SCAN_TOOL_LABEL["LoadPhotos"] = "Load photos"
SCAN_TOOL_LABEL["MeshPhoto"] = "Photo tools"
