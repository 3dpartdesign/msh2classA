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

