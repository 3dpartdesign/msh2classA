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
from .evaluate import srf_point
from .lsq import gauss_solve
from .patch_fit import side_row_indices
from .smoothing import SPatch
from .fillet import _add, _binom, _closest_t_edge, _cross, _dot, _len, _mul, _sub, _unit, side_eval
# <<< dev-only
# =============================================================================
# NPATCH: an N-sided hole filled with Rhino's Patch (trimmed, its surface
# rebuilt to 6x6 = one span, degree 5). It never changes; its neighbours are
# brought to it (G0 / G1 / G2) through a PROXY strip: an untrimmed 6-row
# patch lying on the NPatch surface along the neighbour's edge - the
# ordinary seam machinery (match_step) then works as with any patch.
# =============================================================================

def _bern_all(p, t):
    return [_binom(p, i) * (t ** i) * ((1.0 - t) ** (p - i)) for i in range(p + 1)]


def loop_corners(L, flat_deg=25.0):
    """-> (real corners, flat ones): at a flat corner the loop runs on
    almost straight (a T-joint point, not a corner of the hole)."""
    real = 0; flat = 0
    n = len(L)
    for i in range(n):
        a = L[i]; b = L[(i + 1) % n]
        pa, ka, a0, a1, fa = a; pb, kb, b0, b1, fb = b
        ta = a1 if fa else a0; tb = b0 if fb else b1
        da = _mul(side_eval(pa, ka, ta, 0.0, 1)["t"], (a1 - a0) * (1.0 if fa else -1.0))
        db = _mul(side_eval(pb, kb, tb, 0.0, 1)["t"], (b1 - b0) * (1.0 if fb else -1.0))
        c = _dot(_unit(da), _unit(db))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, c))))
        if ang < flat_deg: flat += 1
        else: real += 1
    return real, flat


def point_in_loop(poly, q):
    """q inside the loop seen along the loop's own normal (Newell), and not
    far off its plane."""
    n = len(poly)
    c = tuple(sum(p[i] for p in poly) / n for i in range(3))
    N = [0.0, 0.0, 0.0]
    for i in range(n):
        a = poly[i]; b = poly[(i + 1) % n]
        N[0] += (a[1] - b[1]) * (a[2] + b[2]); N[1] += (a[2] - b[2]) * (a[0] + b[0]); N[2] += (a[0] - b[0]) * (a[1] + b[1])
    ln = _len(N)
    if ln < 1e-12: return False
    N = _mul(N, 1.0 / ln)
    ex = _unit(_sub(poly[0], c)); ey = _cross(N, ex)
    size = max(_len(_sub(p, c)) for p in poly)
    d = _sub(q, c)
    if abs(_dot(d, N)) > size: return False
    x, y = _dot(d, ex), _dot(d, ey)
    P2 = [(_dot(_sub(p, c), ex), _dot(_sub(p, c), ey)) for p in poly]
    inside = False
    for i in range(n):
        (x0, y0), (x1, y1) = P2[i], P2[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xi = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xi > x: inside = not inside
    return inside


def _edge_row(N, k):
    """CV indices of N's side k in the order of the side's t."""
    r0 = side_row_indices(N.du, N.dv, k, 0)
    a = side_eval(N, k, 0.0, 0.0, 1)["p"]
    if _len(_sub(N.P[r0[0]], a)) > _len(_sub(N.P[r0[-1]], a)):
        r0 = list(reversed(r0))
    return r0


def proxy_strip(N, k, S, W, key="_proxy", nw=11):
    """An untrimmed patch on surface S along N's side k: its edge row IS
    N's edge row (exact seam), its 5 other rows fitted to S across the edge,
    over a band of width W into the hole. S: closest(q) -> (u, v) and
    derivs(u, v) -> (point, Su, Sv, unit normal). -> (SPatch, max fit error)
    or (None, reason)."""
    r0 = _edge_row(N, k)
    pa = len(r0) - 1; na = pa + 1
    ns = max(3 * na + 2, 12)
    grid = []
    for i in range(ns):
        t = i / (ns - 1.0)
        E = side_eval(N, k, t, 0.0, 1)
        x = E["p"]; T3 = _unit(E["t"]); eout = E["e"]
        uv = S.closest(x)
        if uv is None: return None, "the NPatch surface is not found at the neighbour's edge"
        col = [(t, 0.0, x)]
        u, v = uv
        h = W / (nw - 1.0); sub = 4
        for j in range(1, nw):
            for _ in range(sub):
                pt, Su, Sv, n = S.derivs(u, v)
                A3 = _cross(n, T3)
                if _dot(A3, eout) < 0: A3 = _mul(A3, -1.0)
                A3 = _unit(A3)
                d = _mul(A3, h / sub)
                a11 = _dot(Su, Su); a12 = _dot(Su, Sv); a22 = _dot(Sv, Sv)
                b1 = _dot(Su, d); b2 = _dot(Sv, d)
                det = a11 * a22 - a12 * a12
                if abs(det) < 1e-300: return None, "the NPatch surface is degenerate there"
                u += (a22 * b1 - a12 * b2) / det; v += (a11 * b2 - a12 * b1) / det
                if not S.inside(u, v):
                    return None, "band leaves the NPatch surface"
            col.append((t, j / (nw - 1.0), S.derivs(u, v)[0]))
        grid.append(col)
    # least squares for rows 1..5 (row 0 = N's edge row, exact)
    pw = 5; nwc = pw + 1
    fixed = [N.P[i] for i in r0]
    nunk = na * pw
    A = [[0.0] * nunk for _ in range(nunk)]; B = [[0.0, 0.0, 0.0] for _ in range(nunk)]
    for col in grid:
        for (t, w, X) in col:
            bu = _bern_all(pa, t); bw = _bern_all(pw, w)
            rhs = list(X)
            for a in range(na):
                c = bu[a] * bw[0]
                if c:
                    rhs = [rhs[q] - c * fixed[a][q] for q in range(3)]
            idx = []; val = []
            for a in range(na):
                for b in range(1, nwc):
                    c = bu[a] * bw[b]
                    if c != 0.0:
                        idx.append(a * pw + (b - 1)); val.append(c)
            for m1 in range(len(idx)):
                i1 = idx[m1]; v1 = val[m1]
                for q in range(3): B[i1][q] += v1 * rhs[q]
                row = A[i1]
                for m2 in range(len(idx)):
                    row[idx[m2]] += v1 * val[m2]
    for i in range(nunk): A[i][i] += 1e-12
    X = gauss_solve(A, B)
    P = []
    for a in range(na):
        P.append(fixed[a])
        for b in range(1, nwc):
            P.append(tuple(X[a * pw + (b - 1)]))
    px = SPatch(key, pa, pw, P)
    err = 0.0
    for col in grid:
        for (t, w, Xp) in col:
            err = max(err, _len(_sub(srf_point(px.du, px.dv, px.P, t, w), Xp)))
    return px, err


def patch_corners(allp, tol):
    """All patch corners (shared ones once): [point]."""
    out = []
    for p in allp:
        nu, nv = p.du.n, p.dv.n
        for q in (p.P[0], p.P[nv - 1], p.P[(nu - 1) * nv], p.P[nu * nv - 1]):
            if not any(_len(_sub(q, c)) < tol for c in out):
                out.append(q)
    return out


def _bb_near(p, q, pad):
    for c in range(3):
        lo = min(x[c] for x in p.P) - pad; hi = max(x[c] for x in p.P) + pad
        if q[c] < lo or q[c] > hi: return False
    return True


def edge_between(allp, qa, qb, tol):
    """The side - or the part of a side - running from corner qa to corner
    qb, with no other patch on it: (p, k, ta, tb) or (None, reason)."""
    cands = []
    for p in allp:
        if not (_bb_near(p, qa, tol) and _bb_near(p, qb, tol)): continue
        for k in range(4):
            ta = _closest_t_edge(p, k, qa)
            if _len(_sub(side_eval(p, k, ta, 0.0, 1)["p"], qa)) > tol: continue
            tb = _closest_t_edge(p, k, qb)
            if _len(_sub(side_eval(p, k, tb, 0.0, 1)["p"], qb)) > tol: continue
            if abs(ta - tb) < 1e-3: continue
            cands.append((p, k, ta, tb))
    if not cands:
        return None, "no patch edge runs between these two corners"
    for (p, k, ta, tb) in cands:
        free = True
        for s_ in (0.25, 0.5, 0.75):
            mq = side_eval(p, k, ta + (tb - ta) * s_, 0.0, 1)["p"]
            for q in allp:
                if q is p or not _bb_near(q, mq, tol): continue
                for kq in range(4):
                    t_ = _closest_t_edge(q, kq, mq)
                    if _len(_sub(side_eval(q, kq, t_, 0.0, 1)["p"], mq)) < tol:
                        free = False; break
                if not free: break
            if not free: break
        if free:
            return (p, k, ta, tb), ""
    return None, "the edge between these two corners already has a patch on both sides (a seam)"


def _bern_d(p, t, order):
    """Bernstein basis of degree p at t, or its 1st / 2nd derivative."""
    if order == 0:
        return _bern_all(p, t)
    if p - order < 0:
        return [0.0] * (p + 1)
    low = _bern_all(p - order, t)
    out = [0.0] * (p + 1)
    if order == 1:
        for i in range(p + 1):
            a = low[i - 1] if 0 <= i - 1 < len(low) else 0.0
            b = low[i] if i < len(low) else 0.0
            out[i] = p * (a - b)
    else:
        for i in range(p + 1):
            a = low[i - 2] if 0 <= i - 2 < len(low) else 0.0
            b = low[i - 1] if 0 <= i - 1 < len(low) else 0.0
            c = low[i] if i < len(low) else 0.0
            out[i] = p * (p - 1) * (a - 2.0 * b + c)
    return out


def npatch_frame(bpts):
    """Plane of a hole boundary (Newell normal) with its main direction
    (PCA in the plane): (centre, ex, ey, N)."""
    n = len(bpts)
    c = tuple(sum(q[i] for q in bpts) / n for i in range(3))
    N = [0.0, 0.0, 0.0]
    for i in range(n):
        a = bpts[i]; b = bpts[(i + 1) % n]
        N[0] += (a[1] - b[1]) * (a[2] + b[2]); N[1] += (a[2] - b[2]) * (a[0] + b[0]); N[2] += (a[0] - b[0]) * (a[1] + b[1])
    N = _unit(tuple(N))
    e0 = _unit(_sub(bpts[0], c)); e1 = _cross(N, e0)
    sxx = syy = sxy = 0.0
    for q in bpts:
        d = _sub(q, c); x = _dot(d, e0); y = _dot(d, e1)
        sxx += x * x; syy += y * y; sxy += x * y
    ang = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    ex = _unit(_add(_mul(e0, math.cos(ang)), _mul(e1, math.sin(ang))))
    ey = _cross(N, ex)
    return c, ex, ey, N


def fit_npatch(bpts, bnorm, inner=(), deg=5, margin=0.01, w_tan=3.0, w_fair=None, w_in=0.3, frame=None,
               w_net=10.0):
    """Own N-sided patch: ONE clean span (deg x deg, (deg+1)^2 CVs) over the
    hole, its CV grid lined up with the hole's main direction and only a
    little bigger than the hole. Least squares: through the boundary points
    bpts, tangent to the neighbours (S_u . n = S_v . n = 0 with their normals
    bnorm), through the mesh points inner, and as little bending as possible
    (thin plate) - that keeps the CV net even. -> (SPatch, (c, ex, ey, N, x0, x1, y0, y1))."""
    c, ex, ey, N = frame or npatch_frame(bpts)
    xs = [_dot(_sub(q, c), ex) for q in bpts]; ys = [_dot(_sub(q, c), ey) for q in bpts]
    x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
    mx = margin * (x1 - x0); my = margin * (y1 - y0)
    x0 -= mx; x1 += mx; y0 -= my; y1 += my
    W = x1 - x0; H = y1 - y0
    L = max(W, H)
    if w_fair is None:
        w_fair = 2e-4
    def uv(q):
        d = _sub(q, c)
        return ((_dot(d, ex) - x0) / W, (_dot(d, ey) - y0) / H)
    n1 = deg + 1; ncv = n1 * n1; nu = 3 * ncv
    A = [[0.0] * nu for _ in range(nu)]; B = [0.0] * nu
    def add_vec(coef, target, w):            # sum coef_ab P_ab = target (all 3 coordinates)
        idx = [(i, v) for i, v in enumerate(coef) if v != 0.0]
        for q in range(3):
            for (i, vi) in idx:
                r = 3 * i + q
                B[r] += w * vi * target[q]
                row = A[r]
                for (j, vj) in idx:
                    row[3 * j + q] += w * vi * vj
    def add_dot(coef, nrm, w):               # sum coef_ab (P_ab . n) = 0
        ent = []
        for i, v in enumerate(coef):
            if v == 0.0: continue
            for q in range(3):
                if nrm[q] != 0.0: ent.append((3 * i + q, v * nrm[q]))
        for (r, vr) in ent:
            row = A[r]
            for (s_, vs) in ent:
                row[s_] += w * vr * vs
    def basis(u, v, du, dv):
        bu = _bern_d(deg, u, du); bv = _bern_d(deg, v, dv)
        return [bu[a] * bv[b] for a in range(n1) for b in range(n1)]
    wb = 1.0 / max(1, len(bpts))
    for q, nrm in zip(bpts, bnorm):
        u, v = uv(q)
        add_vec(basis(u, v, 0, 0), q, 1e3 * wb)
        if nrm is not None:
            add_dot(basis(u, v, 1, 0), nrm, w_tan * 1e3 * wb * (L / W) ** 2)
            add_dot(basis(u, v, 0, 1), nrm, w_tan * 1e3 * wb * (L / H) ** 2)
    wi = w_in / max(1, len(inner))
    for q in inner:
        u, v = uv(q)
        add_vec(basis(u, v, 0, 0), q, 1e3 * wi)
    ng = 12; z = (0.0, 0.0, 0.0)
    for a in range(ng):
        for b in range(ng):
            u = (a + 0.5) / ng; v = (b + 0.5) / ng
            add_vec(basis(u, v, 2, 0), z, w_fair * (L / W) ** 4)
            add_vec(basis(u, v, 1, 1), z, 2.0 * w_fair * (L / W) ** 2 * (L / H) ** 2)
            add_vec(basis(u, v, 0, 2), z, w_fair * (L / H) ** 4)
    if w_net > 0.0:                          # an even CV net: no zigzag along the rows
        for a in range(n1):
            for b in range(n1):
                for (da, db) in ((1, 0), (0, 1)):
                    if 0 < a < deg if da else 0 < b < deg:
                        coef = [0.0] * ncv
                        coef[(a - da) * n1 + (b - db)] = 1.0; coef[a * n1 + b] = -2.0
                        coef[(a + da) * n1 + (b + db)] = 1.0
                        add_vec(coef, z, w_net)
    for i in range(nu):
        A[i][i] += 1e-12
    X = gauss_solve(A, [[b] for b in B])
    P = [(X[3 * i][0], X[3 * i + 1][0], X[3 * i + 2][0]) for i in range(ncv)]
    return SPatch("_npatch", deg, deg, P), (c, ex, ey, N, x0, x1, y0, y1)


