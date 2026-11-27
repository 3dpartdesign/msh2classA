"""Start of msh2classA: DrawPatches / clicking the scan/mesh / BackToPatches after a tool / Esc."""
import sys, io, contextlib
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.argv = ["x"]
import test_scan_tools as T
import fakerhino as fr
res = []
def chk(name, c, info=""):
    res.append(c); print(("  [ok] " if c else "  [FAIL] ") + name, info)
def run(b, fn, answers, *a):
    fr.SCRIPT.q = list(answers)
    out = b.ns[fn](*a)
    left = list(fr.SCRIPT.q)
    return out, left
b = T.Bench(names=("A", "B"))
oB = b.sc.doc.Objects.FindId(b.objs["B"].Id); b.sc.doc.Layers[oB.Attributes.LayerIndex].IsVisible = False
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m, left = run(b, "get_scan", [("opt", "DrawPatches")])
chk("DrawPatches with ONE visible scan/mesh: starts the patches on it", m is not None and not left and
    str(b.ns["get_scan"].obj_id) == str(b.objs["A"].Id), buf.getvalue().strip().splitlines()[-1])
b.sc.doc.Layers[oB.Attributes.LayerIndex].IsVisible = True
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m, left = run(b, "get_scan", [("opt", "DrawPatches"), ("pick", b.objs["B"].Id)])
chk("DrawPatches with two visible: asks which, takes B", m is not None and not left and
    str(b.ns["get_scan"].obj_id) == str(b.objs["B"].Id))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m, left = run(b, "get_scan", [("pick", b.objs["A"].Id)])
chk("clicking the scan/mesh still starts the patches", m is not None and str(b.ns["get_scan"].obj_id) == str(b.objs["A"].Id))
# back from a tool: NOT automatic - a tool first, then BackToPatches
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m, left = run(b, "get_scan", [("opt", "MeshFloor"), ("esc",), ("opt", "BackToPatches")], b.objs["B"].Id)
txt = buf.getvalue()
chk("after a tool: waits; another tool runs; BackToPatches goes back to the same scan/mesh",
    m is not None and not left and str(b.ns["get_scan"].obj_id) == str(b.objs["B"].Id) and "SCAN/MESH TOOL: Floor" in txt,
    "" if m is not None else txt[-600:])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m, left = run(b, "get_scan", [("esc",)], b.objs["B"].Id)
chk("Esc there = end msh2classA", m is None and not left)
print("ALL OK" if all(res) else "SOME FAILED")
sys.exit(0 if all(res) else 1)
