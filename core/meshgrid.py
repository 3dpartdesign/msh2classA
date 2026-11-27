# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Closest point and ray queries on a triangle mesh, pure Python.

This is the mesh access the patch builder needs (core/builder.py: MeshQuery).
Blender has its own fast BVH tree and uses that; FreeCAD and the tests use
this one. It is a uniform grid of triangles - fine for scans of a few
hundred thousand triangles, slow to build for many millions.
"""
from __future__ import print_function, division
import math


def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _mul(a, s): return (a[0] * s, a[1] * s, a[2] * s)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def closest_on_triangle(p, a, b, c):
    """Closest point to p on triangle abc (Ericson, Real-Time Collision Detection 5.1.5)."""
    ab = _sub(b, a); ac = _sub(c, a); ap = _sub(p, a)
    d1 = _dot(ab, ap); d2 = _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0: return a
    bp = _sub(p, b); d3 = _dot(ab, bp); d4 = _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3: return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3); return _add(a, _mul(ab, v))
    cp = _sub(p, c); d5 = _dot(ab, cp); d6 = _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6: return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6); return _add(a, _mul(ac, w))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6)); return _add(b, _mul(_sub(c, b), w))
    den = 1.0 / (va + vb + vc)
    v = vb * den; w = vc * den
    return _add(a, _add(_mul(ab, v), _mul(ac, w)))


class TriMesh(object):
    """verts: [(x, y, z)], tris: [(a, b, c)]  (already in world coordinates)."""

    def __init__(self, verts, tris, cells_per_tri=1.0):
        self.V = [tuple(map(float, v)) for v in verts]
        self.T = [tuple(t) for t in tris]
        self.N = []
        area = 0.0
        for (a, b, c) in self.T:
            n = _cross(_sub(self.V[b], self.V[a]), _sub(self.V[c], self.V[a]))
            l = math.sqrt(_dot(n, n))
            area += 0.5 * l
            self.N.append(_mul(n, 1.0 / l) if l > 0 else (0.0, 0.0, 1.0))
        xs = [v[0] for v in self.V]; ys = [v[1] for v in self.V]; zs = [v[2] for v in self.V]
        self.lo = (min(xs), min(ys), min(zs)); self.hi = (max(xs), max(ys), max(zs))
        diag = math.sqrt(_dot(_sub(self.hi, self.lo), _sub(self.hi, self.lo))) or 1.0
        nt = max(1, len(self.T))
        h = math.sqrt(max(area, 1e-30) / nt) * 2.0 / max(cells_per_tri, 1e-6) ** 0.5
        self.h = max(h, diag / 400.0)
        self.dim = tuple(max(1, int(math.ceil((self.hi[k] - self.lo[k]) / self.h)) + 1) for k in range(3))
        self.grid = {}
        for ti, (a, b, c) in enumerate(self.T):
            pa, pb, pc = self.V[a], self.V[b], self.V[c]
            i0 = self._cell((min(pa[0], pb[0], pc[0]), min(pa[1], pb[1], pc[1]), min(pa[2], pb[2], pc[2])))
            i1 = self._cell((max(pa[0], pb[0], pc[0]), max(pa[1], pb[1], pc[1]), max(pa[2], pb[2], pc[2])))
            for x in range(i0[0], i1[0] + 1):
                for y in range(i0[1], i1[1] + 1):
                    for z in range(i0[2], i1[2] + 1):
                        self.grid.setdefault((x, y, z), []).append(ti)
        self.size = diag

    def _cell(self, p):
        return tuple(min(self.dim[k] - 1, max(0, int((p[k] - self.lo[k]) / self.h))) for k in range(3))

    def closest(self, p):
        """-> (point, face normal, face index) or None."""
        if not self.T:
            return None
        c = self._cell(p)
        best = None
        rmax = max(self.dim)
        seen = set()
        D = self.dim
        for r in range(rmax + 1):
            # only the cells ON the shell r around c (not the whole cube), inside the grid
            for x in range(max(0, c[0] - r), min(D[0] - 1, c[0] + r) + 1):
                ex = abs(x - c[0]) == r
                for y in range(max(0, c[1] - r), min(D[1] - 1, c[1] + r) + 1):
                    if ex or abs(y - c[1]) == r:
                        zs = range(max(0, c[2] - r), min(D[2] - 1, c[2] + r) + 1)
                    else:
                        zs = [z for z in (c[2] - r, c[2] + r) if 0 <= z < D[2]] if r else [c[2]]
                    for z in zs:
                        for ti in self.grid.get((x, y, z), ()):
                            if ti in seen: continue
                            seen.add(ti)
                            a, b, cc = self.T[ti]
                            q = closest_on_triangle(p, self.V[a], self.V[b], self.V[cc])
                            d = _dot(_sub(q, p), _sub(q, p))
                            if best is None or d < best[0]:
                                best = (d, q, ti)
            if best is not None and math.sqrt(best[0]) <= r * self.h:   # unexplored cells are >= r*h away
                break
        if best is None:
            return None
        return best[1], self.N[best[2]], best[2]

    def _hit_tri(self, o, d, ti):
        a, b, c = self.T[ti]
        pa = self.V[a]
        e1 = _sub(self.V[b], pa); e2 = _sub(self.V[c], pa)
        pv = _cross(d, e2); det = _dot(e1, pv)
        if abs(det) < 1e-30: return None
        inv = 1.0 / det
        tv = _sub(o, pa)
        u = _dot(tv, pv) * inv
        if u < -1e-12 or u > 1.0 + 1e-12: return None
        qv = _cross(tv, e1)
        v = _dot(d, qv) * inv
        if v < -1e-12 or u + v > 1.0 + 1e-12: return None
        t = _dot(e2, qv) * inv
        return t if t >= 0.0 else None

    def ray(self, o, d):
        """First hit of o + t*d, t >= 0 -> (t, face normal, face index) or None.
        d need not be unit length (t is in units of |d|)."""
        if not self.T:
            return None
        # clip to the box
        t0, t1 = 0.0, float("inf")
        for k in range(3):
            if abs(d[k]) < 1e-300:
                if o[k] < self.lo[k] or o[k] > self.hi[k]: return None
                continue
            a = (self.lo[k] - o[k]) / d[k]; b = (self.hi[k] - o[k]) / d[k]
            if a > b: a, b = b, a
            t0 = max(t0, a); t1 = min(t1, b)
            if t0 > t1: return None
        p = _add(o, _mul(d, t0))
        cell = list(self._cell(p))
        step = [1 if d[k] > 0 else -1 for k in range(3)]
        tmax = []; tdel = []
        for k in range(3):
            if abs(d[k]) < 1e-300:
                tmax.append(float("inf")); tdel.append(float("inf")); continue
            nb = self.lo[k] + (cell[k] + (1 if step[k] > 0 else 0)) * self.h
            tmax.append((nb - o[k]) / d[k]); tdel.append(self.h / abs(d[k]))
        best = None
        seen = set()
        while True:
            for ti in self.grid.get(tuple(cell), ()):
                if ti in seen: continue
                seen.add(ti)
                t = self._hit_tri(o, d, ti)
                if t is not None and (best is None or t < best[0]):
                    best = (t, ti)
            k = tmax.index(min(tmax))
            if best is not None and best[0] <= tmax[k]:
                break
            if tmax[k] > t1 + self.h:
                break
            cell[k] += step[k]
            if cell[k] < 0 or cell[k] >= self.dim[k]:
                break
            tmax[k] += tdel[k]
        if best is None:
            return None
        return best[0], self.N[best[1]], best[1]
