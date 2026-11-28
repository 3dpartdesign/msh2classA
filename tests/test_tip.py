"""The bubble at the cursor (CURSOR_TIP, click_get(gp, tip=...)) - in Align: what the next click is, in the
colour of the scan/mesh to click ON (RED stays, GREEN moves); a real Align run has it at every click and
leaves nothing drawn afterwards.
    python tests/test_tip.py"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.argv = sys.argv[:1]
import test_scan_tools as T
import fakerhino as fr

b = T.Bench(); ns = b.ns
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)

RED, GREEN = ns["ALIGN_STAY_COL"], ns["ALIGN_MOVE_COL"]
st = {"first": None, "fixed": None, "moving": None}
t = ns["align_tip"](st, 1, False)
chk("1st click, nothing chosen: the scan/mesh that STAYS, in RED", "STAYS" in t[0] and t[1] == RED, t[0])
st["fixed"] = "x"; t = ns["align_tip"](st, 2, False)
chk("later 1st clicks: the RED scan/mesh, pair number", "RED" in t[0] and "Pair 2" in t[0] and t[1] == RED, t[0])
st["first"] = object(); t = ns["align_tip"](st, 2, False)
chk("2nd click, mover not chosen: the SAME point on the one that MOVES, in GREEN",
    "SAME" in t[0] and "MOVES" in t[0] and t[1] == GREEN, t[0])
st["moving"] = "y"; t = ns["align_tip"](st, 3, False)
chk("later 2nd clicks: the GREEN scan/mesh", "GREEN" in t[0] and t[1] == GREEN, t[0])

# drawn next to the cursor, in its view only, readable text colour
dots = []
class D(object):
    def DrawDot(self, x, y, txt, c, k): dots.append((x, y, txt, c, k))
class VP(object):
    def __init__(self, i): self.Id = i
gp = fr.GetPoint()
ns["tip_on"](gp, ("Pair 1: point on the scan/mesh that STAYS (turns RED)", RED))
for h in gp.MouseMove.handlers:
    h(None, types.SimpleNamespace(Viewport=VP(1), WindowPoint=types.SimpleNamespace(X=400, Y=300)))
draw = ns["Rhino"].Display.DisplayPipeline.DrawForeground
for h in list(draw.handlers): h(None, types.SimpleNamespace(Viewport=VP(1), Display=D()))
chk("drawn below right of the cursor (never over the point clicked)", dots and dots[-1][0] > 400 and dots[-1][1] > 300,
    str(dots[-1][:2]) if dots else "")
n = len(dots)
for h in list(draw.handlers): h(None, types.SimpleNamespace(Viewport=VP(2), Display=D()))
chk("not in the other views", len(dots) == n)
ns["tip_off"]()
chk("off: its drawing is gone", ns["_tip_draw"] not in draw.handlers)

# at once where the mouse is - not only after it moves
dots.clear()
orig_c = ns["cursor_in_view"]; ns["cursor_in_view"] = lambda: (1, 700.0, 500.0)
gp2 = fr.GetPoint(); ns["tip_on"](gp2, ("Pair 1: point on the scan/mesh that STAYS (turns RED)", RED))
for h in list(draw.handlers): h(None, types.SimpleNamespace(Viewport=VP(1), Display=D()))
chk("the bubble is there at once, at the mouse (no move needed)", dots and dots[-1][0] > 700 and dots[-1][1] > 500,
    str(dots[-1][:2]) if dots else "nothing drawn")
ns["tip_off"](); ns["cursor_in_view"] = orig_c

# a real Align run: the tip at every click, the right colour, nothing left drawn
seen = []
orig = ns["tracked_get"]
def spy(gp, mouse_up=True):
    tp = ns["_TIP"]["tip"]; seen.append(tp() if callable(tp) else tp)
    return orig(gp, mouse_up)
ns["tracked_get"] = spy
b.run("scan_align", T.align_pairs("A", "B", T.AB) + [("enter",)])
ns["tracked_get"] = orig
clicks = [x for x in seen if x]
chk("Align: a bubble at every click", len(clicks) == len(seen) and len(seen) >= 6, "%d of %d" % (len(clicks), len(seen)))
cols = [c for (_, c) in clicks[:6]]
chk("... RED, GREEN, RED, GREEN, ... (stays / moves)", cols == [RED, GREEN] * 3, str(cols))
chk("Align still aligns", b.rel_err("A", "B") < T.TOL)
chk("after Align: no bubble drawn", ns["_TIP"]["tip"] is None and ns["_tip_draw"] not in draw.handlers)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
