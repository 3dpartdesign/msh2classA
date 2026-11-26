# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Read scan meshes (STL binary/ASCII, OBJ, PLY ASCII/binary) - READ ONLY.

The coordinates are taken exactly as they are in the file: no axis swap
(Y-up -> Z-up), no scale. That is what the Rhino version does too (its OBJ
import is forced to MapYtoZ=No), so a setup matrix means the same in every
program.

read_mesh(path) -> (verts, tris)
    verts: list of (x, y, z) floats, tris: list of (a, b, c) indices
Uses numpy when it is there (Blender and FreeCAD have it), else pure Python.
"""
from __future__ import print_function, division
import os
import struct

try:
    import numpy as np
except Exception:          # pragma: no cover - plain Python fallback
    np = None


def read_mesh(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".stl":
        return _read_stl(path)
    if ext == ".obj":
        return _read_obj(path)
    if ext == ".ply":
        return _read_ply(path)
    raise ValueError("not a mesh file msh2classA reads (STL, OBJ, PLY): " + path)


# ---------------------------------------------------------------- STL
def _read_stl(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(84)
        n = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else -1
        if n >= 0 and size == 84 + 50 * n:
            data = f.read()
            return _stl_binary(data, n)
    return _stl_ascii(path)


def _stl_binary(data, n):
    if np is not None:
        rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n)
        corners = rec["v"].reshape(-1, 3).astype(np.float64)
        return _weld_np(corners)
    corners = []
    for i in range(n):
        vals = struct.unpack_from("<12f", data, i * 50 + 12)
        corners.append((vals[0], vals[1], vals[2]))
        corners.append((vals[3], vals[4], vals[5]))
        corners.append((vals[6], vals[7], vals[8]))
    return _weld_py(corners)


def _stl_ascii(path):
    corners = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("vertex"):
                p = s.split()
                corners.append((float(p[1]), float(p[2]), float(p[3])))
    if len(corners) % 3:
        corners = corners[:len(corners) - len(corners) % 3]
    if np is not None and corners:
        return _weld_np(np.array(corners, dtype=np.float64))
    return _weld_py(corners)


def _weld_np(corners):
    """Same coordinates -> one vertex (STL stores every triangle on its own)."""
    uniq, inv = np.unique(corners, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    tris = inv.reshape(-1, 3)
    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    tris = tris[keep]
    return [tuple(map(float, v)) for v in uniq], [tuple(map(int, t)) for t in tris]


def _weld_py(corners):
    idx = {}
    verts = []
    tris = []
    cur = []
    for c in corners:
        k = idx.get(c)
        if k is None:
            k = idx[c] = len(verts)
            verts.append(c)
        cur.append(k)
        if len(cur) == 3:
            if cur[0] != cur[1] and cur[1] != cur[2] and cur[0] != cur[2]:
                tris.append(tuple(cur))
            cur = []
    return verts, tris


# ---------------------------------------------------------------- OBJ
def _read_obj(path):
    verts, tris = [], []
    with open(path, "r", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("f "):
                ids = []
                for tok in line.split()[1:]:
                    i = int(tok.split("/")[0])
                    ids.append(i - 1 if i > 0 else len(verts) + i)
                for j in range(1, len(ids) - 1):          # fan
                    tris.append((ids[0], ids[j], ids[j + 1]))
    return verts, tris


# ---------------------------------------------------------------- PLY
_PLY_T = {"char": "b", "int8": "b", "uchar": "B", "uint8": "B", "short": "h", "int16": "h",
          "ushort": "H", "uint16": "H", "int": "i", "int32": "i", "uint": "I", "uint32": "I",
          "float": "f", "float32": "f", "double": "d", "float64": "d"}


def _read_ply(path):
    with open(path, "rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("not a PLY file: " + path)
        fmt = None
        elements = []           # [name, count, [(name, type) | (name, ("list", ct, it))]]
        while True:
            line = f.readline()
            if not line:
                raise ValueError("PLY header not closed: " + path)
            s = line.decode("ascii", "replace").strip()
            if s == "end_header":
                break
            p = s.split()
            if not p:
                continue
            if p[0] == "format":
                fmt = p[1]
            elif p[0] == "element":
                elements.append([p[1], int(p[2]), []])
            elif p[0] == "property":
                if p[1] == "list":
                    elements[-1][2].append((p[4], ("list", p[2], p[3])))
                else:
                    elements[-1][2].append((p[2], p[1]))
        body = f.read()
    if fmt == "ascii":
        return _ply_ascii(body, elements)
    end = "<" if fmt == "binary_little_endian" else ">"
    return _ply_binary(body, elements, end)


def _ply_ascii(body, elements):
    toks = body.split()
    pos = 0
    verts, tris = [], []
    for name, count, props in elements:
        for _ in range(count):
            vals = {}
            for pname, ptype in props:
                if isinstance(ptype, tuple):
                    n = int(toks[pos]); pos += 1
                    vals[pname] = [int(float(x)) for x in toks[pos:pos + n]]; pos += n
                else:
                    vals[pname] = float(toks[pos]); pos += 1
            if name == "vertex":
                verts.append((vals["x"], vals["y"], vals["z"]))
            elif name == "face":
                ids = vals.get("vertex_indices", vals.get("vertex_index", []))
                for j in range(1, len(ids) - 1):
                    tris.append((ids[0], ids[j], ids[j + 1]))
    return verts, tris


def _ply_binary(body, elements, end):
    pos = 0
    verts, tris = [], []
    for name, count, props in elements:
        fixed = all(not isinstance(t, tuple) for _, t in props)
        if fixed:
            fmt = end + "".join(_PLY_T[t] for _, t in props)
            size = struct.calcsize(fmt)
            if name == "vertex":
                names = [n for n, _ in props]
                ix, iy, iz = names.index("x"), names.index("y"), names.index("z")
                if np is not None:
                    dt = np.dtype([(n, end + _PLY_T[t]) for n, t in props])
                    arr = np.frombuffer(body, dtype=dt, count=count, offset=pos)
                    verts = list(zip(arr["x"].astype(float).tolist(), arr["y"].astype(float).tolist(),
                                     arr["z"].astype(float).tolist()))
                else:
                    for i in range(count):
                        v = struct.unpack_from(fmt, body, pos + i * size)
                        verts.append((float(v[ix]), float(v[iy]), float(v[iz])))
            pos += size * count
            continue
        # elements with a list (faces): try the common "3 indices each" layout fast
        if (name == "face" and len(props) == 1 and np is not None):
            ct, it = props[0][1][1], props[0][1][2]
            dt = np.dtype([("n", end + _PLY_T[ct]), ("i", end + _PLY_T[it], 3)])
            if pos + dt.itemsize * count <= len(body):
                arr = np.frombuffer(body, dtype=dt, count=count, offset=pos)
                if (arr["n"] == 3).all():
                    tris = [tuple(t) for t in arr["i"].astype(int).tolist()]
                    pos += dt.itemsize * count
                    continue
        for _ in range(count):
            ids = None
            for pname, ptype in props:
                if isinstance(ptype, tuple):
                    cf = end + _PLY_T[ptype[1]]
                    n = struct.unpack_from(cf, body, pos)[0]; pos += struct.calcsize(cf)
                    itf = end + _PLY_T[ptype[2]] * n
                    vals = struct.unpack_from(itf, body, pos); pos += struct.calcsize(itf)
                    if pname in ("vertex_indices", "vertex_index"):
                        ids = vals
                else:
                    pos += struct.calcsize(end + _PLY_T[ptype])
            if name == "face" and ids:
                for j in range(1, len(ids) - 1):
                    tris.append((ids[0], ids[j], ids[j + 1]))
    return verts, tris
