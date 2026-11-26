# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Part of the shared math core (no host program needed).
from __future__ import print_function, division
import json
import math
import os
import time
import traceback
from .constants import SEAM_ANG, SEAM_K
from .vectors import fl, v_add, v_cross, v_dist, v_dot, v_len, v_lerp, v_mul, v_sub, v_unit
from .bspline import BDir
from .evaluate import crv_point, srf_coefs, srf_derivs, srf_point
from .lsq import LSQ, gauss_solve
from .curve_fit import chord_params, dist_points_to_polyline
from .patch_fit import LEVEL_NAME, SIDE_MAP, W_G0, W_G1, W_G2, add_fairing, normal_curv_neighbour, project_param, side_row_indices
# <<< dev-only
# =============================================================================
# SMOOTHING OF A FINISHED PATCH (multi-patch, continuity kept)
# =============================================================================
# Quantities on side k of a patch, as (ku, kv, factor) of a surface derivative:
#   pt  boundary point          a1/a2  1st/2nd derivative ALONG the side (per t)
#   c1/c2  1st/2nd derivative ACROSS, into the patch      tw  d(c1)/dt
QSPEC = [
    {"pt": (0, 0, 1.0), "a1": (1, 0, 1.0), "a2": (2, 0, 1.0), "c1": (0, 1, 1.0), "c2": (0, 2, 1.0), "tw": (1, 1, 1.0)},
    {"pt": (0, 0, 1.0), "a1": (0, 1, 1.0), "a2": (0, 2, 1.0), "c1": (1, 0, -1.0), "c2": (2, 0, 1.0), "tw": (1, 1, -1.0)},
    {"pt": (0, 0, 1.0), "a1": (1, 0, -1.0), "a2": (2, 0, 1.0), "c1": (0, 1, -1.0), "c2": (0, 2, 1.0), "tw": (1, 1, 1.0)},
    {"pt": (0, 0, 1.0), "a1": (0, 1, -1.0), "a2": (0, 2, 1.0), "c1": (1, 0, 1.0), "c2": (2, 0, 1.0), "tw": (1, 1, -1.0)},
]


class SPatch(object):
    """A single-span (Bezier) patch taking part in smoothing."""
    def __init__(self, key, pu, pv, P):
        self.key = key
        self.du = BDir(pu, 1); self.dv = BDir(pv, 1)
        self.P = list(P)
        self.off = 0
        self.move = set()      # local CV indices allowed to move

    def n(self): return self.du.n * self.dv.n

    def val(self, k, t, q, P=None):
        ku, kv, f = QSPEC[k][q]
        u, v = SIDE_MAP[k][0](t)
        D = srf_derivs(self.du, self.dv, P if P is not None else self.P, u, v, ku + kv)
        return v_mul(D[(ku, kv)], f)

    def coefs(self, k, t, q, scale=1.0):
        ku, kv, f = QSPEC[k][q]
        u, v = SIDE_MAP[k][0](t)
        return [(self.off + i, f * c * scale) for (i, c) in srf_coefs(self.du, self.dv, u, v, ku, kv)]


def _closest_on_side(Y, l, p, P=None):
    best = None
    for i in range(41):
        s = i / 40.0
        d = v_dist(Y.val(l, s, "pt", P), p)
        if best is None or d < best[0]: best = (d, s)
    lo, hi = max(0.0, best[1] - 0.025), min(1.0, best[1] + 0.025)
    for _ in range(30):
        m1 = lo + (hi - lo) / 3.0; m2 = hi - (hi - lo) / 3.0
        if v_dist(Y.val(l, m1, "pt", P), p) < v_dist(Y.val(l, m2, "pt", P), p): hi = m2
        else: lo = m1
    s = 0.5 * (lo + hi)
    return s, v_dist(Y.val(l, s, "pt", P), p)


def find_seams(patches, tol):
    """Shared (overlapping) sides between patches: list of dicts."""
    seams = []
    poly = {}
    for P_ in patches:            # cheap side polylines for a quick reject
        for k in range(4):
            poly[(id(P_), k)] = [P_.val(k, i / 10.0, "pt") for i in range(11)]
    for xi in range(len(patches)):
        X = patches[xi]
        for yi in range(xi + 1, len(patches)):
            Y = patches[yi]
            for k in range(4):
                for l in range(4):
                    px = poly[(id(X), k)]; py = poly[(id(Y), l)]
                    seg = max(v_dist(py[0], py[-1]), v_dist(px[0], px[-1])) / 10.0
                    near = sum(1 for q in px[1:-1] if min(dist_points_to_polyline([q], py)) < tol + seg)
                    if near < 3: continue
                    ts = [0.05 + 0.9 * i / 9.0 for i in range(10)]
                    close = []
                    for t in ts:
                        s, d = _closest_on_side(Y, l, X.val(k, t, "pt"))
                        if d < tol: close.append((t, s))
                    if len(close) < 3: continue
                    # overlap ends: X ends that lie on Y's side, else Y ends on X's side
                    ends = []
                    for t in (0.0, 1.0):
                        s, d = _closest_on_side(Y, l, X.val(k, t, "pt"))
                        if d < tol: ends.append((t, s))
                    for s in (0.0, 1.0):
                        t, d = _closest_on_side(X, k, Y.val(l, s, "pt"))
                        if d < tol: ends.append((t, s))
                    if len(ends) < 2:
                        ends = [close[0], close[-1]]
                    ends.sort()
                    (ta, sa), (tb, sb) = ends[0], ends[-1]
                    if tb - ta < 0.05: continue
                    seams.append({"X": X, "k": k, "Y": Y, "l": l, "ta": ta, "tb": tb, "sa": sa, "sb": sb})
    return seams


def _seam_samples(sm, K=14):
    """K samples along the seam: (t on X, s on Y, tau 0..1, r = ds/dt).
    Full edges (corner to corner on both): straight share of the parameter.
    Otherwise s is the CLOSEST point of Y's side to X(t) - not a straight share of
    the parameter. A share is only right while both edges are parametrised
    alike; after rows were moved along an edge (Even / Straight / ToDev /
    Relax) or where one edge meets two patches (T-joint) it compared points
    that are not opposite each other: the neighbour's edge was dragged
    along the seam and G2 was measured as G0. r is the local speed ratio."""
    X, k, Y, l = sm["X"], sm["k"], sm["Y"], sm["l"]
    key = (id(X.P), id(Y.P))
    cache = sm.setdefault("_samp", {})
    if cache.get(K, (None,))[0] == key:
        return cache[K][1]
    ta, tb, sa, sb = sm["ta"], sm["tb"], sm["sa"], sm["sb"]
    lin = None
    if True:
        # also on a PART of an edge (T-joint): the short edge is then an exact
        # piece of the long one (a polynomial restricted to an interval) - a
        # pairing by closest point can never be met exactly between two
        # polynomials, and the strong G0 equations bent the long patch by
        # many mm to close a gap of a few microns
        # corner to corner on both sides: pairing by parameter is right when
        # it fits the geometry (same-size patches; an edge whose corner moved
        # away, FreeEdgeEdt - a closest point would stop at the old corner).
        # It is WRONG when the CVs along the two edges are spread differently
        # (a skewed blend): then it sees a false gap and drags the neighbour's
        # edge along the seam - checked below against the closest points.
        r_ = (sb - sa) / (tb - ta)
        lin = [(ta + (tb - ta) * (i / (K - 1.0)), sa + (sb - sa) * (i / (K - 1.0)), i / (K - 1.0), r_)
               for i in range(K)]
    out = []
    for i in range(K):
        tau = i / (K - 1.0)
        t = ta + (tb - ta) * tau
        p = X.val(k, t, "pt")
        s = sa + (sb - sa) * tau                    # start: the straight share
        for _ in range(12):                          # Newton: (Y(s) - p) . Y'(s) = 0
            d0 = v_sub(Y.val(l, s, "pt"), p)
            d1 = Y.val(l, s, "a1"); d2 = Y.val(l, s, "a2")
            f = v_dot(d0, d1); fp = v_dot(d1, d1) + v_dot(d0, d2)
            if abs(fp) < 1e-300: break
            ds = -f / fp
            s = min(1.0, max(0.0, s + ds))
            if abs(ds) < 1e-12: break
        if v_dist(Y.val(l, s, "pt"), p) > 1e-6 + 1.5 * v_dist(Y.val(l, sa + (sb - sa) * tau, "pt"), p):
            s = _closest_on_side(Y, l, p)[0]         # Newton went astray: plain search
        ya = Y.val(l, s, "a1")
        r = v_dot(X.val(k, t, "a1"), ya) / max(v_dot(ya, ya), 1e-300)
        out.append((t, s, tau, r))
    if lin is not None:
        g_lin = max(v_dist(X.val(k, t, "pt"), Y.val(l, s, "pt")) for (t, s, tau, r) in lin)
        g_cls = max(v_dist(X.val(k, t, "pt"), Y.val(l, s, "pt")) for (t, s, tau, r) in out)
        size = v_dist(X.val(k, 0.0, "pt"), X.val(k, 1.0, "pt"))
        if g_lin <= 1.5 * g_cls + 1e-4 * size:
            out = lin
    cache[K] = (key, out)
    return out


def seam_measure(sm, g0tol):
    """Current continuity level (0/1/2, -1 = not even G0) + numbers."""
    X, k, Y, l = sm["X"], sm["k"], sm["Y"], sm["l"]
    gap = ang = kd = kmax = 0.0
    for (t, s, tau, r) in _seam_samples(sm, 9):
        gap = max(gap, v_dist(X.val(k, t, "pt"), Y.val(l, s, "pt")))
        xa = X.val(k, t, "a1"); xc = X.val(k, t, "c1")
        nx = v_unit(v_cross(xa, xc))
        ny = v_unit(v_cross(v_mul(Y.val(l, s, "a1"), r), Y.val(l, s, "c1")))
        ang = max(ang, math.degrees(math.acos(min(1.0, abs(v_dot(nx, ny))))))
        k1 = v_dot(X.val(k, t, "c2"), nx) / max(v_dot(xc, xc), 1e-300)
        Qr = v_mul(Y.val(l, s, "c1"), -1.0)
        k2 = normal_curv_neighbour(xc, Qr, Y.val(l, s, "c2"), xa, X.val(k, t, "a2"),
                                   v_mul(Y.val(l, s, "tw"), -r), nx)
        kd = max(kd, abs(k1 - k2)); kmax = max(kmax, abs(k1), abs(k2))
    level = -1
    if gap <= g0tol:
        level = 0
        if ang <= SEAM_ANG:
            level = 1
            if kd <= SEAM_K * max(kmax, 1e-9) or kmax < 1e-7:
                level = 2
    sm.update({"gap": gap, "ang": ang, "dk": kd / max(kmax, 1e-12)})
    return level


SEAM_ANG_HOLD = 1.0     # deg: patches without a stored level - still counted as G1 up to this kink
SEAM_K_HOLD = 0.30      # ... and as G2 up to this relative curvature difference


def seam_level(sm, tol):
    """The level a seam is KEPT at by every patch button (never lower):
      the level stored on the patches (set when built / by Match), else
      what it is now, with a generous reading - a G2 seam that drifted a
      little (kink < 1 deg, curvature within 30%) still counts as G2, so
      repeated clicks bring it back instead of letting it slip to G1/G0.
    A seam found but opened (gap) is closed again at that level."""
    lv = seam_measure(sm, tol)
    w = max(getattr(sm["X"], "want", {}).get(sm["k"], -1), getattr(sm["Y"], "want", {}).get(sm["l"], -1))
    if w >= 0:
        lv = w
    elif lv >= 0:
        if sm.get("ang", 99.0) <= SEAM_ANG_HOLD:
            lv = max(lv, 1)
            if sm.get("dk", 99.0) <= SEAM_K_HOLD:
                lv = 2
    else:
        lv = 0                                   # found (close) but open: close it again
    sm["level"] = lv
    return lv


def _small_lsq(rows):
    """rows: list of (coef vector over m unknowns, rhs) -> least squares."""
    m = len(rows[0][0])
    A = [[0.0] * m for _ in range(m)]; B = [[0.0] for _ in range(m)]
    for (c, r) in rows:
        for i in range(m):
            B[i][0] += c[i] * r
            for j in range(m):
                A[i][j] += c[i] * c[j]
    for i in range(m): A[i][i] += 1e-12
    return [x[0] for x in gauss_solve(A, B)]


def seam_scalars(sm):
    """Reparametrisation of the seam (a, beta, b, gamma) - kept fixed while
    the CVs move, so the continuity stays of the same kind.
    Safe also for a seam that is NOT at its level now (an earlier step bent
    it, G2 wanted but G0 now): a fitted from a bent seam can be negative or
    huge - that threw the neighbour's rows out more with every click. So:
      a     > 0 always: the ratio of the cross-row lengths of both sides
            (the fitted value only if it is close to that ratio);
      beta  fitted with that a;
      b, g  (G2) fitted as before (the equations are scaled, see
            _norm_terms, so they cannot throw CVs out any more)."""
    X, k, Y, l = sm["X"], sm["k"], sm["Y"], sm["l"]
    S = _seam_samples(sm)

    def perp(v, t):                                 # part of v across the edge
        tu = v_unit(t)
        return v_sub(v, v_mul(tu, v_dot(v, tu)))
    nx = ny = 0.0
    for (t, s, tau, r) in S:
        xa = X.val(k, t, "a1")
        nx += v_len(perp(X.val(k, t, "c1"), xa)); ny += v_len(perp(Y.val(l, s, "c1"), xa))
    ratio = nx / max(ny, 1e-300)
    rows = []
    for (t, s, tau, r) in S:
        xc = X.val(k, t, "c1"); yc = Y.val(l, s, "c1"); xa = X.val(k, t, "a1")
        for c in range(3):    # xc = -a*yc + (b0(1-tau) + b1 tau) xa
            rows.append(([-yc[c], (1 - tau) * xa[c], tau * xa[c]], xc[c]))
    a, b0, b1 = _small_lsq(rows)
    if not (0.5 * ratio <= a <= 2.0 * ratio):
        a = ratio
    rows = []
    for (t, s, tau, r) in S:                        # beta with that a
        xc = X.val(k, t, "c1"); yc = Y.val(l, s, "c1"); xa = X.val(k, t, "a1")
        rhs = v_add(xc, v_mul(yc, a))
        for c in range(3):
            rows.append(([(1 - tau) * xa[c], tau * xa[c]], rhs[c]))
    b0, b1 = _small_lsq(rows)
    sm.update({"a": a, "b0": b0, "b1": b1})
    rows = []
    for (t, s, tau, r) in S:
        beta = (1 - tau) * b0 + tau * b1
        R = v_sub(X.val(k, t, "c2"), v_mul(Y.val(l, s, "c2"), a * a))
        R = v_add(R, v_mul(Y.val(l, s, "tw"), 2 * a * beta * r))
        R = v_sub(R, v_mul(X.val(k, t, "a2"), beta * beta))
        yc = Y.val(l, s, "c1"); xa = X.val(k, t, "a1")
        for c in range(3):    # R = -b*yc + (g0(1-tau) + g1 tau) xa
            rows.append(([-yc[c], (1 - tau) * xa[c], tau * xa[c]], R[c]))
    b, g0, g1 = _small_lsq(rows)
    sm.update({"b": b, "g0": g0, "g1": g1})


def _norm_terms(terms):
    """Seam equation scaled to coefficients of size 1: a narrow blend next to
    a big patch gives G1/G2 rows with coefficients thousands of times larger
    on one side - with the huge seam weights the solve then lost the small
    'stay where you are' terms and CVs drifted far away."""
    mx = max([abs(c) for (i, c) in terms] + [1e-300])
    return [(i, c / mx) for (i, c) in terms]


def seam_keep_level(sm, tol, repair_far=False):
    """Up to which level the seam is ALREADY right (kink / curvature, the gap
    aside): there the buttons keep its G1 / G2 exactly as it is and only
    follow the change. The simple model of the G1/G2 equations (constant
    speed ratio, linear shear) does not fit every exact seam: forced onto
    it, a seam that was G2 was 'repaired' - the neighbour's rows slid by
    many mm, at every click again (worst at a corner of four patches)."""
    want = sm.get("level", 0)
    c_ = dict(sm)
    lv = seam_measure(c_, max(tol, 1e-9))
    # all or nothing: a seam below its level is repaired fully (keeping its
    # G1 as it is while forcing G2 onto the model contradicted itself)
    if lv >= want:
        return want
    return 0                                           # below its level: repaired (the buttons hold G at all costs)


def _eval_terms(terms, pts):
    x = y = z = 0.0
    for (i, c) in terms:
        q = pts[i]
        x += c * q[0]; y += c * q[1]; z += c * q[2]
    return (x, y, z)


def add_seam_eqs(L, sm, level, keep=0, old=None):
    """keep: G1 (1) or G1+G2 (2) equations keep the value they have in the
    CVs old {global index: point} (the seam was right there) instead of 0.
    G0 is always made exact."""
    X, k, Y, l = sm["X"], sm["k"], sm["Y"], sm["l"]
    Z = (0.0, 0.0, 0.0)
    for (t, s, tau, r) in _seam_samples(sm):
        L.add(_norm_terms(X.coefs(k, t, "pt") + Y.coefs(l, s, "pt", -1.0)), Z, W_G0)
        if level >= 1:
            beta = (1 - tau) * sm["b0"] + tau * sm["b1"]
            tm = _norm_terms(X.coefs(k, t, "c1") + Y.coefs(l, s, "c1", sm["a"]) + X.coefs(k, t, "a1", -beta))
            L.add(tm, _eval_terms(tm, old) if (keep >= 1 and old is not None) else Z, W_G1)
        if level >= 2:
            a = sm["a"]; gam = (1 - tau) * sm["g0"] + tau * sm["g1"]
            tm = _norm_terms(X.coefs(k, t, "c2") + Y.coefs(l, s, "c2", -a * a) + Y.coefs(l, s, "tw", 2 * a * beta * r)
                             + X.coefs(k, t, "a2", -beta * beta) + Y.coefs(l, s, "c1", sm["b"])
                             + X.coefs(k, t, "a1", -gam))
            L.add(tm, _eval_terms(tm, old) if (keep >= 2 and old is not None) else Z, W_G2)


class _OffsetL(object):
    """LSQ view with shifted CV indices (one patch inside a joint system)."""
    def __init__(self, L, off): self.L = L; self.off = off
    def add(self, terms, rhs, w): self.L.add([(i + self.off, c) for (i, c) in terms], rhs, w)


def mirror_rows(p, mirror):
    """Sides of patch p lying on the mirror plane: [(k, row0, row1 or None,
    row2 or None)] - row1 when it goes straight across (G1), row2 when it is
    at twice row1's distance from the plane (G2 like MatchSrf average)."""
    if not mirror: return []
    axis, val, mtol = mirror
    out = []
    for k in range(4):
        r0 = side_row_indices(p.du, p.dv, k, 0)
        if any(abs(p.P[i][axis] - val) > mtol for i in r0): continue
        r1 = side_row_indices(p.du, p.dv, k, 1)
        straight = all(abs(p.P[i1][c] - p.P[i0][c]) <= mtol for i0, i1 in zip(r0, r1) for c in range(3) if c != axis)
        r2 = None
        if straight and (p.du.n if k % 2 else p.dv.n) > 2:
            r2_ = side_row_indices(p.du, p.dv, k, 2)
            if all(abs(p.P[i2][axis] - 2.0 * p.P[i1][axis] + p.P[i0][axis]) <= mtol for i0, i1, i2 in zip(r0, r1, r2_)):
                r2 = r2_
        out.append((k, r0, r1 if straight else None, r2))
    return out


def add_mirror_eqs(L, p, mirror):
    """Mirror side stays a mirror side: row0 ON the plane (free inside it),
    row1 straight across it (G1), row2 at twice row1's distance (G2)."""
    axis, val, mtol = mirror
    for (k, r0, r1, r2) in mirror_rows(p, mirror):
        for i0 in r0:
            L.add_axis([(p.off + i0, 1.0)], axis, val, W_G0)
        if r1:
            for i0, i1 in zip(r0, r1):
                for c in range(3):
                    if c != axis:
                        L.add_axis([(p.off + i1, 1.0), (p.off + i0, -1.0)], c, 0.0, W_G1)
        if r2:
            for i0, i1, i2 in zip(r0, r1, r2):
                L.add_axis([(p.off + i2, 1.0), (p.off + i1, -2.0), (p.off + i0, 1.0)], axis, 0.0, W_G2)


NB_PULL_W = 0.15     # Pull2Msh: weight of the neighbours' own scan (the pulled patch = 1)


NB_MOVE_FRAC = 0.12   # a neighbour moving more than this share of its size in one step = thrown out


def _move_guard(A, moving, P_ref):
    """-> (anchors, ok_fn) for LSQ.solve_safe: the neighbours of A (not A)
    may not move more than NB_MOVE_FRAC of their size from P_ref."""
    anchors = []
    lims = []
    for p in moving:
        if p is A:
            continue
        P0 = P_ref[p.key]
        lo = [min(q[c] for q in P0) for c in range(3)]
        hi = [max(q[c] for q in P0) for c in range(3)]
        size = math.sqrt(sum((hi[c] - lo[c]) ** 2 for c in range(3)))
        lims.append((p, NB_MOVE_FRAC * size))
        for i in range(p.n()):
            if i in p.move:
                anchors.append((p.off + i, P0[i]))

    def ok(sol):
        for (p, lim) in lims:
            P0 = P_ref[p.key]
            for i in range(p.n()):
                if v_dist(sol[p.off + i], P0[i]) > lim:
                    return False
        return True
    return anchors, ok


def _part_of(sm, P):
    """True if the seam covers only a PART of P's edge (P is the longer one at
    a T-joint). P can never follow there exactly: its single-span edge would
    have to BE the other edge on that part, i.e. its whole edge would be the
    other edge extended - CVs thrown out by hundreds of mm."""
    if sm["X"] is P:
        return abs(sm["tb"] - sm["ta"]) < 0.98
    return abs(sm["sb"] - sm["sa"]) < 0.98


def _pull_in_corners(A, seams, moving, rings=2, fixed=()):
    """A neighbour P that must move its rows to A, while some of THOSE rows
    are also next to a seam with a patch Q that does not move (a corner: the
    patch diagonal to A, or the patch above a blend and its two sides) -
    P's CVs there were frozen AND had to move: the equations fought, the
    solver threw the rest of P out, and the seam did not close either.
    Then Q moves too: only its continuity rows at its seams with the moving
    patches (Q's own rows towards patches that stay are frozen as usual).
    -> patches added (moving is extended)."""
    added = []
    for _ in range(rings):
        new_ = []
        for sm in seams:
            if sm["level"] < 0:
                continue
            for (P_, kk, Q_, ll) in ((sm["X"], sm["k"], sm["Y"], sm["l"]), (sm["Y"], sm["l"], sm["X"], sm["k"])):
                if P_ not in moving or Q_ in moving or Q_ is A or Q_ in fixed or Q_ in new_ or _part_of(sm, Q_):
                    continue
                rows = set()
                for rr in range(sm["level"] + 1):
                    rows.update(side_row_indices(P_.du, P_.dv, kk, rr))
                if rows & P_.move:
                    new_.append(Q_)
        if not new_:
            break
        for Q_ in new_:
            Q_.move = set()
            moving.append(Q_)
            added.append(Q_)
        for sm in seams:
            if sm["level"] < 0:
                continue
            for (P_, kk, Q_, ll) in ((sm["X"], sm["k"], sm["Y"], sm["l"]), (sm["Y"], sm["l"], sm["X"], sm["k"])):
                if Q_ in new_ and P_ in moving and P_ not in new_:
                    for rr in range(sm["level"] + 1):
                        Q_.move.update(side_row_indices(Q_.du, Q_.dv, ll, rr))
                        # and P's rows towards Q too (not A's): the cheaper
                        # side gives way - a narrow blend instead of the wide
                        # patch next to it (that one would move 1/a^2 times)
                        if P_ is not A:
                            P_.move.update(side_row_indices(P_.du, P_.dv, kk, rr))
    return added


SCALAR_KEYS = ("a", "b0", "b1", "b", "g0", "g1")


def seam_key(sm):
    return (sm["X"].key, sm["k"], sm["Y"].key, sm["l"])


def scalars_before(seams):
    """The seam scalars of the state BEFORE a change (seams exact then): the
    G1/G2 equations of all seams at a corner agree with each other. Refitted
    from the changed (bent) geometry they did not - at a corner of four
    patches G2 slid to G1/G0 with every click."""
    out = {}
    for sm in seams:
        if sm.get("level", -1) >= 1:
            seam_scalars(sm)
            out[seam_key(sm)] = dict((k, sm[k]) for k in SCALAR_KEYS)
    return out


def adjust_step(A, others, target_fn, tol=1e-3, extra_freeze=None, data_fn=None, mirror=None, force_levels=None,
                keep_scalars=None, P_before=None, keep_levels=None):
    """One adjustment step of patch A, shared by Smooth and Pull2Msh.
    target_fn(A, fixed) -> wanted CVs of A (fixed: CVs of A that may not move).
    mirror = (axis, plane value, tol): sides on the symmetry plane stay on it
    (their CVs may slide inside the plane) and keep G1/G2 across it.
    data_fn(p) -> [(u, v, mesh point)]: PULL mode - every moved patch is fitted
    to its mesh in ONE joint solve (neighbours follow A to keep the seams, and
    stay on their mesh with what they may move); CV anchors are then weak.
    Moves: the CVs of A that are free; of each direct neighbour only the rows
    taken by its continuity with A. Every seam among moved patches keeps its
    level; rows touching a seam with a patch that does not move are frozen.
    Returns (moved patches, seams kept, {key: new CVs})."""
    allp = [A] + [p for p in others if p is not A]
    seams = find_seams(allp, tol)
    for sm in seams:
        seam_level(sm, tol)
        key = (sm["X"].key, sm["k"], sm["Y"].key, sm["l"])
        if force_levels and key in force_levels:
            sm["level"] = force_levels[key]          # level from BEFORE a layout step
    # who moves what
    A.move = set(range(A.n()))
    moving = [A]
    for sm in seams:
        if sm["level"] < 0: continue
        for (P_, kk, Q_, ll) in ((sm["X"], sm["k"], sm["Y"], sm["l"]), (sm["Y"], sm["l"], sm["X"], sm["k"])):
            if P_ is A and Q_ is not A:
                if _part_of(sm, Q_):
                    continue                     # T-joint, A is the short one: A keeps to Q (frozen below)
                if Q_ not in moving:
                    Q_.move = set(); moving.append(Q_)
                for rr in range(sm["level"] + 1):
                    Q_.move.update(side_row_indices(Q_.du, Q_.dv, ll, rr))
    _pull_in_corners(A, seams, moving)
    # seams to patches that do not move freeze the rows next to them
    for sm in seams:
        if sm["level"] < 0: continue
        for (P_, kk, Q_) in ((sm["X"], sm["k"], sm["Y"]), (sm["Y"], sm["l"], sm["X"])):
            if P_ in moving and Q_ not in moving:
                for rr in range(sm["level"] + 1):
                    P_.move.difference_update(side_row_indices(P_.du, P_.dv, kk, rr))
    for p in moving:              # sides touching surfaces we cannot handle
        for (kk, nrows) in (extra_freeze or {}).get(p.key, []):
            for rr in range(nrows):
                p.move.difference_update(side_row_indices(p.du, p.dv, kk, rr))
    if not A.move:
        return moving, [], dict((p.key, list(p.P)) for p in moving)
    bound_sides = set()
    for sm in seams:
        if sm["level"] < 0: continue
        if sm["X"] is A: bound_sides.add(sm["k"])
        if sm["Y"] is A: bound_sides.add(sm["l"])
    for (k_, r0_, r1_, r2_) in mirror_rows(A, mirror):
        bound_sides.add(k_)
    A.free_sides = set(k_ for k_ in range(4) if k_ not in bound_sides)
    F = target_fn(A, dict((i, A.P[i]) for i in range(A.n()) if i not in A.move))
    off = 0
    for p in moving:
        p.off = off; off += p.n()
    # joint solve: A towards F, neighbours stay, all seams exact
    fixed = {}
    for p in moving:
        for i in range(p.n()):
            if i not in p.move:
                fixed[p.off + i] = p.P[i]
    L = LSQ(off, 0, fixed)
    if data_fn is None:
        for p in moving:                      # the clicked patch wins, the neighbours follow
            tgt = F if p is A else p.P
            w_ = 1.0 if p is A else NB_PULL_W
            for i in range(p.n()):
                L.add([(p.off + i, 1.0)], tgt[i], w_)
    else:
        for p in moving:
            data = data_fn(p) or []
            wd = 1.0 if p is A else NB_PULL_W        # the pulled patch wins, neighbours give way
            for (u, v, q) in data:
                L.add([(p.off + i, c) for (i, c) in srf_coefs(p.du, p.dv, u, v, 0, 0)], q, wd)
            ws = max(1.0, float(len(data))) / p.n()
            for i in range(p.n()):                    # weak: keeps the solve regular
                L.add([(p.off + i, 1.0)], p.P[i], 0.01 * ws)
            add_fairing(_OffsetL(L, p.off), p.du.n, p.dv.n, 0.002 * ws)
    # the state BEFORE the change (layout_step has already changed A): the
    # point pairs along every seam come from it too - the equations keep the
    # seam as it WAS, so they must compare the same points as then
    Pb = dict((p.key, (P_before or {}).get(p.key, p.P)) for p in moving)
    if P_before:
        for sm in seams:
            if sm["level"] < 0 or not (sm["X"] in moving and sm["Y"] in moving):
                continue
            X_, Y_ = sm["X"], sm["Y"]
            cur = (X_.P, Y_.P)
            try:
                X_.P, Y_.P = Pb[X_.key], Pb[Y_.key]
                S_ = list(_seam_samples(sm))
            finally:
                X_.P, Y_.P = cur
            sm.setdefault("_samp", {})[14] = ((id(X_.P), id(Y_.P)), S_)
    old = {}
    for p in moving:
        for i in range(p.n()):
            old[p.off + i] = Pb[p.key][i]
    used = []
    for sm in seams:
        if sm["level"] < 0: continue
        if sm["X"] in moving and sm["Y"] in moving:
            keep = 0
            if sm["level"] >= 1:
                ks = (keep_scalars or {}).get(seam_key(sm))
                if ks:
                    sm.update(ks)
                else:
                    seam_scalars(sm)
                if keep_levels is not None and seam_key(sm) in keep_levels:
                    keep = keep_levels[seam_key(sm)]
                else:
                    keep = seam_keep_level(sm, tol)
            add_seam_eqs(L, sm, sm["level"], keep, old)
            used.append(sm)
    if mirror:
        for p in moving:
            add_mirror_eqs(L, p, mirror)
    anc, ok_ = _move_guard(A, moving, dict((p.key, p.P) for p in moving))
    sol = L.solve_safe(anc, ok_)[0]
    new = {}
    for p in moving:
        new[p.key] = [sol[p.off + i] for i in range(p.n())]
    return moving, used, new


def fair_target(strength=0.5):
    """Smooth: implicit fairing of A alone (4th differences)."""
    def fn(A, fixed):
        LA = LSQ(A.n())
        for i in range(A.n()):
            LA.add([(i, 1.0)], A.P[i], 1.0)
        add_fairing(LA, A.du.n, A.dv.n, strength)
        return LA.solve()[0]
    return fn


def scan_target(data, strength=0.5):
    """Pull2Msh: fit A to mesh samples data = [(u, v, point)], fairing as in
    the build, then go `strength` of the way (one click = one step)."""
    def fn(A, fixed):
        if not data:
            return list(A.P)
        L = LSQ(A.n(), 0, fixed)
        for (u, v, q) in data:
            L.add(srf_coefs(A.du, A.dv, u, v, 0, 0), q, 1.0)
        for i in range(A.n()):
            L.add([(i, 1.0)], A.P[i], 1e-4)
        add_fairing(L, A.du.n, A.dv.n, 0.0005 * len(data) / A.n())
        Fit = L.solve()[0]
        return [v_lerp(p, q, strength) for p, q in zip(A.P, Fit)]
    return fn


def _row_targets(p, lo, hi, gaps, dev=None, step=0.5):
    """New parameters of the CV rows of one direction (degree p, single span).
    Rows 0..lo-1 and p-hi+1..p stay; the free rows between move `step` of the
    way towards:
      dev given : equal shares of 1 + 4*dev/max(dev) (rows gather where the
                  mesh is far)  -> Even/ToDev
      dev None  : equal 3D spacing                 -> Even
    Gaps stay 0.5..1.6 of the mean gap IN SPACE (no bunching, no bare edge).
    gaps: 3D distances between the rows now. -> list or None (nothing to do)."""
    g = [i / float(p) for i in range(p + 1)]
    a, b = lo - 1, p - hi + 1
    if b - a < 2: return None
    # current 3D position of every row along the direction, and its inverse
    cum = [0.0]
    for d in gaps: cum.append(cum[-1] + d)
    def par_at(x):                             # 3D length -> parameter (rows are at g)
        for k in range(p):
            if x <= cum[k + 1] or k == p - 1:
                f = (x - cum[k]) / max(cum[k + 1] - cum[k], 1e-12)
                return g[k] + f * (g[k + 1] - g[k])
    La, Lb = cum[a], cum[b]
    if dev is None:
        want = [La + (Lb - La) * (i - a) / float(b - a) for i in range(a + 1, b)]
    else:
        m = len(dev) - 1
        if max(dev) <= 0 or max(dev) < 1.3 * sum(dev) / float(len(dev)): return None
        w = lambda s: 1.0 + 4.0 * dev[min(m, int(round(s * m)))] / max(dev)
        K = 200; xs = [La + (Lb - La) * k / float(K) for k in range(K + 1)]
        acc = [0.0]
        for k in range(K):
            acc.append(acc[-1] + 0.5 * (w(par_at(xs[k])) + w(par_at(xs[k + 1]))) * (xs[k + 1] - xs[k]))
        want = []
        for i in range(a + 1, b):
            tg = acc[-1] * (i - a) / float(b - a); k = 0
            while k < K - 1 and acc[k + 1] < tg: k += 1
            f = (tg - acc[k]) / max(acc[k + 1] - acc[k], 1e-300)
            want.append(xs[k] + f * (xs[k + 1] - xs[k]))
    mean = (Lb - La) / float(b - a)
    pos = list(cum)
    for n_, i in enumerate(range(a + 1, b)):
        pos[i] = cum[i] + step * (want[n_] - cum[i])
    for _ in range(3):                         # spacing limits in space: 0.5 .. 1.6 of the mean
        for i in range(a + 1, b):
            pos[i] = max(pos[i], pos[i - 1] + 0.5 * mean, pos[i + 1] - 1.6 * mean)
        for i in range(b - 1, a, -1):
            pos[i] = min(pos[i], pos[i + 1] - 0.5 * mean, pos[i - 1] + 1.6 * mean)
    if max(abs(pos[i] - cum[i]) for i in range(a + 1, b)) < 0.02 * mean: return None
    return [par_at(x) for x in pos]


ITER_SLIDE = 5


def _profile_peaks(prof, lo_s, hi_s):
    """Local maxima of a 0..1 profile strictly inside (lo_s, hi_s): [(value, s)]."""
    m = len(prof) - 1; out = []
    for k in range(m + 1):
        s = k / float(m)
        if not (lo_s < s < hi_s): continue
        left = prof[k - 1] if k > 0 else -1.0
        right = prof[k + 1] if k < m else -1.0
        if prof[k] >= left and prof[k] >= right and prof[k] > 0:
            out.append((prof[k], s))
    return out


def _move_one_row(p, lo, hi, gaps, s_target, step=1.0):
    """Row parameters with the free row nearest s_target moved `step` of the
    way to it, and the row behind it (the one it moves away from) following
    half as far - one row alone hardly changes anything. 3D gaps kept
    0.45 .. 1.75 of the mean gap.
    -> (new params, row index) or (None, reason)."""
    g = [i / float(p) for i in range(p + 1)]
    a, b = lo - 1, p - hi + 1
    if b - a < 2: return None, "no free row"
    i = min(range(a + 1, b), key=lambda i_: abs(g[i_] - s_target))
    even = 1.0 / p
    if abs(g[i] - s_target) < 0.3 * even: return None, "row already there"
    cum = [0.0]
    for d in gaps: cum.append(cum[-1] + d)
    len_at = lambda s: cum[min(p - 1, int(s * p))] + (s * p - min(p - 1, int(s * p))) * gaps[min(p - 1, int(s * p))]
    def par_at(x):
        for k in range(p):
            if x <= cum[k + 1] or k == p - 1:
                return g[k] + (x - cum[k]) / max(cum[k + 1] - cum[k], 1e-12) * even
    mean = (cum[b] - cum[a]) / float(b - a)
    pos = list(cum)
    x = cum[i] + step * (len_at(s_target) - cum[i])
    sgn = 1 if x > cum[i] else -1
    j = i - sgn                                  # the row it moves away from follows half way
    if a < j < b:
        pos[j] = cum[j] + 0.5 * (x - cum[i])
    pos[i] = x
    for _ in range(3):                           # 3D spacing 0.45 .. 1.75 of the mean
        for k in (j, i) if sgn > 0 else (i, j):
            if not (a < k < b): continue
            pos[k] = max(pos[k], pos[k - 1] + 0.45 * mean, pos[k + 1] - 1.75 * mean)
            pos[k] = min(pos[k], pos[k + 1] - 0.45 * mean, pos[k - 1] + 1.75 * mean)
    if abs(pos[i] - cum[i]) < 0.05 * mean: return None, "row cannot go further (spacing)"
    new = list(g)
    for k in range(a + 1, b):
        new[k] = par_at(pos[k])
    return new, i


def cv_normals(p, P=None):
    """Unit surface normal at every CV's Greville point."""
    P = P if P is not None else p.P
    out = []
    for i in range(p.du.n):
        for j in range(p.dv.n):
            D = srf_derivs(p.du, p.dv, P, i / float(p.du.p), j / float(p.dv.p), 1)
            n = v_cross(D[(1, 0)], D[(0, 1)])
            out.append(v_unit(n) if v_len(n) > 1e-300 else (0.0, 0.0, 1.0))
    return out


def side_length(p, k, P=None):
    P = P if P is not None else p.P
    r = side_row_indices(p.du, p.dv, k, 0)
    return sum(v_dist(P[r[a]], P[r[a + 1]]) for a in range(len(r) - 1))


def short_side(p, k, ratio=0.3):
    """Side k is the short end of a narrow patch (a fillet end): much shorter
    than the sides next to it."""
    other = 0.5 * (side_length(p, (k + 1) % 4) + side_length(p, (k + 3) % 4))
    return side_length(p, k) < ratio * other


def normal_only(base_fn):
    """Target of base_fn, but each CV moves ONLY along the surface normal -
    the shape changes, the layout of the CVs (spacing, straightness) stays.
    Free edges (no neighbour, no mirror): their OUTLINE is shape too - the
    edge CVs may also move sideways (across the edge), not along it."""
    def fn(A, fixed):
        F = base_fn(A, fixed)
        N = cv_normals(A)
        out = [v_add(p, v_mul(n, v_dot(v_sub(f, p), n))) for p, f, n in zip(A.P, F, N)]
        for k in getattr(A, "free_sides", ()):
            r0 = side_row_indices(A.du, A.dv, k, 0)
            if short_side(A, k):
                continue        # short end of a narrow patch: "across" = along the patch - it zigzagged
            for m_, i in enumerate(r0):
                a_ = r0[max(0, m_ - 1)]; b_ = r0[min(len(r0) - 1, m_ + 1)]
                t = v_unit(v_sub(A.P[b_], A.P[a_]))
                mv = v_sub(F[i], A.P[i])
                out[i] = v_add(A.P[i], v_sub(mv, v_mul(t, v_dot(mv, t))))
        for i in fixed:
            out[i] = A.P[i]
        return out
    return fn


def fair_normal_target(strength=0.5):
    """FairN: the fairing of Smooth, CVs moved only along the normal.
    (_fair_normal_curv - curvature fairing - was tried and tested worse.)"""
    return normal_only(fair_target(strength))


def _straight_targets(p, fixed, strength=0.5):
    """Straight: every CV row / column straightened as seen along the surface
    normal - only the sideways (in-surface, across the row) part of the 2nd
    difference is used, so it changes neither the spacing nor the shape."""
    nu, nv = p.du.n, p.dv.n
    N = cv_normals(p)
    P = p.P
    out = list(P)
    for i in range(nu):
        for j in range(nv):
            k = i * nv + j
            if k in fixed:
                continue
            mv = (0.0, 0.0, 0.0)
            n = N[k]
            pairs = []
            if 0 < i < nu - 1:
                pairs.append(((i - 1) * nv + j, (i + 1) * nv + j))
            if 0 < j < nv - 1:
                pairs.append((i * nv + j - 1, i * nv + j + 1))
            for (a, b) in pairs:
                lap = v_sub(v_mul(v_add(P[a], P[b]), 0.5), P[k])
                t = v_unit(v_sub(P[b], P[a]))
                lap = v_sub(lap, v_mul(n, v_dot(lap, n)))      # not along the normal
                lap = v_sub(lap, v_mul(t, v_dot(lap, t)))      # not along the row (spacing)
                mv = v_add(mv, lap)
            out[k] = v_add(P[k], v_mul(mv, strength))
    return out


def _keep_shape_fit(p, fixed, guide_fn, cv_guide=None, shape_tol=0.05, mirror=None, iters=5, free_sides=()):
    """New CVs for patch p that lie on its OLD surface (points may slide along
    it) and follow a wish as far as the shape allows:
      guide_fn(s, t) -> (old surface point the new (s, t) should land on, its (u, v))
      cv_guide       -> wished CV positions.
    fixed CVs stay exactly; mirror rows stay mirror rows. The outline stays
    on the old outline - except on free_sides (no neighbour, no mirror): there
    the edge may move too (on the old surface or its tangent plane past the
    edge), so a wavy free edge can be cleaned. -> (CVs, shape error)."""
    free_sides = set(free_sides or ())
    def side_of(s, t):
        out = set()
        if t == 0.0: out.add(0)
        if s == 1.0: out.add(1)
        if t == 1.0: out.add(2)
        if s == 0.0: out.add(3)
        return out
    du, dv, P0 = p.du, p.dv, p.P
    nsu = 2 * du.n + 3
    nsv = 2 * dv.n + 3
    grid = [(i / float(nsu - 1), j / float(nsv - 1)) for i in range(nsu) for j in range(nsv)]
    if guide_fn is not None:
        gg = [guide_fn(s, t) for (s, t) in grid]
        gpts = [g[0] for g in gg]
        guess = [g[1] for g in gg]
    else:
        gpts = None
        guess = list(grid)
    N_ = None
    for it in range(iters):
        L = LSQ(p.n(), 0, dict((i, P0[i]) for i in fixed))
        for k, (s, t) in enumerate(grid):
            cf = srf_coefs(du, dv, s, t, 0, 0)
            sides_ = side_of(s, t)
            edge = bool(sides_) and not sides_ <= free_sides       # pinned outline
            if gpts is not None:
                L.add(cf, gpts[k], (100.0 if edge else 1.0) if N_ is None else 0.3)
            elif N_ is None and edge:
                L.add(cf, srf_point(du, dv, P0, s, t), 100.0)
            if N_ is None:
                continue
            q = srf_point(du, dv, N_, s, t)                   # geometric: closest on the OLD surface
            u, v = project_param(du, dv, P0, q, guess[k])
            if edge:
                if s in (0.0, 1.0) and not (side_of(s, -1.0) <= free_sides): u = s     # outline stays
                if t in (0.0, 1.0) and not (side_of(-1.0, t) <= free_sides): v = t
                L.add(cf, srf_point(du, dv, P0, u, v), 100.0)
            else:
                D = srf_derivs(du, dv, P0, u, v, 1)
                c = D[(0, 0)]; n = v_cross(D[(1, 0)], D[(0, 1)])
                if v_len(n) > 1e-300:
                    n = v_unit(n)
                    c = v_sub(q, v_mul(n, v_dot(v_sub(q, c), n)))  # onto the surface / its tangent plane
                L.add(cf, c, 1.0)
        if cv_guide is not None:
            wg = (1.0 if N_ is None else 0.3) * len(grid) / float(p.n())
            for i in range(p.n()):
                L.add([(i, 1.0)], cv_guide[i], wg)
        for i in range(p.n()):
            L.add([(i, 1.0)], P0[i], 1e-6)
        if mirror:
            p.off = 0
            add_mirror_eqs(L, p, mirror)
        N_ = L.solve()[0]
    err = 0.0
    for k, (s, t) in enumerate(grid):
        q = srf_point(du, dv, N_, s, t)
        u, v = project_param(du, dv, P0, q, guess[k])
        D = srf_derivs(du, dv, P0, u, v, 1)
        n = v_cross(D[(1, 0)], D[(0, 1)])
        if v_len(n) > 1e-300 and (u in (0.0, 1.0) or v in (0.0, 1.0)):
            e_ = abs(v_dot(v_sub(q, D[(0, 0)]), v_unit(n)))   # past a free edge: off the tangent plane only
        else:
            e_ = v_dist(q, D[(0, 0)])
        err = max(err, e_)
    return N_, err


def _edge_dir(k):
    """Surface direction (0 = u, 1 = v) along side k, and whether the side's
    own parameter runs against it."""
    return (0 if k in (0, 2) else 1), (k in (2, 3))


def _bez1(coefs):
    b = BDir(len(coefs) - 1, 1)
    C = [(x, 0.0, 0.0) for x in coefs]
    return lambda s: crv_point(b, C, s)[0]


def layout_step(A, others, mode, tol=1e-3, extra_freeze=None, devs=None, shape_tol=0.05, mirror=None, keep_all=False):
    """Even / Straight / ToDev: the CVs move ALONG the surface, the shape stays.
    Rows that cross a seam move together with the matching rows of the
    neighbour there (no 'S' at the seam); if that neighbour is bound in that
    direction on its far side too (a chain), that direction is left alone.
    Afterwards every seam is made exact again at its level from before.
    -> ({key: new CVs} or None, text, moved patches)."""
    allp = [A] + [p for p in others if p is not A]
    seams = find_seams(allp, tol)
    levels = {}
    for sm in seams:
        seam_level(sm, tol)
        levels[(sm["X"].key, sm["k"], sm["Y"].key, sm["l"])] = sm["level"]
    sc_before = scalars_before(seams)
    keep_before = dict((seam_key(sm), sm["level"] if keep_all else seam_keep_level(sm, tol))
                       for sm in seams if sm["level"] >= 1)
    P_before = dict((p.key, list(p.P)) for p in allp)

    def side_seams(P_):
        out = {}
        for sm in seams:
            if sm["level"] < 0:
                continue
            if sm["X"] is P_:
                out.setdefault(sm["k"], []).append((sm["level"], sm["Y"], sm["l"], sm))
            if sm["Y"] is P_:
                out.setdefault(sm["l"], []).append((sm["level"], sm["X"], sm["k"], sm))
        return out
    mir = {}
    if mirror:
        for (k, r0, r1, r2) in mirror_rows(A, mirror):
            mir[k] = 3 if r2 else (2 if r1 else 1)

    def bound_rows(P_, ss, k):
        n_ = max([lv + 1 for (lv, o, l_, sm) in ss.get(k, [])] + [0])
        if P_ is A:
            n_ = max(n_, mir.get(k, 0))
        for (kk, nr) in (extra_freeze or {}).get(P_.key, []):
            if kk == k:
                n_ = max(n_, nr)
        return n_

    ssA = side_seams(A)
    freeA = set(k for k in range(4) if not ssA.get(k) and k not in mir)

    def fixed_for(dirs):
        """CVs of A that stay: at a seam only its EDGE row (the inner rows
        move, the neighbour follows afterwards with its continuity rows);
        rows next to surfaces we cannot handle stay whole. At seams crossed
        by a moved direction nothing stays (it moves with the neighbour)."""
        fx = set()
        for k in range(4):
            if k in mir:
                continue                               # mirror: kept by its equations
            crossed_by = 0 if k in (0, 2) else 1
            if crossed_by in dirs and ssA.get(k):
                continue
            hard = 0
            for (kk, nr) in (extra_freeze or {}).get(A.key, []):
                if kk == k: hard = max(hard, nr)
            n_ = hard if hard else (1 if ssA.get(k) else 0)
            # T-joint: the neighbour is the longer one and cannot follow - this
            # patch keeps its whole continuity rows there
            # T-joint on either side (several short patches along a long edge,
            # or this one short): the short ones are bound at their own
            # corners and cannot follow rows sliding along the long edge
            for (lv_, o_, l_, sm_) in ssA.get(k, []):
                if _part_of(sm_, o_) or _part_of(sm_, A):
                    n_ = max(n_, lv_ + 1)
            for r in range(n_):
                fx.update(side_row_indices(A.du, A.dv, k, r))
        return fx

    plan = {}
    notes = []
    for d in (0, 1):
        name = "PINK" if d == 0 else "BLUE"
        lo_side, hi_side = (3, 1) if d == 0 else (0, 2)
        crossed = (0, 2) if d == 0 else (1, 3)
        lo = max(1, bound_rows(A, ssA, lo_side))
        hi = max(1, bound_rows(A, ssA, hi_side))
        partners = []
        ok = True
        for k in crossed:
            for (lv, B, l, sm) in ssA.get(k, []):
                full = sm["tb"] - sm["ta"] > 0.98 and abs(abs(sm["sb"] - sm["sa"]) - 1.0) < 0.02
                dB, revB = _edge_dir(l)
                dA, revA = _edge_dir(k)
                if not full or (A.du.p if d == 0 else A.dv.p) != (B.du.p if dB == 0 else B.dv.p):
                    ok = False
                    notes.append("{} rows NOT moved: the seam on side {} is only PART of an edge (the patches there "
                                 "are of different length) - an exact G1/G2 makes the shorter edge a piece of the longer "
                                 "one, so the rows along it are fixed. EvenUV the LONGER patch before the shorter one is "
                                 "built against it (a new patch copies the spacing of the edge it is built to)".format(
                                     name, k + 1))
                    break
                flip = (revA != revB) != (sm["sb"] < sm["sa"])
                ssB = side_seams(B)
                if any(o is not A for (lv2, o, l2, sm2) in ssB.get((l + 2) % 4, [])):
                    ok = False
                    notes.append("{} rows NOT moved: they would have to move the neighbour on side {} too, and that one "
                                 "is bound on its far side as well".format(name, k + 1))
                    break
                lb, hb = (3, 1) if dB == 0 else (0, 2)
                blo, bhi = bound_rows(B, ssB, lb), bound_rows(B, ssB, hb)
                if flip:
                    blo, bhi = bhi, blo
                lo = max(lo, blo)
                hi = max(hi, bhi)
                partners.append((B, dB, flip, ssB, k))
            if not ok:
                break
        if ok:
            plan[d] = (lo, hi, partners)
    if not plan:
        return None, "; ".join(notes) or "nothing to move", []

    nu_, nv_ = A.du.n, A.dv.n

    def row_centres(P_, d):
        if d == 0:
            return [tuple(sum(P_[i * nv_ + j][c] for j in range(nv_)) / nv_ for c in range(3)) for i in range(nu_)]
        return [tuple(sum(P_[i * nv_ + j][c] for i in range(nu_)) / nu_ for c in range(3)) for j in range(nv_)]
    def end_gaps(P_, d):
        """3D gaps between the rows of direction d at both ends of the rows."""
        if d == 0:
            return [[v_dist(P_[i * nv_ + j], P_[(i + 1) * nv_ + j]) for i in range(nu_ - 1)] for j in (0, nv_ - 1)]
        return [[v_dist(P_[i * nv_ + j], P_[i * nv_ + j + 1]) for j in range(nv_ - 1)] for i in (0, nu_ - 1)]
    def bound_note():
        """If the uneven gaps are in the rows tied to a seam (edge, G1, G2
        row), Even cannot touch them - say which and why."""
        out = []
        for d, (lo, hi, partners) in plan.items():
            for (nb, k) in ((lo, 3 if d == 0 else 0), (hi, 1 if d == 0 else 2)):
                if nb < 2 or not ssA.get(k):
                    continue
                gs = end_gaps(A.P, d)
                bad = False
                for g_ in gs:
                    m_ = sum(g_) / len(g_)
                    part = g_[:nb - 1] if k in (3, 0) else g_[len(g_) - nb + 1:]
                    if part and (min(part) < 0.5 * m_ or max(part) > 1.8 * m_):
                        bad = True
                if bad:
                    out.append("the first {} rows at side {} are tied to the neighbour ({}) - their spacing follows its "
                               "rows; EvenUV the neighbour, or MatchUVN at a lower level".format(
                                   nb, k + 1, LEVEL_NAME.get(nb - 1, "?")))
        return ("; " + "; ".join(out)) if out else ""
    rowc = dict((d, row_centres(A.P, d)) for d in (0, 1))
    gaps = dict((d, [v_dist(rc[k], rc[k + 1]) for k in range(len(rc) - 1)]) for d, rc in rowc.items())
    deg = {0: A.du.p, 1: A.dv.p}

    def run(targets):
        """targets {d: new row parameters of A}: fit A and its partners.
        A target is one list (every row moves the same along its length) or
        a pair (at the start, at the end of the rows): a FAN - rows close
        together at one end and far apart at the other get even at both."""
        ident = lambda x: x
        ga = {}
        for d in (0, 1):
            r = targets.get(d)
            if not r:
                ga[d] = (ident, ident)
            elif isinstance(r, tuple):
                ga[d] = tuple(_bez1(x) if x else ident for x in r)
            else:
                f_ = _bez1(r)
                ga[d] = (f_, f_)

        def gmap(s, t):
            u_ = (1.0 - t) * ga[0][0](s) + t * ga[0][1](s)
            v_ = (1.0 - s) * ga[1][0](t) + s * ga[1][1](t)
            return u_, v_
        new = {}
        moved = [A]
        fitA = lambda s, t: (srf_point(A.du, A.dv, A.P, *gmap(s, t)), gmap(s, t))
        PA, worst = _keep_shape_fit(A, fixed_for([d for d, r in targets.items() if r]), fitA, None, shape_tol, mirror,
                                    free_sides=freeA)
        new[A.key] = PA
        for d, r in targets.items():
            if not r:
                continue
            for (B, dB, flip, ssB, kA) in plan[d][2]:
                if isinstance(r, tuple):                # the end of the fan at this seam
                    r_ = r[1] if kA in (1, 2) else r[0]
                    if not r_:
                        r_ = [i / float(deg[d]) for i in range(deg[d] + 1)]
                else:
                    r_ = r
                rb = [1.0 - x for x in reversed(r_)] if flip else list(r_)
                gb = {dB: _bez1(rb), 1 - dB: (lambda s: s)}
                fixB = set()
                for k in range(4):
                    if any(o is not A for (lv, o, l_, sm) in ssB.get(k, [])):
                        for rr in range(bound_rows(B, ssB, k)):
                            fixB.update(side_row_indices(B.du, B.dv, k, rr))
                fitB = (lambda s, t, B=B, gb=gb:
                        (srf_point(B.du, B.dv, B.P, gb[0](s), gb[1](t)), (gb[0](s), gb[1](t))))
                PB, eB = _keep_shape_fit(B, fixB, fitB, None, shape_tol, None,
                                         free_sides=set(k for k in range(4) if not ssB.get(k)))
                new[B.key] = PB
                worst = max(worst, eB)
                moved.append(B)
        return new, worst, moved

    def spacing_ok(new):
        P_ = new[A.key]
        for d in plan:
            lo, hi, _ = plan[d]
            rc = row_centres(P_, d)
            a_, b_ = lo - 1, len(rc) - hi
            if b_ - a_ < 2:
                continue

            def ratios(r_):
                g_ = [v_dist(r_[k], r_[k + 1]) for k in range(a_, b_)]
                m_ = sum(g_) / len(g_)
                return min(g_) / m_, max(g_) / m_
            n_lo, n_hi = ratios(rc)
            o_lo, o_hi = ratios(rowc[d])
            if n_lo < min(0.45, o_lo) - 1e-6 or n_hi > max(1.75, o_hi) + 1e-6:
                return False
            # and at both ends of the rows: a step must not bunch rows there
            # more than they already were (worst gap / mean of the free gaps)
            def worst(gs):
                out = 1e9
                for g_ in gs:
                    f_ = g_[a_:b_]
                    out = min(out, min(f_) / max(sum(f_) / len(f_), 1e-300))
                return out
            if worst(end_gaps(P_, d)) < min(0.45, 0.95 * worst(end_gaps(A.P, d))) - 1e-6:
                return False
        return True

    result = None
    txt = ""
    if mode in ("straight", "relax"):
        fx = fixed_for(())
        for st in (1.0, 0.5, 0.25):
            tg = _straight_targets(A, fx, 0.5 * st) if mode == "straight" else relax_targets(A, fx, 0.5 * st)
            PA, err = _keep_shape_fit(A, fx, None, tg, shape_tol, mirror, free_sides=freeA)
            if err <= shape_tol:
                result = ({A.key: PA}, [A])
                txt = ("rows / columns straightened" if mode == "straight" else
                       "CV layout relaxed (less skew)") + " (shape changed {})".format(fl(err))
                break
        if result is None:
            return None, "cannot do more here without changing the shape", []
    elif mode == "even":
        for st in (1.0, 0.5, 0.25):
            tg = {}
            for d, (lo, hi, partners) in plan.items():
                # the spacing at BOTH ends of the rows (not only on average:
                # a fan of rows is even on average and was left as it was)
                e0, e1 = end_gaps(A.P, d)
                r0 = _row_targets(deg[d], lo, hi, e0, None, st)
                r1 = _row_targets(deg[d], lo, hi, e1, None, st)
                tg[d] = (r0, r1) if (r0 or r1) else None
            if not any(tg.values()):
                return None, "rows are already evenly spread" + bound_note() + ("; " + "; ".join(notes) if notes else ""), []
            new, err, moved = run(tg)
            if err <= shape_tol and spacing_ok(new):
                result = (new, moved)
                txt = "rows a step towards even spacing (shape changed {})".format(fl(err))
                break
        if result is None:
            return None, "rows cannot spread more without changing the shape", []
    else:                                              # todev
        N_ = 20
        acc = {0: [[0.0, 0, 0.0] for _ in range(N_ + 1)], 1: [[0.0, 0, 0.0] for _ in range(N_ + 1)]}
        for (u, v, dd) in devs or []:
            for (d, k) in ((0, int(round(u * N_))), (1, int(round(v * N_)))):
                e = acc[d][k]
                e[0] += dd
                e[1] += 1
                e[2] = max(e[2], dd)
        whole = lambda e: (e[0] / e[1]) if e[1] and e[0] / e[1] >= 0.5 * e[2] else 0.0
        cands = []
        for d, (lo, hi, partners) in plan.items():
            p_ = deg[d]
            for (val, s) in _profile_peaks([whole(e) for e in acc[d]], (lo - 1) / float(p_), (p_ - hi + 1) / float(p_)):
                cands.append((val, s, d))
        cands.sort(reverse=True)
        why = "every place where a whole row is off already has a row - now PullN"
        for (val, s, d) in cands:
            if val <= shape_tol:
                break
            lo, hi, partners = plan[d]
            for st in (1.0, 0.5, 0.25):
                r, i = _move_one_row(deg[d], lo, hi, gaps[d], s, st)
                if r is None:
                    if i != "row already there":
                        why = i
                    break
                new, err, moved = run({d: r})
                if err <= shape_tol and spacing_ok(new):
                    result = (new, moved)
                    txt = "{} row {} (+ the one behind) moved to the far place at {:.0%} (shape changed {})".format(
                        "PINK" if d == 0 else "BLUE", i + 1, s, fl(err))
                    break
                why = "rows cannot move there without changing the shape or bunching up"
            if result is not None:
                break
        if result is None:
            return None, why + ("; " + "; ".join(notes) if notes else ""), []
    new, moved = result
    # every seam exact again at its level from before: THIS patch stays as
    # it now is, the neighbours follow with their continuity rows
    for p in moved:
        p.P = new[p.key]
    want = {}
    for sm in seams:
        if sm["level"] >= 0 and (sm["X"] is A or sm["Y"] is A):
            want[(sm["X"].key, sm["k"], sm["Y"].key, sm["l"])] = sm["level"]
    # joint solve (as SmthUVN): A goes to its new layout, the neighbours follow
    # with their continuity rows, every seam exact at its level from before.
    # A may give way a little where it has to - at a corner of four patches
    # a fixed A made the equations contradict each other (seams stayed open,
    # neighbours drifted by many mm).
    PA_new = list(A.P)
    force = dict(levels)
    tgt_fn = lambda A_, fixed_: PA_new
    mv2, used2, new2 = adjust_step(A, allp, tgt_fn, tol, extra_freeze, None, mirror, force, sc_before, P_before,
                                   keep_levels=keep_before)
    for p in mv2:
        new[p.key] = new2[p.key]
        if p not in moved:
            moved.append(p)
    if len(moved) > 1:
        txt += "; the matching rows of the neighbour moved along"
    if notes:
        txt += "; " + "; ".join(notes)
    # never worse in what the button is for (at a corner the neighbours'
    # continuity can force a step that undoes it) - then nothing changes
    qk = {"even": "spacing", "straight": "kink", "relax": "skew"}.get(mode)
    if qk:
        cur = A.P
        try:
            def qual(P_):
                A.P = P_
                q_ = net_quality(A)[qk]
                if mode == "even":                     # and at both ends of the rows (a fan)
                    for d in (0, 1):
                        for g_ in end_gaps(P_, d):
                            q_ = max(q_, max(g_) / max(min(g_), 1e-300))
                return q_
            q0 = qual(P_before[A.key])
            q1 = qual(new[A.key])
        finally:
            A.P = cur
        if q1 > q0 * 1.02 + 1e-9:
            for p in allp:
                p.P = P_before[p.key]
            return None, "a step here would make it worse ({} {:.3g} -> {:.3g}) - nothing changed (the shape or " \
                         "the neighbours' continuity rows do not allow more)".format(qk, q0, q1), []
    return new, txt, moved


def match_seams(A, others, tol):
    """Seams of patch A (found with a LOOSE tol - hand edits may have opened
    them): [(key, seam, neighbour, side of A, level now)]."""
    allp = [A] + [p for p in others if p is not A]
    seams = find_seams(allp, tol)
    out = []
    for sm in seams:
        lv = seam_level(sm, tol)
        if sm["X"] is A:
            out.append(((sm["X"].key, sm["k"], sm["Y"].key, sm["l"]), sm, sm["Y"], sm["k"], lv))
        elif sm["Y"] is A:
            out.append(((sm["X"].key, sm["k"], sm["Y"].key, sm["l"]), sm, sm["X"], sm["l"], lv))
    return seams, out


NARROW_RATIO = 0.5
MATCH_ITERS = 4


def match_step(A, others, want, tol, extra_freeze=None, mirror=None, mirror_sides=None,
               keep_scalars=None, P_before=None, keep_levels=None, no_narrow=False, only=None, extra=None):
    """Match: patch A is the MASTER (after hand edits). Its direct neighbours
    are brought back to it at the chosen levels want = {seam key: 0/1/2},
    moving only their continuity rows to A (G0 1, G1 2, G2 3 rows); seams
    between those neighbours keep their level. No mesh.
    A itself moves only where it has to stay a mirror patch, and on the sides
    in mirror_sides = {side: 0/1/2}: those are brought ONTO the mirror plane
    (G0 edge on it, G1 2nd row straight across, G2 3rd row at twice the
    distance) - e.g. a patch drawn near the axis without snapping.
    -> (moved patches, {key: new CVs}, text)."""
    # extra = [(M, only_M, want_M)]: more MASTERS that never move (strips on
    # an NPatch: all sides of a hole are matched in ONE solve)
    extra = list(extra or [])
    xm = [M for (M, o_, w_) in extra]
    others = [p for p in others if p not in xm] + xm
    allp = [A] + [p for p in others if p is not A]
    seams, mine = match_seams(A, others, tol)
    masters = [(A, only, want)] + extra
    def _other_of(sm_, M):
        return sm_["Y"] if sm_["X"] is M else (sm_["X"] if sm_["Y"] is M else None)
    for (M, only_M, want_M) in masters:  # a strip touches ONLY its own neighbour (its short ends may lie along other edges)
        if only_M is None: continue
        seams = [sm_ for sm_ in seams if _other_of(sm_, M) is None or _other_of(sm_, M) in only_M]
    mine = [x_ for x_ in mine if only is None or x_[2] in only]
    xmine = []
    for (M, only_M, want_M) in extra:
        for sm_ in seams:
            B_ = _other_of(sm_, M)
            if B_ is None or (only_M is not None and B_ not in only_M): continue
            kM = sm_["k"] if sm_["X"] is M else sm_["l"]
            key_ = (sm_["X"].key, sm_["k"], sm_["Y"].key, sm_["l"])
            xmine.append((key_, sm_, B_, kM, seam_level(sm_, tol), want_M, M))
    tgt = {}
    for (key, sm, B, k, lv) in mine:
        sm["level"] = want.get(key, max(lv, 0))
        tgt[id(sm)] = sm["level"]
    for (key, sm, B, k, lv, want_M, M) in xmine:
        if isinstance(want_M, dict):                  # the key in either order
            sw = (key[2], key[3], key[0], key[1])
            sm["level"] = want_M.get(key, want_M.get(sw, max(lv, 0)))
        else:
            sm["level"] = int(want_M)                 # one level for all its seams
        tgt[id(sm)] = sm["level"]
    moving = []
    for p in allp:
        p.move = set()
    # a NARROW patch (fillet) - its cross rows are short against the
    # neighbours': Match REBUILDS it from them. The neighbours stay; the
    # fillet's rows go straight across (no shear taken over from its
    # current, possibly tangled, state). Big patches never pay for a fillet.
    narrow_rebuild = {}
    for (key, sm, B, k, lv) in ([] if no_narrow else mine):     # no_narrow: A is a strip on a fixed surface (NPatch)
        if sm["level"] < 1 or _part_of(sm, B):
            continue
        seam_scalars(sm)
        rA = sm["a"] if sm["X"] is A else 1.0 / max(sm["a"], 1e-300)
        if rA < NARROW_RATIO:
            d_ = dict((q, sm[q]) for q in SCALAR_KEYS)
            for q in ("b0", "b1", "b", "g0", "g1"):
                d_[q] = 0.0
            narrow_rebuild[seam_key(sm)] = d_
    if narrow_rebuild:
        keep_scalars = dict(keep_scalars or {})
        keep_scalars.update(narrow_rebuild)
        keep_levels = dict(keep_levels or {})
        for k_ in narrow_rebuild:
            keep_levels[k_] = 0
        extra_freeze = dict(extra_freeze or {})
        for p_ in allp:
            if p_ is not A:
                extra_freeze[p_.key] = [(kk, max(p_.du.n, p_.dv.n)) for kk in range(4)]
    a_gives = []                                 # T-joints where A is the short one
    for (key, sm, B, k, lv) in mine:
        l = sm["l"] if sm["X"] is A else sm["k"]
        if _part_of(sm, B):
            a_gives.append((sm, k))
            if B not in moving: moving.append(B)     # in the system, but its rows stay
            continue
        if B not in moving: moving.append(B)
        for r in range(sm["level"] + 1):
            B.move.update(side_row_indices(B.du, B.dv, l, r))
    for (key, sm, B, k, lv, want_M, M) in xmine:
        l = sm["l"] if sm["X"] is M else sm["k"]
        if B not in moving: moving.append(B)
        for r in range(sm["level"] + 1):
            B.move.update(side_row_indices(B.du, B.dv, l, r))
    if not moving and not mirror_sides:
        return [], {}, "no neighbour found at the sides of this patch"
    A.move = set()
    for M in xm:
        M.move = set()
    _pull_in_corners(A, seams, moving, fixed=xm)
    # neighbours' rows next to seams with patches that stay (not A, not moving) are frozen
    for sm in seams:
        if id(sm) in tgt or sm["level"] < 0: continue
        for (P_, kk, Q_) in ((sm["X"], sm["k"], sm["Y"]), (sm["Y"], sm["l"], sm["X"])):
            if P_ in moving and Q_ not in moving and Q_ is not A and Q_ not in xm:
                for r in range(sm["level"] + 1):
                    P_.move.difference_update(side_row_indices(P_.du, P_.dv, kk, r))
    for p in moving:
        for (kk, nrows) in (extra_freeze or {}).get(p.key, []):
            for r in range(nrows):
                p.move.difference_update(side_row_indices(p.du, p.dv, kk, r))
    mir_rows = set()
    if mirror:
        for (k, r0, r1, r2) in mirror_rows(A, mirror):
            mir_rows.update(r0); mir_rows.update(r1 or []); mir_rows.update(r2 or [])
    A.move = set(mir_rows)
    # T-joint: A's edge is only a part of B's edge - B cannot follow, A's rows
    # at that side go to B instead (A gives way there)
    for (sm, k) in a_gives:
        hard = 0
        for (kk, nr) in (extra_freeze or {}).get(A.key, []):
            if kk == k: hard = max(hard, nr)
        for r in range(hard, sm["level"] + 1):
            A.move.update(side_row_indices(A.du, A.dv, k, r))
    # A NARROW patch (a blend) against a wide neighbour: its cross rows are
    # short, the neighbour's long - the neighbour's G1 row would have to move
    # 1/a times, its G2 row 1/a^2 times what A's rows are off (a = 0.07:
    # x15 and x225 - the CVs flew out). There A's own continuity rows (not
    # its edge) give way too: they need a resp. a^2 times as little.
    for (key, sm, B, k, lv) in ([] if no_narrow else mine):   # a strip on a fixed surface never gives way
        if sm["level"] < 1:
            continue
        ks = (keep_scalars or {}).get(seam_key(sm))
        if ks:
            sm.update(ks)
        else:
            seam_scalars(sm)
        rA = sm["a"] if sm["X"] is A else 1.0 / max(sm["a"], 1e-300)
        if rA >= NARROW_RATIO:
            continue
        hard = 0
        for (kk, nr) in (extra_freeze or {}).get(A.key, []):
            if kk == k: hard = max(hard, nr)
        for r in range(max(1, hard), sm["level"] + 1):
            A.move.update(side_row_indices(A.du, A.dv, k, r))
    extra_mir = []
    if mirror and mirror_sides:
        axis, val, mtol = mirror
        for k, lv_ in mirror_sides.items():
            rows_ = [side_row_indices(A.du, A.dv, k, r) for r in range(min(lv_, 2) + 1)]
            for r_ in rows_: A.move.update(r_)
            extra_mir.append((rows_, axis, val))
    sysp = [A] + xm + [p for p in moving if p not in xm]
    off = 0
    for p in sysp:
        p.off = off; off += p.n()
    # the point pairs along a seam come from the CURRENT edges: where the two
    # edges are spread differently (hand edits, a corner of four patches) one
    # solve leaves the seam a little open - solve again from the result,
    # always as close as possible to the CVs from before
    P_orig = dict((p.key, list(p.P)) for p in sysp)
    old_pts = {}
    for p in sysp:
        Pb_ = (P_before or {}).get(p.key, P_orig[p.key])
        for i in range(p.n()):
            old_pts[p.off + i] = Pb_[i]
    keeps = {}
    for sm in seams:
        if sm["level"] >= 1 and sm["X"] in sysp and sm["Y"] in sysp:
            if keep_levels is not None and seam_key(sm) in keep_levels:
                keeps[id(sm)] = keep_levels[seam_key(sm)]
            else:
                keeps[id(sm)] = seam_keep_level(sm, tol, True)  # already right: only follow, no 'repair'
    size = max(v_dist(a, b) for p in sysp for a in (p.P[0], p.P[-1]) for b in (p.P[0], p.P[-1]))
    used = []
    new = {}
    try:
        for it_ in range(MATCH_ITERS):
            fixed = {}
            for p in sysp:
                for i in range(p.n()):
                    if i not in p.move:
                        fixed[p.off + i] = P_orig[p.key][i]
            L = LSQ(off, 0, fixed)
            for p in sysp:                                 # as little change as possible
                for i in range(p.n()):
                    L.add([(p.off + i, 1.0)], P_orig[p.key][i], 1.0)
            used = []
            for sm in seams:
                if sm["level"] < 0: continue
                if sm["X"] in sysp and sm["Y"] in sysp:
                    if sm["level"] >= 1 and it_ == 0:
                        ks = (keep_scalars or {}).get(seam_key(sm))
                        if ks:
                            sm.update(ks)              # from the state before the change
                        else:
                            seam_scalars(sm)           # once (see scalars_before)
                    add_seam_eqs(L, sm, sm["level"], keeps.get(id(sm), 0), old_pts)
                    used.append(sm)
            if mirror:
                for p in sysp:
                    add_mirror_eqs(L, p, mirror)
                for (rows_, axis, val) in extra_mir:              # sides brought onto the mirror
                    for i0 in rows_[0]:
                        L.add_axis([(A.off + i0, 1.0)], axis, val, W_G0)
                    if len(rows_) > 1:
                        for i0, i1 in zip(rows_[0], rows_[1]):
                            for c in range(3):
                                if c != axis:
                                    L.add_axis([(A.off + i1, 1.0), (A.off + i0, -1.0)], c, 0.0, W_G1)
                    if len(rows_) > 2:
                        for i0, i1, i2 in zip(rows_[0], rows_[1], rows_[2]):
                            L.add_axis([(A.off + i2, 1.0), (A.off + i1, -2.0), (A.off + i0, 1.0)], axis, 0.0, W_G2)
            anc, ok_ = _move_guard(A, sysp, P_orig)
            sol = L.solve_safe(anc, ok_)[0]
            new = {}
            for p in sysp:
                new[p.key] = [sol[p.off + i] for i in range(p.n())]
            done = max(v_dist(a, b) for p in sysp for a, b in zip(p.P, new[p.key]))
            for p in sysp:
                p.P = new[p.key]
            if done < 1e-5 * max(size, 1e-9):
                break
    finally:
        for p in sysp:
            p.P = P_orig[p.key]
    # what it REALLY reached (it said "matched: G2" also when it could not)
    got = []
    for sm in used:
        if id(sm) not in tgt:
            continue
        cur = (sm["X"].P, sm["Y"].P)
        try:
            sm["X"].P = new.get(sm["X"].key, sm["X"].P); sm["Y"].P = new.get(sm["Y"].key, sm["Y"].P)
            c_ = dict(sm); c_.pop("_samp", None)
            lv_ = seam_measure(c_, min(tol, 0.01))
        finally:
            sm["X"].P, sm["Y"].P = cur
        if lv_ >= sm["level"]:
            got.append(LEVEL_NAME.get(sm["level"], "?"))
        else:
            got.append("{} wanted, {} reached (kink {:.2f} deg)".format(
                LEVEL_NAME.get(sm["level"], "?"), LEVEL_NAME.get(lv_, "open"), c_.get("ang", 0.0)))
    txt = ", ".join(got)
    if narrow_rebuild:
        txt += " (narrow patch: rebuilt from its neighbours, they stay; rows straight across)"
    if a_gives:
        txt += " (at a T-joint this patch is the shorter one: it followed the longer edge there)"
    if mirror_sides:
        txt = (txt + ", " if txt else "") + "mirror " + ", ".join(LEVEL_NAME.get(v, "?") for v in mirror_sides.values())
    return sysp, new, "matched: " + (txt or "-")


def relax_targets(p, fixed, strength=1.0):
    """Relax: the CV layout the patch's four boundary CV rows imply (a Coons
    blend of the boundary polygons, by chord length) - rows and columns cross
    without skew. Only the sideways (UV) part is used; the shape is kept by
    the fit afterwards."""
    nu, nv = p.du.n, p.dv.n
    P = p.P
    idx = lambda i, j: i * nv + j

    def chord(pts):
        c = [0.0]
        for a in range(len(pts) - 1):
            c.append(c[-1] + v_dist(pts[a], pts[a + 1]))
        return [x / c[-1] if c[-1] > 1e-300 else a / float(len(pts) - 1) for a, x in enumerate(c)]
    s0 = chord([P[idx(i, 0)] for i in range(nu)]); s1 = chord([P[idx(i, nv - 1)] for i in range(nu)])
    t0 = chord([P[idx(0, j)] for j in range(nv)]); t1 = chord([P[idx(nu - 1, j)] for j in range(nv)])
    N = cv_normals(p)
    out = list(P)
    for i in range(1, nu - 1):
        for j in range(1, nv - 1):
            k = idx(i, j)
            if k in fixed: continue
            tt = j / float(nv - 1); ss = i / float(nu - 1)
            s = (1 - tt) * s0[i] + tt * s1[i]          # blended chord parameters
            t = (1 - ss) * t0[j] + ss * t1[j]
            c = v_add(v_add(v_mul(P[idx(0, j)], 1 - s), v_mul(P[idx(nu - 1, j)], s)),
                      v_add(v_mul(P[idx(i, 0)], 1 - t), v_mul(P[idx(i, nv - 1)], t)))
            c = v_sub(c, v_add(v_add(v_mul(P[idx(0, 0)], (1 - s) * (1 - t)), v_mul(P[idx(nu - 1, 0)], s * (1 - t))),
                               v_add(v_mul(P[idx(0, nv - 1)], (1 - s) * t), v_mul(P[idx(nu - 1, nv - 1)], s * t))))
            mv = v_sub(c, P[k]); n = N[k]
            mv = v_sub(mv, v_mul(n, v_dot(mv, n)))     # UV only
            out[k] = v_add(P[k], v_mul(mv, strength))
    return out


def net_quality(p, fixed=()):
    """Numbers for the patch-button colours (each one = one button):
      waves    iso lines whose curvature changes sign twice or more  (Fair)
      spacing  largest / smallest gap between CV rows               (Even)
      kink     largest sideways kink of a CV row seen from above, deg (Straight)
      skew     how far Relax would move the CVs, % of the mean gap   (Relax)"""
    du, dv, P = p.du, p.dv, p.P
    nu, nv = du.n, dv.n
    # waves: normal curvature along iso lines
    waves = 0
    for d in (0, 1):
        for c in (0.2, 0.4, 0.6, 0.8):
            ks = []; Lc = 0.0
            for a in range(25):
                t = a / 24.0
                u, v = (t, c) if d == 0 else (c, t)
                D = srf_derivs(du, dv, P, u, v, 2)
                n = v_cross(D[(1, 0)], D[(0, 1)])
                if v_len(n) < 1e-300: continue
                n = v_unit(n)
                d1 = D[(1, 0)] if d == 0 else D[(0, 1)]
                d2 = D[(2, 0)] if d == 0 else D[(0, 2)]
                ks.append(v_dot(d2, n) / max(v_dot(d1, d1), 1e-300))
                Lc += v_len(d1) / 24.0
            if not ks: continue
            mx = max(abs(k) for k in ks)
            if mx * Lc < 0.005: continue          # practically flat along this line
            # class-A: between two inflections the curvature only rises or only
            # falls. A local max AND min of the curvature with no sign change
            # between them = a bump in the highlights (5% hysteresis). One S
            # (an inflection) is fine.
            extr = []; dirn = 0; ref = ks[0]
            for k in ks[1:]:
                if dirn >= 0 and k < ref - 0.05 * mx:
                    if dirn > 0: extr.append(ref)
                    dirn = -1; ref = k
                elif dirn <= 0 and k > ref + 0.05 * mx:
                    if dirn < 0: extr.append(ref)
                    dirn = 1; ref = k
                elif (dirn > 0 and k > ref) or (dirn < 0 and k < ref):
                    ref = k
            bump = any(extr[a] * extr[a + 1] > 0 for a in range(len(extr) - 1))
            if bump:
                waves += 1
    # spacing: row centres in both directions
    spacing = 1.0
    for d in (0, 1):
        if d == 0:
            rc = [tuple(sum(P[i * nv + j][c] for j in range(nv)) / nv for c in range(3)) for i in range(nu)]
        else:
            rc = [tuple(sum(P[i * nv + j][c] for i in range(nu)) / nu for c in range(3)) for j in range(nv)]
        g = [v_dist(rc[k], rc[k + 1]) for k in range(len(rc) - 1)]
        if min(g) > 1e-12:
            spacing = max(spacing, max(g) / min(g))
    # kink: change of the sideways turn along every CV row
    N = cv_normals(p)
    kink = 0.0
    for d in (0, 1):
        for r in range(nv if d == 0 else nu):
            ids = [i * nv + r for i in range(nu)] if d == 0 else [r * nv + j for j in range(nv)]
            turns = []
            for a in range(1, len(ids) - 1):
                n = N[ids[a]]
                l1 = v_sub(P[ids[a]], P[ids[a - 1]]); l2 = v_sub(P[ids[a + 1]], P[ids[a]])
                l1 = v_sub(l1, v_mul(n, v_dot(l1, n))); l2 = v_sub(l2, v_mul(n, v_dot(l2, n)))
                if v_len(l1) < 1e-12 or v_len(l2) < 1e-12: continue
                cr = v_dot(v_cross(v_unit(l1), v_unit(l2)), n)
                turns.append(math.degrees(math.asin(max(-1.0, min(1.0, cr)))))
            for a in range(1, len(turns)):
                kink = max(kink, abs(turns[a] - turns[a - 1]))
            if len(turns) == 1:
                kink = max(kink, abs(turns[0]))
    # skew: how far Relax would move the inner CVs
    R = relax_targets(p, set(fixed))
    gaps = [v_dist(P[i * nv + j], P[(i + 1) * nv + j]) for i in range(nu - 1) for j in range(nv)]
    mean = sum(gaps) / max(len(gaps), 1)
    skew = 100.0 * max([v_dist(a, b) for a, b in zip(P, R)] + [0.0]) / max(mean, 1e-12)
    return {"waves": waves, "spacing": spacing, "kink": kink, "skew": skew}


QUALITY_LIMITS = {          # (yellow from, red from)
    "waves": (1, 2), "spacing": (1.6, 2.5), "kink": (4.0, 10.0), "skew": (10.0, 25.0)}


def quality_state(name, value):
    y, r = QUALITY_LIMITS[name]
    return "red" if value >= r else ("yellow" if value >= y else "green")


def free_sides_of(A, others, tol, mirror=None, hold_neighbours=True):
    """Sides of A with no neighbour and no mirror, and how many CVs at each
    end of every side are held by the ADJACENT sides: {side: (held at low
    end, held at high end)}. The mirror always holds its rows; neighbours
    only if hold_neighbours (else they follow the edge afterwards)."""
    seams, mine = match_seams(A, others, tol)
    lv = {}; bound = set()
    for (key, sm, B, k, l_) in mine:
        if l_ >= 0:
            bound.add(k)
            if hold_neighbours: lv[k] = max(lv.get(k, -1), l_)
    if mirror:
        for (k, r0, r1, r2) in mirror_rows(A, mirror):
            bound.add(k)
            lv[k] = max(lv.get(k, -1), 2 if r2 else (1 if r1 else 0))
    out = {}
    for k in range(4):
        if k in bound: continue
        # row0 of side k runs along u (k = 0, 2) or v (k = 1, 3); its low end
        # touches side 3 (u=0) or side 0 (v=0), its high end side 1 or side 2
        lo_side, hi_side = (3, 1) if k in (0, 2) else (0, 2)
        out[k] = (lv.get(lo_side, -1) + 1, lv.get(hi_side, -1) + 1)
    return out


def refit_free_edge(A, k, pts, held=(0, 0)):
    """FreeEdgeEdt: new boundary row of free side k from a drawn curve pts
    (on the mesh, corner to corner). Only the CVs of that row between the
    corners move - and not those held by the adjacent sides' neighbours /
    mirror. The rest of the patch stays as it is.  -> new CVs."""
    r0 = side_row_indices(A.du, A.dv, k, 0)
    if v_dist(pts[0], A.P[r0[0]]) > v_dist(pts[-1], A.P[r0[0]]):
        pts = list(reversed(pts))                   # drawn from the other corner
    n = len(r0)
    d = BDir(n - 1, 1)
    fixed = {0: A.P[r0[0]], n - 1: A.P[r0[-1]]}
    for a in range(1, n - 1):
        if a < max(1, held[0]) or a > n - 1 - max(1, held[1]):
            fixed[a] = A.P[r0[a]]
    L = LSQ(n, 0, fixed)
    params = chord_params(pts)
    for p, t in zip(pts, params):
        f, N = d.basis(t, 0)
        L.add([(f + a, N[0][a]) for a in range(d.p + 1)], p, 1.0)
    for a in range(n):
        L.add([(a, 1.0)], A.P[r0[a]], 1e-6)
    C = L.solve()[0]
    out = list(A.P)
    for a, i in enumerate(r0):
        out[i] = C[a]
    return out


def move_free_edge(A, k, pts, mirror=None):
    """FreeEdgeEdt along a drawn line: the WHOLE boundary row of side k
    (corners too) is fitted to pts (corner to corner, on the mesh); the
    rows behind it follow, less and less towards the middle of the patch
    (no fold at the edge). A corner on the mirror plane stays on it.
    Neighbours are brought back afterwards (match_step). -> new CVs."""
    r0 = side_row_indices(A.du, A.dv, k, 0)
    if v_dist(pts[0], A.P[r0[0]]) > v_dist(pts[-1], A.P[r0[0]]):
        pts = list(reversed(pts))
    n = len(r0)
    d = BDir(n - 1, 1)
    L = LSQ(n, 0, {})
    for p, t in zip(pts, chord_params(pts)):
        f, N = d.basis(t, 0)
        L.add([(f + a, N[0][a]) for a in range(d.p + 1)], p, 1.0)
    for a in range(n):
        L.add([(a, 1.0)], A.P[r0[a]], 1e-6)
    C = L.solve()[0]
    if mirror:                                       # (axis, plane value, tol)
        ax, val, mt = mirror
        for a in (0, n - 1):
            if abs(A.P[r0[a]][ax] - val) <= 10.0 * mt:
                q = list(C[a]); q[ax] = val; C[a] = tuple(q)
    delta = [v_sub(C[a], A.P[r0[a]]) for a in range(n)]
    nrows = (A.dv.p + 1) if k in (0, 2) else (A.du.p + 1)
    reach = max(1, (nrows - 1) // 2 + 1)              # rows up to the middle follow, fading
    out = list(A.P)
    for r in range(reach):
        w = 1.0 - float(r) / reach
        for a, i in enumerate(side_row_indices(A.du, A.dv, k, r)):
            out[i] = v_add(A.P[i], v_mul(delta[a], w))
    return out


