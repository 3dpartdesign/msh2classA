"""The ONE click/hover point of the plugin (scan_tools: cursor_ray + mesh_ray_point,
used by click_ray, session mesh_at_click and mesh_under):
  - a perspective camera, the cursor point on the camera-facing click plane IN FRONT of
    the scan/mesh (as Rhino gives it now) -> the point ON the scan/mesh along that ray
  - the same with the click plane BEHIND the scan/mesh, and with a parallel view
  - the cursor beside the scan/mesh -> None (the session says "click ON the scan/mesh")
  - only the window point (no 3D point) -> the frustum line
    python tests/test_click.py"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerhino as fr  # noqa: E402

SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(fr.REPO, "dist", "rhino", "msh2classA.py")
ns, sc = fr.load_script(SCRIPT)
rg = fr.Rhino.Geometry if hasattr(fr, "Rhino") else None
P3, V3 = fr.Point3d, fr.Vector3d
fails = []


def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)


# a bumpy plate z = f(x, y), 0..100 x 0..100 mm
import math
n = 21
V = [(x * 5.0, y * 5.0, 3.0 * math.sin(x * 0.4) * math.cos(y * 0.3)) for x in range(n) for y in range(n)]
T = []
for i in range(n - 1):
    for j in range(n - 1):
        a = i * n + j; b = a + n; T += [(a, b, b + 1), (a, b + 1, a + 1)]
mesh = fr.Mesh(V, T)


class VP(object):
    def __init__(self, cam, d, persp=True):
        self.CameraLocation = P3(*cam); self.CameraDirection = V3(*d); self.IsPerspectiveProjection = persp
    def GetFrustumLine(self, x, y):
        return True, fr.Line(P3(*self.f[0]), P3(*self.f[1]))


def surf(x, y):
    return 3.0 * math.sin(x / 5.0 * 0.4) * math.cos(y / 5.0 * 0.3)


cam = (20.0, -30.0, 200.0)
tgt = (55.0, 40.0, surf(55.0, 40.0))          # the scan/mesh point under the cursor
d = [tgt[k] - cam[k] for k in range(3)]
vp = VP(cam, d)
for name, s in (("click plane in front of the scan/mesh", 0.4), ("click plane behind the scan/mesh", 1.7)):
    P = P3(*[cam[k] + s * d[k] for k in range(3)])
    q = ns["mesh_ray_point"](mesh, ns["cursor_ray"](vp, P))
    err = None if q is None else math.sqrt(sum((getattr(q, c) - tgt[k]) ** 2 for k, c in enumerate("XYZ")))
    chk(name + ": the point ON the scan/mesh", q is not None and err < 0.05, "(err %s mm)" % err)
    chk("   ... and on its surface", q is not None and abs(q.Z - surf(q.X, q.Y)) < 0.2)

# parallel view straight down
vpp = VP((0, 0, 500.0), (0, 0, -1.0), persp=False)
P = P3(30.0, 70.0, -40.0)                        # click plane under the plate: the ray still finds it
q = ns["mesh_ray_point"](mesh, ns["cursor_ray"](vpp, P))
chk("parallel view: straight down onto the scan/mesh", q is not None and abs(q.X - 30) < 1e-6 and abs(q.Y - 70) < 1e-6
    and abs(q.Z - surf(30.0, 70.0)) < 0.2)

# beside the scan/mesh
P = P3(*[cam[k] + 0.5 * (d[k] + (300.0 if k == 0 else 0.0)) for k in range(3)])
chk("cursor beside the scan/mesh -> None", ns["mesh_ray_point"](mesh, ns["cursor_ray"](vp, P)) is None)
chk("no ray -> None", ns["mesh_ray_point"](mesh, None) is None)

# only the window point
vp.f = (cam, tgt)
q = ns["mesh_ray_point"](mesh, ns["cursor_ray"](vp, None, types.SimpleNamespace(X=10, Y=20)))
chk("window point only: the frustum line", q is not None and abs(q.X - tgt[0]) < 0.05 and abs(q.Y - tgt[1]) < 0.05)

# the session uses exactly these two (no other way to a mesh point)
src = open(SCRIPT).read()
chk("sessions take mesh points only through mesh_under / mesh_at_click",
    "return mesh_ray_point(self.mesh, cursor_ray(vp, P, p2))" in src
    and "return mesh_ray_point(self.mesh, click_ray(gp))" in src)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
