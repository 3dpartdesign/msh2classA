"""SideLenHint ('too complex', 'bends ...') measures the scan/mesh for the patch: only with FitSrfTo=WholeMesh.
With TempPts / NoScan the patch does not follow the scan/mesh - no hint (it was shown red, misleading).
    python tests/test_hint_mode.py"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerhino as fr
ns, sc = fr.load_script(os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py"))
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)

ns["xf_tuple"] = lambda vp: None
ns["hit_test"] = lambda *a: []


class S(ns["DrawMixin"], ns["SettingsMixin"]):
    def __init__(self, fit):
        self._fit = fit; self.view_hot = None; self.corners = [{"pt": fr.Point3d(0, 0, 0)}] * 4; self.calls = 0; self.cands = []
        self.hint = ("red", None, "too complex", None, None, None)
    def s_fit(self): return self._fit
    def s_sidehint(self): return True
    def on_move_view(self, sender, e): pass
    def pick_px(self, vp): return 1, 1
    def visible_hits(self, h, vp): return []
    def bubble_at(self, m, x, y): return None
    def update_side_hint(self, e): self.calls += 1; self.hint = ("red", None, "too complex", None, None, None)


e = types.SimpleNamespace(Viewport=None, WindowPoint=types.SimpleNamespace(X=10, Y=10))
for fit, name, want in (("Mesh", "WholeMesh", True), ("Points", "TempPts", False), ("NoScan", "NoScan", False)):
    s = S(fit); s.on_move(None, e)
    if want:
        chk("%s: the hint is worked out and shown" % name, s.calls == 1 and s.hint is not None)
    else:
        chk("%s: no hint (also at the last side and while the TempPts points are clicked)" % name,
            s.calls == 0 and s.hint is None)
src = open(os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py")).read()
chk("the legend says why it is off", "off with FitSrfTo=" in src)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
