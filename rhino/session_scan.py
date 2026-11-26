# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# Scan sampling: only faces turned like the patch
# -----------------------------------------------------------------------------

class ScanMixin(object):
    def prep_front(self):
        """Thin parts are often scanned on both sides. Every clicked point hit a
        mesh face on the side you work on; its normal tells which side that is.
        Mesh samples are taken only from faces turned the same way as the
        nearest clicked face, otherwise by a ray along that normal."""
        m = self.mesh
        try:
            if m.FaceNormals.Count != m.Faces.Count:
                m.FaceNormals.ComputeFaceNormals()
        except Exception:
            pass
        clicked = [c["pt"] for c in self.corners]
        for pts in self.shape.values():
            clicked += list(pts)
        self.refs = []
        for p in clicked:
            mp = m.ClosestMeshPoint(p, 0.0)
            if mp is None: continue
            n = m.FaceNormals[mp.FaceIndex]
            self.refs.append((T(p), v_unit((n.X, n.Y, n.Z))))
        span = max(self.corners[i]["pt"].DistanceTo(self.corners[j]["pt"]) for i in range(4) for j in range(4))
        c_ = [c["pt"] for c in self.corners]
        width = min(0.5 * (c_[0].DistanceTo(c_[1]) + c_[2].DistanceTo(c_[3])),
                    0.5 * (c_[1].DistanceTo(c_[2]) + c_[3].DistanceTo(c_[0])))
        mm = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
        # how far to look for the visible side of a thin wall: about the patch
        # WIDTH (a 5 mm fillet 300 mm long must not search 75 mm away)
        self.ray_back = min(0.25 * span + mm, max(1.5 * width, 3.0 * mm))
        self.n_back = 0
        self.n_wall = 0

    def scan_side_sign(self, ns):
        """+1 if the surface normal points to the side of the mesh we work on
        (normal of the nearest clicked face), else -1. Stored on the object so
        Smooth / Pull2Msh later sample the right side of a thin part."""
        u = ns.Domain(0).Mid; v = ns.Domain(1).Mid
        n = T(ns.NormalAt(u, v)); r = self.ref_normal(ns.PointAt(u, v))
        return -1 if (r is not None and v_dot(n, r) < 0) else 1

    def patch_sign(self, coons, ox):
        """+1/-1: how the patch parameter normal relates to the side you work
        on, decided ONCE from all four corners (their clicked mesh faces).
        Per-sample 'nearest click' orientation fails on narrow fillets /
        blends, where the normal turns ~90 deg between the corners."""
        votes = 0.0
        for (u, v) in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
            h = 1e-3
            cu = v_sub(coons(min(1.0, u + h), v), coons(max(0.0, u - h), v))
            cv = v_sub(coons(u, min(1.0, v + h)), coons(u, max(0.0, v - h)))
            n = v_cross(cu, cv)
            p = coons(u, v)
            mp = self.mesh.ClosestMeshPoint(rg.Point3d(p[0] + ox[0], p[1] + ox[1], p[2] + ox[2]), 0.0)
            if mp is None or v_len(n) < 1e-300: continue
            f = self.mesh.FaceNormals[mp.FaceIndex]
            votes += v_dot(v_unit(n), (f.X, f.Y, f.Z))
        return -1.0 if votes < 0 else 1.0

    def ray_hit(self, o, d):
        """First hit of the ray o + t*d on the mesh -> (t, face index or None)."""
        ray = rg.Ray3d(o, rg.Vector3d(d[0], d[1], d[2]))
        try:
            res = rg.Intersect.Intersection.MeshRay(self.mesh, ray)
            if isinstance(res, tuple):
                t, faces = res[0], res[1]
                return t, (faces[0] if faces is not None and len(faces) else None)
        except Exception:
            res = None
        try:
            t, faces = rg.Intersect.Intersection.MeshRay(self.mesh, ray, None)
            return t, (faces[0] if faces is not None and len(faces) else None)
        except Exception:
            return rg.Intersect.Intersection.MeshRay(self.mesh, ray), None

    def front_along(self, p, nref, min_dot, maxd):
        """From p along +nref: first mesh face turned like nref (skipping the
        back faces of a thin wall on the way), within maxd."""
        eps = 1e-6 * max(self.size, 1.0)
        o = rg.Point3d(p.X, p.Y, p.Z); gone = 0.0
        for _ in range(6):
            t, fi = self.ray_hit(o, nref)
            if t is None or t < 0: return None
            gone += t
            if gone > maxd: return None
            hit = rg.Point3d(o.X + nref[0] * t, o.Y + nref[1] * t, o.Z + nref[2] * t)
            if fi is None:
                mh = self.mesh.ClosestMeshPoint(hit, 0.0)
                fi = mh.FaceIndex if mh is not None else None
            if fi is not None:
                n = self.mesh.FaceNormals[fi]
                if n.X * nref[0] + n.Y * nref[1] + n.Z * nref[2] >= min_dot:
                    return hit
            o = rg.Point3d(hit.X + nref[0] * eps, hit.Y + nref[1] * eps, hit.Z + nref[2] * eps)
            gone += eps
        return None

    def ref_normal(self, p):
        tp = T(p); best = None
        for (q, n) in getattr(self, "refs", []):
            d = v_dist(tp, q)
            if best is None or d < best[0]:
                best = (d, n)
        return best[1] if best else None

    def face_dot(self, mp, nref):
        n = self.mesh.FaceNormals[mp.FaceIndex]
        return n.X * nref[0] + n.Y * nref[1] + n.Z * nref[2]

    def scan_pt(self, p, nsurf=None, fit=False):
        """Mesh point for p, ONLY from faces turned the same way as the patch.
        nsurf : local patch normal (fit and start surface); otherwise the
                normal of the nearest clicked point is used.
        fit   : during the fit nothing else is tried - an angled or opposite
                face (rib, neighbouring wall, back side) just drops the sample.
        Otherwise (projecting sides / start) a ray along the normal is tried,
        and its hit is accepted only if THAT face is turned the right way."""
        m = self.mesh
        mp = m.ClosestMeshPoint(p, 0.0)
        nref = self.ref_normal(p)
        if nsurf is not None and v_len(nsurf) > 1e-12:
            sg = getattr(self, "nsign", None)
            if sg is not None:
                nsurf = v_mul(nsurf, sg)                   # one orientation for the whole patch
            elif nref is not None and v_dot(nsurf, nref) < 0:
                nsurf = v_mul(nsurf, -1.0)                 # orient to the clicked side
            nref = v_unit(nsurf)
        if nref is None:
            return mp.Point if mp is not None else None
        min_dot = 0.7 if fit else 0.5
        if mp is not None:
            dn = self.face_dot(mp, nref)
            if dn >= min_dot:
                return mp.Point
            if dn > -0.3:
                self.n_wall += 1
                return None                               # wall / rib (angled face): skip
            # nearest face is turned AWAY = back side of a thin part: the point
            # is inside the wall or already through it. The visible side is
            # forward along the normal - walk there past the back faces.
            hit = self.front_along(p, nref, min_dot, self.ray_back)
            if hit is not None:
                self.n_back += 1
                return hit
            if fit:
                self.n_wall += 1
                return None
        r = self.ray_back                                 # back side of a thin part
        o = rg.Point3d(p.X + nref[0] * r, p.Y + nref[1] * r, p.Z + nref[2] * r)
        ray = rg.Ray3d(o, rg.Vector3d(-nref[0], -nref[1], -nref[2]))
        t = rg.Intersect.Intersection.MeshRay(m, ray)
        if t >= 0:
            hit = ray.PointAt(t)
            mh = m.ClosestMeshPoint(hit, 0.0)
            if mh is not None and self.face_dot(mh, nref) >= min_dot:
                self.n_back += 1
                return hit
        return None


