# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Write single-span patches to IGES 5.3 (entity 128, rational B-spline
surface, untrimmed). No CAD kernel needed: Rhino, FreeCAD, CATIA, NX, Alias
... read it as exact NURBS surfaces.

write_iges(path, patches, units="MM", scale=1.0)
    patches: [(pu, pv, P)] with P = CVs, index i*(pv+1)+j (i along u)
    scale  : multiplies every coordinate (model units -> the file units)
"""
from __future__ import print_function, division
import time

from .legal import VERSION

UNITS = {"IN": 1, "MM": 2, "FT": 4, "M": 6, "CM": 10, "UM": 9}


def _real(x):
    s = "%.15G" % float(x)
    if "E" in s:
        m, e = s.split("E")
        if "." not in m:
            m += "."
        return m + "E" + e
    return s if "." in s else s + "."


def _h(s):
    return "%dH%s" % (len(s), s)


def _lines(tokens, width, letter, start, de=None):
    """Join tokens (each already with its ',' or ';') into fixed lines."""
    out = []
    cur = ""
    for tok in tokens:
        while len(tok) > width:                       # very long token (Hollerith): split it
            room = width - len(cur)
            cur += tok[:room]; tok = tok[room:]
            out.append(cur); cur = ""
        if len(cur) + len(tok) > width:
            out.append(cur); cur = ""
        cur += tok
    if cur:
        out.append(cur)
    res = []
    for i, txt in enumerate(out):
        if de is None:
            res.append("%-72s%s%7d" % (txt, letter, start + i))
        else:
            res.append("%-64s %7d%s%7d" % (txt, de, letter, start + i))
    return res


def write_iges(path, patches, units="MM", scale=1.0, author="msh2classA", org="3dPARTdesign"):
    units = units.upper()
    stamp = time.strftime("%Y%m%d.%H%M%S")
    maxc = 0.0
    for (pu, pv, P) in patches:
        for q in P:
            maxc = max(maxc, max(abs(c * scale) for c in q))
    start = ["{} - single-span patches (entity 128). https://3dPARTdesign.com".format(VERSION)]
    g = [_h(","), _h(";"), _h("msh2classA"), _h(path.replace("\\", "/").split("/")[-1]),
         _h(VERSION), _h(VERSION), "32", "38", "6", "308", "15", _h("msh2classA"),
         "1.0", str(UNITS.get(units, 2)), _h(units), "1", "0.01", _h(stamp),
         "1.0E-6", _real(maxc or 1.0), _h(author), _h(org), "11", "0", _h(stamp)]
    g_tokens = [t + "," for t in g[:-1]] + [g[-1] + ";"]

    d_lines = []
    p_lines = []
    for n, (pu, pv, P) in enumerate(patches):
        nu, nv = pu + 1, pv + 1
        de = 2 * n + 1
        par = ["128", str(nu - 1), str(nv - 1), str(pu), str(pv), "0", "0", "1", "0", "0"]
        par += ["0."] * nu + ["1."] * nu
        par += ["0."] * nv + ["1."] * nv
        par += ["1."] * (nu * nv)
        for j in range(nv):                            # IGES: first index (u) runs fastest
            for i in range(nu):
                x, y, z = P[i * nv + j]
                par += [_real(x * scale), _real(y * scale), _real(z * scale)]
        par += ["0.", "1.", "0.", "1."]
        toks = [t + "," for t in par[:-1]] + [par[-1] + ";"]
        first = len(p_lines) + 1
        lines = _lines(toks, 64, "P", first, de)
        p_lines += lines
        label = "PATCH%d" % (n + 1)
        d_lines.append("%8d%8d%8d%8d%8d%8d%8d%8d%8s%s%7d" % (128, first, 0, 0, 0, 0, 0, 0, "00000000", "D", de))
        d_lines.append("%8d%8d%8d%8d%8d%8s%8s%8s%8d%s%7d" % (128, 0, 0, len(lines), 0, "", "", label[:8], 0, "D", de + 1))
    s_text = [t[i:i + 72] for t in start for i in range(0, len(t), 72)]
    s_lines = ["%-72s%s%7d" % (t, "S", i + 1) for i, t in enumerate(s_text)]
    g_lines = _lines(g_tokens, 72, "G", 1)
    t_line = "%-72s%s%7d" % ("S%7dG%7dD%7dP%7d" % (len(s_lines), len(g_lines), len(d_lines), len(p_lines)), "T", 1)
    with open(path, "w") as f:
        f.write("\n".join(s_lines + g_lines + d_lines + p_lines + [t_line]) + "\n")
    return len(patches)
