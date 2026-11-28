"""msh2classA tests - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign (GPL-3.0, see NOTICE).

A small stand-in for RhinoCommon / rhinoscriptsyntax / scriptcontext, enough
to RUN the mesh tools of the built msh2classA.py (Center, Floor, Align, Setup)
outside Rhino, with scripted clicks. Geometry is computed for real (numpy);
mesh queries use core/meshgrid.TriMesh."""
import math
import sys
import types
import uuid

import numpy as np

import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from core.meshgrid import TriMesh  # noqa: E402


# ----------------------------------------------------------------- events
class Event(object):
    def __init__(self): self.handlers = []
    def __iadd__(self, h): self.handlers.append(h); return self
    def __isub__(self, h):
        if h in self.handlers: self.handlers.remove(h)
        return self


# ----------------------------------------------------------------- geometry
class Vector3d(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if isinstance(x, (Vector3d, Point3d)): x, y, z = x.X, x.Y, x.Z
        self.X, self.Y, self.Z = float(x), float(y), float(z)
    def _a(self): return np.array([self.X, self.Y, self.Z])
    def __add__(self, o): return Vector3d(self.X + o.X, self.Y + o.Y, self.Z + o.Z)
    def __sub__(self, o): return Vector3d(self.X - o.X, self.Y - o.Y, self.Z - o.Z)
    def __neg__(self): return Vector3d(-self.X, -self.Y, -self.Z)
    def __mul__(self, o):
        if isinstance(o, (Vector3d, Point3d)): return self.X * o.X + self.Y * o.Y + self.Z * o.Z
        return Vector3d(self.X * o, self.Y * o, self.Z * o)
    __rmul__ = __mul__
    def __truediv__(self, s): return Vector3d(self.X / s, self.Y / s, self.Z / s)
    __div__ = __truediv__
    @property
    def Length(self): return math.sqrt(self.X ** 2 + self.Y ** 2 + self.Z ** 2)
    def Unitize(self):
        L = self.Length
        if L < 1e-300: return False
        self.X /= L; self.Y /= L; self.Z /= L; return True
    def Transform(self, xf):
        v = xf.M[:3, :3].dot(self._a()); self.X, self.Y, self.Z = v
    @staticmethod
    def CrossProduct(a, b): return Vector3d(*np.cross(a._a(), b._a()))
    def __repr__(self): return "V(%.4f, %.4f, %.4f)" % (self.X, self.Y, self.Z)


class Point3d(Vector3d):
    @property
    def IsValid(self): return all(math.isfinite(c) for c in (self.X, self.Y, self.Z))
    def __add__(self, o): return Point3d(self.X + o.X, self.Y + o.Y, self.Z + o.Z)
    def __sub__(self, o):
        if isinstance(o, Point3d): return Vector3d(self.X - o.X, self.Y - o.Y, self.Z - o.Z)
        return Point3d(self.X - o.X, self.Y - o.Y, self.Z - o.Z)
    def __mul__(self, s): return Point3d(self.X * s, self.Y * s, self.Z * s)
    __rmul__ = __mul__
    def __truediv__(self, s): return Point3d(self.X / s, self.Y / s, self.Z / s)
    __div__ = __truediv__
    def DistanceTo(self, o): return math.sqrt((self.X - o.X) ** 2 + (self.Y - o.Y) ** 2 + (self.Z - o.Z) ** 2)
    def Transform(self, xf):
        v = xf.M.dot(np.array([self.X, self.Y, self.Z, 1.0])); self.X, self.Y, self.Z = v[:3] / v[3]
    def __repr__(self): return "P(%.4f, %.4f, %.4f)" % (self.X, self.Y, self.Z)


class _ClassProp(object):
    def __init__(self, f): self.f = f
    def __get__(self, obj, cls): return self.f(cls)


Point3d.Origin = _ClassProp(lambda cls: Point3d(0, 0, 0))


class Transform(object):
    def __init__(self, v=0.0):
        if isinstance(v, Transform): self.M = v.M.copy()
        else: self.M = np.eye(4) * float(v) if v else np.zeros((4, 4))
    Identity = _ClassProp(lambda cls: Transform(1.0))
    def __getitem__(self, ij): return float(self.M[ij[0], ij[1]])
    def __setitem__(self, ij, v): self.M[ij[0], ij[1]] = float(v)
    def __mul__(self, o):
        if isinstance(o, Transform):
            t = Transform(); t.M = self.M.dot(o.M); return t
        p = Point3d(o); p.Transform(self); return p
    def TryGetInverse(self):
        try:
            t = Transform(); t.M = np.linalg.inv(self.M); return True, t
        except Exception:
            return False, Transform(1.0)
    @staticmethod
    def Translation(v):
        t = Transform(1.0); t.M[:3, 3] = [v.X, v.Y, v.Z]; return t
    @staticmethod
    def _about(R, c):
        t = Transform(1.0); t.M[:3, :3] = R; cc = np.array([c.X, c.Y, c.Z]); t.M[:3, 3] = cc - R.dot(cc); return t
    @staticmethod
    def Rotation(a, b, c):
        if isinstance(a, (int, float)):                 # (angle, axis, center)
            ax = b._a() / np.linalg.norm(b._a()); x, y, z = ax; C, S = math.cos(a), math.sin(a); K = 1 - C
            R = np.array([[C + x * x * K, x * y * K - z * S, x * z * K + y * S],
                          [y * x * K + z * S, C + y * y * K, y * z * K - x * S],
                          [z * x * K - y * S, z * y * K + x * S, C + z * z * K]])
            return Transform._about(R, c)
        u = a._a() / np.linalg.norm(a._a()); v = b._a() / np.linalg.norm(b._a())   # (from, to, center)
        w = np.cross(u, v); s = np.linalg.norm(w); cth = float(u.dot(v))
        if s < 1e-15:
            if cth > 0: return Transform._about(np.eye(3), c)
            p = np.cross(u, [1, 0, 0]) if abs(u[0]) < 0.9 else np.cross(u, [0, 1, 0]); p /= np.linalg.norm(p)
            return Transform.Rotation(math.pi, Vector3d(*p), c)
        return Transform.Rotation(math.atan2(s, cth), Vector3d(*(w / s)), c)
    @staticmethod
    def Mirror(p, n):
        nn = n._a() / np.linalg.norm(n._a()); R = np.eye(3) - 2 * np.outer(nn, nn)
        return Transform._about(R, p)
    @staticmethod
    def Scale(p, f): return Transform._about(np.eye(3) * f, p)


class BoundingBox(object):
    def __init__(self, a=None, b=None):
        self.Min = a; self.Max = b
    Empty = _ClassProp(lambda cls: BoundingBox())
    @property
    def IsValid(self): return self.Min is not None
    def Union(self, o):
        if not o.IsValid: return
        if not self.IsValid: self.Min, self.Max = Point3d(o.Min), Point3d(o.Max); return
        self.Min = Point3d(min(self.Min.X, o.Min.X), min(self.Min.Y, o.Min.Y), min(self.Min.Z, o.Min.Z))
        self.Max = Point3d(max(self.Max.X, o.Max.X), max(self.Max.Y, o.Max.Y), max(self.Max.Z, o.Max.Z))
    @property
    def Center(self): return (self.Min + self.Max) * 0.5
    @property
    def Diagonal(self): return self.Max - self.Min
    def Transform(self, xf):
        pts = [Point3d(x, y, z) for x in (self.Min.X, self.Max.X) for y in (self.Min.Y, self.Max.Y) for z in (self.Min.Z, self.Max.Z)]
        for p in pts: p.Transform(xf)
        b = BoundingBox._of(pts); self.Min, self.Max = b.Min, b.Max
    @staticmethod
    def _of(pts):
        a = np.array([[p.X, p.Y, p.Z] for p in pts])
        return BoundingBox(Point3d(*a.min(0)), Point3d(*a.max(0)))


class _Verts(object):
    def __init__(self, m): self.m = m
    @property
    def Count(self): return len(self.m.V)
    def __len__(self): return len(self.m.V)
    def __getitem__(self, i): return Point3d(*self.m.V[i])
    def ToPoint3dArray(self): return [Point3d(*v) for v in self.m.V]


class _Faces(object):
    def __init__(self, m): self.m = m
    @property
    def Count(self): return len(self.m.T)


class _FN(object):
    def __init__(self, m): self.m = m
    @property
    def Count(self): return len(self.m.T)
    def ComputeFaceNormals(self): return True
    def __getitem__(self, i): return Vector3d(*self.m._tm().N[i])


class MeshPoint(object):
    def __init__(self, p, fi): self.Point = p; self.FaceIndex = fi


class Mesh(object):
    def __init__(self, V=None, T=None):
        self.V = [tuple(map(float, v)) for v in (V or [])]; self.T = [tuple(t) for t in (T or [])]; self._t = None
    Vertices = property(lambda self: _Verts(self))
    Faces = property(lambda self: _Faces(self))
    FaceNormals = property(lambda self: _FN(self))
    IsValid = True
    def _tm(self):
        if self._t is None: self._t = TriMesh(self.V, self.T)
        return self._t
    def Duplicate(self): return Mesh(self.V, self.T)
    def Transform(self, xf):
        a = np.hstack([np.array(self.V), np.ones((len(self.V), 1))]).dot(xf.M.T)
        self.V = [tuple(r[:3] / r[3]) for r in a]; self._t = None; return True
    def Append(self, o):
        n = len(self.V); self.V += o.V; self.T += [(a + n, b + n, c + n) for (a, b, c) in o.T]; self._t = None
    def RebuildNormals(self): return True
    def Compact(self): return True
    def GetBoundingBox(self, arg):
        pts = [Point3d(*v) for v in self.V]
        if isinstance(arg, Transform):
            for p in pts: p.Transform(arg)
        return BoundingBox._of(pts)
    def ClosestMeshPoint(self, p, maxd):
        r = self._tm().closest((p.X, p.Y, p.Z))
        if r is None: return None
        if maxd and maxd > 0 and math.sqrt(sum((r[0][i] - c) ** 2 for i, c in enumerate((p.X, p.Y, p.Z)))) > maxd:
            return None                                   # like Rhino: nothing within maxDistance
        return MeshPoint(Point3d(*r[0]), r[2])
    def ClosestPoint(self, p):
        r = self._tm().closest((p.X, p.Y, p.Z))
        return None if r is None else Point3d(*r[0])
    @staticmethod
    def CreateFromSphere(*a): return Mesh()


class PointCloud(object):
    def __init__(self, pts): self.pts = [Point3d(p) for p in pts]
    def GetPoints(self): return self.pts
    @property
    def Count(self): return len(self.pts)


class Ray3d(object):
    def __init__(self, o, d): self.Position = Point3d(o); self.Direction = Vector3d(d)
    def PointAt(self, t): return self.Position + self.Direction * t


class Line(object):
    def __init__(self, a, b): self.From = Point3d(a); self.To = Point3d(b)


class Plane(object):
    def __init__(self, o=None, n=None, y=None):
        self.Origin = Point3d(o) if o is not None else Point3d()
        if y is not None:                                   # (origin, x axis, y axis)
            xa = n._a() / np.linalg.norm(n._a()); ya = y._a() / np.linalg.norm(y._a())
            self.XAxis = Vector3d(*xa); self.YAxis = Vector3d(*ya); self.Normal = Vector3d(*np.cross(xa, ya))
        else:
            self.Normal = Vector3d(n) if n is not None else Vector3d(0, 0, 1)
    @staticmethod
    def FitPlaneToPoints(pts):
        a = np.array([[p.X, p.Y, p.Z] for p in pts]); c = a.mean(0)
        w, v = np.linalg.eigh((a - c).T.dot(a - c)); n = v[:, 0]
        return 0, Plane(Point3d(*c), Vector3d(*n))


class Sphere(object):
    def __init__(self, c, r): self.Center = c; self.Radius = r


class _Intersection(object):
    @staticmethod
    def MeshRay(mesh, ray, *rest):
        r = mesh._tm().ray((ray.Position.X, ray.Position.Y, ray.Position.Z),
                           (ray.Direction.X, ray.Direction.Y, ray.Direction.Z))
        return -1.0 if r is None else r[0]


# ----------------------------------------------------------------- document objects
class ObjectAttributes(object):
    def __init__(self):
        self.LayerIndex = 0; self.ColorSource = "layer"; self.ObjectColor = (0, 0, 0); self.strings = {}
    def Duplicate(self):
        a = ObjectAttributes(); a.LayerIndex = self.LayerIndex; a.ColorSource = self.ColorSource
        a.ObjectColor = self.ObjectColor; a.strings = dict(self.strings); return a
    def GetUserString(self, k): return self.strings.get(k)
    def SetUserString(self, k, v): self.strings[k] = v; return True


class _Piece(object):
    def __init__(self, mesh): self.Geometry = mesh


class InstanceDefinition(object):
    def __init__(self, name, meshes, index):
        self.Name = name; self.Id = uuid.uuid4(); self.Index = index; self.pieces = [_Piece(m) for m in meshes]
        self.IsDeleted = False; self.SourceArchive = ""
    @property
    def ObjectCount(self): return len(self.pieces)
    def Object(self, i): return self.pieces[i]


class _InstGeo(object):
    def __init__(self, o): self.o = o
    def GetBoundingBox(self, acc):
        bb = BoundingBox()
        for p in self.o.InstanceDefinition.pieces:
            bb.Union(p.Geometry.GetBoundingBox(self.o.InstanceXform))
        return bb


class InstanceObject(object):
    def __init__(self, idef, xf, attrs):
        self.Id = uuid.uuid4(); self.InstanceDefinition = idef; self.InstanceXform = Transform(xf); self.Attributes = attrs
        self.Visible = True
    Geometry = property(lambda self: _InstGeo(self))


class Layer(object):
    def __init__(self, path=None, idx=-1):
        self.FullPath = path; self.IsVisible = True; self.Index = idx; self.Name = path.split("::")[-1] if path else ""
        self.Id = uuid.uuid4(); self.ParentLayerId = None; self.Color = None
    def CommitChanges(self): return True


class _Layers(object):
    def __init__(self): self.items = []; self.CurrentLayerIndex = 0
    def __getitem__(self, i): return self.items[i]
    def FindByFullPath(self, p, d=-1):
        for L in self.items:
            if L.FullPath == p: return L.Index
        return -1
    def add(self, path):
        i = self.FindByFullPath(path)
        if i >= 0: return i
        self.items.append(Layer(path, len(self.items))); return len(self.items) - 1
    def SetCurrentLayerIndex(self, i, q): self.CurrentLayerIndex = i
    def Add(self, L):
        par = [x for x in self.items if L.ParentLayerId is not None and x.Id == L.ParentLayerId]
        L.FullPath = (par[0].FullPath + "::" if par else "") + L.Name
        L.Index = len(self.items); self.items.append(L); return L.Index


class ObjectEnumeratorSettings(object):
    def __init__(self): self.HiddenObjects = False; self.LockedObjects = False; self.ObjectTypeFilter = None


class _Objects(object):
    def __init__(self): self.items = {}
    def GetObjectList(self, s): return list(self.items.values())
    def FindId(self, i): return self.items.get(i)
    def Transform(self, i, xf, delete):
        o = self.items[i]
        if isinstance(o, CurveObject): o.curve.Transform(xf); return i
        if isinstance(o, PictureObject): o.Geometry.Transform(xf); return i
        o.InstanceXform = xf * o.InstanceXform; return i
    def AddPictureFrame(self, pl, path, asMesh, w, h, selfIllum, embed):
        O = Point3d(pl.Origin); X = O + pl.XAxis * w; Y = O + pl.YAxis * h
        o = PictureObject(PlaneSrf(O, X, Y), path); o.Attributes.LayerIndex = self.layer_now(); self.items[o.Id] = o
        return o.Id
    def layer_now(self): return SCRIPT.doc.Layers.CurrentLayerIndex
    def AddCurve(self, crv):
        o = CurveObject(crv); self.items[o.Id] = o; return o.Id
    def ModifyAttributes(self, o, a, q):
        oid = o.Id if hasattr(o, "Id") else o
        self.items[oid].Attributes = a.Duplicate(); return True
    def Delete(self, i, q): self.items.pop(i, None); return True
    def Hide(self, i, q):
        o = self.items.get(i)
        if o is None or not o.Visible: return False
        o.Visible = False; return True
    def Show(self, i, q):
        o = self.items.get(i)
        if o is None: return False
        o.Visible = True; return True
    def GetSelectedObjects(self, a, b): return [o for o in self.items.values() if getattr(o, "selected", False)]


class _IDefs(list):
    def Find(self, name):
        for d in self:
            if d.Name == name: return d
        return None
    @property
    def Count(self): return len(self)


class _Views(object):
    ActiveView = None
    def Redraw(self): pass
    def GetViewList(self, a, b): return []


class _Strings(object):
    """RhinoCommon StringTable (document user text)."""
    def __init__(self): self.d = {}
    def SetString(self, k, v): self.d[k] = v; return ""
    def GetValue(self, k): return self.d.get(k)
    def Delete(self, k): self.d.pop(k, None)


class Doc(object):
    def __init__(self, unit):
        self.Strings = _Strings()
        self.Objects = _Objects(); self.Layers = _Layers(); self.Layers.add("Default"); self.Views = _Views()
        self.InstanceDefinitions = _IDefs(); self.ModelUnitSystem = unit; self.RuntimeSerialNumber = 1
        self.ModelAbsoluteTolerance = 0.001


# ----------------------------------------------------------------- input (scripted)
class GetResult(object):
    Point = "Point"; Point2d = "Point2d"; Option = "Option"; Nothing = "Nothing"; Cancel = "Cancel"
    String = "String"; Object = "Object"; CustomMessage = "CustomMessage"; Number = "Number"


class Script(object):
    """Queue of scripted answers; resolve(action) turns ('click_on', name, local_pt) into a ray."""
    def __init__(self): self.q = []; self.prompts = []; self.resolver = None; self.ray = None
    def push(self, *acts): self.q.extend(acts)
    def pop(self, kind):
        if not self.q: raise RuntimeError("script ran out of answers at prompt: %s" % (self.prompts[-1] if self.prompts else "?"))
        return self.q.pop(0)


SCRIPT = Script()


class _Opt(object):
    def __init__(self, i, li=0): self.Index = i; self.CurrentListOptionIndex = li


class OptionDouble(object):
    def __init__(self, v, lo=None, hi=None): self.CurrentValue = float(v)


class OptionToggle(object):
    def __init__(self, v, a, b): self.CurrentValue = bool(v)


class _View(object):
    class _VP(object):
        """Camera = the scripted click ray: eye at its start, looking along it."""
        IsPerspectiveProjection = True; Name = "Perspective"
        CameraX = Vector3d(1, 0, 0); CameraY = Vector3d(0, 1, 0)
        @property
        def CameraLocation(self): return Point3d(SCRIPT.ray[0]) if SCRIPT.ray else Point3d(0, 0, 1e5)
        @property
        def CameraDirection(self):
            if not SCRIPT.ray: return Vector3d(0, 0, -1)
            a, b = SCRIPT.ray; return Vector3d(b.X - a.X, b.Y - a.Y, b.Z - a.Z)
        @property
        def CameraTarget(self): return Point3d(SCRIPT.ray[1]) if SCRIPT.ray else Point3d(0, 0, 0)
        def GetFrustumLine(self, x, y):
            a, b = SCRIPT.ray; return True, Line(a, b)
        def GetFrustum(self):
            return (True, -1.0, 1.0, -1.0, 1.0, 1.0, 1e7)
    ActiveViewport = _VP()
    def Redraw(self): pass


class GetPoint(object):
    def __init__(self):
        self.opts = []; self.MouseMove = Event(); self.MouseDown = Event(); self.DynamicDraw = Event(); self._opt = None
    def SetCommandPrompt(self, s): self.prompt = s; SCRIPT.prompts.append(s)
    def AcceptNothing(self, b): pass
    def AcceptCustomMessage(self, b): pass
    def AddOption(self, name): self.opts.append(name); return len(self.opts)
    def AddOptionDouble(self, name, od): self.opts.append(name); self.__dict__["od_" + name] = od; return len(self.opts)
    def AddOptionToggle(self, name, ot): self.opts.append(name); self.__dict__["ot_" + name] = ot; return len(self.opts)
    def AddOptionList(self, name, items, cur): self.opts.append(name); return len(self.opts)
    def PermitOrthoSnap(self, b): pass
    def PermitElevatorMode(self, b): pass
    def PermitObjectSnap(self, b): pass
    def Constrain(self, *a): pass
    def View(self): return _View()
    def Point2d(self): return types.SimpleNamespace(X=0, Y=0)
    def Point(self): return Point3d(SCRIPT.ray[1])
    def Option(self): return self._opt
    def Get(self, *a):
        act = SCRIPT.pop("point")
        kind = act[0]
        if kind in ("click_on", "click_photo"):
            SCRIPT.ray = SCRIPT.resolver(act); return GetResult.Point
        if kind == "opt":
            name = act[1]
            if name not in self.opts:
                raise RuntimeError("option %r not offered at prompt %r (offered: %s)" % (name, self.prompt, self.opts))
            i = self.opts.index(name) + 1
            if len(act) > 2:
                if ("od_" + name) in self.__dict__: self.__dict__["od_" + name].CurrentValue = act[2]
            self._opt = _Opt(i, act[2] if len(act) > 2 and isinstance(act[2], int) else 0)
            return GetResult.Option
        if kind == "enter": return GetResult.Nothing
        if kind == "esc": return GetResult.Cancel
        raise RuntimeError("unexpected scripted answer %r for prompt %r" % (act, self.prompt))


class GetString(object):
    def __init__(self): self.opts = []; self._oi = -1
    def SetCommandPrompt(self, s): SCRIPT.prompts.append(s); self.prompt = s
    def AcceptNothing(self, b): pass
    def AddOption(self, n): self.opts.append(n); return len(self.opts)
    def Get(self):
        act = SCRIPT.pop("string")
        if act[0] == "string": self.s = act[1]; return GetResult.String
        if act[0] == "enter": return GetResult.Nothing
        if act[0] == "opt":
            if act[1] not in self.opts:
                raise RuntimeError("option %r not offered at %r (%s)" % (act[1], self.prompt, self.opts))
            self._oi = self.opts.index(act[1]) + 1; return GetResult.Option
        if act[0] == "esc": return GetResult.Cancel
        raise RuntimeError("unexpected answer %r for prompt %r" % (act, getattr(self, "prompt", "?")))
    def Option(self): return types.SimpleNamespace(Index=self._oi)
    def StringResult(self): return self.s


class Curve(object):
    """A polyline curve (MeshScale)."""
    def __init__(self, pts): self.P = [Point3d(p) for p in pts]
    def GetLength(self): return sum(self.P[i].DistanceTo(self.P[i + 1]) for i in range(len(self.P) - 1))
    def PointAtNormalizedLength(self, t):
        L = self.GetLength() * t; acc = 0.0
        for i in range(len(self.P) - 1):
            d = self.P[i].DistanceTo(self.P[i + 1])
            if acc + d >= L:
                f = (L - acc) / d if d else 0.0; a, b = self.P[i], self.P[i + 1]
                return Point3d(a.X + (b.X - a.X) * f, a.Y + (b.Y - a.Y) * f, a.Z + (b.Z - a.Z) * f)
            acc += d
        return Point3d(self.P[-1])
    def Transform(self, xf):
        for p in self.P: p.Transform(xf)
        return True


class _Dom(object):
    Min = 0.0; Max = 1.0


class PlaneSrf(object):
    """A picture frame's flat surface: corners O (lower-left), X (lower-right), Y (upper-left)."""
    def __init__(self, O, X, Y): self.O, self.X, self.Y = Point3d(O), Point3d(X), Point3d(Y)
    @property
    def Faces(self): return [self]
    def UnderlyingSurface(self): return self
    def Domain(self, i): return _Dom()
    def PointAt(self, u, v):
        return Point3d(self.O.X + (self.X.X - self.O.X) * u + (self.Y.X - self.O.X) * v,
                       self.O.Y + (self.X.Y - self.O.Y) * u + (self.Y.Y - self.O.Y) * v,
                       self.O.Z + (self.X.Z - self.O.Z) * u + (self.Y.Z - self.O.Z) * v)
    def Transform(self, xf):
        for p in (self.O, self.X, self.Y): p.Transform(xf)
        return True


class PictureObject(object):
    def __init__(self, srf, path):
        self.Id = uuid.uuid4(); self.Geometry = srf; self.Attributes = ObjectAttributes(); self.path = path
        self.Visible = True; self.IsDeleted = False; self.InstanceDefinition = None


class CurveObject(object):
    def __init__(self, crv):
        self.Id = uuid.uuid4(); self.curve = crv; self.Attributes = ObjectAttributes()
        self.Visible = True; self.IsDeleted = False; self.InstanceDefinition = None; self.Geometry = crv


class GetObject(object):
    GeometryFilter = None
    def __init__(self): self.opts = []; self._opt = None
    def SetCommandPrompt(self, s): SCRIPT.prompts.append(s); self.prompt = s
    def EnablePreSelect(self, a, b): pass
    def AcceptCustomMessage(self, b): pass
    def AddOption(self, n): self.opts.append(n); return len(self.opts)
    def AddOptionList(self, n, items, i): self.opts.append(n); return len(self.opts)
    def Option(self): return self._opt
    def AcceptNothing(self, b): self.nothing_ok = b
    def Get(self):
        act = SCRIPT.pop("pick")
        if act[0] == "esc": return GetResult.Cancel
        if act[0] == "obj_enter":
            if not getattr(self, "nothing_ok", False): raise RuntimeError("Enter not accepted at %r" % self.prompt)
            return GetResult.Nothing
        if act[0] == "opt":
            if act[1] not in self.opts:
                raise RuntimeError("option %r not offered at %r (%s)" % (act[1], self.prompt, self.opts))
            self._opt = types.SimpleNamespace(Index=self.opts.index(act[1]) + 1, CurrentListOptionIndex=0)
            return GetResult.Option
        if act[0] != "pick": raise RuntimeError("expected a pick, got %r" % (act,))
        self._id = act[1]; return GetResult.Object
    def Object(self, i):
        o = SCRIPT.doc.Objects.FindId(self._id)
        return types.SimpleNamespace(ObjectId=o.Id, Curve=lambda: o.curve)


class GetNumber(object):
    def SetCommandPrompt(self, s): SCRIPT.prompts.append(s)
    def SetDefaultNumber(self, v): self.v = v
    def SetLowerLimit(self, v, strict): pass
    def Get(self):
        act = SCRIPT.pop("number")
        if act[0] == "esc": return GetResult.Cancel
        if act[0] == "enter": return GetResult.Number
        self.v = float(act[1]); return GetResult.Number
    def Number(self): return self.v


# ----------------------------------------------------------------- modules
UNIT_MM = {"Millimeters": 1.0, "Centimeters": 10.0, "Meters": 1000.0, "Inches": 25.4, "Feet": 304.8}


class UnitSystem(object):
    pass


for _n in UNIT_MM:
    setattr(UnitSystem, _n, _n)


def install(unit="Millimeters"):
    for k in [k for k in sys.modules if k == "Rhino" or k.startswith("Rhino.") or k in ("rhinoscriptsyntax", "scriptcontext", "System", "System.Drawing")]:
        del sys.modules[k]
    R = types.ModuleType("Rhino")
    rg = types.ModuleType("Rhino.Geometry")
    for n in ("Point3d", "Vector3d", "Transform", "BoundingBox", "Mesh", "PointCloud", "Ray3d", "Line", "Plane", "Sphere"):
        setattr(rg, n, globals()[n])
    rg.Intersect = types.SimpleNamespace(Intersection=_Intersection)
    rg.ControlPoint = object
    ro = types.ModuleType("Rhino.DocObjects")
    ro.InstanceObject = InstanceObject; ro.ObjectAttributes = ObjectAttributes; ro.ObjectEnumeratorSettings = ObjectEnumeratorSettings
    ro.ObjectType = types.SimpleNamespace(InstanceReference=4096, Mesh=32, Curve=4)
    ro.ObjectColorSource = types.SimpleNamespace(ColorFromObject="object", ColorFromParent="parent", ColorFromLayer="layer")
    ro.Layer = Layer
    ri = types.ModuleType("Rhino.Input"); ric = types.ModuleType("Rhino.Input.Custom")
    ri.GetResult = GetResult; ri.Custom = ric
    ric.GetPoint = GetPoint; ric.GetString = GetString; ric.OptionDouble = OptionDouble; ric.OptionToggle = OptionToggle
    ric.GetObject = GetObject; ric.GetNumber = GetNumber
    rd = types.ModuleType("Rhino.Display")
    rd.DisplayPipeline = type("DisplayPipeline", (), {"CalculateBoundingBox": Event(), "PostDrawObjects": Event(),
                                                       "DrawForeground": Event()})
    rd.DisplayMaterial = lambda *a: None; rd.PointStyle = types.SimpleNamespace(RoundSimple=0)
    R.Geometry = rg; R.DocObjects = ro; R.Input = ri; R.Display = rd
    R.UnitSystem = UnitSystem
    R.RhinoMath = types.SimpleNamespace(UnitScale=lambda a, b: UNIT_MM[a] / UNIT_MM[b])
    R.RhinoApp = types.SimpleNamespace(SetCommandPrompt=lambda s: None, Wait=lambda: None, EscapeKeyPressed=Event(),
                                       ExeVersion=8)
    R.UI = types.SimpleNamespace(StatusBar=types.SimpleNamespace(ShowProgressMeter=lambda *a: 0,
                                                                UpdateProgressMeter=lambda *a: 0,
                                                                HideProgressMeter=lambda *a: 0))
    sc = types.ModuleType("scriptcontext"); sc.sticky = {}; sc.doc = Doc(unit); sc.escape_test = lambda b=False: False
    SCRIPT.doc = sc.doc
    rs = types.ModuleType("rhinoscriptsyntax")
    rs.AddTextDot = lambda *a: None; rs.ObjectColor = lambda *a: None; rs.DeleteObject = lambda *a: True
    rs.UnselectAllObjects = lambda: 0; rs.MessageBox = lambda *a: 6
    rs.ListBox = lambda items, *a: SCRIPT.pop("list")[1]
    def _multi(items, *a):
        act = SCRIPT.pop("multi")
        if act[0] != "multi": raise RuntimeError("expected a multi list answer, got %r" % (act,))
        for x in act[1] or []:
            if x not in items: raise RuntimeError("%r not offered in %r" % (x, items))
        return act[1]
    rs.MultiListBox = _multi
    rs.OpenFileNames = lambda *a: SCRIPT.pop("files")[1]
    System = types.ModuleType("System"); sd = types.ModuleType("System.Drawing")
    sd.Color = types.SimpleNamespace(FromArgb=lambda *a: tuple(a), Black=(0, 0, 0)); sd.PointF = lambda x, y: (x, y)
    System.Drawing = sd; System.Guid = types.SimpleNamespace(NewGuid=uuid.uuid4)
    sys.modules.update({"Rhino": R, "Rhino.Geometry": rg, "Rhino.DocObjects": ro, "Rhino.Input": ri,
                        "Rhino.Input.Custom": ric, "Rhino.Display": rd, "scriptcontext": sc,
                        "rhinoscriptsyntax": rs, "System": System, "System.Drawing": sd})
    return sc


def load_script(path, unit="Millimeters"):
    sc = install(unit)
    ns = {"__name__": "m2ca_test", "__file__": path}
    exec(compile(open(path).read(), path, "exec"), ns)
    assert ns["IN_RHINO"], "fake Rhino not picked up"
    return ns, sc
