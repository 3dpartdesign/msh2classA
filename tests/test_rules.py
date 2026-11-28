"""Rules of the code, checked on the sources (not on Rhino):
  1  GetPoint.Get is called ONLY in tracked_get (all clicks go through click_get)
  2  GetPoint.Constrain is called ONLY by the click plane (view_constrain / _reconstrain)
  3  every function that makes a GetPoint gets its click with click_get / scan_get (or the Fine
     drag with tracked_get after view_constrain)
  4  no point ON a mesh from Rhino's GetPoint: gp.Point() / e.Point only as the cursor ray's input
  5  the ray under the cursor is made ONLY by cursor_ray (no other GetFrustumLine)
    python tests/test_rules.py
See DEVNOTES.md."""
import ast, glob, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GET_OK = {"tracked_get"}
CONSTRAIN_OK = {"view_constrain", "_reconstrain", "follow", "_on_view_modified"}
POINT_OK = {"click_ray", "on_move_view", "collect_polygon"}      # ray input / screen position only
bad = []


def calls(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            yield n


for path in sorted(glob.glob(os.path.join(ROOT, "rhino", "*.py"))):
    src = open(path).read()
    tree = ast.parse(src)
    name = os.path.basename(path)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    owner = {}
    for f in funcs:                       # innermost function of each call
        for c in calls(f):
            if id(c) not in owner or owner[id(c)][1] > (f.end_lineno - f.lineno):
                owner[id(c)] = (f.name, f.end_lineno - f.lineno)
    for c in calls(tree):
        fn = owner.get(id(c), ("<module>", 0))[0]
        attr = c.func.attr
        tgt = c.func.value
        tname = tgt.id if isinstance(tgt, ast.Name) else (tgt.attr if isinstance(tgt, ast.Attribute) else "")
        if attr == "Get" and tname in ("gp", "g") and fn not in GET_OK:
            bad.append("%s:%d %s(): GetPoint.Get - use click_get" % (name, c.lineno, fn))
        if attr == "Constrain" and fn not in CONSTRAIN_OK:
            bad.append("%s:%d %s(): GetPoint.Constrain - only the click plane may constrain" % (name, c.lineno, fn))
        if attr == "GetFrustumLine" and fn != "cursor_ray":
            bad.append("%s:%d %s(): GetFrustumLine - the cursor ray is cursor_ray" % (name, c.lineno, fn))
        if attr == "Point" and tname == "gp" and fn not in POINT_OK:
            bad.append("%s:%d %s(): gp.Point() - take the point from click_ray / mesh_at_click" % (name, c.lineno, fn))
    for f in funcs:
        made = any(c.func.attr == "GetPoint" for c in calls(f))
        if not made: continue
        used = set(c.func.id for c in ast.walk(f) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name))
        if not used & {"click_get", "scan_get", "tracked_get"}:
            bad.append("%s:%d %s(): makes a GetPoint but does not click with click_get" % (name, f.lineno, f.name))
        if "tracked_get" in used and "view_constrain" not in used and f.name != "click_get":
            bad.append("%s:%d %s(): tracked_get without view_constrain" % (name, f.lineno, f.name))
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and n.attr == "Point" and isinstance(n.value, ast.Name) and n.value.id == "e":
            fn = [f.name for f in funcs if f.lineno <= n.lineno <= f.end_lineno]
            if not fn or fn[-1] not in POINT_OK:
                bad.append("%s:%d e.Point - a mouse point is only the input of cursor_ray" % (name, n.lineno))

for b in bad: print("  [FAIL]", b)
print("rules: %d broken" % len(bad))
sys.exit(1 if bad else 0)
