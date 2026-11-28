"""
msh2classA - Align / AutoAlign / Center / Floor / Setup in EVERY order.

A model, written independently of the script, says after every step what
must hold - and the real script (through fakerhino) is checked against it:

  * scans/meshes aligned to each other (directly or through others) sit
    EXACTLY right relative to each other (true car geometry)
  * the ones Center made symmetric are symmetric (x = 0), the ones Floor
    levelled have their floor points at z = 0 (and front/back 0 if used)
  * the Center / Floor marks of the script say exactly that (members)
  * every saved setup, as it is in scan_setups.json, holds the same three
    things for its own positions - and applying it brings them back exactly
  * whatever the guard refuses, or Esc cancels, changes nothing at all

Two parts: a fixed checklist of orders (the ones people do), then random
sequences of 12 steps (many seeds, mm and inch documents).

    python tests/test_orders.py [script] [--seeds N]
"""
from __future__ import print_function

import json
import math
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_argv = sys.argv[:]
sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:2] if not a.startswith("--")]
import test_scan_tools as T  # noqa: E402
import fakerhino as fr       # noqa: E402
sys.argv = _argv

TOL = 0.01       # mm
NAMES = ("A", "B", "C", "D")
# where two pieces overlap: points (x, y) inside both
OVERLAP = {frozenset("AB"): T.AB, frozenset("BC"): T.BC, frozenset("AD"): T.AD, frozenset("BD"): T.AB}
CENTER_ON = {"A": T.CENTER,
             "C": [("click_on", "C", p) for p in ((390, 1500), (-390, 1500), (690, 900), (-690, 900),
                                                   (300, 2000), (-300, 2000))]}
FLOOR_ON = {"A": [(870, -1980), (-870, -1980), (870, -390), (-870, -390)],
            "C": [(870, 900), (-870, 900), (870, 2100), (-870, 2100)]}
REF_ON = {"A": (0, -1500), "C": (0, 1500)}
SAVE = "<save the present positions as ...>"

results = []


def check(name, cond, info=""):
    results.append((name, bool(cond), info))
    if not cond:
        print("      [FAIL] %s %s" % (name, info))
    return cond


def true_pt(x, y):
    return (x, y, T.height(x, y))


# ------------------------------------------------------------------ measurements on transforms
def mm_pt(b, p, X):
    q = fr.Point3d(*[c * b.k for c in p]); q.Transform(X); return q


def is_centred(b, X):
    worst = 0.0
    for p in ((300, -1000, 100), (500, 800, 200), (100, 2000, 50), (700, -2000, 300)):
        a = mm_pt(b, p, X); c = mm_pt(b, (-p[0], p[1], p[2]), X)
        worst = max(worst, abs(a.X + c.X), abs(a.Y - c.Y), abs(a.Z - c.Z))
    return worst / b.k


def is_level(b, X, fdef):
    pts, ref = fdef
    worst = max(abs(mm_pt(b, true_pt(*p), X).Z) for p in pts)
    if ref is not None:
        worst = max(worst, abs(mm_pt(b, true_pt(*ref), X).Y))
    return worst / b.k


def rel(b, Xa, Xb):
    ok, ia = Xa.TryGetInverse(); E = ia * Xb; worst = 0.0
    for p in ((-900, -2190, 0), (900, 2190, 300), (900, -2190, 0), (-900, 2190, 300), (0, 0, 150)):
        q = fr.Point3d(*[c * b.k for c in p]); r = fr.Point3d(q); r.Transform(E)
        worst = max(worst, q.DistanceTo(r))
    return worst / b.k


# ------------------------------------------------------------------ the model
class State(object):
    """What must hold: labels (same label = exactly aligned), centred set, levelled set + floor definition,
    the core (clicked on) of Center and Floor."""
    def __init__(self):
        self.label = dict((n, i) for i, n in enumerate(NAMES)); self.next = len(NAMES)
        self.C = set(); self.Ccore = set(); self.center = False
        self.L = set(); self.Lcore = set(); self.fdef = None; self.fpiece = None

    def copy(self):
        s = State(); s.__dict__.update(dict((k, (set(v) if isinstance(v, set) else
                                                 dict(v) if isinstance(v, dict) else v))
                                            for k, v in self.__dict__.items()))
        return s

    def cls(self, n):
        return set(k for k in NAMES if self.label[k] == self.label[n])


class Runner(object):
    def __init__(self, unit, log):
        # D aside (not above A: a click on A must not hit a D that is not aligned yet - a user would hide it)
        self.b = T.Bench(unit, names=NAMES, shifts={"A": (0, 0, 0), "B": (2600, 400, 150), "C": (-2600, 900, -80),
                                                    "D": (0, 6500, 0)})
        self.m = State()
        self.setups = {}          # name -> (State, {piece: matrix or None (moved in it since)})
        self.log = log

    # -- what the script says
    def frame(self):
        s_ = self.b.sc.doc.Strings.GetValue(self.b.ns["FRAME_KEY"])
        return self.b.ns["_norm_frame"](json.loads(s_)) if s_ else {}

    def tree(self):
        par = self.b.ns["align_parent_map"]()
        idn = dict((str(self.b.objs[n].Id), n) for n in NAMES)
        return dict((idn[c], idn[p]) for c, p in par.items() if c in idn and p in idn)

    def branch(self, n):
        t = self.tree(); out = {n}; grow = True
        while grow:
            grow = False
            for c, p in t.items():
                if p in out and c not in out: out.add(c); grow = True
        return out

    def file_state(self):
        try:
            return open(os.path.join(T.WORK, "scans", "scan_setups.json")).read()
        except IOError:
            return ""

    def everything(self):
        return (self.b.snapshot(), json.dumps(self.frame(), sort_keys=True), self.file_state())

    def same(self, before):
        snap, frm, fil = before
        return self.b.same_as(snap) < 1e-9 and json.dumps(self.frame(), sort_keys=True) == frm \
            and self.file_state() == fil

    # -- refusal as the model sees it
    def refused(self, moving, onto):
        m = self.m
        for core, mem in ((m.Ccore, m.C), (m.Lcore, m.L)):
            if (moving & core) and not (onto & core): return True
            if (moving & mem) and not (onto & mem): return True
        return False

    def moved_onto(self, moving, f):
        """model: the branch moving now lies exactly on f"""
        m = self.m
        for n in moving:
            m.label[n] = m.label[f]
        for mem, core in ((m.C, m.Ccore), (m.L, m.Lcore)):
            if f in mem:
                mem |= moving
            else:
                mem -= moving; core -= moving
        for name, (S, pos) in self.setups.items():
            for n in moving:
                S.label[n] = S.label[f]; pos[n] = None
            for mem, core in ((S.C, S.Ccore), (S.L, S.Lcore)):
                if f in mem: mem |= moving
                else: mem -= moving; core -= moving

    # ------------------------------------------------------------ steps
    def show_only(self, names):
        """Like a user: only the scans/meshes of this Align are visible (None = all)."""
        for n in NAMES:
            o = self.b.sc.doc.Objects.FindId(self.b.objs[n].Id)
            self.b.sc.doc.Layers[o.Attributes.LayerIndex].IsVisible = (names is None or n in names)

    def align(self, f, mv, rough=False):
        self.show_only({f} | self.branch(mv))
        try:
            self._align(f, mv, rough)
        finally:
            self.show_only(None)

    def _align(self, f, mv, rough=False):
        b, m = self.b, self.m
        pts = OVERLAP[frozenset(f + mv)]
        br = self.branch(mv)
        if f in br:
            self.log("align %s onto %s: %s carries %s -> refused" % (mv, f, mv, f))
            before = self.everything()
            b.run("scan_align", [("click_on", f, pts[0]), ("click_on", mv, pts[0]), ("esc",)])
            check("refused (cycle) changes nothing", self.same(before)); return
        if self.refused(br, {f}):
            self.log("align %s onto %s -> guard refuses" % (mv, f))
            before = self.everything()
            b.run("scan_align", [("click_on", f, pts[0]), ("click_on", mv, pts[0]), ("esc",)])
            check("guard refusal changes nothing (%s onto %s)" % (mv, f), self.same(before)); return
        self.log("align %s onto %s%s" % (mv, f, " (rough + AutoAlign)" if rough else ""))
        if not rough:
            b.run("scan_align", T.align_pairs(f, mv, pts) + [("enter",)])
            self.moved_onto(br, f)
            return
        rng = np.random.RandomState(len(results))
        b.run("scan_align", T.align_pairs(f, mv, pts, noise=12.0, rng=rng) + [("enter",)])
        stays = sorted(m.cls(f) - br)
        b.run("scan_autoalign", [("multi", [mv]), ("multi", stays), ("opt", "SphereRadius", 150.0 * b.k),
                                 ("opt", "FitInSpheres"), ("opt", "FitInSpheres"), ("enter",)])
        self.moved_onto(br, f)

    def center(self, p):
        self.show_only({p})                 # like a user: the scan/mesh you click on
        try:
            self._center(p)
        finally:
            self.show_only(None)

    def _center(self, p):
        b, m = self.b, self.m
        self.log("center on %s" % p)
        b.run("scan_center", CENTER_ON[p] + [("enter",), ("opt", "DontSave")])
        m.C = m.cls(p); m.Ccore = {p}; m.center = True
        if m.fdef is not None and m.fpiece in m.C:         # Center levels the floor again (same Floor points)
            m.L = m.cls(m.fpiece); m.Lcore = {m.fpiece}
        else:                                              # floor not on the centred ones: gone, Floor again
            m.L = set(); m.Lcore = set(); m.fdef = None; m.fpiece = None

    def floor(self, p, ref):
        self.show_only({p})
        try:
            self._floor(p, ref)
        finally:
            self.show_only(None)

    def _floor(self, p, ref):
        b, m = self.b, self.m
        if m.C and p not in m.C:
            self.log("floor on %s -> refused (not centred, %s is)" % (p, sorted(m.C)))
            before = self.everything()
            b.run("scan_floor", [("click_on", p, q) for q in FLOOR_ON[p]] + [("enter",)])
            check("refused Floor changes nothing", self.same(before)); return
        self.log("floor on %s%s" % (p, " + front/back 0" if ref else ""))
        ans = [("click_on", p, q) for q in FLOOR_ON[p]] + [("enter",)]
        ans += [("click_on", p, REF_ON[p])] if ref else [("enter",)]
        b.run("scan_floor", ans + [("opt", "DontSave")])
        m.L = m.cls(p); m.Lcore = {p}; m.fpiece = p
        m.fdef = (FLOOR_ON[p], REF_ON[p] if ref else None)

    def save(self, name):
        self.log("save setup %s" % name)
        self.b.run("scan_setup", [("list", SAVE), ("string", name)])
        self.setups[name] = (self.m.copy(), dict((n, fr.Transform(self.b.xf(n))) for n in NAMES))

    def apply(self, name):
        self.log("apply setup %s" % name)
        self.b.run("scan_setup", [("list", name)])
        S, pos = self.setups[name]
        for n, X in pos.items():
            if X is not None:
                d = float(np.abs(self.b.xf(n).M - X.M).max()) / self.b.k
                check("setup %s: %s back exactly where it was saved" % (name, n), d < 1e-6, "%.2e mm" % d)
        self.m = S.copy()

    def escapes(self):
        b = self.b
        before = self.everything()
        f, mv = "A", "B"
        b.run("scan_align", [("click_on", f, T.AB[1]), ("click_on", mv, T.AB[1]), ("esc",)])
        check("Esc in Align changes nothing", self.same(before))
        b.run("scan_center", CENTER_ON["A"][:2] + [("esc",)])
        check("Esc in Center changes nothing", self.same(before))
        b.run("scan_floor", [("click_on", "A", q) for q in FLOOR_ON["A"][:2]] + [("esc",)])
        check("Esc in Floor changes nothing", self.same(before))
        self.log("Esc in Align / Center / Floor")

    # ------------------------------------------------------------ the checks
    def verify(self, where):
        b, m = self.b, self.m
        X = dict((n, b.xf(n)) for n in NAMES)
        bad = []
        for i, a in enumerate(NAMES):
            for c in NAMES[i + 1:]:
                if m.label[a] == m.label[c]:
                    e = rel(b, X[a], X[c])
                    if e > TOL: bad.append("%s-%s off %.3f" % (a, c, e))
        check("%s: aligned ones exact" % where, not bad, "; ".join(bad))
        bad = ["%s %.3f" % (n, is_centred(b, X[n])) for n in sorted(m.C) if is_centred(b, X[n]) > TOL]
        check("%s: centred ones symmetric" % where, not bad, "; ".join(bad))
        if m.L:
            bad = ["%s %.3f" % (n, is_level(b, X[n], m.fdef)) for n in sorted(m.L) if is_level(b, X[n], m.fdef) > TOL]
            check("%s: levelled ones level" % where, not bad, "; ".join(bad))
        fd = self.frame()
        cm = set((fd.get("center") or {}).get("members", [])); fm = set((fd.get("floor") or {}).get("members", []))
        check("%s: Center marks = the centred ones" % where, cm == m.C, "script %s, model %s" % (sorted(cm), sorted(m.C)))
        check("%s: Floor marks = the levelled ones" % where, fm == m.L, "script %s, model %s" % (sorted(fm), sorted(m.L)))
        t = self.tree()
        for n in NAMES:
            seen = set(); k = n
            while k in t and k not in seen: seen.add(k); k = t[k]
            if k in seen:
                check("%s: Align tree without loops" % where, False, n); break
        # every setup, as written in the file
        data = b.setups() if self.setups else {}
        S_ = b.ns
        for name, (S, pos) in self.setups.items():
            st = data.get(name)
            if not check("%s: setup %s in the file" % (where, name), st is not None): continue
            XS = {}
            for n in NAMES:
                rec = st.get(n)
                if rec: XS[n] = S_["xf_from_setup"](rec["matrix_row_major"], st)
            bad = []
            for i, a in enumerate(NAMES):
                for c in NAMES[i + 1:]:
                    if S.label[a] == S.label[c] and a in XS and c in XS:
                        e = rel(b, XS[a], XS[c])
                        if e > TOL: bad.append("%s-%s off %.3f" % (a, c, e))
            bad += ["%s not symmetric %.3f" % (n, is_centred(b, XS[n])) for n in sorted(S.C)
                    if n in XS and is_centred(b, XS[n]) > TOL]
            if S.L:
                bad += ["%s not level %.3f" % (n, is_level(b, XS[n], S.fdef)) for n in sorted(S.L)
                        if n in XS and is_level(b, XS[n], S.fdef) > TOL]
            for n, Xp in pos.items():
                if Xp is not None and n in XS:
                    d = float(np.abs(XS[n].M - Xp.M).max()) / b.k
                    if d > 1e-6: bad.append("%s moved in it %.2e" % (n, d))
            sf = S_["_norm_frame"](st.get("_frame") or {})
            scm = set((sf.get("center") or {}).get("members", [])); sfm = set((sf.get("floor") or {}).get("members", []))
            if scm != S.C: bad.append("Center marks %s, should be %s" % (sorted(scm), sorted(S.C)))
            if sfm != S.L: bad.append("Floor marks %s, should be %s" % (sorted(sfm), sorted(S.L)))
            check("%s: setup %s right in the file" % (where, name), not bad, "; ".join(bad))


# ------------------------------------------------------------------ the checklist
CHECKLIST = [
    ("Align AB, BC, AD - Center - Floor - save", ["align A B", "align B C", "align A D", "center A", "floor A",
                                                    "save S1"]),
    ("Floor - Center (order turned)", ["align A B", "floor A", "center A", "save S1"]),
    ("Center - Align the others - Floor", ["center A", "align A B", "align B C", "floor A ref", "save S1"]),
    ("Floor - Align - Center - Floor again", ["floor A", "align A B", "center A", "floor A ref", "save S1"]),
    ("Center twice, Floor twice", ["align A B", "center A", "center A", "floor A", "floor A ref", "center A"]),
    ("save, align more, apply the old setup", ["align A B", "center A", "save S1", "align B C", "align A D",
                                                "apply S1", "floor A", "save S2", "apply S1", "apply S2"]),
    ("front + rear centres, switching", ["align A B", "align B C", "center A", "floor A", "save front",
                                          "center C", "save rear", "apply front", "apply rear", "apply front"]),
    ("rear centre on a NOT aligned rear", ["center A", "floor A", "save front", "center C", "save rear",
                                            "align C B", "apply front", "align A B", "apply rear"]),
    ("guard: the centred one onto a new one", ["center A", "align B A", "align A B", "align D A", "align A D",
                                               "floor A", "align D A"]),
    ("re-align a mesh that carries others", ["align A B", "align B C", "center A", "align D B", "align A D",
                                             "align D B", "save S1", "align B D", "apply S1"]),
    ("rough Align + AutoAlign, then Center / Floor", ["align A B rough", "center A", "align B C rough",
                                                       "floor A", "save S1", "align A D rough", "apply S1"]),
    ("Floor on the rear, Center on the front", ["align A B", "align B C", "floor C", "center A", "save S1",
                                                 "center C", "apply S1"]),
    ("Esc everywhere in between", ["align A B", "esc", "center A", "esc", "floor A", "esc", "save S1", "esc"]),
]


def do(r, step):
    w = step.split()
    if w[0] == "align": r.align(w[1], w[2], rough=(len(w) > 3))
    elif w[0] == "center": r.center(w[1])
    elif w[0] == "floor": r.floor(w[1], len(w) > 2)
    elif w[0] == "save": r.save(w[1])
    elif w[0] == "apply": r.apply(w[1])
    elif w[0] == "esc": r.escapes()


def run_seq(title, steps, unit):
    print("\n== %s  [%s]" % (title, unit))
    lines = []
    r = Runner(unit, lambda s_: lines.append(s_))
    fr.SCRIPT.prompts = []
    for i, st in enumerate(steps):
        n0 = len(results)
        try:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                do(r, st)
                r.verify("step %d (%s)" % (i + 1, st))
            failed = [x for x in results[n0:] if not x[1]]
            print("   %-28s %s" % (st, "ok" if not failed else "FAIL"))
            for x in failed: print("      [FAIL] %s %s" % (x[0], x[2]))
            if failed:
                print("      script said:\n        " + "\n        ".join(buf.getvalue().strip().splitlines()[-12:]))
        except Exception as e:
            results.append(("%s: step %d %s ran" % (title, i + 1, st), False, repr(e)))
            print("   %-28s ERROR %r" % (st, e)); traceback.print_exc(); break


def random_steps(seed, n=12):
    rng = np.random.RandomState(seed)
    pairs = [("A", "B"), ("B", "C"), ("A", "D"), ("B", "D")]
    out = []
    saved = []
    for _ in range(n):
        k = rng.choice(["align", "align", "align", "center", "floor", "save", "apply", "esc"],
                       p=[0.2, 0.15, 0.1, 0.15, 0.15, 0.1, 0.1, 0.05])
        if k == "align":
            a, c = pairs[rng.randint(len(pairs))]
            if rng.rand() < 0.5: a, c = c, a
            out.append("align %s %s%s" % (a, c, " rough" if rng.rand() < 0.12 else ""))
        elif k == "center":
            out.append("center %s" % ("A" if rng.rand() < 0.7 else "C"))
        elif k == "floor":
            out.append("floor %s%s" % ("A" if rng.rand() < 0.7 else "C", " ref" if rng.rand() < 0.4 else ""))
        elif k == "save":
            nm = "S%d" % (len(saved) % 3 + 1); saved.append(nm); out.append("save " + nm)
        elif k == "apply":
            if saved: out.append("apply " + saved[rng.randint(len(saved))])
        else:
            out.append("esc")
    return out


if __name__ == "__main__":
    nseeds = 30
    if "--seeds" in sys.argv: nseeds = int(sys.argv[sys.argv.index("--seeds") + 1])
    only = "--random-only" in sys.argv
    if not only:
        for title, steps in CHECKLIST:
            run_seq(title, steps, "Millimeters")
        for title, steps in CHECKLIST[:4] + CHECKLIST[6:8]:
            run_seq(title, steps, "Inches")
    for sd in range(nseeds):
        run_seq("random %d" % sd, random_steps(sd), "Inches" if sd % 3 == 2 else "Millimeters")
    bad = [r for r in results if not r[1]]
    print("\n%d checks, %d failed" % (len(results), len(bad)))
    seen = set()
    for r in bad:
        if r[0] in seen: continue
        seen.add(r[0]); print("  FAIL:", r[0], r[2])
    sys.exit(1 if bad else 0)
