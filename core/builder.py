# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Build ONE single-span patch from 4 clicked corners on a scan - the same
recipe as the Rhino version (rhino/session_build.py, BuildMixin._build), with
the program-specific parts replaced by two small interfaces:

  mesh     anything with
             closest(p)  -> (point, unit face normal, face index) or None
             ray(o, d)   -> (t, unit face normal, face index) or None,
                            first hit of o + t*d with t >= 0
           (core/meshgrid.TriMesh does it in pure Python; Blender wraps its
           BVH tree)
  patches  the finished patches the new one may join: [Patch]

A side whose two corners lie on the same side of a finished patch follows that
edge exactly, with G0 / G1 / G2 to it. Every other side lies on the scan and
RESERVES rows for a future neighbour (as in Rhino).

Not in this first host-neutral version (Rhino has them): mirror plane sides,
curve sides, extra points along a side, TempPts / NoScan fits, AutoSplit,
the narrow-patch clean-up.
"""
from __future__ import print_function, division
import math

from .vectors import fl, median, percentile, v_add, v_cross, v_dist, v_dot, v_len, v_lerp, v_mul, v_sub, v_unit
from .bspline import BDir
from .evaluate import srf_derivs, srf_point
from .curve_fit import curve_fit_deviation
from .patch_fit import (LEVEL_NAME, SIDE_LV_NAME, SIDE_MAP, Cont, FitProblem, coons_point, fit_to_function,
                        normal_curv_neighbour, run_fit)
from .smoothing import NARROW_RATIO, SPatch, _closest_on_side


class Patch(object):
    """A finished single-span patch: degrees pu, pv, CVs P (world, index i*(pv+1)+j)."""

    def __init__(self, pu, pv, P, levels=None, name=""):
        self.pu = int(pu); self.pv = int(pv)
        self.P = [tuple(map(float, q)) for q in P]
        self.levels = dict(levels or {})       # side -> G0/G1/G2 built with (0/1/2)
        self.name = name

    def spatch(self):
        return SPatch(self.name or id(self), self.pu, self.pv, self.P)

    def grid(self):
        """CVs as rows: [[P(i, j) for j] for i]."""
        nv = self.pv + 1
        return [self.P[i * nv:(i + 1) * nv] for i in range(self.pu + 1)]


class BSide(object):
    """One side of the quad, evaluated in quad orientation t in [0, 1] (world tuples)."""

    def __init__(self, kind, fn, ox):
        self.kind = kind            # "scan" | "edge"
        self.fn = fn
        self.ox = ox
        self.ev = None              # edge: t -> (pt, D1, D2, normal, E1, E2, TW)
        self.exact = False
        self.along_deg = None
        self.at_end = (False, False)
        self.tab = [self.exact_pt(i / 100.0) for i in range(101)]

    def exact_pt(self, t):
        return v_sub(self.fn(t), self.ox)

    def fast(self, t):
        x = max(0.0, min(1.0, t)) * 100.0
        k = min(int(x), 99)
        return v_lerp(self.tab[k], self.tab[k + 1], x - k)


def polyline_fn(pts):
    n = len(pts)

    def fn(t):
        x = max(0.0, min(1.0, t)) * (n - 1)
        k = min(int(x), n - 2)
        return v_lerp(pts[k], pts[k + 1], x - k)
    return fn


def project_line_samples(a, b, proj, n=41):
    pts = []
    for i in range(n):
        p = v_lerp(a, b, i / (n - 1.0))
        if proj is not None and 0 < i < n - 1:
            q = proj(p)
            if q is not None:
                p = q
        pts.append(p)
    return pts


class PatchBuilder(object):
    """cont: default continuity for new sides (0 G0, 1 G1, 2 G2);
    tol: allowed mesh deviation (model units); snap: corners this close to a
    patch corner / edge go onto it; model_mm: mm per model unit."""

    def __init__(self, mesh, patches=(), cont=2, tol=None, snap=None, model_mm=1.0,
                 corner_flow=False, log=print):
        self.mesh = mesh
        self.patches = list(patches)
        self.cont = int(cont)
        self.mm = 1.0 / float(model_mm)               # model units per mm
        self.tol = tol if tol is not None else 0.5 * self.mm
        self.snap = snap if snap is not None else max(2.0 * self.tol, 1e-6)
        self.corner_flow = corner_flow
        self.log = log
        self.sp = [p.spatch() for p in self.patches]
        self.refs = []
        self.nsign = None
        self.n_wall = self.n_back = 0
        self.ray_back = 10.0 * self.mm
        self.size = 1.0

    # ------------------------------------------------------------ snapping
    def snap_point(self, p):
        """-> (point, what): onto a patch corner, else onto a patch edge, else p."""
        best = None
        for Y in self.sp:
            for k in range(4):
                q = Y.val(k, 0.0, "pt")
                d = v_dist(p, q)
                if d <= self.snap and (best is None or d < best[0]):
                    best = (d, q)
        if best is not None:
            return best[1], "corner"
        for Y in self.sp:
            for l in range(4):
                s, d = _closest_on_side(Y, l, p)
                if d <= self.snap and (best is None or d < best[0]):
                    best = (d, Y.val(l, s, "pt"))
        if best is not None:
            return best[1], "edge"
        return p, "mesh"

    # ------------------------------------------------------------ the scan
    def prep_front(self, corners):
        self.refs = []
        for p in corners:
            h = self.mesh.closest(p)
            if h is not None:
                self.refs.append((p, v_unit(h[1])))
        span = max(v_dist(corners[i], corners[j]) for i in range(4) for j in range(4))
        c_ = corners
        width = min(0.5 * (v_dist(c_[0], c_[1]) + v_dist(c_[2], c_[3])),
                    0.5 * (v_dist(c_[1], c_[2]) + v_dist(c_[3], c_[0])))
        self.ray_back = min(0.25 * span + self.mm, max(1.5 * width, 3.0 * self.mm))
        self.size = max(span, 1e-9)
        self.n_back = self.n_wall = 0

    def ref_normal(self, p):
        best = None
        for (q, n) in self.refs:
            d = v_dist(p, q)
            if best is None or d < best[0]:
                best = (d, n)
        return best[1] if best else None

    def front_along(self, p, nref, min_dot, maxd):
        eps = 1e-6 * max(self.size, 1.0)
        o = p; gone = 0.0
        for _ in range(6):
            h = self.mesh.ray(o, nref)
            if h is None:
                return None
            t = h[0]
            gone += t
            if gone > maxd:
                return None
            hit = v_add(o, v_mul(nref, t))
            if v_dot(h[1], nref) >= min_dot:
                return hit
            o = v_add(hit, v_mul(nref, eps))
            gone += eps
        return None

    def scan_pt(self, p, nsurf=None, fit=False):
        """Mesh point for p, only from faces turned the same way as the patch
        (thin parts scanned on both sides, walls, ribs are skipped)."""
        h = self.mesh.closest(p)
        nref = self.ref_normal(p)
        if nsurf is not None and v_len(nsurf) > 1e-12:
            if self.nsign is not None:
                nsurf = v_mul(nsurf, self.nsign)
            elif nref is not None and v_dot(nsurf, nref) < 0:
                nsurf = v_mul(nsurf, -1.0)
            nref = v_unit(nsurf)
        if nref is None:
            return h[0] if h is not None else None
        min_dot = 0.7 if fit else 0.5
        if h is not None:
            dn = v_dot(h[1], nref)
            if dn >= min_dot:
                return h[0]
            if dn > -0.3:
                self.n_wall += 1
                return None
            hit = self.front_along(p, nref, min_dot, self.ray_back)
            if hit is not None:
                self.n_back += 1
                return hit
            if fit:
                self.n_wall += 1
                return None
        r = self.ray_back
        o = v_add(p, v_mul(nref, r))
        res = self.mesh.ray(o, v_mul(nref, -1.0))
        if res is not None and v_dot(res[1], nref) >= min_dot:
            self.n_back += 1
            return v_sub(o, v_mul(nref, res[0]))
        return None

    def patch_sign(self, coons, ox):
        votes = 0.0
        for (u, v) in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
            h = 1e-3
            cu = v_sub(coons(min(1.0, u + h), v), coons(max(0.0, u - h), v))
            cv = v_sub(coons(u, min(1.0, v + h)), coons(u, max(0.0, v - h)))
            n = v_cross(cu, cv)
            hit = self.mesh.closest(v_add(coons(u, v), ox))
            if hit is None or v_len(n) < 1e-300:
                continue
            votes += v_dot(v_unit(n), hit[1])
        return -1.0 if votes < 0 else 1.0

    # ------------------------------------------------------------ sides
    def find_edge(self, a, b, ox):
        """Side along a finished patch's side when both corners lie on it."""
        tol_e = max(1e-7 * self.size, 0.05 * self.snap)
        best = None
        for Y in self.sp:
            for l in range(4):
                sa, da = _closest_on_side(Y, l, a)
                if da > tol_e:
                    continue
                sb, db = _closest_on_side(Y, l, b)
                if db > tol_e or abs(sb - sa) < 0.02:
                    continue
                if best is None or da + db < best[0]:
                    best = (da + db, Y, l, sa, sb)
        if best is None:
            return None
        _, Y, l, sa, sb = best
        ds = sb - sa

        def fn(t):
            return Y.val(l, sa + t * ds, "pt")

        def ev(t):
            s = sa + t * ds
            u, v = SIDE_MAP[l][0](s)
            D = srf_derivs(Y.du, Y.dv, Y.P, u, v, 1)
            n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
            D1 = v_mul(Y.val(l, s, "c1"), -1.0)            # across, OUT of the neighbour
            D2 = Y.val(l, s, "c2")
            E1 = v_mul(Y.val(l, s, "a1"), ds)
            E2 = v_mul(Y.val(l, s, "a2"), ds * ds)
            TW = v_mul(Y.val(l, s, "tw"), -ds)
            return Y.val(l, s, "pt"), D1, D2, n, E1, E2, TW

        side = BSide("edge", fn, ox)
        side.ev = ev
        side.exact = True
        side.along_deg = Y.du.p if l % 2 == 0 else Y.dv.p
        side.at_end = (min(sa, 1.0 - sa) < 1e-4, min(sb, 1.0 - sb) < 1e-4)
        return side

    # ------------------------------------------------------------ build
    def build(self, corners, side_levels=None, snap=True):
        """corners: 4 world points (c0..c3, the order of the clicks).
        side_levels: {side: 0..4} as the Rhino side bubbles (G0, G1, G2,
        G2>G0, G1>G0); default = cont for every side.
        -> dict(pu, pv, P (world), levels {edge side: level}, deviation, lines)."""
        log = self.log
        lines = []

        def say(s):
            lines.append(s)
            if log:
                log(s)

        corners = [tuple(map(float, c)) for c in corners]
        if snap:
            for i, c in enumerate(corners):
                corners[i] = self.snap_point(c)[0]
        self.prep_front(corners)
        ox = tuple(sum(c[k] for c in corners) / 4.0 for k in range(3))
        sides = []
        for k in range(4):
            a, b = corners[k], corners[(k + 1) % 4]
            s = self.find_edge(a, b, ox)
            if s is None:
                s = BSide("scan", polyline_fn(project_line_samples(a, b, self.scan_pt)), ox)
            sides.append(s)
        raw = [(side_levels or {}).get(k, self.cont) for k in range(4)]
        layout = [{3: 2, 4: 1}.get(r, r) for r in raw]

        def level_for(k):
            r = raw[k]
            if r >= 3:
                return 0 if sides[k].kind == "edge" else (2 if r == 3 else 1)
            return r
        levels = [level_for(k) if s.kind == "edge" else 0 for k, s in enumerate(sides)]
        say("=== Patch | sides {} ===".format(", ".join(
            "{}:{}".format(k + 1, SIDE_LV_NAME[raw[k]] if s.kind == "edge" else "scan") for k, s in enumerate(sides))))

        fs = [s.fast for s in sides]
        coons = lambda u, v: coons_point(fs, u, v)
        self.nsign = self.patch_sign(coons, ox)

        def iso_lines(direction):
            out = []
            for c in (0.0, 0.5, 1.0):
                pts = []
                for i in range(61):
                    t = i / 60.0
                    p = coons(t, c) if direction == 0 else coons(c, t)
                    q = self.scan_pt(v_add(p, ox))
                    if q is not None:
                        p = v_sub(q, ox)
                    pts.append(p)
                out.append(pts)
            return out

        def count(d):
            ends = (1, 3) if d == 0 else (0, 2)
            along = (0, 2) if d == 0 else (1, 3)
            n = sum(layout[k] + 1 for k in ends)             # G0 1, G1 2, G2 3 rows per end
            for k in along:
                s = sides[k]
                if s.kind == "edge" and s.along_deg and s.along_deg <= 7:
                    n = max(n, s.along_deg + 1)
            if all(sides[k].kind == "scan" for k in ends):
                req = max(4, n)
                if req < 6:
                    got = None
                    lns = iso_lines(d)
                    worst = 0.0
                    for deg in (3, 5):
                        if deg + 1 < req:
                            continue
                        worst = max(curve_fit_deviation(pts, deg, 1)[0] for pts in lns)
                        if worst <= self.tol:
                            got = deg + 1
                            break
                    if got is None:
                        got = 6
                        say("  [INFO] {} direction: even 6CV is {} off the mesh (MeshTol {}) - make the patch "
                            "smaller".format("U" if d == 0 else "V", fl(worst), fl(self.tol)))
                    req = max(req, got)
                n = max(n, req)
            return n

        nu, nv = count(0), count(1)
        du = BDir(nu - 1, 1); dv = BDir(nv - 1, 1)
        say("  {} x {} CV".format(nu, nv))

        def start_fn(u, v):
            p = coons(u, v)
            h = 1e-3
            cu = v_sub(coons(min(1.0, u + h), v), coons(max(0.0, u - h), v))
            cv = v_sub(coons(u, min(1.0, v + h)), coons(u, max(0.0, v - h)))
            q = self.scan_pt(v_add(p, ox), v_cross(cu, cv))
            return v_sub(q, ox) if q is not None else p
        P0 = fit_to_function(du, dv, start_fn)
        prob = FitProblem(du, dv, P0)
        for k, s in enumerate(sides):
            if s.kind == "scan":
                prob.reserved.append((k, level_for(k) + 1))
        for k, (u, v) in enumerate(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))):
            prob.hard.append((u, v, v_sub(corners[k], ox)))

        w_side = 2.0
        for k, s in enumerate(sides):
            uvf, axis, sign = SIDE_MAP[k]
            along_n = du.n if k % 2 == 0 else dv.n
            K = max(10, 3 * along_n + 2)
            ts = [i / float(K - 1) for i in range(K)]
            if s.kind == "scan":
                for t in ts:
                    u, v = uvf(t)
                    prob.soft.append((u, v, s.fast(t), w_side))
                continue
            evs = [s.ev(t) for t in ts]
            for i, t in enumerate(ts):
                u, v = uvf(t)
                prob.hard.append((u, v, v_sub(evs[i][0], ox)))
            if levels[k] >= 1:
                Lm = v_len(s.ev(0.5)[1])
                if Lm < 1e-12:
                    say("  [WARN] edge {}: degenerate tangent, only G0".format(k + 1))
                    continue
                samples = []
                for i, t in enumerate(ts):
                    u, v = uvf(t)
                    D1 = v_mul(evs[i][1], 1.0 / Lm)
                    D2 = v_mul(evs[i][2], 1.0 / (Lm * Lm))
                    E1, E2, TW = evs[i][4], evs[i][5], v_mul(evs[i][6], 1.0 / Lm)
                    samples.append((u, v, axis, sign, D1, D2, E1, E2, TW, t))
                um, vm = uvf(0.5)
                der = srf_derivs(du, dv, P0, um, vm, 1)
                a0 = v_len(der[(1, 0)] if axis == 0 else der[(0, 1)])
                cn = Cont(k, levels[k], samples, a0)
                cn.narrow = a0 < NARROW_RATIO * Lm
                if cn.narrow:
                    cn.flow = [True, True]
                    say("  side {}: narrow patch against its neighbour - rows straight across".format(k + 1))
                elif self.corner_flow:
                    cn.flow = list(s.at_end)
                prob.conts.append(cn)

        nsu = max(14, 3 * du.n + 6); nsv = max(14, 3 * dv.n + 6)
        params = [(i / float(nsu - 1), j / float(nsv - 1)) for i in range(nsu) for j in range(nsv)]
        size = self.size

        def data_fn(P):
            raw_ = []
            for (u, v) in params:
                Dd = srf_derivs(du, dv, P, u, v, 1)
                s = Dd[(0, 0)]
                ns_ = v_cross(Dd[(1, 0)], Dd[(0, 1)])
                q = self.scan_pt(v_add(s, ox), ns_ if v_len(ns_) > 1e-12 else None, fit=True)
                if q is None:
                    continue
                t = v_sub(q, ox)
                raw_.append((u, v, t, v_dist(s, t)))
            if not raw_:
                return []
            kk = max(2.5 * median([r[3] for r in raw_]), size * 1e-7)
            out = []
            for (u, v, t, d) in raw_:
                if d > 6.0 * kk:
                    continue
                out.append((u, v, t, 1.0 if d <= kk else kk / d))
            return out

        P = run_fit(prob, data_fn, 8, log=say)
        Pw = [v_add(q, ox) for q in P]

        # normal towards the side you work on (the clicked faces): else turn u round
        D = srf_derivs(du, dv, P, 0.5, 0.5, 1)
        n = v_cross(D[(1, 0)], D[(0, 1)])
        r = self.ref_normal(v_add(D[(0, 0)], ox))
        flipped = r is not None and v_dot(n, r) < 0
        out_levels = dict((k, levels[k]) for k, s in enumerate(sides) if s.kind == "edge")
        if flipped:
            Pw = [Pw[(nu - 1 - i) * nv + j] for i in range(nu) for j in range(nv)]
            out_levels = dict(({1: 3, 3: 1}.get(k, k), lv) for k, lv in out_levels.items())

        dev = self.report(du, dv, P, ox, sides, prob, say)
        if self.n_wall:
            say("  [INFO] mesh faces turned away from the patch were ignored ({} samples)".format(self.n_wall))
        if self.n_back:
            say("  [INFO] {} samples hit the back side - taken from the visible side".format(self.n_back))
        return {"pu": du.p, "pv": dv.p, "P": Pw, "levels": out_levels, "deviation": dev, "lines": lines,
                "flipped": flipped, "corners": corners}

    def report(self, du, dv, P, ox, sides, prob, say):
        devs = []
        n = 25
        for i in range(n):
            for j in range(n):
                s = srf_point(du, dv, P, i / (n - 1.0), j / (n - 1.0))
                q = self.scan_pt(v_add(s, ox))
                if q is not None:
                    devs.append(v_dist(s, v_sub(q, ox)))
        dev = None
        if devs:
            dev = (percentile(devs, 0.9), max(devs), sum(devs) / len(devs))
            say("  Deviation to scan: P90 {}  max {}  avg {}".format(fl(dev[0]), fl(dev[1]), fl(dev[2])))
        for c in prob.conts:
            s = sides[c.side]
            uvf, axis, sign = SIDE_MAP[c.side]
            gap = ang = angc = dk = kmax = 0.0
            for i in range(21):
                t = i / 20.0
                u, v = uvf(t)
                D = srf_derivs(du, dv, P, u, v, 2)
                evt = s.ev(t)
                pt, D1, D2, nb = evt[:4]
                gap = max(gap, v_dist(D[(0, 0)], v_sub(pt, ox)))
                nn = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                a_ = math.degrees(math.acos(min(1.0, abs(v_dot(nn, nb)))))
                if 0.15 <= t <= 0.85:
                    ang = max(ang, a_)
                else:
                    angc = max(angc, a_)
                if c.level >= 2:
                    Sc = v_mul(D[(1, 0)] if axis == 0 else D[(0, 1)], sign)
                    Scc = D[(2, 0)] if axis == 0 else D[(0, 2)]
                    k1 = v_dot(Scc, nn) / max(v_dot(Sc, Sc), 1e-300)
                    k2 = normal_curv_neighbour(Sc, D1, D2, evt[4], evt[5], evt[6], nn)
                    dk = max(dk, abs(k1 - k2)); kmax = max(kmax, abs(k2))
            line = "  Side {} {}: G0 gap {}  G1 angle {:.3f} deg (near corners {:.3f})".format(
                c.side + 1, LEVEL_NAME[c.level], fl(gap), ang, angc)
            if c.level >= 2:
                line += "  G2 dK {:.2e} ({:.1f}% of K)".format(dk, 100.0 * dk / kmax if kmax > 1e-12 else 0.0)
            say(line)
        return dev


def tessellate(pu, pv, P, n=24):
    """Points and quads of a patch for display: (verts, quads) - n x n faces."""
    du = BDir(pu, 1); dv = BDir(pv, 1)
    verts = []
    for i in range(n + 1):
        for j in range(n + 1):
            verts.append(srf_point(du, dv, P, i / float(n), j / float(n)))
    quads = []
    for i in range(n):
        for j in range(n):
            a = i * (n + 1) + j
            quads.append((a, a + n + 1, a + n + 2, a + 1))
    return verts, quads
