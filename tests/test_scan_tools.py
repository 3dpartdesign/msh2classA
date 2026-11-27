"""msh2classA tests - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign (GPL-3.0, see NOTICE).

Run the REAL mesh tools of the built msh2classA.py (fake Rhino, scripted
clicks) on three overlapping pieces of a symmetric car-like scan, in many
orders, and check the result. Usage: python test_scan_tools.py [script]"""
import json
import math
import os
import random
import shutil
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerhino as fr  # noqa: E402

SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py")
import tempfile  # noqa: E402
WORK = os.path.join(tempfile.gettempdir(), "m2ca_scan_tests")


# ------------------------------------------------------------------ the "car"
def height(x, y):
    """Symmetric in x (the car's centre plane x = 0)."""
    base = 300.0 * math.sqrt(max(0.05, 1.0 - (x / 900.0) ** 2 - (y / 2200.0) ** 2))
    ax = abs(x)
    bumps = (25 * math.exp(-((ax - 450) ** 2 + (y - 300) ** 2) / 20000.0)
             + 18 * math.exp(-((ax - 250) ** 2 + (y + 900) ** 2) / 15000.0)
             + 15 * math.exp(-((ax - 600) ** 2 + (y - 1500) ** 2) / 30000.0)
             + 12 * math.exp(-((ax - 150) ** 2 + (y + 1700) ** 2) / 12000.0)
             # features where the pieces overlap (body lines, as on a real car)
             + 14 * math.exp(-((ax - 330) ** 2 + (y + 420) ** 2) / 6000.0)
             + 10 * math.exp(-((ax - 600) ** 2 + (y + 300) ** 2) / 4000.0)
             + 14 * math.exp(-((ax - 300) ** 2 + (y - 1000) ** 2) / 6000.0)
             + 10 * math.exp(-((ax - 660) ** 2 + (y - 870) ** 2) / 4000.0)
             + 12 * math.exp(-((ax - 420) ** 2 + (y + 600) ** 2) / 5000.0))
    return base + bumps


def normal(x, y, h=1.0):
    fx = (height(x + h, y) - height(x - h, y)) / (2 * h); fy = (height(x, y + h) - height(x, y - h)) / (2 * h)
    n = np.array([-fx, -fy, 1.0]); return n / np.linalg.norm(n)


def piece(y0, y1, step=30.0, scale=1.0):
    xs = np.arange(-900, 900 + 1e-9, step); ys = np.arange(y0, y1 + 1e-9, step)
    V = [(x * scale, y * scale, height(x, y) * scale) for x in xs for y in ys]
    T = []; ny = len(ys)
    for i in range(len(xs) - 1):
        for j in range(ny - 1):
            a = i * ny + j; b = a + ny
            if xs[i] >= 0: T += [(a, b, b + 1), (a, b + 1, a + 1)]
            else: T += [(a, b, a + 1), (b, b + 1, a + 1)]          # mirrored diagonal: the mesh is symmetric
    return fr.Mesh(V, T)


PIECES = {"A": (-2190, -150), "B": (-660, 1260), "C": (750, 2190), "D": (-1200, 300)}   # one grid of 30


def rand_pose(rng, shift, ang_max=20.0):
    ax = fr.Vector3d(*rng.normal(size=3)); ang = math.radians(rng.uniform(-ang_max, ang_max))
    return fr.Transform.Translation(fr.Vector3d(*shift)) * fr.Transform.Rotation(ang, ax, fr.Point3d(0, 0, 0))


# ------------------------------------------------------------------ test bench
class Bench(object):
    def __init__(self, unit="Millimeters", seed=1, names=("A", "B", "C"), shifts=None):
        self.ns, self.sc = fr.load_script(SCRIPT, unit)
        self.unit = unit
        self.k = 1.0 / fr.UNIT_MM[unit]                     # model units per mm (geometry is made in mm)
        self.rng = np.random.RandomState(seed)
        self.objs = {}
        if os.path.isdir(WORK): shutil.rmtree(WORK)
        os.makedirs(os.path.join(WORK, "scans"))
        shifts = shifts or {"A": (0, 0, 0), "B": (2600, 400, 150), "C": (-2600, 900, -80), "D": (0, 0, 2500)}
        for n in names:
            self.add(n, shifts[n])
        fr.SCRIPT.q = []; fr.SCRIPT.prompts = []
        fr.SCRIPT.resolver = self.resolve
        self.out = []

    def add(self, n, shift):
        ns, sc = self.ns, self.sc
        y0, y1 = PIECES[n]
        m = piece(y0, y1, scale=self.k)
        path = os.path.join(WORK, "scans", n + ".stl")
        open(path, "wb").write(("scan %s %d" % (n, len(m.V))).encode())
        li = sc.doc.Layers.add(ns["SCAN_ROOT"] + "::" + n)
        idef = fr.InstanceDefinition(n, [m], len(sc.doc.InstanceDefinitions)); sc.doc.InstanceDefinitions.append(idef)
        a = fr.ObjectAttributes(); a.LayerIndex = li; a.ColorSource = "object"; a.ObjectColor = (100, 100, 100)
        a.SetUserString(ns["SCAN_KEY"], path)
        sh = [c * self.k for c in shift]
        o = fr.InstanceObject(idef, rand_pose(self.rng, sh), a)
        sc.doc.Objects.items[o.Id] = o
        self.objs[n] = o
        return o

    # world position of a point given in the TRUE (mm) frame on piece n
    def world(self, n, p):
        o = self.sc.doc.Objects.FindId(self.objs[n].Id)
        q = fr.Point3d(*[c * self.k for c in p]); q.Transform(o.InstanceXform); return q

    def resolve(self, act):
        """('click_on', piece, (x, y)) -> ray. Like a user turning the view: a direction
        where that piece is the front-most mesh at that point (else the normal).
        ('click_photo', layer name, (x, y)): a point of the picture, x / y in the frame's own units."""
        if act[0] == "click_photo":
            o = [v for v in self.sc.doc.Objects.items.values() if isinstance(v, fr.PictureObject) and
                 self.sc.doc.Layers[v.Attributes.LayerIndex].Name == act[1]][0]
            w, h = [float(t) for t in o.Attributes.GetUserString(self.ns["PHOTO_SIZE_KEY"]).split()]
            g = o.Geometry; u, v = act[2][0] / w, act[2][1] / h
            p = g.PointAt(u, v)
            nv = fr.Vector3d(*np.cross((g.X - g.O)._a(), (g.Y - g.O)._a())); nv.Unitize()
            L = 9000.0 * self.k
            return p + nv * L, p - nv * L
        _, n, (x, y) = act
        target = self.world(n, (x, y, height(x, y)))
        o = self.sc.doc.Objects.FindId(self.objs[n].Id)
        nl = fr.Vector3d(*normal(x, y)); nl.Transform(o.InstanceXform)
        insts = [v for v in self.sc.doc.Objects.items.values()]
        dirs = [nl, -nl, fr.Vector3d(0, 0, 1), fr.Vector3d(0, 0, -1), fr.Vector3d(1, 0, 0), fr.Vector3d(-1, 0, 0),
                fr.Vector3d(0, 1, 0), fr.Vector3d(0, -1, 0)]
        L = 3000.0 * self.k
        for d in dirs:
            d = fr.Vector3d(d); d.Unitize()
            a = target + d * L
            hit = self.ns["ray_hit_scans"](a, target - a, insts)
            if hit and hit[1].Id == o.Id and hit[0].DistanceTo(target) < 0.5 * self.k:
                return a, target - d * L
        d = fr.Vector3d(nl); d.Unitize()
        return target + d * L, target - d * L

    def run(self, fn, answers):
        fr.SCRIPT.q = list(answers)
        try:
            self.ns[fn]()
        finally:
            self.ns["_ScanDraw"].off_all()
        if fr.SCRIPT.q:
            raise AssertionError("%s did not use all scripted answers, left: %s; last prompt: %s" %
                                 (fn, fr.SCRIPT.q[:3], fr.SCRIPT.prompts[-1] if fr.SCRIPT.prompts else "-"))

    # ---- measurements (in mm) ----
    def xf(self, n): return self.sc.doc.Objects.FindId(self.objs[n].Id).InstanceXform

    def rel_err(self, a, b):
        """How far piece b sits from where it belongs relative to piece a (mm)."""
        ok, ia = self.xf(a).TryGetInverse()
        E = ia * self.xf(b); worst = 0.0
        for p in ((-900, -2190, 0), (900, 2190, 300), (900, -2190, 0), (-900, 2190, 300), (0, 0, 150)):
            q = fr.Point3d(*[c * self.k for c in p]); r = fr.Point3d(q); r.Transform(E)
            worst = max(worst, q.DistanceTo(r))
        return worst / self.k

    def sym_err(self, n="A"):
        worst = 0.0
        for (x, y) in ((390, -1200), (690, -390), (300, -1890), (210, -810)):
            if not (PIECES[n][0] <= y <= PIECES[n][1]): continue
            a = self.world(n, (x, y, height(x, y))); b = self.world(n, (-x, y, height(-x, y)))
            worst = max(worst, math.sqrt((a.X + b.X) ** 2 + (a.Y - b.Y) ** 2 + (a.Z - b.Z) ** 2))
        return worst / self.k

    FLOOR = [(870, -1980), (-870, -1980), (870, -390), (-870, -390)]

    def floor_err(self):
        zs = [self.world("A", (x, y, height(x, y))).Z for (x, y) in self.FLOOR]
        return (max(zs) - min(zs)) / self.k, sum(zs) / len(zs) / self.k

    def snapshot(self): return dict((n, fr.Transform(self.xf(n))) for n in self.objs)

    def same_as(self, snap):
        worst = 0.0
        for n, X in snap.items():
            worst = max(worst, float(np.abs(self.xf(n).M - X.M).max()) / self.k)
        return worst

    def perturb_all(self, seed=7):
        rng = np.random.RandomState(seed); G = rand_pose(rng, (rng.uniform(-500, 500) * self.k, 0, 0), 30)
        for n in self.objs:
            o = self.sc.doc.Objects.FindId(self.objs[n].Id); o.InstanceXform = G * o.InstanceXform
        self.ns["remember_positions"]()             # a test move, not a hand move

    def setups(self):
        return json.load(open(os.path.join(WORK, "scans", "scan_setups.json")))


# ------------------------------------------------------------------ scripted steps
def align_pairs(stay, move, pts, noise=0.0, rng=None):
    out = []
    for (x, y) in pts:
        out.append(("click_on", stay, (x, y)))
        dx, dy = (rng.uniform(-noise, noise, 2) if noise else (0, 0))
        out.append(("click_on", move, (x + dx, y + dy)))
    return out


AB = [(-600, -480), (510, -300), (0, -450), (690, -600)]
BC = [(-510, 900), (600, 1110), (90, 810), (-690, 1200)]
AD = [(-600, -990), (510, -300), (0, -690), (690, -1110)]     # inside A AND D
CENTER = [("click_on", "A", (390, -1200)), ("click_on", "A", (-390, -1200)),
          ("click_on", "A", (690, -390)), ("click_on", "A", (-690, -390)),
          ("click_on", "A", (300, -1890)), ("click_on", "A", (-300, -1890))]
FLOOR = [("click_on", "A", p) for p in Bench.FLOOR]

TOL = 0.01            # mm, exact clicks
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond), info))
    print("  [{}] {} {}".format("ok" if cond else "FAIL", name, info))


def scenario(title, fn):
    print("\n== " + title)
    try:
        fn()
    except Exception:
        traceback.print_exc()
        check(title + ": ran without error", False, traceback.format_exc().splitlines()[-1])


# ------------------------------------------------------------------ scenarios
def s_align_center_floor_setup(unit="Millimeters"):
    def f():
        b = Bench(unit)
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        check("Align B to A", b.rel_err("A", "B") < TOL, "%.2e mm" % b.rel_err("A", "B"))
        b.run("scan_align", align_pairs("B", "C", BC) + [("enter",)])
        check("Align C to B", b.rel_err("B", "C") < TOL, "%.2e mm" % b.rel_err("B", "C"))
        check("A-C chain", b.rel_err("A", "C") < TOL)
        b.run("scan_center", CENTER + [("enter",), ("string", "centred")])
        check("Center: symmetric", b.sym_err() < TOL, "%.2e mm" % b.sym_err())
        check("Center keeps the alignment", max(b.rel_err("A", "B"), b.rel_err("A", "C")) < TOL)
        snap_c = b.snapshot()
        b.run("scan_floor", FLOOR + [("enter",), ("enter",), ("opt", "DontSave")])
        fe = b.floor_err()
        check("Floor: level", fe[0] < TOL, "spread %.2e mm" % fe[0])
        check("Floor: at 0", abs(fe[1]) < TOL, "z %.2e mm" % fe[1])
        check("Floor keeps symmetry", b.sym_err() < TOL, "%.2e mm" % b.sym_err())
        check("Floor keeps the alignment", max(b.rel_err("A", "B"), b.rel_err("A", "C")) < TOL)
        st = b.setups()
        check("setup has units", st["centred"].get("_units") == {"Millimeters": "mm", "Inches": "in"}[unit],
              str(st["centred"].get("_units")))
        b.perturb_all()
        b.run("scan_setup", [("list", "centred")])
        check("MeshSetup brings the saved positions back", b.same_as(snap_c) < 1e-6, "%.2e" % b.same_as(snap_c))
    return f


def s_center_first():
    def f():
        b = Bench()
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        check("Center (A only aligned to nothing yet)", b.sym_err() < TOL)
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_align", align_pairs("B", "C", BC) + [("enter",)])
        check("Align after Center keeps A symmetric", b.sym_err() < TOL)
        check("Align after Center: B, C right", max(b.rel_err("A", "B"), b.rel_err("A", "C")) < TOL)
        b.run("scan_floor", FLOOR + [("enter",), ("enter",), ("opt", "DontSave")])
        check("Floor last: level + symmetric", b.floor_err()[0] < TOL and b.sym_err() < TOL)
    return f


def s_floor_before_center(unit="Millimeters"):
    """Floor -> Center must give EXACTLY what Center -> Floor gives (Center levels the floor again by itself)."""
    def f():
        ref = [("click_on", "A", (0, -1500))]
        b = Bench(unit)
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_floor", FLOOR + [("enter",)] + ref + [("opt", "DontSave")])
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        fe = b.floor_err()
        check("Floor -> Center: symmetric", b.sym_err() < TOL, "%.2e mm" % b.sym_err())
        check("Floor -> Center: floor still level and at 0 (no Floor again)", fe[0] < TOL and abs(fe[1]) < TOL,
              "spread %.2e, z %.2e mm" % fe)
        y0 = b.world("A", (0, -1500, height(0, -1500))).Y / b.k
        check("Floor -> Center: front/back 0 point kept", abs(y0) < TOL, "%.2e mm" % y0)
        check("Floor -> Center keeps the alignment", b.rel_err("A", "B") < TOL)
        b2 = Bench(unit)
        b2.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b2.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        b2.run("scan_floor", FLOOR + [("enter",)] + ref + [("opt", "DontSave")])
        d = max(float(np.abs(b.xf(n).M - b2.xf(n).M).max()) / b.k for n in ("A", "B"))
        check("Floor -> Center == Center -> Floor (same place)", d < 1e-6, "%.2e" % d)
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        check("Center twice: still level + symmetric", b.floor_err()[0] < TOL and b.sym_err() < TOL)
    return f


def s_floor_other_symplane():
    def f():
        b = Bench()
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        snap = b.snapshot()
        b.run("scan_floor", [("opt", "SymPlane", 1)] + FLOOR + [("enter",)])
        check("Floor with another SymPlane than Center is refused, nothing moved", b.same_as(snap) < 1e-9)
        b.run("scan_floor", [("opt", "SymPlane", 0)] + FLOOR + [("enter",), ("enter",), ("opt", "DontSave")])
        check("Floor with the Center SymPlane works", b.floor_err()[0] < TOL and b.sym_err() < TOL)
    return f


def frame(b):
    s_ = b.sc.doc.Strings.GetValue(b.ns["FRAME_KEY"])
    return json.loads(s_) if s_ else {}


def s_align_guard():
    """A centred scan must never be moved onto one that is not."""
    def f():
        b = Bench(names=("A", "B"))
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        b.run("scan_floor", FLOOR + [("enter",), ("enter",), ("string", "car")])
        check("Center / Floor mark A and B (B is aligned to A)", set(frame(b).get("members", [])) == {"A", "B"},
              str(frame(b).get("members")))
        check("... the core (clicked on) is A only", set(frame(b).get("core", [])) == {"A"}, str(frame(b).get("core")))
        b.add("D", (0, 0, 2500))
        snap = b.snapshot()
        # D first (stays), then A (would move A + B away from the centre): refused
        b.run("scan_align", [("click_on", "D", (-600, -990)), ("click_on", "A", (-600, -990)), ("esc",)])
        check("Align: the centred A onto the new D is REFUSED, nothing moved", b.same_as(snap) < 1e-9)
        b.run("scan_align", [("click_on", "D", (-600, -990)), ("click_on", "B", (-510, 900)), ("esc",)])
        check("Align: B (aligned to the centred A) onto D is REFUSED too", b.same_as(snap) < 1e-9)
        # the right way: A stays, D moves
        b.run("scan_align", [("click_on", "D", (-600, -990)), ("click_on", "A", (-600, -990)),
                             ("opt", "UndoPair")] + align_pairs("A", "D", AD) + [("enter",)])
        check("Align the right way: D onto A", b.rel_err("A", "D") < TOL)
        check("centre and floor untouched", b.sym_err() < TOL and b.floor_err()[0] < TOL)
        check("D joins the centred set", "D" in frame(b).get("members", []))
        check("D joins the centred set in the saved setup too", "D" in b.setups()["car"]["_frame"]["members"])
        snap = b.snapshot()
        b.run("scan_align", [("click_on", "A", (-600, -990)), ("click_on", "D", (-600, -990)), ("esc",)])
        check("Esc after a pair inside the centred set: back", b.same_as(snap) < 1e-9)
        # setups carry the marks: a setup of the raw scans clears them, 'car' brings them back
        b.perturb_all(3)
        b.run("scan_setup", [("list", "car")])
        check("MeshSetup 'car': positions back", b.same_as(snap) < 1e-6)
        check("MeshSetup 'car': marks back (floor points too)", set(frame(b).get("members", [])) >= {"A", "B", "D"}
              and frame(b).get("floor") is not None)
    return f


def nudge(b, n, ang_deg, shift_mm, seed):
    """Knock piece n a little (as after rough hand pairs): a turn about its middle + a shift."""
    rng = np.random.RandomState(seed)
    y = 0.5 * sum(PIECES[n]); c = b.world(n, (0, y, height(0, y)))
    ax = fr.Vector3d(*rng.normal(size=3)); sh = rng.normal(size=3); sh = sh / np.linalg.norm(sh) * shift_mm * b.k
    G = fr.Transform.Translation(fr.Vector3d(*sh)) * fr.Transform.Rotation(math.radians(ang_deg), ax, c)
    o = b.sc.doc.Objects.FindId(b.objs[n].Id); o.InstanceXform = G * o.InstanceXform
    b.ns["remember_positions"]()


def s_autoalign():
    """Many at once: B and C knocked off, pulled back onto the centred A (which stays) and onto each other."""
    def f():
        b = Bench()
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_align", align_pairs("B", "C", BC) + [("enter",)])
        check("Align leaves markers (8 pairs)", len(json.loads(b.sc.doc.Strings.GetValue(b.ns["MARKER_KEY"]))) == 8)
        b.run("scan_center", CENTER + [("enter",), ("string", "front")])
        check("setup keeps the markers", len(b.setups()["front"].get("_markers", [])) == 8)
        xa = fr.Transform(b.xf("A"))
        nudge(b, "B", 0.6, 5.0, 1); nudge(b, "C", 0.8, 7.0, 2)
        e0 = (b.rel_err("A", "B"), b.rel_err("B", "C"))
        b.run("scan_autoalign", [("multi", ["B", "C"]), ("multi", ["A"]), ("opt", "AutoFitRadius", 150.0),
                                 ("opt", "Fit"), ("enter",)])
        e1 = (b.rel_err("A", "B"), b.rel_err("B", "C"))
        check("AutoAlign B + C at once (knocked %.1f / %.1f mm off)" % e0, max(e1) < 0.3, "%.3f / %.3f mm after" % e1)
        check("AutoAlign: A (stays, centred) did not move", float(np.abs(b.xf("A").M - xa.M).max()) < 1e-12)
        check("AutoAlign: centre kept", b.sym_err() < TOL)
        snap = b.snapshot(); b.perturb_all(4)
        b.run("scan_setup", [("list", "front")])
        check("setup 'front' has the AutoAlign result", b.same_as(snap) < 1e-6, "%.2e" % b.same_as(snap))
    return f


def s_autoalign_guard_and_clicks():
    def f():
        b = Bench(names=("A", "B"))
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        b.add("D", (0, 0, 2500))
        b.run("scan_align", align_pairs("A", "D", AD[:3]) + [("enter",)])          # rough: 3 pairs
        snap = b.snapshot()
        b.run("scan_autoalign", [("multi", ["A"]), ("multi", ["D"])])
        check("AutoAlign: A (Center clicked on it) moving onto D is REFUSED, nothing moved", b.same_as(snap) < 1e-12)
        b.run("scan_align", [("click_on", "D", (-600, -990)), ("click_on", "A", (-600, -990)), ("esc",)])
        check("Align: A onto D (D aligned to A, joined the set) REFUSED too", b.same_as(snap) < 1e-12)
        nudge(b, "D", 0.5, 6.0, 3)
        e0 = b.rel_err("A", "D")
        extra = [("click_on", "A", (690, -1110)), ("click_on", "A", (-300, -900))]
        b.run("scan_autoalign", [("multi", ["D"]), ("multi", ["A", "B"])] + extra +
              [("opt", "AutoFitRadius", 150.0), ("opt", "Fit"), ("opt", "Fit"), ("enter",)])
        check("AutoAlign D onto A + B with clicked markers (%.1f mm off)" % e0, b.rel_err("A", "D") < 0.3,
              "%.3f mm after" % b.rel_err("A", "D"))
        check("AutoAlign: A, B untouched, centre kept", b.rel_err("A", "B") < TOL and b.sym_err() < TOL)
        check("D in the centred set after AutoAlign", "D" in frame(b).get("members", []))
        snap = b.snapshot()
        nudge(b, "D", 0.5, 6.0, 5)
        snap2 = b.snapshot()
        b.run("scan_autoalign", [("multi", ["D"]), ("multi", ["A"]), ("opt", "Fit"), ("esc",)])
        check("AutoAlign Esc after Fit: back", b.same_as(snap2) < 1e-9)
    return f


def s_setups_front_rear():
    """Center front -> setup 'front'; more scans aligned; a second (rear) Center -> 'rear'; switch back and forth."""
    def f():
        b = Bench()
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("string", "front")])
        b.run("scan_align", align_pairs("B", "C", BC) + [("enter",)])           # C added after the front setup
        rng = np.random.RandomState(9)
        rear = [(a, n, (x + rng.uniform(-6, 6), y)) for (a, n, (x, y)) in CENTER]   # another, local centre
        b.run("scan_center", rear + [("enter",), ("string", "rear")])
        snapR = b.snapshot()
        b.run("scan_setup", [("list", "front")]); snapF = b.snapshot()
        check("'front' after a later Align + rear Center: still centred", b.sym_err() < TOL)
        check("'front' knows C (aligned after it was saved)", b.rel_err("B", "C") < TOL)
        check("'front' and 'rear' differ", b.same_as(snapR) > 0.01)
        for k in range(3):
            b.run("scan_setup", [("list", "rear")]); r_ = b.same_as(snapR)
            b.run("scan_setup", [("list", "front")]); f_ = b.same_as(snapF)
        check("switch front <-> rear 3x: exact", r_ < 1e-6 and f_ < 1e-6, "%.1e / %.1e" % (r_, f_))
        check("alignment the same in both", b.rel_err("A", "B") < TOL and b.rel_err("A", "C") < TOL)
        b.run("scan_setup", [("list", "rear")])
        check("alignment the same in 'rear'", b.rel_err("A", "B") < TOL and b.rel_err("A", "C") < TOL)
    return f


def resize(b, n, f, about=(0, -400, 100)):
    """Piece n gets the wrong SIZE (an AI / photogrammetry mesh)."""
    o = b.sc.doc.Objects.FindId(b.objs[n].Id)
    c = fr.Point3d(*[v * b.k for v in about]); c.Transform(o.InstanceXform)
    o.InstanceXform = fr.Transform.Scale(c, f) * o.InstanceXform
    b.ns["remember_positions"]()


def rel_size(b, a, c):
    ok, ia = b.xf(a).TryGetInverse()
    return b.ns["xf_scale"](ia * b.xf(c))


def s_align_scale():
    """Two AI meshes of the same car, one 8 % too big: Align with Scale (pairs, Fit, AutoAlign)."""
    def f():
        b = Bench(names=("A", "B"))
        resize(b, "B", 1.08)
        b.run("scan_align", [("opt", "Scale")] + align_pairs("A", "B", AB) + [("enter",)])
        check("Align Scale=On, exact pairs: B on A, size too", b.rel_err("A", "B") < TOL and
              abs(rel_size(b, "A", "B") - 1.0) < 1e-9, "%.2e mm, size %.9f" % (b.rel_err("A", "B"), rel_size(b, "A", "B")))
        b2 = Bench(names=("A", "B"))
        resize(b2, "B", 0.93)
        rng = np.random.RandomState(4)
        b2.run("scan_align", [("opt", "Scale")] + align_pairs("A", "B", AB, noise=8.0, rng=rng) +
               [("opt", "AutoFitRadius", 150.0), ("opt", "Fit"), ("enter",)])
        check("Align Scale=On, rough pairs + Fit", b2.rel_err("A", "B") < 0.05 and abs(rel_size(b2, "A", "B") - 1) < 1e-4,
              "%.4f mm, size %.6f" % (b2.rel_err("A", "B"), rel_size(b2, "A", "B")))
        b3 = Bench(names=("A", "B"))
        b3.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        check("Align Scale=Off (default): size untouched", abs(b3.ns["xf_scale"](b3.xf("B")) - 1.0) < 1e-12)
        resize(b3, "B", 1.04); nudge(b3, "B", 0.4, 4.0, 6)
        b3.run("scan_autoalign", [("multi", ["B"]), ("multi", ["A"]), ("opt", "Scale"), ("opt", "AutoFitRadius", 150.0),
                                  ("opt", "Fit"), ("opt", "Fit"), ("enter",)])
        check("AutoAlign Scale=On: B back on A, size too", b3.rel_err("A", "B") < 0.05 and abs(rel_size(b3, "A", "B") - 1) < 1e-4,
              "%.4f mm, size %.6f" % (b3.rel_err("A", "B"), rel_size(b3, "A", "B")))
        snap = b.snapshot()
        b.run("scan_setup", [("list", "<save the present positions as ...>"), ("string", "s")])
        b.perturb_all(3); b.run("scan_setup", [("list", "s")])
        check("setup keeps the size", b.same_as(snap) < 1e-9, "%.2e" % b.same_as(snap))
    return f


def lamp_curve(b, n="A"):
    """A curve drawn on piece n over the bonnet, lamp to lamp; -> (object id, its TRUE length)."""
    true = [(x, -1800.0, height(x, -1800.0)) for x in range(-660, 661, 60)]
    L = sum(math.dist(true[i], true[i + 1]) for i in range(len(true) - 1))
    pts = [b.world(n, p) for p in true]
    return b.sc.doc.Objects.AddCurve(fr.Curve(pts)), L


def s_mesh_scale():
    def f():
        for via_center in (False, True):
            b = Bench(names=("A", "B"))
            for n in ("A", "B"): resize(b, n, 0.8, about=(0, 0, 0))            # the whole AI car 20 % small
            b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
            if via_center:
                b.run("scan_floor", FLOOR + [("enter",), ("enter",), ("opt", "DontSave")])
                cid, L = lamp_curve(b)
                b.run("scan_center", CENTER + [("opt", "ScaleByCurve"), ("pick", cid), ("number", L),
                                               ("string", "ai")])
            else:
                b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
                b.run("scan_floor", FLOOR + [("enter",), ("enter",), ("opt", "DontSave")])
                cid, L = lamp_curve(b)
                b.run("scan_scale", [("pick", cid), ("number", L), ("string", "ai")])
            how = " (Center option ScaleByCurve)" if via_center else ""
            sz = b.ns["xf_scale"](b.xf("A"))
            check("MeshScale%s: true size" % how, abs(sz - 1.0) < 1e-9, "size %.9f" % sz)
            check("MeshScale%s: centre, floor, alignment kept" % how, b.sym_err() < TOL and b.floor_err()[0] < TOL and
                  abs(b.floor_err()[1]) < TOL and b.rel_err("A", "B") < TOL)
            crv = b.sc.doc.Objects.FindId(cid).curve
            check("MeshScale%s: the curve scaled too, true length" % how, abs(crv.GetLength() - L) < 1e-6 * L)
            snap = b.snapshot(); b.perturb_all(2); b.run("scan_setup", [("list", "ai")])
            check("MeshScale%s: setup 'ai' keeps the size" % how, b.same_as(snap) < 1e-9)
    return f


def by_hand(b, names, G):
    """A move made by the user, outside the script (NOT remembered)."""
    for n in names:
        o = b.sc.doc.Objects.FindId(b.objs[n].Id); o.InstanceXform = G * o.InstanceXform


def s_hand_moves():
    def f():
        b = Bench(names=("A", "B", "C"))
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_align", align_pairs("B", "C", BC) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("string", "front")])
        small = rand_pose(np.random.RandomState(1), (2.0 * b.k, 1.0 * b.k, 0), 0.3)
        by_hand(b, ["B", "C"], small)                      # B (+ C on it) nudged by hand
        snap = b.snapshot()
        b.run("scan_setup", [("opt", "Keep"), ("list", "front")])
        check("hand move + Keep: setup 'front' follows (B, C at the new place against A)", b.same_as(snap) < 1e-6,
              "%.2e" % b.same_as(snap))
        check("hand move of a non-core scan/mesh: Center marks kept", "center" in frame(b))
        by_hand(b, ["B"], small)
        b.run("scan_setup", [("opt", "KeepOnly"), ("list", "front")])
        check("hand move + KeepOnly: the setup is as it was", b.same_as(snap) < 1e-6)
        by_hand(b, ["C"], small)
        b.run("scan_align", [("opt", "Back"), ("esc",)])
        check("hand move + Back: put back", b.same_as(snap) < 1e-9)
        by_hand(b, ["C"], small)
        b.run("scan_center", [("esc",)])
        moved = b.snapshot()
        check("hand move + Esc: tool not started, C stays moved", b.same_as(snap) > 0.1 and b.sc.doc.Strings is not None)
        b.run("scan_center", [("opt", "Back"), ("esc",)])
        check("... asked again next time (Back)", b.same_as(snap) < 1e-9)
        # the whole set moved together
        by_hand(b, ["A", "B", "C"], fr.Transform.Translation(fr.Vector3d(0, 300 * b.k, 0)))
        b.run("scan_setup", [("enter",), ("list", None)])
        check("whole set moved along the symmetry plane: Center marks kept", "center" in frame(b))
        by_hand(b, ["A", "B", "C"], fr.Transform.Rotation(math.radians(2), fr.Vector3d(0, 0, 1), fr.Point3d(0, 0, 0)))
        b.run("scan_setup", [("enter",), ("list", None)])
        check("whole set turned off the plane: Center marks dropped", "center" not in frame(b))
        b.run("scan_setup", [("list", "front")])
        check("MeshSetup 'front' brings the centre + marks back", b.sym_err() < TOL and "center" in frame(b))
        by_hand(b, ["A"], small)
        b.run("scan_setup", [("opt", "Keep"), ("list", None)])
        check("hand move of the core (Center was clicked on A): Center marks dropped", "center" not in frame(b))
        b.run("scan_floor", [("esc",)])
        check("no question when nothing moved by hand", True)
    return f


def png(path, w, h):
    """A PNG file with just a header (the size is all LoadPhotos reads)."""
    import struct
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00")


def s_photos():
    """Photos: a side photo fitted onto the scans/meshes, the loose rear part put in its place from it; a front photo
    centred, levelled and scaled by a known distance; setups keep the photos."""
    def f():
        b = Bench(names=("A", "B", "C"))
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])
        b.run("scan_floor", FLOOR + [("enter",), ("enter",), ("opt", "DontSave")])
        # C (rear, scanned apart): right across the car, wrong up/down, front/back and pitch
        oC = b.sc.doc.Objects.FindId(b.objs["C"].Id)
        cpt = b.world("A", (0, 1500, height(0, 1500)))
        G = fr.Transform.Translation(fr.Vector3d(0, 180 * b.k, -90 * b.k)) * \
            fr.Transform.Rotation(math.radians(3.0), fr.Vector3d(1, 0, 0), cpt)
        oC.InstanceXform = G * fr.Transform(b.xf("A")); b.ns["remember_positions"]()
        side = os.path.join(WORK, "side.png"); png(side, 4000, 1500)
        front = os.path.join(WORK, "front.png"); png(front, 2000, 1200)
        b.run("load_photos", [("files", [side, front]), ("list", "Right"), ("list", "Front")])
        frames = [v for v in b.sc.doc.Objects.items.values() if isinstance(v, fr.PictureObject)]
        check("LoadPhotos: two picture frames on their layers", len(frames) == 2)
        # what the side photo SHOWS: the true car (y, z) -> picture point, 0.9 x, tilted 2 deg, shifted
        al = math.radians(2.0)
        def side_px(y, z):
            return (2500 + 0.9 * (y * math.cos(al) - z * math.sin(al)), 300 + 0.9 * (y * math.sin(al) + z * math.cos(al)))
        def on_side(x, y):                            # click the photo where it shows the true point (x, y) of A
            return ("click_photo", "side", side_px(y, height(x, y)))
        pairs = []
        for (x, y) in ((600, -1800), (-300, -390), (510, 900)):        # on mesh vertices (30 mm grid): exact
            pairs += [on_side(x, y), ("click_on", "A" if y < -150 else "B", (x, y))]
        b.run("photo_tools", [("opt", "PhotoToMesh")] + pairs + [("enter",)])
        # the side photo now shows every true point where it is in the world (seen from the side: y, z)
        worst = 0.0
        ph = [v for v in frames if b.sc.doc.Layers[v.Attributes.LayerIndex].Name == "side"][0]
        w, h = [float(t) for t in ph.Attributes.GetUserString(b.ns["PHOTO_SIZE_KEY"]).split()]
        for (x, y) in ((0, -2000), (700, 1100), (-800, 300)):
            u, v = side_px(y, height(x, y)); p = ph.Geometry.PointAt(u / w, v / h)
            q = b.world("A", (x, y, height(x, y)))
            worst = max(worst, abs(p.Y - q.Y), abs(p.Z - q.Z))
        check("PhotoToMesh: the side photo sits on the car (scaled, turned, moved in its plane)", worst / b.k < 1e-6,
              "%.2e mm" % (worst / b.k))
        # the loose rear part C onto the photo (MeshToPhoto)
        mp = []
        for (x, y) in ((600, 1500), (-510, 2010), (300, 900)):
            mp += [("click_on", "C", (x, y)), ("click_photo", "side", side_px(y, height(x, y)))]
        b.run("photo_tools", [("opt", "MeshToPhoto")] + mp + [("enter",)])
        check("MeshToPhoto: the rear part in its place from the side photo", b.rel_err("A", "C") < TOL,
              "%.4f mm" % b.rel_err("A", "C"))
        snap = b.snapshot()
        b.run("photo_tools", [("opt", "MeshToPhoto"), ("click_on", "A", (600, -1800)), ("click_photo", "side", (100, 100)),
                              ("enter",)])
        check("MeshToPhoto on the centred A: REFUSED, nothing moved", b.same_as(snap) < 1e-12)
        # the front photo: centred, levelled, scaled to a known distance
        be = math.radians(3.0)
        def front_px(x, z):
            return (1000 + 0.8 * (x * math.cos(be) - z * math.sin(be)), 50 + 0.8 * (x * math.sin(be) + z * math.cos(be)))
        fpairs = []
        for (x, z) in ((800, 600), (500, 900), (300, 200)):
            fpairs += [("click_photo", "front", front_px(x, z)), ("click_photo", "front", front_px(-x, z))]
        b.run("photo_tools", [("opt", "Center")] + fpairs + [("enter",)])
        ph = [v for v in frames if b.sc.doc.Layers[v.Attributes.LayerIndex].Name == "front"][0]
        w, h = [float(t) for t in ph.Attributes.GetUserString(b.ns["PHOTO_SIZE_KEY"]).split()]
        def fw(x, z):
            u, v = front_px(x, z); return ph.Geometry.PointAt(u / w, v / h)
        def mir():
            return max(max(abs(fw(x, z).X + fw(-x, z).X), abs(fw(x, z).Z - fw(-x, z).Z)) for (x, z) in ((800, 600), (100, 50)))
        check("Photo Center: LEFT/RIGHT of the front photo mirrored across x = 0", mir() / b.k < 1e-6, "%.2e" % mir())
        b.run("photo_tools", [("opt", "Floor"), ("click_photo", "front", front_px(700, 0)),
                              ("click_photo", "front", front_px(-700, 0)), ("enter",)])
        check("Photo Floor: tyre contact at z = 0, still centred", abs(fw(700, 0).Z) / b.k < 1e-6 and mir() / b.k < 1e-6)
        b.run("photo_tools", [("opt", "Scale"), ("click_photo", "front", front_px(800, 600)),
                              ("click_photo", "front", front_px(-800, 600)), ("number", 1600 * b.k)])
        d = fw(800, 600).DistanceTo(fw(-800, 600)) / b.k
        check("Photo Scale: the known distance, centre and floor kept", abs(d - 1600) < 1e-6 and mir() / b.k < 1e-6 and
              abs(fw(700, 0).Z) / b.k < 1e-6, "%.6f mm" % d)
        check("... so the front photo is now the true car", max(abs(fw(x, z).X - x * b.k) for (x, z) in ((800, 600), (300, 200)))
              / b.k < 1e-6)
        # setups keep the photos
        corners = [(p.O.X, p.O.Y, p.X.Z) for p in [v.Geometry for v in frames]]
        b.run("scan_setup", [("list", "<save the present positions as ...>"), ("string", "ph")])
        for v in frames: v.Geometry.Transform(fr.Transform.Translation(fr.Vector3d(55, -20, 7)))     # by hand
        b.run("scan_setup", [("list", "ph")])
        back = [(p.O.X, p.O.Y, p.X.Z) for p in [v.Geometry for v in frames]]
        check("MeshSetup brings the photos back", max(abs(a_ - c_) for t1, t2 in zip(corners, back)
                                                       for a_, c_ in zip(t1, t2)) < 1e-6)
    return f


def s_realign_tree():
    def f():
        b = Bench()
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_align", align_pairs("B", "C", BC) + [("enter",)])
        # B knocked away (as if moved by hand) - C was aligned to B
        oB = b.sc.doc.Objects.FindId(b.objs["B"].Id); oC = b.sc.doc.Objects.FindId(b.objs["C"].Id)
        G = rand_pose(np.random.RandomState(3), (900, -300, 200))
        oB.InstanceXform = G * oB.InstanceXform; oC.InstanceXform = G * oC.InstanceXform
        b.ns["remember_positions"]()
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        check("re-Align B carries C (tree)", b.rel_err("A", "C") < TOL and b.rel_err("B", "C") < TOL,
              "%.2e mm" % b.rel_err("A", "C"))
        snap = b.snapshot()
        # the other way round must be refused: A (the root, carrying B and C) onto C
        b.run("scan_align", [("click_on", "C", (-500, 900)), ("click_on", "A", (-600, -500)), ("esc",)])
        check("Align of a mesh onto its own child is refused, nothing moved", b.same_as(snap) < 1e-9)
    return f


def s_cancel_undo():
    def f():
        b = Bench()
        snap = b.snapshot()
        b.run("scan_align", align_pairs("A", "B", AB[:2]) + [("esc",)])
        check("Align Esc: meshes back", b.same_as(snap) < 1e-9)
        b.run("scan_align", align_pairs("A", "B", AB[:2]) + [("opt", "UndoPair"), ("opt", "UndoPair"),
                                                              ] + align_pairs("A", "B", AB) + [("enter",)])
        check("Align UndoPair to zero, then again", b.rel_err("A", "B") < TOL)
        snap = b.snapshot()
        b.run("scan_center", CENTER[:2] + [("opt", "Cancel")])
        check("Center Cancel: meshes back", b.same_as(snap) < 1e-9)
        b.run("scan_center", CENTER[:2] + [("esc",)])
        check("Center Esc: meshes back", b.same_as(snap) < 1e-9)
        b.run("scan_floor", FLOOR[:3] + [("esc",)])
        check("Floor Esc after 3 points: nothing changed", b.same_as(snap) < 1e-9)
        b.run("scan_floor", FLOOR + [("enter",), ("esc",)])
        check("Floor Esc at the front/back point: nothing changed", b.same_as(snap) < 1e-9)
        b.run("scan_center", CENTER + [("enter",), ("opt", "DontSave")])        # Floor levels only after Center
        b.run("scan_floor", FLOOR[:2] + [("opt", "UndoPt")] + FLOOR[1:] + [("enter",), ("enter",), ("opt", "DontSave")])
        check("Floor UndoPt", b.floor_err()[0] < TOL, "spread %.2e mm" % b.floor_err()[0])
    return f


def s_fit(radius=None):
    def f():
        b = Bench()
        rng = np.random.RandomState(4)
        rad = [("opt", "AutoFitRadius", float(radius))] if radius else []
        b.run("scan_align", align_pairs("A", "B", AB, noise=6.0, rng=rng) + [("enter",)])
        before = b.rel_err("A", "B")
        b.run("scan_align", [("opt", "UndoPair")] * 0 + align_pairs("A", "B", AB, noise=6.0, rng=np.random.RandomState(4)) +
              rad + [("opt", "Fit"), ("enter",)]) if False else None
        b2 = Bench(); rng = np.random.RandomState(4)
        b2.run("scan_align", align_pairs("A", "B", AB, noise=6.0, rng=rng) + rad + [("opt", "Fit"), ("enter",)])
        check("Align Fit, AutoFitRadius %s (pairs 6 mm off: %.1f mm before Fit)" % (radius or "default 50", before),
              b2.rel_err("A", "B") < 0.5, "%.3f mm after Fit" % b2.rel_err("A", "B"))
        b3 = Bench(); rng = np.random.RandomState(9)
        noisy = [(a, n, (x + rng.uniform(-8, 8), y)) for (a, n, (x, y)) in CENTER]
        b3.run("scan_center", noisy + [("enter",), ("opt", "DontSave")])
        before = b3.sym_err()
        b4 = Bench()
        b4.run("scan_center", noisy + rad + [("opt", "AutoFit"), ("enter",), ("opt", "DontSave")])
        check("Center AutoFit, AutoFitRadius %s (pairs 8 mm off: %.2f mm before)" % (radius or "default 50", before),
              b4.sym_err() < 0.5, "%.3f mm after AutoFit" % b4.sym_err())
    return f


def s_setup_with_later_scan():
    def f():
        b = Bench(names=("A", "B"))
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("string", "front")])
        # a scan loaded later, aligned to A: every saved setup learns where it goes
        b.add("D", (0, 0, 2500))
        b.run("scan_align", align_pairs("A", "D", AD) + [("enter",)])
        check("Align new scan D", b.rel_err("A", "D") < TOL)
        snap = b.snapshot()
        b.perturb_all(11)
        b.run("scan_setup", [("list", "front")])
        check("setup 'front' also puts D right (updated after Align)", b.same_as(snap) < 1e-6, "%.2e" % b.same_as(snap))
    return f


def s_units_cross_program():
    """A setup written by Rhino in INCHES, read by the Blender / FreeCAD side (core.setups, mm) and back."""
    def f():
        b = Bench("Inches")
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("string", "inch_setup")])
        check("inch document: Align + Center", b.rel_err("A", "B") < TOL and b.sym_err() < TOL)
        from core import setups as cs
        st = b.setups()["inch_setup"]
        check("setup units 'in', 25.4 mm per unit", st["_units"] == "in" and abs(st["_mm_per_unit"] - 25.4) < 1e-12)
        rec = cs.find_record(st, os.path.join(WORK, "scans", "B.stl"))
        m = cs.scaled_matrix(rec, st, 1.0)                    # what Blender / FreeCAD (mm) put
        X = b.xf("B")
        ok = all(abs(m[i * 4 + 3] - X[i, 3] * 25.4) < 1e-6 for i in range(3)) and \
            all(abs(m[i * 4 + j] - X[i, j]) < 1e-12 for i in range(3) for j in range(3))
        check("Blender/FreeCAD read the inch setup in mm", ok)
        # a setup written in mm by Blender / FreeCAD, applied in the inch Rhino document
        data = b.setups()
        recs = []
        for n in ("A", "B"):
            Xn = b.xf(n); mm = [Xn[i, j] for i in range(4) for j in range(4)]
            for i in (3, 7, 11): mm[i] *= 25.4
            recs.append((os.path.join(WORK, "scans", n + ".stl"), mm, None))
        data["from_blender"] = cs.make_setup(recs, "mm")
        cs.write_setups([os.path.join(WORK, "scans", "A.stl")], data)
        snap = b.snapshot(); b.perturb_all(5)
        b.run("scan_setup", [("list", "from_blender")])
        check("Rhino (inches) applies a mm setup from Blender/FreeCAD", b.same_as(snap) < 1e-6, "%.2e" % b.same_as(snap))
    return f


def s_old_setup_without_units():
    def f():
        b = Bench()
        b.run("scan_align", align_pairs("A", "B", AB) + [("enter",)])
        b.run("scan_center", CENTER + [("enter",), ("string", "x")])
        data = b.setups()
        for k in ("_units", "_mm_per_unit", "_format"): data["x"].pop(k, None)        # as 0.1.1 wrote it
        json.dump(data, open(os.path.join(WORK, "scans", "scan_setups.json"), "w"))
        snap = b.snapshot(); b.perturb_all(2)
        b.run("scan_setup", [("list", "x")])
        check("an old setup (no units) still works in a mm document", b.same_as(snap) < 1e-6)
    return f


if __name__ == "__main__":
    scenario("Align, Align, Center, Floor, Setup (mm)", s_align_center_floor_setup())
    scenario("the same in an INCH document", s_align_center_floor_setup("Inches"))
    scenario("Center first, then Align, then Floor", s_center_first())
    scenario("Floor before Center", s_floor_before_center())
    scenario("Floor before Center, INCH document", s_floor_before_center("Inches"))
    scenario("Floor with another SymPlane than Center", s_floor_other_symplane())
    scenario("Align never moves a centred scan/mesh away", s_align_guard())
    scenario("AutoAlign: B and C at once onto the centred A", s_autoalign())
    scenario("AutoAlign: guard, clicked markers, Esc", s_autoalign_guard_and_clicks())
    scenario("setups 'front' / 'rear' (two centres), switching", s_setups_front_rear())
    scenario("Align / AutoAlign with Scale", s_align_scale())
    scenario("MeshScale from a curve (+ Center option)", s_mesh_scale())
    scenario("moved by hand: Keep / KeepOnly / Back / Esc / whole set", s_hand_moves())
    scenario("photos: side photo onto the car, rear part from it, front photo centred / levelled / scaled",
             s_photos())
    scenario("re-Align a mesh that carries another one; refused cycle", s_realign_tree())
    scenario("Esc / Cancel / UndoPair / UndoPt", s_cancel_undo())
    scenario("Fit and AutoFit with inexact clicks", s_fit())
    scenario("Fit and AutoFit, bigger spheres", s_fit(300))
    scenario("a scan loaded later + setups", s_setup_with_later_scan())
    scenario("units across programs (inch Rhino <-> mm Blender/FreeCAD)", s_units_cross_program())
    scenario("old setup file without units", s_old_setup_without_units())
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed" % (len(results), len(bad)))
    for r in bad: print("  FAIL:", r[0], r[2])
    sys.exit(1 if bad else 0)
