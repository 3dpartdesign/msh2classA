"""The scan/mesh is found ONLY straight under the patch (scan_pt - the one function of every build,
PullN, the deviation and the start surface): over a hole (a window) the nearest point is its rim,
BESIDE the patch - it must not pull the patch sideways (before 0.1.3: the CVs pulled to the sill).
    python tests/test_under.py"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.argv = sys.argv[:1]
import fakerhino as fr

SCRIPT = os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py")
ns, sc = fr.load_script(SCRIPT)
P3 = fr.Point3d
fails = []


def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)


# a 400 x 400 plate (z = 0, normals up) with a 200 x 200 window in the middle, and the floor of the car
# 300 mm under it (also turned up)
V = []; Tr = []
def quad(a, b, c, d):
    i = len(V); V.extend([a, b, c, d]); Tr.extend([(i, i + 1, i + 2), (i, i + 2, i + 3)])
step = 20.0
for i in range(20):
    for j in range(20):
        x0, y0 = -200 + i * step, -200 + j * step
        if -100 <= x0 < 100 and -100 <= y0 < 100: continue            # the window
        quad((x0, y0, 0), (x0 + step, y0, 0), (x0 + step, y0 + step, 0), (x0, y0 + step, 0))
quad((-300, -300, -300), (300, -300, -300), (300, 300, -300), (-300, 300, -300))
mesh = fr.Mesh(V, Tr)


class S(ns["ScanMixin"]):
    def __init__(self):
        self.mesh = mesh; self.size = 600.0; self.refs = [((0.0, 0.0, 0.0), (0.0, 0.0, 1.0))]
        self.ray_back = 50.0; self.n_wall = self.n_back = self.n_side = 0; self.nsign = None


s = S()
up = (0.0, 0.0, 1.0)
def at(x, y, z, n=up, fit=True): return s.scan_pt(P3(x, y, z), n, fit=fit)

q = at(150, 150, 10)
chk("over the scan/mesh: the point straight under", q is not None and abs(q.X - 150) < 1e-6 and abs(q.Y - 150) < 1e-6
    and abs(q.Z) < 1e-6)
tilt = (math.sin(0.5), 0.0, math.cos(0.5))
q = at(150, 150, 10, tilt)
chk("... also when the patch is tilted 29 deg to it", q is not None and abs(q.Z) < 1e-6)
rim = mesh.ClosestMeshPoint(P3(0, -60, 10), 0.0).Point
chk("(over the window the nearest point IS the rim, beside: %.0f mm away)" % rim.DistanceTo(P3(0, -60, 10)),
    abs(rim.Y + 100) < 1e-6)
chk("over the window (fit): nothing - not pulled to the rim", at(0, -60, 10) is None)
chk("over the window (fit): not the floor 300 mm down either", at(0, 0, 10) is None)
chk("... and counted (the build reports it)", s.n_side >= 2)
q = at(0, -60, 10, fit=False)
chk("start surface / sides (no fit): only along the normal - the floor, never the rim",
    q is None or (abs(q.X) < 1e-6 and abs(q.Y + 60) < 1e-6))
q = at(0, -98, 10)
chk("2 mm past the rim, 10 mm over it: the rim is (nearly) under - used", q is not None and abs(q.Y + 100) < 1e-6)
q = at(0, -60, -0.5)
chk("in the window plane: nothing either", q is None)
# the data of a build (data_fn) and PullN (scan_samples) use scan_pt only
src = open(SCRIPT).read()
chk("the fit data and PullN take the scan/mesh through scan_pt",
    src.count("self.scan_pt(") >= 3 and "def scan_samples" in src)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
