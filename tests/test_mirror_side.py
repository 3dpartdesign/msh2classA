"""The MIRROR side of a patch stays ON its own curve (bad_patch.stp, 0.1.2-alpha, Sweep, WholeMesh):
the mirror side was held only softly (weight 2, like a side on the scan/mesh) - the scan/mesh points and
the Sweep shape pulled it off its curve, two of its CVs fell together (88 mm, then 1 mm) and the next CV
column made up for it (a 30 mm peak). Now it is held like every drawn curve (session_build: the side loop).
Here: the same build of the constraints (corners, 3 curve sides, the mirror side G2 to its mirror image,
scan/mesh data that run 25 mm off the curve near the plane) - old (soft) vs new (held).
    python tests/test_mirror_side.py"""
import math, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.bspline import BDir
from core.patch_fit import FitProblem, run_fit, fit_to_function, coons_point, SIDE_MAP
from core.evaluate import srf_point
from core.cv_order import cv_order_worst
fails = []


def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)


# a half rear window, v = 1 on the plane x = 0 (side 3 = the mirror side)
def true(u, v):
    return (530.0 * (1 - v), 785 + 150 * u, -752 - 180 * u - 12 * math.sin(math.pi * u) * (1 - v * v))
def scan(u, v):
    g = true(u, v)
    if v > 0.7: return (g[0], g[1], g[2] - 25.0 * math.sin(math.pi * u) * (v - 0.7) / 0.3)   # a lip under the plane
    return g
fs = [lambda t: true(t, 0.0), lambda t: true(1.0, t), lambda t: true(1.0 - t, 1.0), lambda t: true(0.0, 1.0 - t)]
coons = lambda u, v: coons_point(fs, u, v)


def build(mirror_held):
    du = dv = BDir(5, 1); n = 6
    prob = FitProblem(du, dv, fit_to_function(du, dv, coons))
    for (u, v) in ((0, 0), (1, 0), (1, 1), (0, 1)): prob.hard.append((float(u), float(v), true(u, v)))
    K = 3 * n + 2
    for k in range(4):
        uvf = SIDE_MAP[k][0]
        for i in range(K):
            t = i / float(K - 1); u, v = uvf(t)
            if k == 2 and not mirror_held: prob.soft.append((u, v, fs[k](t), 2.0))     # 0.1.2-alpha and older
            else: prob.hard.append((u, v, fs[k](t)))
    rows = lambda r: [i * n + (n - 1 - r) for i in range(n)]
    prob.mirrors.append((0, 0.0, rows(0), rows(1), rows(2)))
    ns_ = 3 * n + 6
    params = [(i / (ns_ - 1.0), j / (ns_ - 1.0)) for i in range(ns_) for j in range(ns_)]
    P = run_fit(prob, lambda P: [(u, v, scan(u, v), 1.0) for (u, v) in params], 8)
    off = max(math.dist(srf_point(du, dv, P, t, 1.0), true(t, 1.0)) for t in [i / 40.0 for i in range(41)])
    edge = [P[i * n + n - 1] for i in range(n)]
    steps = [math.dist(edge[i], edge[i + 1]) for i in range(n - 1)]
    return off, min(steps) / max(steps), cv_order_worst(P, n, n)[0], max(abs(p[0]) for p in edge)


off0, r0, f0, x0 = build(False)
off1, r1, f1, x1 = build(True)
print("  old (soft): mirror edge off its curve %.2f mm, CV steps min/max %.2f, fold %.2f" % (off0, r0, f0))
print("  new (held): mirror edge off its curve %.4f mm, CV steps min/max %.2f, fold %.2f" % (off1, r1, f1))
chk("the old way shows the fault here (the edge is pulled off its curve)", off0 > 1.0)
chk("the mirror edge lies ON its curve", off1 < 0.05, "(%.4f mm)" % off1)
chk("... and ON the plane x = 0", x1 < 1e-6)
chk("its CVs evenly along it (no two together)", r1 > 0.5, "(%.2f)" % r1)
chk("the CV net does not fold", f1 > 0.0)
src = open(os.path.join(ROOT, "rhino", "session_build.py")).read()
chk("session_build holds the mirror side like a curve", 'if s.kind == "scan" and k not in self.mirror_sides:\n'
    '                for t in ts:' in src)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
