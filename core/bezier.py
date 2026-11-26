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
from .vectors import v_add, v_mul
# <<< dev-only
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

