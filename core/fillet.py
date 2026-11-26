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
from .vectors import v_add, v_cross, v_dist, v_dot, v_len, v_lerp, v_mul, v_sub, v_unit
from .bspline import BDir
from .evaluate import srf_coefs, srf_derivs, srf_point
from .lsq import LSQ, gauss_solve
from .patch_fit import side_row_indices
from .smoothing import SPatch, add_seam_eqs, find_seams, seam_scalars
from .cv_order import cv_order_worst
# <<< dev-only
# =============================================================================
# FILLET: rolling ball between two single-span patches, no trims.
#   1. contact lines of a ball of radius R rolled between patch A (side kA)
#      and patch B (side kB) - the patches extended as polynomials if the
#      ball touches beyond their edges (a gap between them)
#   2. both patches extended / cut back to their contact line: the SAME
#      surface, only its boundary moves (refit), no trim
#   3. the fillet: a single-span patch built from the CVs of both new edges -
#      rows straight across (no shear), G2 (or G1) exact, the same cross
#      section all along (production: an even radius)
# Local coordinates of a side k:  t along the side (as SIDE_MAP),  e across:
#   e = 0 on the edge, e > 0 beyond it (extension), e < 0 inside the patch.
# =============================================================================


def _binom(n, k):
    r = 1.0
    for i in range(1, k + 1):
        r = r * (n - k + i) / i
    return r


def bez_basis_ext(p, t, nd):
    """Bernstein basis of degree p and its derivatives up to nd at ANY t
    (no clamping: outside [0, 1] the polynomial goes on = extension)."""
    out = []
    for k in range(nd + 1):
        row = []
        if k > p:
            out.append([0.0] * (p + 1)); continue
        # d^k/dt^k B_i^p = p!/(p-k)! sum_j (-1)^(k-j) C(k,j) B_{i-j}^{p-k}
        f = 1.0
        for x in range(p - k + 1, p + 1):
            f *= x
        q = p - k
        low = [(_binom(q, i) * (t ** i) * ((1.0 - t) ** (q - i))) for i in range(q + 1)]
        for i in range(p + 1):
            s = 0.0
            for j in range(k + 1):
                ii = i - j
                if 0 <= ii <= q:
                    s += ((-1) ** (k - j)) * _binom(k, j) * low[ii]
            row.append(f * s)
        out.append(row)
    return out


def srf_derivs_ext(pu, pv, P, u, v, nd):
    """Like srf_derivs for a single-span patch, but valid outside [0,1]^2."""
    Nu = bez_basis_ext(pu, u, nd)
    Nv = bez_basis_ext(pv, v, nd)
    nv = pv + 1
    out = {}
    for ku in range(nd + 1):
        for kv in range(nd + 1 - ku):
            x = y = z = 0.0
            for a in range(pu + 1):
                cu = Nu[ku][a]
                if cu == 0.0: continue
                for b in range(pv + 1):
                    c = cu * Nv[kv][b]
                    if c == 0.0: continue
                    q = P[a * nv + b]
                    x += c * q[0]; y += c * q[1]; z += c * q[2]
            out[(ku, kv)] = (x, y, z)
    return out


def side_uv(k, t, e):
    """(u, v) of side-k coordinates (t along, e across: 0 edge, >0 beyond)."""
    if k == 0: return (t, -e)
    if k == 1: return (1.0 + e, t)
    if k == 2: return (1.0 - t, 1.0 + e)
    return (-e, 1.0 - t)


def side_te(k, u, v):
    """Inverse of side_uv."""
    if k == 0: return (u, -v)
    if k == 1: return (v, u - 1.0)
    if k == 2: return (1.0 - u, v - 1.0)
    return (1.0 - v, -u)


def side_eval(p, k, t, e, nd=1):
    """Point and derivatives d/dt, d/de (and 2nd) of patch p at side coords."""
    u, v = side_uv(k, t, e)
    D = srf_derivs_ext(p.du.p, p.dv.p, p.P, u, v, nd)
    # chain rule: (u, v) = side_uv(k, t, e)
    if k == 0:   jt, je = (1.0, 0.0), (0.0, -1.0)
    elif k == 1: jt, je = (0.0, 1.0), (1.0, 0.0)
    elif k == 2: jt, je = (-1.0, 0.0), (0.0, 1.0)
    else:        jt, je = (0.0, -1.0), (-1.0, 0.0)
    def comb(a, b):
        return tuple(a * x + b * y for x, y in zip(D[(1, 0)], D[(0, 1)]))
    out = {"p": D[(0, 0)], "t": comb(*jt), "e": comb(*je)}
    if nd >= 2:
        def comb2(j1, j2):
            return tuple(j1[0] * j2[0] * D[(2, 0)][c] + (j1[0] * j2[1] + j1[1] * j2[0]) * D[(1, 1)][c]
                         + j1[1] * j2[1] * D[(0, 2)][c] for c in range(3))
        out["tt"] = comb2(jt, jt); out["ee"] = comb2(je, je); out["te"] = comb2(jt, je)
    return out


# --------------------------------------------------------------- vectors
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def _len(a): return math.sqrt(_dot(a, a))
def _unit(a):
    L = _len(a)
    return _mul(a, 1.0 / L) if L > 1e-300 else (0.0, 0.0, 0.0)


def _solve3(M, r):
    """3x3 linear system (Cramer)."""
    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    D = det(M)
    if abs(D) < 1e-300: return None
    out = []
    for c in range(3):
        m = [list(row) for row in M]
        for i in range(3): m[i][c] = r[i]
        out.append(det(m) / D)
    return tuple(out)


def _closest_te(p, k, q, t0, e0, it=20, t_fixed=False):
    """Closest point of patch p (extended) to q, in side coords starting at
    (t0, e0). t_fixed: only along the cross iso line t = t0."""
    t, e = t0, e0
    for _ in range(it):
        E = side_eval(p, k, t, e, 2)
        d = _sub(E["p"], q)
        if t_fixed:
            g = _dot(d, E["e"]); h = _dot(E["e"], E["e"]) + _dot(d, E["ee"])
            if abs(h) < 1e-300: break
            de = -g / h
            de = max(-0.5, min(0.5, de))
            e += de
            if abs(de) < 1e-12: break
            continue
        gt = _dot(d, E["t"]); ge = _dot(d, E["e"])
        htt = _dot(E["t"], E["t"]) + _dot(d, E["tt"]); hee = _dot(E["e"], E["e"]) + _dot(d, E["ee"])
        hte = _dot(E["t"], E["e"]) + _dot(d, E["te"])
        det = htt * hee - hte * hte
        if abs(det) < 1e-300: break
        dt = -(hee * gt - hte * ge) / det; de = -(htt * ge - hte * gt) / det
        s = 1.0
        mx = max(abs(dt), abs(de))
        if mx > 0.5: s = 0.5 / mx
        t += s * dt; e += s * de
        if mx < 1e-12: break
    return t, e


def _closest_t_edge(p, k, q, t0=0.5, it=30):
    """Closest point of q on the EDGE of side k (e = 0), t clamped to [0,1]."""
    best = None
    for i in range(11):
        t = i / 10.0
        dd = _len(_sub(side_eval(p, k, t, 0.0, 1)["p"], q))
        if best is None or dd < best[0]: best = (dd, t)
    t = best[1]
    for _ in range(it):
        E = side_eval(p, k, t, 0.0, 2)
        dvec = _sub(E["p"], q)
        g = _dot(dvec, E["t"]); h = _dot(E["t"], E["t"]) + _dot(dvec, E["tt"])
        if abs(h) < 1e-300: break
        dt = max(-0.2, min(0.2, -g / h))
        t = min(1.0, max(0.0, t + dt))
        if abs(dt) < 1e-12: break
    return t


def _corner_sides(A, kA, B, kB):
    """Signs of A's and B's normals so that both point into the corner (to
    the side where the other patch's EDGE is)."""
    # the other patch's MIDDLE (not its edge: with patches overlapping each
    # other the edge pokes through to the wrong side)
    sA = sB = 0.0
    for t in (0.25, 0.5, 0.75):
        pa = side_eval(A, kA, t, 0.0, 1)["p"]
        tb = _closest_t_edge(B, kB, pa)
        pb = side_eval(B, kB, tb, 0.0, 1)["p"]
        nA, _ = side_normal(A, kA, t, 0.0); nB, _ = side_normal(B, kB, tb, 0.0)
        qb = side_eval(B, kB, tb, -0.5, 1)["p"]; qa = side_eval(A, kA, t, -0.5, 1)["p"]
        sA += _dot(nA, _sub(qb, pa)); sB += _dot(nB, _sub(qa, pb))
    return (1.0 if sA > 0 else -1.0), (1.0 if sB > 0 else -1.0)


def side_normal(p, k, t, e):
    E = side_eval(p, k, t, e, 1)
    return _unit(_cross(E["t"], E["e"])), E


class Contact(object):
    """One station of the rolling ball."""
    def __init__(self, t, eA, tB, eB, C, pA, pB, nA, nB, R):
        self.t = t; self.eA = eA; self.tB = tB; self.eB = eB
        self.C = C; self.pA = pA; self.pB = pB; self.nA = nA; self.nB = nB; self.R = R


def rolling_ball(A, kA, B, kB, R_of_t, stations=None, it=40):
    """Contact stations of a ball of radius R_of_t(t) rolled between patch A
    (side kA) and patch B (side kB). Stations: A's side parameter t; the
    ball centre lies in the plane through A's contact point normal to A's
    direction along the side, so A's contact point stays on A's cross iso
    line t (its new edge is then exactly a function e(t)).
    -> [Contact] (None for a station that did not converge)."""
    if stations is None:
        stations = [i / 20.0 for i in range(21)]
    out = []
    # sides of the normals: towards the other patch (inside the corner)
    sA, sB = _corner_sides(A, kA, B, kB)
    tb0, eb0 = _closest_t_edge(B, kB, side_eval(A, kA, 0.5, 0.0, 1)["p"]), 0.0
    size = max(_len(_sub(side_eval(A, kA, 0.0, 0.0, 1)["p"], side_eval(A, kA, 1.0, 0.0, 1)["p"])), 1e-9)
    tB, eB = tb0, eb0
    for t in stations:
        R = R_of_t(t)
        eA = 0.0
        pA = side_eval(A, kA, t, eA, 1)["p"]
        tB, eB = _closest_t_edge(B, kB, pA, tB), 0.0
        ok = False
        C = None
        for _ in range(it):
            nA, EA = side_normal(A, kA, t, eA); nA = _mul(nA, sA)
            nB, EB = side_normal(B, kB, tB, eB); nB = _mul(nB, sB)
            pA = EA["p"]; pB = EB["p"]
            ta = _unit(EA["t"])
            C = _solve3([list(nA), list(nB), list(ta)], [_dot(nA, pA) + R, _dot(nB, pB) + R, _dot(ta, pA)])
            if C is None: break
            eA2 = _closest_te(A, kA, C, t, eA, 20, True)[1]
            tB2, eB2 = _closest_te(B, kB, C, tB, eB)
            moved = abs(eA2 - eA) + abs(tB2 - tB) + abs(eB2 - eB)
            eA, tB, eB = eA2, tB2, eB2
            if moved < 1e-10:
                ok = True; break
        if not ok or C is None:
            out.append(None); continue
        nA, EA = side_normal(A, kA, t, eA); nB, EB = side_normal(B, kB, tB, eB)
        pA = EA["p"]; pB = EB["p"]
        # sanity: both contacts at distance R from the centre
        if abs(_len(_sub(C, pA)) - R) > 1e-4 * size + 1e-6 or abs(_len(_sub(C, pB)) - R) > 1e-4 * size + 1e-6:
            out.append(None); continue
        out.append(Contact(t, eA, tB, eB, C, pA, pB, _mul(nA, sA), _mul(nB, sB), R))
    return out


def radius_from_gap(A, kA, B, kB, n=11):
    """Radius that puts the contact lines where the edges are now (the gap
    drawn by the user): per station  chord / (2 cos(alpha/2)),  alpha the
    angle between the normals pointing into the corner. -> (mean, list)."""
    vals = []
    sA, sB = _corner_sides(A, kA, B, kB)
    tb = 0.5
    for i in range(n):
        t = (i + 0.5) / n
        pa = side_eval(A, kA, t, 0.0, 1)["p"]
        tb = _closest_t_edge(B, kB, pa, tb)
        pb = side_eval(B, kB, tb, 0.0, 1)["p"]
        nA, _ = side_normal(A, kA, t, 0.0); nB, _ = side_normal(B, kB, tb, 0.0)
        nA = _mul(nA, sA); nB = _mul(nB, sB)
        c = _len(_sub(pa, pb))
        cosal = max(-1.0, min(1.0, _dot(nA, nB)))
        half = math.cos(0.5 * math.acos(cosal))
        if half > 1e-3:
            vals.append(c / (2.0 * half))
    if not vals: return None, []
    vals.sort()
    m = vals[len(vals) // 2]
    good = [v for v in vals if abs(v - m) <= 0.5 * m]
    return sum(good) / len(good), vals


# --------------------------------------------------------------- helpers
def poly_fit(xs, ys, deg):
    """Least-squares polynomial (coefficients, low first)."""
    deg = max(0, min(deg, len(xs) - 1))
    n = deg + 1
    A = [[0.0] * n for _ in range(n)]; b = [0.0] * n
    for x, y in zip(xs, ys):
        pw = [x ** i for i in range(n)]
        for i in range(n):
            b[i] += pw[i] * y
            for j in range(n):
                A[i][j] += pw[i] * pw[j]
    for i in range(n): A[i][i] += 1e-14
    sol = gauss_solve(A, [[v] for v in b])
    return [r[0] for r in sol]


def poly_fit_ends(xs, ys, deg):
    """Polynomial through the FIRST and LAST value exactly (the ends of the
    fillet are shared with the next one), least squares in between.
    xs must start at 0 and end at 1."""
    y0, y1 = ys[0], ys[-1]
    if deg < 2 or len(xs) < 4:
        return [y0, y1 - y0]
    qx = []; qy = []
    for x, y in zip(xs[1:-1], ys[1:-1]):
        w = x * (1.0 - x)
        if w < 1e-9: continue
        qx.append(x); qy.append((y - y0 - (y1 - y0) * x) / w)
    q = poly_fit(qx, qy, deg - 2) if qx else [0.0]
    # y = y0 + (y1-y0) x + (x - x^2) q(x)
    c = [0.0] * (deg + 1)
    c[0] += y0; c[1] += y1 - y0
    for i, qi in enumerate(q):
        c[i + 1] += qi
        c[i + 2] -= qi
    return c


def poly_val(c, x):
    s = 0.0
    for i in range(len(c) - 1, -1, -1):
        s = s * x + c[i]
    return s


def cross_degree(p, k):
    return p.dv.p if k % 2 == 0 else p.du.p


def row_by_t(p, k, r, P=None):
    """CV row r (0 = the edge) of side k, ordered by the side's t."""
    P = P if P is not None else p.P
    idx = side_row_indices(p.du, p.dv, k, r)
    if k in (2, 3):
        idx = list(reversed(idx))
    return [P[i] for i in idx]


def reshape_to_line(p, k, e_of_t, t_of_t=None, ns=None):
    """The SAME surface, its side k moved to the line e = e_of_t(t) (side
    coords; > 0 extends beyond the edge, < 0 cuts back) - no trim: the CVs
    are refitted. t_of_t: the old side parameter for the new one (the
    patch is stretched / shortened along the side too); None = unchanged.
    The opposite side stays where it is. -> (new CVs, max deviation from
    the old surface)."""
    du, dv = p.du, p.dv
    ns = ns or (2 * max(du.n, dv.n) + 5)
    tt = t_of_t or (lambda t: t)
    L = LSQ(p.n())
    pts = []
    for i in range(ns):
        for j in range(ns):
            u, v = i / (ns - 1.0), j / (ns - 1.0)
            t, e_new = side_te(k, u, v)            # e_new in [-1, 0]: -1 = opposite side
            x = 1.0 + e_new                          # cross param from the opposite side 0..1
            ec = e_of_t(t)
            x_old = x * (1.0 + ec)
            uo, vo = side_uv(k, tt(t), x_old - 1.0)
            q = srf_derivs_ext(du.p, dv.p, p.P, uo, vo, 0)[(0, 0)]
            L.add(srf_coefs(du, dv, u, v, 0, 0), q, 1.0)
            pts.append((u, v, q))
    for i in range(p.n()):
        L.add([(i, 1.0)], p.P[i], 1e-10)
    P2 = L.solve()[0]
    dev = max(v_dist(srf_point(du, dv, P2, u, v), q) for (u, v, q) in pts)
    return P2, dev


def bez_curve(C, w):
    n = len(C) - 1
    out = (0.0, 0.0, 0.0)
    for i, c in enumerate(C):
        out = v_add(out, v_mul(c, _binom(n, i) * (w ** i) * ((1.0 - w) ** (n - i))))
    return out




def build_fillet(prep, level=2, shape=1.0):
    """The fast part: the fillet CVs for a level (2/1) and shape from prep.
    -> dict(A=new CVs of A, B=new CVs of B, F=fillet CVs (u along A's side,
    v across from A to B; degrees (along, 5)), ...)."""
    A = prep["A"]; kA = prep["kA"]; B = prep["B"]; kB = prep["kB"]
    PA = prep["PA"]; PB = prep["PB"]; cs = prep["cs"]; csB = prep["csB"]; revB = prep["revB"]
    devA = prep["devA"]; devB = prep["devB"]
    A2 = SPatch(A.key, A.du.p, A.dv.p, PA); B2 = SPatch(B.key, B.du.p, B.dv.p, PB)
    SA = side_bands(A2, kA); SB = side_bands(B2, kB, revB)
    pa_, pb_ = len(SA["E"]) - 1, len(SB["E"]) - 1
    if pa_ < pb_: SA = elevate_bands(SA, pb_ - pa_)
    if pb_ < pa_: SB = elevate_bands(SB, pa_ - pb_)
    pal = len(SA["E"]) - 1
    mid = cs[len(cs) // 2]
    du_f = BDir(pal + 2, 1); dv_f = BDir(5, 1)

    def arc_dir(c, side_a):
        """The ball's arc at a contact point: its tangent (towards the other
        contact) and the normal - the same for two fillets meeting there."""
        p0, p1 = (c.pA, c.pB) if side_a else (c.pB, c.pA)
        n_ = v_unit(v_sub(c.C, p0))
        d_ = v_sub(p1, p0)
        d_ = v_unit(v_sub(d_, v_mul(n_, v_dot(d_, n_))))
        return d_, n_
    c_first, c_last = cs[0], cs[-1]
    endsA = [arc_dir(c_first, True), arc_dir(c_last, True)]
    # B's own balls at B's corners (in csB "A" is B)
    endsB = [arc_dir(csB[-1] if revB else csB[0], True), arc_dir(csB[0] if revB else csB[-1], True)]

    cmA = cs[len(cs) // 2]; cmB = csB[len(csB) // 2]
    midA = arc_dir(cmA, True); midB = arc_dir(cmB, True)

    def build(L):
        if len(L) == 3 and level < 2:
            L0, Lm, L1 = L
            LB = (L0, Lm, L1)          # SB is already in the fillet's t (reversed if revB)
            ra = fillet_rows_geo(SA, [endsA[0], midA, endsA[1]], (L0, Lm, L1), level)
            rb = fillet_rows_geo(SB, [endsB[0], midB, endsB[1]], LB, level)
        else:
            L0, L1 = L[0], L[-1]
            LB = (L0, L1)              # SB is already in the fillet's t (reversed if revB)
            ra = fillet_rows_geo(SA, endsA, (L0, L1), level)
            rb = fillet_rows_geo(SB, endsB, LB, level)
        if level >= 2:
            rows = [ra[0], ra[1], ra[2], rb[2], rb[1], rb[0]]
        else:
            rows = [ra[0], ra[1], None, None, rb[1], rb[0]]
            rows[2] = [v_lerp(a, b, 1.0 / 3.0) for a, b in zip(ra[1], rb[1])]
            rows[3] = [v_lerp(a, b, 2.0 / 3.0) for a, b in zip(ra[1], rb[1])]
        P = [rows[r][i] for i in range(pal + 3) for r in range(6)]
        return P, rows

    # the tangent length L is found at each END from that end's ball alone
    # (the section at t = 0 / 1 depends only on that end's values): two
    # fillets meeting at a station get the same L there - the same section
    def with_end(Lx, t_end, other):
        return (Lx, other) if t_end == 0.0 else (other, Lx)

    def solve_end(c, t_end, other):
        cm = _mul(_add(c.pA, c.pB), 0.5)
        am = _add(c.C, _mul(_unit(_sub(cm, c.C)), c.R))
        tg = _add(cm, _mul(_sub(am, cm), shape)); ax = _unit(_sub(am, cm))
        ch = _len(_sub(c.pA, c.pB))
        if level >= 2:
            def f(Lx):
                P_, _r = build(with_end(Lx, t_end, other))
                return _dot(_sub(srf_point(du_f, dv_f, P_, t_end, 0.5), tg), ax)
            lo, hi = 0.02 * ch, 3.0 * ch
            flo, fhi = f(lo), f(hi)
            if flo * fhi > 0:
                return lo if abs(flo) < abs(fhi) else hi
            for _ in range(60):
                mL = 0.5 * (lo + hi); fm = f(mL)
                if flo * fm <= 0: hi, fhi = mL, fm
                else: lo, flo = mL, fm
            return 0.5 * (lo + hi)

        cb = (csB[-1] if revB else csB[0]) if t_end == 0.0 else (csB[0] if revB else csB[-1])

        def arc_err(Lx):
            P_, _r = build(with_end(Lx, t_end, other))
            P_ = _g1_arc_fit(P_, du_f, dv_f, [c], [cb], revB)
            e_ = 0.0
            for k_ in range(1, 20):
                q_ = srf_point(du_f, dv_f, P_, t_end, k_ / 20.0)
                e_ = max(e_, abs(_len(_sub(q_, c.C)) - c.R))
            return e_
        a_, b_ = 0.05 * ch, 1.2 * ch
        g_ = 0.5 * (math.sqrt(5.0) - 1.0)
        x1 = b_ - g_ * (b_ - a_); x2 = a_ + g_ * (b_ - a_)
        f1, f2 = arc_err(x1), arc_err(x2)
        for _ in range(40):
            if f1 < f2:
                b_, x2, f2 = x2, x1, f1; x1 = b_ - g_ * (b_ - a_); f1 = arc_err(x1)
            else:
                a_, x1, f1 = x1, x2, f2; x2 = a_ + g_ * (b_ - a_); f2 = arc_err(x2)
        return 0.5 * (a_ + b_)
    chord = _len(_sub(mid.pA, mid.pB))
    L0 = solve_end(cs[0], 0.0, chord)
    L1 = solve_end(cs[-1], 1.0, L0)
    L = (L0, L1)
    if level < 2:
        # the middle: the same arc search with both ends fixed
        def arc_err_mid(Lx):
            P_, _r = build((L0, Lx, L1))
            P_ = _g1_arc_fit(P_, du_f, dv_f, [cmA], [cmB], revB)
            e_ = 0.0
            for k_ in range(1, 20):
                q_ = srf_point(du_f, dv_f, P_, cmA.t, k_ / 20.0)
                e_ = max(e_, abs(_len(_sub(q_, cmA.C)) - cmA.R))
            return e_
        a_, b_ = 0.05 * chord, 1.2 * chord
        g_ = 0.5 * (math.sqrt(5.0) - 1.0)
        x1 = b_ - g_ * (b_ - a_); x2 = a_ + g_ * (b_ - a_)
        f1, f2 = arc_err_mid(x1), arc_err_mid(x2)
        for _ in range(40):
            if f1 < f2:
                b_, x2, f2 = x2, x1, f1; x1 = b_ - g_ * (b_ - a_); f1 = arc_err_mid(x1)
            else:
                a_, x1, f1 = x1, x2, f2; x2 = a_ + g_ * (b_ - a_); f2 = arc_err_mid(x2)
        L = (L0, 0.5 * (a_ + b_), L1)
    PF, rows = build(L)
    if level < 2:
        PF = _g1_arc_fit(PF, du_f, dv_f, cs, csB, revB)
    return {"A": PA, "B": PB, "F": PF, "deg": (pal + 2, 5), "revB": revB, "devA": devA, "devB": devB,
            "contacts": cs, "L": L, "shape": shape, "level": level}


def section_curvature(P, du, dv, t, n=21):
    out = []
    for i in range(n):
        w = i / (n - 1.0)
        D = srf_derivs(du, dv, P, t, w, 2)
        d1 = D[(0, 1)]; d2 = D[(0, 2)]
        L1 = v_len(d1)
        out.append(v_len(v_cross(d1, d2)) / max(L1 ** 3, 1e-300))
    return out


def _one_hump(ks, rel=0.005):
    """The cross-section curvature rises to ONE maximum and falls (it need
    not be in the middle - an uneven gap gives an off-centre highlight, but
    still one): no dip = no double highlight."""
    ks = [abs(k) for k in ks]
    top = max(ks); i0 = ks.index(top); tol = rel * top
    up = all(ks[i + 1] >= ks[i] - tol for i in range(i0))
    down = all(ks[i + 1] <= ks[i] + tol for i in range(i0, len(ks) - 1))
    return up and down


def auto_shape(prep, level=2):
    """G2: the smallest shape whose cross section has ONE curvature maximum
    AND whose CV net stays clean (no CV crossing or squeezing against the
    one next to it). If no shape gives one maximum, the cleanest one that
    keeps the net clean."""
    if level < 2:
        return 1.0
    near = globals().get("CV_NEAR", 0.15)
    s_ = 1.0; best = None
    while s_ <= 1.8 + 1e-9:
        res = build_fillet(prep, level, s_)
        PF = res["F"]
        order = cv_order_worst(PF, res["deg"][0] + 1, 6)[0]
        if order >= near:
            ks = section_curvature(PF, BDir(res["deg"][0], 1), BDir(5, 1), 0.5)
            if _one_hump(ks):
                return s_
            a = [abs(k) for k in ks]
            score = a[len(a) // 2] / (max(a) or 1.0)
            if best is None or score > best[0]:
                best = (score, s_)
        elif best is not None:
            break                      # sharper only squeezes the net more
        s_ += 0.02
    return best[1] if best else 1.0


def _g1_arc_fit(PF, du_f, dv_f, cs, csB=None, revB=False):
    """G1 fillet: the two middle rows fitted to the ball's arcs (rows 0, 1, 4,
    5 stay = G1 exact), i.e. a real circular-looking section. A's half from
    the balls rolled along A, B's half from those rolled along B (each
    lands on its own edge exactly at the fillet's t)."""
    nal = du_f.n
    free = set()
    for i in range(nal):
        free.add(i * 6 + 2); free.add(i * 6 + 3)
    fixed = dict((i, PF[i]) for i in range(len(PF)) if i not in free)
    L = LSQ(len(PF), 0, fixed)

    def arc_pts(c, from_a, t_f, half):
        a = _unit(_sub(c.pA, c.C)); b = _unit(_sub(c.pB, c.C))
        ang = math.acos(max(-1.0, min(1.0, _dot(a, b))))
        if ang < 1e-6: return
        for k_ in range(1, 12):
            w = k_ / 12.0
            if (half == "lo" and w > 0.55) or (half == "hi" and w < 0.45):
                continue
            q = _add(_mul(a, math.sin((1 - w) * ang) / math.sin(ang)), _mul(b, math.sin(w * ang) / math.sin(ang)))
            wf = w if from_a else 1.0 - w
            L.add(srf_coefs(du_f, dv_f, t_f, wf, 0, 0), _add(c.C, _mul(q, c.R)), 1.0)
    if csB is None:
        for c in cs:
            arc_pts(c, True, c.t, "all")
    else:
        for c in cs:
            arc_pts(c, True, c.t, "lo")
        for c in csB:
            arc_pts(c, False, (1.0 - c.t) if revB else c.t, "lo")
    for i in range(len(PF)):
        L.add([(i, 1.0)], PF[i], 1e-8)
    return L.solve()[0]


# ---------------------------------------------------------------- Bernstein
def bern_elevate(C, r=1):
    """Degree elevation of a Bezier (vector CVs) by r."""
    for _ in range(r):
        n = len(C) - 1
        D = [C[0]]
        for i in range(1, n + 1):
            a = i / float(n + 1)
            D.append(v_add(v_mul(C[i - 1], a), v_mul(C[i], 1.0 - a)))
        D.append(C[n])
        C = D
    return C


def bern_mul(f, C):
    """Scalar Bezier f (coefs, degree m) times vector Bezier C (degree n)."""
    m = len(f) - 1; n = len(C) - 1
    out = []
    for k in range(m + n + 1):
        s = (0.0, 0.0, 0.0)
        for i in range(max(0, k - n), min(m, k) + 1):
            j = k - i
            w = _binom(m, i) * _binom(n, j) / _binom(m + n, k)
            s = v_add(s, v_mul(C[j], w * f[i]))
        out.append(s)
    return out


def bern_add(*Cs):
    deg = max(len(c) for c in Cs) - 1
    Cs = [bern_elevate(c, deg - (len(c) - 1)) for c in Cs]
    return [tuple(sum(c[i][x] for c in Cs) for x in range(3)) for i in range(deg + 1)]


def bern_deriv(C):
    n = len(C) - 1
    if n == 0: return [(0.0, 0.0, 0.0)]
    return [v_mul(v_sub(C[i + 1], C[i]), float(n)) for i in range(n)]


def bern_scale(C, s):
    return [v_mul(c, s) for c in C]


def elevate_bands(S, r):
    E = bern_elevate(S["E"], r); Ec = bern_elevate(S["Ec"], r); Ecc = bern_elevate(S["Ecc"], r)
    Et = bern_deriv(E)
    return {"E": E, "Ec": Ec, "Ecc": Ecc, "Et": Et, "Ett": bern_deriv(Et), "Ect": bern_deriv(Ec)}


def side_bands(p, k, rev=False):
    """The edge data of side k as Bezier curves in t (outward cross):
    E (edge), Ec (d/de), Ecc, Et, Ett, Ect."""
    R0, R1, R2 = [row_by_t(p, k, r) for r in range(3)]
    if rev:
        R0, R1, R2 = [list(reversed(x)) for x in (R0, R1, R2)]
    n = cross_degree(p, k)
    Ec = [v_mul(v_sub(a, b), float(n)) for a, b in zip(R0, R1)]
    Ecc = [v_mul(v_add(v_sub(a, v_mul(b, 2.0)), c), float(n * (n - 1))) for a, b, c in zip(R0, R1, R2)]
    Et = bern_deriv(R0)
    return {"E": R0, "Ec": Ec, "Ecc": Ecc, "Et": Et, "Ett": bern_deriv(Et), "Ect": bern_deriv(Ec)}


def _bern_eval(C, t):
    return bez_curve(C, t) if len(C) > 1 else C[0]


def _side_coeffs(S, t, d_geo, L, nrm):
    """lambda, beta, b, gamma at t: F_w = L d_geo exactly (lambda Ec + beta Et),
    F_ww purely along the normal (b, gamma take the in-plane part away)."""
    Ec = _bern_eval(S["Ec"], t); Et = _bern_eval(S["Et"], t)
    tgt = v_mul(d_geo, L)
    # 2x2 in the tangent plane: least squares on 3 components
    a11 = v_dot(Ec, Ec); a12 = v_dot(Ec, Et); a22 = v_dot(Et, Et)
    r1 = v_dot(Ec, tgt); r2 = v_dot(Et, tgt)
    det = a11 * a22 - a12 * a12
    lam = (a22 * r1 - a12 * r2) / det; bet = (a11 * r2 - a12 * r1) / det
    Ecc = _bern_eval(S["Ecc"], t); Ect = _bern_eval(S["Ect"], t); Ett = _bern_eval(S["Ett"], t)
    W = v_add(v_add(v_mul(Ecc, lam * lam), v_mul(Ect, 2.0 * lam * bet)), v_mul(Ett, bet * bet))
    Wt = v_sub(W, v_mul(nrm, v_dot(W, nrm)))            # in-plane part -> taken away by b, gamma
    r1 = -v_dot(Ec, Wt); r2 = -v_dot(Et, Wt)
    b = (a22 * r1 - a12 * r2) / det; g = (a11 * r2 - a12 * r1) / det
    return lam, bet, b, g


def fillet_rows_geo(S, ends, L, level):
    """Rows F0, F1, F2 from one side's bands with LINEAR lambda, beta, b,
    gamma interpolating the geometric values at both ends (t = 0, 1).
    ends = [(d_geo, normal) at t=0, at t=1]. Degrees: row0 p+2 (elevated),
    row1 p+2, row2 p+2."""
    Ls = [L, L] if not isinstance(L, (tuple, list)) else list(L)
    c0 = _side_coeffs(S, 0.0, ends[0][0], Ls[0], ends[0][1])
    c1 = _side_coeffs(S, 1.0, ends[-1][0], Ls[-1], ends[-1][1])
    lam = [c0[0], c1[0]]; bet = [c0[1], c1[1]]; b = [c0[2], c1[2]]; g = [c0[3], c1[3]]
    F0 = S["E"]
    if level < 2 and len(ends) == 3:
        # G1: lambda, beta quadratic through the middle too (a curved edge:
        # the tangent length stays even all along, so the arc fits)
        cm = _side_coeffs(S, 0.5, ends[1][0], Ls[1], ends[1][1])
        lq = [c0[0], 2.0 * cm[0] - 0.5 * (c0[0] + c1[0]), c1[0]]
        bq = [c0[1], 2.0 * cm[1] - 0.5 * (c0[1] + c1[1]), c1[1]]
        Fw = bern_add(bern_mul(lq, S["Ec"]), bern_mul(bq, bern_elevate(S["Et"], 1)))  # degree p+2
        F1 = bern_add(bern_elevate(F0, 2), bern_scale(Fw, 1.0 / 5.0))
        return [bern_elevate(F0, 2), F1, None]
    Fw = bern_add(bern_mul(lam, S["Ec"]), bern_mul(bet, bern_elevate(S["Et"], 1)))     # degree p+1
    F1 = bern_add(F0, bern_scale(Fw, 1.0 / 5.0))
    if level < 2:
        return [bern_elevate(F0, 2), bern_elevate(F1, 1), None]
    lam2 = [lam[0] ** 2, lam[0] * lam[1], lam[1] ** 2]
    lb2 = [2 * lam[0] * bet[0], lam[0] * bet[1] + lam[1] * bet[0], 2 * lam[1] * bet[1]]
    bet2 = [bet[0] ** 2, bet[0] * bet[1], bet[1] ** 2]
    Fww = bern_add(bern_mul(lam2, S["Ecc"]), bern_mul(lb2, bern_elevate(S["Ect"], 1)),
                   bern_mul(bet2, bern_elevate(S["Ett"], 2)), bern_mul(b, S["Ec"]),
                   bern_mul(g, bern_elevate(S["Et"], 1)))                                  # degree p+2
    F2 = bern_add(bern_scale(F1, 2.0), bern_scale(F0, -1.0), bern_scale(Fww, 1.0 / 20.0))
    return [bern_elevate(F0, 2), bern_elevate(F1, 1), F2]


def smooth_fillet_chain(fillets, bigs, level=2, tol=0.05, iters=3):
    """Consecutive fillets meet exactly (G0) by construction; G1/G2 between
    them comes from ONE joint solve over the fillets' inner CV rows: the big
    patches and the fillets' edge rows (on the big patches) stay, the
    fillet-to-big seams stay as they are, the fillet-to-fillet seams get
    their level. -> {key: new CVs} of the fillets, text."""
    allp = list(bigs) + list(fillets)
    fk = set(p.key for p in fillets)
    off = 0
    for p in allp:
        p.off = off; off += p.n()
    fixed = {}
    P_orig = dict((p.key, list(p.P)) for p in allp)
    for p in allp:
        for i in range(p.n()):
            if p.key not in fk:
                fixed[p.off + i] = p.P[i]
            else:
                j = i % p.dv.n
                if j in (0, p.dv.n - 1):
                    fixed[p.off + i] = p.P[i]
    seams = [sm for sm in find_seams(allp, tol) if sm["X"].key in fk or sm["Y"].key in fk]
    old = {}
    for p in allp:
        for i in range(p.n()):
            old[p.off + i] = p.P[i]
    for sm in seams:
        sm["level"] = level
        seam_scalars(sm)
    new = {}
    try:
        for _ in range(iters):
            L = LSQ(off, 0, fixed)
            for p in allp:
                for i in range(p.n()):
                    L.add([(p.off + i, 1.0)], P_orig[p.key][i], 1.0)
            for sm in seams:
                both_f = sm["X"].key in fk and sm["Y"].key in fk
                add_seam_eqs(L, sm, level, 0 if both_f else level, old)
            sol = L.solve()[0]
            for p in fillets:
                p.P = [sol[p.off + i] for i in range(p.n())]
        new = dict((p.key, list(p.P)) for p in fillets)
    finally:
        for p in allp:
            p.P = P_orig[p.key]
    return new


def _edge_ends(p, k):
    return side_eval(p, k, 0.0, 0.0, 1)["p"], side_eval(p, k, 1.0, 0.0, 1)["p"]



