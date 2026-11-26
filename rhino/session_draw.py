# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# Viewport preview: polygon, bubbles, labels, legend, CV nets
# -----------------------------------------------------------------------------

class DrawMixin(object):
    def on_move(self, sender, e):
        self.on_move_view(sender, e)
        if self.view_hot is not None:
            self.hover = None; self.hover_bubble = None; self.hover_srf = None
            return
        if getattr(self, "fillet_mode", None):
            self.hover = None; self.hover_bubble = None; self.hover_srf = None; self.hint = None
            try:
                self.fillet_mode["hot"] = self._fm_corner_at(xf_tuple(e.Viewport), e.WindowPoint.X, e.WindowPoint.Y)
            except Exception:
                self.fillet_mode["hot"] = None
            return
        try:
            vp = e.Viewport
            m = xf_tuple(vp)
            pk, sn = self.pick_px(vp)
            hs = self.visible_hits(hit_test(self.cands, m, e.WindowPoint.X, e.WindowPoint.Y, pk, sn), vp)
            self.hover = hs[0] if hs else None
            self.hover_bubble = self.bubble_at(m, e.WindowPoint.X, e.WindowPoint.Y)
            if self.corners and self.s_sidehint():
                self.update_side_hint(e)
            else:
                self.hint = None
            if not self.corners and self.hover is None and self.hover_bubble is None:
                self.update_srf_buttons(vp, m, e.WindowPoint.X, e.WindowPoint.Y)
            else:
                self.hover_srf = None
            if self.s_showcv() or self.view_on("graph"):     # CV points and Graph: the patch under the mouse
                if self.hover_srf is not None:
                    self.cv_srf = self.hover_srf[0]
                else:
                    hs_ = self.surface_under(vp, m, e.WindowPoint.X, e.WindowPoint.Y)
                    self.cv_srf = hs_[0] if hs_ else None
        except Exception:
            self.hover = None
            self.hover_bubble = None
            self.hover_srf = None

    def update_side_hint(self, e):
        """Can ONE patch follow the mesh along the side being drawn?"""
        x, y = e.WindowPoint.X, e.WindowPoint.Y
        last = getattr(self, "hint_xy", None)
        n = len(self.corners)
        if last is not None and last[2] == n and last[3] == len(self.pend):
            if abs(last[0] - x) + abs(last[1] - y) < 6:
                return
            if time.time() - getattr(self, "hint_t", 0.0) < max(0.08, 1.5 * getattr(self, "hint_dt", 0.0)):
                return          # still busy with the last one: keep the mouse fluid
        self.hint_xy = (x, y, n, len(self.pend))
        t0 = time.time()
        try:
            self._side_hint(n)
        finally:
            self.hint_t = time.time(); self.hint_dt = self.hint_t - t0

    def _side_hint(self, n):
        if n < 4 and self.cur_pt is None:
            self.hint = None; return
        path = [self.corners[-1]["pt"]] + list(self.pend) + ([self.corners[0]["pt"]] if n == 4 else [self.cur_pt])
        if path[0].DistanceTo(path[-1]) < 1e-6:
            self.hint = None; return
        self.hint_n = self.hint_normal([c["pt"] for c in self.corners] + list(self.pend) + [path[-1]])
        pts_ = [c["pt"] for c in self.corners] + [path[-1]]
        span_ = max(p.DistanceTo(q) for p in pts_ for q in pts_)
        steps_ = [pts_[i].DistanceTo(pts_[i + 1]) for i in range(len(pts_) - 1)]
        short_ = min([s_ for s_ in steps_ if s_ > 1e-9] or [span_])
        # search depth along 'up': about the patch WIDTH (narrow fillet: small)
        self.hint_R = max(0.5 * min(span_, 3.0 * short_), 1e-3 * self.size)
        crv = smooth_curve(path); dom = crv.Domain
        prof = [T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / 30.0)))) for i in range(31)]
        lvl = 2 if self.s_cont() == "G2" else (1 if self.s_cont() == "G1" else 0)
        cv = [max(4, 2 * (lvl + 1), self.dir_cv(dd) or 0) for dd in (0, 1)]
        along = cv[(n - 1) % 2]
        pct = self.s_tolpct()
        s, why, reach = single_span_reach(prof, along - 1, pct)
        rp = None
        if reach < 0.999:
            rp = self.hint_proj(crv.PointAt(dom.ParameterAt(reach)))
        txt = why if s > GREEN_AT else ""
        if rp is not None:
            txt = "end here: " + why
        # whole patch, once the 4 sides are known (side 3 = cursor; 4 = straight back)
        pc = None; ptxt = ""
        if n >= 3 and s <= 1.0:
            try:
                ps, pwhy, pc = self.patch_hint(n, crv, path[-1], cv)
                if ps > GREEN_AT:
                    ptxt = "patch: " + pwhy
                s = max(s, ps)
            except Exception:
                pass
        self.hint = (hint_state(s), rp, txt, along, pc, ptxt)

    def side_poly(self, k, n_pts=21):
        """Side k (drawn) as tuples, corner k -> corner k+1."""
        a, b = self.corners[k], self.corners[(k + 1) % 4]
        pick = None if self.shape.get(k) else self.common(a, b)
        if pick is not None:
            c = self.cands[pick[0]]; ta, tb = pick[1], pick[2]
            return [T(c.crv.PointAt(ta + (tb - ta) * i / (n_pts - 1.0))) for i in range(n_pts)]
        crv = self.side_curve(k); dom = crv.Domain
        out = []
        for i in range(n_pts):          # scan side: lies on the scan once built
            out.append(T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / (n_pts - 1.0))))))
        return out

    def warm_hint(self, pt):
        """Run the side-hint pipeline once on a dummy side right after the first
        corner, behind a 'please wait' - so the first mouse move is not slow."""
        try:
            q = rg.Point3d(pt.X + 0.01 * self.size, pt.Y, pt.Z)
            self.hint_n = self.hint_normal([pt, q])
            self.hint_R = 0.01 * self.size
            crv = smooth_curve([pt, q]); dom = crv.Domain
            prof = [T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / 30.0)))) for i in range(31)]
            single_span_reach(prof, 5, self.s_tolpct())
            self.visible(pt, sc.doc.Views.ActiveView.ActiveViewport)
        except Exception:
            pass

    def hint_normal(self, pts):
        """Average mesh normal at the clicked points, turned like corner 1's
        face: 'up' of the patch being drawn."""
        acc = (0.0, 0.0, 0.0); ref = None
        if not hasattr(self.mesh, "ClosestMeshPoint"): return None
        for p in pts:
            mp = self.mesh.ClosestMeshPoint(p, 0.0)
            if mp is None: continue
            f = self.mesh.FaceNormals[mp.FaceIndex]; nn = (f.X, f.Y, f.Z)
            if ref is None: ref = nn
            acc = v_add(acc, nn if v_dot(nn, ref) >= 0 else v_mul(nn, -1.0))
        return v_unit(acc) if v_len(acc) > 1e-12 else None

    def hint_proj(self, q):
        """Mesh point for the hint: the face turned up-ish (not a rib wall,
        not the back of a thin part) straight below / above q along the patch
        'up'. Closest-point queries only (fast on big meshes): first q itself,
        then q moved along 'up' until the hit is an up-facing face."""
        n = getattr(self, "hint_n", None)
        m = self.mesh
        if n is None:
            c = m.ClosestPoint(q)
            return c if c.IsValid else q
        h = 0.02 * getattr(self, "hint_R", 0.05 * self.size)
        best = None
        for k in (0.0, -0.5, 0.5, -1.0, 1.0, -2.0, 2.0, -4.0, 4.0):
            p = rg.Point3d(q.X + n[0] * k * h, q.Y + n[1] * k * h, q.Z + n[2] * k * h)
            mp = m.ClosestMeshPoint(p, 0.0)
            if mp is None: continue
            f = m.FaceNormals[mp.FaceIndex]
            if f.X * n[0] + f.Y * n[1] + f.Z * n[2] < 0.3: continue
            c = mp.Point
            dv_ = (c.X - q.X, c.Y - q.Y, c.Z - q.Z); a_ = v_dot(dv_, n)
            lat = v_len(v_sub(dv_, v_mul(n, a_)))
            if best is None or lat < best[0]: best = (lat, c)
            if lat < 0.5 * h: break
        if best is not None:
            return best[1]
        c = m.ClosestPoint(q)
        return c if c.IsValid else q

    def patch_hint(self, n, crv, cursor, cv):
        """Score of the whole future patch: Coons interior on the mesh."""
        sides = [self.side_poly(k) for k in range(n - 1)]
        dom = crv.Domain
        sides.append([T(self.hint_proj(crv.PointAt(dom.ParameterAt(i / 20.0)))) for i in range(21)])
        if n == 3:
            a_, b_ = T(cursor), T(self.corners[0]["pt"])
            sides.append([T(self.hint_proj(P3(v_lerp(a_, b_, i / 20.0)))) for i in range(21)])

        def fn(pts):
            m = len(pts) - 1
            def f(t):
                x = max(0.0, min(1.0, t)) * m; k = min(int(x), m - 1)
                return v_lerp(pts[k], pts[k + 1], x - k)
            return f
        fs = [fn(p) for p in sides]
        N = 9; grid = []
        for i in range(N):
            row = []
            for j in range(N):
                p = coons_point(fs, i / (N - 1.0), j / (N - 1.0))
                if 0 < i < N - 1 and 0 < j < N - 1:
                    p = T(self.hint_proj(P3(p)))
                row.append(p)
            grid.append(row)
        s, why = patch_score(grid, cv[0] - 1, cv[1] - 1, self.s_tolpct())
        return s, why, P3(grid[N // 2][N // 2])

    def bubble_at(self, m, sx, sy):
        for k, bp in self.bubbles.items():
            bx, by = to_screen(m, T(bp))
            if abs(bx - sx) < 55 and abs(by - sy) < 11:
                return (k, bx, by)
        return None

    def on_down(self, sender, e):
        try:
            self.down = (e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
        except Exception:
            self.down = None
        # view buttons act on mouse down: a click off the scan never returns a
        # point from the scan-constrained GetPoint, but the mouse down comes
        self.view_clicked = False
        try:
            right = bool(getattr(e, "RightButtonDown", False))
            k = None if right else self.view_button_at(e.Viewport, e.WindowPoint.X, e.WindowPoint.Y)
            if k is not None:
                self.view_clicked = True
                self.toggle_view(k)
        except Exception:
            pass


    def visible(self, p, vp):
        """Is point p (on or at the mesh) on the side of the part you look at?
        The mesh face nearest to it must face the camera - an edge of a patch
        on the far side of a thin part (or a label there) is not. Fast: one
        closest-point query on the mesh."""
        try:
            mp = self.mesh.ClosestMeshPoint(p, 0.0)
            if mp is None: return True
            n = self.mesh.FaceNormals[mp.FaceIndex]
            if vp.IsParallelProjection:
                c = vp.CameraDirection; vv = (-c.X, -c.Y, -c.Z)
            else:
                cam = vp.CameraLocation; vv = (cam.X - p.X, cam.Y - p.Y, cam.Z - p.Z)
            return n.X * vv[0] + n.Y * vv[1] + n.Z * vv[2] > 0
        except Exception:
            return True

    def visible_hits(self, hits, vp):
        """Edge / curve hits only on the side you look at."""
        out = []
        for h in hits or []:
            c = self.cands[h["ci"]]
            p = c.crv.PointAt(h["t"]) if h.get("end") is None else rg.Point3d(c.p0 if h["end"] == 0 else c.p1)
            if self.visible(p, vp): out.append(h)
        return out

    def draw_cv_net(self, d, vp):
        """CV net of the surface under the mouse only, and only the CVs on
        the side facing you (the others would show through the part)."""
        oid = getattr(self, "cv_srf", None)
        item = getattr(self, "cvnets", {}).get(oid) if oid is not None else None
        if item is None: return
        net, nrm = item
        try:
            par = vp.IsParallelProjection
            cam = vp.CameraLocation; cdir = vp.CameraDirection
        except Exception:
            return
        def front(i, j):
            p = net[i][j]; n = nrm[i][j]
            if par: vv = (-cdir.X, -cdir.Y, -cdir.Z)
            else: vv = (cam.X - p.X, cam.Y - p.Y, cam.Z - p.Z)
            return n[0] * vv[0] + n[1] * vv[1] + n[2] * vv[2] > 0
        nu = len(net); nv = len(net[0]) if nu else 0
        vis = [[front(i, j) for j in range(nv)] for i in range(nu)]
        cl = col(60, 60, 60); cp = col(0, 0, 0)
        for i in range(nu):
            for j in range(nv):
                if not vis[i][j]: continue
                if i + 1 < nu and vis[i + 1][j]: d.DrawDottedLine(net[i][j], net[i + 1][j], cl)
                if j + 1 < nv and vis[i][j + 1]: d.DrawDottedLine(net[i][j], net[i][j + 1], cl)
                d.DrawPoint(net[i][j], cp)

    def draw_mirror_hint(self, d):
        """MirrorPlane on: when the cursor is within MirrorSnapDist of the
        plane, show where the point / corner will snap."""
        ax = MIRROR_AXIS.get(self.s_mirror())
        p = getattr(self, "cur_pt", None)
        if ax is None or p is None or getattr(self, "view_hot", None): return
        c = [p.X, p.Y, p.Z]
        if abs(c[ax]) > self.s_mtol(): return
        c[ax] = 0.0
        q = rg.Point3d(c[0], c[1], c[2])
        d.DrawLine(p, q, C_RED, 2)
        d.DrawDot(q, "mirror", C_RED, col(255, 255, 255))

    def side_color(self, k):
        return C_RED if k % 2 == 0 else C_BLUE

    def on_draw(self, sender, e):
        try:
            d = e.Display
            n = len(self.corners)
            self.bubbles = {}
            self.draw_matcap(d)                 # first: everything else on top
            if self.view_on("deviation"):
                self.draw_deviation(d)
            if self.view_on("graph"):
                self.draw_graph(d)
            if self.s_legend():
                self.draw_legend(d)
            if self.s_showcv():
                self.draw_cv_net(d, e.Viewport)
            self.draw_mirror_hint(d)
            self.draw_srf_buttons(d, e.Viewport)
            self.draw_match(d, e.Viewport)
            self.draw_flash(d)
            self.draw_edge_mode(d, e.Viewport)
            self.draw_fillet_mode(d, e.Viewport)
            self.draw_view_buttons(d, e.Viewport)
            if self.s_labels():
                vp_ = e.Viewport
                for (p, txt, cc) in self.markers:
                    if self.visible(p, vp_):
                        d.DrawDot(p, txt, cc, col(0, 0, 0))
                for c in self.cands:
                    if c.ncv and self.visible(c.lbl, vp_):
                        d.DrawDot(c.lbl, str(c.ncv), C_GRAY, col(255, 255, 255))
            for k in range(n - 1):
                self.draw_side(d, k, self.corners[k], self.corners[k + 1])
            for k, pts in self.shape.items():
                for p in pts:
                    d.DrawPoint(p, self.side_color(k))
            if n > 0:
                cur = [self.corners[-1]["pt"]] + list(self.pend)
                if n == 4: cur += [self.corners[0]["pt"]]
                elif self.cur_pt is not None: cur += [self.cur_pt]   # off the scan: no rubber band
                h = getattr(self, "hint", None) if len(cur) > 1 else None
                if h is not None:
                    hc = {"green": col(0, 170, 60), "yellow": col(235, 190, 0), "red": col(220, 30, 30)}[h[0]]
                    draw_smooth(d, cur, hc, 3)
                    if h[1] is not None:
                        d.DrawDot(h[1], h[2], hc, col(255, 255, 255))
                    elif h[2]:
                        d.DrawDot(self.cur_pt or e.CurrentPoint, h[2], hc, col(0, 0, 0))
                    if h[4] is not None and h[5]:
                        d.DrawDot(h[4], h[5], hc, col(0, 0, 0))
                elif len(cur) > 1:
                    draw_smooth(d, cur, self.side_color(n - 1), 2)
                if n == 4:
                    c4 = smooth_curve(cur)
                    m4 = c4.PointAt(c4.Domain.ParameterAt(0.5))
                    a_, b_ = {"on": self.corners[3]["on"]}, {"on": self.corners[0]["on"]}
                    kind = "edge" if (not self.pend and self.common(a_, b_)) else "scan"
                    self.draw_bubble(d, 3, m4, kind)
                if self.cur_pt is not None and n < 4 and getattr(self, "hover", None) is None:
                    try:
                        sn_ = self.snap_to_edges(self.cur_pt)
                    except Exception:
                        sn_ = None
                    if sn_ is not None:          # BEFORE the click: this would go onto the edge
                        d.DrawLine(self.cur_pt, sn_[0], col(255, 140, 0), 2)
                        d.DrawDot(self.cur_pt, "{} to edge - snaps".format(fl(sn_[1])), col(255, 140, 0),
                                  col(0, 0, 0))
                for p in self.pend:
                    near_ = None
                    try:
                        near_ = self.snap_to_edges(p)
                    except Exception:
                        pass
                    if near_ is not None:
                        d.DrawPoint(p, Rhino.Display.PointStyle.RoundControlPoint, 6, col(255, 140, 0))
                        d.DrawLine(p, near_[0], col(255, 140, 0), 1)
                    else:
                        d.DrawPoint(p, self.side_color(n - 1))
                if n == 3 and self.cur_pt is not None:
                    d.DrawLine(self.cur_pt, self.corners[0]["pt"], self.side_color(3), 1)
            for i, cc in enumerate(self.corners):
                d.DrawDot(cc["pt"], str(i + 1), col(40, 40, 40), col(255, 255, 255))
            if n == 4:
                cx = sum(c_["pt"].X for c_ in self.corners) / 4.0
                cy = sum(c_["pt"].Y for c_ in self.corners) / 4.0
                cz = sum(c_["pt"].Z for c_ in self.corners) / 4.0
                d.DrawDot(rg.Point3d(cx, cy, cz), self.size_text(), col(255, 255, 255), col(0, 0, 0))
            for i, p in enumerate(self.fit_pts):
                d.DrawDot(p, "P{}".format(i + 1), C_PT, col(255, 255, 255))
            h = self.hover
            if h is not None and n < 4 and h["ci"] < len(self.cands):
                c = self.cands[h["ci"]]
                draw_poly(d, c.pts3, C_HOVER, 2)
                p = c.crv.PointAt(h["t"])
                at_end = min(p.DistanceTo(c.crv.PointAtStart), p.DistanceTo(c.crv.PointAtEnd)) < 1e-6 * max(1.0, c.length)
                txt = "Corner" if at_end else ("Edge" if c.kind == "edge" else "Curve")
                if 0 < n < 4 and not self.pend and any(x[0] == h["ci"] for x in self.corners[-1]["on"]):
                    txt = "side {}: {}".format(n, self.side_text(n - 1, c.kind))
                d.DrawDot(p, txt, C_HOVER, col(0, 0, 0))
        except Exception:
            pass

    def draw_legend(self, d):
        """Command-line options cannot have tooltips in Rhino - this short
        legend in the corner of the view explains them instead."""
        lines = [
            ("msh2classA options (Legend button in the view panel hides this, Help = more)", None),
            ("DefaultCont = {}".format(self.s_cont()), "continuity for new sides; side bubbles change one side"),
            ("FitSrfTo = {}".format({"Mesh": "WholeMesh", "Points": "TempPts"}.get(self.s_fit(), self.s_fit())),
             "WholeMesh whole mesh | TempPts clicked points | NoScan edges only"),
            ("SrfSplit = {}".format("AutoSplit" if self.s_spans() == "Auto" else "OneSrf"),
             "one patch | strip cut into single-span patches"),
            ("MeshTol = {:g}".format(self.s_tol()), "allowed mesh deviation (also the Deviation colours)"),
            ("HintTolPct = {:g}".format(self.s_tolpct()), "SideLenHint deviation, % of the size"),
            ("MirrorPlane = {}".format(self.s_mirror()), "symmetry plane; sides within MirrorSnapDist snap onto it"),
            ("CornerFlow = {}".format("Smooth" if self.s_flow() == "Smooth" else "KinkOK"), "at a neighbour's corner: new side may kink / continues its outline"),
            ("SideLenHint = {}".format("Yes" if self.s_sidehint() else "No"), "green sure / yellow near limit / red end side at dot"),
            ("SnapPctOfView = {:g}%".format(self.s_snap()), "corner catch radius - zoom in to click beside a corner"),
            ("UndoSrfEdit", "undo the last patch-button step"),
            ("PATCH BUTTONS (hover a finished patch, one step per click)", None),
            ("  CVs move:  N = up/down (off the patch)   UV = sideways (in the patch)   UVN = both", None),
            ("  EvenUV", "CV rows to even spacing; shape stays"),
            ("  StrUV", "CV rows straight seen from above; shape stays"),
            ("  RelaxUV", "less skew: CV layout as the four edges imply; shape stays"),
            ("  PullN", "fit to the mesh along the normal; CV layout stays"),
            ("  FairN", "smoother shape: waves, noise; CV layout stays"),
            ("  RstN", "smoothest the edges allow (no mesh) - then PullN"),
            ("  SmthUVN", "FairN + EvenUV + StrUV at once, strongest"),
            ("  ToDevUV", "a CV row onto the place furthest from the mesh"),
            ("  MatchUVN", "after hand edits: neighbours come back to this patch"),
            ("  FreeEdgeEdt", "click a free edge; from corner to corner = middle only, beside it = whole edge"),
            ("  Del", "delete the patch (UndoSrfEdit brings it back)"),
            ("  every button keeps each seam at its G (built / MatchUVN)", None),
            ("  colours: green = fine, yellow = could be better, red = click it (white = no measure)", None),
        ]
        hs = getattr(self, "hover_srf", None)
        if hs is not None and hs[2]:
            q = self.patch_quality(hs[0])
            if q: lines.append(("  " + q["text"], None))
        y = 30
        try:      # light panel behind the text: readable over any scan colour
            rect = sd.Rectangle(6, 24, 8 + 7 * max(len(a if b is None else "{:<22} {}".format(a, b)) for (a, b) in lines),
                                16 * len(lines) + 10)
            d.Draw2dRectangle(rect, sd.Color.FromArgb(200, 255, 255, 255), 1, sd.Color.FromArgb(200, 255, 255, 255))
        except Exception:
            pass
        for (a, b) in lines:
            txt = a if b is None else "{:<22} {}".format(a, b)
            for (dx, dy, c) in ((1, 1, col(255, 255, 255)), (0, 0, col(20, 20, 20))):
                try:
                    d.Draw2dText(txt, c, rg.Point2d(12 + dx, y + dy), False, 12, "Consolas")
                except Exception:
                    d.Draw2dText(txt, c, rg.Point2d(12 + dx, y + dy), False, 12)
            y += 16

    def draw_bubble(self, d, k, pt, kind):
        if kind == "scan" and self.is_mirror_side(k):
            kind = "mirror"
        hb = getattr(self, "hover_bubble", None)
        txt = self.side_text(k, kind)
        if hb is not None and hb[0] == k:
            d.DrawDot(pt, txt, C_HOVER, col(0, 0, 0))
            try:
                d.DrawDot(float(hb[1]), float(hb[2] + 24), self.bubble_tip(k), col(255, 255, 225), col(0, 0, 0))
            except Exception:
                try:
                    d.Draw2dText(self.bubble_tip(k), col(0, 0, 0), rg.Point2d(hb[1], hb[2] + 24), True, 13)
                except Exception:
                    pass
        else:
            d.DrawDot(pt, txt, self.side_color(k), col(255, 255, 255))
        self.bubbles[k] = pt

    def draw_side(self, d, k, a, b):
        pick = None if self.shape.get(k) else self.common(a, b)
        if pick is None and self.shape.get(k):
            crv = self.side_curve(k); dom = crv.Domain
            pts = [crv.PointAt(dom.ParameterAt(i / 30.0)) for i in range(31)]
            draw_poly(d, pts, self.side_color(k), 2)
            self.draw_bubble(d, k, pts[15], "scan")
            return
        if pick is None:
            d.DrawLine(a["pt"], b["pt"], self.side_color(k), 2)
            mid = rg.Point3d((a["pt"].X + b["pt"].X) / 2, (a["pt"].Y + b["pt"].Y) / 2, (a["pt"].Z + b["pt"].Z) / 2)
            self.draw_bubble(d, k, mid, "scan")
            return
        ci, ta, tb = pick
        c = self.cands[ci]
        pts = [c.crv.PointAt(ta + (tb - ta) * i / 20.0) for i in range(21)]
        draw_poly(d, pts, self.side_color(k), 4)
        self.draw_bubble(d, k, pts[10], c.kind)


