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

