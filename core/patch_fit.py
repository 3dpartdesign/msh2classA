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
from .vectors import v_add, v_dist, v_dot, v_mul, v_sub
from .evaluate import srf_coefs, srf_derivs, srf_point
from .lsq import LSQ
from .bspline import BDir
# <<< dev-only
# =============================================================================
# PATCH FITTING WITH G0 / G1 / G2 CONSTRAINTS
# =============================================================================
# Side k of the quad (corners c0..c3):
#   side 0: c0->c1  (u=t,   v=0)   PINK     cross derivative into patch: +Sv
#   side 1: c1->c2  (u=1,   v=t)   BLUE                                  -Su
#   side 2: c2->c3  (u=1-t, v=1)   PINK                                   -Sv
#   side 3: c3->c0  (u=0,   v=1-t) BLUE                                  +Su
SIDE_MAP = [
    (lambda t: (t, 0.0), 1, +1.0),
    (lambda t: (1.0, t), 0, -1.0),
    (lambda t: (1.0 - t, 1.0), 1, -1.0),
    (lambda t: (0.0, 1.0 - t), 0, +1.0),
]

# priority: G0 >> G1 >> G2 >> scan data
W_G0 = 1e10
W_G1 = 1e7
W_G2 = 1e7

def coons_point(fs, u, v):
    """Bilinearly blended Coons patch from 4 side functions (own orientation)."""
    b = fs[0](u); r = fs[1](v); t = fs[2](1.0 - u); l = fs[3](1.0 - v)
    c00 = fs[0](0.0); c10 = fs[0](1.0); c11 = fs[1](1.0); c01 = fs[2](1.0)
    x = []
    for k in range(3):
        x.append((1 - v) * b[k] + v * t[k] + (1 - u) * l[k] + u * r[k]
                 - ((1 - u) * (1 - v) * c00[k] + u * (1 - v) * c10[k]
                    + u * v * c11[k] + (1 - u) * v * c01[k]))
    return tuple(x)

def fit_to_function(du, dv, fn):
    nsu = 3 * du.n + 4; nsv = 3 * dv.n + 4
    L = LSQ(du.n * dv.n)
    for i in range(nsu):
        u = i / float(nsu - 1)
        for j in range(nsv):
            v = j / float(nsv - 1)
            L.add(srf_coefs(du, dv, u, v, 0, 0), fn(u, v), 1.0)
    for i in range(du.n * dv.n):
        L.add([(i, 1.0)], (0.0, 0.0, 0.0), 1e-12)
    return L.solve()[0]


def rebuild_fit(fn, nu=6, nv=6, edge_w=1e3, n_dev=25):
    """Rebuild: a clean single-span patch of nu x nv CV on the surface fn(u, v)
    (u, v 0..1 - e.g. a 20x20 CV, multi-span or rational surface): its 4
    corners exactly, its 4 edges closely (so the neighbours' seams stay), the
    inside by least squares, the CV net faired a little.
    -> (P, deviation inside, deviation on the edges) - at the same (u, v)."""
    du = BDir(nu - 1, 1); dv = BDir(nv - 1, 1)
    prob = FitProblem(du, dv, fit_to_function(du, dv, fn))
    for (u, v) in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
        prob.hard.append((u, v, fn(u, v)))
    K = 4 * max(nu, nv) + 4
    for k in range(4):
        uvf = SIDE_MAP[k][0]
        for i in range(K):
            u, v = uvf(i / float(K - 1))
            prob.soft.append((u, v, fn(u, v), edge_w))
    ns_ = 4 * max(nu, nv) + 6
    data = [(i / (ns_ - 1.0), j / (ns_ - 1.0)) for i in range(ns_) for j in range(ns_)]
    data = [(u, v, fn(u, v), 1.0) for (u, v) in data]
    P = run_fit(prob, lambda P_: data, 2)
    dev = 0.0; dev_e = 0.0
    for i in range(n_dev):
        for j in range(n_dev):
            u, v = i / (n_dev - 1.0), j / (n_dev - 1.0)
            d = v_dist(srf_point(du, dv, P, u, v), fn(u, v))
            dev = max(dev, d)
            if i in (0, n_dev - 1) or j in (0, n_dev - 1):
                dev_e = max(dev_e, d)
    return P, dev, dev_e


class Cont(object):
    """Continuity constraint along one side (G1 or G2)."""
    def __init__(self, side, level, samples, a_init):
        self.side = side
        self.level = level          # 1 or 2
        # samples: (u, v, axis, sign, D1n, D2n[, E1, E2, TWn, t])
        #   D1n/D2n: neighbour 1st/2nd derivative across the edge (outward)
        #   E1/E2  : neighbour 1st/2nd derivative ALONG the edge (per our t)
        #   TWn    : d(D1n)/dt (twist).  With E1.. given, the cross tangent may
        #            also lean along the edge (shear) - still exact G1/G2, and the
        #            free boundaries may meet the neighbour's at an angle (top view).
        self.samples = samples
        self.shear = len(samples[0]) >= 10 if samples else False
        self.a_init = max(a_init, 1e-9)
        self.a = self.a_init
        self.b = 0.0
        self.beta = [0.0, 0.0]
        self.gam = [0.0, 0.0]
        self.a_fixed = False
        self.flow = [False, False]  # end t=0/1: no shear -> outline continues the neighbour's


class FitProblem(object):
    def __init__(self, du, dv, P0):
        self.du = du; self.dv = dv; self.P0 = P0
        self.hard = []      # (u, v, target)
        self.soft = []      # (u, v, target, w)
        self.conts = []
        self.mirrors = []   # (axis, value, row0 CV indices, row1 CV indices or None)
        self.w_reg = 1e-4
        self.w_fair = None       # CV-net fairing weight (None = automatic)
        self.reserved = []       # (side k, rows) kept for a FUTURE neighbour
        self.anchor = {}         # CV index -> target (pull toward the free fit)
        self.anchor_rel = 0.0
        self.extra_fixed = {}


def add_fairing(L, nu, nv, wf):
    """CV-net fairness. 3rd differences along rows/columns: a CV jumping out
    of its row is penalised hard, an even arc (constant curvature) is not.
    Plus a tiny 2nd-difference term for 3-CV rows and holes in the data."""
    idx = lambda i, j: i * nv + j
    Z = (0.0, 0.0, 0.0)
    for i in range(nu):
        for j in range(nv):
            if i + 4 < nu:
                L.add([(idx(i, j), 1.0), (idx(i + 1, j), -4.0), (idx(i + 2, j), 6.0),
                       (idx(i + 3, j), -4.0), (idx(i + 4, j), 1.0)], Z, wf)
            elif i + 3 < nu and nu == 4:
                L.add([(idx(i, j), -1.0), (idx(i + 1, j), 3.0), (idx(i + 2, j), -3.0), (idx(i + 3, j), 1.0)], Z, wf)
            if j + 4 < nv:
                L.add([(idx(i, j), 1.0), (idx(i, j + 1), -4.0), (idx(i, j + 2), 6.0),
                       (idx(i, j + 3), -4.0), (idx(i, j + 4), 1.0)], Z, wf)
            elif j + 3 < nv and nv == 4:
                L.add([(idx(i, j), -1.0), (idx(i, j + 1), 3.0), (idx(i, j + 2), -3.0), (idx(i, j + 3), 1.0)], Z, wf)
            if 0 < i < nu - 1:
                L.add([(idx(i - 1, j), 1.0), (idx(i, j), -2.0), (idx(i + 1, j), 1.0)], Z, wf * 0.05)
            if 0 < j < nv - 1:
                L.add([(idx(i, j - 1), 1.0), (idx(i, j), -2.0), (idx(i, j + 1), 1.0)], Z, wf * 0.05)


def fit_smooth(prob, data_fn, iters, log, smooth_tol=0.0):
    """Plain fit (kept as the single entry point for the A/C fit steps)."""
    return run_fit_joint(prob, data_fn, iters, log)


def side_row_indices(du, dv, k, r):
    """CV indices of row r (0 = boundary) counted from side k."""
    nu, nv = du.n, dv.n
    out = []
    for x in range(nu if k % 2 == 0 else nv):
        if k == 0: i, j = x, r
        elif k == 2: i, j = x, nv - 1 - r
        elif k == 1: i, j = nu - 1 - r, x
        else: i, j = r, x
        out.append(i * nv + j)
    return out


def _clone(prob, P0=None, conts=None, mirrors=None, soft=None, w_reg=None, extra_fixed=None):
    q = FitProblem(prob.du, prob.dv, list(P0 if P0 is not None else prob.P0))
    q.hard = prob.hard
    q.soft = prob.soft if soft is None else soft
    q.conts = prob.conts if conts is None else conts
    q.mirrors = prob.mirrors if mirrors is None else mirrors
    q.w_reg = prob.w_reg if w_reg is None else w_reg
    q.extra_fixed = extra_fixed or {}
    q.w_fair = prob.w_fair
    q.reserved = prob.reserved
    return q


def run_fit(prob, data_fn, iters=8, log=None, smooth_tol=0.0):
    """Patch fit in three steps (like fit -> MatchSrf -> refit):
      A  fit the whole patch to the mesh as if there were no neighbours
      B  match: satisfy G1/G2 (and mirror) moving the CVs as LITTLE as
         possible away from A  (no mesh here -> no wild CVs)
      C  refit the rows that do not belong to any continuity to the mesh,
         continuity rows locked (continuity stays exact)."""
    if not prob.conts and not prob.mirrors:
        return fit_smooth(prob, data_fn, iters, log, smooth_tol)
    PA = fit_smooth(_clone(prob, conts=[], mirrors=[]), data_fn, iters, log, smooth_tol)
    PB = run_fit_joint(_clone(prob, P0=PA, w_reg=1.0), lambda P_: [], max(iters, 6), log)
    du, dv = prob.du, prob.dv
    locked = set()
    for c in prob.conts:
        for r in range(c.level + 1):
            locked.update(side_row_indices(du, dv, c.side, r))
    for (axis, val, row0, row1, row2) in prob.mirrors:
        locked.update(row0)
        if row1: locked.update(row1)
        if row2: locked.update(row2)
    fixed = dict((i, PB[i]) for i in locked)
    qc = _clone(prob, P0=PB, conts=[], mirrors=[], extra_fixed=fixed)
    # rows reserved for a FUTURE neighbour stay close to the free fit A, so the
    # edge handed to the next patch carries the scan's curvature, not the
    # curvature transition forced by this patch's own neighbour
    for (k, nrows) in prob.reserved:
        for r in range(nrows):
            for i in side_row_indices(du, dv, k, r):
                if i not in locked:
                    qc.anchor[i] = PA[i]
    qc.anchor_rel = 0.5
    return fit_smooth(qc, data_fn, iters, log, smooth_tol)


def run_fit_joint(prob, data_fn, iters=8, log=None):
    du, dv = prob.du, prob.dv
    ncv = du.n * dv.n
    P = list(prob.P0)
    size = max(1e-9, max(v_dist(prob.P0[0], q) for q in prob.P0))
    # Stage 1: G0 has absolute priority. Boundary CVs touched by G0
    # constraints are solved from the constraints alone and then frozen.
    fixed = {}
    if prob.hard:
        L0 = LSQ(ncv)
        touched = set()
        for (u, v, t) in prob.hard:
            cf = srf_coefs(du, dv, u, v, 0, 0)
            L0.add(cf, t, 1.0)
            for (i, c) in cf:
                if abs(c) > 1e-12: touched.add(i)
        for i in range(ncv):
            L0.add([(i, 1.0)], prob.P0[i], 1e-9)
        sol = L0.solve()[0]
        for i in touched:
            fixed[i] = sol[i]
        for i in fixed:
            P[i] = fixed[i]
    for i, q in getattr(prob, "extra_fixed", {}).items():
        fixed[i] = q; P[i] = q
    for it in range(iters):
        data = data_fn(P)
        ns = 0
        for c in prob.conts:
            c.ia = None
            if not c.a_fixed:
                c.ia = ns; ns += 1
            c.ibeta = None
            if c.shear:
                c.ibeta = ns; ns += 2
            c.ib = c.igam = None
            if c.level >= 2:
                c.ib = ns; ns += 1
                if c.shear:
                    c.igam = ns; ns += 2
        if prob.mirrors and ns == 0:
            ns = 1                              # need coupled system (per-axis eqs)
        L = LSQ(ncv, ns, fixed)
        if ns and not prob.conts:
            L.add_scalar_reg(0, 0.0, 1.0)
        # symmetry plane = an invisible mirrored copy of this patch, matched
        # like MatchSrf with 'average': G0 row0 ON the plane, G1 row1
        # straight across it, G2 row2 at twice row1's distance from the plane
        for (axis, val, row0, row1, row2) in prob.mirrors:
            for i0 in row0:
                L.add_axis([(i0, 1.0)], axis, val, W_G0)
            if row1:
                for i0, i1 in zip(row0, row1):
                    for c in range(3):
                        if c != axis:
                            L.add_axis([(i1, 1.0), (i0, -1.0)], c, 0.0, W_G1)
            if row2:
                for i0, i1, i2 in zip(row0, row1, row2):
                    L.add_axis([(i2, 1.0), (i1, -2.0), (i0, 1.0)], axis, 0.0, W_G2)
        for (u, v, t, w) in data:
            L.add(srf_coefs(du, dv, u, v, 0, 0), t, w)
        for (u, v, t, w) in prob.soft:
            L.add(srf_coefs(du, dv, u, v, 0, 0), t, w)
        for c in prob.conts:
            for smp in c.samples:
                u, v, axis, sign, D1, D2 = smp[:6]
                ku, kv = (1, 0) if axis == 0 else (0, 1)
                cf = [(i, sign * x) for (i, x) in srf_coefs(du, dv, u, v, ku, kv)]
                # G1:  S_cross = a*D1 + beta(t)*E1
                scal = []; rhs = (0.0, 0.0, 0.0)
                if c.ia is None: rhs = v_mul(D1, c.a)
                else: scal.append((c.ia, v_mul(D1, -1.0)))
                if c.shear:
                    E1, E2, TW, tt = smp[6:10]
                    scal.append((c.ibeta, v_mul(E1, -(1.0 - tt))))
                    scal.append((c.ibeta + 1, v_mul(E1, -tt)))
                if scal: L.add_coupled(cf, scal, rhs, W_G1)
                else: L.add(cf, rhs, W_G1)
                if c.level >= 2:
                    # G2:  S_cc = a^2 D2 + 2 a beta TW + beta^2 E2 + b D1 + gamma(t) E1
                    ku, kv = (2, 0) if axis == 0 else (0, 2)
                    cf2 = srf_coefs(du, dv, u, v, ku, kv)
                    rhs2 = v_mul(D2, c.a * c.a)
                    scal2 = [(c.ib, v_mul(D1, -1.0))]
                    if c.shear:
                        bt = (1.0 - tt) * c.beta[0] + tt * c.beta[1]
                        rhs2 = v_add(rhs2, v_add(v_mul(TW, 2.0 * c.a * bt), v_mul(E2, bt * bt)))
                        scal2.append((c.igam, v_mul(E1, -(1.0 - tt))))
                        scal2.append((c.igam + 1, v_mul(E1, -tt)))
                    L.add_coupled(cf2, scal2, rhs2, W_G2)
            if c.ia is not None:
                L.add_scalar_reg(c.ia, c.a, 1e-6)
            fl = getattr(c, "flow", [False, False])
            if c.ibeta is not None:
                for e_ in (0, 1):
                    L.add_scalar_reg(c.ibeta + e_, 0.0, W_G1 if fl[e_] else 1e-6)
            if c.ib is not None:
                L.add_scalar_reg(c.ib, c.b, 1e-8)
            if c.igam is not None:
                for e_ in (0, 1):
                    L.add_scalar_reg(c.igam + e_, 0.0, W_G2 if fl[e_] else 1e-8)
        for i in range(ncv):
            L.add([(i, 1.0)], prob.P0[i], prob.w_reg)
        if prob.anchor and data:
            wa = prob.anchor_rel * sum(w for (_, _, _, w) in data) / ncv
            for i, q in prob.anchor.items():
                L.add([(i, 1.0)], q, wa)
        # Fairing: keep the CV net smooth (2nd differences small). Holds CVs
        # where the scan gives no data (holes, ignored walls) and stops the
        # wild CVs of degree-5 patches. Weak compared with the data.
        wf = prob.w_fair
        if wf is None:
            wf = 0.0005 * max(1.0, sum(w for (_, _, _, w) in data)) / ncv if data else 0.0
        wf *= getattr(prob, "fair_mult", 1.0)
        if wf > 0:
            add_fairing(L, du.n, dv.n, wf)
        Pn, scal = L.solve()
        a_change = 0.0
        for c in prob.conts:
            if c.ia is not None:
                an = scal[c.ia]
                if an < 0.05 * c.a_init:
                    if log: log("    [WARN] side {}: tangent flipped by mesh -> fixed magnitude".format(c.side + 1))
                    c.a_fixed = True; an = c.a_init
                a_change = max(a_change, abs(an - c.a) / max(c.a, 1e-12))
                c.a = an
            if c.ibeta is not None:
                nb = [scal[c.ibeta], scal[c.ibeta + 1]]
                a_change = max(a_change, max(abs(x - y) for x, y in zip(nb, c.beta)))
                c.beta = nb
            if c.ib is not None:
                c.b = scal[c.ib]
            if c.igam is not None:
                c.gam = [scal[c.igam], scal[c.igam + 1]]
        move = max(v_dist(p, q) for p, q in zip(P, Pn))
        P = Pn
        if it >= 2 and move < 1e-5 * size and a_change < 1e-3:
            break
    return P


def project_param(du, dv, P, p, guess=None):
    """Closest (u,v) on surface to point p (grid seed + Newton)."""
    if guess is None:
        best = None
        for i in range(13):
            for j in range(13):
                u = i / 12.0; v = j / 12.0
                d = v_dist(srf_point(du, dv, P, u, v), p)
                if best is None or d < best[0]:
                    best = (d, u, v)
        u, v = best[1], best[2]
    else:
        u, v = guess
    for _ in range(10):
        D = srf_derivs(du, dv, P, u, v, 2)
        r = v_sub(D[(0, 0)], p)
        Su, Sv = D[(1, 0)], D[(0, 1)]
        f = v_dot(r, Su); g = v_dot(r, Sv)
        a11 = v_dot(Su, Su) + v_dot(r, D[(2, 0)])
        a12 = v_dot(Su, Sv) + v_dot(r, D[(1, 1)])
        a22 = v_dot(Sv, Sv) + v_dot(r, D[(0, 2)])
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-300: break
        du_ = (-f * a22 + g * a12) / det
        dv_ = (-g * a11 + f * a12) / det
        u = min(1.0, max(0.0, u + du_)); v = min(1.0, max(0.0, v + dv_))
        if abs(du_) + abs(dv_) < 1e-10: break
    return u, v


def normal_curv_neighbour(dirv, D1, D2, E1, E2, TW, n):
    """Neighbour normal curvature in direction dirv (tangent plane), from its
    derivatives across (D1, D2), along (E1, E2) and twist TW at the edge."""
    a11 = v_dot(E1, E1); a12 = v_dot(E1, D1); a22 = v_dot(D1, D1)
    r1 = v_dot(dirv, E1); r2 = v_dot(dirv, D1)
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-300:
        return v_dot(D2, n) / max(a22, 1e-300)
    al = (r1 * a22 - r2 * a12) / det; be = (r2 * a11 - r1 * a12) / det
    II = al * al * v_dot(E2, n) + 2 * al * be * v_dot(TW, n) + be * be * v_dot(D2, n)
    return II / max(v_dot(dirv, dirv), 1e-300)


LEVEL_NAME = {0: "G0", 1: "G1", 2: "G2"}
# side choices in the bubble of a new patch: 3 / 4 = laid out like G2 / G1
# (the rows are there) but only the edge row is matched to the neighbour -
# the seam IS G0 (stored and judged as G0); MatchUVN can raise it later
SIDE_LV_NAME = {0: "G0", 1: "G1", 2: "G2", 3: "G2>G0", 4: "G1>G0"}

MIRROR_AXIS = {"YZ": 0, "XZ": 1, "XY": 2}      # plane name -> its normal axis (plane through 0)


