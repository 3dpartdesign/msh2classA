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
from .vectors import fl, v_dot, v_sub
from .smoothing import find_seams, seam_key, seam_level, seam_measure
# <<< dev-only
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


