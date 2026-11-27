"""Sweep sides (patch drawing): the made sides from 2 or 3 drawn ones - geometry only.
    python tests/test_sweep.py"""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.argv = sys.argv[:1]
import test_scan_tools as T
import fakerhino as fr

b = T.Bench(names=("A",))
ns = b.ns
P3 = fr.Point3d
res = []


def chk(name, c, info=""):
    res.append(bool(c)); print(("  [ok] " if c else "  [FAIL] ") + name, info)


class Stub(ns["BuildMixin"], ns["PolygonMixin"]):
    """Only what sweep_complete needs: corners, the drawn sides as point lists."""
    def __init__(self, corners, sides, mirror="YZ"):
        self.reset_polygon()
        self.corners = [{"pt": P3(*c), "on": []} for c in corners]
        self.drawn = sides; self.cands = []; self.tol_on = 1e-6; self._m = mirror
    def s_sweep(self): return True
    def s_mirror(self): return self._m
    def s_mtol(self): return 0.5
    def s_cont(self): return "G2"
    def s_fit(self): return "Mesh"
    def corner(self, pt): return {"pt": pt, "on": []}
    def _side_pts(self, k, n=41):
        if k in self.drawn: return [P3(*self.drawn[k](i / float(n - 1))) for i in range(n)]
        pts = [self.corners[k]["pt"]] + list(self.shape.get(k, [])) + [self.corners[(k + 1) % 4]["pt"]]
        return pts


def arc(c, r, a0, a1, plane):
    """points of a circular arc; plane 'xz' or 'yz' etc."""
    def f(t):
        a = a0 + (a1 - a0) * t; u, v = r * math.cos(a), r * math.sin(a)
        return {"xz": (c[0] + u, c[1], c[2] + v), "yz": (c[0], c[1] + u, c[2] + v),
                "xy": (c[0] + u, c[1] + v, c[2])}[plane]
    return f


def line(a, b):
    return lambda t: tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# 2 sides, straight rail: side 3 = the profile moved along the rail, side 4 = the rail moved
prof = arc((0, 0, 0), 100.0, math.radians(90), 0.0, "yz")        # corner 1 (0,0,100) -> corner 2 (0,100,0)
c0, c1 = prof(0), prof(1)
c2 = (300.0, 100.0, 0.0)
s = Stub([c0, c1, c2], {0: prof, 1: line(c1, c2)}, mirror="None")
s.sweep_complete()
c3 = s.corners[3]["pt"]
chk("2 sides, straight rail: corner 4 = corner 1 moved along the rail", c3.DistanceTo(P3(300, 0, 100)) < 1e-9, str(c3))
side3 = [s.corners[2]["pt"]] + s.shape[2] + [c3]
worst = max(abs(math.hypot(p.Y - 0.0, p.Z - 0.0) - 100.0) + abs(p.X - 300.0) for p in side3)
chk("... side 3 = the profile arc again (radius 100, at x = 300)", worst < 1e-6, "%.2e" % worst)
chk("... side 3 and 4 take the rows of sides 1 and 2", s.side_g.get(2) == s.layout_for_side(0) and
    s.side_g.get(3) == s.layout_for_side(1) and s.sweep_sides == [2, 3])

# 2 sides, curved rail (quarter circle in xy): the profile turns with the rail
rail = arc((0, 0, 0), 500.0, math.radians(-90), 0.0, "xy")           # (0,-500,0) -> (500,0,0)
p0 = (0.0, -500.0, 0.0)
prof2 = line((0.0, -400.0, 0.0), p0)                                  # corner 1 inside the curve -> corner 2
s = Stub([prof2(0), p0, rail(1)], {0: prof2, 1: rail}, mirror="None")
s.sweep_complete()
c3 = s.corners[3]["pt"]
chk("2 sides, curved rail: the profile turned 90 deg with it", c3.DistanceTo(P3(400, 0, 0)) < 1e-3, str(c3))
side4 = [c3] + s.shape[3] + [s.corners[0]["pt"]]
worst = max(abs(math.hypot(p.X, p.Y) - 400.0) for p in side4)
chk("... side 4 = the rail at the distance of corner 1 (radius 400)", worst < 2.0, "%.3f" % worst)

# 2 sides at the mirror: half window. side 1 down the left, side 2 along the bottom to the plane x = 0
left = line((-700.0, 0.0, 300.0), (-700.0, 0.0, 0.0))
bottom = arc((0, -2000.0, 0), 2000.0 + 0.0, math.radians(90 + 20.5), math.radians(90), "xy")   # ends at x = 0
bot = lambda t: (bottom(t)[0], bottom(t)[1], 0.0)
st_ = bot(0)
left = line((st_[0], st_[1], 300.0), st_)
s = Stub([left(0), left(1), bot(1)], {0: left, 1: bot}, mirror="YZ")
s.sweep_complete()
c3 = s.corners[3]["pt"]; side3 = [s.corners[2]["pt"]] + s.shape[2] + [c3]
chk("half window at the mirror: side 3 lies ON the symmetry plane (a mirror side)", max(abs(p.X) for p in side3) < 1e-12
    and s.is_mirror_side(2), "corner 4 %s" % c3)
chk("... corner 4 at the height of corner 1", abs(c3.Z - 300.0) < 1e-6, "%.6f" % c3.Z)

# 3 sides: side 4 = side 2 carried onto corners 4 -> 1
L = line((-600.0, 0.0, 300.0), (-600.0, 0.0, 0.0))
Bt = arc((0, -3000.0, 0), 3000.0, math.radians(90 + 11.5), math.radians(90 - 11.5), "xy")
B = lambda t: (Bt(t)[0], Bt(t)[1], 0.0)
Rt = line(B(1), (B(1)[0], B(1)[1], 300.0))
L = line((B(0)[0], B(0)[1], 300.0), B(0))
s = Stub([L(0), L(1), Rt(0), Rt(1)], {0: L, 1: B, 2: Rt}, mirror="None")
s.sweep_complete()
top = [s.corners[3]["pt"]] + s.shape[3] + [s.corners[0]["pt"]]
worst = max(abs(math.hypot(p.X, p.Y + 3000.0) - 3000.0) + abs(p.Z - 300.0) for p in top)
chk("3 sides: side 4 = the bottom arc again, at the top", worst < 1e-6, "%.2e" % worst)
chk("... with the rows of side 2", s.side_g.get(3) == s.layout_for_side(1) and s.sweep_sides == [3])
print("ALL OK" if all(res) else "SOME FAILED")
sys.exit(0 if all(res) else 1)
