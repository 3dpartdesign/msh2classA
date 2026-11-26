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
from .vectors import fl, percentile, v_add, v_cross, v_dist, v_dot, v_len, v_lerp, v_mul, v_sub, v_unit
from .bspline import BDir
from .evaluate import crv_point, srf_coefs, srf_point
from .lsq import LSQ
# <<< dev-only
# =============================================================================
# CURVE FIT + AUTO SPAN ANALYSIS
# =============================================================================

def chord_params(pts):
    d = [0.0]
    for i in range(1, len(pts)):
        d.append(d[-1] + v_dist(pts[i], pts[i - 1]))
    tot = d[-1] if d[-1] > 0 else 1.0
    return [x / tot for x in d]

def fit_curve(pts, params, d):
    """LSQ B-spline curve through pts (ends fixed)."""
    L = LSQ(d.n)
    for p, t in zip(pts, params):
        f, N = d.basis(t, 0)
        L.add([(f + a, N[0][a]) for a in range(d.p + 1)], p, 1.0)
    W = 1e6
    L.add([(0, 1.0)], pts[0], W)
    L.add([(d.n - 1, 1.0)], pts[-1], W)
    g = d.greville()
    for i in range(d.n):            # tiny pull to chord -> never singular
        L.add([(i, 1.0)], v_lerp(pts[0], pts[-1], g[i]), 1e-6)
    return L.solve()[0]

def dist_points_to_polyline(pts, poly):
    out = []
    for p in pts:
        best = 1e300
        for k in range(len(poly) - 1):
            a = poly[k]; b = poly[k + 1]
            ab = v_sub(b, a); l2 = v_dot(ab, ab)
            s = 0.0 if l2 < 1e-300 else max(0.0, min(1.0, v_dot(v_sub(p, a), ab) / l2))
            dd = v_dist(p, v_add(a, v_mul(ab, s)))
            if dd < best: best = dd
        out.append(best)
    return out

def _fit_dev(pts, degree, spans, q=0.9):
    """Fit `spans`-span curve of `degree` -> (q-percentile dist, max dist, CVs)."""
    d = BDir(degree, spans)
    params = chord_params(pts)
    C = fit_curve(pts, params, d)
    # one parameter-correction pass
    poly_t = [i / 200.0 for i in range(201)]
    poly = [crv_point(d, C, t) for t in poly_t]
    newp = []; prev = 0; npl = len(poly)
    for p in pts:                    # points go along the curve: search near the last one
        lo = max(0, prev - 15); hi = min(npl, prev + 60)
        bi = min(range(lo, hi), key=lambda k: v_dist(p, poly[k]))
        if bi == hi - 1 and hi < npl:
            bi = min(range(npl), key=lambda k: v_dist(p, poly[k]))
        newp.append(poly_t[bi]); prev = bi
    newp[0] = 0.0; newp[-1] = 1.0
    C = fit_curve(pts, newp, d)
    poly = [crv_point(d, C, t) for t in poly_t]
    devs = []
    for p, tp in zip(pts, newp):     # nearest segment: near the point's own parameter
        k0 = int(round(tp * 200)); best = 1e300
        for k in range(max(0, k0 - 12), min(200, k0 + 12)):
            a_ = poly[k]; ab = v_sub(poly[k + 1], a_); l2 = v_dot(ab, ab)
            s = 0.0 if l2 < 1e-300 else max(0.0, min(1.0, v_dot(v_sub(p, a_), ab) / l2))
            dd = v_dist(p, v_add(a_, v_mul(ab, s)))
            if dd < best: best = dd
        devs.append(best)
    return percentile(devs, q), max(devs), C

def curve_fit_deviation(pts, degree, spans, q=0.9):
    """Fit `spans`-span curve of `degree`, return (q-percentile, max) distance."""
    r = _fit_dev(pts, degree, spans, q)
    return r[0], r[1]

# -----------------------------------------------------------------------------
# "Can ONE single-span patch do this?"  Scale-free: the same shape gives the
# same answer at any size.  Three things must hold, each is a ratio (<= 1 ok):
#   dev   : 90% of the scan within TolPct % of the length (above scan noise)
#   turn  : the scan turns at most SPAN_TURN degrees (4CV 100, 6CV 130) - more
#           and the CV net wraps round and the patch parametrisation goes bad
#           even when the deviation still looks fine
#   fair  : the fitted curve has no inflection the scan does not have (a 6CV
#           span over a flat->fillet change waves even when it is "close")
# -----------------------------------------------------------------------------

SPAN_TURN = {1: 5.0, 2: 50.0, 3: 100.0, 4: 115.0, 5: 130.0}
GREEN_AT = 0.6          # score <= 0.6 green, <= 1 yellow, else red

def poly_length(pts):
    return sum(v_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

def _angle(a, b):
    la = v_len(a); lb = v_len(b)
    if la < 1e-300 or lb < 1e-300: return 0.0
    c = max(-1.0, min(1.0, v_dot(a, b) / (la * lb)))
    return math.degrees(math.acos(c))

def resample(pts, n):
    """n+1 points evenly spaced along the polyline."""
    cum = [0.0]
    for i in range(len(pts) - 1): cum.append(cum[-1] + v_dist(pts[i], pts[i + 1]))
    L = cum[-1]; out = []; k = 0
    for i in range(n + 1):
        s = L * i / float(n)
        while k < len(pts) - 2 and cum[k + 1] < s: k += 1
        seg = cum[k + 1] - cum[k]
        out.append(v_lerp(pts[k], pts[k + 1], 0.0 if seg < 1e-300 else (s - cum[k]) / seg))
    return out

def _crv_frames(d, C, n=41):
    """Tangents and curvature vectors (C'xC'' / |C'|^3, binormal-scaled)."""
    T = []; K = []
    for i in range(n):
        t = i / (n - 1.0)
        f, N = d.basis(t, 2)
        c1 = (0.0, 0.0, 0.0); c2 = (0.0, 0.0, 0.0)
        for a in range(d.p + 1):
            c1 = v_add(c1, v_mul(C[f + a], N[1][a])); c2 = v_add(c2, v_mul(C[f + a], N[2][a]))
        l = v_len(c1)
        T.append(c1); K.append(v_mul(v_cross(c1, c2), 1.0 / max(l * l * l, 1e-300)))
    return T, K

def profile_turn(pts, nseg=10):
    """Total tangent turning in degrees, of a smoothed (3-span cubic) copy of
    the profile, so mesh noise does not add up."""
    d = BDir(3, 3); C = fit_curve(pts, chord_params(pts), d)
    T, _ = _crv_frames(d, C)
    return sum(_angle(T[i], T[i + 1]) for i in range(len(T) - 1))

def _inflections(K, ref):
    """Sign changes of curvature (seen along ref), ignoring |k| < 10% of max."""
    mx = max(v_len(k) for k in K)
    if mx < 1e-300: return 0
    sg = [1 if v_dot(k, ref) > 0 else -1 for k in K if v_len(k) > 0.1 * mx]
    return sum(1 for i in range(len(sg) - 1) if sg[i] != sg[i + 1])

def scan_noise(pts):
    """Scatter of the mesh along the profile: what a flexible (6-span) curve
    still cannot follow.  Used only as a floor so noise never turns it red."""
    if len(pts) < 16: return 0.0
    return curve_fit_deviation(pts, 3, 6)[0]

def span_score(pts, degree, tol_pct, noise=None):
    """-> (score, reason). score <= 1: one span of this degree follows pts."""
    L = poly_length(pts)
    if L < 1e-12: return 0.0, ""
    if noise is None: noise = scan_noise(pts)
    tol = max(tol_pct / 100.0 * L, 2.0 * noise, 1e-9)
    p90, _, C = _fit_dev(pts, degree, 1)
    dr = BDir(3, 3); Cr = fit_curve(pts, chord_params(pts), dr)
    Tr, Kr = _crv_frames(dr, Cr)
    turn = sum(_angle(Tr[i], Tr[i + 1]) for i in range(len(Tr) - 1))
    lim = SPAN_TURN.get(degree, 130.0)
    ref = (0.0, 0.0, 0.0)
    for k in Kr: ref = v_add(ref, k)
    if v_len(ref) < 1e-300: ref = (0.0, 0.0, 1.0)
    # waves only mean something on a clearly bent, PLANE profile: on a near-
    # flat one the sign of a tiny curvature is just scan facets, and on a
    # 3D (twisted) one the curvature turns round instead of changing sign
    extra = 0
    if turn >= 10.0:
        u_ = v_unit(ref); kmax = max(v_len(k) for k in Kr)
        plane = all(abs(v_dot(k, u_)) >= 0.9 * v_len(k) for k in Kr if v_len(k) > 0.1 * kmax)
        if plane:
            _, Kf = _crv_frames(BDir(degree, 1), C)
            extra = _inflections(Kf, ref) - _inflections(Kr, ref)
    parts = [(p90 / tol, "too complex"),
             (turn / lim, "bends {:.0f} deg".format(turn))]
    if extra > 0 and degree > 3:
        parts.append((9.9, "waves"))
    s, why = max(parts, key=lambda x: x[0])
    return s, why

def single_span_reach(pts, degree, tol_pct):
    """Profile pts (along a side, on the mesh) -> (score, reason, fraction of
    the profile ONE single-span curve still handles)."""
    noise = scan_noise(pts)
    s, why = span_score(pts, degree, tol_pct, noise)
    if s <= 1.0:
        return s, why, 1.0
    lo, hi = 0.0, 1.0
    cum = chord_params(pts)
    for _ in range(5):
        f = 0.5 * (lo + hi)
        sub = [q for q, c in zip(pts, cum) if c <= f]
        if len(sub) < degree + 3:
            lo = f; continue
        if span_score(sub, degree, tol_pct, noise)[0] <= 1.0: lo = f
        else: hi = f
    return s, why, lo

def patch_score(grid, deg_u, deg_v, tol_pct):
    """grid[i][j]: mesh points over the whole future patch (i along u).
    -> (score, reason) for ONE deg_u x deg_v span: surface deviation relative
    to the patch size, and turning of every iso line in both directions."""
    nu = len(grid); nv = len(grid[0])
    du = BDir(deg_u, 1); dv = BDir(deg_v, 1)
    L = LSQ(du.n * dv.n)
    for i in range(nu):
        for j in range(nv):
            L.add(srf_coefs(du, dv, i / (nu - 1.0), j / (nv - 1.0), 0, 0), grid[i][j], 1.0)
    for i in range(du.n * dv.n):
        L.add([(i, 1.0)], grid[0][0], 1e-9)
    P = L.solve()[0]
    devs = [v_dist(srf_point(du, dv, P, i / (nu - 1.0), j / (nv - 1.0)), grid[i][j])
            for i in range(nu) for j in range(nv)]
    rows_u = [[grid[i][j] for i in range(nu)] for j in range(nv)]
    rows_v = [list(grid[i]) for i in range(nu)]
    size = max(max(poly_length(r) for r in rows_u), max(poly_length(r) for r in rows_v))
    noise = max(scan_noise(resample(r, 20)) for r in (rows_u[nv // 2], rows_v[nu // 2]))
    tol = max(tol_pct / 100.0 * size, 2.0 * noise, 1e-9)
    p90 = percentile(devs, 0.9)
    tu = max(profile_turn(r) for r in rows_u); tv = max(profile_turn(r) for r in rows_v)
    lu = SPAN_TURN.get(deg_u, 130.0); lv = SPAN_TURN.get(deg_v, 130.0)
    parts = [(p90 / tol, "too complex"),
             (tu / lu, "bends {:.0f} deg".format(tu)),
             (tv / lv, "bends {:.0f} deg".format(tv))]
    return max(parts, key=lambda x: x[0])

def hint_state(score):
    return "green" if score <= GREEN_AT else ("yellow" if score <= 1.0 else "red")

def auto_spans(lines, degree, tol, max_spans=10, log=None):
    """Smallest span count for which every line fits within tol (P90) and no
    span has to turn more than SPAN_TURN (same limit as SideLenHint)."""
    turn = max(profile_turn(pts) for pts in lines)
    n0 = max(1, int(math.ceil(turn / SPAN_TURN.get(degree, 130.0) - 1e-9)))
    if n0 > 1 and log:
        log("    mesh turns {:.0f} deg -> at least {} patch(es)".format(turn, n0))
    for n in range(min(n0, max_spans), max_spans + 1):
        worst = 0.0; worst_max = 0.0
        for pts in lines:
            p90, mx = curve_fit_deviation(pts, degree, n)
            worst = max(worst, p90); worst_max = max(worst_max, mx)
        ok = worst <= tol
        if log: log("    {} patch(es): P90 dev {}  (max {})  {}".format(
            n, fl(worst), fl(worst_max), "OK" if ok else ">" + fl(tol)))
        if ok:
            return n
    return max_spans

