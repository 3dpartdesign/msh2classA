# -*- coding: utf-8 -*-
# msh2classA 0.1.0-alpha   (the version is also in VERSION below and printed at start)
# Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign - https://3dPARTdesign.com
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, with the
# additional terms in the NOTICE file (GPL-3.0 section 7: the names
# "msh2classA" and "3dPARTdesign", and the author attribution).
# It is distributed WITHOUT ANY WARRANTY. See LICENSE and NOTICE.
"""
msh2classA - class-A single-span patches on a scan, patch after patch,
without leaving the command.
The scan (mesh or block) is loaded once and cached for the Rhino session.
The "Help" option prints the full description; the Legend button shows a short one.

File layout
  math core (no Rhino):  vectors, B-spline basis, least squares, curve fit,
                         Bezier split, patch fit with G0/G1/G2 + mirror,
                         three-step fit (scan -> match -> scan), smoothing
                         of a patch with its neighbours
  Rhino part:            scan loading, pick candidates (edges/curves),
                         sides, and the Session = SettingsMixin + ScanMixin
                         + PolygonMixin + DrawMixin + BuildMixin + SmoothMixin

Rhino 8 / 9, Windows and Mac (IronPython 2.7 or Python 3). Pure-Python
math, no numpy. Any model unit (mm, cm, m, inch, ...): defaults are given
in mm and converted; lengths in messages get the decimals the unit needs.
"""

from __future__ import print_function, division

VERSION = "msh2classA 0.1.0-alpha"
import json
import math
import os
import traceback
import time

MATCAP_FILE = "reflection_matcap_3dPARTdesign_com.png"     # next to this script: own EMap


SCAN_DISPLAY_FILE = "msh2classA_3dPARTdesign_com.ini"      # next to this script: the display mode for scan work
SCAN_DISPLAY_NAMES = ("3dPARTdesign_ScanCAD",)   # the display mode looked for


def matcap_path():
    """The matcap image next to the script, or None."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, MATCAP_FILE)
        return p if os.path.isfile(p) else None
    except Exception:
        return None

try:
    import Rhino
    import Rhino.Geometry as rg
    import Rhino.Input.Custom as ric
    import Rhino.DocObjects as ro
    import rhinoscriptsyntax as rs
    import scriptcontext as sc
    import System.Drawing as sd
    IN_RHINO = True
except ImportError:
    IN_RHINO = False

LEGAL_LINE = ("msh2classA (C) 2026 3dPARTdesign - https://3dPARTdesign.com - "
              "free software under GPL-3.0 (see LICENSE, NOTICE), WITHOUT ANY WARRANTY")

# =============================================================================
# SMALL VECTOR HELPERS (plain tuples, no Rhino)
# =============================================================================

def fl(x):
    """A length for messages, with as many decimals as the model unit needs
    (about 0.001 mm): mm 3, cm 4, inch 5, m 6. Works outside Rhino too."""
    try:
        if IN_RHINO:
            u = Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)   # mm per unit
            dec = max(2, min(8, int(math.ceil(3.0 + math.log10(max(u, 1e-9)) - 1e-9))))
        else:
            dec = 3
    except Exception:
        dec = 3
    return "{:.{}f}".format(x, dec)


def v_add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def v_sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def v_mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def v_dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def v_len(a): return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
def v_dist(a, b): return v_len(v_sub(a, b))

def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

def v_unit(a):
    l = v_len(a)
    return (0.0, 0.0, 0.0) if l < 1e-300 else (a[0] / l, a[1] / l, a[2] / l)

def v_lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)

def percentile(vals, q):
    if not vals: return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * q
    f = int(math.floor(k)); c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)

def median(vals):
    return percentile(vals, 0.5)

# =============================================================================
# B-SPLINE BASIS (Piegl & Tiller), clamped uniform knots on [0,1]
# =============================================================================

def clamped_knots(p, spans):
    inner = [float(i) / spans for i in range(1, spans)]
    return [0.0] * (p + 1) + inner + [1.0] * (p + 1)

def find_span(ncv, p, u, U):
    n = ncv - 1
    if u >= U[n + 1]: return n
    if u <= U[p]: return p
    lo, hi = p, n + 1
    mid = (lo + hi) // 2
    while u < U[mid] or u >= U[mid + 1]:
        if u < U[mid]: hi = mid
        else: lo = mid
        mid = (lo + hi) // 2
    return mid

def ders_basis(span, u, p, nd, U):
    """Basis function values and derivatives: ders[k][j], k<=nd, j<=p."""
    ndu = [[0.0] * (p + 1) for _ in range(p + 1)]
    ndu[0][0] = 1.0
    left = [0.0] * (p + 1); right = [0.0] * (p + 1)
    for j in range(1, p + 1):
        left[j] = u - U[span + 1 - j]
        right[j] = U[span + j] - u
        saved = 0.0
        for r in range(j):
            ndu[j][r] = right[r + 1] + left[j - r]
            temp = ndu[r][j - 1] / ndu[j][r]
            ndu[r][j] = saved + right[r + 1] * temp
            saved = left[j - r] * temp
        ndu[j][j] = saved
    ders = [[0.0] * (p + 1) for _ in range(nd + 1)]
    for j in range(p + 1):
        ders[0][j] = ndu[j][p]
    a = [[0.0] * (p + 1) for _ in range(2)]
    for r in range(p + 1):
        s1, s2 = 0, 1
        a[0][0] = 1.0
        for k in range(1, nd + 1):
            d = 0.0
            rk = r - k; pk = p - k
            if r >= k:
                a[s2][0] = a[s1][0] / ndu[pk + 1][rk]
                d = a[s2][0] * ndu[rk][pk]
            j1 = 1 if rk >= -1 else -rk
            j2 = k - 1 if (r - 1) <= pk else p - r
            for j in range(j1, j2 + 1):
                a[s2][j] = (a[s1][j] - a[s1][j - 1]) / ndu[pk + 1][rk + j]
                d += a[s2][j] * ndu[rk + j][pk]
            if r <= pk:
                a[s2][k] = -a[s1][k - 1] / ndu[pk + 1][r]
                d += a[s2][k] * ndu[r][pk]
            ders[k][r] = d
            s1, s2 = s2, s1
    r = p
    for k in range(1, nd + 1):
        for j in range(p + 1):
            ders[k][j] *= r
        r *= (p - k)
    return ders


class BDir(object):
    """One parametric direction: degree p, number of spans, clamped uniform."""
    def __init__(self, p, spans=1):
        self.p = p
        self.spans = spans
        self.n = spans + p          # CV count
        self.U = clamped_knots(p, spans)

    def basis(self, t, nd=0):
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        span = find_span(self.n, self.p, t, self.U)
        k = min(nd, self.p)
        d = ders_basis(span, t, self.p, k, self.U)
        while len(d) < nd + 1:
            d.append([0.0] * (self.p + 1))
        return span - self.p, d

    def greville(self):
        p = self.p; U = self.U
        if p == 0: return [0.5] * self.n
        return [sum(U[i + 1:i + p + 1]) / float(p) for i in range(self.n)]

# =============================================================================
# SURFACE / CURVE EVALUATION  (control points = flat list, index i*nv + j,
# i along U (PINK), j along V (BLUE))
# =============================================================================

def srf_derivs(du, dv, P, u, v, nd):
    fu, Nu = du.basis(u, nd)
    fv, Nv = dv.basis(v, nd)
    nv = dv.n
    out = {}
    for ku in range(nd + 1):
        for kv in range(nd + 1 - ku):
            x = y = z = 0.0
            for a in range(du.p + 1):
                cu = Nu[ku][a]
                if cu == 0.0: continue
                base = (fu + a) * nv + fv
                for b in range(dv.p + 1):
                    c = cu * Nv[kv][b]
                    if c == 0.0: continue
                    q = P[base + b]
                    x += c * q[0]; y += c * q[1]; z += c * q[2]
            out[(ku, kv)] = (x, y, z)
    return out

def srf_point(du, dv, P, u, v):
    return srf_derivs(du, dv, P, u, v, 0)[(0, 0)]

def srf_coefs(du, dv, u, v, ku, kv):
    """Linear coefficients of d^(ku+kv) S / du^ku dv^kv  w.r.t. control points."""
    fu, Nu = du.basis(u, ku)
    fv, Nv = dv.basis(v, kv)
    nv = dv.n
    res = []
    for a in range(du.p + 1):
        cu = Nu[ku][a]
        if cu == 0.0: continue
        base = (fu + a) * nv + fv
        for b in range(dv.p + 1):
            c = cu * Nv[kv][b]
            if c != 0.0:
                res.append((base + b, c))
    return res

def crv_point(d, C, t):
    f, N = d.basis(t, 0)
    x = y = z = 0.0
    for a in range(d.p + 1):
        c = N[0][a]; q = C[f + a]
        x += c * q[0]; y += c * q[1]; z += c * q[2]
    return (x, y, z)

# =============================================================================
# LINEAR LEAST SQUARES (dense normal equations, Gauss with pivoting)
# =============================================================================

def gauss_solve(A, B):
    n = len(A); m = len(B[0])
    M = [list(A[i]) + list(B[i]) for i in range(n)]
    for col in range(n):
        piv = col; best = abs(M[col][col])
        for r in range(col + 1, n):
            val = abs(M[r][col])
            if val > best:
                best = val; piv = r
        if best < 1e-300:
            raise ValueError("singular system")
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        prow = M[col]; pv = prow[col]
        tail = prow[col:]
        for r in range(col + 1, n):
            row = M[r]
            f = row[col] / pv
            if f != 0.0:
                row[col:] = [x - f * y for x, y in zip(row[col:], tail)]
    X = [[0.0] * m for _ in range(n)]
    for r in range(n - 1, -1, -1):
        row = M[r]; pv = row[r]
        for c in range(m):
            s = row[n + c]
            for k in range(r + 1, n):
                s -= row[k] * X[k][c]
            X[r][c] = s / pv
    return X


class LSQ(object):
    """
    Unknowns: ncv control points (xyz) + nscal scalars.
    add()         : equation on CVs only (same for x, y, z)    -> cheap block
    add_coupled() : equation with scalar unknowns (vector coefficient)
    """
    def __init__(self, ncv, nscal=0, fixed=None):
        self.ncv = ncv; self.ns = nscal
        self.fixed = fixed or {}
        self.G = [[0.0] * ncv for _ in range(ncv)]
        self.R = [[0.0, 0.0, 0.0] for _ in range(ncv)]
        if nscal > 0:
            N = 3 * ncv + nscal
            self.A = [[0.0] * N for _ in range(N)]
            self.B = [[0.0] for _ in range(N)]

    def _strip(self, cvc, rhs):
        fx = self.fixed
        if not fx: return cvc, rhs
        out = []; r0, r1, r2 = rhs
        for (i, ci) in cvc:
            q = fx.get(i)
            if q is None: out.append((i, ci))
            else:
                r0 -= ci * q[0]; r1 -= ci * q[1]; r2 -= ci * q[2]
        return out, (r0, r1, r2)

    def add(self, cvc, rhs, w):
        cvc, rhs = self._strip(cvc, rhs)
        G = self.G; R = self.R
        for (i, ci) in cvc:
            wi = w * ci; Gi = G[i]
            for (j, cj) in cvc:
                Gi[j] += wi * cj
            Ri = R[i]
            Ri[0] += wi * rhs[0]; Ri[1] += wi * rhs[1]; Ri[2] += wi * rhs[2]

    def add_coupled(self, cvc, scal, rhs, w):
        cvc, rhs = self._strip(cvc, rhs)
        base = 3 * self.ncv
        A = self.A; B = self.B
        for c in range(3):
            row = [(3 * i + c, ci) for (i, ci) in cvc]
            for (s, vc) in scal:
                if vc[c] != 0.0:
                    row.append((base + s, vc[c]))
            for (a, ca) in row:
                wa = w * ca; Aa = A[a]
                for (b, cb) in row:
                    Aa[b] += wa * cb
                B[a][0] += wa * rhs[c]

    def add_axis(self, cvc, axis, value, w):
        """Equation on ONE coordinate: sum(c_i * P_i[axis]) = value.
        Coupled mode: into the big system. CV-only mode: kept per axis and the
        three coordinates are solved one by one (still small and fast)."""
        row = []
        for (i, ci) in cvc:
            q = self.fixed.get(i)
            if q is None: row.append((3 * i + axis, ci))
            else: value -= ci * q[axis]
        if self.ns == 0:
            ex = self.__dict__.setdefault("ax_extra", {})
            E, Rv = ex.setdefault(axis, ([[0.0] * self.ncv for _ in range(self.ncv)], [0.0] * self.ncv))
            for (a, ca) in row:
                ia = a // 3; wa = w * ca
                for (b, cb) in row:
                    E[ia][b // 3] += wa * cb
                Rv[ia] += wa * value
            return
        A = self.A; B = self.B
        for (a, ca) in row:
            wa = w * ca; Aa = A[a]
            for (b, cb) in row:
                Aa[b] += wa * cb
            B[a][0] += wa * value

    def add_scalar_reg(self, s, target, w):
        k = 3 * self.ncv + s
        self.A[k][k] += w
        self.B[k][0] += w * target

    def solve_safe(self, anchors, ok_fn, weights=(1e2, 1e4, 1e6)):
        """solve(); if ok_fn(points) says the result throws CVs out (the
        equations contradict each other somewhere - a corner, a T-joint),
        solve again with the anchors [(index, point)] held harder each time.
        The seams may stay a little open then, but nothing flies out."""
        G0 = [r[:] for r in self.G]
        R0 = [r[:] for r in self.R]
        ex0 = None
        if getattr(self, "ax_extra", None):
            ex0 = dict((c, ([r[:] for r in E], list(Rv))) for c, (E, Rv) in self.ax_extra.items())
        res = self.solve()
        if ok_fn(res[0]):
            return res
        for w in weights:
            self.G = [r[:] for r in G0]
            self.R = [r[:] for r in R0]
            if ex0 is not None:
                self.ax_extra = dict((c, ([r[:] for r in E], list(Rv))) for c, (E, Rv) in ex0.items())
            for (i, q) in anchors:
                self.add([(i, 1.0)], q, w)
            res = self.solve()
            if ok_fn(res[0]):
                return res
        return res

    def solve(self):
        ncv = self.ncv
        for i, q in self.fixed.items():          # exact, decoupled rows
            self.G[i][i] += 1.0
            self.R[i][0] += q[0]; self.R[i][1] += q[1]; self.R[i][2] += q[2]
        if self.ns == 0:
            ex = getattr(self, "ax_extra", None)
            if not ex:
                X = gauss_solve(self.G, self.R)
                return [(r[0], r[1], r[2]) for r in X], []
            cols = []
            for c in range(3):                   # per-axis equations: one system per coordinate
                if c in ex:
                    E, Rv = ex[c]
                    Gc = [[g + e for g, e in zip(gr, er)] for gr, er in zip(self.G, E)]
                    Rc = [[self.R[i][c] + Rv[i]] for i in range(ncv)]
                else:
                    Gc = self.G; Rc = [[self.R[i][c]] for i in range(ncv)]
                cols.append([r[0] for r in gauss_solve(Gc, Rc)])
            return [(cols[0][i], cols[1][i], cols[2][i]) for i in range(ncv)], []
        A = self.A; B = self.B; G = self.G; R = self.R
        for i in range(ncv):
            Gi = G[i]
            for j in range(ncv):
                g = Gi[j]
                if g != 0.0:
                    for c in range(3):
                        A[3 * i + c][3 * j + c] += g
            for c in range(3):
                B[3 * i + c][0] += R[i][c]
        X = gauss_solve(A, B)
        pts = [(X[3 * i][0], X[3 * i + 1][0], X[3 * i + 2][0]) for i in range(ncv)]
        scal = [X[3 * ncv + s][0] for s in range(self.ns)]
        return pts, scal

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
    the profile, so scan noise does not add up."""
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
    """Scatter of the scan along the profile: what a flexible (6-span) curve
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
    """Profile pts (along a side, on the scan) -> (score, reason, fraction of
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
    """grid[i][j]: scan points over the whole future patch (i along u).
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
        log("    scan turns {:.0f} deg -> at least {} patch(es)".format(turn, n0))
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

# =============================================================================
# B-SPLINE -> BEZIER PATCHES (exact knot insertion, Piegl & Tiller A5.6)
# =============================================================================

def decompose(P, p, U):
    n = len(P) - 1
    m = n + p + 1
    a = p; b = p + 1
    segs = [list(P[0:p + 1])]
    while b < m:
        i = b
        while b < m and U[b + 1] == U[b]:
            b += 1
        mult = b - i + 1
        cur = segs[-1]
        nxt = [None] * (p + 1)
        if mult < p:
            numer = U[b] - U[a]
            alphas = {}
            for j in range(p, mult, -1):
                alphas[j - mult - 1] = numer / (U[a + j] - U[a])
            r = p - mult
            for j in range(1, r + 1):
                save = r - j
                s = mult + j
                for k in range(p, s - 1, -1):
                    al = alphas[k - s]
                    cur[k] = v_add(v_mul(cur[k], al), v_mul(cur[k - 1], 1.0 - al))
                if b < m:
                    nxt[save] = cur[p]
        if b < m:
            for ii in range(p - mult, p + 1):
                nxt[ii] = P[b - p + ii]
            segs.append(nxt)
            a = b; b += 1
    return segs

def decompose_surface(du, dv, P):
    """Returns list of (iu, iv, pts) Bezier patches ((pu+1)*(pv+1) CVs each)."""
    nu, nv = du.n, dv.n
    cols = []
    for j in range(nv):
        cols.append(decompose([P[i * nv + j] for i in range(nu)], du.p, du.U))
    nsegu = len(cols[0])
    out = []
    for su in range(nsegu):
        per_a = [decompose([cols[j][su][a] for j in range(nv)], dv.p, dv.U)
                 for a in range(du.p + 1)]
        for sv in range(len(per_a[0])):
            pts = []
            for a in range(du.p + 1):
                for b in range(dv.p + 1):
                    pts.append(per_a[a][sv][b])
            out.append((su, sv, pts))
    return out

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
      A  fit the whole patch to the scan as if there were no neighbours
      B  match: satisfy G1/G2 (and mirror) moving the CVs as LITTLE as
         possible away from A  (no scan here -> no wild CVs)
      C  refit the rows that do not belong to any continuity to the scan,
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
                    if log: log("    [WARN] side {}: tangent flipped by scan -> fixed magnitude".format(c.side + 1))
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
    data_fn(p) -> [(u, v, scan point)]: PULL mode - every moved patch is fitted
    to its scan in ONE joint solve (neighbours follow A to keep the seams, and
    stay on their scan with what they may move); CV anchors are then weak.
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
    """Pull2Msh: fit A to scan samples data = [(u, v, point)], fairing as in
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
                  scan is far)  -> Even/ToDev
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
    between those neighbours keep their level. No scan.
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
    (on the scan, corner to corner). Only the CVs of that row between the
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
    (corners too) is fitted to pts (corner to corner, on the scan); the
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


# =============================================================================
#                              RHINO PART
# =============================================================================

SIDE_G_KEY = "msh2classA_sideG"
PTS_KEY = "msh2classA_pts"            # user text on a patch built through TempPoints: the points (JSON)
NPATCH_KEY = "msh2classA_npatch"      # user text on an NPatch: "sides;deviation"        # user text on a patch: {"side": level} wanted at its seams


def side_levels(oid):
    """{side: 0/1/2} stored on patch oid (sides built to / matched with a neighbour)."""
    try:
        txt = rs.GetUserText(oid, SIDE_G_KEY)
        return dict((int(k), int(v)) for k, v in json.loads(txt).items()) if txt else {}
    except Exception:
        return {}


def set_side_level(oid, side, level):
    try:
        d = side_levels(oid); d[int(side)] = int(level)
        rs.SetUserText(oid, SIDE_G_KEY, json.dumps(dict((str(k), v) for k, v in d.items())))
    except Exception:
        pass


# Settings that are LENGTHS are kept in millimetres, so a value set in a mm
# file stays the same real size in an inch or metre file (same Rhino session).
LENGTH_KEYS = ("autotol", "mtol", "fit_radius", "fine_mv")


def _mm_per_unit():
    try:
        return Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    except Exception:
        return 1.0


def opt_get(key, default):
    v = sc.sticky.get("M2CA_" + key, None)
    if v is None: return default
    if key in LENGTH_KEYS and isinstance(v, tuple) and len(v) == 2 and v[0] == "mm":
        return v[1] / _mm_per_unit()
    return v


def opt_set(key, val):
    if key in LENGTH_KEYS:
        sc.sticky["M2CA_" + key] = ("mm", float(val) * _mm_per_unit())
    else:
        sc.sticky["M2CA_" + key] = val


def T(p):
    return (p.X, p.Y, p.Z)

def P3(t):
    return rg.Point3d(t[0], t[1], t[2])

def col(r, g, b):
    return sd.Color.FromArgb(r, g, b)

C_RED = None; C_BLUE = None; C_HOVER = None; C_PT = None; C_GRAY = None

def init_colors():
    global C_RED, C_BLUE, C_HOVER, C_PT, C_GRAY
    C_RED = col(235, 0, 190); C_BLUE = col(30, 60, 230)   # PINK / BLUE directions (red = hint)
    C_HOVER = col(255, 200, 0); C_PT = col(0, 170, 0); C_GRAY = col(90, 90, 90)

class UserAbort(Exception):
    """Esc pressed during a long wait."""
    pass

_ESC = [False]

def _on_escape(sender, e):
    _ESC[0] = True

def hook_escape():
    """Esc key -> flag (works while Rhino processes events); old hook removed
    first so repeated runs do not stack handlers."""
    old = sc.sticky.get("M2CA_esc_hook")
    try:
        if old is not None: Rhino.RhinoApp.EscapeKeyPressed -= old
    except Exception: pass
    try:
        Rhino.RhinoApp.EscapeKeyPressed += _on_escape
        sc.sticky["M2CA_esc_hook"] = _on_escape
    except Exception: pass

def esc_pressed():
    try:
        if sc.escape_test(False): _ESC[0] = True
    except Exception:
        try: Rhino.RhinoApp.Wait()
        except Exception: pass
    return _ESC[0]

def check_esc():
    if esc_pressed():
        _ESC[0] = False
        raise UserAbort()

def show_busy(msg="Working..."):
    _ESC[0] = False
    msg = msg + "   (Esc = stop)"
    try:
        Rhino.RhinoApp.SetCommandPrompt(msg)
    except Exception: pass
    try:
        vp = sc.doc.Views.ActiveView.ActiveViewport
        dot = rs.AddTextDot(msg, vp.CameraTarget)
        rs.ObjectColor(dot, (255, 200, 0))
        sc.doc.Views.Redraw()
        return dot
    except Exception:
        Rhino.RhinoApp.SetCommandPrompt(msg)
        return None

def hide_busy(dot_id):
    if dot_id is not None:
        try:
            rs.DeleteObject(dot_id); sc.doc.Views.Redraw()
        except Exception:
            pass

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
    """Layer full path fp is the scan root (or below it)."""
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
    """[(InstanceObject, layer full path)] of the scan blocks (under 'Reference Meshes- Msh2classA')."""
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
    """[(block, layer full path)] the scan tools work on: the scan blocks
    (LoadScans, layer 'Reference Meshes- Msh2classA') and any other BLOCK with a mesh inside.
    Plain meshes are not used (keep scans as blocks - the file stays small)."""
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
    idx = sc.doc.InstanceDefinitions.Add(dname, "scan: " + path, rg.Point3d.Origin, geos, ats)
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
    """LoadScans: pick many STL / OBJ / PLY files; each one becomes a LINKED
    block (embedded if Rhino refuses the link) at its own coordinates, on its
    own layer under 'Reference Meshes- Msh2classA', with its own colour. Each is hidden after
    loading - only the last one stays visible. Files are only read."""
    files = rs.OpenFileNames("Load scans as linked blocks",
                             "Scans (*.stl;*.obj;*.ply)|*.stl;*.obj;*.ply|All files (*.*)|*.*||")
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
    try: Rhino.UI.StatusBar.ShowProgressMeter(0, len(files), "Loading scans", True, True)
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
            busy = show_busy("Loading scan {}/{}: {} ...".format(n + 1, len(files), name))
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
        print("[LoadScans] STOPPED by Esc - {} of {} scan(s) loaded, the rest skipped".format(len(done), len(files)))
    else:
        print("[LoadScans] {} of {} scan(s) loaded as blocks under layer '{}' (only the last one shown)".format(
            len(done), len(files), SCAN_ROOT))




def prefetch_meshes(insts):
    """Read the meshes (and the mirror points) of the visible scans NOW,
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
    visible scan built (that tree made the first click take ~10 s)."""
    busy = show_busy("{}: preparing the scans for clicking (first time only, big scans take a few seconds)...".format(tool))
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
    """Clicks on the scan: the point comes from OUR ray on the scans, Rhino
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
    """ScanMirror (left on, maybe from an earlier run of the script) would draw
    a second mirror that the MirrorPreview option cannot hide - off while a
    scan tool runs. -> its plane axis, or None."""
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
    busy = show_busy("Reading scan {} (once)...".format(idef.Name))
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
    """Point under the click on the VISIBLE scans (front-most). The scan tools
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
    -> (hit, object) on the front-most visible scan. Blocks: ray in the
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
    """Ray a_ + t*d0 (t > 0) -> (hit, object) on the front-most visible scan."""
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
    """Symmetry plane for the scan tools = MirrorPlane. Off: the default YZ
    (the middle line of the Front view) is taken - no question. Change it
    with the SymPlane option of ScanCenter / ScanFloor (or MirrorPlane)."""
    cur = opt_get("mirror", "Off")
    if cur not in allowed:
        cur = SYM_DEFAULT if SYM_DEFAULT in allowed else allowed[0]
        opt_set("mirror", cur)
        print("[{}] symmetry plane = {} - change it with the SymPlane option".format(tool, SYM_TEXT[cur]))
    return MIRROR_AXIS[cur]


def add_sym_option(gp, allowed=("YZ", "XZ", "XY")):
    """SymPlane list option on a scan-tool prompt -> its index."""
    cur = opt_get("mirror", SYM_DEFAULT)
    return gp.AddOptionList("SymPlane", list(allowed), list(allowed).index(cur) if cur in allowed else 0)


def set_sym_from_option(gp, allowed=("YZ", "XZ", "XY")):
    key = list(allowed)[gp.Option().CurrentListOptionIndex]
    opt_set("mirror", key)
    print("  symmetry plane = {}".format(SYM_TEXT[key]))
    return MIRROR_AXIS[key]


def single_view():
    """One maximized Perspective view: every viewport keeps its own copy of
    the big scan meshes for display, 4 views are much slower than one."""
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
    """Bring every chosen scan to T * (its place at the start of the tool).
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
    points) of the visible scans, and (Fine) the gumball."""
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
    chosen scans (hidden ones too) are turned and moved so the symmetry plane
    of the pairs is the MirrorPlane. Blue points = the mirrored scans.
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
                print("[Center] cancelled - scans back where they were"); return
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
                print("  click ON a visible scan"); continue
            ok, inv = total().TryGetInverse()
            pb = rg.Point3d(hit[0]); pb.Transform(inv)       # back to base coordinates
            if st["left"] is None:
                st["left"] = pb
            else:
                pairs.append((st["left"], pb)); st["left"] = None
                st["T"] = solve(); commit()
                print("  pair {}: scans turned {:.2f} deg and moved onto the symmetry plane".format(len(pairs), st["tilt"]))
                print_devs()
                if st["tilt"] > 15.0:
                    print("  [WARN] that is a big turn: are LEFT/RIGHT really across the car and is MirrorPlane "
                          "right? (UndoPair / Cancel)")
            sc.doc.Views.Redraw()
            continue
        if res == Rhino.Input.GetResult.Cancel:          # Esc = cancel, like the Cancel option
            _apply(insts, applied, rg.Transform.Identity); sc.doc.Views.Redraw()
            print("[Center] cancelled (Esc) - scans back where they were"); return
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
    """Sphere radius around every tag for AutoFit (model units); 0 = whole scan."""
    return float(opt_get("fit_radius", 50.0 / _mm_per_unit()))


def _scan_closest(geos, q):
    """Nearest point of the visible scans to world point q -> (point, normal) or None."""
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
    """Find the scan's OWN symmetry plane: scan points are mirrored in the world
    plane and pulled onto the scan (point-to-plane ICP); points that land far
    from it (holes, parts scanned on one side only, asymmetric details) are
    left out. -> (transform that puts that plane onto the world plane,
    report text) or (None, reason).
    centres + radius: only scan points inside spheres around the tags (the
    places you marked as symmetric) - fast on any scan size and nothing
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
        return None, ("too few scan points inside the tag spheres - larger AutoFitRadius?" if spheres
                      else "too few scan points (are the scans visible?)")
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
            return None, "the mirrored scan does not land on the scan (centre the pairs first)"
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
    txt = ("[{}] ".format("spheres r={:g} around {} tags".format(radius, len(centres)) if spheres else "whole scan") +
           "mirror-to-scan distance (median) {} -> {}, {} of {} points used; "
           "scans turned {:.4f} deg, centre moved {}".format(fl(first), fl(last), used, len(base), ang,
                                                                  fl(abs(o.X * E.X + o.Y * E.Y + o.Z * E.Z))))
    return move, txt


# ---- Fine: a small gumball in WORLD axes (move / turn only) -----------------
_AXES = (rg.Vector3d(1, 0, 0), rg.Vector3d(0, 1, 0), rg.Vector3d(0, 0, 1)) if IN_RHINO else ()


def _fine_pivot(insts, axis, pairs, world):
    """Turning point: the middle of the tags, put ON the symmetry plane; no
    tags: the middle of the visible scans."""
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
    All chosen scans move, also the hidden ones. Enter / Back = keep,
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
    busy = show_busy("AutoFit: fitting the mirror to the scan...")
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
    """Every scan block: once scans are aligned to each other they move as
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
    """The scan root_id and every scan aligned to it, directly or further down."""
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
    """Every saved setup keeps the alignment: the moved scans get, in each
    setup, the place they now have relative to a scan that did not move."""
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
    """Align: click a point on the scan that STAYS, then the same point on the
    scan that MOVES - at least 3 such pairs. The moving scan (and every scan
    aligned to it before) jumps onto the other one after each pair. Fit =
    automatic on the overlap, Fine = gumball. Enter = done, Esc = cancel."""
    single_view()
    insts = all_scan_insts()
    if len(insts) < 2:
        print("[Align] needs two scans at least"); return
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
        AutoFit of Center: moving-scan points there -> closest points of the
        scan that stays. The rest of the scans (extra material, free ends)
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
            print("  too few scan points inside the spheres - larger AutoFitRadius?"); return
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
            gp.SetCommandPrompt("Align pair {}: point on the scan that {} <right click = done, Esc = cancel>".format(
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
                print("  click ON a visible scan"); continue
            p, obj = hit
            if st["first"] is None:                            # a point on the scan that stays
                if st["fixed"] is not None and str(obj.Id) != st["fixed"]:
                    print("  the first point of a pair goes on the scan that STAYS"); continue
                st["first"] = rg.Point3d(p)
                if st["fixed"] is None: st["fixed"] = str(obj.Id); st["fixed_obj"] = obj
            else:                                              # the same point on the scan that moves
                if str(obj.Id) == st["fixed"]:
                    print("  the second point goes on the scan that MOVES"); continue
                if st["moving"] is None:
                    branch = align_branch(obj.Id)
                    if st["fixed"] in branch:
                        print("  this scan carries the one that stays (it was aligned to it) - align the other way"); continue
                    st["moving"] = str(obj.Id); st["ids"] = branch; st["moving_obj"] = obj
                    st["set"] = [o for o in insts if str(o.Id) in branch]
                elif str(obj.Id) not in st["ids"]:
                    print("  the second point goes on the same moving scan as before"); continue
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
            print(tag + " cancelled - scans back where they were"); return
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
    print(tag + " done: {} scan(s) moved{}".format(len(st["set"]), "" if len(st["set"]) == 1 else
                                                    " (the moved scan carries the ones aligned to it)"))



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
            if hit is None: print("  click ON a visible scan"); continue
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
              "check the Center pairs or the scans".format(roll))
    save_setup_prompt(insts, "Floor")


# ---- named setups ------------------------------------------------------------
# A setup = the position of every scan block under a name (front, rear ...).
# Saved ONLY with the scans: 'scan_setups.json' in the folder of the scan
# files, written at once. Nothing is stored in the .3dm - any file finds the
# setups again after LoadScans of the same scans.
SETUP_JSON = "scan_setups.json"


def _setup_files():
    """scan_setups.json in every folder the loaded scans come from."""
    out = []
    for (o, lp) in scan_instances():
        src = obj_src(o)
        if src:
            f = os.path.join(os.path.dirname(src), SETUP_JSON)
            if f not in out: out.append(f)
    return out


def _read_setups():
    """All setups from scan_setups.json in the scan folders (newest copy of a
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
    """Write to scan_setups.json in every scan folder. -> files written."""
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
    """Key of a scan in a setup: its block name (= the scan file name)."""
    return obj.InstanceDefinition.Name


def obj_src(obj):
    return obj.Attributes.GetUserString(SCAN_KEY) or ""


def _setup_record(st, o):
    """The entry of scan block o in setup st: by block name, else by the scan
    file name (a new .3dm after LoadScans), else by the old full-path key."""
    rec = st.get(_scan_name(o))
    if rec: return rec
    fn = os.path.basename(obj_src(o)).lower()
    if fn:
        for k, v in st.items():
            if isinstance(v, dict) and v.get("file", "").lower() == fn: return v
    return st.get(obj_src(o))


def save_setup_prompt(insts, what):
    if insts and not any(isinstance(o, ro.InstanceObject) for o in insts):
        print("  (plain meshes carry their position in the mesh itself - setups are for scan blocks only)")
        return
    dflt = sc.sticky.get("M2CA_setup", "front")
    gs = ric.GetString()
    gs.SetCommandPrompt("{} done. Remember these scan positions under a NAME (e.g. front, rear) to bring them back "
                        "later with ScanSetup - type a name + Enter, Enter alone = '{}'".format(what, dflt))
    gs.AcceptNothing(True)
    gs.AddOption("DontSave")
    res = gs.Get()
    if res == Rhino.Input.GetResult.Nothing:
        name = dflt
    elif res == Rhino.Input.GetResult.String:
        name = (gs.StringResult() or "").strip() or dflt
    else:                                          # DontSave / Esc
        print("  positions not saved as a setup (the scans stay where they are)"); return
    sc.sticky["M2CA_setup"] = name
    data = _read_setups()
    st = {"_saved": time.time()}
    for (o, lp) in scan_instances():
        x = o.InstanceXform
        st[_scan_name(o)] = {"layer": lp, "matrix_row_major": [x[i, j] for i in range(4) for j in range(4)],
                             "block": o.InstanceDefinition.Name, "file": os.path.basename(obj_src(o))}
    data[name] = st
    if not _setup_files():
        print("  [Setup] not saved: these scan blocks do not know their scan files (load them with LoadScans)")
        return
    where = _write_setups(data)
    n = len([k for k in st if not k.startswith("_")])
    print("[Setup] '{}' saved: {} scan(s), in {}".format(name, n, " and ".join(where) or "-"))


def scan_setup():
    """ScanSetup: apply a saved setup (all scan blocks jump there), save the
    present positions under a name, or remove a setup."""
    data = _read_setups()
    names = sorted(data.keys())
    SAVE = "<save the present positions as ...>"; REMOVE = "<remove a setup ...>"
    items = names + [SAVE] + ([REMOVE] if names else [])
    choice = rs.ListBox(items, "Scan setups (positions of the scan blocks)", "ScanSetup")
    if choice is None: return
    if choice == SAVE:
        save_setup_prompt([], "Setup"); return
    if choice == REMOVE:
        gone = rs.ListBox(names, "Remove which setup?", "ScanSetup - remove")
        if not gone: return
        if rs.MessageBox("Remove the setup '{}'?\n(The scans stay where they are.)".format(gone), 4 | 32,
                         "ScanSetup - remove") != 6:
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
    print("[Setup] '{}' applied to {} scan(s){}".format(choice, n,
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
    """Vertices of the scan as a point cloud in its own coordinates, thinned
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
    """Mirrored copies of the scan instances objs as opaque points (drawn
    only, nothing added to the file). Lifted a little towards the eye -
    same place on screen - so points lying ON the real scan are not hidden
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
    """ScanMirror: mirrored copies of the visible scans as points."""
    if ids is None:
        objs = [o for (o, lp) in scan_objects()]
    else:
        objs = [sc.doc.Objects.FindId(i) for i in ids]
    draw_mirror_points(d, axis, [o for o in objs if o is not None and scan_visible(o)])


class _ScanMirrorDraw(object):
    """Pipeline events (no conduit subclass - same way as the rest of the
    script); kept in sticky so the next ScanMirror can switch it off."""
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
    """ScanMirror: mirrored copies of the VISIBLE scan blocks as blue points, drawn only (nothing added to the file). Again =
    off. Switched off while ScanCenter / ScanFloor run, back on after."""
    cur = sc.sticky.get("M2CA_scanmir")
    if cur is not None:
        try: cur.off()
        except Exception: pass
        sc.sticky["M2CA_scanmir"] = None
        sc.doc.Views.Redraw(); print("[ScanMirror] off"); return
    axis = mirror_normal("ScanMirror")
    if axis is None: return
    objs = [o for (o, lp) in scan_objects() if scan_visible(o)]
    prefetch_meshes(objs)
    n = len(objs)
    c = _ScanMirrorDraw(axis, [o.Id for o in objs]); c.on()
    sc.sticky["M2CA_scanmir"] = c
    sc.doc.Views.Redraw()
    print("[ScanMirror] {} scan(s) mirrored, shown as blue points (ScanMirror again = off)".format(n))


SCAN_TOOL_LABEL = {"LoadScans": "Load scans", "ScanAlign": "Align", "ScanCenter": "Center", "ScanFloor": "Floor",
                   "ScanSetup": "Setups", "ScanMirror": "Mirror on/off"}
SCAN_TOOLS = (("LoadScans", load_scans), ("ScanAlign", scan_align), ("ScanCenter", scan_center),
              ("ScanFloor", scan_floor), ("ScanSetup", scan_setup), ("ScanMirror", scan_mirror))


# ---- scan tools as panel BUTTONS (and command-line options) -----------------
# A button click only leaves a note and wakes up the waiting Get (custom
# message); the tool then runs from the command loop - never from inside
# the click (a tool has its own clicking).
def scan_tools_ready(on):
    sc.sticky["M2CA_tools_ok"] = bool(on)


def post_scan_tool(name):
    if name == "ScanMirror":                     # only drawing on/off: straight away, nothing to leave
        run_scan_tool(name); return
    if not sc.sticky.get("M2CA_tools_ok"):
        print("  [{}] finish the step you are in first (or clear the patch you are drawing)".format(name)); return
    sc.sticky["M2CA_pending_tool"] = name
    try:
        ric.GetBaseClass.PostCustomMessage(name)
    except Exception:
        sc.sticky["M2CA_pending_tool"] = None
        print("  [{}] buttons cannot start it here - use the command-line option {}".format(name, name))


def take_pending_tool():
    nm = sc.sticky.get("M2CA_pending_tool")
    sc.sticky["M2CA_pending_tool"] = None
    return nm


def run_scan_tool(nm):
    fn = dict(SCAN_TOOLS).get(nm)
    if fn is None: return
    try:
        fn()
    except UserAbort:
        print("[{}] stopped by Esc".format(nm))
    except Exception:
        print("[{}] failed:".format(nm)); print(traceback.format_exc())
    finally:
        _MIRROR_HIDE[0] = False; sc.doc.Views.Redraw()
        _ESC[0] = False                     # an Esc inside the tool ends the tool, not the plugin


SURFACE_TOOLS = (("Fillet", "Fillet"), ("NPatch", "NPatch"), ("GlobalMatch", "GlobalMatch"),
                 ("ForceMirror", "ForceMirror"))


def post_surface_tool(name):
    """A button of the Surfaces group: run in the main loop (like the scan tools)."""
    if not sc.sticky.get("M2CA_tools_ok"):
        print("  [{}] finish the step you are in first (or clear the patch you are drawing)".format(name)); return
    sc.sticky["M2CA_pending_tool"] = "SRF:" + name
    try:
        ric.GetBaseClass.PostCustomMessage(name)
    except Exception:
        sc.sticky["M2CA_pending_tool"] = None
        print("  [{}] the button cannot start it here".format(name))


def surface_tool_buttons(ef, lay):
    """The 'Surfaces' group of the panel: tools that work on many patches."""
    try:
        lb = ef.Label(); lb.Text = "Surfaces"
        lay.AddRow(lb)
    except Exception:
        pass
    for (nm, label) in SURFACE_TOOLS:
        b = ef.Button(); b.Text = label
        b.Click += (lambda n_: (lambda sender, e: post_surface_tool(n_)))(nm)
        lay.AddRow(b)


def scan_tool_buttons(ef, lay):
    """The 'Scans' group of the panel: one button per scan tool."""
    try:
        lb = ef.Label(); lb.Text = "Scans"
        lay.AddRow(lb)
    except Exception:
        pass
    for (nm, fn) in SCAN_TOOLS:
        b = ef.Button(); b.Text = SCAN_TOOL_LABEL.get(nm, nm)
        b.Click += (lambda n_: (lambda sender, e: post_scan_tool(n_)))(nm)
        lay.AddRow(b)


CAD_SERVICES_EMAIL = "info@3dPARTdesign.com"
CAD_SERVICES_URL = "https://3dPARTdesign.com"
CAD_SERVICES_QUESTIONS = ("Too much work and not enough time?",
                          "Shapes that won't come out the way you planned them?",
                          "Trouble with your scanner or a scan?",
                          "Not sure how to prepare a file for series production?")
CAD_SERVICES_TEXT = ("I can help: on-demand 3D modeling for production - custom body kits, "
                     "NURBS/SubD surfacing, 3D scan cleanup and production preparation.")


def _open_url(url):
    try:
        import Eto.Forms as ef
        ef.Application.Instance.Open(url)
        return True
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        print("  Open in your browser: " + url)
        return False


def _wrap_lines(text, width):
    """Text broken into lines of at most width characters (at spaces)."""
    lines = []; cur = ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur: lines.append(cur)
    return "\n".join(lines)


def show_cad_services():
    """Popup: who made the plugin and how to reach him."""
    try:
        import Eto.Forms as ef
        import Eto.Drawing as ed
        import Rhino.UI as rui
        dlg = ef.Dialog()
        dlg.Title = "CAD Services"
        dlg.Resizable = False
        dlg.Padding = ed.Padding(14)
        lay = ef.DynamicLayout(); lay.Spacing = ed.Size(6, 8)
        for n_, qtxt in enumerate(CAD_SERVICES_QUESTIONS):
            t = ef.Label(); t.Text = qtxt
            try: t.Font = ed.Font(t.Font.Family, t.Font.Size + (1 if n_ == 0 else 0), ed.FontStyle.Bold)
            except Exception: pass
            lay.AddRow(t)
        b = ef.Label(); b.Text = _wrap_lines(CAD_SERVICES_TEXT, 62)   # own line breaks: the dialog sizes right
        lay.AddRow(b)
        e = ef.Label(); e.Text = "E-mail: " + CAD_SERVICES_EMAIL
        lay.AddRow(e)
        w = ef.Label(); w.Text = "Portfolio: 3dPARTdesign.com"
        lay.AddRow(w)
        info = ef.Label(); info.Text = " "
        bc = ef.Button(); bc.Text = "Copy e-mail"
        bo = ef.Button(); bo.Text = "Open website"
        def on_copy(sender, ev):
            try:
                cb = ef.Clipboard()
                cb.Text = CAD_SERVICES_EMAIL
                info.Text = "E-mail copied."
            except Exception:
                info.Text = CAD_SERVICES_EMAIL
        def on_open(sender, ev):
            _open_url(CAD_SERVICES_URL)
        bc.Click += on_copy
        bo.Click += on_open
        row = ef.DynamicLayout(); row.Spacing = ed.Size(8, 0)
        row.AddRow(bc, bo, None)
        lay.AddRow(row)
        lay.AddRow(info)
        dlg.Content = lay
        dlg.MinimumSize = ed.Size(440, 0)
        try:
            dlg.ShowModal(rui.RhinoEtoApp.MainWindow)
        except Exception:
            dlg.ShowModal()
    except Exception:
        print("CAD Services - " + " ".join(CAD_SERVICES_QUESTIONS) + " " + CAD_SERVICES_TEXT)
        print("  E-mail: {}   Portfolio: {}".format(CAD_SERVICES_EMAIL, CAD_SERVICES_URL))


def cad_services_button(ef, lay):
    """At the very bottom of both panels."""
    try:
        sp = ef.Label(); sp.Text = " "
        lay.AddRow(sp)
        b = ef.Button(); b.Text = "CAD Services"
        b.Click += lambda sender, e: show_cad_services()
        lay.AddRow(b)
    except Exception:
        pass


def open_scan_panel():
    """Small panel with the scan tools while you pick the scan."""
    try:
        import Eto.Forms as ef
        import Eto.Drawing as ed
        import Rhino.UI as rui
        f = ef.Form()
        f.Title = "msh2classA"
        f.WindowStyle = ef.WindowStyle.Utility
        f.Resizable = False; f.Minimizable = False; f.Maximizable = False
        f.ShowInTaskbar = False
        try: f.Owner = rui.RhinoEtoApp.MainWindow
        except Exception:
            try: f.Topmost = True
            except Exception: pass
        lay = ef.DynamicLayout(); lay.Padding = ed.Padding(4); lay.Spacing = ed.Size(2, 2)
        scan_tool_buttons(ef, lay)
        cad_services_button(ef, lay)
        f.Content = lay
        loc = sc.sticky.get("M2CA_panel_loc")
        f.Show()
        if loc:
            f.Location = ed.Point(loc[0], loc[1])
        else:
            try:
                r = sc.doc.Views.ActiveView.ScreenRectangle
                f.Location = ed.Point(int(r.Right - f.Width - 12), int(r.Top + 12))
            except Exception:
                pass
        return f
    except Exception:
        return None


def close_scan_panel(f):
    if f is None: return
    try: sc.sticky["M2CA_panel_loc"] = (f.Location.X, f.Location.Y)
    except Exception: pass
    try: f.Close()
    except Exception: pass


def get_scan(preset_id=None):
    """Pick the scan to work on - or run a scan tool first (they move only
    the scan BLOCKS; afterwards pick the scan). preset_id: the scan of the
    session that was left for a scan tool - taken again without asking."""
    obj_id = None
    if preset_id is not None and sc.doc.Objects.FindId(preset_id) is not None:
        obj_id = preset_id
    panel = None if obj_id is not None else open_scan_panel()
    while obj_id is None:
        go = ric.GetObject()
        go.SetCommandPrompt("Click the scan, or a scan tool <Esc = quit>")
        try:
            go.GeometryFilter = ro.ObjectType.Mesh | ro.ObjectType.InstanceReference
        except Exception:
            import System
            go.GeometryFilter = System.Enum.ToObject(ro.ObjectType, 32 | 4096)
        go.EnablePreSelect(True, True)
        idx = {}
        for (nm, fn) in SCAN_TOOLS:
            idx[go.AddOption(nm)] = nm
        try: go.AcceptCustomMessage(True)            # the panel's scan buttons
        except Exception: pass
        scan_tools_ready(True)
        try:
            res = go.Get()
        finally:
            scan_tools_ready(False)
        if res == Rhino.Input.GetResult.CustomMessage:            # a scan button
            nm = take_pending_tool()
            if nm: run_scan_tool(nm)
            continue
        if res == Rhino.Input.GetResult.Option:
            nm = idx.get(go.Option().Index)
            if nm: run_scan_tool(nm)
            continue
        if res == Rhino.Input.GetResult.Nothing:          # Enter / right click (e.g. left over from a file
            continue                                      # dialog): not a reason to quit - only Esc quits
        if res != Rhino.Input.GetResult.Object:
            close_scan_panel(panel)
            return None
        obj_id = go.Object(0).ObjectId
    close_scan_panel(panel)
    if obj_id is None:
        return None
    obj = sc.doc.Objects.FindId(obj_id)
    if obj is None:
        return None
    get_scan.obj_id = obj_id             # for the Scan on/off view button
    if isinstance(obj.Geometry, rg.Mesh):
        print("[OK] Mesh ({} faces)".format(obj.Geometry.Faces.Count))
        return obj.Geometry
    if not isinstance(obj, ro.InstanceObject):
        print("[ERROR] Not a mesh or block."); return None
    xf = obj.InstanceXform
    key = "{}|{}|{}".format(sc.doc.RuntimeSerialNumber, obj_id,
                            ",".join("{:.6g}".format(xf[i, j]) for i in range(4) for j in range(4)))
    cache = sc.sticky.get("M2CA_meshcache")
    if cache and cache[0] == key:
        print("[OK] Mesh from block (cached, {} faces)".format(cache[1].Faces.Count))
        return cache[1]
    busy = show_busy("Extracting mesh from block (once)...")
    meshes = []

    def collect(idef, x):
        for i in range(idef.ObjectCount):
            check_esc()
            piece = idef.Object(i)
            if piece is None: continue
            if isinstance(piece.Geometry, rg.Mesh):
                m = piece.Geometry.Duplicate(); m.Transform(x); meshes.append(m)
            elif isinstance(piece, ro.InstanceObject):
                collect(piece.InstanceDefinition, x * piece.InstanceXform)
    try:
        collect(obj.InstanceDefinition, xf)
    finally:
        hide_busy(busy)
    if not meshes:
        print("[ERROR] No mesh in block."); return None
    joined = rg.Mesh()
    for m in meshes:
        joined.Append(m)
    joined.Compact(); joined.RebuildNormals()
    sc.sticky["M2CA_meshcache"] = (key, joined)
    print("[OK] Mesh from block ({} faces) - cached for next runs".format(joined.Faces.Count))
    return joined

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
    """Visible single-face surfaces near the scan: (id, brep, bbox, smoothable)."""
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

# -----------------------------------------------------------------------------
# SESSION
# -----------------------------------------------------------------------------

HELP = """
------------------------------------------------------------------
msh2classA - how it works
  DRAWING  Side 1: click start, points on the scan, right click = end
           side. Sides 2, 3: points, right click. Side 4 returns to
           corner 1: optional points, right click = BUILD.
           A click on an edge / curve / its end is always a corner and
           ends the side at once. Two corners on the same edge or curve
           -> that side follows it (G0/G1/G2 to a surface edge).
           Near an edge END the corner snaps to that end (the
           neighbour's corner); a corner made on the scan within 2 x
           ScanTol of an edge is moved onto it; points clicked along
           an edge do not turn that side into a scan side. Narrow
           blend: click its 4 corners = the 4 corners of the two
           patches, both long sides then follow the edges.
  SIDE BUBBLES  Click: G0 -> G1 -> G2.  On a surface edge = continuity
           to it. On the scan = rows RESERVED for a future neighbour.
           Rows across the patch: G0/curve 1, G1 2, G2 3; CV count =
           sum of the two opposite sides (centre label shows the size).
           Bubble on an EDGE side: G0 -> G1 -> G2 -> G2>G0 -> G1>G0.
           G2>G0 / G1>G0: the rows are laid out like G2 / G1 (3 / 2 rows),
           but only the edge row is matched to the neighbour - the seam
           is G0 (the buttons treat it as G0); MatchUVN can raise it later.
  PATCH BUTTONS  Before the first corner, hover a finished patch: small
           buttons in its centre, one step per click (click again for more).
           Direction the CVs move:  N = up / down (along the normal, off
           the patch), UV = sideways (in the patch), UVN = both.
             FairN    N    smoother shape: waves, noise; the CV layout
                           stays (free edges: also UV across the edge)
             SmthUVN  UVN  FairN + EvenUV + StrUV at once (strongest)
             EvenUV   UV   rows a step towards even spacing; shape stays.
                           Rows along a seam that is only PART of an edge
                           (T-joint, patches of different length) stay: an
                           exact G1/G2 fixes them. Even long patches BEFORE
                           shorter ones are built against them
             StrUV    UV   rows / columns straight seen from above (only
                           sideways across the row); shape stays
             RelaxUV  UV   less skew: rows and columns cross as the four
                           edges imply (Coons layout); shape stays
             ToDevUV  UV   the nearest row (+ the one behind) moves onto
                           the place where a WHOLE row is furthest from
                           the scan; shape stays
             PullN    N    fit the patch to the scan, CVs only along the
                           normal (free edges also across the edge): the
                           CV layout (EvenUV / StrUV / RelaxUV) stays;
                           neighbours give way with their G0/G1/G2 rows.
                           A TempPts patch: onto its points, not the scan
             RstN     N    the smoothest shape its edges / seams allow,
                           CVs only along the normal, no scan, no points
                           (then PullN)
             FreeEdgeEdt   redraw a free edge (no neighbour / mirror): first
                           click the edge (the nearest one lights up), then
                           a) start AT a corner dot, points on the scan, end
                              AT the other corner: only the middle of the
                              edge moves, both corners stay;
                           b) start BESIDE the patch, points on the scan
                              where the edge should be, right click: the
                              WHOLE edge (corners too) goes onto that line,
                              the rows behind it follow (fading to the
                              middle); neighbours at its corners come along
                              and keep their G0/G1/G2
             Del           delete the patch; neighbours stay as they are;
                           UndoSrfEdit brings it back
             MatchUVN UVN  after hand edits of the patch: bubbles on its seams
                           (and on sides NEAR the mirror plane - e.g. drawn
                           without snapping: Apply puts them onto the plane)
                           (click = G0 / G1 / G2), Apply -> the NEIGHBOURS
                           come back to this patch (and stay matched among
                           themselves); the patch itself and the scan are
                           not used to change anything
           Button colours: green fine, yellow could be better, red = worth
           a click (FairN: curvature bumps, EvenUV: spacing ratio, StrUV:
           row kinks, RelaxUV: skew, PullN: scan deviation vs ScanTol,
           MatchUVN: seams open or below their G). The numbers are at the
           bottom of the legend. Usual order: row 1 (the CV net), row 2
           (the shape), row 3 when needed - click where it is red.
           Every seam is KEPT at the level it was built with (G0/G1/G2,
           stored on the patch) or set by Match - never lower; older
           patches: a seam that drifted a little still counts as before.
           Seams opened a little (up to 2 x ScanTol) are closed again.
           Neighbours change only their continuity rows - except rows that
           CROSS a seam in EvenUV / ToDevUV: the neighbour's matching rows move
           along (no 'S' at the seam). Every seam keeps its G level, the
           mirror stays a mirror; UndoSrfEdit undoes any of them.
  ORDER    Build flat / gently curved patches first, fillets G2 to them.
           A strip = ONE patch with SrfSplit=AutoSplit.
options
  DefaultCont    G0Pos / G1Tan / G2Curv: default for every new side.
  FitSrfTo       ScanMsh = fit the whole scan under the patch
                 TempPts = click points the patch must follow (e.g. to
                           bridge a recess); the rest is as smooth as the
                           edges allow. The points are kept ON the patch:
                           every button later uses them, not the scan
                           (RstN = smoothest the edges allow, then PullN =
                           onto the points).
                 NoScan  = only the edges lie on the scan; the inside is
                           the smoothest surface the edges and G0/G1/G2
                           allow (smoothing has no scan leash there)
  SrfSplit       OneSrf = one single-span patch
                 AutoSplit = as many single-span patches as the scan needs
                 (cut from one smooth surface, C4 between them)
  ScanTol        allowed scan deviation in model units: picking CVs,
                 AutoSplit, the smoothing leash. Compared with 90% of the
                 samples. The Deviation colours use it too.
  HintTolPct     SideLenHint only: allowed deviation in % of the side /
                 patch size (default 0.2 %), so the size advice depends on
                 the SHAPE, not on how big it is.
  CornerFlow     where a corner sits on the END of a neighbour's edge:
                 KinkOK = the new side may leave at any angle (kink in
                 the outline is allowed, the seam is still G1/G2)
                 Smooth = the new side continues the neighbour's outline
                 edge with the seam's G1/G2 (no kink in the outline)
  MirrorPlane    Off / XZ (y=0) / YZ (x=0) / XY (z=0). A pink "mirror"
                 dot shows when the cursor is within snap distance. Sides within
  MirrorSnapDist of the plane snap onto it. The mirror is matched like
                 an invisible mirrored copy (MatchSrf, average): G0 = edge
                 on the plane, G1 = 2nd row straight across, G2 = 3rd row
                 at twice the 2nd row's distance (rows 1/2/3, as any G).
  SeamLabels     seam bubbles (G2 green, G1 yellow, G0 kink orange, gap red)
                 and grey CV counts on edges / curves
  SideLenHint    while drawing: green = one patch surely works, yellow =
                 works but near the limit, red = end the side at the dot.
                 Checked (all scale-free): scan deviation (HintTolPct),
                 how far the scan turns (4CV max 100 deg, 6CV max 130
                 deg) and that the patch would not wave. From side 3 on
                 the whole patch (inside and both directions) is checked.
  SCAN TOOLS (buttons in the small panel - before the scan is picked and
  while you work on patches (then the work is left for the tool and comes
  back with the scan where it is now) - and options at 'Click scan'; they
  move / turn only the scan BLOCKS - the customer's files are never
  touched, the scale never changes):
    LoadScans    many STL / OBJ / PLY meshes at once as LINKED blocks
                 (embedded if Rhino refuses the link), each on its own
                 layer under 'Reference Meshes- Msh2classA', own colour; hidden after loading
                 except the last one. PLY point clouds are refused (mesh
                 them first)
    ScanAlign    scans to each other: click a point on the scan that STAYS,
                 then the same point on the scan that MOVES; 3+ pairs =
                 turned + moved (the number = how far off). Fit = automatic,
                 ONLY inside the spheres of AutoFitRadius around the pairs
                 (as AutoFit of Center; the rest of the scans is never
                 used), Fine = gumball. Right click = done, Esc = back. Works as a tree:
                 a scan aligned to another remembers it; aligning a scan
                 again moves every scan hanging on it too. Saved setups
                 keep the new alignment
    ScanCenter   click pairs LEFT / RIGHT on the same features of the
                 visible scans: after every pair ALL scans (one aligned
                 structure, hidden ones too) turn + move so the symmetry plane of the
                 pairs is the MirrorPlane; blue points = the mirrored
                 scans (always shown, MirrorPreview = on/off); the
                 number at every L / R tag = how far the mirrored
                 partner of the other side lies from it (how well the
                 scan sits there), details in the command line; Enter =
                 done, Esc / Cancel = back as before.
                 Tags are pins: the cross-hair is the exact point; the
                 blue cross = the LEFT tag after the mirror, the number at
                 R = its distance to the right tag.
                 In Fine: r1 = L1 mirrored in the present centre, its
                 number = distance to the R1 you clicked (grey cross).
                 Fine (option): a small gumball in WORLD axes on the
                 symmetry plane - drag an arrow = move along X/Y/Z, a ring
                 = turn about it; MovePerPx / DegPerPx = step per pixel of
                 the drag (default 0.02 mm / 0.005 deg). Mirror and tags
                 follow live; all scans move (hidden ones too).
                 Enter = keep, Esc = undo this Fine round. Fine is pinned to
                 the bottom right corner of the view (turns with the view);
                 the yellow cross = the point it turns about.
                 AutoFit (option, in Center and Fine): the mirror is fitted
                 to the scan itself - scan points mirrored and pulled onto
                 the scan. With tags: only the points inside spheres of
                 AutoFitRadius (default 50 mm) around every tag - the
                 places you marked as symmetric; AutoFitRadius 0 = whole
                 scan. Far points (holes, one-sided parts) are left out.
                 After a radius change the spheres show see-through for 3 s;
                 SpherePreview = keep them shown.
                 Order: pairs first (rough), then Fine and AutoFit in any
                 order, as often as you like. After the first Fine /
                 AutoFit the pairs are LOCKED (a new pair would undo that
                 work); ResetFine = back to the result of the pairs and
                 unlocks them
    ScanFloor    3+ points on the floor: turns only about the axis across
                 the symmetry plane, moves only up/down (+ front/back to a
                 clicked point) - the symmetry stays; repeat any time.
                 Click the floor on BOTH sides (not along one sill)
    ScanSetup    apply a saved setup (e.g. front bumper / spoiler / rear),
                 save the present positions under a name, or remove one.
                 Saved only WITH THE SCANS, at once: scan_setups.json in
                 the folder of the scan files (nothing in the .3dm; any
                 file finds them after LoadScans of the same scans). Per
                 scan the 4x4 rotation+move of its block from its 0,0,0
    ScanMirror   mirrored copies of the visible scans as blue points,
                 drawn only (again = off)
  VIEW PANEL (small window at the top right of the view, drag it where
  you like - the place is remembered; click to switch; below them the
  Scans buttons, see SCAN TOOLS):
                 Legend = this short option list (top left);  CV pts =
                 control points of the surface under the mouse, only the
                 ones on the side facing you;  EMap = with the image
                 reflection_matcap_3dPARTdesign_com.png next to the script
                 (if there) the patches are drawn with that matcap, no
                 Rhino EMap window; otherwise Rhino's EMap;  Zebra = Rhino's
                 analysis on the surfaces (not on the scan) - they show
                 what is really on, also when switched on before the
                 command, and stay as you leave them;  Graph = curvature
                 combs on the patch under the mouse (highest tooth = half
                 the mean edge length);  Deviation = dots on all patches,
                 green 0, yellow half ScanTol, red ScanTol and more (blue
                 = scan below), grey no scan; labels: the biggest + and -;
                 nothing to set for either;  Scan = hide the mesh
                 (points still go onto it), shown again at the end;
                 Mirror = real mirrored copies of the surfaces over
                 MirrorPlane (Zebra / EMap on them too), kept up to date,
                 deleted when switched off and when the command ends.
  DISPLAY  while the plugin runs every view uses the display mode
           '3dPARTdesign_ScanCAD' (or 'ScanCAD'); if it is missing it is
           imported from msh2classA_3dPARTdesign_com.ini next to the script and
           stays in your display modes. At the end the views get back the
           modes they had (the ScanCAD mode is not removed).
  SnapPctOfView  catch radius for corners/edges, % of the view (zoom in to
                 click right beside a corner on purpose)
  SURFACES (view panel)   left click = corner, right click = go on,
           UndoPt = last corner back, Esc = cancel.
  Fillet   Side A: click its first and last corner (the edges between are
           found; click a corner in between only to choose the way), right
           click. Side B the same, right click (any number of patches).
           Radius step with preview - Radius, RadiusEnd, Cont, Shape (0 =
           auto); right click = apply, Esc = back to the corners. The big
           patches are extended / cut back, no trim; the fillet is split at
           the corners of both sides, G2 everywhere.
  NPatch   Click the corners round the hole (corners between are found
           along free edges; a side may be part of an edge). Right click =
           build: one clean 6x6 span, trimmed.
           Neighbours with a whole edge on it come to it (DefaultCont).
  GlobalMatch  Seams below their stored level (e.g. after hand edits)
           are brought back; the patch with most broken seams stays.
  ForceMirror  Every patch the mirror plane runs across is cut at it (the
           bigger part stays, no trim); one trimmed at the mirror is rebuilt
           untrimmed. Its side on the plane gets DefaultCont (G2 / G1 / G0).
           TempPoints on the other side are dropped. One UndoSrfEdit.
  Labels   On/off: CV-count and continuity bubbles of finished patches.
  UndoPt / ClearSrf / UndoSrf / UndoSrfEdit
                 last point / unfinished patch / last patch / last
                 patch-button step (FairN, SmthUVN, EvenUV, StrUV, RelaxUV,
                 ToDevUV, PullN, RstN, MatchUVN, FreeEdgeEdt, Fillet, NPatch,
                 GlobalMatch, Del)
  Right click with no corners = finish.  Esc = finish.
------------------------------------------------------------------"""


# -----------------------------------------------------------------------------
# Settings stored between runs (sc.sticky)
# -----------------------------------------------------------------------------

# Command-line options: (name, sticky key, shown values, stored values, default)
# Names say what they do; abbreviations: Srf surface, Msh mesh, Pt(s) point(s),
# Cont continuity, Pct percent, Len length, Opt option.
OPT_LISTS = [
    ("DefaultCont", "cont", ["G0Pos", "G1Tan", "G2Curv"], ["G0", "G1", "G2"], "G2"),
    ("FitSrfTo", "fit", ["ScanMsh", "TempPts", "NoScan"], ["Mesh", "Points", "NoScan"], "Mesh"),
    ("SrfSplit", "spans", ["OneSrf", "AutoSplit"], ["Single", "Auto"], "Single"),
    ("MirrorPlane", "mirror", ["Off", "XZ", "YZ", "XY"], ["Off", "XZ", "YZ", "XY"], "Off"),
    ("CornerFlow", "flow", ["KinkOK", "Smooth"], ["Kink", "Smooth"], "Kink"),
]
OPT_TOGGLES = [   # (name, sticky key, default)
    ("SeamLabels", "labels", True),
    ("SideLenHint", "sidehint", True),
]


class SettingsMixin(object):
    def add_options(self, gp):
        """Put all options on the command line; returns name -> handler info."""
        ui = {}
        for (nm, key, shown, vals, dflt) in OPT_LISTS:
            cur = opt_get(key, dflt)
            gp.AddOptionList(nm, shown, vals.index(cur) if cur in vals else vals.index(dflt))
            ui[nm] = ("list", key, vals)
        nums = [("ScanTol", "autotol", self.s_tol(), 1e-6, 1e6, True),
                ("HintTolPct", "tolpct", self.s_tolpct(), 0.01, 10.0, self.s_sidehint()),
                ("MirrorSnapDist", "mtol", self.s_mtol(), 0.0, 1e6, self.s_mirror() != "Off"),
                ("SnapPctOfView", "snap", self.s_snap(), 0.0, 20.0, True)]
        for (nm, key, val, lo, hi, show) in nums:
            if show:
                o = ric.OptionDouble(val, lo, hi); gp.AddOptionDouble(nm, o); ui[nm] = ("num", key, o)
        for (nm, key, dflt) in OPT_TOGGLES:
            o = ric.OptionToggle(bool(opt_get(key, dflt)), "No", "Yes"); gp.AddOptionToggle(nm, o)
            ui[nm] = ("tog", key, o)
        fm_ = getattr(self, "fillet_mode", None)
        if self.corners:
            gp.AddOption("UndoPt"); gp.AddOption("ClearSrf")
        elif fm_ and (fm_["pts"] or fm_.get("ptsA")):
            gp.AddOption("UndoPt")
        if self.patches: gp.AddOption("UndoSrf")
        if self.smooth_undo: gp.AddOption("UndoSrfEdit")
        gp.AddOption("Help")
        return ui

    def handle_option(self, opt, ui):
        name = opt.EnglishName
        h = ui.get(name)
        if h is not None:
            if h[0] == "list": opt_set(h[1], h[2][opt.CurrentListOptionIndex])
            else: opt_set(h[1], h[2].CurrentValue)
            return
        if name == "UndoPt":
            if getattr(self, "fillet_mode", None): self.fillet_undo_pt()
            else: self.undo()
        elif name == "ClearSrf": self.reset_polygon()
        elif name == "UndoSrf": self.undo_patch()
        elif name == "UndoSrfEdit": self.undo_smooth()
        elif name == "Help": print(HELP)

    def s_sidehint(self): return bool(opt_get("sidehint", True))

    def s_fit(self): return opt_get("fit", "Mesh")

    def s_spans(self): return opt_get("spans", "Single")


    def s_snap(self): return opt_get("snap", 3.0)

    def s_legend(self): return bool(opt_get("legend", True))

    def s_showcv(self): return bool(opt_get("showcv", False))

    def pick_px(self, vp):
        """Catch radius as % of the view: same on screen at any zoom, so
        zooming in lets you click NEXT to a corner on purpose."""
        try:
            sz = vp.Size
            diag = math.hypot(sz.Width, sz.Height)
        except Exception:
            diag = 1500.0
        snap = max(SNAP_PX, self.s_snap() / 100.0 * diag)
        return max(PICK_PX, 0.4 * snap), snap

    def s_cont(self): return opt_get("cont", "G2")

    def s_flow(self): return opt_get("flow", "Kink")

    def s_labels(self): return bool(opt_get("labels", True))

    def s_mirror(self): return opt_get("mirror", "Off")

    def s_mtol(self):
        v = opt_get("mtol", None)
        if v is None:
            v = 3.0 * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
        return v

    def s_tolpct(self): return opt_get("tolpct", 0.2)

    def s_tol(self):
        """Allowed scan deviation in model units (CVs, AutoSplit, leash):
        0.5 mm unless you set it."""
        default = 0.5 * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
        return opt_get("autotol", default)


# -----------------------------------------------------------------------------
# Viewport preview: polygon, bubbles, labels, legend, CV nets
# -----------------------------------------------------------------------------

class DrawMixin(object):
    def on_move(self, sender, e):
        self.on_move_view(sender, e)
        if self.view_hot is not None:
            self.hover = None; self.hover_bubble = None; self.hover_srf = None
            return
        if getattr(self, "fillet_mode", None):
            self.hover = None; self.hover_bubble = None; self.hover_srf = None; self.hint = None
            try:
                self.fillet_mode["hot"] = self._fm_corner_at(xf_tuple(e.Viewport), e.WindowPoint.X, e.WindowPoint.Y)
            except Exception:
                self.fillet_mode["hot"] = None
            return
        try:
            vp = e.Viewport
            m = xf_tuple(vp)
            pk, sn = self.pick_px(vp)
            hs = self.visible_hits(hit_test(self.cands, m, e.WindowPoint.X, e.WindowPoint.Y, pk, sn), vp)
            self.hover = hs[0] if hs else None
            self.hover_bubble = self.bubble_at(m, e.WindowPoint.X, e.WindowPoint.Y)
            if self.corners and self.s_sidehint():
                self.update_side_hint(e)
            else:
                self.hint = None
            if not self.corners and self.hover is None and self.hover_bubble is None:
                self.update_srf_buttons(vp, m, e.WindowPoint.X, e.WindowPoint.Y)
            else:
                self.hover_srf = None
            if self.s_showcv() or self.view_on("graph"):     # CV points and Graph: the patch under the mouse
                if self.hover_srf is not None:
                    self.cv_srf = self.hover_srf[0]
                else:
                    hs_ = self.surface_under(vp, m, e.WindowPoint.X, e.WindowPoint.Y)
                    self.cv_srf = hs_[0] if hs_ else None
        except Exception:
            self.hover = None
            self.hover_bubble = None
            self.hover_srf = None

    def update_side_hint(self, e):
        """Can ONE patch follow the scan along the side being drawn?"""
        x, y = e.WindowPoint.X, e.WindowPoint.Y
        last = getattr(self, "hint_xy", None)
        n = len(self.corners)
        if last is not None and last[2] == n and last[3] == len(self.pend):
            if abs(last[0] - x) + abs(last[1] - y) < 6:
                return
            if time.time() - getattr(self, "hint_t", 0.0) < max(0.08, 1.5 * getattr(self, "hint_dt", 0.0)):
                return          # still busy with the last one: keep the mouse fluid
        self.hint_xy = (x, y, n, len(self.pend))
        t0 = time.time()
        try:
            self._side_hint(n)
        finally:
            self.hint_t = time.time(); self.hint_dt = self.hint_t - t0

    def _side_hint(self, n):
        if n < 4 and self.cur_pt is None:
            self.hint = None; return
        path = [self.corners[-1]["pt"]] + list(self.pend) + ([self.corners[0]["pt"]] if n == 4 else [self.cur_pt])
        if path[0].DistanceTo(path[-1]) < 1e-6:
            self.hint = None; return
        self.hint_n = self.hint_normal([c["pt"] for c in self.corners] + list(self.pend) + [path[-1]])
        pts_ = [c["pt"] for c in self.corners] + [path[-1]]
        span_ = max(p.DistanceTo(q) for p in pts_ for q in pts_)
        steps_ = [pts_[i].DistanceTo(pts_[i + 1]) for i in range(len(pts_) - 1)]
        short_ = min([s_ for s_ in steps_ if s_ > 1e-9] or [span_])
        # search depth along 'up': about the patch WIDTH (narrow fillet: small)
        self.hint_R = max(0.5 * min(span_, 3.0 * short_), 1e-3 * self.size)
        crv = smooth_curve(path); dom = crv.Domain
        prof = [T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / 30.0)))) for i in range(31)]
        lvl = 2 if self.s_cont() == "G2" else (1 if self.s_cont() == "G1" else 0)
        cv = [max(4, 2 * (lvl + 1), self.dir_cv(dd) or 0) for dd in (0, 1)]
        along = cv[(n - 1) % 2]
        pct = self.s_tolpct()
        s, why, reach = single_span_reach(prof, along - 1, pct)
        rp = None
        if reach < 0.999:
            rp = self.hint_proj(crv.PointAt(dom.ParameterAt(reach)))
        txt = why if s > GREEN_AT else ""
        if rp is not None:
            txt = "end here: " + why
        # whole patch, once the 4 sides are known (side 3 = cursor; 4 = straight back)
        pc = None; ptxt = ""
        if n >= 3 and s <= 1.0:
            try:
                ps, pwhy, pc = self.patch_hint(n, crv, path[-1], cv)
                if ps > GREEN_AT:
                    ptxt = "patch: " + pwhy
                s = max(s, ps)
            except Exception:
                pass
        self.hint = (hint_state(s), rp, txt, along, pc, ptxt)

    def side_poly(self, k, n_pts=21):
        """Side k (drawn) as tuples, corner k -> corner k+1."""
        a, b = self.corners[k], self.corners[(k + 1) % 4]
        pick = None if self.shape.get(k) else self.common(a, b)
        if pick is not None:
            c = self.cands[pick[0]]; ta, tb = pick[1], pick[2]
            return [T(c.crv.PointAt(ta + (tb - ta) * i / (n_pts - 1.0))) for i in range(n_pts)]
        crv = self.side_curve(k); dom = crv.Domain
        out = []
        for i in range(n_pts):          # scan side: lies on the scan once built
            out.append(T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / (n_pts - 1.0))))))
        return out

    def warm_hint(self, pt):
        """Run the side-hint pipeline once on a dummy side right after the first
        corner, behind a 'please wait' - so the first mouse move is not slow."""
        try:
            q = rg.Point3d(pt.X + 0.01 * self.size, pt.Y, pt.Z)
            self.hint_n = self.hint_normal([pt, q])
            self.hint_R = 0.01 * self.size
            crv = smooth_curve([pt, q]); dom = crv.Domain
            prof = [T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / 30.0)))) for i in range(31)]
            single_span_reach(prof, 5, self.s_tolpct())
            self.visible(pt, sc.doc.Views.ActiveView.ActiveViewport)
        except Exception:
            pass

    def hint_normal(self, pts):
        """Average scan normal at the clicked points, turned like corner 1's
        face: 'up' of the patch being drawn."""
        acc = (0.0, 0.0, 0.0); ref = None
        if not hasattr(self.mesh, "ClosestMeshPoint"): return None
        for p in pts:
            mp = self.mesh.ClosestMeshPoint(p, 0.0)
            if mp is None: continue
            f = self.mesh.FaceNormals[mp.FaceIndex]; nn = (f.X, f.Y, f.Z)
            if ref is None: ref = nn
            acc = v_add(acc, nn if v_dot(nn, ref) >= 0 else v_mul(nn, -1.0))
        return v_unit(acc) if v_len(acc) > 1e-12 else None

    def hint_proj(self, q):
        """Scan point for the hint: the face turned up-ish (not a rib wall,
        not the back of a thin part) straight below / above q along the patch
        'up'. Closest-point queries only (fast on big scans): first q itself,
        then q moved along 'up' until the hit is an up-facing face."""
        n = getattr(self, "hint_n", None)
        m = self.mesh
        if n is None:
            c = m.ClosestPoint(q)
            return c if c.IsValid else q
        h = 0.02 * getattr(self, "hint_R", 0.05 * self.size)
        best = None
        for k in (0.0, -0.5, 0.5, -1.0, 1.0, -2.0, 2.0, -4.0, 4.0):
            p = rg.Point3d(q.X + n[0] * k * h, q.Y + n[1] * k * h, q.Z + n[2] * k * h)
            mp = m.ClosestMeshPoint(p, 0.0)
            if mp is None: continue
            f = m.FaceNormals[mp.FaceIndex]
            if f.X * n[0] + f.Y * n[1] + f.Z * n[2] < 0.3: continue
            c = mp.Point
            dv_ = (c.X - q.X, c.Y - q.Y, c.Z - q.Z); a_ = v_dot(dv_, n)
            lat = v_len(v_sub(dv_, v_mul(n, a_)))
            if best is None or lat < best[0]: best = (lat, c)
            if lat < 0.5 * h: break
        if best is not None:
            return best[1]
        c = m.ClosestPoint(q)
        return c if c.IsValid else q

    def patch_hint(self, n, crv, cursor, cv):
        """Score of the whole future patch: Coons interior on the scan."""
        sides = [self.side_poly(k) for k in range(n - 1)]
        dom = crv.Domain
        sides.append([T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / 20.0)))) for i in range(21)])
        if n == 3:
            a_, b_ = T(cursor), T(self.corners[0]["pt"])
            sides.append([T(self.hint_proj(P3(v_lerp(a_, b_, i / 20.0)))) for i in range(21)])

        def fn(pts):
            m = len(pts) - 1
            def f(t):
                x = max(0.0, min(1.0, t)) * m; k = min(int(x), m - 1)
                return v_lerp(pts[k], pts[k + 1], x - k)
            return f
        fs = [fn(p) for p in sides]
        N = 9; grid = []
        for i in range(N):
            row = []
            for j in range(N):
                p = coons_point(fs, i / (N - 1.0), j / (N - 1.0))
                if 0 < i < N - 1 and 0 < j < N - 1:
                    p = T(self.hint_proj(P3(p)))
                row.append(p)
            grid.append(row)
        s, why = patch_score(grid, cv[0] - 1, cv[1] - 1, self.s_tolpct())
        return s, why, P3(grid[N // 2][N // 2])

    def bubble_at(self, m, sx, sy):
        for k, bp in self.bubbles.items():
            bx, by = to_screen(m, T(bp))
            if abs(bx - sx) < 55 and abs(by - sy) < 11:
                return (k, bx, by)
        return None

    def on_down(self, sender, e):
        try:
            self.down = (e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
        except Exception:
            self.down = None
        # view buttons act on mouse down: a click off the scan never returns a
        # point from the scan-constrained GetPoint, but the mouse down comes
        self.view_clicked = False
        try:
            right = bool(getattr(e, "RightButtonDown", False))
            k = None if right else self.view_button_at(e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
            if k is not None:
                self.view_clicked = True
                self.toggle_view(k)
        except Exception:
            pass


    def visible(self, p, vp):
        """Is point p (on or at the scan) on the side of the part you look at?
        The scan face nearest to it must face the camera - an edge of a patch
        on the far side of a thin part (or a label there) is not. Fast: one
        closest-point query on the scan."""
        try:
            mp = self.mesh.ClosestMeshPoint(p, 0.0)
            if mp is None: return True
            n = self.mesh.FaceNormals[mp.FaceIndex]
            if vp.IsParallelProjection:
                c = vp.CameraDirection; vv = (-c.X, -c.Y, -c.Z)
            else:
                cam = vp.CameraLocation; vv = (cam.X - p.X, cam.Y - p.Y, cam.Z - p.Z)
            return n.X * vv[0] + n.Y * vv[1] + n.Z * vv[2] > 0
        except Exception:
            return True

    def visible_hits(self, hits, vp):
        """Edge / curve hits only on the side you look at."""
        out = []
        for h in hits or []:
            c = self.cands[h["ci"]]
            p = c.crv.PointAt(h["t"]) if h.get("end") is None else rg.Point3d(c.p0 if h["end"] == 0 else c.p1)
            if self.visible(p, vp): out.append(h)
        return out

    def draw_cv_net(self, d, vp):
        """CV net of the surface under the mouse only, and only the CVs on
        the side facing you (the others would show through the part)."""
        oid = getattr(self, "cv_srf", None)
        item = getattr(self, "cvnets", {}).get(oid) if oid is not None else None
        if item is None: return
        net, nrm = item
        try:
            par = vp.IsParallelProjection
            cam = vp.CameraLocation; cdir = vp.CameraDirection
        except Exception:
            return
        def front(i, j):
            p = net[i][j]; n = nrm[i][j]
            if par: vv = (-cdir.X, -cdir.Y, -cdir.Z)
            else: vv = (cam.X - p.X, cam.Y - p.Y, cam.Z - p.Z)
            return n[0] * vv[0] + n[1] * vv[1] + n[2] * vv[2] > 0
        nu = len(net); nv = len(net[0]) if nu else 0
        vis = [[front(i, j) for j in range(nv)] for i in range(nu)]
        cl = col(60, 60, 60); cp = col(0, 0, 0)
        for i in range(nu):
            for j in range(nv):
                if not vis[i][j]: continue
                if i + 1 < nu and vis[i + 1][j]: d.DrawDottedLine(net[i][j], net[i + 1][j], cl)
                if j + 1 < nv and vis[i][j + 1]: d.DrawDottedLine(net[i][j], net[i][j + 1], cl)
                d.DrawPoint(net[i][j], cp)

    def draw_mirror_hint(self, d):
        """MirrorPlane on: when the cursor is within MirrorSnapDist of the
        plane, show where the point / corner will snap."""
        ax = MIRROR_AXIS.get(self.s_mirror())
        p = getattr(self, "cur_pt", None)
        if ax is None or p is None or getattr(self, "view_hot", None): return
        c = [p.X, p.Y, p.Z]
        if abs(c[ax]) > self.s_mtol(): return
        c[ax] = 0.0
        q = rg.Point3d(c[0], c[1], c[2])
        d.DrawLine(p, q, C_RED, 2)
        d.DrawDot(q, "mirror", C_RED, col(255, 255, 255))

    def side_color(self, k):
        return C_RED if k % 2 == 0 else C_BLUE

    def on_draw(self, sender, e):
        try:
            d = e.Display
            n = len(self.corners)
            self.bubbles = {}
            self.draw_matcap(d)                 # first: everything else on top
            if self.view_on("deviation"):
                self.draw_deviation(d)
            if self.view_on("graph"):
                self.draw_graph(d)
            if self.s_legend():
                self.draw_legend(d)
            if self.s_showcv():
                self.draw_cv_net(d, e.Viewport)
            self.draw_mirror_hint(d)
            self.draw_srf_buttons(d, e.Viewport)
            self.draw_match(d, e.Viewport)
            self.draw_flash(d)
            self.draw_edge_mode(d, e.Viewport)
            self.draw_fillet_mode(d, e.Viewport)
            self.draw_view_buttons(d, e.Viewport)
            if self.s_labels():
                vp_ = e.Viewport
                for (p, txt, cc) in self.markers:
                    if self.visible(p, vp_):
                        d.DrawDot(p, txt, cc, col(0, 0, 0))
                for c in self.cands:
                    if c.ncv and self.visible(c.lbl, vp_):
                        d.DrawDot(c.lbl, str(c.ncv), C_GRAY, col(255, 255, 255))
            for k in range(n - 1):
                self.draw_side(d, k, self.corners[k], self.corners[k + 1])
            for k, pts in self.shape.items():
                for p in pts:
                    d.DrawPoint(p, self.side_color(k))
            if n > 0:
                cur = [self.corners[-1]["pt"]] + list(self.pend)
                if n == 4: cur += [self.corners[0]["pt"]]
                elif self.cur_pt is not None: cur += [self.cur_pt]   # off the scan: no rubber band
                h = getattr(self, "hint", None) if len(cur) > 1 else None
                if h is not None:
                    hc = {"green": col(0, 170, 60), "yellow": col(235, 190, 0), "red": col(220, 30, 30)}[h[0]]
                    draw_smooth(d, cur, hc, 3)
                    if h[1] is not None:
                        d.DrawDot(h[1], h[2], hc, col(255, 255, 255))
                    elif h[2]:
                        d.DrawDot(self.cur_pt or e.CurrentPoint, h[2], hc, col(0, 0, 0))
                    if h[4] is not None and h[5]:
                        d.DrawDot(h[4], h[5], hc, col(0, 0, 0))
                elif len(cur) > 1:
                    draw_smooth(d, cur, self.side_color(n - 1), 2)
                if n == 4:
                    c4 = smooth_curve(cur)
                    m4 = c4.PointAt(c4.Domain.ParameterAt(0.5))
                    a_, b_ = {"on": self.corners[3]["on"]}, {"on": self.corners[0]["on"]}
                    kind = "edge" if (not self.pend and self.common(a_, b_)) else "scan"
                    self.draw_bubble(d, 3, m4, kind)
                if self.cur_pt is not None and n < 4 and getattr(self, "hover", None) is None:
                    try:
                        sn_ = self.snap_to_edges(self.cur_pt)
                    except Exception:
                        sn_ = None
                    if sn_ is not None:          # BEFORE the click: this would go onto the edge
                        d.DrawLine(self.cur_pt, sn_[0], col(255, 140, 0), 2)
                        d.DrawDot(self.cur_pt, "{} to edge - snaps".format(fl(sn_[1])), col(255, 140, 0),
                                  col(0, 0, 0))
                for p in self.pend:
                    near_ = None
                    try:
                        near_ = self.snap_to_edges(p)
                    except Exception:
                        pass
                    if near_ is not None:
                        d.DrawPoint(p, Rhino.Display.PointStyle.RoundControlPoint, 6, col(255, 140, 0))
                        d.DrawLine(p, near_[0], col(255, 140, 0), 1)
                    else:
                        d.DrawPoint(p, self.side_color(n - 1))
                if n == 3 and self.cur_pt is not None:
                    d.DrawLine(self.cur_pt, self.corners[0]["pt"], self.side_color(3), 1)
            for i, cc in enumerate(self.corners):
                d.DrawDot(cc["pt"], str(i + 1), col(40, 40, 40), col(255, 255, 255))
            if n == 4:
                cx = sum(c_["pt"].X for c_ in self.corners) / 4.0
                cy = sum(c_["pt"].Y for c_ in self.corners) / 4.0
                cz = sum(c_["pt"].Z for c_ in self.corners) / 4.0
                d.DrawDot(rg.Point3d(cx, cy, cz), self.size_text(), col(255, 255, 255), col(0, 0, 0))
            for i, p in enumerate(self.fit_pts):
                d.DrawDot(p, "P{}".format(i + 1), C_PT, col(255, 255, 255))
            h = self.hover
            if h is not None and n < 4 and h["ci"] < len(self.cands):
                c = self.cands[h["ci"]]
                draw_poly(d, c.pts3, C_HOVER, 2)
                p = c.crv.PointAt(h["t"])
                at_end = min(p.DistanceTo(c.crv.PointAtStart), p.DistanceTo(c.crv.PointAtEnd)) < 1e-6 * max(1.0, c.length)
                txt = "Corner" if at_end else ("Edge" if c.kind == "edge" else "Curve")
                if 0 < n < 4 and not self.pend and any(x[0] == h["ci"] for x in self.corners[-1]["on"]):
                    txt = "side {}: {}".format(n, self.side_text(n - 1, c.kind))
                d.DrawDot(p, txt, C_HOVER, col(0, 0, 0))
        except Exception:
            pass

    def draw_legend(self, d):
        """Command-line options cannot have tooltips in Rhino - this short
        legend in the corner of the view explains them instead."""
        lines = [
            ("msh2classA options (Legend button in the view panel hides this, Help = more)", None),
            ("DefaultCont = {}".format(self.s_cont()), "continuity for new sides; side bubbles change one side"),
            ("FitSrfTo = {}".format({"Mesh": "ScanMsh", "Points": "TempPts"}.get(self.s_fit(), self.s_fit())),
             "ScanMsh whole scan | TempPts clicked points | NoScan edges only"),
            ("SrfSplit = {}".format("AutoSplit" if self.s_spans() == "Auto" else "OneSrf"),
             "one patch | strip cut into single-span patches"),
            ("ScanTol = {:g}".format(self.s_tol()), "allowed scan deviation (also the Deviation colours)"),
            ("HintTolPct = {:g}".format(self.s_tolpct()), "SideLenHint deviation, % of the size"),
            ("MirrorPlane = {}".format(self.s_mirror()), "symmetry plane; sides within MirrorSnapDist snap onto it"),
            ("CornerFlow = {}".format("Smooth" if self.s_flow() == "Smooth" else "KinkOK"), "at a neighbour's corner: new side may kink / continues its outline"),
            ("SideLenHint = {}".format("Yes" if self.s_sidehint() else "No"), "green sure / yellow near limit / red end side at dot"),
            ("SnapPctOfView = {:g}%".format(self.s_snap()), "corner catch radius - zoom in to click beside a corner"),
            ("UndoSrfEdit", "undo the last patch-button step"),
            ("PATCH BUTTONS (hover a finished patch, one step per click)", None),
            ("  CVs move:  N = up/down (off the patch)   UV = sideways (in the patch)   UVN = both", None),
            ("  EvenUV", "CV rows to even spacing; shape stays"),
            ("  StrUV", "CV rows straight seen from above; shape stays"),
            ("  RelaxUV", "less skew: CV layout as the four edges imply; shape stays"),
            ("  PullN", "fit to the scan along the normal; CV layout stays"),
            ("  FairN", "smoother shape: waves, noise; CV layout stays"),
            ("  RstN", "smoothest the edges allow (no scan) - then PullN"),
            ("  SmthUVN", "FairN + EvenUV + StrUV at once, strongest"),
            ("  ToDevUV", "a CV row onto the place furthest from the scan"),
            ("  MatchUVN", "after hand edits: neighbours come back to this patch"),
            ("  FreeEdgeEdt", "click a free edge; from corner to corner = middle only, beside it = whole edge"),
            ("  Del", "delete the patch (UndoSrfEdit brings it back)"),
            ("  every button keeps each seam at its G (built / MatchUVN)", None),
            ("  colours: green = fine, yellow = could be better, red = click it (white = no measure)", None),
        ]
        hs = getattr(self, "hover_srf", None)
        if hs is not None and hs[2]:
            q = self.patch_quality(hs[0])
            if q: lines.append(("  " + q["text"], None))
        y = 30
        try:      # light panel behind the text: readable over any scan colour
            rect = sd.Rectangle(6, 24, 8 + 7 * max(len(a if b is None else "{:<22} {}".format(a, b)) for (a, b) in lines),
                                16 * len(lines) + 10)
            d.Draw2dRectangle(rect, sd.Color.FromArgb(200, 255, 255, 255), 1, sd.Color.FromArgb(200, 255, 255, 255))
        except Exception:
            pass
        for (a, b) in lines:
            txt = a if b is None else "{:<22} {}".format(a, b)
            for (dx, dy, c) in ((1, 1, col(255, 255, 255)), (0, 0, col(20, 20, 20))):
                try:
                    d.Draw2dText(txt, c, rg.Point2d(12 + dx, y + dy), False, 12, "Consolas")
                except Exception:
                    d.Draw2dText(txt, c, rg.Point2d(12 + dx, y + dy), False, 12)
            y += 16

    def draw_bubble(self, d, k, pt, kind):
        if kind == "scan" and self.is_mirror_side(k):
            kind = "mirror"
        hb = getattr(self, "hover_bubble", None)
        txt = self.side_text(k, kind)
        if hb is not None and hb[0] == k:
            d.DrawDot(pt, txt, C_HOVER, col(0, 0, 0))
            try:
                d.DrawDot(float(hb[1]), float(hb[2] + 24), self.bubble_tip(k), col(255, 255, 225), col(0, 0, 0))
            except Exception:
                try:
                    d.Draw2dText(self.bubble_tip(k), col(0, 0, 0), rg.Point2d(hb[1], hb[2] + 24), True, 13)
                except Exception:
                    pass
        else:
            d.DrawDot(pt, txt, self.side_color(k), col(255, 255, 255))
        self.bubbles[k] = pt

    def draw_side(self, d, k, a, b):
        pick = None if self.shape.get(k) else self.common(a, b)
        if pick is None and self.shape.get(k):
            crv = self.side_curve(k); dom = crv.Domain
            pts = [crv.PointAt(dom.ParameterAt(i / 30.0)) for i in range(31)]
            draw_poly(d, pts, self.side_color(k), 2)
            self.draw_bubble(d, k, pts[15], "scan")
            return
        if pick is None:
            d.DrawLine(a["pt"], b["pt"], self.side_color(k), 2)
            mid = rg.Point3d((a["pt"].X + b["pt"].X) / 2, (a["pt"].Y + b["pt"].Y) / 2, (a["pt"].Z + b["pt"].Z) / 2)
            self.draw_bubble(d, k, mid, "scan")
            return
        ci, ta, tb = pick
        c = self.cands[ci]
        pts = [c.crv.PointAt(ta + (tb - ta) * i / 20.0) for i in range(21)]
        draw_poly(d, pts, self.side_color(k), 4)
        self.draw_bubble(d, k, pts[10], c.kind)


# -----------------------------------------------------------------------------
# View buttons (top right): what the patches look like while working
# -----------------------------------------------------------------------------

# (key, label, sticky default). Zebra / EMap / CV only on surfaces, Scan = the mesh.
VIEW_BTN = (("legend", "Legend", True), ("showcv", "CV pts", False), ("zebra", "Zebra", False),
            ("emap", "EMap", False), ("graph", "Graph", False), ("deviation", "Deviation", False),
            ("scanvis", "Scan", True), ("mirrorview", "Mirror", False),
            ("labels", "Labels", True))      # = SeamLabels: the CV-count and continuity bubbles of the patches


def analysis_modes():
    """Rhino's built-in Zebra and EMap analysis modes (None if not found)."""
    out = {}
    try:
        VAM = Rhino.Display.VisualAnalysisMode
        out["zebra"] = VAM.Find(VAM.RhinoZebraStripeAnalysisModeId)
        out["emap"] = VAM.Find(VAM.RhinoEmapAnalysisModeId)
    except Exception:
        pass
    return out


class ViewMixin(object):
    def view_on(self, key):
        if key == "scanvis":
            return getattr(self, "scan_visible", True)
        if key == "mirrorview":
            return bool(getattr(self, "mirror_view", False))
        if key in ("zebra", "emap"):
            return self.__dict__.setdefault("analysis_state", {}).get(key, False)
        dflt = dict((k, d) for (k, _, d) in VIEW_BTN)[key]
        return bool(opt_get(key, dflt))

    view_hot = None            # key of the view button under the mouse
    cur_pt = None              # scan point under the mouse (None = off the scan)
    last_mouse = None          # (viewport, x, y) of the last mouse move

    def _view_btn_rects(self, vp):
        """(key, label, x0, y0, w, h) in viewport pixels, top right."""
        try:
            W = vp.Size.Width
        except Exception:
            W = 1200
        for i, (key, label, _) in enumerate(VIEW_BTN):
            yield key, label, W - 112, 8 + 24 * i, 104, 20

    # ---- floating panel (Eto) at the top right of the view ----------------
    # Buttons drawn INTO the viewport only get clicks where Rhino delivers
    # them (not over the scan / background with the scan-constrained pick).
    # A small panel window always gets its clicks, and while the mouse is on
    # it the viewport sees no mouse moves: no hint work, no points.
    panel = None

    def open_panel(self):
        try:
            import Eto.Forms as ef
            import Eto.Drawing as ed
            import Rhino.UI as rui
        except Exception:
            self.panel = None; return False
        try:
            f = ef.Form()
            f.Title = "msh2classA view"
            f.WindowStyle = ef.WindowStyle.Utility
            f.Resizable = False
            f.Minimizable = False; f.Maximizable = False
            f.ShowInTaskbar = False
            try: f.Owner = rui.RhinoEtoApp.MainWindow       # stays above Rhino, not above other apps
            except Exception:
                try: f.Topmost = True
                except Exception: pass
            lay = ef.DynamicLayout()
            lay.Padding = ed.Padding(4); lay.Spacing = ed.Size(2, 2)
            self.panel_btns = {}
            for (key, label, _) in VIEW_BTN:
                b = ef.Button()
                b.Text = self._btn_text(key, label)
                b.Click += self._make_click(key)
                lay.AddRow(b)
                self.panel_btns[key] = (b, label)
            surface_tool_buttons(ef, lay)
            scan_tool_buttons(ef, lay)
            cad_services_button(ef, lay)
            f.Content = lay
            loc = sc.sticky.get("M2CA_panel_loc")
            f.Show()
            if loc:
                f.Location = ed.Point(loc[0], loc[1])
            else:
                try:
                    r = sc.doc.Views.ActiveView.ScreenRectangle
                    f.Location = ed.Point(int(r.Right - f.Width - 12), int(r.Top + 12))
                except Exception:
                    pass
            self.panel = f
            return True
        except Exception:
            self.panel = None
            return False

    def _btn_text(self, key, label):
        return "{}   {}".format(label, "ON" if self.view_on(key) else "off")

    def _make_click(self, key):
        def h(sender, e):
            try:
                self.toggle_view(key)
                self.update_panel()
            except Exception:
                pass
        return h

    def update_panel(self):
        for key, (b, label) in getattr(self, "panel_btns", {}).items():
            try: b.Text = self._btn_text(key, label)
            except Exception: pass

    def close_panel(self):
        f = self.panel
        if f is None: return
        try:
            sc.sticky["M2CA_panel_loc"] = (f.Location.X, f.Location.Y)
        except Exception:
            pass
        try: f.Close()
        except Exception: pass
        self.panel = None

    def view_button_at(self, vp, sx, sy):
        if self.panel is not None: return None
        for key, label, x0, y0, w, h in self._view_btn_rects(vp):
            if x0 <= sx <= x0 + w and y0 <= sy <= y0 + h:
                return key
        return None

    def on_move_view(self, sender, e):
        """Mouse position + hover of the view buttons."""
        try:
            self.last_mouse = (e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
            hot = self.view_button_at(e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
            self.cur_pt = None if hot else e.Point          # constrained to the scan by Rhino (fast)
        except Exception:
            hot = None; self.cur_pt = None
        if hot != self.view_hot:
            self.view_hot = hot
            try:
                Rhino.UI.MouseCursor.SetToolTip("click: {} on/off".format(
                    dict((k, l) for (k, l, _) in VIEW_BTN)[hot]) if hot else "")
            except Exception:
                pass

    def click_view_button(self):
        """GetPoint returned a point: was that click a view button (already
        switched in on_down)? Then it is not a corner."""
        if getattr(self, "view_clicked", False):
            self.view_clicked = False
            return True
        return False

    def draw_view_buttons(self, d, vp):
        if self.panel is not None: return
        for key, label, x0, y0, w, h in self._view_btn_rects(vp):
            on = self.view_on(key)
            hot = key == self.view_hot
            fill = col(40, 170, 70) if on else col(250, 250, 250)
            if hot: fill = col(90, 200, 110) if on else col(255, 225, 120)
            txt = "{}  {}".format(label, "ON" if on else "off")
            tc = col(255, 255, 255) if on else col(0, 0, 0)
            try:
                rect = sd.Rectangle(int(x0), int(y0), int(w), int(h))
                d.Draw2dRectangle(rect, col(30, 30, 30), 2 if hot else 1, fill)
            except Exception:
                pass
            try:
                d.Draw2dText(txt, tc, rg.Point2d(x0 + w / 2.0, y0 + h / 2.0), True, 12)
            except Exception:
                pass

    def toggle_view(self, key):
        if key == "scanvis":
            self.scan_visible = not getattr(self, "scan_visible", True)
            sid = getattr(self, "scan_id", None)
            if sid is not None:
                if self.scan_visible: sc.doc.Objects.Show(sid, True)
                else: sc.doc.Objects.Hide(sid, True)
            print("  Scan {} (points still snap onto it)".format("shown" if self.scan_visible else "hidden"))
        elif key == "mirrorview":
            if MIRROR_AXIS.get(self.s_mirror()) is None:
                print("  Mirror view: set MirrorPlane first (XZ / YZ / XY)")
            else:
                self.mirror_view = not getattr(self, "mirror_view", False)
                self.update_mirror_view()
                print("  Mirror view {}".format("ON: mirrored copies of the surfaces (removed again when off / at the end)"
                                               if self.mirror_view else "off: copies removed"))
        elif key == "emap" and matcap_path():
            # own EMap: the patches drawn with YOUR matcap (no Rhino EMap window)
            st = self.__dict__.setdefault("analysis_state", {})
            st["emap"] = not st.get("emap", False)
            self.matcap_meshes = {}
            print("  EMap {} (matcap {})".format("ON" if st["emap"] else "off", MATCAP_FILE))
        elif key in ("zebra", "emap"):
            st = self.__dict__.setdefault("analysis_state", {})
            st[key] = not st.get(key, False)
            self.apply_analysis(key)
        else:
            opt_set(key, not self.view_on(key))
        sc.doc.Views.Redraw()

    # ---- Graph: curvature combs on the patches (all settings automatic) ----
    GRAPH_ISO = (0.1, 0.3, 0.5, 0.7, 0.9)      # iso lines per direction
    GRAPH_N = 32                               # samples per iso line

    def _patch_nurbs(self, oid):
        obj = sc.doc.Objects.FindId(oid)
        if obj is None: return None
        g = obj.Geometry
        try:
            if isinstance(g, rg.Brep):
                return g.Faces[0].UnderlyingSurface().ToNurbsSurface()
            return g.ToNurbsSurface()
        except Exception:
            return None

    def draw_graph(self, d):
        """Curvature combs along iso lines of the patch under the mouse (as the
        CV points). The highest tooth (95 % of them lower) is half the mean
        length of the patch's edges; very sharp places are capped."""
        oid = getattr(self, "cv_srf", None)
        if oid is None: return
        cache = self.__dict__.setdefault("graph_cache", {})
        if oid not in cache:
            ns = self._patch_nurbs(oid)
            if ns is None: return
            lines = []; ks = []
            for dirn in (0, 1):
                dom_c = ns.Domain(1 - dirn)
                for f in self.GRAPH_ISO:
                    try:
                        crv = ns.IsoCurve(dirn, dom_c.ParameterAt(f))
                    except Exception:
                        crv = None
                    if crv is None: continue
                    dom = crv.Domain
                    cpar = dom_c.ParameterAt(f)
                    pts = []
                    for i in range(self.GRAPH_N + 1):
                        t = dom.ParameterAt(i / float(self.GRAPH_N))
                        kv = crv.CurvatureAt(t)
                        # only the NORMAL curvature (the bending of the surface): the sideways bending of
                        # the iso line inside the surface would lay the teeth flat
                        n_ = ns.NormalAt(t, cpar) if dirn == 0 else ns.NormalAt(cpar, t)
                        kn = kv * n_
                        v_ = rg.Vector3d(n_); v_ *= kn
                        pts.append((crv.PointAt(t), v_)); ks.append(abs(kn))
                    lines.append(pts)
            edges = []
            for dirn in (0, 1):
                dom_c = ns.Domain(1 - dirn)
                for f in (0.0, 1.0):
                    try: edges.append(ns.IsoCurve(dirn, dom_c.ParameterAt(f)).GetLength())
                    except Exception: pass
            mean_edge = sum(edges) / len(edges) if edges else 0.1 * self.size
            ks.sort()
            k95 = ks[int(0.95 * (len(ks) - 1))] if ks else 0.0
            cache[oid] = (lines, (0.5 * mean_edge / k95) if k95 > 1e-12 else 0.0, 0.75 * mean_edge)
        lines, scale, cap = cache[oid]
        c_comb = col(120, 120, 200); c_env = col(200, 30, 160)
        for pts in lines:
            env = []
            for (p, kv) in pts:
                v = rg.Vector3d(kv); v *= -scale            # comb on the outer side, like Rhino
                if v.Length > cap:
                    v.Unitize(); v *= cap
                tip = p + v
                d.DrawLine(p, tip, c_comb, 1)
                env.append(tip)
            if len(env) > 1:
                d.DrawPolyline(env, c_env, 2)

    # ---- Deviation: patch vs scan as coloured dots (all settings automatic) ----
    DEV_N = 17                                 # dots per direction and patch

    def _dev_colour(self, dev, scale):
        """0 = green, half ScanTol = yellow (+, scan above the patch) / cyan
        (-, below), ScanTol and more = red / blue. Grey = no scan."""
        if dev is None:
            return col(150, 150, 150)
        t = min(1.0, abs(dev) / scale)
        g = (0, 180, 70)
        mid, end = ((235, 190, 0), (220, 30, 30)) if dev > 0 else ((0, 190, 200), (30, 70, 230))
        a, b, f = (g, mid, 2.0 * t) if t <= 0.5 else (mid, end, 2.0 * t - 1.0)
        return col(*[int(a[i] + (b[i] - a[i]) * f) for i in range(3)])

    def draw_deviation(self, d):
        """Dots on every patch (DEV_N along its longer side, the same spacing
        across) coloured by the distance to the scan against ScanTol: green 0,
        yellow / cyan half ScanTol, red / blue ScanTol and more (scan above /
        below). Two labels for the whole model: the biggest + and - (red when
        more than ScanTol)."""
        cache = self.__dict__.setdefault("dev_cache", {})
        tol = max(self.s_tol(), 1e-9)
        ids = [s_[0] for s_ in getattr(self, "surfs", [])]
        for oid in ids:
            if oid in cache: continue
            ns = self._patch_nurbs(oid)
            if ns is None:
                cache[oid] = []; continue
            try:
                sgn = -1.0 if rs.GetUserText(oid, "msh2classA_side") == "-1" else 1.0
            except Exception:
                sgn = 1.0
            pts_ = self.patch_points(oid)
            if pts_:                                   # built through TempPoints: deviation AT those points
                dots = []
                for q in pts_:
                    qq = P3(q)
                    ok_, u_, v_ = ns.ClosestPoint(qq)
                    if not ok_: continue
                    n = ns.NormalAt(u_, v_); n *= sgn
                    dots.append((qq, (qq - ns.PointAt(u_, v_)) * n))
                cache[oid] = dots
                continue
            du_, dv_ = ns.Domain(0), ns.Domain(1)
            # DEV_N dots along the longer side of the patch, evenly spaced (any size, any units)
            def len_along(dirn):
                try:
                    return ns.IsoCurve(dirn, ns.Domain(1 - dirn).Mid).GetLength()
                except Exception:
                    return 1.0
            Lu_, Lv_ = len_along(0), len_along(1)
            Lm_ = max(Lu_, Lv_, 1e-12)
            nu_ = max(5, int(round(self.DEV_N * Lu_ / Lm_))); nv_ = max(5, int(round(self.DEV_N * Lv_ / Lm_)))
            dots = []
            saved = getattr(self, "refs", None); self.refs = []
            try:
                for i in range(nu_):
                    for j in range(nv_):
                        u = du_.ParameterAt(i / (nu_ - 1.0)); v = dv_.ParameterAt(j / (nv_ - 1.0))
                        p = ns.PointAt(u, v)
                        n = ns.NormalAt(u, v); n *= sgn
                        q = self.mesh.ClosestPoint(p)               # the real distance to the scan
                        if not q.IsValid or q.DistanceTo(p) > 20.0 * tol:
                            dots.append((p, None)); continue
                        dd = q.DistanceTo(p)
                        dots.append((p, dd if (q - p) * n >= 0 else -dd))     # + : the scan is above
            finally:
                self.refs = saved
            cache[oid] = dots
        hi = lo = None
        for oid in ids:
            for (p, dev) in cache.get(oid, []):
                if dev is None: continue
                if hi is None or dev > hi[1]: hi = (p, dev)
                if lo is None or dev < lo[1]: lo = (p, dev)
        scale = tol
        for oid in ids:
            for (p, dev) in cache.get(oid, []):
                c_ = self._dev_colour(dev, scale)
                try:
                    d.DrawPoint(p, Rhino.Display.PointStyle.RoundSimple, 3, c_)
                except Exception:
                    d.DrawPoint(p, c_)
        for (it, sign) in ((hi, "+"), (lo, "-")):
            if it is None: continue
            bg = col(220, 30, 30) if abs(it[1]) > tol else col(255, 255, 255)
            d.DrawDot(it[0], "{}{}".format(sign, fl(abs(it[1]))), bg, col(0, 0, 0))

    def draw_matcap(self, d):
        """Own EMap: every surface (and the mirror copies) drawn shaded with the
        matcap image as an environment map. Meshes are made once per change and
        lifted a hair off the surface on the side you look at (no flicker)."""
        if not (self.view_on("emap") and matcap_path()):
            return
        mat = getattr(self, "matcap_mat", None)
        if mat is None:
            try:
                mat = Rhino.Display.DisplayMaterial()
                mat.Diffuse = sd.Color.White
                mat.SetEnvironmentTexture(matcap_path(), True)
                mat.SetEnvironmentTexture(matcap_path(), False)
            except Exception:
                return
            self.matcap_mat = mat
        cache = self.__dict__.setdefault("matcap_meshes", {})
        ids = [s_[0] for s_ in getattr(self, "surfs", [])] + list(getattr(self, "mirror_ids", []))
        eps = 2e-5 * self.size
        for oid in ids:
            m_ = cache.get(oid)
            if m_ is None:
                obj = sc.doc.Objects.FindId(oid)
                if obj is None: continue
                g = obj.Geometry
                brep = g if isinstance(g, rg.Brep) else (g.ToBrep() if hasattr(g, "ToBrep") else None)
                if brep is None: continue
                mp = rg.MeshingParameters.QualityRenderMesh
                ms = rg.Mesh.CreateFromBrep(brep, mp)
                if not ms: continue
                m_ = rg.Mesh()
                for x in ms: m_.Append(x)
                m_.Normals.ComputeNormals()
                try:
                    sgn = -1.0 if rs.GetUserText(oid, "msh2classA_side") == "-1" else 1.0
                except Exception:
                    sgn = 1.0
                for a in range(m_.Vertices.Count):
                    v = m_.Vertices[a]; n = m_.Normals[a]
                    m_.Vertices.SetVertex(a, v.X + n.X * eps * sgn, v.Y + n.Y * eps * sgn, v.Z + n.Z * eps * sgn)
                cache[oid] = m_
            try:
                d.DrawMeshShaded(m_, mat)
            except Exception:
                pass

    def read_analysis(self):
        """At start: the buttons show what is really on (Zebra / EMap may be
        switched on before the command) - nothing is changed here."""
        st = self.__dict__.setdefault("analysis_state", {})
        for key, mode in analysis_modes().items():
            if mode is None: continue
            if key == "emap" and matcap_path():
                st[key] = False; continue          # own matcap EMap, starts off
            on = False
            for (oid, brep, bb, ok) in getattr(self, "surfs", []):
                obj = sc.doc.Objects.FindId(oid)
                try:
                    if obj is not None and obj.InVisualAnalysisMode(mode):
                        on = True; break
                except Exception:
                    pass
            st[key] = on

    def update_mirror_view(self):
        """Real mirrored copies of the surfaces (so Zebra / EMap show the
        symmetry line too), tagged and never used as neighbours. Rebuilt
        after every change; deleted when switched off and at the end."""
        self.clear_mirror_view()
        ax = MIRROR_AXIS.get(self.s_mirror())
        if not getattr(self, "mirror_view", False) or ax is None:
            return
        nrm = [0.0, 0.0, 0.0]; nrm[ax] = 1.0
        xf = rg.Transform.Mirror(rg.Point3d(0, 0, 0), rg.Vector3d(nrm[0], nrm[1], nrm[2]))
        modes = analysis_modes()
        ids = []
        for (oid, brep, bb, ok) in getattr(self, "surfs", []):
            obj = sc.doc.Objects.FindId(oid)
            if obj is None: continue
            g = obj.Geometry.Duplicate()
            if not g.Transform(xf): continue
            # a mirror turns the surface inside out: flip it back so its normal
            # points to the same (outer) side as the original
            try:
                if isinstance(g, rg.Brep):
                    g.Flip()
                elif isinstance(g, rg.Surface):
                    r_ = g.Reverse(0)
                    if r_ is not None: g = r_
            except Exception:
                pass
            at = obj.Attributes.Duplicate()
            at.SetUserString(PREVIEW_KEY, "1")
            nid = sc.doc.Objects.Add(g, at)
            if nid is None or str(nid) == "00000000-0000-0000-0000-000000000000": continue
            ids.append(nid)
            for key, mode in modes.items():
                if mode is not None and self.view_on(key):
                    try: sc.doc.Objects.FindId(nid).EnableVisualAnalysisMode(mode, True)
                    except Exception: pass
        self.mirror_ids = ids
        sc.doc.Views.Redraw()

    def clear_mirror_view(self):
        ids = list(getattr(self, "mirror_ids", []))
        try:
            s = ro.ObjectEnumeratorSettings(); s.HiddenObjects = True; s.LockedObjects = True
            for obj in sc.doc.Objects.GetObjectList(s):   # also stale ones (crash, older run)
                if is_preview(obj) and obj.Id not in ids: ids.append(obj.Id)
        except Exception:
            pass
        for i in ids:
            try: sc.doc.Objects.Delete(i, True)
            except Exception: pass
        self.mirror_ids = []

    def apply_analysis(self, only=None, new_only=False):
        """Zebra / EMap of the surfaces near the scan (never the scan) as the
        buttons say. new_only: just switch it ON on surfaces that lack it (new
        or changed patches) - never switches anything off by itself."""
        for key, mode in analysis_modes().items():
            if mode is None or (only is not None and key != only): continue
            if key == "emap" and matcap_path(): continue     # own matcap, not Rhino's EMap
            want = self.view_on(key)
            if new_only and not want: continue
            ids_ = [s_[0] for s_ in getattr(self, "surfs", [])] + list(getattr(self, "mirror_ids", []))
            for oid in ids_:
                obj = sc.doc.Objects.FindId(oid)
                if obj is None: continue
                try:
                    if obj.InVisualAnalysisMode(mode) != want:
                        obj.EnableVisualAnalysisMode(mode, want)
                except Exception:
                    pass

    def restore_view(self):
        """At the end: panel closed, the scan shown again. Zebra / EMap stay as
        the buttons left them (as they were, if you did not touch them)."""
        self.close_panel()
        self.mirror_view = False
        self.clear_mirror_view()
        sid = getattr(self, "scan_id", None)
        if sid is not None and not getattr(self, "scan_visible", True):
            sc.doc.Objects.Show(sid, True)
        sc.doc.Views.Redraw()


# -----------------------------------------------------------------------------
# Drawing a patch: corners, sides, side bubbles, CV counts
# -----------------------------------------------------------------------------

class PolygonMixin(object):
    def reset_polygon(self):
        self.corners = []      # dicts {"pt": Point3d, "on": [(ci, t)]}
        self.shape = {}        # k -> [Point3d] scan points along side k (click order)
        self.pend = []         # scan points clicked for the side being drawn
        self.side_g = {}             # side k -> 0/1/2 continuity chosen in its bubble
        self.mirror_sides = []
        self.mirror_axis = None
        self.bubbles = {}            # side k -> Point3d of its clickable bubble
        self.hover_bubble = None
        self.picks = []        # undo stack

    def corner(self, pt):
        """Corner + all edges/curves it lies on (within tolerance)."""
        on = []
        p = T(pt)
        for ci, c in enumerate(self.cands):
            if not c.near_bbox(p, self.tol_on): continue
            ok, t = c.crv.ClosestPoint(pt)
            if ok and c.crv.PointAt(t).DistanceTo(pt) < self.tol_on:
                on.append((ci, t))
        return {"pt": pt, "on": on}

    def is_mirror_side(self, k):
        """Side k (not on an edge/curve) with both corners near the symmetry plane."""
        ax = MIRROR_AXIS.get(self.s_mirror())
        if ax is None or len(self.corners) < 4 and not k < len(self.corners) - 1:
            return False
        a = self.corners[k]; b = self.corners[(k + 1) % 4]
        if self.common(a, b) is not None:
            return False
        tol = self.s_mtol()
        return abs(T(a["pt"])[ax]) <= tol and abs(T(b["pt"])[ax]) <= tol

    def preview_side(self, k):
        """(kind, cand) of side k as far as it is drawn, or (None, None)."""
        n = len(self.corners)
        if not (k < n - 1 or (n == 4 and k == 3)):
            return None, None
        if self.is_mirror_side(k):
            return "mirror", None
        shaped = self.shape.get(k) or (k == 3 and self.pend)
        if shaped:
            return "scan", None
        pick = self.common(self.corners[k], self.corners[(k + 1) % 4])
        if pick is None:
            return "scan", None
        c = self.cands[pick[0]]
        return c.kind, c

    def side_rows(self, k):
        """CV rows side k takes across the patch: G0 1, G1 2, G2 3.
        A side on scan RESERVES rows for its future neighbour (free until then)."""
        kind, c = self.preview_side(k)
        if kind == "mirror":                       # like an invisible mirrored neighbour
            return self.level_for_side(k) + 1
        if kind in ("edge", "scan"):
            return self.layout_for_side(k) + 1
        return 1

    def dir_cv(self, d):
        """CV count in direction d (0 PINK = along sides 1,3; 1 BLUE = along 2,4).
        = rows of the two END sides of that direction; None = both on scan
        (auto by ScanTol). Never below the CVs of an edge/curve along d."""
        ends = (1, 3) if d == 0 else (0, 2)
        along = 0
        for k in ((0, 2) if d == 0 else (1, 3)):
            kind, c = self.preview_side(k)
            if kind in ("edge", "curve") and c is not None and c.ncv and c.ncv <= 8:
                along = max(along, c.ncv)
        # class-A: CVs across = exactly the rows the two ends take
        # (G1+G1 = 4, G2+G2 = 6, G1+G2 = 5, G0 = 1 row) - no extra rows
        n = sum(self.side_rows(k) for k in ends)
        return max(n, along)

    def side_text(self, k, kind):
        if kind == "edge":
            return u"{} \u25BC".format(SIDE_LV_NAME[self.raw_side_level(k)])
        if kind == "curve":
            return "G0 crv"
        if kind == "mirror":
            return u"mirror | {} \u25BC".format(LEVEL_NAME[self.level_for_side(k)])
        return u"scan | {} later \u25BC".format(LEVEL_NAME[self.level_for_side(k)])

    def bubble_tip(self, k):
        kind, c = self.preview_side(k)
        d_across = 1 - (k % 2)
        ends = (1, 3) if d_across == 0 else (0, 2)
        rows = " + ".join(str(self.side_rows(e)) for e in ends)
        n = self.dir_cv(d_across)
        size = str(n)
        if all(self.preview_side(e)[0] == "scan" for e in ends):
            size += "+ (more if the scan needs it)"
        if kind == "edge":
            return "click: G0 -> G1 -> G2 to this neighbour   (across: {} = {} CV)".format(rows, size)
        if kind == "curve":
            return "follows the curve (G0), 1 row   (across: {} = {} CV)".format(rows, size)
        if kind == "mirror":
            return ("click: G0 -> G1 -> G2 to the mirrored copy ({} plane). G0 = edge on the plane, "
                    "G1 = 2nd row straight across it, G2 = 3rd row at twice its distance   (across: {} = {} CV)".format(
                        self.s_mirror(), rows, size))
        return ("click: reserve rows for a FUTURE G0 -> G1 -> G2 neighbour here; free (follow the scan) "
                "until then   (across: {} = {} CV)".format(rows, size))

    def cycle_cv(self, k):
        kind, c = self.preview_side(k)
        if kind not in ("edge", "scan", "mirror"):
            print("  side {}: follows a curve (G0) - nothing to switch".format(k + 1)); return
        cur = self.raw_side_level(k)
        if kind == "edge":                  # G0 -> G1 -> G2 -> G2>G0 -> G1>G0 -> G0
            self.side_g[k] = (cur + 1) % 5
            v = self.side_g[k]
            if v >= 3:
                print("  side {}: {} - laid out like {} ({} rows), only the edge row matched to the neighbour "
                      "(the seam is G0; MatchUVN can raise it later)".format(k + 1, SIDE_LV_NAME[v],
                                                                            LEVEL_NAME[self.layout_for_side(k)],
                                                                            self.layout_for_side(k) + 1))
            else:
                print("  side {}: {} to neighbour".format(k + 1, LEVEL_NAME[v]))
            return
        self.side_g[k] = (min(cur, 2) + 1) % 3
        if kind == "mirror":
            print("  side {}: {} to the mirrored copy".format(k + 1, LEVEL_NAME[self.side_g[k]]))
        else:
            print("  side {}: rows reserved for a future {} neighbour".format(k + 1, LEVEL_NAME[self.side_g[k]]))

    def size_text(self):
        return "{} x {} CV".format(self.dir_cv(0), self.dir_cv(1))

    def raw_side_level(self, k):
        """The bubble's choice 0..4 (SIDE_LV_NAME), default = option."""
        if k in self.side_g:
            return self.side_g[k]
        return 2 if self.s_cont() == "G2" else (1 if self.s_cont() == "G1" else 0)

    def level_for_side(self, k):
        """Continuity MATCHED to the neighbour on side k: G2>G0 / G1>G0 = G0."""
        v = self.raw_side_level(k)
        if v >= 3:
            kind, c = self.preview_side(k)
            return 0 if kind == "edge" else (2 if v == 3 else 1)
        return v

    def layout_for_side(self, k):
        """Continuity the side's ROWS are laid out for: G2>G0 = G2, G1>G0 = G1."""
        v = self.raw_side_level(k)
        return {3: 2, 4: 1}.get(v, v)

    def edge_snap_dist(self):
        """A corner / side point this close (model units) to an edge or its
        end goes ONTO it: 2 x ScanTol."""
        return max(2.0 * self.s_tol(), self.tol_on)

    def snap_to_edges(self, pt):
        """Scan point near an edge/curve -> (point on it, distance), its END
        preferred (the neighbour's corner); None if nothing is near."""
        dmax = self.edge_snap_dist(); p = T(pt)
        best_end = None; best_on = None
        for c in self.cands:
            if not c.near_bbox(p, dmax): continue
            for q in (c.p0, c.p1):
                d = q.DistanceTo(pt)
                if d < dmax and (best_end is None or d < best_end[1]): best_end = (rg.Point3d(q), d)
            ok, t = c.crv.ClosestPoint(pt)
            if ok:
                q = c.crv.PointAt(t); d = q.DistanceTo(pt)
                if d < dmax and (best_on is None or d < best_on[1]): best_on = (q, d)
        return best_end or best_on

    def pend_on_edge(self, a, b, pend):
        """Side a -> b with clicked points pend: True if a and b lie on the same
        edge/curve and every point lies on it too (within the snap distance)
        - then the side simply follows that edge (the points were only
        clicked along it)."""
        pick = self.common(a, b)
        if pick is None or not pend: return False
        c = self.cands[pick[0]]; dmax = self.edge_snap_dist()
        for q in pend:
            ok, t = c.crv.ClosestPoint(q)
            if not ok or c.crv.PointAt(t).DistanceTo(q) > dmax: return False
        return True

    def add_corner(self, pt, forced_on=None):
        cc = self.corner(pt)
        if not cc["on"] and not forced_on:
            sn = self.snap_to_edges(pt)                # scan corner right beside an edge: put it ON the edge
            if sn is not None and sn[1] > self.tol_on:
                self.flash_at("[SNAP] corner {} pulled {} onto the edge next to it (it was closer than {}). "
                              "To leave a gap: click further away, or zoom in".format(
                                  len(self.corners) + 1, fl(sn[1]), fl(self.edge_snap_dist())), sn[0], 8.0)
                pt = sn[0]; cc = self.corner(pt)
        if self.corners and pt.DistanceTo(self.corners[-1]["pt"]) < self.tol_on:
            print("  [SKIP] same point as previous corner"); return False
        if len(self.corners) == 3 and pt.DistanceTo(self.corners[0]["pt"]) < self.tol_on:
            print("  [SKIP] that closes a triangle - a patch needs 4 corners"); return False
        for (ci, t) in (forced_on or []):
            if all(ci != x[0] for x in cc["on"]):
                cc["on"].append((ci, t))
        k = len(self.corners) - 1          # side that this corner ends
        moved = list(self.pend)
        if k >= 0 and self.pend:
            if self.pend_on_edge(self.corners[-1], cc, self.pend):
                self.flash_at("[SNAP] side {}: all its points are closer than {} to the edge - the side now FOLLOWS "
                              "the edge (no gap). Undo and click further away to leave a gap".format(
                                  k + 1, fl(self.edge_snap_dist())), cc["pt"], 10.0)
            else:
                self.shape[k] = list(self.pend)
        self.pend = []
        self.corners.append(cc)
        self.picks.append(("corner", k, moved, None))
        return True

    def undo(self):
        if not self.picks: return
        e = self.picks.pop()
        if e[0] == "pend":
            if self.pend: self.pend.pop()
        elif e[0] == "corner":
            _, k, moved, last = e
            if self.corners: self.corners.pop()
            self.shape.pop(k, None)
            self.pend = list(moved) + ([last] if last is not None else [])

    def end_side_here(self):
        """Right click while drawing side n: last scan point becomes corner."""
        if not self.pend:
            return False
        last = self.pend.pop()
        moved = list(self.pend)
        if not self.add_corner(last):
            self.pend.append(last); return False
        e = self.picks.pop()
        self.picks.append(("corner", e[1], moved, last))
        return True

    def side_points(self, k):
        """Corner A, scan points in click order, corner B."""
        a = self.corners[k]["pt"]; b = self.corners[(k + 1) % 4]["pt"]
        return [a] + list(self.shape.get(k, [])) + [b]

    def side_curve(self, k):
        pts = self.side_points(k)
        if len(pts) == 2:
            return rg.LineCurve(pts[0], pts[1])
        return smooth_curve(pts)

    def handle_pick(self, pt, hits, m=None, sx=None, sy=None):
        """Scan click = point along the current side (first click = corner 1).
        Click on edge/curve/its end = corner right there, ends the side.
          1. an END of an edge/curve within the snap radius -> the nearest end
             on screen (= the neighbour's corner, exactly)
          2. two edges/curves that really MEET there (corner of a hole) ->
             the corner sits on both
          3. otherwise -> on the nearest edge/curve, under the cursor.
        Two edges merely running close (both sides of a narrow blend) are
        NOT joined: that put the corner in the gap between them."""
        if not hits:
            if not self.corners:
                self.add_corner(pt)               # corner 1
            else:
                self.pend.append(pt)              # point along current side
                self.picks.append(("pend",))
                sn = self.snap_to_edges(pt)
                if sn is not None:
                    self.flash_at("point only {} from the edge next to it (pulled onto it if the whole side is that "
                                  "close: closer than {}) - click further away to leave a gap".format(
                                      fl(sn[1]), fl(self.edge_snap_dist())), pt, 6.0)
            return
        if m is not None:
            ends = []
            for h in hits:
                if h["end"] is None: continue
                c = self.cands[h["ci"]]
                q = c.p0 if h["end"] == 0 else c.p1
                x, y = to_screen(m, T(q))
                ends.append((math.hypot(x - sx, y - sy), h, q))
            if ends:
                d_, h, q = min(ends, key=lambda e: e[0])
                c = self.cands[h["ci"]]
                self.add_corner(rg.Point3d(q), [(h["ci"], c.crv.Domain.T0 if h["end"] == 0 else c.crv.Domain.T1)])
                return
        h0 = hits[0]; c0 = self.cands[h0["ci"]]
        meet = max(self.tol_on, 0.5 * self.s_tol())
        for other in hits[1:]:
            if other["ci"] == h0["ci"]: continue
            c1 = self.cands[other["ci"]]
            P, ta, tb, gap = joint_point(c0.crv, h0["t"], c1.crv, other["t"])
            if gap <= meet:
                self.add_corner(P, [(h0["ci"], ta), (other["ci"], tb)])
                return
        if h0["end"] is not None:
            self.add_corner(rg.Point3d(c0.p0 if h0["end"] == 0 else c0.p1))
        else:
            self.add_corner(c0.crv.PointAt(h0["t"]), [(h0["ci"], h0["t"])])

    def common(self, a, b):
        """Edge/curve both corners lie on -> (ci, ta, tb). Edges beat curves."""
        best = None
        for (ci, ta) in a["on"]:
            for (cj, tb) in b["on"]:
                if ci != cj: continue
                c = self.cands[ci]
                rel = abs(tb - ta) / max(c.crv.Domain.Length, 1e-12)
                if rel < 1e-6: continue
                key = (0 if c.kind == "edge" else 1, rel)
                if best is None or key < best[0]:
                    best = (key, (ci, ta, tb))
        return best[1] if best else None

    def prompt(self):
        if getattr(self, "fillet_mode", None):
            fm_ = self.fillet_mode
            if fm_.get("kind") == "npatch":
                return "NPatch: corner {} <right click = build, Esc = cancel>".format(len(fm_["pts"]) + 1)
            nxt = "side B" if fm_["side"] == "A" else "radius"
            return "Fillet side {}: corner {} <right click = {}, Esc = cancel>".format(fm_["side"], len(fm_["pts"]) + 1, nxt)
        n = len(self.corners)
        info = "{} default, {} {}".format(self.s_cont(), self.s_fit(), self.s_spans())
        if n == 0:
            return "Patch {}: side 1 start ({}), or hover a patch: patch buttons <right click = finish>".format(
                len(self.patches) + 1, info)
        if n == 4:
            return "Side 4 back to corner 1: points on scan <right click = BUILD>"
        if not self.pend:
            return "Side {}: click on scan (or edge/curve = corner) <Undo>".format(n)
        return "Side {}: more points on scan <right click = end side>".format(n)

    def collect_polygon(self):
        while True:
            gp = ric.GetPoint()
            gp.SetCommandPrompt(self.prompt())
            try: gp.Constrain(self.mesh, False)     # Rhino's own fast pick on the scan
            except Exception: pass
            gp.AcceptNothing(True)
            ui = self.add_options(gp)
            self.down = None
            gp.MouseMove += self.on_move
            gp.MouseDown += self.on_down
            gp.DynamicDraw += self.on_draw
            try: gp.AcceptCustomMessage(True)       # the panel's scan buttons
            except Exception: pass
            # scan buttons only while nothing is half done
            scan_tools_ready(not self.corners and not getattr(self, "match_mode", None)
                             and not getattr(self, "edge_mode", None) and not getattr(self, "fillet_mode", None))
            try:
                res = gp.Get()
            finally:
                scan_tools_ready(False)
            self.hover = None
            if res == Rhino.Input.GetResult.CustomMessage:
                nm = take_pending_tool()
                if nm and nm.startswith("SRF:"):            # Surfaces group: Fillet / NPatch / GlobalMatch
                    try:
                        self.start_surface_tool(nm[4:])
                    except Exception:
                        print("[{}] failed:".format(nm[4:])); print(traceback.format_exc())
                    continue
                if nm:
                    self.pending_tool = nm
                    return "scantool"
                continue
            if res == Rhino.Input.GetResult.Cancel:
                # Esc inside Match / FreeEdgeEdt ends only that mode (the patch
                # buttons come back), not the whole command
                if getattr(self, "edge_mode", None):
                    self.edge_mode = None; print("[FreeEdgeEdt] cancelled - patch buttons back"); continue
                if getattr(self, "fillet_mode", None):
                    self.fillet_mode = None; self.fillet_preview = None; continue
                if getattr(self, "match_mode", None):
                    self.match_mode = None; print("[MatchUVN] cancelled - patch buttons back"); continue
                return "exit"
            if res == Rhino.Input.GetResult.Nothing:
                if getattr(self, "edge_mode", None):
                    self.edge_finish(); continue
                if getattr(self, "fillet_mode", None):
                    try:
                        if self.corner_mode_enter():
                            continue                     # it built something / told what is missing
                    except Exception:
                        print("[Fillet/NPatch] failed:"); print(traceback.format_exc())
                    if getattr(self, "fillet_mode", None) is None:
                        continue
                    self.fillet_mode = None; self.fillet_preview = None; continue
                if getattr(self, "match_mode", None):
                    self.match_mode = None; print("[MatchUVN] cancelled"); continue
                if not self.corners:
                    return "exit"
                if len(self.corners) == 4:
                    if self.pend:
                        if self.pend_on_edge(self.corners[3], self.corners[0], self.pend):
                            print("  side 4: the clicked points lie on the edge - the side follows the edge")
                        else:
                            self.shape[3] = list(self.pend)
                        self.pend = []
                    return "build"
                if not self.end_side_here():
                    print("  Side {} needs at least one point (Undo / Clear).".format(len(self.corners)))
                continue
            if res == Rhino.Input.GetResult.Option:
                self.handle_option(gp.Option(), ui)
                continue
            if res == Rhino.Input.GetResult.Point:
                if self.click_view_button():
                    continue                                    # it was a view button
                if self.down is not None:
                    vp, sx, sy = self.down
                elif self.last_mouse is not None:
                    vp, sx, sy = self.last_mouse
                else:
                    vp = gp.View().ActiveViewport
                    sx, sy = to_screen(xf_tuple(vp), T(gp.Point()))
                pt = gp.Point()
                m = xf_tuple(vp)
                bh = self.bubble_at(m, sx, sy)
                if bh is not None:
                    self.cycle_cv(bh[0]); continue
                pk, sn = self.pick_px(vp)
                hits = self.visible_hits(hit_test(self.cands, m, sx, sy, pk, sn), vp)
                if getattr(self, "match_mode", None) and not self.corners:
                    self.match_click(m, sx, sy); continue
                if getattr(self, "edge_mode", None) and not self.corners:
                    self.edge_click(pt, m, sx, sy); continue
                if getattr(self, "fillet_mode", None) and not self.corners:
                    try:
                        self.fillet_click(m, sx, sy)
                    except Exception:
                        print("[Fillet] failed:"); print(traceback.format_exc())
                        self.fillet_mode = None; self.fillet_preview = None
                    continue
                if not self.corners and not hits:
                    btn = self.srf_button_at(m, sx, sy)
                    if btn is not None:
                        try:
                            self.adjust_patch(btn[0], btn[1])
                        except Exception:
                            print("[{}] failed:".format(btn[1].upper())); print(traceback.format_exc())
                        continue
                if len(self.corners) == 4:
                    if hits:
                        print("  Side 4 closes at corner 1 - click on scan to bend it, right click = build.")
                    else:
                        self.pend.append(pt); self.picks.append(("pend",))
                    continue
                first = not getattr(self, "warmed", False) and not self.corners
                busy = show_busy("Please wait - preparing the tools (first click only)...") if first else None
                try:
                    self.handle_pick(pt, hits, m, sx, sy)
                    if first:
                        self.warm_hint(pt)
                finally:
                    if busy is not None:
                        hide_busy(busy)
                    self.warmed = True
                if len(self.corners) == 4:
                    print("  4 corners. Side 4: points on scan or right click = build.")
                continue
            return "exit"

    def collect_fit_points(self):
        self.fit_pts = []
        while True:
            gp = ric.GetPoint()
            gp.SetCommandPrompt("Fit point {} (Enter = build, Undo)".format(len(self.fit_pts) + 1))
            try: gp.Constrain(self.mesh, False)
            except Exception: pass
            gp.AcceptNothing(True)
            if self.fit_pts: gp.AddOption("UndoPt")
            gp.DynamicDraw += self.on_draw
            gp.MouseMove += self.on_move_view
            gp.MouseDown += self.on_down
            res = gp.Get()
            if res == Rhino.Input.GetResult.Point:
                if self.click_view_button():
                    continue
                self.fit_pts.append(gp.Point()); continue
            if res == Rhino.Input.GetResult.Option:
                if self.fit_pts: self.fit_pts.pop()
                continue
            if res == Rhino.Input.GetResult.Cancel:
                return None
            pts = list(self.fit_pts)
            return pts


# -----------------------------------------------------------------------------
# Scan sampling: only faces turned like the patch
# -----------------------------------------------------------------------------

class ScanMixin(object):
    def prep_front(self):
        """Thin parts are often scanned on both sides. Every clicked point hit a
        mesh face on the side you work on; its normal tells which side that is.
        Scan samples are taken only from faces turned the same way as the
        nearest clicked face, otherwise by a ray along that normal."""
        m = self.mesh
        try:
            if m.FaceNormals.Count != m.Faces.Count:
                m.FaceNormals.ComputeFaceNormals()
        except Exception:
            pass
        clicked = [c["pt"] for c in self.corners]
        for pts in self.shape.values():
            clicked += list(pts)
        self.refs = []
        for p in clicked:
            mp = m.ClosestMeshPoint(p, 0.0)
            if mp is None: continue
            n = m.FaceNormals[mp.FaceIndex]
            self.refs.append((T(p), v_unit((n.X, n.Y, n.Z))))
        span = max(self.corners[i]["pt"].DistanceTo(self.corners[j]["pt"]) for i in range(4) for j in range(4))
        c_ = [c["pt"] for c in self.corners]
        width = min(0.5 * (c_[0].DistanceTo(c_[1]) + c_[2].DistanceTo(c_[3])),
                    0.5 * (c_[1].DistanceTo(c_[2]) + c_[3].DistanceTo(c_[0])))
        mm = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
        # how far to look for the visible side of a thin wall: about the patch
        # WIDTH (a 5 mm fillet 300 mm long must not search 75 mm away)
        self.ray_back = min(0.25 * span + mm, max(1.5 * width, 3.0 * mm))
        self.n_back = 0
        self.n_wall = 0

    def scan_side_sign(self, ns):
        """+1 if the surface normal points to the side of the scan we work on
        (normal of the nearest clicked face), else -1. Stored on the object so
        Smooth / Pull2Msh later sample the right side of a thin part."""
        u = ns.Domain(0).Mid; v = ns.Domain(1).Mid
        n = T(ns.NormalAt(u, v)); r = self.ref_normal(ns.PointAt(u, v))
        return -1 if (r is not None and v_dot(n, r) < 0) else 1

    def patch_sign(self, coons, ox):
        """+1/-1: how the patch parameter normal relates to the side you work
        on, decided ONCE from all four corners (their clicked scan faces).
        Per-sample 'nearest click' orientation fails on narrow fillets /
        blends, where the normal turns ~90 deg between the corners."""
        votes = 0.0
        for (u, v) in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
            h = 1e-3
            cu = v_sub(coons(min(1.0, u + h), v), coons(max(0.0, u - h), v))
            cv = v_sub(coons(u, min(1.0, v + h)), coons(u, max(0.0, v - h)))
            n = v_cross(cu, cv)
            p = coons(u, v)
            mp = self.mesh.ClosestMeshPoint(rg.Point3d(p[0] + ox[0], p[1] + ox[1], p[2] + ox[2]), 0.0)
            if mp is None or v_len(n) < 1e-300: continue
            f = self.mesh.FaceNormals[mp.FaceIndex]
            votes += v_dot(v_unit(n), (f.X, f.Y, f.Z))
        return -1.0 if votes < 0 else 1.0

    def ray_hit(self, o, d):
        """First hit of the ray o + t*d on the scan -> (t, face index or None)."""
        ray = rg.Ray3d(o, rg.Vector3d(d[0], d[1], d[2]))
        try:
            res = rg.Intersect.Intersection.MeshRay(self.mesh, ray)
            if isinstance(res, tuple):
                t, faces = res[0], res[1]
                return t, (faces[0] if faces is not None and len(faces) else None)
        except Exception:
            res = None
        try:
            t, faces = rg.Intersect.Intersection.MeshRay(self.mesh, ray, None)
            return t, (faces[0] if faces is not None and len(faces) else None)
        except Exception:
            return rg.Intersect.Intersection.MeshRay(self.mesh, ray), None

    def front_along(self, p, nref, min_dot, maxd):
        """From p along +nref: first scan face turned like nref (skipping the
        back faces of a thin wall on the way), within maxd."""
        eps = 1e-6 * max(self.size, 1.0)
        o = rg.Point3d(p.X, p.Y, p.Z); gone = 0.0
        for _ in range(6):
            t, fi = self.ray_hit(o, nref)
            if t is None or t < 0: return None
            gone += t
            if gone > maxd: return None
            hit = rg.Point3d(o.X + nref[0] * t, o.Y + nref[1] * t, o.Z + nref[2] * t)
            if fi is None:
                mh = self.mesh.ClosestMeshPoint(hit, 0.0)
                fi = mh.FaceIndex if mh is not None else None
            if fi is not None:
                n = self.mesh.FaceNormals[fi]
                if n.X * nref[0] + n.Y * nref[1] + n.Z * nref[2] >= min_dot:
                    return hit
            o = rg.Point3d(hit.X + nref[0] * eps, hit.Y + nref[1] * eps, hit.Z + nref[2] * eps)
            gone += eps
        return None

    def ref_normal(self, p):
        tp = T(p); best = None
        for (q, n) in getattr(self, "refs", []):
            d = v_dist(tp, q)
            if best is None or d < best[0]:
                best = (d, n)
        return best[1] if best else None

    def face_dot(self, mp, nref):
        n = self.mesh.FaceNormals[mp.FaceIndex]
        return n.X * nref[0] + n.Y * nref[1] + n.Z * nref[2]

    def scan_pt(self, p, nsurf=None, fit=False):
        """Scan point for p, ONLY from faces turned the same way as the patch.
        nsurf : local patch normal (fit and start surface); otherwise the
                normal of the nearest clicked point is used.
        fit   : during the fit nothing else is tried - an angled or opposite
                face (rib, neighbouring wall, back side) just drops the sample.
        Otherwise (projecting sides / start) a ray along the normal is tried,
        and its hit is accepted only if THAT face is turned the right way."""
        m = self.mesh
        mp = m.ClosestMeshPoint(p, 0.0)
        nref = self.ref_normal(p)
        if nsurf is not None and v_len(nsurf) > 1e-12:
            sg = getattr(self, "nsign", None)
            if sg is not None:
                nsurf = v_mul(nsurf, sg)                   # one orientation for the whole patch
            elif nref is not None and v_dot(nsurf, nref) < 0:
                nsurf = v_mul(nsurf, -1.0)                 # orient to the clicked side
            nref = v_unit(nsurf)
        if nref is None:
            return mp.Point if mp is not None else None
        min_dot = 0.7 if fit else 0.5
        if mp is not None:
            dn = self.face_dot(mp, nref)
            if dn >= min_dot:
                return mp.Point
            if dn > -0.3:
                self.n_wall += 1
                return None                               # wall / rib (angled face): skip
            # nearest face is turned AWAY = back side of a thin part: the point
            # is inside the wall or already through it. The visible side is
            # forward along the normal - walk there past the back faces.
            hit = self.front_along(p, nref, min_dot, self.ray_back)
            if hit is not None:
                self.n_back += 1
                return hit
            if fit:
                self.n_wall += 1
                return None
        r = self.ray_back                                 # back side of a thin part
        o = rg.Point3d(p.X + nref[0] * r, p.Y + nref[1] * r, p.Z + nref[2] * r)
        ray = rg.Ray3d(o, rg.Vector3d(-nref[0], -nref[1], -nref[2]))
        t = rg.Intersect.Intersection.MeshRay(m, ray)
        if t >= 0:
            hit = ray.PointAt(t)
            mh = m.ClosestMeshPoint(hit, 0.0)
            if mh is not None and self.face_dot(mh, nref) >= min_dot:
                self.n_back += 1
                return hit
        return None


# -----------------------------------------------------------------------------
# Building a patch: sides -> constraints -> fit -> surfaces
# -----------------------------------------------------------------------------

class BuildMixin(object):
    def undo_patch(self):
        if not self.patches: return
        ids = self.patches.pop()
        for i in ids:
            sc.doc.Objects.Delete(i, True)
        sc.doc.Views.Redraw()
        self.reset_polygon()
        self.refresh()
        print("[UNDO] removed {} patch(es), polygon cleared".format(len(ids)))

    def resolve_side(self, k, ox, use_mesh):
        sd_ = self._resolve_side(k, ox, use_mesh)
        ax = self.mirror_axis if k in self.mirror_sides else None
        if ax is not None:
            pts = []
            for i in range(61):
                q = sd_.fn(i / 60.0)
                c = [q.X, q.Y, q.Z]; c[ax] = 0.0
                pts.append(rg.Point3d(c[0], c[1], c[2]))
            sd_ = Side("scan", polyline_fn(pts), ox, "mirror")
        return sd_

    def _resolve_side(self, k, ox, use_mesh):
        a = self.corners[k]; b = self.corners[(k + 1) % 4]
        pick = None if self.shape.get(k) else self.common(a, b)
        if pick is None and self.shape.get(k):
            crv = self.side_curve(k); dom = crv.Domain
            pts = []
            for i in range(61):
                p = crv.PointAt(dom.ParameterAt(i / 60.0))
                if use_mesh and 0 < i < 60:
                    q = self.scan_pt(p)
                    if q is not None: p = q
                pts.append(p)
            return Side("scan", polyline_fn(pts), ox, "scan")
        if pick is None:
            pts = project_line_samples(a["pt"], b["pt"], self.scan_pt if use_mesh else None)
            return Side("scan", polyline_fn(pts), ox, "scan")
        ci, t0, t1 = pick
        c = self.cands[ci]
        if c.kind == "curve":
            sd_ = Side("curve", curve_fn(c.crv, t0, t1), ox, "curve")
            try:
                nc = c.crv.ToNurbsCurve()
                if nc.SpanCount == 1: sd_.along_deg = nc.Degree
            except Exception:
                pass
            return sd_
        return edge_side(c, a["pt"], b["pt"], ox)

    def build(self):
        red, blue = self.dir_cv(0), self.dir_cv(1)      # None = auto (scan)
        fit_mode = self.s_fit(); auto = self.s_spans() == "Auto"
        fit_pts = None
        if fit_mode == "Points":
            fit_pts = self.collect_fit_points()
            if fit_pts is None:
                print("[INFO] cancelled"); return
        busy = show_busy("Building patch...")
        try:
            self._build(red, blue, fit_mode, auto, fit_pts)
        finally:
            hide_busy(busy)
            self.fit_pts = []
            self.nsign = None

    def _build(self, red, blue, fit_mode, auto, fit_pts):
        self.prep_front()
        self.mirror_axis = MIRROR_AXIS.get(self.s_mirror())
        self.mirror_sides = [k for k in range(4) if self.is_mirror_side(k)]
        mirror_lv = dict((k, self.level_for_side(k)) for k in self.mirror_sides)
        for k in self.mirror_sides:                      # snap to the plane
            ax = self.mirror_axis
            for cc in (self.corners[k], self.corners[(k + 1) % 4]):
                c = [cc["pt"].X, cc["pt"].Y, cc["pt"].Z]; c[ax] = 0.0
                cc["pt"] = rg.Point3d(c[0], c[1], c[2])
            if k in self.shape:
                for i, q in enumerate(self.shape[k]):
                    c = [q.X, q.Y, q.Z]; c[ax] = 0.0
                    self.shape[k][i] = rg.Point3d(c[0], c[1], c[2])
        ox = tuple(sum(T(c["pt"])[k] for c in self.corners) / 4.0 for k in range(3))
        on_scan = fit_mode in ("Mesh", "NoScan")          # edges are projected onto the scan
        sides = [self.resolve_side(k, ox, on_scan) for k in range(4)]
        levels = [self.level_for_side(k) if s.kind == "edge" else 0 for k, s in enumerate(sides)]
        desc = []
        for k, s in enumerate(sides):
            if s.kind == "edge": desc.append("{}:{}".format(k + 1, SIDE_LV_NAME[self.raw_side_level(k)]))
            elif s.kind == "curve": desc.append("{}:crv".format(k + 1))
            elif k in self.mirror_sides: desc.append("{}:mirror {}".format(k + 1, LEVEL_NAME[mirror_lv[k]]))
            else: desc.append("{}:scan".format(k + 1))
        print("\n=== Patch {} | {} | {} | {} | sides {} ===".format(
            len(self.patches) + 1,
            self.size_text(),
            fit_mode, "Auto spans" if auto else "Single",
            ", ".join(desc) if desc else "all on scan"))
        self.warn_scan_sides(sides)
        fs = [s.fast for s in sides]
        coons = lambda u, v: coons_point(fs, u, v)
        self.nsign = self.patch_sign(coons, ox)

        def iso_lines(direction):
            lines = []
            for c in (0.0, 0.5, 1.0):
                pts = []
                for i in range(61):
                    t = i / 60.0
                    p = coons(t, c) if direction == 0 else coons(c, t)
                    q = self.scan_pt(rg.Point3d(p[0] + ox[0], p[1] + ox[1], p[2] + ox[2]))
                    if q is not None: p = v_sub(T(q), ox)
                    pts.append(p)
                lines.append(pts)
            return lines

        def rows_why(d):
            ends = (1, 3) if d == 0 else (0, 2)
            parts = []
            for k in ends:
                if sides[k].kind == "edge": lab = SIDE_LV_NAME[self.raw_side_level(k)]
                elif sides[k].kind == "scan": lab = "scan, {} later".format(LEVEL_NAME[self.level_for_side(k)])
                else: lab = "curve"
                parts.append("{}({})".format(self.side_rows(k), lab))
            return " + ".join(parts)

        def count(d, n):
            ends = (1, 3) if d == 0 else (0, 2)
            why = rows_why(d)
            if fit_mode != "NoScan" and all(sides[k].kind == "scan" for k in ends):
                lay = [self.layout_for_side(k_) if s_.kind == "edge" else 0 for k_, s_ in enumerate(sides)]
                need = self.auto_cv(d, sides, lay, iso_lines, auto)[0]
                if need > n:
                    return need, why + ", raised to {} for the scan".format(need)
            return n, why
        nu, why_u = count(0, red)
        nv, why_v = count(1, blue)
        print("  PINK {}CV ({})   BLUE {}CV ({})".format(nu, why_u, nv, why_v))
        red, blue = nu, nv
        deg_u, deg_v = red - 1, blue - 1
        su = sv = 1
        self.corner_check(sides, levels)
        for k, s in enumerate(sides):
            if s.kind == "edge" and levels[k] > 0 and not s.exact:
                print("  [INFO] edge {} is trimmed / not iso - continuity is approximated".format(k + 1))
            if s.kind in ("edge", "curve") and s.along_deg and s.along_deg <= 7:
                if k % 2 == 0 and s.along_deg > deg_u:
                    print("  [INFO] PINK raised to {}CV to follow side {} exactly".format(s.along_deg + 1, k + 1)); deg_u = s.along_deg
                if k % 2 == 1 and s.along_deg > deg_v:
                    print("  [INFO] BLUE raised to {}CV to follow side {} exactly".format(s.along_deg + 1, k + 1)); deg_v = s.along_deg
        if auto and fit_mode == "NoScan":
            print("  [INFO] SrfSplit=AutoSplit needs the scan - NoScan builds one patch")
            auto = False
        if auto:
            tol = self.s_tol()
            print("  AutoSplit, ScanTol={}. PINK direction:".format(fl(tol)))
            su = auto_spans(iso_lines(0), max(deg_u, 1), tol, log=print) if deg_u >= 1 else 1
            print("  BLUE direction:")
            sv = auto_spans(iso_lines(1), max(deg_v, 1), tol, log=print) if deg_v >= 1 else 1
            print("  -> {} x {} = {} patch(es)".format(su, sv, su * sv))

        du = BDir(deg_u, su); dv = BDir(deg_v, sv)
        if fit_mode == "Mesh":
            # Start ON the scan (as v49 did with projected curves): the Coons
            # interior between the drawn sides is projected onto the visible
            # side of the scan first, so the fit never starts under it.
            def start_fn(u, v):
                p = coons(u, v)
                h = 1e-3
                cu = v_sub(coons(min(1.0, u + h), v), coons(max(0.0, u - h), v))
                cv = v_sub(coons(u, min(1.0, v + h)), coons(u, max(0.0, v - h)))
                q = self.scan_pt(rg.Point3d(p[0] + ox[0], p[1] + ox[1], p[2] + ox[2]), v_cross(cu, cv))
                return v_sub(T(q), ox) if q is not None else p
            P0 = fit_to_function(du, dv, start_fn)
        else:
            P0 = fit_to_function(du, dv, coons)
        prob = FitProblem(du, dv, P0)
        for k, s in enumerate(sides):
            if s.kind == "scan" and k not in self.mirror_sides:
                prob.reserved.append((k, self.level_for_side(k) + 1))

        nu_, nv_ = du.n, dv.n
        for k in self.mirror_sides:
            def rows(r):
                out = []
                for x in range(nu_ if k % 2 == 0 else nv_):
                    if k == 0: i, j = x, r
                    elif k == 2: i, j = x, nv_ - 1 - r
                    elif k == 1: i, j = nu_ - 1 - r, x
                    else: i, j = r, x
                    out.append(i * nv_ + j)
                return out
            plane_val = -ox[self.mirror_axis]           # plane is at 0 in world coords
            prob.mirrors.append((self.mirror_axis, plane_val, rows(0), rows(1) if mirror_lv[k] >= 1 else None,
                                 rows(2) if mirror_lv[k] >= 2 else None))
        corners_uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for k in range(4):
            u, v = corners_uv[k]
            prob.hard.append((u, v, v_sub(T(self.corners[k]["pt"]), ox)))

        w_side = {"Mesh": 2.0, "Points": 1.0, "NoScan": 20.0}.get(fit_mode, 2.0)
        for k, s in enumerate(sides):
            uvf, axis, sign = SIDE_MAP[k]
            along_n = (du.n if k % 2 == 0 else dv.n)
            K = max(10, 3 * along_n + 2)
            ts = [i / float(K - 1) for i in range(K)]
            if s.kind == "scan":
                for t in ts:
                    u, v = uvf(t)
                    prob.soft.append((u, v, s.fast(t), w_side))
                continue
            evs = [s.ev(t) for t in ts] if s.kind == "edge" else None
            for i, t in enumerate(ts):
                u, v = uvf(t)
                tgt = v_sub(evs[i][0], ox) if evs else s.exact_pt(t)
                prob.hard.append((u, v, tgt))
            if s.kind == "edge" and levels[k] >= 1:
                Lm = v_len(s.ev(0.5)[1])
                if Lm < 1e-12:
                    print("  [WARN] edge {}: degenerate tangent, only G0".format(k + 1)); continue
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
                # NARROW patch (fillet) against this neighbour: its cross rows
                # are much shorter than the neighbour's. A free shear there
                # (rows leaning along the seam) was magnified ~1/a^2 into the
                # fillet: its CVs slid back and forth along it = the wave.
                # The rows go straight across instead.
                cn.narrow = a0 < NARROW_RATIO * Lm
                if cn.narrow:
                    cn.flow = [True, True]
                    print("  side {}: narrow patch against its neighbour - rows straight across (no shear)".format(k + 1))
                if self.s_flow() == "Smooth" and not cn.narrow:
                    cn.flow = list(s.at_end)
                    if any(cn.flow):
                        print("  CornerFlow=Smooth: side {} continues the neighbour's outline at corner(s) {}".format(
                            k + 1, ", ".join(str((k + e_) % 4 + 1) for e_ in (0, 1) if cn.flow[e_])))
                prob.conts.append(cn)
                if levels[k] >= 2:
                    self.curvature_tip(k, s, du, dv, P0, ox)

        if fit_mode == "Mesh":
            nsu = max(14, 3 * du.n + 6); nsv = max(14, 3 * dv.n + 6)
            params = [(i / float(nsu - 1), j / float(nsv - 1)) for i in range(nsu) for j in range(nsv)]

            def data_fn(P):
                raw = []
                for (u, v) in params:
                    Dd = srf_derivs(du, dv, P, u, v, 1)
                    s = Dd[(0, 0)]
                    ns_ = v_cross(Dd[(1, 0)], Dd[(0, 1)])
                    q = self.scan_pt(rg.Point3d(s[0] + ox[0], s[1] + ox[1], s[2] + ox[2]),
                                     ns_ if v_len(ns_) > 1e-12 else None, fit=True)
                    if q is None: continue
                    t = v_sub(T(q), ox)
                    raw.append((u, v, t, v_dist(s, t)))
                if not raw: return []
                kk = max(2.5 * median([r[3] for r in raw]), self.size * 1e-7)
                out = []
                for (u, v, t, d) in raw:
                    if d > 6.0 * kk: continue
                    out.append((u, v, t, 1.0 if d <= kk else kk / d))
                return out
            iters = 8
        elif fit_mode == "NoScan":
            # inside = the smoothest surface the edges and G0/G1/G2 allow
            prob.w_fair = 0.1
            data_fn = lambda P: []
            iters = 3
        else:
            # TempPts: through the points, the rest as smooth as the edges allow (a bridge - the scan
            # under it, e.g. a recess, is not used at all)
            prob.w_reg = 0.005
            prob.w_fair = 0.1
            tp = [v_sub(T(p), ox) for p in (fit_pts or [])]
            state = {"uv": [None] * len(tp)}

            def data_fn(P):
                out = []
                for i, p in enumerate(tp):
                    u, v = project_param(du, dv, P, p, state["uv"][i])
                    state["uv"][i] = (u, v)
                    out.append((u, v, p, 10.0))
                return out
            iters = 6 if tp else 1

        self.freedom_report(du, dv, sides, levels, prob)
        P = run_fit(prob, data_fn, iters, log=print)
        P = self.clean_build(P, prob, levels, sides, du, dv, data_fn, iters)

        # ---- make Rhino surfaces ----
        if du.spans > 1 or dv.spans > 1:
            bu, bv = BDir(du.p, 1), BDir(dv.p, 1)
            parts = [(bu, bv, pts) for (_, _, pts) in decompose_surface(du, dv, P)]
        else:
            parts = [(du, dv, P)]
        ids = []
        for (a, b, pts) in parts:
            ns = make_nurbs(a, b, pts, ox)
            if ns is None or not ns.IsValid:
                print("  [ERROR] invalid surface"); continue
            side_ = self.scan_side_sign(ns)
            flipped = False
            if side_ < 0:                     # normal towards the side you work on (the clicked faces)
                try:
                    rv_ = ns.Reverse(0)
                    if rv_ is not None and rv_.IsValid:
                        ns = rv_; side_ = 1; flipped = True
                except Exception:
                    pass
            oid_ = sc.doc.Objects.AddSurface(ns)
            ids.append(oid_)
            try:
                rs.SetUserText(oid_, "msh2classA_fit", fit_mode)
                rs.SetUserText(oid_, "msh2classA_side", str(side_))
                if fit_mode == "Points" and fit_pts:      # kept ON the patch: every button uses them, not the scan
                    rs.SetUserText(oid_, PTS_KEY, json.dumps([[round(c, 6) for c in T(q)] for q in fit_pts]))
            except Exception: pass
            if len(parts) == 1:               # the G0/G1/G2 each side was BUILT with - kept by every button later
                for k_, s_ in enumerate(sides):
                    if s_.kind == "edge":
                        set_side_level(oid_, {1: 3, 3: 1}.get(k_, k_) if flipped else k_, levels[k_])
        sc.doc.Views.Redraw()
        if not ids:
            print("[ERROR] Patch failed."); return
        self.patches.append(ids)
        print("[OK] {} patch(es), {}x{} CV each".format(len(ids), du.p + 1, dv.p + 1))
        self.report(du, dv, P, ox, sides, prob, fit_mode, fit_pts)
        for k in self.mirror_sides:
            uvf, axis, sign = SIDE_MAP[k]
            dmax = tilt = 0.0
            for i in range(21):
                u, v = uvf(i / 20.0)
                D = srf_derivs(du, dv, P, u, v, 1)
                dmax = max(dmax, abs(D[(0, 0)][self.mirror_axis] + ox[self.mirror_axis]))
                n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                tilt = max(tilt, math.degrees(math.asin(min(1.0, abs(n[self.mirror_axis])))))
            print("  Side {} mirror {}: off plane {:.5f}  normal tilt to plane {:.3f} deg".format(
                k + 1, LEVEL_NAME[mirror_lv[k]], dmax, tilt))
        if getattr(self, "n_wall", 0):
            print("  [INFO] scan faces turned away from the patch (walls, ribs, opposite side) were ignored ({} samples over all iterations)".format(self.n_wall))
        if getattr(self, "n_back", 0):
            print("  [INFO] {} scan samples hit the hidden (back) side - taken from the visible side instead".format(self.n_back))

    def warn_scan_sides(self, sides):
        """A side on the SCAN that runs right beside a neighbour's edge leaves
        a gap - say so (the corners were not both on that edge)."""
        for k, s_ in enumerate(sides):
            if s_.kind != "scan" or k in self.mirror_sides: continue
            mid = s_.fn(0.5)
            for c in self.cands:
                if c.kind != "edge" or not c.near_bbox(T(mid), 5.0 * self.edge_snap_dist()): continue
                ok, t = c.crv.ClosestPoint(mid)
                if ok and c.crv.PointAt(t).DistanceTo(mid) < 5.0 * self.edge_snap_dist():
                    print("  [WARN] side {} runs along a neighbour's edge but is fitted to the SCAN (its corners "
                          "are not both on that edge) - expect a gap. UndoSrf and click both corners ON the edge "
                          "(yellow) or its end.".format(k + 1))
                    break

    def auto_cv(self, d, sides, levels, iso_lines, spans_auto):
        """CV count for direction d (0 = PINK/u, 1 = BLUE/v) and the reason."""
        req = 4; why = []                   # class-A: never ruled (2CV) in Auto
        cross = (1, 3) if d == 0 else (0, 2)      # sides whose continuity needs rows in d
        along = (0, 2) if d == 0 else (1, 3)      # sides running along d
        for k in cross:
            if sides[k].kind == "edge" and levels[k] >= 1:
                need = 4 if levels[k] == 1 else 6
                if need > req: req = need
                why.append("{} side {}".format(LEVEL_NAME[levels[k]], k + 1))
        for k in along:
            s = sides[k]
            if s.kind in ("edge", "curve") and s.along_deg and s.along_deg + 1 > req and s.along_deg <= 7:
                req = s.along_deg + 1
                why.append("= {} side {}".format("edge" if s.kind == "edge" else "curve", k + 1))
        if spans_auto:
            if req < 4: req = 4
            return req, ", ".join(why) or "spans auto"
        if req < 6:
            tol = self.s_tol(); lines = iso_lines(d); got = None
            for deg in (3, 5):
                if deg + 1 < req: continue
                worst = max(curve_fit_deviation(pts, deg, 1)[0] for pts in lines)
                if worst <= tol:
                    got = deg + 1; break
            if got is None:
                got = 6
                print("  [INFO] {} direction: even 6CV is {:.3f} off the scan (ScanTol {:.3f}) - "
                      "smaller patch or SrfSplit=AutoSplit".format("PINK" if d == 0 else "BLUE", worst, tol))
            if got > req:
                req = got; why.append("scan")
            elif not why:
                why.append("scan")
        return req, ", ".join(why)

    def curvature_tip(self, k, s, du, dv, P0, ox):
        """G2 forces this patch to START with the neighbour's curvature across
        the edge. If the scan here is flatter, that becomes a visible bump."""
        try:
            uvf, axis, sign = SIDE_MAP[k]
            pt, D1, D2, n = s.ev(0.5)[:4]
            kn = v_dot(D2, n) / max(v_dot(D1, D1), 1e-300)
            u, v = uvf(0.5)
            D = srf_derivs(du, dv, P0, u, v, 2)
            Sc = D[(1, 0)] if axis == 0 else D[(0, 1)]
            Scc = D[(2, 0)] if axis == 0 else D[(0, 2)]
            ks = v_dot(Scc, n) / max(v_dot(Sc, Sc), 1e-300)
            uo, vo = SIDE_MAP[(k + 2) % 4][0](0.5)
            W = v_dist(srf_point(du, dv, P0, u, v), srf_point(du, dv, P0, uo, vo))
            bump = (abs(kn) - abs(ks)) * W * W / 12.0     # only when the neighbour is MORE curved
            if abs(kn) > 2.0 * abs(ks) and bump > max(0.2 * self.s_tol(), 0.05):
                print("  [TIP] side {}: the neighbour is curved across the edge (R {:.0f}) and the scan here is "
                      "flatter (R {:.0f}). G2 makes this patch start with the neighbour's curvature -> expect a "
                      "bump of about {:.2f}. Better: build the FLATTER patch first and the curved one G2 to it "
                      "(the curvature then ramps inside the curved patch), or use G1 on this side.".format(
                          k + 1, 1.0 / abs(kn) if abs(kn) > 1e-9 else float("inf"),
                          1.0 / abs(ks) if abs(ks) > 1e-9 else float("inf"), bump))
        except Exception:
            pass

    def corner_check(self, sides, levels):
        """Corner k joins side k-1 (its end) and side k (its start)."""
        for k in range(4):
            a, b = sides[(k - 1) % 4], sides[k]
            if not (a.kind == "edge" and b.kind == "edge"): continue
            if levels[(k - 1) % 4] < 1 or levels[k] < 1: continue
            try:
                na = a.ev(1.0)[3]; nb = b.ev(0.0)[3]
                ang = math.degrees(math.acos(min(1.0, abs(v_dot(v_unit(na), v_unit(nb))))))
            except Exception:
                continue
            if ang > 0.05:
                print("  [WARN] corner {}: the two neighbour surfaces meet at {:.2f} deg there - "
                      "G1 near this corner cannot be better than that.".format(k + 1, ang))

    def build_check(self, P, prob, du, dv):
        """-> (CVs jumped over each other?, worst G1 kink in deg over the
        continuity sides, away from the corners)."""
        cross = cv_order_worst(P, du.n, dv.n)[0] < 0.0
        worst = 0.0
        for c in prob.conts:
            uvf, axis, sign = SIDE_MAP[c.side]
            for smp in c.samples:
                u, v = smp[0], smp[1]
                t = smp[9] if len(smp) >= 10 else 0.5
                if not (0.15 <= t <= 0.85):
                    continue
                D = srf_derivs(du, dv, P, u, v, 1)
                n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                E1 = smp[6] if len(smp) >= 10 else None
                if E1 is None:
                    continue
                nb = v_unit(v_cross(E1, smp[4]))
                if v_len(nb) < 0.5:
                    continue
                worst = max(worst, math.degrees(math.acos(min(1.0, abs(v_dot(n, nb))))))
        return cross, worst

    def clean_build(self, P, prob, levels, sides, du, dv, data_fn, iters):
        """A narrow patch tied G2 on its long sides AND on a short end at a
        corner of four patches cannot meet all of it - the leftover went into
        CVs sliding over each other. Then the SHORT end gives way (G2 -> G1 ->
        G0) and the patch is built again; it says so."""
        tmp = SPatch("_", du.p, dv.p, P)
        shorts = [c for c in prob.conts if short_side(tmp, c.side)]
        if not shorts or not any(getattr(c, "narrow", False) for c in prob.conts):
            return P
        cross, worst = self.build_check(P, prob, du, dv)
        if not cross and worst <= 2.0 * SEAM_ANG:
            return P
        snap = lambda: (list(prob.conts), [(c, c.level) for c in prob.conts], dict(levels) if isinstance(levels, dict)
                        else list(levels))
        best = (cross, worst, P, snap())
        for step in range(2):
            changed = []
            for c in shorts:
                if c not in prob.conts:
                    continue
                if c.level >= 2:
                    c.level = 1
                else:
                    prob.conts.remove(c)              # G0 only (its edge stays on the neighbour's)
                levels[c.side] = c.level if c in prob.conts else 0
                changed.append("side {} {}".format(c.side + 1, LEVEL_NAME[levels[c.side]]))
            if not changed:
                break
            for c in prob.conts:
                c.a = c.a_init; c.b = 0.0; c.beta = [0.0, 0.0]; c.gam = [0.0, 0.0]; c.a_fixed = False
            P2 = run_fit(prob, data_fn, iters)
            cross2, worst2 = self.build_check(P2, prob, du, dv)
            print("  [clean] short end of a narrow patch at a corner: {} -> CVs {}, kink on the long sides {:.2f} deg".format(
                ", ".join(changed), "cross" if cross2 else "in order", worst2))
            if (cross2, worst2) < (best[0], best[1]):
                best = (cross2, worst2, P2, snap())
            if not cross2 and worst2 <= 2.0 * SEAM_ANG:
                break
        P = best[2]
        conts_, lvls_, levels_ = best[3]
        prob.conts[:] = conts_
        for (c, lv) in lvls_:
            c.level = lv
        for k_ in range(len(levels)):
            levels[k_] = levels_[k_]
        if best[0]:
            print("  [WARN] the CV net of this narrow patch still crosses - rebuild it wider, or as ONE patch "
                  "across the split of its neighbours")
        return P

    def freedom_report(self, du, dv, sides, levels, prob):
        """How much of the patch the scan can still shape."""
        locked = set()
        nu, nv = du.n, dv.n
        for k, s in enumerate(sides):
            L = levels[k] if s.kind == "edge" else 0      # scan/curve: boundary row follows the side
            for r in range(L + 1):
                for x in range(nu if k % 2 == 0 else nv):
                    if k == 0: locked.add((x, r))
                    elif k == 2: locked.add((x, nv - 1 - r))
                    elif k == 1: locked.add((nu - 1 - r, x))
                    else: locked.add((r, x))
        free = nu * nv - len(locked)
        bulges = len(prob.conts)
        if prob.conts and free == 0:
            print("  Scan can shape: 0 inner CV + {} bulge value(s). The patch is defined by its neighbours "
                  "(class-A blend). If it is far from the scan: make the patch narrower or split it.".format(bulges))
        elif prob.conts:
            print("  Scan can shape: {} inner CV + {} bulge value(s).".format(free, bulges))

    def report(self, du, dv, P, ox, sides, prob, fit_mode, fit_pts):
        devs = []
        if fit_mode in ("Mesh", "NoScan"):
            n = 25
            for i in range(n):
                for j in range(n):
                    s = srf_point(du, dv, P, i / (n - 1.0), j / (n - 1.0))
                    q = self.scan_pt(rg.Point3d(s[0] + ox[0], s[1] + ox[1], s[2] + ox[2]))
                    if q is not None: devs.append(v_dist(s, v_sub(T(q), ox)))
            label = "scan" if fit_mode == "Mesh" else "scan (NoScan - for information only)"
        else:
            for p in (fit_pts or []):
                tp = v_sub(T(p), ox)
                u, v = project_param(du, dv, P, tp)
                devs.append(v_dist(srf_point(du, dv, P, u, v), tp))
            label = "points"
        if devs:
            print("  Deviation to {}: P90 {}  max {}  avg {}".format(
                label, fl(percentile(devs, 0.9)), fl(max(devs)), fl(sum(devs) / len(devs))))
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
                n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                a_ = math.degrees(math.acos(min(1.0, abs(v_dot(n, nb)))))
                if 0.15 <= t <= 0.85: ang = max(ang, a_)
                else: angc = max(angc, a_)
                if c.level >= 2:
                    Sc = v_mul(D[(1, 0)] if axis == 0 else D[(0, 1)], sign)
                    Scc = D[(2, 0)] if axis == 0 else D[(0, 2)]
                    k1 = v_dot(Scc, n) / max(v_dot(Sc, Sc), 1e-300)
                    k2 = normal_curv_neighbour(Sc, D1, D2, evt[4], evt[5], evt[6], n)
                    dk = max(dk, abs(k1 - k2)); kmax = max(kmax, abs(k2))
            line = "  Side {} {}: G0 gap {}  G1 angle {:.3f} deg (near corners {:.3f})".format(
                c.side + 1, LEVEL_NAME[c.level], fl(gap), ang, angc)
            if c.level >= 2:
                line += "  G2 dK {:.2e} ({:.1f}% of K)".format(dk, 100.0 * dk / kmax if kmax > 1e-12 else 0.0)
            print(line)
        if any(True for c in prob.conts) and len(prob.conts) >= 3:
            print("  Note: near corners G1 can only be as good as the neighbours agree with each other there.")


# -----------------------------------------------------------------------------
# Smoothing finished patches (neighbours keep continuity)
# -----------------------------------------------------------------------------

# patch buttons in the order of the work:
#   row 1  the CV net (UV, shape stays)     EvenUV  StrUV  RelaxUV
#   row 2  the shape (N, net stays)         PullN   FairN  SmthUVN (both at once)  RstN
#   row 3  special                          ToDevUV MatchUVN FreeEdgeEdt Del
#   (Fillet, NPatch, GlobalMatch: the Surfaces group of the view panel - they work on many patches)
SRF_ROWS = ((("even", "EvenUV"), ("straight", "StrUV"), ("relax", "RelaxUV")),
            (("pull", "PullN"), ("fair", "FairN"), ("smooth", "SmthUVN"), ("reset", "RstN")),
            (("todev", "ToDevUV"), ("match", "MatchUVN"), ("edge", "FreeEdgeEdt"), ("del", "Del")))


def srf_button_rects(cx, cy):
    """[(mode, label, centre x, centre y, half width)] around the patch centre."""
    out = []; H = 22
    y0 = cy - H * (len(SRF_ROWS) - 1) / 2.0
    for r, row in enumerate(SRF_ROWS):
        ws = [3.5 * len(lb) + 9 for (_, lb) in row]
        total = sum(2 * w for w in ws) + 8 * (len(row) - 1)
        x = cx - total / 2.0
        for (mode, lb), w in zip(row, ws):
            out.append((mode, lb, x + w, y0 + H * r, w))
            x += 2 * w + 8
    return out   # (mode, label, dy px)


class SmoothMixin(object):
    """Hover a finished patch: small buttons in its centre (see SRF_ROWS) -
    one step per click; all keep every seam at its G level."""

    def patch_quality(self, oid):
        """Numbers + green / yellow / red per patch button (cached until the
        next change). FairN waves, EvenUV spacing, StrUV kink, RelaxUV skew,
        PullN scan deviation, MatchUVN seams open or below their G level."""
        cache = self.__dict__.setdefault("qcache", {})
        if oid in cache: return cache[oid]
        res = None
        try:
            got = self.load_patches(oid, "")
            if got is not None:
                A, sp, freeze, ox, tol = got
                allp = [v[0] for v in sp.values()]
                seams, mine = match_seams(A, allp, max(tol, 3.0 * self.s_tol()))
                fixed = set(); open_ = 0
                for (key, sm, B, k, lv) in mine:
                    fine = seam_measure(dict(sm), tol)
                    if fine < lv: open_ += 1             # open, or below the G it should keep
                    if fine < 0: fine = 0
                    for r in range(fine + 1):
                        fixed.update(side_row_indices(A.du, A.dv, k, r))
                q = net_quality(A, fixed)
                saved = self.refs; self.refs = []
                try:
                    dev = self.patch_dev(A, A.P, ox)
                finally:
                    self.refs = saved
                tolm = self.s_tol()
                st = {"fair": quality_state("waves", q["waves"]),
                      "even": quality_state("spacing", q["spacing"]),
                      "straight": quality_state("kink", q["kink"]),
                      "relax": quality_state("skew", q["skew"]),
                      "pull": "red" if dev > tolm else ("yellow" if dev > 0.5 * tolm else "green"),
                      "match": "red" if open_ else "green"}
                txt = "this patch:  waves {} | spacing {:.1f}x | kink {:.0f} deg | skew {:.0f}% | scan {} | seams {}".format(
                    q["waves"], q["spacing"], q["kink"], q["skew"], fl(dev),
                    "{} not at their G (MatchUVN)".format(open_) if open_ else "ok")
                res = {"state": st, "text": txt}
        except Exception:
            res = None
        cache[oid] = res
        return res

    def srf_centre(self, oid):
        cache = self.__dict__.setdefault("centre_cache", {})
        if oid not in cache:
            for (sid, brep, bb, ok) in self.surfs:
                if sid == oid:
                    f = brep.Faces[0]
                    cache[oid] = f.PointAt(f.Domain(0).Mid, f.Domain(1).Mid)
        return cache.get(oid)

    def _btn_under(self, m, sx, sy):
        hs = self.hover_srf
        if hs is None or not hs[2]: return None
        cx, cy = to_screen(m, T(hs[1]))
        for (mode, label, x, y, w) in srf_button_rects(cx, cy):
            if abs(sx - x) < w and abs(sy - y) < 10:
                return mode
        return None

    def update_srf_buttons(self, vp, m, sx, sy):
        """Keep the buttons of the patch while the mouse is on them, else
        show the buttons of the patch under the cursor."""
        if getattr(self, "match_mode", None):
            self.hover_srf = None
            mm = self.match_mode; mm["hot"] = self._match_under(m, sx, sy)
            return
        if getattr(self, "fillet_mode", None):
            self.hover_srf = None
            fm = self.fillet_mode
            try:
                fm["hot"] = self._fm_corner_at(m, sx, sy)
            except Exception:
                fm["hot"] = None
            return
        if getattr(self, "edge_mode", None):
            self.hover_srf = None
            return
        if self.hover_srf is not None:
            hot = self._btn_under(m, sx, sy)
            if hot is not None:
                self.hover_srf = (self.hover_srf[0], self.hover_srf[1], self.hover_srf[2], hot)
                return
        hs = self.surface_under(vp, m, sx, sy)
        if hs is None:
            self.hover_srf = None; return
        c = self.srf_centre(hs[0]) or hs[1]
        self.hover_srf = (hs[0], c, hs[2], None)

    def srf_button_at(self, m, sx, sy):
        """(object id, mode) of the button clicked, or None."""
        mode = self._btn_under(m, sx, sy)
        return None if mode is None else (self.hover_srf[0], mode)

    def draw_srf_buttons(self, d, vp):
        hs = self.hover_srf
        if hs is None: return
        if getattr(self, "fillet_mode", None) or getattr(self, "match_mode", None) or getattr(self, "edge_mode", None):
            return
        m = xf_tuple(vp)
        cx, cy = to_screen(m, T(hs[1]))
        if not hs[2]:
            try:
                isnp = bool(rs.GetUserText(hs[0], NPATCH_KEY))
            except Exception:
                isnp = False
            d.DrawDot(hs[1], "NPatch" if isnp else "trimmed / multi-span", C_GRAY, col(255, 255, 255))
            return
        qs = self.patch_quality(hs[0])
        colours = {"green": col(150, 225, 150), "yellow": col(255, 222, 110), "red": col(245, 110, 110)}
        for (mode, label, x, y, w) in srf_button_rects(cx, cy):
            hot = hs[3] == mode
            st = (qs or {}).get("state", {}).get(mode)
            bg = C_HOVER if hot else colours.get(st, col(255, 255, 255))
            fg = col(0, 0, 0)
            if mode == "del" and not hot:               # set apart: dark, not a 'fix' button
                bg = col(80, 80, 80); fg = col(255, 255, 255)
            try:
                d.DrawDot(float(x), float(y), label, bg, fg)
            except Exception:
                d.DrawDot(hs[1], label, bg, col(0, 0, 0))

    def surface_under(self, vp, m, sx, sy):
        """(object id, hit point, smoothable?) of the surface under the cursor."""
        cand = []
        for (oid, brep, bb, ok) in self.surfs:
            xs = []; ys = []
            for x in (bb[0][0], bb[1][0]):
                for y in (bb[0][1], bb[1][1]):
                    for z in (bb[0][2], bb[1][2]):
                        a, b = to_screen(m, (x, y, z)); xs.append(a); ys.append(b)
            if min(xs) <= sx <= max(xs) and min(ys) <= sy <= max(ys):
                cand.append((oid, brep, ok))
        if not cand:
            return None
        res = vp.GetFrustumLine(sx, sy)
        line = res[1] if isinstance(res, tuple) else res
        lc = rg.LineCurve(line)
        cam = vp.CameraLocation
        best = None
        for (oid, brep, ok) in cand:
            r = rg.Intersect.Intersection.CurveBrep(lc, brep, sc.doc.ModelAbsoluteTolerance)
            pts = r[2] if isinstance(r, tuple) else []
            for pt in pts or []:
                d = pt.DistanceTo(cam)
                if best is None or d < best[0]:
                    best = (d, oid, pt, ok)
        return None if best is None else (best[1], best[2], best[3])

    def adjust_patch(self, oid, mode):
        """One Smooth or Pull2Msh step on patch oid. Same machinery for both
        (adjust_step): only the target for the patch itself differs. Direct
        neighbours change only their continuity rows, all seams are kept,
        the scan leash is the same, UndoSrfEdit reverts either."""
        if mode in ("even", "straight", "relax", "todev"):
            return self.layout_patch(oid, mode)
        if mode == "match":
            return self.enter_match(oid)
        if mode == "edge":
            return self.enter_edge(oid)
        if mode == "del":
            return self.delete_patch(oid)
        if mode == "reset":
            return self.reset_patch(oid)
        tag = {"smooth": "[SmthUVN]", "fair": "[FairN]", "pull": "[PullN]"}[mode]
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        saved_refs = self.refs; self.refs = []           # no clicks of an old polygon here
        try:
            if mode in ("smooth", "fair"):
                data_fn = None
                target = fair_target(0.5) if mode == "smooth" else fair_normal_target(0.5)
            else:
                # PullN: fit to the scan, but the CVs move only along the normal
                # (free edges also across the edge) - the CV layout from EvenUV /
                # StrUV / RelaxUV stays; neighbours give way with their rows
                data = self.scan_samples(A, A.P, ox)
                if len(data) < A.n():
                    print(tag + " too little scan under this patch ({} samples). Nothing changed.".format(len(data)))
                    return
                data_fn = None
                target = normal_only(scan_target(data, 1.0))
            allp_ = [v[0] for v in sp.values()]

            def compute(keep_levels):
                moving, used, new = adjust_step(A, allp_, target, self.seam_tol(tol), freeze, data_fn,
                                                self.mirror_spec(ox), None, None, None, keep_levels)
                if not A.move:
                    return None
                # leash: scan deviation may not grow above max(ScanTol, before).
                # PullN: only for the pulled patch - the neighbours may give way
                old = dict((p.key, list(p.P)) for p in moving)
                free_keys = set()                         # NoScan patches: no scan leash
                if mode == "pull":
                    free_keys.update(p.key for p in moving if p is not A)
                for p in moving:
                    try:
                        if rs.GetUserText(p.key, "msh2classA_fit") == "NoScan" and not (mode == "pull" and p is A):
                            free_keys.add(p.key)
                    except Exception:
                        pass
                before = dict((p.key, self.patch_dev(p, old[p.key], ox)) for p in moving)
                alpha = 1.0; after = before; trial = old
                for _ in range(6):
                    trial = dict((p.key, [v_lerp(a, b, alpha) for a, b in zip(old[p.key], new[p.key])]) for p in moving)
                    after = dict((p.key, self.patch_dev(p, trial[p.key], ox)) for p in moving)
                    if all(after[k] <= max(self.s_tol(), before[k]) + 1e-9 or k in free_keys for k in after):
                        break
                    alpha *= 0.5
                return moving, used, trial, alpha, before, after
            res = compute(None)
            if res is None:
                print(tag + " nothing can move: every CV of this patch belongs to a continuity with a patch "
                      "that is not a direct neighbour (e.g. 6x6 with G2 on all sides). Nothing changed.")
                return
        finally:
            self.refs = saved_refs
        moving, used, trial, alpha, before, after = res
        if not self.write_cvs(moving, trial, sp, ox, quiet=True):
            # a seam that is broken already (a fillet built badly) could only be
            # repaired at the neighbours' cost: leave it as it is and do the rest
            keep_all = {}
            for sm in find_seams(allp_, self.seam_tol(tol)):
                keep_all[seam_key(sm)] = seam_level(sm, self.seam_tol(tol))
            saved_refs = self.refs; self.refs = []
            try:
                res = compute(keep_all)
            finally:
                self.refs = saved_refs
            if res is None:
                return
            moving, used, trial, alpha, before, after = res
            if not self.write_cvs(moving, trial, sp, ox):
                return
            print("    broken seam(s) were left as they are (repairing them would spoil the neighbours) - "
                  "MatchUVN on the narrow patch rebuilds it")
        nb = [p for p in moving if p is not A]
        print("{} {} neighbour(s) adjusted (only their continuity rows){}".format(
            tag, len(nb), "" if alpha == 1.0 else ", step reduced to {:.0%} by the scan leash".format(alpha)))
        for sm in used:
            print("    seam kept {}".format(LEVEL_NAME.get(sm["level"], "?")))
        print("    scan deviation of this patch (max): {} -> {} (ScanTol {})".format(
            fl(before[A.key]), fl(after[A.key]), fl(self.s_tol())))
        for p in nb:
            if after[p.key] > before[p.key] + 1e-4:
                print("    neighbour gave way: its scan deviation {} -> {}{}".format(
                    fl(before[p.key]), fl(after[p.key]), "  (pull it too if that is too much)" if after[p.key] > self.s_tol() else ""))
        if freeze:
            print("    rows next to trimmed / multi-span surfaces were kept as they are")
        self.refresh()

    def load_patches(self, oid, tag, extra=None):
        """Patch oid + nearby single-span surfaces as SPatch (centred by ox),
        and rows to keep next to surfaces that cannot take part.
        -> (A, {id: (SPatch, NurbsSurface)}, freeze, ox, seam tol) or None."""
        tol = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 2e-6)
        A_obj = None; near = []; bad = []
        for (sid, brep, bb, ok) in self.surfs:
            if sid == oid: A_obj = (sid, brep, bb, ok)
        if A_obj is None or not A_obj[3]:
            print(tag + " this surface cannot be changed (trimmed, multi-span or rational)."); return None
        bbA = A_obj[2]
        if extra:                                  # several patches at once (all sides of a hole)
            lo = list(bbA[0]); hi = list(bbA[1])
            for (sid, brep, bb, ok) in self.surfs:
                if sid in extra:
                    for c in range(3):
                        lo[c] = min(lo[c], bb[0][c]); hi[c] = max(hi[c], bb[1][c])
            bbA = (tuple(lo), tuple(hi))
        pad = 0.05 * v_dist(bbA[0], bbA[1])
        for item in self.surfs:
            sid, brep, bb, ok = item
            if sid == oid: continue
            if any(bb[1][c] < bbA[0][c] - pad or bb[0][c] > bbA[1][c] + pad for c in range(3)): continue
            (near if ok else bad).append(item)
        ox = tuple(0.5 * (bbA[0][c] + bbA[1][c]) for c in range(3))
        sp = {}
        for (sid, brep, bb, ok) in [A_obj] + near:
            ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
            P = [v_sub(T(ns.Points.GetControlPoint(i, j).Location), ox)
                 for i in range(ns.Points.CountU) for j in range(ns.Points.CountV)]
            sp[sid] = (SPatch(sid, ns.Degree(0), ns.Degree(1), P), ns)
            sp[sid][0].want = side_levels(sid)
        A = sp[oid][0]
        # sides lying on surfaces we cannot handle: keep 3 rows there
        freeze = {}
        btol = {}
        for (bid, bbrep, bbb, bok) in bad:        # an NPatch fits the edges only to a tolerance
            try:
                btol[bid] = self.np_tol(tol) if rs.GetUserText(bid, NPATCH_KEY) else tol
            except Exception:
                btol[bid] = tol
        for sid, (p, ns) in sp.items():
            for k in range(4):
                hits = 0
                for t in (0.2, 0.4, 0.6, 0.8):
                    q = v_add(p.val(k, t, "pt"), ox)
                    for (bid, bbrep, bbb, bok) in bad:
                        cp = bbrep.ClosestPoint(P3(q))
                        if cp.DistanceTo(P3(q)) < btol.get(bid, tol):
                            hits += 1; break
                if hits >= 3:
                    freeze.setdefault(sid, []).append((k, 3))
        return A, sp, freeze, ox, tol

    def seam_tol(self, tol):
        """Seams are FOUND with this (an earlier step may have opened one a
        little - it must still count as a seam and be closed again)."""
        return max(tol, 2.0 * self.s_tol())

    def mirror_spec(self, ox):
        """(axis, plane value in centred coords, tol) of MirrorPlane, or None."""
        ax = MIRROR_AXIS.get(self.s_mirror())
        if ax is None: return None
        return (ax, -ox[ax], max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 1e-5))

    # ---- Match: neighbours back to a hand-edited patch -----------------------
    def enter_match(self, oid):
        """Seam bubbles on every side of the patch where a neighbour is (also
        if hand edits opened the seam); click a bubble = G0 -> G1 -> G2, then
        Apply. The patch stays, the neighbours come to it. No scan."""
        tag = "[MatchUVN]"
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        loose = max(tol, 3.0 * self.s_tol())          # hand edits may have opened the seam
        allp = [v[0] for v in sp.values()]
        seams, mine = match_seams(A, allp, loose)
        dflt = {"G0": 0, "G1": 1, "G2": 2}.get(self.s_cont(), 2)
        items = []
        ms = self.mirror_spec(ox)
        if ms:                                      # sides near the mirror plane (drawn without snapping)
            axis, val, mt = ms
            diag = v_dist(tuple(min(q[c] for q in A.P) for c in range(3)), tuple(max(q[c] for q in A.P) for c in range(3)))
            near = max(self.s_mtol(), loose, 0.03 * diag)
            taken = set(k for (key, sm, B, k, lv) in mine)
            exact = dict((k, (2 if r2 else (1 if r1 else 0))) for (k, r0, r1, r2) in mirror_rows(A, ms))
            for k in range(4):
                if k in taken: continue
                r0 = side_row_indices(A.du, A.dv, k, 0)
                off_ = max(abs(A.P[i][axis] - val) for i in r0)
                if off_ > near: continue
                q = v_add(A.val(k, 0.5, "pt"), ox)
                now = ("mirror " + LEVEL_NAME[exact[k]]) if k in exact else "{} off the mirror".format(fl(off_))
                items.append({"key": ("mirror", k), "pos": P3(q), "now": now, "want": dflt})
        if not ms:                                  # MirrorPlane Off: is a side near x=0 / y=0 / z=0?
            for ax_, nm_ in ((0, "YZ"), (1, "XZ"), (2, "XY")):
                for k in range(4):
                    r0 = side_row_indices(A.du, A.dv, k, 0)
                    if max(abs(A.P[i][ax_] + ox[ax_]) for i in r0) <= max(self.s_mtol(), loose):
                        self.flash("side {} lies near the {} plane - set MirrorPlane={} and click MatchUVN again".format(
                            k + 1, nm_, nm_), oid)
                        return
        if not mine and not items:
            self.flash("MatchUVN: no neighbour and no mirror plane along the sides of this patch", oid); return
        for (key, sm, B, k, lv) in mine:
            ta, tb = (sm["ta"], sm["tb"]) if sm["X"] is A else (sm["sa"], sm["sb"])
            q = v_add(A.val(k, 0.5 * (ta + tb), "pt"), ox)
            fine = seam_measure(dict(sm), tol)
            now = "gap {}".format(fl(sm["gap"])) if fine < 0 else LEVEL_NAME.get(fine, "?")
            # proposed: the level this seam was BUILT / last matched with (stored
            # on the patches) - after hand edits that is what it should go back to
            stored = max(A.want.get(k, -1) if hasattr(A, "want") else -1,
                         B.want.get(sm["l"] if sm["X"] is A else sm["k"], -1) if hasattr(B, "want") else -1)
            items.append({"key": key, "pos": P3(q), "now": now, "want": stored if stored >= 0 else dflt,
                          "src": "stored" if stored >= 0 else "not stored - set it"})
        self.match_mode = {"oid": oid, "items": items, "loose": loose, "hot": None,
                           "centre": self.srf_centre(oid)}
        print(tag + " {} seam(s): click a bubble to choose G0 / G1 / G2, then Apply "
              "(right click = cancel). The patch stays; the neighbours come to it.".format(len(items)))

    def flash_at(self, text, pt, secs=6.0):
        """flash() at a point of the model (not at a patch centre)."""
        print("  " + text)
        self.flash_msg = (text, pt, time.time() + secs)
        sc.doc.Views.Redraw()

    def flash(self, text, oid=None, secs=6.0):
        """A message on screen at the patch centre for a few seconds (and in
        the console) - so 'nothing happens' never happens silently."""
        print("  " + text)
        pos = self.srf_centre(oid) if oid is not None else None
        self.flash_msg = (text, pos, time.time() + secs)
        sc.doc.Views.Redraw()

    def draw_flash(self, d):
        fm = getattr(self, "flash_msg", None)
        if not fm: return
        if time.time() > fm[2]:
            self.flash_msg = None; return
        try:
            if fm[1] is not None:
                try:                       # ABOVE the patch buttons (they sit around the centre)
                    sp_ = d.Viewport.WorldToClient(fm[1])
                    d.DrawDot(float(sp_.X), float(sp_.Y) - 75.0, fm[0], col(255, 235, 150), col(0, 0, 0))
                except Exception:
                    d.DrawDot(fm[1], fm[0], col(255, 235, 150), col(0, 0, 0))
            else:
                lm = getattr(self, "last_mouse", None)
                if lm is not None:
                    d.DrawDot(float(lm[1]) + 20.0, float(lm[2]) - 30.0, fm[0], col(255, 235, 150), col(0, 0, 0))
                else:
                    d.Draw2dText(fm[0], col(0, 0, 0), rg.Point2d(40, 40), False, 14)
        except Exception:
            pass

    def _match_rects(self, m):
        mm = self.match_mode
        out = []
        for n, it in enumerate(mm["items"]):
            x, y = to_screen(m, T(it["pos"]))
            out.append(("seam", n, x, y, 70))
        c = mm.get("centre")
        if c is not None:
            cx, cy = to_screen(m, T(c))
            out.append(("apply", None, cx - 40, cy, 30))
            out.append(("cancel", None, cx + 40, cy, 32))
        return out

    def _match_under(self, m, sx, sy):
        """Nearest Match bubble / Apply / Cancel to the click (a bit generous:
        the bubbles sit on top of seam labels and are easy to miss)."""
        best = None
        for (kind, n, x, y, w) in self._match_rects(m):
            if abs(sx - x) < w + 6 and abs(sy - y) < 16:
                d = abs(sy - y) * 3.0 + abs(sx - x)
                if best is None or d < best[0]: best = (d, (kind, n))
        return None if best is None else best[1]

    def match_click(self, m, sx, sy):
        hit = self._match_under(m, sx, sy)
        mm = self.match_mode
        if hit is None:
            print("  [MatchUVN] click a seam bubble, Apply or Cancel (right click / Esc = cancel; the patch "
                  "buttons are hidden while MatchUVN is open)"); return
        kind, n = hit
        if kind == "seam":
            it = mm["items"][n]; it["want"] = (it["want"] + 1) % 3
        elif kind == "cancel":
            self.match_mode = None; print("[MatchUVN] cancelled")
        else:
            self.match_mode = None
            try:
                self.apply_match(mm)
            except Exception:
                print("[MatchUVN] failed:"); print(traceback.format_exc())
        sc.doc.Views.Redraw()

    def apply_match(self, mm):
        tag = "[MatchUVN]"
        got = self.load_patches(mm["oid"], tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        want = dict((it["key"], it["want"]) for it in mm["items"] if it["key"][0] != "mirror")
        msides = dict((it["key"][1], it["want"]) for it in mm["items"] if it["key"][0] == "mirror")
        moved, new, txt = match_step(A, allp, want, mm["loose"], freeze, self.mirror_spec(ox), msides)
        if not moved:
            print(tag + " " + txt); return
        if not self.write_cvs(moved, new, sp, ox, want):
            return
        for (xk, k, yk, l), lv in want.items():           # remembered: every button keeps this level from now on
            set_side_level(xk, k, lv); set_side_level(yk, l, lv)
        print(tag + " " + txt + " (neighbours: only their continuity rows; this patch: only its rows at the mirror; "
              "UndoSrfEdit undoes it)")
        self.refresh()

    def draw_match(self, d, vp):
        mm = getattr(self, "match_mode", None)
        if not mm: return
        m = xf_tuple(vp); hot = mm.get("hot")
        for (kind, n, x, y, w) in self._match_rects(m):
            if kind == "seam":
                it = mm["items"][n]
                txt = u"{}{} \u25BC  (now {}{})".format("mirror " if it["key"][0] == "mirror" else "",
                                                          LEVEL_NAME[it["want"]], it["now"],
                                                          ", " + it["src"] if it.get("src") else "")
                bg = C_HOVER if hot == ("seam", n) else col(120, 220, 120)
            else:
                txt = "Apply" if kind == "apply" else "Cancel"
                bg = C_HOVER if hot == (kind, None) else col(255, 255, 255)
            try:
                d.DrawDot(float(x), float(y), txt, bg, col(0, 0, 0))
            except Exception:
                pass

    # ---- FreeEdgeEdt: redraw a free edge of a finished patch ------------------
    def enter_edge(self, oid):
        """1. click the free edge to change (the nearest one lights up);
        2a. start AT one of its corners, points on the scan, end AT the other
            corner: only the middle of the edge moves, the corners stay;
        2b. start BESIDE the patch: points on the scan along where the edge
            should be, right click / Enter: the WHOLE edge (corners too) goes
            onto that line; the patch follows near the edge, neighbours at
            its corners come along and keep their G0/G1/G2."""
        tag = "[FreeEdgeEdt]"
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        free = free_sides_of(A, allp, self.seam_tol(tol), self.mirror_spec(ox), hold_neighbours=False)
        nps_ = self._npatches()
        if nps_:
            free = [k for k in free if not self._side_on_npatch(A, k, ox, nps_, self.np_tol(tol))]
        if not free:
            self.flash(tag + " this patch has no free edge (every side has a neighbour, the mirror or an NPatch)", oid); return
        edges = {}
        for k in free:
            edges[k] = [P3(v_add(A.val(k, a / 24.0, "pt"), ox)) for a in range(25)]
        self.edge_mode = {"oid": oid, "free": free, "edges": edges, "side": None, "mode": None,
                          "start": None, "pts": [], "corners": {}}
        print(tag + " click the free edge you want to change (it lights up when the mouse is near). "
              "Right click = cancel.")

    def _edge_side_at(self, m, sx, sy, rad=60.0):
        """Nearest free edge on screen (generous - easy to hit)."""
        em = self.edge_mode; best = None
        for k, pts in em["edges"].items():
            scr = [to_screen(m, T(p)) for p in pts]
            for i in range(len(scr) - 1):
                (x0, y0), (x1, y1) = scr[i], scr[i + 1]
                dx, dy = x1 - x0, y1 - y0; l2 = dx * dx + dy * dy
                f = 0.0 if l2 < 1e-9 else max(0.0, min(1.0, ((sx - x0) * dx + (sy - y0) * dy) / l2))
                dd = math.hypot(x0 + f * dx - sx, y0 + f * dy - sy)
                if dd < rad and (best is None or dd < best[0]): best = (dd, k)
        return None if best is None else best[1]

    def _corner_at(self, m, sx, sy):
        em = self.edge_mode; best = None
        rad = 25.0                              # pixels
        for i, p in em["corners"].items():
            x, y = to_screen(m, T(p))
            dd = math.hypot(x - sx, y - sy)
            if dd < rad and (best is None or dd < best[0]): best = (dd, i)
        return None if best is None else best[1]

    def edge_click(self, pt, m, sx, sy):
        em = self.edge_mode
        if em["side"] is None:                       # 1. which edge
            k = self._edge_side_at(m, sx, sy)
            if k is None:
                print("  [FreeEdgeEdt] click closer to a free edge of the patch"); return
            em["side"] = k
            got = self.load_patches(em["oid"], "[FreeEdgeEdt]")
            if got is None: self.edge_mode = None; return
            A, sp, freeze, ox, tol = got
            r0 = side_row_indices(A.du, A.dv, k, 0)
            em["corners"] = {r0[0]: P3(v_add(A.P[r0[0]], ox)), r0[-1]: P3(v_add(A.P[r0[-1]], ox))}
            print("  [FreeEdgeEdt] edge {}: start AT a corner dot and end AT the other one = only the middle moves; "
                  "start BESIDE the patch = the whole edge goes onto your line (right click = done)".format(k + 1))
            return
        if em["mode"] is None:                       # 2. how: from a corner, or a free line
            c = self._corner_at(m, sx, sy)
            if c is not None:
                em["mode"] = "corner"; em["start"] = c
            else:
                if pt is None: return
                em["mode"] = "line"; em["pts"] = [pt]
            return
        if em["mode"] == "line":
            if pt is not None: em["pts"].append(pt)
            return
        c = self._corner_at(m, sx, sy)
        if c is None or c == em["start"]:
            if pt is not None: em["pts"].append(pt)
            return
        self._edge_apply_corner(c)

    def edge_finish(self):
        """Right click / Enter in FreeEdgeEdt: a free line is applied, else cancel."""
        em = self.edge_mode
        if em and em.get("mode") == "line" and len(em["pts"]) >= 2:
            self._edge_apply_line(); return True
        self.edge_mode = None
        print("[FreeEdgeEdt] cancelled - patch buttons back")
        return False

    def _edge_samples(self, crv, ox, t0=None, t1=None, n=41):
        dom = crv.Domain
        a0 = dom.T0 if t0 is None else t0; a1 = dom.T1 if t1 is None else t1
        out = []
        for a in range(n):
            q = crv.PointAt(a0 + (a1 - a0) * a / (n - 1.0))
            mp = self.mesh.ClosestPoint(q)
            out.append(v_sub(T(mp if mp.IsValid else q), ox))
        return out

    def _edge_commit(self, A, sp, allp, freeze, ox, tol, newP, what):
        loose = self.seam_tol(tol)
        old = dict((p.key, list(p.P)) for p in allp)
        seams0, mine0 = match_seams(A, allp, loose)
        want = {}
        for (key, sm, B, k, lv) in mine0:            # the G each seam must keep
            want[key] = max(lv, 0)
        shift = max(v_dist(a_, b_) for a_, b_ in zip(A.P, newP))
        A.P = newP
        # the seams must still be found after the corner moved away from them
        moved, new, txt = match_step(A, allp, want, max(loose, 1.2 * shift), freeze,
                                     self.mirror_spec(ox)) if want else ([], {}, "")
        for p in allp:
            p.P = old[p.key]                         # write_cvs compares with the old CVs
        new[A.key] = newP
        if A not in moved: moved = [A] + list(moved)
        if not self.write_cvs(moved, new, sp, ox):
            return
        nb = [p for p in moved if p is not A]
        print("[FreeEdgeEdt] {}{}".format(what, "" if not nb else
              "; {} neighbour(s) came along with their continuity rows (same G)".format(len(nb))))
        self.refresh()

    def _edge_apply_corner(self, c):
        em = self.edge_mode; side = em["side"]
        got = self.load_patches(em["oid"], "[FreeEdgeEdt]")
        self.edge_mode = None
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        path = [em["corners"][em["start"]]] + list(em["pts"]) + [em["corners"][c]]
        samp = self._edge_samples(smooth_curve(path), ox)
        samp[0] = A.P[em["start"]]; samp[-1] = A.P[c]
        free = free_sides_of(A, allp, self.seam_tol(tol), self.mirror_spec(ox), hold_neighbours=False)
        newP = refit_free_edge(A, side, samp, free.get(side, (0, 0)))
        self._edge_commit(A, sp, allp, freeze, ox, tol, newP,
                          "edge {} redrawn between its corners (corners stayed)".format(side + 1))

    def _edge_apply_line(self):
        em = self.edge_mode; side = em["side"]
        got = self.load_patches(em["oid"], "[FreeEdgeEdt]")
        self.edge_mode = None
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        crv = smooth_curve(list(em["pts"]))
        try:                                         # a little longer, so both corners find their place on it
            ext = crv.Extend(rg.CurveEnd.Both, 0.5 * crv.GetLength(), rg.CurveExtensionStyle.Line)
            if ext is not None: crv = ext
        except Exception:
            pass
        r0 = side_row_indices(A.du, A.dv, side, 0)
        c0 = P3(v_add(A.P[r0[0]], ox)); c1 = P3(v_add(A.P[r0[-1]], ox))
        ok0, t0 = crv.ClosestPoint(c0); ok1, t1 = crv.ClosestPoint(c1)
        if not (ok0 and ok1) or abs(t1 - t0) < 1e-9 * max(1.0, crv.Domain.Length):
            print("[FreeEdgeEdt] the line does not run along this edge - nothing changed"); return
        samp = self._edge_samples(crv, ox, t0, t1)
        newP = move_free_edge(A, side, samp, self.mirror_spec(ox))
        self._edge_commit(A, sp, allp, freeze, ox, tol, newP,
                          "edge {} moved onto your line (corners too); the patch followed near the edge".format(side + 1))

    def draw_edge_mode(self, d, vp):
        em = getattr(self, "edge_mode", None)
        if not em: return
        hot = em["side"]
        if hot is None and self.last_mouse is not None:
            try:
                vp_, sx, sy = self.last_mouse
                hot = self._edge_side_at(xf_tuple(vp_), sx, sy)
            except Exception:
                hot = None
        for k, pts in em["edges"].items():
            if k == hot:
                d.DrawPolyline(pts, C_HOVER, 5)
            elif em["side"] is None:
                d.DrawPolyline(pts, col(255, 255, 255), 2)
        for i, p in em["corners"].items():
            d.DrawDot(p, "start" if i == em["start"] else "corner", C_HOVER if i == em["start"] else col(255, 255, 255),
                      col(0, 0, 0))
        path = list(em["pts"])
        if em["mode"] == "corner" and em["start"] is not None:
            path = [em["corners"][em["start"]]] + path
        cur = getattr(self, "cur_pt", None)
        if em["mode"] is not None and cur is not None: path = path + [cur]
        if len(path) > 1:
            draw_smooth(d, path, col(40, 170, 70), 2)
        for p in em["pts"]:
            d.DrawPoint(p, col(0, 0, 0))

    def layout_patch(self, oid, mode):
        """Even / Straight / ToDev: CVs move along the surface, the shape stays
        (<= 0.3 ScanTol). Rows crossing a seam take the neighbour's matching
        rows along; seams are made exact again (same G as before)."""
        tag = {"even": "[EvenUV]", "straight": "[StrUV]", "relax": "[RelaxUV]", "todev": "[ToDevUV]"}[mode]
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        devs = None
        if mode == "todev":
            saved_refs = self.refs; self.refs = []
            try:
                data = self.scan_samples(A, A.P, ox, 21)
            finally:
                self.refs = saved_refs
            if len(data) < A.n():
                print(tag + " too little scan under this patch. Nothing changed."); return
            devs = [(u, v, v_dist(srf_point(A.du, A.dv, A.P, u, v), q)) for (u, v, q) in data]
        old = dict((p.key, list(p.P)) for p in allp)
        new, txt, moved = layout_step(A, allp, mode, self.seam_tol(tol), freeze, devs, 0.3 * self.s_tol(),
                                      self.mirror_spec(ox))
        for p in allp:
            p.P = old[p.key]                     # write_cvs compares with the old CVs
        if new is None:
            self.flash(tag + " " + txt, oid, 10.0); return
        if not self.write_cvs(moved, new, sp, ox, quiet=True):
            # broken seams (a badly built fillet) stay as they are - only the rest
            new, txt, moved = layout_step(A, allp, mode, self.seam_tol(tol), freeze, devs, 0.3 * self.s_tol(),
                                          self.mirror_spec(ox), True)
            for p in allp:
                p.P = old[p.key]
            if new is None:
                self.flash(tag + " " + txt, oid, 10.0); return
            if not self.write_cvs(moved, new, sp, ox):
                return
            txt += "; broken seam(s) left as they are - MatchUVN on the narrow patch rebuilds it"
        print(tag + " " + txt)
        if "NOT moved" in txt:                   # half done: say on screen why, not only in the console
            self.flash(tag + " only partly: " + "; ".join(x for x in txt.split("; ") if "NOT moved" in x), oid, 10.0)
        self.refresh()

    def write_cvs(self, moving, cvs, sp, ox, allow=None, quiet=False, skip_seams=False, whole=False):
        """Put new CVs into the document, one undo record for the whole step.
        CV order rule first (cv_order_ok): the step is shortened until no CV
        jumps over / squeezes against the CV next to it in UV; if even 1/8 of
        it breaks the rule nothing is written. -> share of the step written
        (0 = nothing)."""
        alpha = 1.0
        why = ""
        allp_ = [v[0] for v in sp.values()]
        ftol = self.seam_tol(self.s_tol())
        gtol = max(0.05 * self.s_tol(), 1e-6)
        try:
            st0 = None if skip_seams else seam_states(allp_, ftol, gtol)
        except Exception:
            st0 = None
        for _ in range(4):
            bad = ""
            if st0 is not None:                # rule 2: no seam gets worse (also between neighbours)
                keep_ = dict((p.key, p.P) for p in moving)
                try:
                    for p in moving:
                        p.P = [v_lerp(a, b, alpha) for a, b in zip(keep_[p.key], cvs[p.key])]
                    ok_, t_ = seams_not_worse(st0, seam_states(allp_, ftol, gtol), allow, gtol)
                finally:
                    for p in moving:
                        p.P = keep_[p.key]
                if not ok_:
                    bad = t_
            for p in moving:
                if bad:
                    break
                tr = [v_lerp(a, b, alpha) for a, b in zip(p.P, cvs[p.key])]
                ok, t_ = cv_order_ok(p.P, tr, p.du.n, p.dv.n)
                if not ok:
                    nm = ""
                    try: nm = sc.doc.Objects.FindId(p.key).Attributes.Name or ""
                    except Exception: pass
                    bad = "{}: {}".format(nm or ("this patch" if p is moving[0] else "a neighbour"), t_)
                    break
            if not bad:
                break
            why = bad
            alpha *= 0.5
        else:
            if quiet:
                return 0.0
            txt_ = ("nothing changed - " + why + ". A narrow patch (fillet) tied G2 on all its rows cannot be "
                    "cleaned without spoiling its neighbours: MatchUVN on it (it is rebuilt from them), at G1 "
                    "or G0 on its short end")
            try: self.flash(txt_, moving[0].key if moving else None, 10.0)
            except Exception: print("  " + txt_)
            return 0.0
        if alpha < 1.0 and whole:              # all or nothing (a fillet is built for the whole step)
            self.flash("Not applied: " + why, None, 8.0)
            return 0.0
        if alpha < 1.0:
            cvs = dict((p.key, [v_lerp(a, b, alpha) for a, b in zip(p.P, cvs[p.key])]) for p in moving)
            print("  step shortened to {:.0%} ({})".format(alpha, why))
        undo = []
        for p in moving:
            if max(v_dist(a, b) for a, b in zip(p.P, cvs[p.key])) < 1e-9: continue
            obj = sc.doc.Objects.FindId(p.key)
            try:
                if obj.GripsOn: obj.GripsOn = False     # Rhino control points on: grips would be stale
            except Exception:
                pass
            undo.append((p.key, obj.Geometry.Duplicate()))
            ns = sp[p.key][1]
            nu, nv = ns.Points.CountU, ns.Points.CountV
            for i in range(nu):
                for j in range(nv):
                    q = v_add(cvs[p.key][i * nv + j], ox)
                    ns.Points.SetControlPoint(i, j, rg.ControlPoint(P3(q)))
            if isinstance(obj.Geometry, rg.Brep):
                sc.doc.Objects.Replace(p.key, ns.ToBrep())
            else:
                sc.doc.Objects.Replace(p.key, ns)
        if undo:
            self.smooth_undo.append(undo)
        sc.doc.Views.Redraw()
        return alpha

    def side_sign(self, p, P, ox):
        """Orientation of patch p's normal towards the scan side we work on:
        stored at build; otherwise majority of the nearest faces."""
        try:
            s = rs.GetUserText(p.key, "msh2classA_side")
            if s in ("1", "-1"): return float(s)
        except Exception:
            pass
        votes = 0.0
        for (u, v) in ((0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
            D = srf_derivs(p.du, p.dv, P, u, v, 1)
            n0 = v_cross(D[(1, 0)], D[(0, 1)])
            mp = self.mesh.ClosestMeshPoint(P3(v_add(D[(0, 0)], ox)), 0.0)
            if mp is None: continue
            fn = self.mesh.FaceNormals[mp.FaceIndex]
            votes += 1.0 if fn.X * n0[0] + fn.Y * n0[1] + fn.Z * n0[2] >= 0 else -1.0
        return 1.0 if votes >= 0 else -1.0

    def reset_patch(self, oid):
        """RstN: the patch as smooth as its edges allow - CVs only along the
        normal, the scan and the TempPoints NOT used; the rows that hold its
        seams (G0 / G1 / G2) stay, the neighbours keep their seams. Then
        PullN brings it to the scan / its TempPoints."""
        tag = "[RstN]"
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp_ = [v[0] for v in sp.values()]
        orig = dict((p.key, list(p.P)) for p in allp_)
        moved = {}
        target = fair_normal_target(1.0)
        try:
            for it in range(40):
                moving, used, new = adjust_step(A, allp_, target, self.seam_tol(tol), freeze, None,
                                                self.mirror_spec(ox), None, None, None, None)
                if not A.move:
                    break
                step = 0.0
                for p in moving:
                    step = max(step, max(v_dist(a, b) for a, b in zip(p.P, new[p.key])))
                    p.P = list(new[p.key]); moved[p.key] = p
                if step < 1e-6 * self.size:
                    break
            final = dict((k, list(p.P)) for k, p in moved.items())
        finally:
            for p in allp_:
                p.P = orig[p.key]
        if not moved:
            self.flash("RstN: every CV holds a seam - nothing to reset", oid, 5.0); return
        if not self.write_cvs(list(moved.values()), final, sp, ox):
            return
        self.flash("RstN: smoothest the edges allow - now PullN", oid, 5.0)
        self.refresh()

    def patch_points(self, oid):
        """The TempPoints a patch was built through ([(x, y, z)] world), or None."""
        cache = self.__dict__.setdefault("pts_cache", {})
        if oid not in cache:
            try:
                txt = rs.GetUserText(oid, PTS_KEY)
                cache[oid] = [tuple(q) for q in json.loads(txt)] if txt else None
            except Exception:
                cache[oid] = None
        return cache[oid]

    def scan_samples(self, p, P, ox, n=None):
        """[(u, v, scan point - ox)] under patch p, visible side only. A patch
        built through TempPoints: its points instead of the scan."""
        pts = self.patch_points(p.key)
        if pts:
            out = []
            for q in pts:
                ql = v_sub(q, ox)
                u, v = project_param(p.du, p.dv, P, ql)
                if -1e-3 <= u <= 1.0 + 1e-3 and -1e-3 <= v <= 1.0 + 1e-3:
                    out.append((min(1.0, max(0.0, u)), min(1.0, max(0.0, v)), ql))
            return out
        sgn = self.side_sign(p, P, ox)
        n = n or max(9, 2 * max(p.du.n, p.dv.n) + 3)
        out = []
        for i in range(n):
            for j in range(n):
                u, v = i / (n - 1.0), j / (n - 1.0)
                D = srf_derivs(p.du, p.dv, P, u, v, 1)
                q = self.scan_pt(P3(v_add(D[(0, 0)], ox)), v_mul(v_cross(D[(1, 0)], D[(0, 1)]), sgn), fit=True)
                if q is not None:
                    out.append((u, v, v_sub(T(q), ox)))
        return out

    def patch_dev(self, p, P, ox):
        """Max distance of patch p (CVs P, centred by ox) to the scan."""
        dmax = 0.0
        for (u, v, q) in self.scan_samples(p, P, ox, 9):
            dmax = max(dmax, v_dist(srf_point(p.du, p.dv, P, u, v), q))
        return dmax

    def delete_patch(self, oid):
        """Del: the patch is deleted (UndoSrfEdit brings it back). Its
        neighbours are not changed."""
        obj = sc.doc.Objects.FindId(oid)
        if obj is None: return
        geo = obj.Geometry.Duplicate(); at = obj.Attributes.Duplicate()
        sc.doc.Objects.Delete(oid, True)
        self.smooth_undo.append([("deleted", geo, at)])
        for ids in self.patches:
            if oid in ids: ids.remove(oid)
        self.patches = [ids for ids in self.patches if ids]
        self.hover_srf = None
        print("[Del] patch deleted (UndoSrfEdit brings it back)")
        self.refresh(); sc.doc.Views.Redraw()

    def undo_smooth(self):
        # any half-done mode ends first: its edges / preview go, the patch buttons come back
        was = [nm for nm in ("fillet_mode", "edge_mode", "match_mode") if getattr(self, nm, None)]
        self.fillet_mode = None; self.edge_mode = None; self.match_mode = None
        self.fillet_preview = None; self.hover_srf = None
        if not self.smooth_undo:
            print("[UNDO] nothing to undo"); sc.doc.Views.Redraw(); return
        for entry in reversed(self.smooth_undo.pop()):
            if len(entry) == 3 and entry[0] == "deleted":
                nid = sc.doc.Objects.Add(entry[1], entry[2])
                if nid and str(nid) != "00000000-0000-0000-0000-000000000000": self.patches.append([nid])
                continue
            if len(entry) == 2 and entry[0] == "added":
                sc.doc.Objects.Delete(entry[1], True)
                for ids in self.patches:
                    if entry[1] in ids: ids.remove(entry[1])
                self.patches = [ids for ids in self.patches if ids]
                continue
            oid, geo = entry
            sc.doc.Objects.Replace(oid, geo)
        sc.doc.Views.Redraw(); self.refresh()
        print("[UNDO] last patch-button step undone{}".format(
            " (Fillet / Match / FreeEdgeEdt mode ended - patch buttons back)" if was else ""))

    # ---- Fillet -----------------------------------------------------------
    def _all_spatches(self):
        """Every changeable single-span patch of the document (no fillets) as
        SPatch, centred by one ox. -> (list, ox, tol)."""
        tol = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 2e-6)
        items = [it for it in self.surfs if it[3]]
        if not items: return [], (0.0, 0.0, 0.0), tol
        lo = [min(it[2][0][c] for it in items) for c in range(3)]
        hi = [max(it[2][1][c] for it in items) for c in range(3)]
        ox = tuple(0.5 * (lo[c] + hi[c]) for c in range(3))
        out = []
        for (sid, brep, bb, ok) in items:
            try:
                isf = bool(rs.GetUserText(sid, "msh2classA_fillet"))
            except Exception:
                isf = False
            ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
            P = [v_sub(T(ns.Points.GetControlPoint(i, j).Location), ox)
                 for i in range(ns.Points.CountU) for j in range(ns.Points.CountV)]
            sp_ = SPatch(sid, ns.Degree(0), ns.Degree(1), P)
            sp_.is_fillet = isf
            out.append(sp_)
        return out, ox, tol

    # ---- Fillet / NPatch: you click patch corners -------------------------
    # One scheme, as for drawing a patch: left click = corner, right click =
    # go on, UndoPt = last corner back, Esc = cancel.
    def _corner_mode(self, kind, oid=None):
        allp, ox, tol = self._all_spatches()
        st = self.seam_tol(tol)
        ct = max(5.0 * tol, 1e-3)          # corners of patches meeting are snapped: tight (a gap is bigger)
        self.hover_srf = None
        self.fillet_mode = {"kind": kind, "oid": oid, "allp": allp, "ox": ox, "tol": tol, "st": st, "ct": ct,
                            "corners": patch_corners(allp, ct), "pts": [], "segs": [],
                            "side": "A", "ptsA": [], "segsA": [], "clicks": [], "clicksA": [], "graph": None,
                            "hot": None, "fillets": [p for p in allp if getattr(p, "is_fillet", False)]}
        self.fillet_preview = None
        self.flash("Fillet: first and last corner of side A" if kind == "fillet" else
                   "NPatch: click the corners round the hole", None, 6.0)

    def enter_fillet(self, oid=None):
        self._corner_mode("fillet", oid)

    def enter_npatch(self, oid=None):
        self._corner_mode("npatch", oid)

    def _fm_corner_at(self, m, sx, sy, rad=22.0):
        fm = self.fillet_mode
        ox = fm["ox"]; best = None
        for i, c in enumerate(fm["corners"]):
            x, y = to_screen(m, v_add(c, ox))
            dd = math.hypot(x - sx, y - sy)
            if dd < rad and (best is None or dd < best[0]): best = (dd, i)
        return None if best is None else best[1]

    def _fm_edge(self, i, j):
        fm = self.fillet_mode
        return edge_between(fm["allp"], fm["corners"][i], fm["corners"][j], fm["ct"])

    def fillet_click(self, m, sx, sy):
        """Left click in Fillet / NPatch: a corner."""
        fm = self.fillet_mode
        i = self._fm_corner_at(m, sx, sy)
        if i is None:
            self.flash("Click a corner", None, 3.0); return
        pts = fm["pts"]
        if i in pts:
            self.flash("Corner already used", None, 3.0); return
        if not pts:
            pts.append(i); fm["segs"].append(None); fm["clicks"].append(1); return
        seg, why = self._fm_edge(pts[-1], i)
        if seg is not None and fm["kind"] == "fillet" and not (min(seg[2], seg[3]) < 1e-3 and max(seg[2], seg[3]) > 1.0 - 1e-3):
            seg = None
        if seg is not None:
            run = [(i, seg)]
        else:                                  # several edges: along the free edges between the corners
            if fm["graph"] is None:
                busy = show_busy("Edges...")
                try:
                    fm["graph"] = free_edge_graph(fm["allp"], fm["corners"], fm["ct"])
                finally:
                    hide_busy(busy)
            run = edge_path(fm["graph"], pts[-1], i)
            if not run:
                self.flash("No free edges to this corner", None, 4.0); return
        if fm["kind"] == "fillet" and fm["side"] == "B" and any(s_[0] is t_[0] for (c_, s_) in run
                                                               for t_ in fm["segsA"][1:]):
            self.flash("This edge is on side A", None, 4.0); return
        if any(c_ in pts for (c_, s_) in run):
            self.flash("Corner already used", None, 3.0); return
        for (c_, s_) in run:
            pts.append(c_); fm["segs"].append(s_)
        fm["clicks"].append(len(run))

    def fillet_undo_pt(self):
        """The last CLICK back (with the edges it took)."""
        fm = self.fillet_mode
        if fm["pts"]:
            n = fm["clicks"].pop() if fm["clicks"] else 1
            del fm["pts"][-n:]; del fm["segs"][-n:]
        elif fm["kind"] == "fillet" and fm["side"] == "B":          # back to side A
            fm["pts"], fm["segs"], fm["clicks"] = fm["ptsA"], fm["segsA"], fm["clicksA"]
            fm["ptsA"], fm["segsA"], fm["clicksA"] = [], [], []; fm["side"] = "A"

    def corner_mode_enter(self):
        """Right click in Fillet / NPatch. -> True = stay in the mode."""
        fm = self.fillet_mode
        pts = fm["pts"]
        if not pts and not (fm["kind"] == "fillet" and fm["side"] == "B"):
            return False
        if fm["kind"] == "npatch":
            if len(pts) < 3:
                self.flash("NPatch: at least 3 corners", None, 4.0); return True
            close, why = self._fm_edge(pts[-1], pts[0])
            if close is None:
                self.flash("NPatch: no free edge back to corner 1", None, 4.0); return True
            L = []
            for seg in fm["segs"][1:] + [close]:
                p, k, ta, tb = seg
                L.append((p, k, min(ta, tb), max(ta, tb), ta < tb))
            try:
                self.npatch_build(L, fm["ox"], fm["tol"])
            except Exception:
                print("[NPatch] failed:"); print(traceback.format_exc())
            self.fillet_mode = None
            return True
        # fillet
        if len(pts) < 2:
            self.flash("Fillet: at least 2 corners on side {}".format(fm["side"]), None, 4.0); return True
        if fm["side"] == "A":
            fm["ptsA"], fm["segsA"], fm["clicksA"] = pts, fm["segs"], fm["clicks"]
            fm["pts"], fm["segs"], fm["clicks"] = [], [], []; fm["side"] = "B"
            self.flash("Fillet: first and last corner of side B", None, 5.0)
            return True
        try:
            done = self.gap_fillet_step()
        except Exception:
            print("[Fillet] failed:"); print(traceback.format_exc()); done = False
        if done:
            self.fillet_mode = None
        return True

    def _fillet_next_to(self, chain):
        """A fillet already made that this gap runs into (at a station):
        -> (R, level, shape) of it, or None. The new pieces take its radius
        and section so the two meet cleanly."""
        fm = self.fillet_mode
        ends = []
        for q in (chain[0], chain[-1]):
            ends.extend(_edge_ends(q[0], q[1])); ends.extend(_edge_ends(q[2], q[3]))
        tol_ = fm.get("ct", 1e-3)
        for F in fm.get("fillets", []):
            nu, nv = F.du.n, F.dv.n
            cor = [F.P[0], F.P[nv - 1], F.P[(nu - 1) * nv], F.P[nu * nv - 1]]
            if sum(1 for c in cor if any(v_dist(c, e) < tol_ for e in ends)) < 2:
                continue
            try:
                txt = rs.GetUserText(F.key, "msh2classA_fillet") or ""
                a = txt.split(";")
                R0 = float(a[0]); R1 = float(a[1]) if a[1] else None
                lv = int(a[2]); sh = float(a[3])
                if len(a) < 5 or a[4] != "fixed":
                    sh = None              # it was Shape 0 = auto: the new pieces are auto too
            except Exception:
                continue
            if R1 is not None:          # its radius at the end that touches this gap
                c0 = [F.P[0], F.P[nv - 1]]
                at0 = sum(1 for c in c0 if any(v_dist(c, e) < tol_ for e in ends)) >= 2
                R0 = R0 if at0 else R1
            return R0, lv, sh
        return None

    # ---- Fillet: side A corners, right click, side B corners, right click --
    def _fillet_chains(self, sp):
        """The clicked corners -> (A chain, B chain) of patches of sp:
        [(patch, side, forward)], B running the same way as A."""
        fm = self.fillet_mode
        def chain(segs):
            out = []
            for (p, k, ta, tb) in segs[1:]:
                out.append((sp[p.key][0], k, ta < tb))
            return out
        A_ = chain(fm["segsA"]); B_ = chain(fm["segs"])
        C = fm["corners"]
        a0, a1 = C[fm["ptsA"][0]], C[fm["ptsA"][-1]]
        b0, b1 = C[fm["pts"][0]], C[fm["pts"][-1]]
        if v_dist(a0, b0) + v_dist(a1, b1) > v_dist(a0, b1) + v_dist(a1, b0):
            B_ = [(p, k, not f) for (p, k, f) in reversed(B_)]
        return A_, B_

    def gap_fillet_step(self):
        """Right click after side B: radius step with preview, right click =
        apply, Esc = back to the corners. -> True when applied."""
        fm = self.fillet_mode
        keys = [s_[0].key for s_ in fm["segsA"][1:] + fm["segs"][1:]]
        got = self.load_patches(keys[0], "[Fillet]", extra=keys[1:])
        if got is None:
            return False
        A0, sp, freeze, ox, tol = got
        if any(k_ not in sp for k_ in keys):
            self.flash("Fillet: a patch cannot be changed", None, 5.0); return False
        Ach, Bch = self._fillet_chains(sp)
        # radius: the gap measured along every A edge against the B edge across
        Rs = []
        for (X, kX, f) in Ach:
            q = side_eval(X, kX, 0.5, 0.0, 1)["p"]
            Y, kY, fy = min(Bch, key=lambda b: v_dist(side_eval(b[0], b[1], _closest_t_edge(b[0], b[1], q), 0.0, 1)["p"], q))
            try:
                r_ = radius_from_gap(X, kX, Y, kY)[0]
                if r_: Rs.append(r_)
            except Exception:
                pass
        if not Rs:
            self.flash("Fillet: the sides do not face each other", None, 5.0); return False
        Rm = sum(Rs) / len(Rs)
        ends = [(Ach[0][0], Ach[0][1], Bch[0][0], Bch[0][1], 0), (Ach[-1][0], Ach[-1][1], Bch[-1][0], Bch[-1][1], 0)]
        nextto = self._fillet_next_to(ends)
        last = sc.sticky.get("M2CA_fillet") or {}
        level = last.get("level", 2); shape = None; R1 = None
        if nextto is not None:
            R0, level, shape = nextto
        elif last and abs(last["R0"] - Rm) <= 0.25 * Rm:
            R0 = last["R0"]
        else:
            R0 = round(Rm, 1)
        print("[Fillet] gap {}, R {}".format(fl(Rm), fl(R0)))
        cache = {}

        def _pv(sender, e):
            for ns_ in (getattr(self, "fillet_preview", None) or []):
                try: e.Display.DrawSurface(ns_, col(230, 120, 0), 2)
                except Exception: pass
        Rhino.Display.DisplayPipeline.PostDrawObjects += _pv
        try:
            while True:
                key = (R0, R1, level, shape)
                if key not in cache:
                    busy = show_busy("Rolling the ball...")
                    try:
                        res, why = gap_fillet(Ach, Bch, R0, R1, level, shape)
                    except Exception:
                        res, why = None, "failed"; print(traceback.format_exc())
                    finally:
                        hide_busy(busy)
                    if res is not None:
                        bad = [n_ + 1 for n_, pc in enumerate(res["pieces"])
                               if cv_order_worst(pc["F"], pc["deg"][0] + 1, 6)[0] < 0.0]
                        if bad:
                            why = "CV net crosses in piece {} - Shape 0 or another radius".format(bad[0]); res = None
                    pv = []
                    if res is not None:
                        for pc in res["pieces"]:
                            ns_ = make_nurbs(BDir(pc["deg"][0], 1), BDir(5, 1), pc["F"], ox)
                            if ns_ is not None: pv.append(ns_)
                    cache[key] = (res, why, pv)
                res, why, pv = cache[key]
                self.fillet_preview = pv; sc.doc.Views.Redraw()
                if res is None:
                    print("[Fillet] " + why)
                go = Rhino.Input.Custom.GetOption()
                go.SetCommandPrompt("Fillet R {}{} {}{} <right click = apply, Esc = back>".format(
                    fl(R0), "" if R1 is None else "-" + fl(R1), LEVEL_NAME.get(level, "G2"),
                    "" if res is None else ", {} pieces".format(len(res["pieces"]))))
                oR = Rhino.Input.Custom.OptionDouble(R0, 1e-6, 1e9)
                oR1 = Rhino.Input.Custom.OptionDouble(R1 if R1 is not None else R0, 1e-6, 1e9)
                oC = Rhino.Input.Custom.OptionToggle(level >= 2, "G1", "G2")
                oS = Rhino.Input.Custom.OptionDouble(shape or 0.0, 0.0, 3.0)
                go.AddOptionDouble("Radius", oR)
                go.AddOptionDouble("RadiusEnd", oR1)
                go.AddOptionToggle("Cont", oC)
                go.AddOptionDouble("Shape", oS)
                go.AcceptNothing(True)
                r = go.Get()
                if r == Rhino.Input.GetResult.Option:
                    nm_ = go.Option().EnglishName
                    if nm_ == "Radius":
                        R0 = oR.CurrentValue
                        if R1 is not None and abs(R1 - R0) < 1e-9: R1 = None
                    elif nm_ == "RadiusEnd":
                        R1 = oR1.CurrentValue if abs(oR1.CurrentValue - R0) > 1e-9 else None
                    elif nm_ == "Cont":
                        level = 2 if oC.CurrentValue else 1
                    elif nm_ == "Shape":
                        shape = oS.CurrentValue if oS.CurrentValue > 0 else None
                    continue
                if r == Rhino.Input.GetResult.Nothing:
                    if res is None:
                        continue
                    self.fillet_preview = None
                    return self.apply_gap(Ach, Bch, res, sp, ox, tol, R0, R1, level, shape is None)
                return False
        finally:
            Rhino.Display.DisplayPipeline.PostDrawObjects -= _pv
            self.fillet_preview = None
            sc.doc.Views.Redraw()

    def apply_gap(self, Ach, Bch, res, sp, ox, tol, R0, R1, level, auto):
        """Write the moved big patches and add the fillet pieces; one
        UndoSrfEdit for all."""
        gt = max(0.05 * self.s_tol(), 1e-4)
        new = dict(res["A"]); new.update(res["B"])
        moving = [X for (X, k, f) in Ach] + [Y for (Y, k, f) in Bch]
        cvs = dict(new)
        # neighbours at the ends of the run: their corner follows
        allp = [v[0] for v in sp.values()]
        for (P_old, k_, f_) in Ach + Bch:
            P_new = SPatch(P_old.key, P_old.du.p, P_old.dv.p, new[P_old.key])
            seams_, mine_ = match_seams(P_old, allp, self.seam_tol(tol))
            for t_end, adj in ((0.0, (k_ + 3) % 4), (1.0, (k_ + 1) % 4)):
                c_old = side_eval(P_old, k_, t_end, 0.0, 1)["p"]
                c_new = side_eval(P_new, k_, t_end, 0.0, 1)["p"]
                if v_dist(c_old, c_new) < gt:
                    continue
                for (key_, sm_, N, kk, lv_) in mine_:
                    if kk != adj or N in moving:
                        continue
                    lN = sm_["l"] if sm_["X"] is P_old else sm_["k"]
                    try:
                        freeN = free_sides_of(N, allp, self.seam_tol(tol), self.mirror_spec(ox), hold_neighbours=False)
                    except Exception:
                        freeN = {}
                    for kN in ((lN + 1) % 4, (lN + 3) % 4):
                        if kN not in freeN:
                            continue
                        for te in (0.0, 1.0):
                            if v_dist(side_eval(N, kN, te, 0.0, 1)["p"], c_old) < 10.0 * gt + 1e-3:
                                PN = follow_corner(N, kN, te, c_new)
                                if PN is not None:
                                    moving.append(N); cvs[N.key] = PN
                                break
                        if N.key in cvs:
                            break
        n0 = len(self.smooth_undo)
        if not self.write_cvs(moving, cvs, sp, ox, skip_seams=True, whole=True):
            return False
        fids = []
        for pc in res["pieces"]:
            ia = pc["A"][0]
            X = Ach[ia][0]
            fid = self._add_fillet_srf(pc, X, Ach[ia][1], new[X.key], ox, R0, R1, level, res["shape"], auto)
            if fid is not None: fids.append(fid)
        for (P_, k_, f_) in Ach + Bch:
            try: set_side_level(P_.key, k_, level)
            except Exception: pass
        if len(self.smooth_undo) > n0:
            step = []
            for st_ in self.smooth_undo[n0:]: step.extend(st_)
            del self.smooth_undo[n0:]
        else:
            step = []
        step.extend(("added", f_) for f_ in fids)
        self.smooth_undo.append(step)
        sc.sticky["M2CA_fillet"] = {"R0": R0, "R1": R1, "level": level, "shape": res["shape"]}
        self.refresh()
        if fids:
            self.join_fillets(fids[0], level)
            if len(fids) > 1: self.join_fillets(fids[-1], level)
        self.flash("Fillet: {} piece(s), R {}".format(len(fids), fl(R0)), None, 6.0)
        sc.doc.Views.Redraw()
        return True

    def _add_fillet_srf(self, pc, X, kX, PX, ox, R0, R1, level, shape, auto):
        """One fillet piece into the document, facing the same way as X."""
        pal = pc["deg"][0]
        du_f = BDir(pal, 1); dv_f = BDir(5, 1)
        PF = list(pc["F"])
        try:
            sgn = -1.0 if rs.GetUserText(X.key, "msh2classA_side") == "-1" else 1.0
        except Exception:
            sgn = 1.0
        DF = srf_derivs(du_f, dv_f, PF, 0.5, 0.0, 1)
        nF = v_cross(DF[(1, 0)], DF[(0, 1)])
        ua, va = SIDE_MAP[kX][0](0.5)
        DA = srf_derivs(X.du, X.dv, PX, ua, va, 1)
        if v_dot(nF, v_mul(v_cross(DA[(1, 0)], DA[(0, 1)]), sgn)) < 0:
            nal = pal + 1
            PF = [PF[(nal - 1 - i) * 6 + j] for i in range(nal) for j in range(6)]
        ns = make_nurbs(du_f, dv_f, PF, ox)
        if ns is None or not ns.IsValid:
            print("[Fillet] [ERROR] invalid fillet surface"); return None
        try:
            attrs = sc.doc.Objects.FindId(X.key).Attributes.Duplicate(); attrs.Name = ""
            fid = sc.doc.Objects.AddSurface(ns, attrs)
        except Exception:
            fid = sc.doc.Objects.AddSurface(ns)
        try:
            rs.SetUserText(fid, "msh2classA_fit", "NoScan")
            rs.SetUserText(fid, "msh2classA_side", "1")
            rs.SetUserText(fid, "msh2classA_fillet", "{:.6g};{};{};{:.3f};{}".format(
                R0, "" if R1 is None else "{:.6g}".format(R1), level, shape, "auto" if auto else "fixed"))
        except Exception:
            pass
        for k_ in (0, 2):
            try: set_side_level(fid, k_, level)
            except Exception: pass
        self.patches.append([fid])
        return fid

    def join_fillets(self, fid, level):
        """A fillet that meets an earlier one at a station: both get G2 (or
        G1) there in one joint solve of their inner rows."""
        got = self.load_patches(fid, "[Fillet]")
        if got is None: return
        F, sp, freeze, ox, tol = got
        fl_ = []; bigs = []
        for k_, (p, ns_) in sp.items():
            try:
                isf = bool(rs.GetUserText(k_, "msh2classA_fillet"))
            except Exception:
                isf = False
            (fl_ if isf else bigs).append(p)
        others = [p for p in fl_ if p is not F]
        touching = [p for p in others if any(F in (sm["X"], sm["Y"]) and p in (sm["X"], sm["Y"])
                                            for sm in find_seams([F, p], self.seam_tol(tol)))]
        if not touching:
            return
        chain = [F] + touching
        gt = max(0.05 * self.s_tol(), 1e-4)
        def levels(patches):
            out = {}
            for sm in find_seams(patches, self.seam_tol(tol)):
                if sm["X"] in chain or sm["Y"] in chain:
                    out[(sm["X"].key, sm["Y"].key)] = (seam_measure(dict(sm), gt), sm)
            return out
        before = levels(chain + bigs)
        ff = [v for (k_, v) in before.items() if v[1]["X"] in chain and v[1]["Y"] in chain]
        if ff and all(v[0] >= level for v in ff):
            print("[Fillet] meets the fillet next to it {} already".format(LEVEL_NAME.get(level, "G2")))
            return
        new = smooth_fillet_chain(chain, bigs, level, self.seam_tol(tol))
        if not new: return
        trial = [SPatch(p.key, p.du.p, p.dv.p, new.get(p.key, p.P)) for p in chain]
        after = levels(trial + bigs)
        worse = [k_ for k_, v in before.items() if k_ in after and after[k_][0] < v[0]]
        if worse:
            print("[Fillet] the joint to the fillet next to it is left as it is (joining would make the seam "
                  "to a big patch worse)")
            return
        if self.write_cvs(chain, new, sp, ox, skip_seams=True, whole=True):
            if len(self.smooth_undo) >= 2:
                last = self.smooth_undo.pop()
                self.smooth_undo[-1].extend(last)                     # one undo step with the fillet
            print("[Fillet] joined {} to the fillet next to it ({})".format("G2" if level >= 2 else "G1",
                                                                       len(touching)))
            self.refresh()

    # ---- NPatch: an N-sided hole --------------------------------------------
    def _npatches(self):
        """[(id, brep)] of the NPatches in the document."""
        out = []
        for (sid, brep, bb, ok) in self.surfs:
            try:
                if rs.GetUserText(sid, NPATCH_KEY):
                    out.append((sid, brep))
            except Exception:
                pass
        return out

    def np_tol(self, tol):
        """A side counts as lying on an NPatch within this (Patch fits the
        edges only to a tolerance)."""
        return max(self.seam_tol(tol), 0.5 * self.s_tol())


    def _side_on_npatch(self, p, k, ox, nps, tol_):
        for (sid, brep) in nps:
            hits = 0
            for t in (0.25, 0.5, 0.75):
                q = P3(v_add(p.val(k, t, "pt"), ox))
                try:
                    if brep.ClosestPoint(q).DistanceTo(q) < tol_: hits += 1
                except Exception:
                    pass
            if hits >= 2:
                return True
        return False

    def npatch_build(self, L, ox, tol):
        tag = "[NPatch]"
        nS = len(L)
        level = {"G0": 0, "G1": 1}.get(self.s_cont(), 2)
        real, flat = loop_corners(L)
        if real == 4 and flat:
            print(tag + " 4 real corners: a normal patch would fit too (untrimmed)")
        whole = lambda e_: e_[2] < 1e-3 and e_[3] > 1.0 - 1e-3
        # ---- the hole's boundary: points on the neighbours' edges + their normals
        bp = []; bn = []; side_of = []
        NS_ = 16
        for j_, e_ in enumerate(L):
            p, k, t0, t1, f = e_
            for i in range(NS_):
                s_ = i / float(NS_)
                t = t0 + (t1 - t0) * (s_ if f else 1.0 - s_)
                E = side_eval(p, k, t, 0.0, 1)
                bp.append(E["p"]); bn.append(v_unit(v_cross(E["t"], E["e"]))); side_of.append(j_)
        poly = list(bp)
        cen = tuple(sum(q[c] for q in poly) / len(poly) for c in range(3))
        hole = max(v_dist(q, cen) for q in poly)
        inner = []
        if self.s_fit() != "NoScan":                    # the scan inside the hole
            c_, ex, ey, Nn = npatch_frame(poly)
            shrink = [v_add(cen, v_mul(v_sub(q, cen), 0.85)) for q in poly]
            for a in range(15):
                for b in range(15):
                    q = v_add(cen, v_add(v_mul(ex, hole * (a / 7.0 - 1.0)), v_mul(ey, hole * (b / 7.0 - 1.0))))
                    if not point_in_loop(shrink, q): continue
                    w = P3(v_add(q, ox))
                    mp = self.mesh.ClosestPoint(w)
                    if mp.IsValid and mp.DistanceTo(w) < 0.5 * hole:
                        inner.append(v_sub(T(mp), ox))
        nscan = len(inner)
        tol_d = sc.doc.ModelAbsoluteTolerance
        busy = show_busy("NPatch ({} sides)...".format(nS))
        try:
            # OUR fit: one clean 6x6 span lined up with the hole (not Rhino's Patch:
            # its big rebuilt surface has a noisy CV net the neighbours would copy)
            S_, fr = fit_npatch(bp, bn, inner)
        finally:
            hide_busy(busy)
        reb = make_nurbs(S_.du, S_.dv, S_.P, ox)
        if reb is None:
            self.flash("NPatch: surface failed", None, 6.0); return False
        # ---- trim: the boundary drawn in the surface's own (u, v) - exactly ON it
        c_, ex, ey, Nn, x0, x1, y0, y1 = fr
        def uv_of(q):
            d = v_sub(q, c_)
            return (min(1.0, max(0.0, (v_dot(d, ex) - x0) / (x1 - x0))), min(1.0, max(0.0, (v_dot(d, ey) - y0) / (y1 - y0))))
        loop2d = []
        for j_, e_ in enumerate(L):
            p, k, t0, t1, f = e_
            pts2 = []
            for i in range(NS_ + 1):
                s_ = i / float(NS_)
                t = t0 + (t1 - t0) * (s_ if f else 1.0 - s_)
                u_, v_ = uv_of(side_eval(p, k, t, 0.0, 1)["p"])
                pts2.append(rg.Point3d(u_, v_, 0.0))
            c2 = rg.Curve.CreateInterpolatedCurve(pts2, 3)
            if c2 is not None: loop2d.append(c2)
        nb = None; c3 = []
        try:
            c3 = [reb.Pushup(c2, tol_d) for c2 in loop2d]
            c3 = [c for c in c3 if c is not None]
            joined = rg.Curve.JoinCurves(c3, 10.0 * tol_d)
            base = reb.ToBrep()
            pieces = base.Faces[0].Split(joined, tol_d)
            if pieces is not None and pieces.Faces.Count > 1:
                for fi in range(pieces.Faces.Count):
                    pc = pieces.Faces[fi].DuplicateFace(False)
                    try:
                        cc = v_sub(T(rg.AreaMassProperties.Compute(pc).Centroid), ox)
                    except Exception:
                        continue
                    if point_in_loop(poly, cc):
                        nb = pc; break
        except Exception:
            print(traceback.format_exc())
        if nb is None:
            try:
                pieces = rg.Brep.CutUpSurface(reb, c3, True, tol_d)
            except Exception:
                pieces = None
            for pc in (pieces or []):
                try:
                    cc = v_sub(T(rg.AreaMassProperties.Compute(pc).Centroid), ox)
                except Exception:
                    continue
                if point_in_loop(poly, cc):
                    nb = pc; break
        if nb is None or not nb.IsValid:
            self.flash("NPatch: trim failed", None, 6.0); return False
        # how well it sits on the neighbours' edges (before they move)
        dev = 0.0; kink = 0.0
        for (p, k, t0, t1, f) in L:
            for s_ in (0.1, 0.3, 0.5, 0.7, 0.9):
                E = side_eval(p, k, t0 + (t1 - t0) * s_, 0.0, 1)
                q = P3(v_add(E["p"], ox))
                ok_, u_, v_ = reb.ClosestPoint(q)
                if not ok_: continue
                dev = max(dev, reb.PointAt(u_, v_).DistanceTo(q))
                n_ = reb.NormalAt(u_, v_); nN = v_unit(v_cross(E["t"], E["e"]))
                kink = max(kink, math.degrees(math.acos(min(1.0, abs(n_.X * nN[0] + n_.Y * nN[1] + n_.Z * nN[2])))))
        try:
            attrs = sc.doc.Objects.FindId(L[0][0].key).Attributes.Duplicate(); attrs.Name = ""
            nid = sc.doc.Objects.AddBrep(nb, attrs)
        except Exception:
            nid = sc.doc.Objects.AddBrep(nb)
        if nid is None or str(nid) == "00000000-0000-0000-0000-000000000000":
            self.flash("NPatch: could not add it", None, 6.0); return False
        try:
            rs.SetUserText(nid, NPATCH_KEY, "{};{:.4g}".format(nS, dev))
            rs.SetUserText(nid, "msh2classA_fit", "NoScan" if not nscan else "Mesh")
        except Exception:
            pass
        n0 = len(self.smooth_undo)
        self.smooth_undo.append([("added", nid)])
        self.patches.append([nid])
        if dev > 2.0 * self.s_tol():
            print(tag + " [WARN] {} off the edges".format(fl(dev)))
        self.refresh()
        # ---- the neighbours come to it (MatchUVN against a strip on its surface)
        res = []
        Lw = [(e_[0], e_[1]) for e_ in L if whole(e_)]
        if level >= 1 and Lw:
            res.append(self._match_to_npatch(Lw, reb, level, hole, tag))
        if len(self.smooth_undo) - n0 > 1:                          # one UndoSrfEdit for all
            merged = []
            for st_ in self.smooth_undo[n0:]: merged.extend(st_)
            del self.smooth_undo[n0:]
            self.smooth_undo.append(merged)
        for r_ in res:
            if r_.startswith("not matched"):
                print(tag + " " + r_)
        self.refresh(); sc.doc.Views.Redraw()
        try:                                   # the seams all round (also the labels on screen)
            obj_ = sc.doc.Objects.FindId(nid)
            got = analyze_npatch_seams(self.cands, [(nid, obj_.Geometry)], sc.doc.ModelAbsoluteTolerance,
                                       1.0 / (100.0 * self.size), 0.5 * self.s_tol())
            cnt = {}
            for (p_, t_, c_) in got:
                k_ = t_.split(" ")[0]; cnt[k_] = cnt.get(k_, 0) + 1
            msg = "NPatch {} sides: ".format(nS) + ", ".join("{} {}".format(k_, cnt[k_]) for k_ in sorted(cnt))
            if len(got) < nS:
                msg += ", {} not touching".format(nS - len(got))
            self.flash(msg, None, 8.0)
        except Exception:
            pass
        return True

    def _match_to_npatch(self, L, reb, level, hole, tag):
        """All neighbours around the hole -> brought to the NPatch surface reb
        at level, in ONE solve (a strip on the NPatch along every side is a
        master that never moves; seams between the neighbours keep their
        level). -> one line for the report."""
        keys = [p.key for (p, k) in L]
        got = self.load_patches(keys[0], tag, extra=keys[1:])
        if got is None:
            return "not matched: a neighbour cannot be changed (trimmed / multi-span)"
        A_, sp, freeze, oxl, tol_l = got
        if any(k_ not in sp for k_ in keys):
            return "not matched: a neighbour was not found"

        class _RhSurf(object):
            def __init__(s_, srf, ox_):
                s_.s = srf; s_.ox = ox_; s_.d0 = srf.Domain(0); s_.d1 = srf.Domain(1)
            def closest(s_, q):
                r_ = s_.s.ClosestPoint(P3(v_add(q, s_.ox)))
                return (r_[1], r_[2]) if r_[0] else None
            def derivs(s_, u_, v_):
                r_ = s_.s.Evaluate(u_, v_, 1)
                pt_, ders_ = r_[1], r_[2]
                n_ = s_.s.NormalAt(u_, v_)
                return v_sub(T(pt_), s_.ox), T(ders_[0]), T(ders_[1]), (n_.X, n_.Y, n_.Z)
            def inside(s_, u_, v_):
                e0 = 1e-9 * max(1.0, s_.d0.Length); e1 = 1e-9 * max(1.0, s_.d1.Length)
                return s_.d0.T0 - e0 <= u_ <= s_.d0.T1 + e0 and s_.d1.T0 - e1 <= v_ <= s_.d1.T1 + e1

        S = _RhSurf(reb, oxl)

        def kink_of(N_, k_, P_=None):
            X_ = SPatch(N_.key, N_.du.p, N_.dv.p, P_ if P_ is not None else N_.P)
            a = 0.0
            for t in (0.1, 0.3, 0.5, 0.7, 0.9):
                E = side_eval(X_, k_, t, 0.0, 1); uv = S.closest(E["p"])
                if uv is None: continue
                nS = S.derivs(*uv)[3]; nN = v_unit(v_cross(E["t"], E["e"]))
                a = max(a, math.degrees(math.acos(min(1.0, abs(v_dot(nS, nN))))))
            return a
        masters = []; sides = []
        for i, (p, k) in enumerate(L):
            Nn = sp[p.key][0]
            Ls = v_dist(Nn.val(k, 0.0, "pt"), Nn.val(k, 1.0, "pt"))
            px = None; why = ""
            for fW in (0.3, 0.15, 0.07):
                px, why = proxy_strip(Nn, k, S, min(fW * Ls, 0.4 * hole), key="_npstrip{}".format(i))
                if px is not None: break
            if px is None:
                return "not matched: side {}: {}".format(i + 1, why)
            masters.append((px, [Nn], level)); sides.append((Nn, k))
        before = [kink_of(N_, k_) for (N_, k_) in sides]
        fz = {}
        for key_, lst in freeze.items():               # the hole's sides are matched now (later buttons keep them)
            fz[key_] = [(kk, r) for (kk, r) in lst if (key_, kk) not in set((N_.key, k_) for (N_, k_) in sides)]
        loose = max(tol_l, 3.0 * self.s_tol())
        A0, o0, _ = masters[0]
        allp = [v[0] for v in sp.values()]
        w0 = dict((key_, level) for (key_, sm, B, kk, lv) in match_seams(A0, [A0] + allp, loose)[1] if B in o0)
        if not w0:
            return "not matched: the seam to the strip is not found"
        moved, new, txt = match_step(A0, allp, w0, loose, fz, self.mirror_spec(oxl), {}, no_narrow=True,
                                     only=o0, extra=masters[1:])
        real = [p for p in moved if p.key in sp]
        if not real:
            return "not matched: " + txt
        if not self.write_cvs(real, new, sp, oxl, whole=True):
            return "not matched (see above)"
        after = [kink_of(N_, k_, new.get(N_.key)) for (N_, k_) in sides]
        return "{} - kink before -> after: {}".format(txt, ", ".join("{:.2f} -> {:.3f} deg".format(a, b)
                                                                     for a, b in zip(before, after)))

    def start_surface_tool(self, nm):
        if nm == "Fillet":
            self.enter_fillet(None)
        elif nm == "NPatch":
            self.enter_npatch(None)
        elif nm == "GlobalMatch":
            self.global_match()
        elif nm == "ForceMirror":
            self.force_mirror()
        sc.doc.Views.Redraw()

    def force_mirror(self):
        """ForceMirror: every patch the mirror plane runs across is cut at it
        (the bigger part stays; the same surface, no trim); a patch trimmed
        at the mirror is rebuilt untrimmed from its surface. Then its side on
        the plane gets the mirror continuity of DefaultCont (G2: the 3rd row
        mirrors smoothly). TempPoints on the wrong side are dropped."""
        tag = "[ForceMirror]"
        ax = MIRROR_AXIS.get(self.s_mirror())
        if ax is None:
            self.flash("ForceMirror: set MirrorPlane first", None, 5.0); return
        level = {"G0": 0, "G1": 1}.get(self.s_cont(), 2)
        eps = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 1e-5)
        n0 = len(self.smooth_undo)
        cut = []; failed = 0
        busy = show_busy("ForceMirror...")
        try:
            # trimmed single-span patches at the mirror: rebuilt untrimmed from their surface
            for (sid, brep, bb, ok) in list(self.surfs):
                if ok or bb[0][ax] > eps or bb[1][ax] < -eps: continue
                try:
                    if rs.GetUserText(sid, NPATCH_KEY): continue
                    ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
                except Exception:
                    continue
                if ns.Points.CountU != ns.Degree(0) + 1 or ns.Points.CountV != ns.Degree(1) + 1:
                    continue                                    # not one span
                P = [T(ns.Points.GetControlPoint(i, j).Location)
                     for i in range(ns.Points.CountU) for j in range(ns.Points.CountV)]
                A = SPatch(sid, ns.Degree(0), ns.Degree(1), P)
                try:
                    keep = 1.0 if rg.AreaMassProperties.Compute(brep).Centroid[ax] >= 0 else -1.0
                except Exception:
                    keep = None
                r = cut_at_plane(A, ax, 0.0, keep=keep)
                if r[0] is None:
                    failed += 1; continue
                k, P2, dev, keep = r
                ns2 = make_nurbs(A.du, A.dv, P2, (0.0, 0.0, 0.0))
                if ns2 is None or not ns2.IsValid:
                    failed += 1; continue
                obj = sc.doc.Objects.FindId(sid)
                self.smooth_undo.append([(sid, obj.Geometry.Duplicate())])
                sc.doc.Objects.Replace(sid, ns2.ToBrep())
                cut.append((sid, k, keep))
            self.refresh()
            # untrimmed patches the plane runs across
            for (sid, brep, bb, ok) in list(self.surfs):
                if not ok or any(sid == c_[0] for c_ in cut): continue
                if bb[0][ax] > -eps or bb[1][ax] < eps: continue
                got = self.load_patches(sid, tag)
                if got is None: continue
                A, sp, freeze, ox, tol = got
                r = cut_at_plane(A, ax, -ox[ax])
                if r[0] is None:
                    failed += 1; continue
                k, P2, dev, keep = r
                if not self.write_cvs([A], {A.key: P2}, sp, ox, skip_seams=True, whole=True):
                    failed += 1; continue
                cut.append((sid, k, keep))
                self.refresh()
            # TempPoints only on the side that stays
            for (sid, k, keep) in cut:
                pts = self.patch_points(sid)
                if pts:
                    kept = [q for q in pts if keep * q[ax] >= 0.0]
                    try: rs.SetUserText(sid, PTS_KEY, json.dumps([[round(c, 6) for c in q] for q in kept]))
                    except Exception: pass
                    self.__dict__.setdefault("pts_cache", {}).pop(sid, None)
            # the side on the plane: mirror continuity
            for (sid, k, keep) in cut:
                got = self.load_patches(sid, tag)
                if got is None: continue
                A, sp, freeze, ox, tol = got
                loose = max(tol, 3.0 * self.s_tol())
                moved, new, txt = match_step(A, [v[0] for v in sp.values()], {}, loose, freeze,
                                             self.mirror_spec(ox), {k: level})
                if moved:
                    self.write_cvs(moved, new, sp, ox, whole=True)
                self.refresh()
        finally:
            hide_busy(busy)
        if len(self.smooth_undo) - n0 > 1:                          # one UndoSrfEdit for all
            merged = []
            for st_ in self.smooth_undo[n0:]: merged.extend(st_)
            del self.smooth_undo[n0:]
            self.smooth_undo.append(merged)
        sc.doc.Views.Redraw()
        if cut:
            self.flash("ForceMirror: {} patch(es) cut, {} on the mirror{}".format(
                len(cut), LEVEL_NAME[level], ", {} failed".format(failed) if failed else ""), None, 6.0)
        else:
            self.flash("ForceMirror: no patch crosses the mirror{}".format(
                " ({} could not be cut)".format(failed) if failed else ""), None, 5.0)

    def global_match(self):
        """GlobalMatch: every seam that is below the level stored for it (it
        was built / matched with that) - e.g. after hand edits of CVs - is
        brought back. The patch with the most broken seams stays (the edited
        one, usually) and its neighbours come to it, like MatchUVN; up to 3
        rounds. Seams with no stored level are left as they are."""
        tag = "[GlobalMatch]"
        n0 = len(self.smooth_undo); fixed = 0; refused = 0
        busy = show_busy("GlobalMatch...")
        try:
            for rnd in range(3):
                todo = []
                for (sid, brep, bb, ok) in self.surfs:
                    if not ok: continue
                    got = self.load_patches(sid, tag)
                    if got is None: continue
                    A, sp, freeze, ox, tol = got
                    loose = max(tol, 3.0 * self.s_tol())
                    seams, mine = match_seams(A, [v[0] for v in sp.values()], loose)
                    n = 0
                    for (key, sm, B, k, lv) in mine:
                        stored = max(A.want.get(k, -1) if hasattr(A, "want") else -1,
                                     B.want.get(sm["l"] if sm["X"] is A else sm["k"], -1) if hasattr(B, "want") else -1)
                        if stored >= 0 and seam_measure(dict(sm), tol) < stored:
                            n += 1
                    if n: todo.append((n, sid))
                if not todo: break
                todo.sort(key=lambda x: -x[0])
                done_round = 0
                for (n, sid) in todo:
                    got = self.load_patches(sid, tag)
                    if got is None: continue
                    A, sp, freeze, ox, tol = got
                    loose = max(tol, 3.0 * self.s_tol())
                    allp = [v[0] for v in sp.values()]
                    seams, mine = match_seams(A, allp, loose)
                    want = {}
                    for (key, sm, B, k, lv) in mine:
                        stored = max(A.want.get(k, -1) if hasattr(A, "want") else -1,
                                     B.want.get(sm["l"] if sm["X"] is A else sm["k"], -1) if hasattr(B, "want") else -1)
                        if stored >= 0 and seam_measure(dict(sm), tol) < stored:
                            want[key] = stored
                    if not want: continue
                    moved, new, txt = match_step(A, allp, want, loose, freeze, self.mirror_spec(ox), {})
                    if moved and self.write_cvs(moved, new, sp, ox, want, quiet=True):
                        fixed += len(want); done_round += 1
                    else:
                        refused += 1
                    self.refresh()
                if not done_round: break
        finally:
            hide_busy(busy)
        if len(self.smooth_undo) - n0 > 1:                          # one UndoSrfEdit for all
            merged = []
            for st_ in self.smooth_undo[n0:]: merged.extend(st_)
            del self.smooth_undo[n0:]
            self.smooth_undo.append(merged)
        self.refresh(); sc.doc.Views.Redraw()
        if fixed or refused:
            self.flash("GlobalMatch: {} seam(s) fixed{}".format(fixed, ", {} refused".format(refused) if refused else ""),
                       None, 8.0)
        else:
            self.flash("GlobalMatch: all seams OK", None, 5.0)

    def draw_fillet_mode(self, d, vp):
        fm = getattr(self, "fillet_mode", None)
        if not fm: return
        ox = fm["ox"]; C = fm["corners"]
        W = lambda i: P3(v_add(C[i], ox))
        def seg_line(seg, colour, w):
            if seg is None: return
            p, k, ta, tb = seg
            d.DrawPolyline([P3(v_add(side_eval(p, k, ta + (tb - ta) * i / 20.0, 0.0, 1)["p"], ox)) for i in range(21)],
                           colour, w)
        try:
            for c in C:
                d.DrawPoint(P3(v_add(c, ox)), Rhino.Display.PointStyle.RoundSimple, 5, col(150, 150, 150))
            if fm["kind"] == "npatch":
                runs = [("", fm["pts"], fm["segs"], col(255, 140, 0))]
            else:
                runs = [("A", fm["ptsA"], fm["segsA"], col(230, 60, 40))] if fm["side"] == "B" else []
                runs.append((fm["side"], fm["pts"], fm["segs"], col(230, 60, 40) if fm["side"] == "A" else col(40, 90, 230)))
            for (lab, pts, segs, colour) in runs:
                for seg in segs:
                    seg_line(seg, colour, 4)
                for n_, i in enumerate(pts):
                    d.DrawDot(W(i), "{}{}".format(lab, n_ + 1), colour, col(255, 255, 255))
            pts = fm["pts"]; h = fm.get("hot")
            if pts and h is not None and h not in pts:          # where the next click goes
                d.DrawDottedLine(W(pts[-1]), W(h), col(255, 200, 0))
            if h is not None:
                d.DrawPoint(W(h), Rhino.Display.PointStyle.RoundSimple, 9, col(255, 200, 0))
        except Exception:
            pass
        for ns_ in (getattr(self, "fillet_preview", None) or []):
            try:
                d.DrawSurface(ns_, col(230, 120, 0), 4)
            except Exception:
                pass

class Session(SettingsMixin, ScanMixin, PolygonMixin, DrawMixin, BuildMixin, SmoothMixin, ViewMixin):
    """One msh2classA run: the scan stays loaded, patches are added one by one."""

    def __init__(self, mesh):
        self.mesh = mesh
        try:      # face normals are needed by every scan lookup (also before the first build)
            if mesh.FaceNormals.Count != mesh.Faces.Count:
                mesh.FaceNormals.ComputeFaceNormals()
        except Exception:
            pass
        mb = mesh.GetBoundingBox(False)
        self.size = mb.Diagonal.Length
        self.tol_on = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 1e-6)
        self.cands = []
        self.patches = []          # list of lists of object ids
        self.reset_polygon()
        self.hover = None
        self.hover_srf = None
        self.smooth_undo = []       # steps: list of (object id, old geometry)
        self.n_wall = 0; self.n_back = 0; self.refs = []; self.ray_back = 1.0
        self.mouse_vp = None
        self.down = None
        self.fit_pts = []
        self.refresh()

    def refresh(self):
        self.cands = collect_candidates(self.mesh)
        self.surfs = collect_surfaces(self.mesh)
        self.cvnets = {}            # id -> (CV grid, front normal grid)
        for (oid, brep, bb, ok) in self.surfs:
            try:
                ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
                nu, nv = ns.Points.CountU, ns.Points.CountV
                try:
                    sgn = -1.0 if rs.GetUserText(oid, "msh2classA_side") == "-1" else 1.0
                except Exception:
                    sgn = 1.0
                du_, dv_ = ns.Domain(0), ns.Domain(1)
                net = []; nrm = []
                for i in range(nu):
                    row = []; nrow = []
                    for j in range(nv):
                        row.append(ns.Points.GetControlPoint(i, j).Location)
                        u = du_.ParameterAt(i / float(max(nu - 1, 1))); v = dv_.ParameterAt(j / float(max(nv - 1, 1)))
                        n_ = ns.NormalAt(u, v)
                        nrow.append((n_.X * sgn, n_.Y * sgn, n_.Z * sgn))
                    net.append(row); nrm.append(nrow)
                self.cvnets[oid] = (net, nrm)
            except Exception:
                pass
        try:
            nps = self._npatches()
        except Exception:
            nps = []
        npids = set(i for (i, b) in nps)
        try:
            self.markers = [(p, t, col(*c)) for (p, t, c) in
                            analyze_seams([c for c in self.cands if c.oid not in npids],
                                          sc.doc.ModelAbsoluteTolerance, 1.0 / (100.0 * self.size))]
        except Exception:
            self.markers = []
        if nps:                         # seams of the NPatches (trimmed: measured along the neighbours' edges)
            try:
                self.markers += [(p, t, col(*c)) for (p, t, c) in
                                 analyze_npatch_seams(self.cands, nps, sc.doc.ModelAbsoluteTolerance,
                                                      1.0 / (100.0 * self.size), 0.5 * self.s_tol())]
            except Exception:
                pass
        self.qcache = {}
        self.centre_cache = {}          # patch centres move with every edit
        self.matcap_meshes = {}
        self.graph_cache = {}           # curvature combs / deviation dots: made again after every change
        self.dev_cache = {}
        if "analysis_state" not in self.__dict__:
            self.read_analysis()
        self.apply_analysis(new_only=True)      # new / changed patches get what is on
        if getattr(self, "mirror_view", False):
            self.update_mirror_view()


def smooth_curve(pts):
    """Fair single-span curve through clicked points: ends exact, inner
    points approximated (no waves from imprecise clicks).
    2 pts -> line, 3 -> parabola through all, 4 -> cubic through all,
    5+ -> least-squares cubic."""
    if len(pts) == 2:
        return rg.LineCurve(pts[0], pts[1])
    deg = min(3, len(pts) - 1)
    tp = [T(p) for p in pts]
    try:
        C = fit_curve(tp, chord_params(tp), BDir(deg, 1))
        crv = rg.NurbsCurve.Create(False, deg, [P3(c) for c in C])
        if crv is not None and crv.IsValid:
            return crv
    except Exception:
        pass
    return rg.PolylineCurve(pts)

def draw_smooth(d, pts, color, thick):
    if len(pts) < 3:
        if len(pts) == 2: d.DrawLine(pts[0], pts[1], color, thick)
        return
    crv = smooth_curve(pts)
    if crv is None:
        draw_poly(d, pts, color, thick); return
    dom = crv.Domain
    draw_poly(d, [crv.PointAt(dom.ParameterAt(i / 40.0)) for i in range(41)], color, thick)

def draw_poly(d, pts, color, thick):
    for k in range(len(pts) - 1):
        d.DrawLine(pts[k], pts[k + 1], color, thick)

def make_nurbs(du, dv, P, ox):
    ns = rg.NurbsSurface.Create(3, False, du.p + 1, dv.p + 1, du.n, dv.n)
    if ns is None: return None
    for i, k in enumerate(du.U[1:-1]): ns.KnotsU[i] = k
    for i, k in enumerate(dv.U[1:-1]): ns.KnotsV[i] = k
    for i in range(du.n):
        for j in range(dv.n):
            x, y, z = P[i * dv.n + j]
            ns.Points.SetControlPoint(i, j, rg.ControlPoint(rg.Point3d(x + ox[0], y + ox[1], z + ox[2])))
    return ns

# =============================================================================
# DISPLAY MODE FOR SCAN WORK (3dPARTdesign_ScanCAD) - on while the plugin runs
# =============================================================================

def _keep_display_mode(m):
    """A display mode imported by a script lives only in memory: Rhino drops
    it at the next restart, SaveAs or ImportLayout (it was never written to
    the settings). Rhino writes the settings only when it sees a change -
    so: flip InMenu, update, flip back, update. The mode then stays in
    Options > View > Display modes like one you imported yourself."""
    DMD = Rhino.Display.DisplayModeDescription
    try:
        was = m.InMenu
        m.InMenu = not was
        DMD.UpdateDisplayMode(m)
        m.InMenu = True if not was else was
        DMD.UpdateDisplayMode(m)
    except Exception:
        try: DMD.UpdateDisplayMode(m)
        except Exception: pass


def scan_display_mode():
    """The ScanCAD display mode: found by name, otherwise imported once from
    msh2classA_3dPARTdesign_com.ini next to the script (and kept in Rhino's list of
    display modes for good). None if neither exists."""
    DMD = Rhino.Display.DisplayModeDescription
    for nm in SCAN_DISPLAY_NAMES:
        try:
            m = DMD.FindByName(nm)
            if m is not None:
                if not sc.sticky.get("M2CA_display_kept"):      # once per session: make sure it is saved
                    _keep_display_mode(m)
                    sc.sticky["M2CA_display_kept"] = True
                return m
        except Exception:
            pass
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, SCAN_DISPLAY_FILE)
    except Exception:
        path = None
    if path and os.path.isfile(path):
        try:
            mid = DMD.ImportFromFile(path)
            m = DMD.GetDisplayMode(mid)
            if m is not None:
                _keep_display_mode(m)
                sc.sticky["M2CA_display_kept"] = True
                print("[Display] mode '{}' imported from {} - it stays in your display modes".format(
                    m.EnglishName, SCAN_DISPLAY_FILE))
                return m
        except Exception:
            print("[Display] could not import {}".format(path))
    print("[Display] no display mode '{}' and no {} next to the script - the views stay as they are".format(
        SCAN_DISPLAY_NAMES[0], SCAN_DISPLAY_FILE))
    return None


def scan_display_on():
    """Every view to the ScanCAD mode; the modes they had are remembered (also
    in sticky - after a crash the next run still restores the right ones)."""
    mode = scan_display_mode()
    if mode is None: return
    prev = sc.sticky.get("M2CA_prev_display")
    if prev is None:
        prev = {}
        for v in sc.doc.Views.GetViewList(True, False):
            try: prev[str(v.ActiveViewportID)] = v.ActiveViewport.DisplayMode.Id
            except Exception: pass
        sc.sticky["M2CA_prev_display"] = prev
    for v in sc.doc.Views.GetViewList(True, False):
        try:
            if v.ActiveViewport.DisplayMode.Id != mode.Id:
                v.ActiveViewport.DisplayMode = mode
        except Exception:
            pass
    sc.doc.Views.Redraw()


def scan_display_off():
    """Every view back to the mode it had before the plugin. The ScanCAD
    mode itself is NOT removed - it stays in the list of display modes."""
    prev = sc.sticky.get("M2CA_prev_display")
    if not prev: return
    DMD = Rhino.Display.DisplayModeDescription
    for v in sc.doc.Views.GetViewList(True, False):
        try:
            mid = prev.get(str(v.ActiveViewportID))
            m = DMD.GetDisplayMode(mid) if mid is not None else None
            if m is not None and v.ActiveViewport.DisplayMode.Id != m.Id:
                v.ActiveViewport.DisplayMode = m
        except Exception:
            pass
    sc.sticky["M2CA_prev_display"] = None
    sc.doc.Views.Redraw()


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
    bnorm), through the scan points inner, and as little bending as possible
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


# =============================================================================
# FILLET ALONG A GAP: side A and side B may have DIFFERENT numbers of
# patches. Every big patch is moved to its contact line (the ball rolled
# along its own edge against the patch across); the fillet is split at the
# corners of BOTH sides, so every piece sits on one patch of A and one of B
# (exact G2 to both); two pieces meeting at a corner use the same ball.
# =============================================================================

def _bez_sub_1d(C, a, b):
    """Bezier control points C (one row) restricted to [a, b]."""
    def split(Q, t):
        L = [Q[0]]; R = [Q[-1]]; W = list(Q)
        while len(W) > 1:
            W = [tuple((1 - t) * x + t * y for x, y in zip(p, q)) for p, q in zip(W[:-1], W[1:])]
            L.append(W[0]); R.append(W[-1])
        return L, list(reversed(R))
    if b < 1.0 - 1e-12:
        C = split(C, b)[0]
    if a > 1e-12:
        C = split(C, a / b)[1]
    return C


def side_sub_patch(p, k, t0, t1):
    """The same patch restricted to t in [t0, t1] of side k (t0 < t1); its
    side k runs the same way. -> SPatch."""
    nu, nv = p.du.n, p.dv.n
    if k in (0, 2):                                   # t runs along u
        a, b = (t0, t1) if k == 0 else (1.0 - t1, 1.0 - t0)
        cols = []
        for j in range(nv):
            cols.append(_bez_sub_1d([p.P[i * nv + j] for i in range(nu)], a, b))
        P = [cols[j][i] for i in range(nu) for j in range(nv)]
    else:                                             # t runs along v
        a, b = (t0, t1) if k == 1 else (1.0 - t1, 1.0 - t0)
        P = []
        for i in range(nu):
            P.extend(_bez_sub_1d([p.P[i * nv + j] for j in range(nv)], a, b))
    return SPatch(p.key, p.du.p, p.dv.p, P)


def _swap_contact(c):
    """The same ball seen from the other side."""
    return Contact(c.tB, c.eB, c.t, c.eA, c.C, c.pB, c.pA, c.nB, c.nA, c.R)


def _roll_against_chain(X, kX, chain, Rf, stations):
    """Ball rolled along X's side kX against the patches of the other side
    (each station against the one across). -> [(Contact, index in chain)]."""
    out = []
    for t in stations:
        q = side_eval(X, kX, t, 0.0, 1)["p"]
        order = sorted(range(len(chain)), key=lambda i: _len(_sub(side_eval(chain[i][0], chain[i][1],
                                                                             _closest_t_edge(chain[i][0], chain[i][1], q), 0.0, 1)["p"], q)))
        got = None
        for i in order[:3]:
            Y, kY = chain[i][0], chain[i][1]
            cs = rolling_ball(X, kX, Y, kY, Rf, [t])
            c = cs[0] if cs else None
            if c is not None and -0.05 <= c.tB <= 1.05:
                got = (c, i); break
        out.append(got)
    return out


def gap_fillet(Achain, Bchain, R0, R1=None, level=2, shape=None, nst=21, merge=0.08):
    """Achain / Bchain: [(patch, side, forward)] in order along the gap
    (forward: the side's t runs along the gap). Both sides may have
    different numbers of patches. -> dict(A={key: CVs}, B={key: CVs},
    pieces=[{"F": CVs, "deg": (along, 5), ...}], shape, why) or (None, why)."""
    nA = len(Achain)
    R1 = R0 if R1 is None else R1
    # gap parameter u along A: patch i covers [i, i+1]
    def u_of_A(i, t):
        return i + (t if Achain[i][2] else 1.0 - t)
    def a_at(q):
        best = None
        for i, (X, kX, f) in enumerate(Achain):
            t = _closest_t_edge(X, kX, q)
            d = _len(_sub(side_eval(X, kX, t, 0.0, 1)["p"], q))
            if best is None or d < best[0]: best = (d, i, t)
        return best[1], best[2]
    Ru = lambda u: R0 + (R1 - R0) * max(0.0, min(1.0, u / float(nA)))
    st = [i / (nst - 1.0) for i in range(nst)]
    # ---- 1. every big patch to its contact line
    newA = []; newB = []; famA = []; famB = []
    for i, (X, kX, f) in enumerate(Achain):
        rs_ = _roll_against_chain(X, kX, Bchain, lambda t, i=i: Ru(u_of_A(i, t)), st)
        if any(r is None for r in rs_):
            return None, "the ball does not roll along A{} everywhere - radius too big for the gap?".format(i + 1)
        cA = poly_fit_ends(st, [r[0].eA for r in rs_], 4)
        PX, dev = reshape_to_line(X, kX, lambda t: poly_val(cA, t))
        newA.append((SPatch(X.key, X.du.p, X.dv.p, PX), dev)); famA.append(rs_)
    for j, (Y, kY, f) in enumerate(Bchain):
        def RfB(t, Y=Y, kY=kY):
            i, ta = a_at(side_eval(Y, kY, t, 0.0, 1)["p"])
            return Ru(u_of_A(i, ta))
        rs_ = _roll_against_chain(Y, kY, Achain, RfB, st)
        if any(r is None for r in rs_):
            return None, "the ball does not roll along B{} everywhere - radius too big for the gap?".format(j + 1)
        cB = poly_fit_ends(st, [r[0].eA for r in rs_], 4)
        PY, dev = reshape_to_line(Y, kY, lambda t: poly_val(cB, t))
        newB.append((SPatch(Y.key, Y.du.p, Y.dv.p, PY), dev)); famB.append(rs_)
    # ---- 2. stations: the corners of both sides, in u
    def ends_of(fam, chain, j):
        first = fam[j][0] if chain[j][2] else fam[j][-1]
        last = fam[j][-1] if chain[j][2] else fam[j][0]
        return first, last
    stations = []          # (u, A side: (contact on A, A index), B side: (contact on B, B index))
    for i in range(nA + 1):                         # A corners: A's own balls
        if i < nA:
            c, jb = ends_of(famA, Achain, i)[0]
            ia = i
        else:
            c, jb = ends_of(famA, Achain, nA - 1)[1]
            ia = nA - 1
        stations.append({"u": float(i), "A": (c, ia), "B": (_swap_contact(c), jb), "kind": "A"})
    for j in range(len(Bchain) + 1):                # B corners: B's own balls
        if j < len(Bchain):
            c, ia = ends_of(famB, Bchain, j)[0]; jb = j
        else:
            c, ia = ends_of(famB, Bchain, len(Bchain) - 1)[1]; jb = len(Bchain) - 1
        cA_ = _swap_contact(c)                       # the ball seen from A
        i_, ta = a_at(cA_.pA)
        stations.append({"u": u_of_A(i_, ta), "A": (cA_, i_), "B": (c, jb), "kind": "B", "j": j})
    stations.sort(key=lambda s_: s_["u"])
    # the gap runs where BOTH sides are: from the later start to the earlier end
    bs = [s_ for s_ in stations if s_["kind"] == "B"]
    u0 = max(0.0, bs[0]["u"]); u1 = min(float(nA), bs[-1]["u"])
    if abs(bs[0]["u"]) < merge: u0 = min(0.0, bs[0]["u"])            # ends across each other: one station each
    if abs(bs[-1]["u"] - nA) < merge: u1 = max(float(nA), bs[-1]["u"])
    merged = []
    for s_ in stations:
        if s_["u"] < u0 - 1e-6 or s_["u"] > u1 + 1e-6:
            continue
        if merged and abs(s_["u"] - merged[-1]["u"]) < merge:
            m_ = merged[-1]                          # A corner + B corner across each other: one station,
            if s_["kind"] == "B": m_["B"] = s_["B"]  # each side its own ball (no sliver piece)
            else: m_["A"] = s_["A"]; m_["u"] = s_["u"]
            m_["kind"] = "AB"
            continue
        merged.append(dict(s_))
    if len(merged) < 2:
        return None, "the two sides do not run along each other"
    # ---- 3. one fillet per piece between stations
    pieces = []; sh = shape
    for n_ in range(len(merged) - 1):
        S0, S1 = merged[n_], merged[n_ + 1]
        um = 0.5 * (S0["u"] + S1["u"])
        ia = min(nA - 1, int(math.floor(um)))
        X, kX, fX = Achain[ia]; X2 = newA[ia][0]
        def ta_of(S):
            c, i_ = S["A"]
            if S["kind"] in ("A", "AB") and abs(S["u"] - round(S["u"])) < 1e-9:
                ui = int(round(S["u"]))
                if ui == ia: return 0.0 if fX else 1.0
                if ui == ia + 1: return 1.0 if fX else 0.0
            return _closest_t_edge(X2, kX, c.pA)
        ta0, ta1 = ta_of(S0), ta_of(S1)
        # B patch of the piece: the one across the middle
        qm = side_eval(X2, kX, 0.5 * (ta0 + ta1), 0.0, 1)["p"]
        jb = min(range(len(Bchain)), key=lambda j: _len(_sub(side_eval(newB[j][0], Bchain[j][1],
                                                                      _closest_t_edge(newB[j][0], Bchain[j][1], qm), 0.0, 1)["p"], qm)))
        Y, kY, fY = Bchain[jb]; Y2 = newB[jb][0]
        def tb_of(S):
            c, j_ = S["B"]
            if S["kind"] in ("B", "AB") and j_ is not None:
                j0 = S.get("j")
                if j0 is not None:
                    if j0 == jb: return 0.0 if fY else 1.0
                    if j0 == jb + 1: return 1.0 if fY else 0.0
            return _closest_t_edge(Y2, kY, c.pA)
        tb0, tb1 = tb_of(S0), tb_of(S1)
        if abs(ta1 - ta0) < 1e-4 or abs(tb1 - tb0) < 1e-4:
            continue
        As = side_sub_patch(X2, kX, min(ta0, ta1), max(ta0, ta1))
        Bs = side_sub_patch(Y2, kY, min(tb0, tb1), max(tb0, tb1))
        revA = ta1 < ta0; revB = tb1 < tb0
        if revA:                                      # the fillet runs along A's t: flip the view of the gap
            revB = not revB
        # contacts at the piece ends (+ middle) as the fillet expects them
        def cA_rec(S, tf):
            c = S["A"][0]
            return Contact(tf, c.eA, c.tB, c.eB, c.C, c.pA, c.pB, c.nA, c.nB, c.R)
        def cB_rec(S, tf):
            c = S["B"][0]
            return Contact(tf, c.eA, c.tB, c.eB, c.C, c.pA, c.pB, c.nA, c.nB, c.R)
        Sa, Sb = (S1, S0) if revA else (S0, S1)       # in As's own t order
        tm = 0.5 * (ta0 + ta1); tbm = 0.5 * (tb0 + tb1)
        Rm = Ru(um)
        cm = rolling_ball(X, kX, Y, kY, lambda t: Rm, [tm])[0]
        cmb = rolling_ball(Y, kY, X, kX, lambda t: Rm, [tbm])[0]
        if cm is None or cmb is None:
            return None, "the ball does not roll in piece {}".format(n_ + 1)
        cs = [cA_rec(Sa, 0.0), Contact(0.5, cm.eA, cm.tB, cm.eB, cm.C, cm.pA, cm.pB, cm.nA, cm.nB, cm.R),
              cA_rec(Sb, 1.0)]
        # B's records in Bs's own t order
        Sbb = (S0, S1) if tb0 < tb1 else (S1, S0)
        csB = [cB_rec(Sbb[0], 0.0), Contact(0.5, cmb.eA, cmb.tB, cmb.eB, cmb.C, cmb.pA, cmb.pB, cmb.nA, cmb.nB, cmb.R),
               cB_rec(Sbb[1], 1.0)]
        prep = {"A": As, "kA": kX, "B": Bs, "kB": kY, "PA": As.P, "PB": Bs.P, "cs": cs, "csB": csB,
                "revB": revB, "devA": newA[ia][1], "devB": newB[jb][1]}
        if sh is None:
            sh = auto_shape(prep, level)
        res = build_fillet(prep, level, sh)
        pieces.append({"F": res["F"], "deg": res["deg"], "A": (ia, min(ta0, ta1), max(ta0, ta1)),
                       "B": (jb, min(tb0, tb1), max(tb0, tb1)), "u": (S0["u"], S1["u"]), "revA": revA})
    if not pieces:
        return None, "no piece could be made"
    return {"A": dict((newA[i][0].key, newA[i][0].P) for i in range(nA)),
            "B": dict((newB[j][0].key, newB[j][0].P) for j in range(len(Bchain))),
            "devA": max(d for (p_, d) in newA), "devB": max(d for (p_, d) in newB),
            "pieces": pieces, "shape": sh, "stations": [s_["u"] for s_ in merged]}, ""


def free_edge_graph(allp, corners, tol):
    """Whole free sides between patch corners: [(i, j, patch, side, length)]
    (i at the side's t = 0, j at t = 1)."""
    def ci(q):
        best = None
        for n, c in enumerate(corners):
            d = _len(_sub(c, q))
            if best is None or d < best[0]: best = (d, n)
        return best[1] if best and best[0] < tol else None
    out = []
    for p in allp:
        for k in range(4):
            a = side_eval(p, k, 0.0, 0.0, 1)["p"]; b = side_eval(p, k, 1.0, 0.0, 1)["p"]
            i, j = ci(a), ci(b)
            if i is None or j is None or i == j: continue
            free = True
            for s_ in (0.25, 0.5, 0.75):
                mq = side_eval(p, k, s_, 0.0, 1)["p"]
                for q in allp:
                    if q is p or not _bb_near(q, mq, tol): continue
                    for kq in range(4):
                        t_ = _closest_t_edge(q, kq, mq)
                        if _len(_sub(side_eval(q, kq, t_, 0.0, 1)["p"], mq)) < tol:
                            free = False; break
                    if not free: break
                if not free: break
            if free:
                out.append((i, j, p, k, _len(_sub(a, b))))
    return out


def edge_path(graph, i0, i1):
    """Shortest run of free sides from corner i0 to corner i1:
    [(patch, side, ta, tb)] or None."""
    import heapq
    adj = {}
    for (i, j, p, k, L) in graph:
        adj.setdefault(i, []).append((j, (p, k, 0.0, 1.0), L))
        adj.setdefault(j, []).append((i, (p, k, 1.0, 0.0), L))
    dist = {i0: 0.0}; prev = {}
    h = [(0.0, i0)]
    while h:
        d, n = heapq.heappop(h)
        if n == i1: break
        if d > dist.get(n, 1e300): continue
        for (m_, seg, L) in adj.get(n, []):
            nd = d + L
            if nd < dist.get(m_, 1e300):
                dist[m_] = nd; prev[m_] = (n, seg); heapq.heappush(h, (nd, m_))
    if i1 not in prev:
        return None
    out = []; n = i1
    while n != i0:
        n0, seg = prev[n]; out.append((n, seg)); n = n0
    return list(reversed(out))          # [(corner reached, seg)]


def cut_at_plane(p, axis, val, nst=21, keep=None):
    """Patch p cut at the plane x[axis] = val (or extended to it): the part
    where most of the patch lies stays; the side across which the plane runs
    goes onto the plane - the same surface, no trim (CVs refitted).
    -> (side, new CVs, deviation, keep sign) or (None, reason)."""
    f = lambda q: q[axis] - val
    n = 9
    pos = neg = 0
    for i in range(n):
        for j in range(n):
            q = srf_point(p.du, p.dv, p.P, i / (n - 1.0), j / (n - 1.0))
            if f(q) >= 0: pos += 1
            else: neg += 1
    if keep is None:
        keep = 1.0 if pos >= neg else -1.0
        if min(pos, neg) == 0:
            return None, "does not cross the mirror"
    # the side that goes onto the plane: the most of it on the wrong side
    best = None
    for k in range(4):
        ws = sum(1 for i in range(11) if keep * f(side_eval(p, k, i / 10.0, 0.0, 1)["p"]) < 0)
        if best is None or ws > best[0]: best = (ws, k)
    k = best[1]
    st = [i / (nst - 1.0) for i in range(nst)]
    es = []
    for t in st:
        def g(e):
            uo, vo = side_uv(k, t, e)
            return keep * f(srf_derivs_ext(p.du.p, p.dv.p, p.P, uo, vo, 0)[(0, 0)])
        lo, hi = -0.98, 0.6                      # inside .. a little beyond the edge
        glo, ghi = g(lo), g(hi)
        if glo < 0 or ghi > 0:
            return None, "the mirror plane does not run across the patch from side to side"
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if g(mid) > 0: lo = mid
            else: hi = mid
        es.append(0.5 * (lo + hi))
    c = poly_fit_ends(st, es, 4)
    P2, dev = reshape_to_line(p, k, lambda t: poly_val(c, t))
    return k, P2, dev, keep


def _jacobi_max_eigvec(A, iters=60):
    """Symmetric 4x4 A -> the eigenvector of its largest eigenvalue (Jacobi)."""
    n = len(A)
    A = [list(r) for r in A]
    V = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(iters):
        off = sum(A[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-30: break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(A[p][q]) < 1e-300: continue
                th = 0.5 * math.atan2(2.0 * A[p][q], A[q][q] - A[p][p])
                c, s = math.cos(th), math.sin(th)
                for k in range(n):
                    akp, akq = A[k][p], A[k][q]
                    A[k][p] = c * akp - s * akq; A[k][q] = s * akp + c * akq
                for k in range(n):
                    apk, aqk = A[p][k], A[q][k]
                    A[p][k] = c * apk - s * aqk; A[q][k] = s * apk + c * aqk
                for k in range(n):
                    vkp, vkq = V[k][p], V[k][q]
                    V[k][p] = c * vkp - s * vkq; V[k][q] = s * vkp + c * vkq
    i = max(range(n), key=lambda k: A[k][k])
    return [V[k][i] for k in range(n)]


def best_rigid(src, dst):
    """Rotation R and translation t with R*src + t closest to dst (least
    squares, Horn's quaternion method). src, dst: [(x, y, z)] (>= 3 not on
    a line for a full turn). -> (R as 3x3 rows, t)."""
    n = float(len(src))
    cs = tuple(sum(p[i] for p in src) / n for i in range(3))
    cd = tuple(sum(p[i] for p in dst) / n for i in range(3))
    S = [[0.0] * 3 for _ in range(3)]
    for a, b in zip(src, dst):
        a_ = _sub(a, cs); b_ = _sub(b, cd)
        for i in range(3):
            for j in range(3):
                S[i][j] += a_[i] * b_[j]
    Sxx, Sxy, Sxz = S[0]; Syx, Syy, Syz = S[1]; Szx, Szy, Szz = S[2]
    N = [[Sxx + Syy + Szz, Syz - Szy, Szx - Sxz, Sxy - Syx],
         [Syz - Szy, Sxx - Syy - Szz, Sxy + Syx, Szx + Sxz],
         [Szx - Sxz, Sxy + Syx, -Sxx + Syy - Szz, Syz + Szy],
         [Sxy - Syx, Szx + Sxz, Syz + Szy, -Sxx - Syy + Szz]]
    q0, qx, qy, qz = _jacobi_max_eigvec(N)
    R = [[q0*q0 + qx*qx - qy*qy - qz*qz, 2*(qx*qy - q0*qz), 2*(qx*qz + q0*qy)],
         [2*(qy*qx + q0*qz), q0*q0 - qx*qx + qy*qy - qz*qz, 2*(qy*qz - q0*qx)],
         [2*(qz*qx - q0*qy), 2*(qz*qy + q0*qx), q0*q0 - qx*qx - qy*qy + qz*qz]]
    Rc = tuple(sum(R[i][j] * cs[j] for j in range(3)) for i in range(3))
    return R, _sub(cd, Rc)


def follow_corner(N, kN, t_end, target):
    """Neighbour N along the fillet edge: its free side kN gets its corner at
    t_end (0/1) onto target (moving along N's own side line), the other end
    stays - so no gap opens where the fillet moved the patch next to it.
    -> new CVs or None."""
    e0 = _closest_te(N, kN, target, t_end, 0.0, 30, True)[1]
    q = side_eval(N, kN, t_end, e0, 1)["p"]
    if _len(_sub(q, target)) > 1e-3 * max(1.0, _len(target)) + 1e-3:
        return None
    if t_end == 0.0:
        ef = lambda t: e0 * (1.0 - t)
    else:
        ef = lambda t: e0 * t
    P2, dev = reshape_to_line(N, kN, ef)
    return P2


# =============================================================================
# CV ORDER RULE (every patch button): a CV never jumps over the CV next to it
# in its row or column, and never comes extremely close to it if it was not
# that close before. A step that would do it is shortened; if even a short
# step does it, nothing changes and the button says which CV.
# =============================================================================

CV_NEAR = 0.15      # a CV closer than this share of the gap to the CV next to it = "extremely close"
CV_MARGIN = 0.03    # ... and only if it comes closer by more than this than it already was


def cv_order_worst(P, nu, nv):
    """Smallest position of an inner CV between its two neighbours along its
    row and along its column (0..1; 0.5 = in the middle, <0 or >1 = jumped
    over one; returned as min(t, 1-t)) and where: (value, i, j, 'U'/'V')."""
    best = (1.0, -1, -1, "")
    for i in range(nu):
        for j in range(nv):
            c = P[i * nv + j]
            for (d, a, b) in (("U", (i - 1, j), (i + 1, j)), ("V", (i, j - 1), (i, j + 1))):
                if not (0 <= a[0] < nu and 0 <= b[0] < nu and 0 <= a[1] < nv and 0 <= b[1] < nv):
                    continue
                pa = P[a[0] * nv + a[1]]; pb = P[b[0] * nv + b[1]]
                ab = v_sub(pb, pa)
                L2 = v_dot(ab, ab)
                if L2 < 1e-24:
                    continue
                t = v_dot(v_sub(c, pa), ab) / L2
                m_ = min(t, 1.0 - t)
                if m_ < best[0]:
                    best = (m_, i, j, d)
    return best


def seam_states(patches, find_tol, gap_tol):
    """{seam key: (stored level, gap, kink deg, rel. curvature difference)}."""
    out = {}
    for sm in find_seams(patches, find_tol):
        c_ = dict(sm)
        W = seam_level(c_, find_tol)
        seam_measure(c_, gap_tol)
        out[seam_key(sm)] = (W, c_.get("gap", 0.0), c_.get("ang", 0.0), c_.get("dk", 0.0))
    return out


def seams_not_worse(before, after, allow=None, gap_tol=0.0):
    """-> (ok, text). Against the seam's STORED level, never against the
    last click (small allowances per click added up: G2 -> G1 -> G0 over a
    few clicks): no gap opens, the kink never gets above max(before, 2x the
    G1 limit), the curvature difference never above max(before, 2x the G2
    limit). allow {seam key: level}: MatchUVN may set the level chosen."""
    for k_, (W, g0, a0, d0) in before.items():
        if k_ not in after:
            return False, "seam {}-{} would open".format(k_[0][-4:], k_[2][-4:])
        W1, g1, a1, d1 = after[k_]
        if allow and k_ in allow:
            W = allow[k_]
        if g1 > max(g0, gap_tol) * 1.5 + 1e-6:
            return False, "seam {}-{} would open a gap ({} -> {})".format(k_[0][-4:], k_[2][-4:], fl(g0), fl(g1))
        if W >= 1 and a1 > max(a0, 2.0 * SEAM_ANG) + 0.005:
            return False, "kink on seam {}-{} would grow {:.2f} -> {:.2f} deg".format(k_[0][-4:], k_[2][-4:], a0, a1)
        if W >= 2 and d1 > max(d0, 2.0 * SEAM_K) + 0.005:
            return False, "seam {}-{} would lose G2 (curvature {:.0%} -> {:.0%})".format(k_[0][-4:], k_[2][-4:], d0, d1)
    return True, ""


def cv_order_ok(old, new, nu, nv):
    """-> (ok, text). Not ok: a CV jumped over its neighbour, or came closer
    than CV_NEAR of the gap to it while it was not that close before."""
    w1 = cv_order_worst(new, nu, nv)
    if w1[0] >= CV_NEAR - 1e-9:
        return True, ""
    w0 = cv_order_worst(old, nu, nv)
    if w1[0] < 0.0 and (w0[0] >= 0.0 or w1[0] < w0[0] - 1e-3):
        return False, "CV {}/{} would jump over the CV next to it ({})".format(w1[1] + 1, w1[2] + 1, w1[3])
    if w1[0] < min(CV_NEAR, w0[0]) - CV_MARGIN:
        return False, "CV {}/{} would come extremely close to the CV next to it ({})".format(w1[1] + 1, w1[2] + 1, w1[3])
    return True, ""


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 50)
    print(VERSION + "   (Help option explains everything)")
    print(LEGAL_LINE)
    print("=" * 50)
    init_colors()
    preset = None; carry = None; n_total = 0
    while True:
        mesh = get_scan(preset)
        if mesh is None: break
        busy = show_busy("Preparing scan...")
        try:
            check_esc()
        except UserAbort:
            hide_busy(busy); print("[STOPPED] Esc - script ended"); break
        try:
            v0 = mesh.Vertices[0]
            mesh.ClosestPoint(rg.Point3d(v0.X, v0.Y, v0.Z))     # builds search tree once
        except Exception:
            pass
        try:      # first call of the big helpers is slow (compiling) - do it now, not at the first click
            mesh.ClosestMeshPoint(rg.Point3d(v0.X, v0.Y, v0.Z), 0.0)
            prof = [(float(i), 0.0, 0.01 * i * i) for i in range(31)]
            single_span_reach(prof, 5, 0.2); single_span_reach(prof, 3, 0.2)
            patch_score([[(float(i), float(j), 0.01 * i * j) for j in range(9)] for i in range(9)], 5, 5, 0.2)
        except Exception:
            pass
        if esc_pressed():
            hide_busy(busy); print("[STOPPED] Esc - script ended"); break
        session = Session(mesh)
        session.scan_id = getattr(get_scan, "obj_id", None)
        if carry: session.patches = carry
        if not session.open_panel():
            print("[INFO] no view panel (Eto) - the view buttons are drawn in the top right corner instead")
        hide_busy(busy)
        print("[OK] {} edges/curves pickable".format(len(session.cands)))
        tool = None
        try:
            while True:
                r = session.collect_polygon()
                if r == "exit":
                    break
                if r == "scantool":
                    tool = session.pending_tool
                    break
                try:
                    session.build()
                except Exception:
                    print("[ERROR] build failed:")
                    print(traceback.format_exc())
                session.reset_polygon()
                session.refresh()
        finally:
            session.restore_view()
        n_total = sum(len(p) for p in session.patches)
        if tool is None:
            break
        run_scan_tool(tool)                      # the scans may have moved: the scan is read again
        preset = session.scan_id; carry = session.patches
        print("[{}] done - back to the patches (scan read again where it is now)".format(tool))
    print("\n{} finished: {} patch(es) created.".format(VERSION, n_total))


if __name__ == "__main__" and IN_RHINO:
    hook_escape()
    try:
        try: scan_display_on()
        except Exception: pass
        main()
    except UserAbort:
        print("[STOPPED] Esc - script ended")
        try: Rhino.UI.StatusBar.HideProgressMeter()
        except Exception: pass
        sc.doc.Views.Redraw()
    finally:
        try: scan_display_off()                     # views back to the modes they had
        except Exception: pass
