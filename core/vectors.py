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
IN_RHINO = False   # plain Python (Blender, FreeCAD, tests)
Rhino = sc = None
# <<< dev-only
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

