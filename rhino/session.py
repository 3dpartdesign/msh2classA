# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
class Session(SettingsMixin, ScanMixin, PolygonMixin, DrawMixin, BuildMixin, SmoothMixin, ViewMixin):
    """One msh2classA run: the mesh stays loaded, patches are added one by one."""

    def __init__(self, mesh):
        self.mesh = mesh
        try:      # face normals are needed by every scan lookup (also before the first build)
            if mesh.FaceNormals.Count != mesh.Faces.Count:
                mesh.FaceNormals.ComputeFaceNormals()
        except Exception:
            pass
        mb = mesh.GetBoundingBox(False)
        self.size = mb.Diagonal.Length
        self.tol_on = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 1e-6)
        self.cands = []
        self.patches = []          # list of lists of object ids
        self.reset_polygon()
        self.hover = None
        self.hover_srf = None
        self.smooth_undo = []       # steps: list of (object id, old geometry)
        self.n_wall = 0; self.n_back = 0; self.refs = []; self.ray_back = 1.0
        self.mouse_vp = None
        self.down = None
        self.fit_pts = []
        self.refresh()

    def surface_report(self):
        """Which surfaces take part (said at the start of every PATCHES run):
        EVERY visible surface near the scan/mesh, on any layer - not only the
        current layer, where the new patches go."""
        def layer_of(oid):
            try:
                o = sc.doc.Objects.FindId(oid)
                return sc.doc.Layers[o.Attributes.LayerIndex].FullPath
            except Exception:
                return "?"
        mine = []; other = []; fixed = []
        for (oid, brep, bb, ok) in self.surfs:
            try: built = rs.GetUserText(oid, "msh2classA_fit") is not None
            except Exception: built = False
            (fixed if not ok else mine if built else other).append(oid)
        try:
            cur = sc.doc.Layers[sc.doc.Layers.CurrentLayerIndex].FullPath
            dest = patch_layer(False)[1]
        except Exception:
            cur = dest = "?"
        where = ("the CURRENT layer '{}'".format(cur) if dest == cur else
                 "'{}' (the current layer '{}' is a scan/mesh or photo layer)".format(dest, cur))
        print("  New patches go to {}. Every VISIBLE surface near the scan/mesh takes part, on any layer but "
              "the scan/mesh and photo layers: {} msh2classA patch(es), {} other(s)".format(
                  where, len(mine), len(other) + len(fixed)))
        lays = lambda ids: ", ".join(sorted(set(layer_of(i) for i in ids))[:5])
        if other:
            print("  [NOTE] {} surface(s) NOT built by msh2classA take part fully (layer: {}) - new patches match them "
                  "and the buttons of a neighbour may move their CVs (UndoSrfEdit brings them back). They have no "
                  "stored G levels: GlobalMatch leaves their seams as they are".format(len(other), lays(other)))
        if fixed:
            print("  [NOTE] {} surface(s) can not be changed (trimmed, several spans, rational or over 8 CV per "
                  "direction; layer: {}) - only their EDGES are used: new patches match them as they are, a bad "
                  "edge gives a bad seam. Rebuild (button on it) / RebuildAll (Surfaces) makes untrimmed ones clean "
                  "{}x{} CV patches that take part".format(len(fixed), lays(fixed), REBUILD_CV, REBUILD_CV))
        if other or fixed:
            print("  To keep surfaces out: HIDE their layer before msh2classA (LOCK it: only their edges are used, "
                  "never changed)")

    def refresh(self):
        self.cands = collect_candidates(self.mesh)
        self.surfs = collect_surfaces(self.mesh)
        self.cvnets = {}            # id -> (CV grid, front normal grid)
        for (oid, brep, bb, ok) in self.surfs:
            try:
                ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
                nu, nv = ns.Points.CountU, ns.Points.CountV
                try:
                    sgn = -1.0 if rs.GetUserText(oid, "msh2classA_side") == "-1" else 1.0
                except Exception:
                    sgn = 1.0
                du_, dv_ = ns.Domain(0), ns.Domain(1)
                net = []; nrm = []
                for i in range(nu):
                    row = []; nrow = []
                    for j in range(nv):
                        row.append(ns.Points.GetControlPoint(i, j).Location)
                        u = du_.ParameterAt(i / float(max(nu - 1, 1))); v = dv_.ParameterAt(j / float(max(nv - 1, 1)))
                        n_ = ns.NormalAt(u, v)
                        nrow.append((n_.X * sgn, n_.Y * sgn, n_.Z * sgn))
                    net.append(row); nrm.append(nrow)
                self.cvnets[oid] = (net, nrm)
            except Exception:
                pass
        try:
            nps = self._npatches()
        except Exception:
            nps = []
        npids = set(i for (i, b) in nps)
        try:
            self.markers = [(p, t, col(*c)) for (p, t, c) in
                            analyze_seams([c for c in self.cands if c.oid not in npids],
                                          sc.doc.ModelAbsoluteTolerance, 1.0 / (100.0 * self.size))]
        except Exception:
            self.markers = []
        if nps:                         # seams of the NPatches (trimmed: measured along the neighbours' edges)
            try:
                self.markers += [(p, t, col(*c)) for (p, t, c) in
                                 analyze_npatch_seams(self.cands, nps, sc.doc.ModelAbsoluteTolerance,
                                                      1.0 / (100.0 * self.size), 0.5 * self.s_tol())]
            except Exception:
                pass
        self.qcache = {}
        self.centre_cache = {}          # patch centres move with every edit
        self.matcap_meshes = {}
        self.graph_cache = {}           # curvature combs / deviation dots: made again after every change
        self.dev_cache = {}
        if "analysis_state" not in self.__dict__:
            self.read_analysis()
        self.apply_analysis(new_only=True)      # new / changed patches get what is on
        if getattr(self, "mirror_view", False):
            self.update_mirror_view()


def smooth_curve(pts):
    """Fair single-span curve through clicked points: ends exact, inner
    points approximated (no waves from imprecise clicks).
    2 pts -> line, 3 -> parabola through all, 4 -> cubic through all,
    5+ -> least-squares cubic."""
    if len(pts) == 2:
        return rg.LineCurve(pts[0], pts[1])
    deg = min(3, len(pts) - 1)
    tp = [T(p) for p in pts]
    try:
        C = fit_curve(tp, chord_params(tp), BDir(deg, 1))
        crv = rg.NurbsCurve.Create(False, deg, [P3(c) for c in C])
        if crv is not None and crv.IsValid:
            return crv
    except Exception:
        pass
    return rg.PolylineCurve(pts)

def draw_smooth(d, pts, color, thick):
    if len(pts) < 3:
        if len(pts) == 2: d.DrawLine(pts[0], pts[1], color, thick)
        return
    crv = smooth_curve(pts)
    if crv is None:
        draw_poly(d, pts, color, thick); return
    dom = crv.Domain
    draw_poly(d, [crv.PointAt(dom.ParameterAt(i / 40.0)) for i in range(41)], color, thick)

def draw_poly(d, pts, color, thick):
    for k in range(len(pts) - 1):
        d.DrawLine(pts[k], pts[k + 1], color, thick)

def make_nurbs(du, dv, P, ox):
    ns = rg.NurbsSurface.Create(3, False, du.p + 1, dv.p + 1, du.n, dv.n)
    if ns is None: return None
    for i, k in enumerate(du.U[1:-1]): ns.KnotsU[i] = k
    for i, k in enumerate(dv.U[1:-1]): ns.KnotsV[i] = k
    for i in range(du.n):
        for j in range(dv.n):
            x, y, z = P[i * dv.n + j]
            ns.Points.SetControlPoint(i, j, rg.ControlPoint(rg.Point3d(x + ox[0], y + ox[1], z + ox[2])))
    return ns

