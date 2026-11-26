# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# PICK CANDIDATES: visible curves + naked surface edges
# -----------------------------------------------------------------------------

class Cand(object):
    def __init__(self, kind, oid, crv, brep=None, ei=-1):
        self.kind = kind; self.oid = oid; self.crv = crv
        self.brep = brep; self.ei = ei
        dom = crv.Domain
        self.ts = [dom.ParameterAt(i / 39.0) for i in range(40)]
        self.pts3 = [crv.PointAt(t) for t in self.ts]
        self.tp = [T(p) for p in self.pts3]
        self.p0 = crv.PointAtStart; self.p1 = crv.PointAtEnd
        bb = crv.GetBoundingBox(False)
        self.bmin = T(bb.Min); self.bmax = T(bb.Max)
        self.scr_key = None; self.scr = None
        self.ncv = None          # CV count along this edge/curve (label)
        self.lbl = self.pts3[13]
        self.length = crv.GetLength()

    def near_bbox(self, p, tol):
        for k in range(3):
            if p[k] < self.bmin[k] - tol or p[k] > self.bmax[k] + tol:
                return False
        return True


PREVIEW_KEY = "msh2classA_mirror_preview"     # user text of the mirrored preview copies


def is_preview(obj):
    try:
        return bool(obj.Attributes.GetUserString(PREVIEW_KEY))
    except Exception:
        return False


def collect_candidates(mesh):
    mb = mesh.GetBoundingBox(False)
    pad = mb.Diagonal.Length * 0.05
    mmin = T(mb.Min); mmax = T(mb.Max)

    def inside(c):
        for k in range(3):
            if c.bmax[k] < mmin[k] - pad or c.bmin[k] > mmax[k] + pad:
                return False
        return True

    s = ro.ObjectEnumeratorSettings()
    s.VisibleFilter = True
    s.HiddenObjects = False
    s.LockedObjects = True
    cands = []
    for obj in sc.doc.Objects.GetObjectList(s):
        if is_preview(obj): continue
        geo = obj.Geometry
        try:
            if isinstance(geo, rg.Curve):
                if geo.IsClosed: continue
                c = Cand("curve", obj.Id, geo.DuplicateCurve())
                try: c.ncv = geo.ToNurbsCurve().Points.Count
                except Exception: pass
                if inside(c): cands.append(c)
                continue
            brep = None
            if isinstance(geo, rg.Brep): brep = geo
            elif isinstance(geo, rg.Extrusion): brep = geo.ToBrep()
            elif isinstance(geo, rg.Surface): brep = geo.ToBrep()
            if brep is None: continue
            for ei in range(brep.Edges.Count):
                e = brep.Edges[ei]
                if e.Valence != rg.EdgeAdjacency.Naked: continue
                ec = e.DuplicateCurve()
                if ec is None or ec.IsClosed: continue
                c = Cand("edge", obj.Id, ec, brep, ei)
                c.ncv = edge_along_cv(brep, ei)
                if inside(c): cands.append(c)
        except Exception:
            continue
    return cands


def collect_surfaces(mesh):
    """Visible single-face surfaces near the mesh: (id, brep, bbox, smoothable)."""
    mb = mesh.GetBoundingBox(False)
    pad = mb.Diagonal.Length * 0.05
    mmin = T(mb.Min); mmax = T(mb.Max)
    s = ro.ObjectEnumeratorSettings()
    s.VisibleFilter = True; s.HiddenObjects = False; s.LockedObjects = False
    out = []
    for obj in sc.doc.Objects.GetObjectList(s):
        if is_preview(obj): continue
        geo = obj.Geometry
        brep = None
        if isinstance(geo, rg.Brep): brep = geo
        elif isinstance(geo, rg.Surface): brep = geo.ToBrep()
        if brep is None or brep.Faces.Count != 1: continue
        bb = brep.GetBoundingBox(False)
        b0, b1 = T(bb.Min), T(bb.Max)
        if any(b1[c] < mmin[c] - pad or b0[c] > mmax[c] + pad for c in range(3)): continue
        ok = False
        try:
            ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
            ok = (brep.IsSurface and ns is not None and ns.SpanCount(0) == 1 and ns.SpanCount(1) == 1
                  and not ns.IsRational and ns.Degree(0) <= 7 and ns.Degree(1) <= 7)
        except Exception:
            ok = False
        out.append((obj.Id, brep, (b0, b1), ok))
    return out


def edge_along_cv(brep, ei):
    """CV count of the surface along an untrimmed iso edge (None otherwise)."""
    try:
        e = brep.Edges[ei]
        face = brep.Faces[e.AdjacentFaces()[0]]
        iso = brep.Trims[e.TrimIndices()[0]].IsoStatus
        ns = face.UnderlyingSurface().ToNurbsSurface()
        if iso == rg.IsoStatus.West or iso == rg.IsoStatus.East:
            return ns.Points.CountV
        if iso == rg.IsoStatus.South or iso == rg.IsoStatus.North:
            return ns.Points.CountU
    except Exception:
        pass
    return None


def _normal_kappa(face, p, tangent):
    ok, u, v = face.ClosestPoint(p)
    n = v_unit(T(face.NormalAt(u, v)))
    d = v_unit(v_cross(n, tangent))
    k = face.CurvatureAt(u, v)
    if k is None:
        return n, 0.0
    cs = v_dot(d, T(k.Direction(0)))
    kc = k.Kappa(0) * cs * cs + k.Kappa(1) * (1.0 - cs * cs)
    if v_dot(T(k.Normal), n) < 0:
        kc = -kc
    return n, kc


SEAM_ANG = 0.1      # deg: below = G1
SEAM_K = 0.02       # relative curvature difference: below = G2

def analyze_seams(cands, abs_tol, kfloor):
    """Every pair of touching surface edges -> (point, text, rgb)."""
    edges = [c for c in cands if c.kind == "edge"]
    out = []
    for i in range(len(edges)):
        A = edges[i]
        for j in range(i + 1, len(edges)):
            B = edges[j]
            if A.oid == B.oid: continue
            pad = 0.02 * min(A.length, B.length)
            if any(A.bmax[k] < B.bmin[k] - pad or A.bmin[k] > B.bmax[k] + pad for k in range(3)):
                continue
            detect = 0.01 * min(A.length, B.length)
            close = []
            dom = A.crv.Domain
            for sidx in range(1, 14):
                ta = dom.ParameterAt(sidx / 14.0)
                pa = A.crv.PointAt(ta)
                ok, tb = B.crv.ClosestPoint(pa)
                if not ok: continue
                pb = B.crv.PointAt(tb)
                d = pa.DistanceTo(pb)
                if d < detect:
                    close.append((pa, pb, d, T(A.crv.TangentAt(ta))))
            if len(close) < 3: continue
            try:
                fa = A.brep.Faces[A.brep.Edges[A.ei].AdjacentFaces()[0]]
                fb = B.brep.Faces[B.brep.Edges[B.ei].AdjacentFaces()[0]]
                gap = ang = kd = 0.0
                for (pa, pb, d, tg) in close:
                    na, ka = _normal_kappa(fa, pa, tg)
                    nb, kb = _normal_kappa(fb, pb, tg)
                    if v_dot(na, nb) < 0: kb = -kb
                    gap = max(gap, d)
                    ang = max(ang, math.degrees(math.acos(min(1.0, abs(v_dot(na, nb))))))
                    kd = max(kd, abs(ka - kb) / max(abs(ka), abs(kb), kfloor))
            except Exception:
                continue
            if gap > abs_tol: text, rgb = "gap {:.3g}".format(gap), (220, 30, 30)
            elif ang > SEAM_ANG: text, rgb = u"G0 (kink {:.2f}\u00b0)".format(ang), (240, 130, 0)
            elif kd > SEAM_K: text, rgb = "G1", (225, 200, 0)
            else: text, rgb = "G2", (40, 170, 60)
            n = float(len(close))
            mid = rg.Point3d(sum(c[0].X for c in close) / n, sum(c[0].Y for c in close) / n,
                             sum(c[0].Z for c in close) / n)
            out.append((mid, text, rgb))
    return out


def analyze_npatch_seams(cands, nps, abs_tol, kfloor, dfloor=0.0):
    """Seams of NPatches (trimmed; their edge may be one closed loop, and a
    hole side may be only a PART of a neighbour's edge): along every
    neighbour edge, where it lies on the NPatch -> (point, text, rgb)."""
    out = []
    for (nid, nb) in nps:
        try:
            face = nb.Faces[0]
            bb = nb.GetBoundingBox(False); b0 = T(bb.Min); b1 = T(bb.Max)
        except Exception:
            continue
        for c in cands:
            if c.kind != "edge" or c.oid == nid: continue
            pad = 0.02 * c.length
            if any(c.bmax[k] < b0[k] - pad or c.bmin[k] > b1[k] + pad for k in range(3)):
                continue
            detect = max(0.01 * c.length, dfloor)
            close = []
            dom = c.crv.Domain
            for sidx in range(1, 26):
                ta = dom.ParameterAt(sidx / 26.0)
                pa = c.crv.PointAt(ta)
                q = nb.ClosestPoint(pa)
                d = pa.DistanceTo(q)
                if d < detect:
                    close.append((pa, q, d, T(c.crv.TangentAt(ta))))
            if len(close) < 3: continue
            try:
                fb = c.brep.Faces[c.brep.Edges[c.ei].AdjacentFaces()[0]]
                gap = ang = kd = 0.0
                for (pa, pb, d, tg) in close:
                    na, ka = _normal_kappa(face, pa, tg)
                    nb_, kb = _normal_kappa(fb, pa, tg)
                    if v_dot(na, nb_) < 0: kb = -kb
                    gap = max(gap, d)
                    ang = max(ang, math.degrees(math.acos(min(1.0, abs(v_dot(na, nb_))))))
                    kd = max(kd, abs(ka - kb) / max(abs(ka), abs(kb), kfloor))
            except Exception:
                continue
            if gap > max(abs_tol, 0.2 * dfloor): text, rgb = "gap {:.3g}".format(gap), (220, 30, 30)
            elif ang > SEAM_ANG: text, rgb = u"G0 (kink {:.2f}\u00b0)".format(ang), (240, 130, 0)
            elif kd > SEAM_K: text, rgb = "G1", (225, 200, 0)
            else: text, rgb = "G2", (40, 170, 60)
            n = float(len(close))
            mid = rg.Point3d(sum(x[0].X for x in close) / n, sum(x[0].Y for x in close) / n,
                             sum(x[0].Z for x in close) / n)
            out.append((mid, text, rgb))
    return out


def xf_tuple(vp):
    xf = vp.GetTransform(ro.CoordinateSystem.World, ro.CoordinateSystem.Screen)
    return (xf.M00, xf.M01, xf.M02, xf.M03, xf.M10, xf.M11, xf.M12, xf.M13,
            xf.M20, xf.M21, xf.M22, xf.M23, xf.M30, xf.M31, xf.M32, xf.M33)

def to_screen(m, p):
    w = m[12] * p[0] + m[13] * p[1] + m[14] * p[2] + m[15]
    if abs(w) < 1e-12: w = 1e-12
    return ((m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3]) / w,
            (m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7]) / w)

PICK_PX = 9.0
SNAP_PX = 12.0

def hit_test(cands, m, sx, sy, pick_px=PICK_PX, snap_px=SNAP_PX):
    """Screen-space pick. Returns list of hits (one per candidate, nearest
    first): {"ci", "d", "t", "end"}  end = 0/1 if within snap_px of an end."""
    hits = []
    for ci, c in enumerate(cands):
        if c.scr_key != m:
            c.scr = [to_screen(m, p) for p in c.tp]; c.scr_key = m
        scr = c.scr
        best = None
        for k in range(len(scr) - 1):
            ax, ay = scr[k]; bx, by = scr[k + 1]
            dx, dy = bx - ax, by - ay
            l2 = dx * dx + dy * dy
            f = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((sx - ax) * dx + (sy - ay) * dy) / l2))
            d = math.hypot(ax + f * dx - sx, ay + f * dy - sy)
            if best is None or d < best[0]:
                best = (d, c.ts[k] + f * (c.ts[k + 1] - c.ts[k]))
        end = None
        for e_, q in ((0, scr[0]), (1, scr[-1])):
            if math.hypot(q[0] - sx, q[1] - sy) < snap_px:
                end = e_
        if best is not None and (best[0] < pick_px or end is not None):
            hits.append({"ci": ci, "d": best[0], "t": best[1], "end": end})
    hits.sort(key=lambda h: h["d"])
    return hits


def joint_point(ca, ta, cb, tb):
    """Closest pair of points between two curves near (ta, tb)."""
    for _ in range(8):
        ok, tb = cb.ClosestPoint(ca.PointAt(ta))
        ok, ta = ca.ClosestPoint(cb.PointAt(tb))
    pa = ca.PointAt(ta); pb = cb.PointAt(tb)
    mid = rg.Point3d((pa.X + pb.X) / 2, (pa.Y + pb.Y) / 2, (pa.Z + pb.Z) / 2)
    return mid, ta, tb, pa.DistanceTo(pb)


