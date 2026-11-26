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
# <<< dev-only
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

