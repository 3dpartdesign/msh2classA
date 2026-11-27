"""msh2classA tests (GPL-3.0, see NOTICE). LoadMeshes scale: scan files in ScanMeshUnits -> document units,
whatever scale Rhino's importer used. Usage: python tests/test_import_units.py"""
import os, struct, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerhino as fr
D = os.path.join(tempfile.gettempdir(), "m2ca_units"); os.makedirs(D, exist_ok=True)
SCRIPT = os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py")
V = [(120.5, -300.25, 410.0), (10.0, 20.0, 30.0), (-55.5, 80.0, 12.25), (300.0, 0.0, -40.0)]
T = [(0, 1, 2), (1, 3, 2)]
def w_stl_bin(p):
    with open(p, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(T)))
        for t in T: f.write(struct.pack("<12fH", 0, 0, 0, *V[t[0]], *V[t[1]], *V[t[2]], 0))
def w_stl_txt(p):
    with open(p, "w") as f:
        f.write("solid x\n")
        for t in T:
            f.write(" facet normal 0 0 0\n  outer loop\n")
            for i in t: f.write("   vertex %r %r %r\n" % V[i])
            f.write("  endloop\n endfacet\n")
        f.write("endsolid x\n")
def w_obj(p):
    with open(p, "w") as f:
        for v in V: f.write("v %r %r %r\n" % v)
        for t in T: f.write("f %d %d %d\n" % tuple(i + 1 for i in t))
def w_ply(p):
    with open(p, "wb") as f:
        f.write(("ply\nformat binary_little_endian 1.0\nelement vertex %d\nproperty float x\nproperty float y\n"
                 "property float z\nelement face %d\nproperty list uchar int vertex_indices\nend_header\n" % (len(V), len(T))).encode())
        for v in V: f.write(struct.pack("<3f", *v))
        for t in T: f.write(struct.pack("<B3i", 3, *t))
files = {"bin.stl": w_stl_bin, "txt.stl": w_stl_txt, "a.obj": w_obj, "a.ply": w_ply}
for n, w in files.items(): w(os.path.join(D, n))
bad = 0
for unit, want in (("Millimeters", 1.0), ("Inches", 1 / 25.4), ("Meters", 0.001)):
    for scan_units, smm in (("mm", 1.0), ("in", 25.4)):
        for n in files:
            for importer in (1.0, 1 / 25.4, 25.4, 0.001):      # what Rhino's importer may have done
                ns, sc = fr.load_script(SCRIPT, unit)
                sc.sticky["M2CA_scan_units"] = scan_units
                m = fr.Mesh([tuple(c * importer for c in v) for v in V], T)
                ns["normalize_scale"](os.path.join(D, n), [m])
                exp = smm * want     # file numbers -> document units
                err = max(abs(m.V[i][k] - V[i][k] * exp) for i in range(len(V)) for k in range(3))
                if err > 1e-4 * max(1, exp * 400):
                    bad += 1; print("FAIL", unit, scan_units, n, "importer x%g" % importer, "err", err)
print("import scale cases:", 3 * 2 * len(files) * 4, "failed:", bad)
sys.exit(1 if bad else 0)
