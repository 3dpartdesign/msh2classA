"""MeshAlign never hides a scan/mesh itself (a test build before 0.1.3 hid the one you wanted and kept the wrong one RED).
Every visible one takes part; with more than 2 visible and some not prepared yet it ASKS first:
Enter = go on with all, Esc = stop (hide the ones not needed yourself, then MeshAlign again).
    python tests/test_align_ask.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.argv = sys.argv[:1]
import test_scan_tools as T
import fakerhino as fr
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)
vis = lambda b: sorted(n for n, o in b.objs.items() if b.sc.doc.Objects.FindId(o.Id).Visible)

b = T.Bench()                                   # A, B, C visible
n0 = len(fr.SCRIPT.prompts)
b.run("scan_align", [("esc",)], ask_all=False)
asked = [p for p in fr.SCRIPT.prompts[n0:] if "ALL take part" in p]
chk("3 visible, not prepared: it asks first", len(asked) == 1, asked[0][:90] if asked else "")
chk("Esc: MeshAlign does not start, nothing hidden, nothing moved", vis(b) == ["A", "B", "C"] and b.rel_err("A", "B") > 1.0)
b.run("scan_align", [("enter",)] + T.align_pairs("A", "B", T.AB) + [("enter",)], ask_all=False)
chk("Enter: all take part, B aligned to A, nothing hidden", b.rel_err("A", "B") < T.TOL and vis(b) == ["A", "B", "C"])
n0 = len(fr.SCRIPT.prompts)
b.run("scan_align", T.align_pairs("B", "C", T.BC) + [("enter",)], ask_all=False)
chk("next MeshAlign (all prepared now): no question", not [p for p in fr.SCRIPT.prompts[n0:] if "ALL take part" in p])
chk("... C aligned to B - any two of the visible ones, the RED one is the one you click first",
    b.rel_err("B", "C") < T.TOL)
# a hidden one: not asked about, not used, still moves along when it hangs on the moving one
b2 = T.Bench()
b2.sc.doc.Objects.FindId(b2.objs["C"].Id).Visible = False
n0 = len(fr.SCRIPT.prompts)
b2.run("scan_align", T.align_pairs("A", "B", T.AB) + [("enter",)], ask_all=False)
chk("2 visible (C hidden by you): no question", not [p for p in fr.SCRIPT.prompts[n0:] if "ALL take part" in p])
chk("... and C stays hidden as you left it", vis(b2) == ["A", "B"])
src = open(T.SCRIPT).read()
chk("no Objects.Hide in MeshAlign any more", "def align_choose" not in src and "show_again" not in src)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
