# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# SIDE GEOMETRY
# -----------------------------------------------------------------------------

class Side(object):
    """One side of the quad, evaluated in quad orientation t in [0,1]."""
    def __init__(self, kind, fn, ox, label=""):
        self.kind = kind        # "scan" | "curve" | "edge"
        self.fn = fn            # t -> rg.Point3d (world)
        self.ox = ox
        self.label = label
        self.ev = None          # edge: t -> (pt, D1, D2, normal) world tuples
        self.exact = False      # untrimmed iso edge -> exact match possible
        self.along_deg = None   # neighbour degree along edge if single-span
        self.at_end = (False, False)   # t=0 / t=1 is an END of the neighbour's edge
        self.tab = [self.exact_pt(i / 100.0) for i in range(101)]

    def exact_pt(self, t):
        return v_sub(T(self.fn(t)), self.ox)

    def fast(self, t):
        x = max(0.0, min(1.0, t)) * 100.0
        k = min(int(x), 99)
        return v_lerp(self.tab[k], self.tab[k + 1], x - k)


def project_line_samples(a, b, proj, n=41):
    pts = []
    for i in range(n):
        p = rg.Point3d(a.X + (b.X - a.X) * i / (n - 1.0),
                       a.Y + (b.Y - a.Y) * i / (n - 1.0),
                       a.Z + (b.Z - a.Z) * i / (n - 1.0))
        if proj is not None and 0 < i < n - 1:
            q = proj(p)
            if q is not None: p = q
        pts.append(p)
    return pts

def polyline_fn(pts):
    n = len(pts)

    def fn(t):
        x = max(0.0, min(1.0, t)) * (n - 1)
        k = min(int(x), n - 2); f = x - k
        a = pts[k]; b = pts[k + 1]
        return rg.Point3d(a.X + (b.X - a.X) * f, a.Y + (b.Y - a.Y) * f, a.Z + (b.Z - a.Z) * f)
    return fn

def curve_fn(crv, t0, t1):
    def fn(t):
        return crv.PointAt(t0 + t * (t1 - t0))
    return fn


def edge_side(cand, A, B, ox):
    """Side along a naked surface edge from A to B, with continuity data."""
    brep = cand.brep
    edge = brep.Edges[cand.ei]
    face = brep.Faces[edge.AdjacentFaces()[0]]
    srf = face.UnderlyingSurface()
    trim = brep.Trims[edge.TrimIndices()[0]]
    iso = trim.IsoStatus
    table = [(rg.IsoStatus.West, (0, 0, -1.0)), (rg.IsoStatus.East, (0, 1, +1.0)),
             (rg.IsoStatus.South, (1, 0, -1.0)), (rg.IsoStatus.North, (1, 1, +1.0))]
    iso_info = None
    for st, val in table:
        if iso == st:
            iso_info = val
    side = None
    if iso_info is not None:
        axis, end, osign = iso_info
        dU = srf.Domain(0); dV = srf.Domain(1)
        okA, uA, vA = srf.ClosestPoint(A)
        okB, uB, vB = srf.ClosestPoint(B)
        if okA and okB:
            if axis == 0:
                uc = dU.T1 if end else dU.T0
                sA, sB = vA, vB
                uv = lambda t: (uc, sA + t * (sB - sA))
            else:
                vc = dV.T1 if end else dV.T0
                sA, sB = uA, uB
                uv = lambda t: (sA + t * (sB - sA), vc)

            ds = sB - sA

            def ev(t):
                u, v = uv(t)
                res = srf.Evaluate(u, v, 2)
                pt, d = res[1], res[2]
                Su, Sv, Suu, Suv, Svv = T(d[0]), T(d[1]), T(d[2]), T(d[3]), T(d[4])
                D1 = v_mul(Su if axis == 0 else Sv, osign)
                D2 = Suu if axis == 0 else Svv
                E1 = v_mul(Sv if axis == 0 else Su, ds)
                E2 = v_mul(Svv if axis == 0 else Suu, ds * ds)
                TW = v_mul(Suv, osign * ds)
                return T(pt), D1, D2, v_unit(v_cross(Su, Sv)), E1, E2, TW

            def fn(t):
                u, v = uv(t)
                return srf.PointAt(u, v)
            side = Side("edge", fn, ox)
            side.ev = ev
            side.exact = True
            rg_ = dV if axis == 0 else dU
            side.at_end = tuple(min(abs(s_ - rg_.T0), abs(s_ - rg_.T1)) < 1e-4 * max(rg_.Length, 1e-12)
                                for s_ in (sA, sB))
            try:
                ns = srf.ToNurbsSurface()
                along = 1 if axis == 0 else 0
                if ns is not None and ns.SpanCount(along) == 1:
                    side.along_deg = ns.Degree(along)
            except Exception:
                pass
    if side is None:
        # trimmed / non-iso edge: approximate from edge curve + face
        ec = edge.DuplicateCurve()
        okA, tA = ec.ClosestPoint(A)
        okB, tB = ec.ClosestPoint(B)
        length = max(ec.GetLength(), 1e-6)
        state = {"flip": 1.0}

        def raw(t, flip):
            tt = tA + t * (tB - tA)
            pt = ec.PointAt(tt); tg = ec.TangentAt(tt)
            ok, u, v = face.ClosestPoint(pt)
            n = face.NormalAt(u, v)
            d = v_mul(v_unit(v_cross(T(n), T(tg))), flip)
            return pt, d, u, v, n

        pt, d, u, v, n = raw(0.5, 1.0)
        q = rg.Point3d(pt.X - d[0] * length * 1e-3, pt.Y - d[1] * length * 1e-3, pt.Z - d[2] * length * 1e-3)
        ok, u2, v2 = face.ClosestPoint(q)
        if face.IsPointOnFace(u2, v2) == rg.PointFaceRelation.Exterior:
            state["flip"] = -1.0

        def ev(t):
            pt, d, u, v, n = raw(t, state["flip"])
            der = ec.DerivativeAt(tA + t * (tB - tA), 2)
            E1 = v_mul(T(der[1]), tB - tA); E2 = v_mul(T(der[2]), (tB - tA) ** 2)
            Z = (0.0, 0.0, 0.0)
            k = face.CurvatureAt(u, v)
            if k is None:
                return T(pt), d, Z, T(n), E1, E2, Z
            cs = v_dot(d, T(k.Direction(0)))
            kc = k.Kappa(0) * cs * cs + k.Kappa(1) * (1.0 - cs * cs)
            return T(pt), d, v_mul(T(k.Normal), kc), T(n), E1, E2, Z

        side = Side("edge", curve_fn(ec, tA, tB), ox)
        side.ev = ev
        dm = ec.Domain
        side.at_end = tuple(min(abs(t_ - dm.T0), abs(t_ - dm.T1)) < 1e-4 * max(dm.Length, 1e-12) for t_ in (tA, tB))
    return side

