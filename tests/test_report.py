"""The start of a PATCHES run says which surfaces take part (Session.surface_report).
    python tests/test_report.py"""
import io, os, sys, uuid, contextlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerhino as fr

ns, sc = fr.load_script(os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py"))
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)

ids = [uuid.uuid4() for _ in range(4)]
built = {ids[0]: "Mesh"}
ns["rs"].GetUserText = lambda oid, k=None: built.get(oid) if k == "msh2classA_fit" else None


class S(object):
    surface_report = ns["Session"].surface_report
def run(surfs):
    s = S(); s.surfs = surfs; out = io.StringIO()
    with contextlib.redirect_stdout(out): s.surface_report()
    return out.getvalue()

txt = run([(ids[0], None, None, True), (ids[1], None, None, True), (ids[2], None, None, False), (ids[3], None, None, False)])
print(txt)
chk("says where new patches go (the current layer)", "CURRENT layer" in txt)
chk("counts: 1 msh2classA patch, 3 others", "1 msh2classA patch(es), 3 other(s)" in txt)
chk("the editable other surface: takes part fully, GlobalMatch leaves it", "1 surface(s) NOT built by msh2classA" in txt
    and "GlobalMatch leaves" in txt)
chk("the 2 that can not be changed: only their edges", "2 surface(s) can not be changed" in txt and "EDGES" in txt)
chk("how to keep them out (hide / lock)", "HIDE their layer" in txt and "LOCK" in txt)
txt = run([(ids[0], None, None, True)])
chk("only msh2classA patches: one line, no notes", txt.count("\n") == 1 and "NOTE" not in txt)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
