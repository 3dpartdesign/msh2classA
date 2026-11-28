"""Rebuild / RebuildAll: a surface that can not take part (several spans, rational, over 8 CV - e.g. 20x20
from another program; not trimmed) -> one clean 6x6 CV patch on it, the same object, deviation printed,
UndoSrfEdit = back. Trimmed ones and NPatches are left. Buttons: Rebuild + Del only on those.
    python tests/test_rebuild.py"""
import io, math, os, sys, uuid, contextlib, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import fakerhino as fr
from core.evaluate import srf_point
from core.bspline import BDir

ns, sc = fr.load_script(os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py"))
rg = ns["rg"]
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)


class Dom(object):
    def __init__(self, a, b): self.Min = a; self.Max = b
    def ParameterAt(self, t): return self.Min + t * (self.Max - self.Min)
class Pts(object):
    def __init__(self, nu, nv): self.CountU = nu; self.CountV = nv; self.cp = {}
    def SetControlPoint(self, i, j, c): self.cp[(i, j)] = c
    def GetControlPoint(self, i, j): return self.cp[(i, j)]
class Srf(object):
    """An 'imported' surface: shape f(u, v), any CV count / spans / rational."""
    def __init__(self, f=None, nu=20, nv=20, spans=(17, 17), rational=False):
        self.f = f; self.Points = Pts(nu, nv); self._sp = spans; self.IsRational = rational
        self.IsValid = True; self.KnotsU = {}; self.KnotsV = {}; self.deg = (3, 3)
    def Domain(self, i): return Dom(0.0, 7.5)
    def SpanCount(self, i): return self._sp[i]
    def Degree(self, i): return self.deg[i]
    def PointAt(self, u, v):
        if self.f is not None: return fr.Point3d(*self.f(u / 7.5, v / 7.5))
        P = [(self.Points.cp[(i, j)].Location.X, self.Points.cp[(i, j)].Location.Y, self.Points.cp[(i, j)].Location.Z)
             for i in range(self.Points.CountU) for j in range(self.Points.CountV)]
        return fr.Point3d(*srf_point(BDir(self.deg[0], 1), BDir(self.deg[1], 1), P, u / 7.5, v / 7.5))
    def ToNurbsSurface(self): return self
    def UnderlyingSurface(self): return self
    def ToBrep(self): return Brp(self)
    @staticmethod
    def Create(dim, rat, ou, ov, nu, nv):
        s = Srf(None, nu, nv, (1, 1)); s.deg = (ou - 1, ov - 1); return s
class Brp(object):
    def __init__(self, s, trimmed=False): self.s = s; self.IsSurface = not trimmed; self.Faces = [self]
    def UnderlyingSurface(self): return self.s
    def Duplicate(self): return Brp(self.s, not self.IsSurface)
class CP(object):
    def __init__(self, p): self.Location = p
rg.NurbsSurface = Srf; rg.ControlPoint = CP; rg.Brep = Brp
class Obj(object):
    def __init__(self, geo): self.Id = uuid.uuid4(); self.Geometry = geo; self.GripsOn = False
objs = {}
sc.doc.Objects.FindId = lambda i: objs.get(i)
def replace(i, g): objs[i].Geometry = g; return True
sc.doc.Objects.Replace = replace
ut = {}
ns["rs"].GetUserText = lambda oid, k=None: ut.get((oid, k))
ns["rs"].SetUserText = lambda oid, k, v: ut.__setitem__((oid, k), v)

f_panel = lambda u, v: (600 * u, 400 * v, 80 * math.sin(math.pi * u) * (0.6 + 0.4 * v) + 30 * v * v)
f_rip = lambda u, v: (600 * u, 400 * v, 80 * math.sin(math.pi * u) + 1.5 * math.sin(9 * math.pi * u) * math.sin(7 * math.pi * v))
def add(f, trimmed=False, npatch=False, **kw):
    o = Obj(Brp(Srf(f, **kw), trimmed)); objs[o.Id] = o
    if npatch: ut[(o.Id, ns["NPATCH_KEY"])] = "4;0.1"
    return o
A = add(f_panel); B = add(f_rip); C = add(f_panel, trimmed=True); N = add(f_panel, trimmed=True, npatch=True)
ok6 = add(f_panel, nu=6, nv=6, spans=(1, 1))


class S(ns["SmoothMixin"]):
    def __init__(self):
        self.smooth_undo = []; self.patches = []; self.flashed = []
        self.mesh = None; self.size = 800.0
        self.refresh()
    def refresh(self):
        self.surfs = []
        for o in objs.values():
            s = o.Geometry.UnderlyingSurface()
            ok = (o.Geometry.IsSurface and s.SpanCount(0) == 1 and s.SpanCount(1) == 1 and not s.IsRational
                  and s.Degree(0) <= 7 and s.Degree(1) <= 7)
            self.surfs.append((o.Id, o.Geometry, ((0, 0, -50), (600, 400, 120)), ok))
    def s_tol(self): return 0.5
    def flash(self, *a): self.flashed.append(a)
    def scan_side_sign(self, ns_): return 1
s = S()
okmap = dict((sid, ok) for (sid, b_, bb, ok) in s.surfs)
chk("buttons: a 20x20 surface gets Rebuild + Del only", s.srf_rows(A.Id, okmap[A.Id]) == ns["SRF_ROWS_REBUILD"])
chk("buttons: a trimmed one and an NPatch get none (Rebuild would bring back what was cut)",
    s.srf_rows(C.Id, False) == () and s.srf_rows(N.Id, False) == ())
chk("buttons: a 6x6 single-span patch keeps all of them", s.srf_rows(ok6.Id, okmap[ok6.Id]) == ns["SRF_ROWS"])

out = io.StringIO()
with contextlib.redirect_stdout(out): s.rebuild_one(A.Id)
txt = out.getvalue(); print(txt.strip())
g = A.Geometry.UnderlyingSurface()
chk("Rebuild: the SAME object, now one clean 6x6 CV span", g.Points.CountU == 6 and g.Points.CountV == 6
    and g.SpanCount(0) == 1 and not g.IsRational)
dev = max(math.dist(T_ := (lambda p: (p.X, p.Y, p.Z))(g.PointAt(7.5 * i / 20.0, 7.5 * j / 20.0)), f_panel(i / 20.0, j / 20.0))
          for i in range(21) for j in range(21))
chk("... on the old surface (smooth panel: under MeshTol)", dev < 0.5, "(%.3f mm)" % dev)
chk("... says the old CV count and the deviation", "20x20 CV" in txt and "off the old surface by" in txt
    and "MORE than MeshTol" not in txt)
s.refresh()
chk("... and takes part now", dict((sid, ok) for (sid, b_, bb, ok) in s.surfs)[A.Id])
s.undo_smooth() if hasattr(s, "undo_smooth") else None
chk("UndoSrfEdit: the old 20x20 surface is back", A.Geometry.UnderlyingSurface().Points.CountU == 20)

out = io.StringIO()
with contextlib.redirect_stdout(out): s.rebuild_all()
txt = out.getvalue(); print(txt.strip())
chk("RebuildAll: both untrimmed 20x20 surfaces, not the trimmed one / NPatch / the 6x6",
    A.Geometry.UnderlyingSurface().Points.CountU == 6 and B.Geometry.UnderlyingSurface().Points.CountU == 6
    and C.Geometry.UnderlyingSurface().Points.CountU == 20 and N.Geometry.UnderlyingSurface().Points.CountU == 20
    and "2 surface(s) rebuilt" in txt)
chk("... the rippled one warned (6x6 can not carry it)", txt.count("MORE than MeshTol") == 1)
chk("... ONE undo step for all", len(s.smooth_undo) == 1 and len(s.smooth_undo[0]) == 2)
s.undo_smooth()
chk("UndoSrfEdit: all back", A.Geometry.UnderlyingSurface().Points.CountU == 20 and
    B.Geometry.UnderlyingSurface().Points.CountU == 20)
chk("RebuildAll is a Surfaces button", ("RebuildAll", "RebuildAll") in ns["SURFACE_TOOLS"])
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
