"""MatchUVN on screen: its seam bubble is the only thing on the seam - no 'Edge' hover label, no seam marker
or CV-count label under it - and the whole bubble takes the click (the area comes from the text drawn).
    python tests/test_match_ui.py"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerhino as fr
ns, sc = fr.load_script(os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py"))
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)

ns["xf_tuple"] = lambda vp: None
ns["to_screen"] = lambda m, p: (p[0], p[1])                 # screen = model x, y (enough here)
P3 = fr.Point3d


class S(ns["DrawMixin"], ns["SmoothMixin"]):
    def __init__(self):
        self.view_hot = None; self.corners = []; self.hover = {"ci": 0}; self.hover_bubble = None
        self.hover_srf = None; self.hint = None
    def on_move_view(self, sender, e): pass


s = S()
s.match_mode = {"oid": None, "loose": 1.0, "hot": None, "centre": P3(500, 300, 0),
                "items": [{"key": ("a", 0, "b", 2), "pos": P3(500, 100, 0), "now": "C0 (kink 2.31 deg)", "want": 2,
                           "src": "not stored - set it"}]}
txt = s._match_text(s.match_mode["items"][0])
w = ns["dot_half_width"](txt)
chk("the seam bubble's click area is as wide as its text", s._match_rects(None)[0][4] == w and w > 100, "%.0f px" % w)
chk("click on its right end: the bubble", s._match_under(None, 500 + w - 3, 104) == ("seam", 0))
chk("click on its left end: the bubble", s._match_under(None, 500 - w + 3, 98) == ("seam", 0))
chk("click beside it: nothing", s._match_under(None, 500 + w + 30, 100) is None)
chk("Apply / Cancel do not overlap", s._match_under(None, 460, 300) == ("apply", None) and
    s._match_under(None, 540, 300) == ("cancel", None))
e = types.SimpleNamespace(Viewport=None, WindowPoint=types.SimpleNamespace(X=500 + w - 5, Y=100))
s.on_move(None, e)
chk("mouse over the seam in MatchUVN: no edge hover ('Edge' label), the bubble is hot",
    s.hover is None and s.match_mode["hot"] == ("seam", 0))
free = s.label_free(None)
chk("a seam marker under the bubble is not drawn", not free(P3(500, 100, 0)) and not free(P3(500 + w, 100, 0)))
chk("... one elsewhere is", free(P3(500, 200, 0)) and free(P3(100, 100, 0)))
s.match_mode = None
chk("no MatchUVN: every label is drawn", s.label_free(None)(P3(500, 100, 0)))
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
