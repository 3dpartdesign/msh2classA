# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# View buttons (top right): what the patches look like while working
# -----------------------------------------------------------------------------

# (key, label, sticky default). Zebra / EMap / CV only on surfaces, Scan = the mesh.
VIEW_BTN = (("legend", "Legend", False), ("showcv", "CV pts", False), ("zebra", "Zebra", False),
            ("emap", "EMap", False), ("graph", "Graph", False), ("deviation", "Deviation", False),
            ("scanvis", "Scan/mesh", True), ("mirrorview", "Mirror", False),
            ("labels", "Labels", True))      # = SeamLabels: the CV-count and continuity bubbles of the patches


def analysis_modes():
    """Rhino's built-in Zebra and EMap analysis modes (None if not found)."""
    out = {}
    try:
        VAM = Rhino.Display.VisualAnalysisMode
        out["zebra"] = VAM.Find(VAM.RhinoZebraStripeAnalysisModeId)
        out["emap"] = VAM.Find(VAM.RhinoEmapAnalysisModeId)
    except Exception:
        pass
    return out


class ViewMixin(object):
    def view_on(self, key):
        if key == "scanvis":
            return getattr(self, "scan_visible", True)
        if key == "mirrorview":
            return bool(getattr(self, "mirror_view", False))
        if key in ("zebra", "emap"):
            return self.__dict__.setdefault("analysis_state", {}).get(key, False)
        dflt = dict((k, d) for (k, _, d) in VIEW_BTN)[key]
        return bool(opt_get(key, dflt))

    view_hot = None            # key of the view button under the mouse
    cur_pt = None              # scan point under the mouse (None = off the scan)
    last_mouse = None          # (viewport, x, y) of the last mouse move

    def _view_btn_rects(self, vp):
        """(key, label, x0, y0, w, h) in viewport pixels, top right."""
        try:
            W = vp.Size.Width
        except Exception:
            W = 1200
        for i, (key, label, _) in enumerate(VIEW_BTN):
            yield key, label, W - 112, 8 + 24 * i, 104, 20

    # ---- floating panel (Eto) at the top right of the view ----------------
    # Buttons drawn INTO the viewport only get clicks where Rhino delivers
    # them (not over the scan / background with the scan-constrained pick).
    # A small panel window always gets its clicks, and while the mouse is on
    # it the viewport sees no mouse moves: no hint work, no points.
    panel = None

    def open_panel(self):
        try:
            import Eto.Forms as ef
            import Eto.Drawing as ed
            import Rhino.UI as rui
        except Exception:
            self.panel = None; return False
        try:
            f = ef.Form()
            f.Title = "msh2classA view"
            f.WindowStyle = ef.WindowStyle.Utility
            f.Resizable = False
            f.Minimizable = False; f.Maximizable = False
            f.ShowInTaskbar = False
            try: f.Owner = rui.RhinoEtoApp.MainWindow       # stays above Rhino, not above other apps
            except Exception:
                try: f.Topmost = True
                except Exception: pass
            lay = ef.DynamicLayout()
            lay.Padding = ed.Padding(4); lay.Spacing = ed.Size(2, 2)
            self.panel_btns = {}
            for (key, label, _) in VIEW_BTN:
                b = ef.Button()
                b.Text = self._btn_text(key, label)
                b.Click += self._make_click(key)
                lay.AddRow(b)
                self.panel_btns[key] = (b, label)
            surface_tool_buttons(ef, lay)
            scan_tool_buttons(ef, lay)
            cad_services_button(ef, lay)
            f.Content = lay
            loc = sc.sticky.get("M2CA_panel_loc")
            f.Show()
            if loc:
                f.Location = ed.Point(loc[0], loc[1])
            else:
                try:
                    r = sc.doc.Views.ActiveView.ScreenRectangle
                    f.Location = ed.Point(int(r.Right - f.Width - 12), int(r.Top + 12))
                except Exception:
                    pass
            self.panel = f
            return True
        except Exception:
            self.panel = None
            return False

    def _btn_text(self, key, label):
        return "{}   {}".format(label, "ON" if self.view_on(key) else "off")

    def _make_click(self, key):
        def h(sender, e):
            try:
                self.toggle_view(key)
                self.update_panel()
            except Exception:
                pass
        return h

    def update_panel(self):
        for key, (b, label) in getattr(self, "panel_btns", {}).items():
            try: b.Text = self._btn_text(key, label)
            except Exception: pass

    def close_panel(self):
        f = self.panel
        if f is None: return
        try:
            sc.sticky["M2CA_panel_loc"] = (f.Location.X, f.Location.Y)
        except Exception:
            pass
        try: f.Close()
        except Exception: pass
        self.panel = None

    def view_button_at(self, vp, sx, sy):
        if self.panel is not None: return None
        for key, label, x0, y0, w, h in self._view_btn_rects(vp):
            if x0 <= sx <= x0 + w and y0 <= sy <= y0 + h:
                return key
        return None

    def on_move_view(self, sender, e):
        """Mouse position + hover of the view buttons."""
        try:
            self.last_mouse = (e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
            hot = self.view_button_at(e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
            self.cur_pt = None if hot else e.Point          # constrained to the scan by Rhino (fast)
        except Exception:
            hot = None; self.cur_pt = None
        if hot != self.view_hot:
            self.view_hot = hot
            try:
                Rhino.UI.MouseCursor.SetToolTip("click: {} on/off".format(
                    dict((k, l) for (k, l, _) in VIEW_BTN)[hot]) if hot else "")
            except Exception:
                pass

    def click_view_button(self):
        """GetPoint returned a point: was that click a view button (already
        switched in on_down)? Then it is not a corner."""
        if getattr(self, "view_clicked", False):
            self.view_clicked = False
            return True
        return False

    def draw_view_buttons(self, d, vp):
        if self.panel is not None: return
        for key, label, x0, y0, w, h in self._view_btn_rects(vp):
            on = self.view_on(key)
            hot = key == self.view_hot
            fill = col(40, 170, 70) if on else col(250, 250, 250)
            if hot: fill = col(90, 200, 110) if on else col(255, 225, 120)
            txt = "{}  {}".format(label, "ON" if on else "off")
            tc = col(255, 255, 255) if on else col(0, 0, 0)
            try:
                rect = sd.Rectangle(int(x0), int(y0), int(w), int(h))
                d.Draw2dRectangle(rect, col(30, 30, 30), 2 if hot else 1, fill)
            except Exception:
                pass
            try:
                d.Draw2dText(txt, tc, rg.Point2d(x0 + w / 2.0, y0 + h / 2.0), True, 12)
            except Exception:
                pass

    def toggle_view(self, key):
        if key == "scanvis":
            self.scan_visible = not getattr(self, "scan_visible", True)
            sid = getattr(self, "scan_id", None)
            if sid is not None:
                if self.scan_visible: sc.doc.Objects.Show(sid, True)
                else: sc.doc.Objects.Hide(sid, True)
            print("  Scan/mesh {} (points still snap onto it)".format("shown" if self.scan_visible else "hidden"))
        elif key == "mirrorview":
            if MIRROR_AXIS.get(self.s_mirror()) is None:
                print("  Mirror view: set MirrorPlane first (XZ / YZ / XY)")
            else:
                self.mirror_view = not getattr(self, "mirror_view", False)
                self.update_mirror_view()
                print("  Mirror view {}".format("ON: mirrored copies of the surfaces (removed again when off / at the end)"
                                               if self.mirror_view else "off: copies removed"))
        elif key == "emap" and matcap_path():
            # own EMap: the patches drawn with YOUR matcap (no Rhino EMap window)
            st = self.__dict__.setdefault("analysis_state", {})
            st["emap"] = not st.get("emap", False)
            self.matcap_meshes = {}
            print("  EMap {} (matcap {})".format("ON" if st["emap"] else "off", MATCAP_FILE))
        elif key in ("zebra", "emap"):
            st = self.__dict__.setdefault("analysis_state", {})
            st[key] = not st.get(key, False)
            self.apply_analysis(key)
        else:
            opt_set(key, not self.view_on(key))
        sc.doc.Views.Redraw()

    # ---- Graph: curvature combs on the patches (all settings automatic) ----
    GRAPH_ISO = (0.1, 0.3, 0.5, 0.7, 0.9)      # iso lines per direction
    GRAPH_N = 32                               # samples per iso line

    def _patch_nurbs(self, oid):
        obj = sc.doc.Objects.FindId(oid)
        if obj is None: return None
        g = obj.Geometry
        try:
            if isinstance(g, rg.Brep):
                return g.Faces[0].UnderlyingSurface().ToNurbsSurface()
            return g.ToNurbsSurface()
        except Exception:
            return None

    def draw_graph(self, d):
        """Curvature combs along iso lines of the patch under the mouse (as the
        CV points). The highest tooth (95 % of them lower) is half the mean
        length of the patch's edges; very sharp places are capped."""
        oid = getattr(self, "cv_srf", None)
        if oid is None: return
        cache = self.__dict__.setdefault("graph_cache", {})
        if oid not in cache:
            ns = self._patch_nurbs(oid)
            if ns is None: return
            lines = []; ks = []
            for dirn in (0, 1):
                dom_c = ns.Domain(1 - dirn)
                for f in self.GRAPH_ISO:
                    try:
                        crv = ns.IsoCurve(dirn, dom_c.ParameterAt(f))
                    except Exception:
                        crv = None
                    if crv is None: continue
                    dom = crv.Domain
                    cpar = dom_c.ParameterAt(f)
                    pts = []
                    for i in range(self.GRAPH_N + 1):
                        t = dom.ParameterAt(i / float(self.GRAPH_N))
                        kv = crv.CurvatureAt(t)
                        # only the NORMAL curvature (the bending of the surface): the sideways bending of
                        # the iso line inside the surface would lay the teeth flat
                        n_ = ns.NormalAt(t, cpar) if dirn == 0 else ns.NormalAt(cpar, t)
                        kn = kv * n_
                        v_ = rg.Vector3d(n_); v_ *= kn
                        pts.append((crv.PointAt(t), v_)); ks.append(abs(kn))
                    lines.append(pts)
            edges = []
            for dirn in (0, 1):
                dom_c = ns.Domain(1 - dirn)
                for f in (0.0, 1.0):
                    try: edges.append(ns.IsoCurve(dirn, dom_c.ParameterAt(f)).GetLength())
                    except Exception: pass
            mean_edge = sum(edges) / len(edges) if edges else 0.1 * self.size
            ks.sort()
            k95 = ks[int(0.95 * (len(ks) - 1))] if ks else 0.0
            cache[oid] = (lines, (0.5 * mean_edge / k95) if k95 > 1e-12 else 0.0, 0.75 * mean_edge)
        lines, scale, cap = cache[oid]
        c_comb = col(120, 120, 200); c_env = col(200, 30, 160)
        for pts in lines:
            env = []
            for (p, kv) in pts:
                v = rg.Vector3d(kv); v *= -scale            # comb on the outer side, like Rhino
                if v.Length > cap:
                    v.Unitize(); v *= cap
                tip = p + v
                d.DrawLine(p, tip, c_comb, 1)
                env.append(tip)
            if len(env) > 1:
                d.DrawPolyline(env, c_env, 2)

    # ---- Deviation: patch vs scan as coloured dots (all settings automatic) ----
    DEV_N = 17                                 # dots per direction and patch

    def _dev_colour(self, dev, scale):
        """0 = green, half MeshTol = yellow (+, mesh above the patch) / cyan
        (-, below), MeshTol and more = red / blue. Grey = no mesh."""
        if dev is None:
            return col(150, 150, 150)
        t = min(1.0, abs(dev) / scale)
        g = (0, 180, 70)
        mid, end = ((235, 190, 0), (220, 30, 30)) if dev > 0 else ((0, 190, 200), (30, 70, 230))
        a, b, f = (g, mid, 2.0 * t) if t <= 0.5 else (mid, end, 2.0 * t - 1.0)
        return col(*[int(a[i] + (b[i] - a[i]) * f) for i in range(3)])

    def draw_deviation(self, d):
        """Dots on every patch (DEV_N along its longer side, the same spacing
        across) coloured by the distance to the mesh against MeshTol: green 0,
        yellow / cyan half MeshTol, red / blue MeshTol and more (mesh above /
        below). Two labels for the whole model: the biggest + and - (red when
        more than MeshTol)."""
        cache = self.__dict__.setdefault("dev_cache", {})
        tol = max(self.s_tol(), 1e-9)
        ids = [s_[0] for s_ in getattr(self, "surfs", [])]
        for oid in ids:
            if oid in cache: continue
            ns = self._patch_nurbs(oid)
            if ns is None:
                cache[oid] = []; continue
            try:
                sgn = -1.0 if rs.GetUserText(oid, "msh2classA_side") == "-1" else 1.0
            except Exception:
                sgn = 1.0
            pts_ = self.patch_points(oid)
            if pts_:                                   # built through TempPoints: deviation AT those points
                dots = []
                for q in pts_:
                    qq = P3(q)
                    ok_, u_, v_ = ns.ClosestPoint(qq)
                    if not ok_: continue
                    n = ns.NormalAt(u_, v_); n *= sgn
                    dots.append((qq, (qq - ns.PointAt(u_, v_)) * n))
                cache[oid] = dots
                continue
            du_, dv_ = ns.Domain(0), ns.Domain(1)
            # DEV_N dots along the longer side of the patch, evenly spaced (any size, any units)
            def len_along(dirn):
                try:
                    return ns.IsoCurve(dirn, ns.Domain(1 - dirn).Mid).GetLength()
                except Exception:
                    return 1.0
            Lu_, Lv_ = len_along(0), len_along(1)
            Lm_ = max(Lu_, Lv_, 1e-12)
            nu_ = max(5, int(round(self.DEV_N * Lu_ / Lm_))); nv_ = max(5, int(round(self.DEV_N * Lv_ / Lm_)))
            dots = []
            saved = getattr(self, "refs", None); self.refs = []
            try:
                for i in range(nu_):
                    for j in range(nv_):
                        u = du_.ParameterAt(i / (nu_ - 1.0)); v = dv_.ParameterAt(j / (nv_ - 1.0))
                        p = ns.PointAt(u, v)
                        n = ns.NormalAt(u, v); n *= sgn
                        q = self.mesh.ClosestPoint(p)               # the real distance to the scan
                        if not q.IsValid or q.DistanceTo(p) > 20.0 * tol:
                            dots.append((p, None)); continue
                        dd = q.DistanceTo(p)
                        dots.append((p, dd if (q - p) * n >= 0 else -dd))     # + : the scan is above
            finally:
                self.refs = saved
            cache[oid] = dots
        hi = lo = None
        for oid in ids:
            for (p, dev) in cache.get(oid, []):
                if dev is None: continue
                if hi is None or dev > hi[1]: hi = (p, dev)
                if lo is None or dev < lo[1]: lo = (p, dev)
        scale = tol
        for oid in ids:
            for (p, dev) in cache.get(oid, []):
                c_ = self._dev_colour(dev, scale)
                try:
                    d.DrawPoint(p, Rhino.Display.PointStyle.RoundSimple, 3, c_)
                except Exception:
                    d.DrawPoint(p, c_)
        for (it, sign) in ((hi, "+"), (lo, "-")):
            if it is None: continue
            bg = col(220, 30, 30) if abs(it[1]) > tol else col(255, 255, 255)
            d.DrawDot(it[0], "{}{}".format(sign, fl(abs(it[1]))), bg, col(0, 0, 0))

    def draw_matcap(self, d):
        """Own EMap: every surface (and the mirror copies) drawn shaded with the
        matcap image as an environment map. Meshes are made once per change and
        lifted a hair off the surface on the side you look at (no flicker)."""
        if not (self.view_on("emap") and matcap_path()):
            return
        mat = getattr(self, "matcap_mat", None)
        if mat is None:
            try:
                mat = Rhino.Display.DisplayMaterial()
                mat.Diffuse = sd.Color.White
                mat.SetEnvironmentTexture(matcap_path(), True)
                mat.SetEnvironmentTexture(matcap_path(), False)
            except Exception:
                return
            self.matcap_mat = mat
        cache = self.__dict__.setdefault("matcap_meshes", {})
        ids = [s_[0] for s_ in getattr(self, "surfs", [])] + list(getattr(self, "mirror_ids", []))
        eps = 2e-5 * self.size
        for oid in ids:
            m_ = cache.get(oid)
            if m_ is None:
                obj = sc.doc.Objects.FindId(oid)
                if obj is None: continue
                g = obj.Geometry
                brep = g if isinstance(g, rg.Brep) else (g.ToBrep() if hasattr(g, "ToBrep") else None)
                if brep is None: continue
                mp = rg.MeshingParameters.QualityRenderMesh
                ms = rg.Mesh.CreateFromBrep(brep, mp)
                if not ms: continue
                m_ = rg.Mesh()
                for x in ms: m_.Append(x)
                m_.Normals.ComputeNormals()
                try:
                    sgn = -1.0 if rs.GetUserText(oid, "msh2classA_side") == "-1" else 1.0
                except Exception:
                    sgn = 1.0
                for a in range(m_.Vertices.Count):
                    v = m_.Vertices[a]; n = m_.Normals[a]
                    m_.Vertices.SetVertex(a, v.X + n.X * eps * sgn, v.Y + n.Y * eps * sgn, v.Z + n.Z * eps * sgn)
                cache[oid] = m_
            try:
                d.DrawMeshShaded(m_, mat)
            except Exception:
                pass

    def read_analysis(self):
        """At start: the buttons show what is really on (Zebra / EMap may be
        switched on before the command) - nothing is changed here."""
        st = self.__dict__.setdefault("analysis_state", {})
        for key, mode in analysis_modes().items():
            if mode is None: continue
            if key == "emap" and matcap_path():
                st[key] = False; continue          # own matcap EMap, starts off
            on = False
            for (oid, brep, bb, ok) in getattr(self, "surfs", []):
                obj = sc.doc.Objects.FindId(oid)
                try:
                    if obj is not None and obj.InVisualAnalysisMode(mode):
                        on = True; break
                except Exception:
                    pass
            st[key] = on

    def update_mirror_view(self):
        """Real mirrored copies of the surfaces (so Zebra / EMap show the
        symmetry line too), tagged and never used as neighbours. Rebuilt
        after every change; deleted when switched off and at the end."""
        self.clear_mirror_view()
        ax = MIRROR_AXIS.get(self.s_mirror())
        if not getattr(self, "mirror_view", False) or ax is None:
            return
        nrm = [0.0, 0.0, 0.0]; nrm[ax] = 1.0
        xf = rg.Transform.Mirror(rg.Point3d(0, 0, 0), rg.Vector3d(nrm[0], nrm[1], nrm[2]))
        modes = analysis_modes()
        ids = []
        for (oid, brep, bb, ok) in getattr(self, "surfs", []):
            obj = sc.doc.Objects.FindId(oid)
            if obj is None: continue
            g = obj.Geometry.Duplicate()
            if not g.Transform(xf): continue
            # a mirror turns the surface inside out: flip it back so its normal
            # points to the same (outer) side as the original
            try:
                if isinstance(g, rg.Brep):
                    g.Flip()
                elif isinstance(g, rg.Surface):
                    r_ = g.Reverse(0)
                    if r_ is not None: g = r_
            except Exception:
                pass
            at = obj.Attributes.Duplicate()
            at.SetUserString(PREVIEW_KEY, "1")
            nid = sc.doc.Objects.Add(g, at)
            if nid is None or str(nid) == "00000000-0000-0000-0000-000000000000": continue
            ids.append(nid)
            for key, mode in modes.items():
                if mode is not None and self.view_on(key):
                    try: sc.doc.Objects.FindId(nid).EnableVisualAnalysisMode(mode, True)
                    except Exception: pass
        self.mirror_ids = ids
        sc.doc.Views.Redraw()

    def clear_mirror_view(self):
        ids = list(getattr(self, "mirror_ids", []))
        try:
            s = ro.ObjectEnumeratorSettings(); s.HiddenObjects = True; s.LockedObjects = True
            for obj in sc.doc.Objects.GetObjectList(s):   # also stale ones (crash, older run)
                if is_preview(obj) and obj.Id not in ids: ids.append(obj.Id)
        except Exception:
            pass
        for i in ids:
            try: sc.doc.Objects.Delete(i, True)
            except Exception: pass
        self.mirror_ids = []

    def apply_analysis(self, only=None, new_only=False):
        """Zebra / EMap of the surfaces near the mesh (never the mesh) as the
        buttons say. new_only: just switch it ON on surfaces that lack it (new
        or changed patches) - never switches anything off by itself."""
        for key, mode in analysis_modes().items():
            if mode is None or (only is not None and key != only): continue
            if key == "emap" and matcap_path(): continue     # own matcap, not Rhino's EMap
            want = self.view_on(key)
            if new_only and not want: continue
            ids_ = [s_[0] for s_ in getattr(self, "surfs", [])] + list(getattr(self, "mirror_ids", []))
            for oid in ids_:
                obj = sc.doc.Objects.FindId(oid)
                if obj is None: continue
                try:
                    if obj.InVisualAnalysisMode(mode) != want:
                        obj.EnableVisualAnalysisMode(mode, want)
                except Exception:
                    pass

    def restore_view(self):
        """At the end: panel closed, the mesh shown again. Zebra / EMap stay as
        the buttons left them (as they were, if you did not touch them)."""
        self.close_panel()
        self.mirror_view = False
        self.clear_mirror_view()
        sid = getattr(self, "scan_id", None)
        if sid is not None and not getattr(self, "scan_visible", True):
            sc.doc.Objects.Show(sid, True)
        sc.doc.Views.Redraw()


