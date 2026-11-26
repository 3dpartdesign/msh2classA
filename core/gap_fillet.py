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
from .smoothing import SPatch
from .fillet import Contact, _closest_t_edge, _closest_te, _len, _sub, auto_shape, build_fillet, poly_fit_ends, poly_val, reshape_to_line, rolling_ball, side_eval, side_uv, srf_derivs_ext
from .npatch import _bb_near
# <<< dev-only
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


