# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Patches as real FreeCAD B-spline faces (Part::Feature)."""
import json

import FreeCAD as App
import Part

from .core import builder as m2b


def is_patch(obj):
    return hasattr(obj, "M2CA_Patch") and obj.M2CA_Patch


def patch_objects(doc):
    return [o for o in doc.Objects if is_patch(o)]


def make_patch(doc, res, name="Patch"):
    pu, pv, P = res["pu"], res["pv"], res["P"]
    nu, nv = pu + 1, pv + 1
    poles = [[App.Vector(*P[i * nv + j]) for j in range(nv)] for i in range(nu)]
    s = Part.BSplineSurface()
    s.buildFromPolesMultsKnots(poles, [nu, nu], [nv, nv], [0.0, 1.0], [0.0, 1.0], False, False, pu, pv)
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = s.toShape()
    obj.addProperty("App::PropertyBool", "M2CA_Patch", "msh2classA", "made by msh2classA")
    obj.addProperty("App::PropertyString", "M2CA_Levels", "msh2classA", "G0/G1/G2 per side it was built with")
    obj.M2CA_Patch = True
    obj.M2CA_Levels = json.dumps(res.get("levels", {}))
    if App.GuiUp:
        try:
            obj.ViewObject.ShapeColor = (0.85, 0.85, 0.9)
            obj.ViewObject.ControlPoints = True
        except Exception:
            pass
    return obj


def as_core_patch(obj):
    """-> core Patch (world CVs) or None if it is not a single-span B-spline face."""
    try:
        face = obj.Shape.Faces[0]
        s = face.Surface
        if s.TypeId != "Part::GeomBSplineSurface":
            return None
        pu, pv = s.UDegree, s.VDegree
        if s.NbUPoles != pu + 1 or s.NbVPoles != pv + 1:
            return None
        poles = s.getPoles()
        P = [tuple(poles[i][j]) for i in range(pu + 1) for j in range(pv + 1)]
        # the face may sit at a Placement that the surface does not carry
        u0, u1, v0, v1 = s.bounds()
        if (face.valueAt(u0, v0) - s.value(u0, v0)).Length > 1e-7:
            pl = obj.Placement
            P = [tuple(pl.multVec(App.Vector(*q))) for q in P]
        try:
            lv = dict((int(k), v) for k, v in json.loads(obj.M2CA_Levels or "{}").items())
        except Exception:
            lv = {}
        return m2b.Patch(pu, pv, P, lv, obj.Name)
    except Exception:
        return None


def export(objs, path):
    """IGES / STEP / BREP by the file extension (FreeCAD's own exporter)."""
    Part.export(objs, path)
