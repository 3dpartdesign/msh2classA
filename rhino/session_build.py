# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# Building a patch: sides -> constraints -> fit -> surfaces
# -----------------------------------------------------------------------------

class BuildMixin(object):
    def undo_patch(self):
        if not self.patches: return
        ids = self.patches.pop()
        for i in ids:
            sc.doc.Objects.Delete(i, True)
        sc.doc.Views.Redraw()
        self.reset_polygon()
        self.refresh()
        print("[UNDO] removed {} patch(es), polygon cleared".format(len(ids)))

    def resolve_side(self, k, ox, use_mesh):
        sd_ = self._resolve_side(k, ox, use_mesh)
        ax = self.mirror_axis if k in self.mirror_sides else None
        if ax is not None:
            pts = []
            for i in range(61):
                q = sd_.fn(i / 60.0)
                c = [q.X, q.Y, q.Z]; c[ax] = 0.0
                pts.append(rg.Point3d(c[0], c[1], c[2]))
            sd_ = Side("scan", polyline_fn(pts), ox, "mirror")
        return sd_

    def _resolve_side(self, k, ox, use_mesh):
        a = self.corners[k]; b = self.corners[(k + 1) % 4]
        pick = None if self.shape.get(k) else self.common(a, b)
        if pick is None and self.shape.get(k):
            crv = self.side_curve(k); dom = crv.Domain
            pts = []
            for i in range(61):
                p = crv.PointAt(dom.ParameterAt(i / 60.0))
                if use_mesh and 0 < i < 60:
                    q = self.scan_pt(p)
                    if q is not None: p = q
                pts.append(p)
            return Side("scan", polyline_fn(pts), ox, "scan")
        if pick is None:
            pts = project_line_samples(a["pt"], b["pt"], self.scan_pt if use_mesh else None)
            return Side("scan", polyline_fn(pts), ox, "scan")
        ci, t0, t1 = pick
        c = self.cands[ci]
        if c.kind == "curve":
            sd_ = Side("curve", curve_fn(c.crv, t0, t1), ox, "curve")
            try:
                nc = c.crv.ToNurbsCurve()
                if nc.SpanCount == 1: sd_.along_deg = nc.Degree
            except Exception:
                pass
            return sd_
        return edge_side(c, a["pt"], b["pt"], ox)

    def build(self):
        red, blue = self.dir_cv(0), self.dir_cv(1)      # None = auto (scan)
        fit_mode = self.s_fit(); auto = self.s_spans() == "Auto"
        fit_pts = None
        if fit_mode == "Points":
            fit_pts = self.collect_fit_points()
            if fit_pts is None:
                print("[INFO] cancelled"); return
        busy = show_busy("Building patch...")
        try:
            self._build(red, blue, fit_mode, auto, fit_pts)
        finally:
            hide_busy(busy)
            self.fit_pts = []
            self.nsign = None

    def _build(self, red, blue, fit_mode, auto, fit_pts):
        self.prep_front()
        self.mirror_axis = MIRROR_AXIS.get(self.s_mirror())
        self.mirror_sides = [k for k in range(4) if self.is_mirror_side(k)]
        mirror_lv = dict((k, self.level_for_side(k)) for k in self.mirror_sides)
        for k in self.mirror_sides:                      # snap to the plane
            ax = self.mirror_axis
            for cc in (self.corners[k], self.corners[(k + 1) % 4]):
                c = [cc["pt"].X, cc["pt"].Y, cc["pt"].Z]; c[ax] = 0.0
                cc["pt"] = rg.Point3d(c[0], c[1], c[2])
            if k in self.shape:
                for i, q in enumerate(self.shape[k]):
                    c = [q.X, q.Y, q.Z]; c[ax] = 0.0
                    self.shape[k][i] = rg.Point3d(c[0], c[1], c[2])
        ox = tuple(sum(T(c["pt"])[k] for c in self.corners) / 4.0 for k in range(3))
        on_scan = fit_mode in ("Mesh", "NoScan")          # edges are projected onto the scan
        sides = [self.resolve_side(k, ox, on_scan) for k in range(4)]
        levels = [self.level_for_side(k) if s.kind == "edge" else 0 for k, s in enumerate(sides)]
        desc = []
        for k, s in enumerate(sides):
            if s.kind == "edge": desc.append("{}:{}".format(k + 1, SIDE_LV_NAME[self.raw_side_level(k)]))
            elif s.kind == "curve": desc.append("{}:crv".format(k + 1))
            elif k in self.mirror_sides: desc.append("{}:mirror {}".format(k + 1, LEVEL_NAME[mirror_lv[k]]))
            else: desc.append("{}:scan".format(k + 1))
        print("\n=== Patch {} | {} | {} | {} | sides {} ===".format(
            len(self.patches) + 1,
            self.size_text(),
            fit_mode, "Auto spans" if auto else "Single",
            ", ".join(desc) if desc else "all on mesh"))
        self.warn_scan_sides(sides)
        fs = [s.fast for s in sides]
        coons = lambda u, v: coons_point(fs, u, v)
        self.nsign = self.patch_sign(coons, ox)

        def iso_lines(direction):
            lines = []
            for c in (0.0, 0.5, 1.0):
                pts = []
                for i in range(61):
                    t = i / 60.0
                    p = coons(t, c) if direction == 0 else coons(c, t)
                    q = self.scan_pt(rg.Point3d(p[0] + ox[0], p[1] + ox[1], p[2] + ox[2]))
                    if q is not None: p = v_sub(T(q), ox)
                    pts.append(p)
                lines.append(pts)
            return lines

        def rows_why(d):
            ends = (1, 3) if d == 0 else (0, 2)
            parts = []
            for k in ends:
                if sides[k].kind == "edge": lab = SIDE_LV_NAME[self.raw_side_level(k)]
                elif sides[k].kind == "scan": lab = "mesh, {} later".format(LEVEL_NAME[self.level_for_side(k)])
                else: lab = "curve"
                parts.append("{}({})".format(self.side_rows(k), lab))
            return " + ".join(parts)

        def count(d, n):
            ends = (1, 3) if d == 0 else (0, 2)
            why = rows_why(d)
            if fit_mode != "NoScan" and all(sides[k].kind == "scan" for k in ends):
                lay = [self.layout_for_side(k_) if s_.kind == "edge" else 0 for k_, s_ in enumerate(sides)]
                need = self.auto_cv(d, sides, lay, iso_lines, auto)[0]
                if need > n:
                    return need, why + ", raised to {} for the mesh".format(need)
            return n, why
        nu, why_u = count(0, red)
        nv, why_v = count(1, blue)
        print("  PINK {}CV ({})   BLUE {}CV ({})".format(nu, why_u, nv, why_v))
        red, blue = nu, nv
        deg_u, deg_v = red - 1, blue - 1
        su = sv = 1
        self.corner_check(sides, levels)
        for k, s in enumerate(sides):
            if s.kind == "edge" and levels[k] > 0 and not s.exact:
                print("  [INFO] edge {} is trimmed / not iso - continuity is approximated".format(k + 1))
            if s.kind in ("edge", "curve") and s.along_deg and s.along_deg <= 7:
                if k % 2 == 0 and s.along_deg > deg_u:
                    print("  [INFO] PINK raised to {}CV to follow side {} exactly".format(s.along_deg + 1, k + 1)); deg_u = s.along_deg
                if k % 2 == 1 and s.along_deg > deg_v:
                    print("  [INFO] BLUE raised to {}CV to follow side {} exactly".format(s.along_deg + 1, k + 1)); deg_v = s.along_deg
        if auto and fit_mode == "NoScan":
            print("  [INFO] SrfSplit=AutoSplit needs the mesh - NoScan builds one patch")
            auto = False
        if auto:
            tol = self.s_tol()
            print("  AutoSplit, MeshTol={}. PINK direction:".format(fl(tol)))
            su = auto_spans(iso_lines(0), max(deg_u, 1), tol, log=print) if deg_u >= 1 else 1
            print("  BLUE direction:")
            sv = auto_spans(iso_lines(1), max(deg_v, 1), tol, log=print) if deg_v >= 1 else 1
            print("  -> {} x {} = {} patch(es)".format(su, sv, su * sv))

        du = BDir(deg_u, su); dv = BDir(deg_v, sv)
        if fit_mode == "Mesh":
            # Start ON the scan (as v49 did with projected curves): the Coons
            # interior between the drawn sides is projected onto the visible
            # side of the scan first, so the fit never starts under it.
            def start_fn(u, v):
                p = coons(u, v)
                h = 1e-3
                cu = v_sub(coons(min(1.0, u + h), v), coons(max(0.0, u - h), v))
                cv = v_sub(coons(u, min(1.0, v + h)), coons(u, max(0.0, v - h)))
                q = self.scan_pt(rg.Point3d(p[0] + ox[0], p[1] + ox[1], p[2] + ox[2]), v_cross(cu, cv))
                return v_sub(T(q), ox) if q is not None else p
            P0 = fit_to_function(du, dv, start_fn)
        else:
            P0 = fit_to_function(du, dv, coons)
        prob = FitProblem(du, dv, P0)
        for k, s in enumerate(sides):
            if s.kind == "scan" and k not in self.mirror_sides:
                prob.reserved.append((k, self.level_for_side(k) + 1))

        nu_, nv_ = du.n, dv.n
        for k in self.mirror_sides:
            def rows(r):
                out = []
                for x in range(nu_ if k % 2 == 0 else nv_):
                    if k == 0: i, j = x, r
                    elif k == 2: i, j = x, nv_ - 1 - r
                    elif k == 1: i, j = nu_ - 1 - r, x
                    else: i, j = r, x
                    out.append(i * nv_ + j)
                return out
            plane_val = -ox[self.mirror_axis]           # plane is at 0 in world coords
            prob.mirrors.append((self.mirror_axis, plane_val, rows(0), rows(1) if mirror_lv[k] >= 1 else None,
                                 rows(2) if mirror_lv[k] >= 2 else None))
        corners_uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for k in range(4):
            u, v = corners_uv[k]
            prob.hard.append((u, v, v_sub(T(self.corners[k]["pt"]), ox)))

        w_side = {"Mesh": 2.0, "Points": 1.0, "NoScan": 20.0}.get(fit_mode, 2.0)
        for k, s in enumerate(sides):
            uvf, axis, sign = SIDE_MAP[k]
            along_n = (du.n if k % 2 == 0 else dv.n)
            K = max(10, 3 * along_n + 2)
            ts = [i / float(K - 1) for i in range(K)]
            if s.kind == "scan":
                for t in ts:
                    u, v = uvf(t)
                    prob.soft.append((u, v, s.fast(t), w_side))
                continue
            evs = [s.ev(t) for t in ts] if s.kind == "edge" else None
            for i, t in enumerate(ts):
                u, v = uvf(t)
                tgt = v_sub(evs[i][0], ox) if evs else s.exact_pt(t)
                prob.hard.append((u, v, tgt))
            if s.kind == "edge" and levels[k] >= 1:
                Lm = v_len(s.ev(0.5)[1])
                if Lm < 1e-12:
                    print("  [WARN] edge {}: degenerate tangent, only G0".format(k + 1)); continue
                samples = []
                for i, t in enumerate(ts):
                    u, v = uvf(t)
                    D1 = v_mul(evs[i][1], 1.0 / Lm)
                    D2 = v_mul(evs[i][2], 1.0 / (Lm * Lm))
                    E1, E2, TW = evs[i][4], evs[i][5], v_mul(evs[i][6], 1.0 / Lm)
                    samples.append((u, v, axis, sign, D1, D2, E1, E2, TW, t))
                um, vm = uvf(0.5)
                der = srf_derivs(du, dv, P0, um, vm, 1)
                a0 = v_len(der[(1, 0)] if axis == 0 else der[(0, 1)])
                cn = Cont(k, levels[k], samples, a0)
                # NARROW patch (fillet) against this neighbour: its cross rows
                # are much shorter than the neighbour's. A free shear there
                # (rows leaning along the seam) was magnified ~1/a^2 into the
                # fillet: its CVs slid back and forth along it = the wave.
                # The rows go straight across instead.
                cn.narrow = a0 < NARROW_RATIO * Lm
                if cn.narrow:
                    cn.flow = [True, True]
                    print("  side {}: narrow patch against its neighbour - rows straight across (no shear)".format(k + 1))
                if self.s_flow() == "Smooth" and not cn.narrow:
                    cn.flow = list(s.at_end)
                    if any(cn.flow):
                        print("  CornerFlow=Smooth: side {} continues the neighbour's outline at corner(s) {}".format(
                            k + 1, ", ".join(str((k + e_) % 4 + 1) for e_ in (0, 1) if cn.flow[e_])))
                prob.conts.append(cn)
                if levels[k] >= 2:
                    self.curvature_tip(k, s, du, dv, P0, ox)

        if fit_mode == "Mesh":
            nsu = max(14, 3 * du.n + 6); nsv = max(14, 3 * dv.n + 6)
            params = [(i / float(nsu - 1), j / float(nsv - 1)) for i in range(nsu) for j in range(nsv)]

            def data_fn(P):
                raw = []
                for (u, v) in params:
                    Dd = srf_derivs(du, dv, P, u, v, 1)
                    s = Dd[(0, 0)]
                    ns_ = v_cross(Dd[(1, 0)], Dd[(0, 1)])
                    q = self.scan_pt(rg.Point3d(s[0] + ox[0], s[1] + ox[1], s[2] + ox[2]),
                                     ns_ if v_len(ns_) > 1e-12 else None, fit=True)
                    if q is None: continue
                    t = v_sub(T(q), ox)
                    raw.append((u, v, t, v_dist(s, t)))
                if not raw: return []
                kk = max(2.5 * median([r[3] for r in raw]), self.size * 1e-7)
                out = []
                for (u, v, t, d) in raw:
                    if d > 6.0 * kk: continue
                    out.append((u, v, t, 1.0 if d <= kk else kk / d))
                return out
            iters = 8
        elif fit_mode == "NoScan":
            # inside = the smoothest surface the edges and G0/G1/G2 allow
            prob.w_fair = 0.1
            data_fn = lambda P: []
            iters = 3
        else:
            # TempPts: through the points, the rest as smooth as the edges allow (a bridge - the scan
            # under it, e.g. a recess, is not used at all)
            prob.w_reg = 0.005
            prob.w_fair = 0.1
            tp = [v_sub(T(p), ox) for p in (fit_pts or [])]
            state = {"uv": [None] * len(tp)}

            def data_fn(P):
                out = []
                for i, p in enumerate(tp):
                    u, v = project_param(du, dv, P, p, state["uv"][i])
                    state["uv"][i] = (u, v)
                    out.append((u, v, p, 10.0))
                return out
            iters = 6 if tp else 1

        self.freedom_report(du, dv, sides, levels, prob)
        P = run_fit(prob, data_fn, iters, log=print)
        P = self.clean_build(P, prob, levels, sides, du, dv, data_fn, iters)

        # ---- make Rhino surfaces ----
        if du.spans > 1 or dv.spans > 1:
            bu, bv = BDir(du.p, 1), BDir(dv.p, 1)
            parts = [(bu, bv, pts) for (_, _, pts) in decompose_surface(du, dv, P)]
        else:
            parts = [(du, dv, P)]
        ids = []
        for (a, b, pts) in parts:
            ns = make_nurbs(a, b, pts, ox)
            if ns is None or not ns.IsValid:
                print("  [ERROR] invalid surface"); continue
            side_ = self.scan_side_sign(ns)
            flipped = False
            if side_ < 0:                     # normal towards the side you work on (the clicked faces)
                try:
                    rv_ = ns.Reverse(0)
                    if rv_ is not None and rv_.IsValid:
                        ns = rv_; side_ = 1; flipped = True
                except Exception:
                    pass
            oid_ = sc.doc.Objects.AddSurface(ns)
            ids.append(oid_)
            try:
                rs.SetUserText(oid_, "msh2classA_fit", fit_mode)
                rs.SetUserText(oid_, "msh2classA_side", str(side_))
                if fit_mode == "Points" and fit_pts:      # kept ON the patch: every button uses them, not the scan
                    rs.SetUserText(oid_, PTS_KEY, json.dumps([[round(c, 6) for c in T(q)] for q in fit_pts]))
            except Exception: pass
            if len(parts) == 1:               # the G0/G1/G2 each side was BUILT with - kept by every button later
                for k_, s_ in enumerate(sides):
                    if s_.kind == "edge":
                        set_side_level(oid_, {1: 3, 3: 1}.get(k_, k_) if flipped else k_, levels[k_])
        sc.doc.Views.Redraw()
        if not ids:
            print("[ERROR] Patch failed."); return
        self.patches.append(ids)
        print("[OK] {} patch(es), {}x{} CV each".format(len(ids), du.p + 1, dv.p + 1))
        self.report(du, dv, P, ox, sides, prob, fit_mode, fit_pts)
        for k in self.mirror_sides:
            uvf, axis, sign = SIDE_MAP[k]
            dmax = tilt = 0.0
            for i in range(21):
                u, v = uvf(i / 20.0)
                D = srf_derivs(du, dv, P, u, v, 1)
                dmax = max(dmax, abs(D[(0, 0)][self.mirror_axis] + ox[self.mirror_axis]))
                n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                tilt = max(tilt, math.degrees(math.asin(min(1.0, abs(n[self.mirror_axis])))))
            print("  Side {} mirror {}: off plane {:.5f}  normal tilt to plane {:.3f} deg".format(
                k + 1, LEVEL_NAME[mirror_lv[k]], dmax, tilt))
        if getattr(self, "n_wall", 0):
            print("  [INFO] mesh faces turned away from the patch (walls, ribs, opposite side) were ignored ({} samples over all iterations)".format(self.n_wall))
        if getattr(self, "n_back", 0):
            print("  [INFO] {} mesh samples hit the hidden (back) side - taken from the visible side instead".format(self.n_back))

    def warn_scan_sides(self, sides):
        """A side on the MESH that runs right beside a neighbour's edge leaves
        a gap - say so (the corners were not both on that edge)."""
        for k, s_ in enumerate(sides):
            if s_.kind != "scan" or k in self.mirror_sides: continue
            mid = s_.fn(0.5)
            for c in self.cands:
                if c.kind != "edge" or not c.near_bbox(T(mid), 5.0 * self.edge_snap_dist()): continue
                ok, t = c.crv.ClosestPoint(mid)
                if ok and c.crv.PointAt(t).DistanceTo(mid) < 5.0 * self.edge_snap_dist():
                    print("  [WARN] side {} runs along a neighbour's edge but is fitted to the MESH (its corners "
                          "are not both on that edge) - expect a gap. UndoSrf and click both corners ON the edge "
                          "(yellow) or its end.".format(k + 1))
                    break

    def auto_cv(self, d, sides, levels, iso_lines, spans_auto):
        """CV count for direction d (0 = PINK/u, 1 = BLUE/v) and the reason."""
        req = 4; why = []                   # class-A: never ruled (2CV) in Auto
        cross = (1, 3) if d == 0 else (0, 2)      # sides whose continuity needs rows in d
        along = (0, 2) if d == 0 else (1, 3)      # sides running along d
        for k in cross:
            if sides[k].kind == "edge" and levels[k] >= 1:
                need = 4 if levels[k] == 1 else 6
                if need > req: req = need
                why.append("{} side {}".format(LEVEL_NAME[levels[k]], k + 1))
        for k in along:
            s = sides[k]
            if s.kind in ("edge", "curve") and s.along_deg and s.along_deg + 1 > req and s.along_deg <= 7:
                req = s.along_deg + 1
                why.append("= {} side {}".format("edge" if s.kind == "edge" else "curve", k + 1))
        if spans_auto:
            if req < 4: req = 4
            return req, ", ".join(why) or "spans auto"
        if req < 6:
            tol = self.s_tol(); lines = iso_lines(d); got = None
            for deg in (3, 5):
                if deg + 1 < req: continue
                worst = max(curve_fit_deviation(pts, deg, 1)[0] for pts in lines)
                if worst <= tol:
                    got = deg + 1; break
            if got is None:
                got = 6
                print("  [INFO] {} direction: even 6CV is {:.3f} off the mesh (MeshTol {:.3f}) - "
                      "smaller patch or SrfSplit=AutoSplit".format("PINK" if d == 0 else "BLUE", worst, tol))
            if got > req:
                req = got; why.append("scan")
            elif not why:
                why.append("scan")
        return req, ", ".join(why)

    def curvature_tip(self, k, s, du, dv, P0, ox):
        """G2 forces this patch to START with the neighbour's curvature across
        the edge. If the mesh here is flatter, that becomes a visible bump."""
        try:
            uvf, axis, sign = SIDE_MAP[k]
            pt, D1, D2, n = s.ev(0.5)[:4]
            kn = v_dot(D2, n) / max(v_dot(D1, D1), 1e-300)
            u, v = uvf(0.5)
            D = srf_derivs(du, dv, P0, u, v, 2)
            Sc = D[(1, 0)] if axis == 0 else D[(0, 1)]
            Scc = D[(2, 0)] if axis == 0 else D[(0, 2)]
            ks = v_dot(Scc, n) / max(v_dot(Sc, Sc), 1e-300)
            uo, vo = SIDE_MAP[(k + 2) % 4][0](0.5)
            W = v_dist(srf_point(du, dv, P0, u, v), srf_point(du, dv, P0, uo, vo))
            bump = (abs(kn) - abs(ks)) * W * W / 12.0     # only when the neighbour is MORE curved
            if abs(kn) > 2.0 * abs(ks) and bump > max(0.2 * self.s_tol(), 0.05):
                print("  [TIP] side {}: the neighbour is curved across the edge (R {:.0f}) and the mesh here is "
                      "flatter (R {:.0f}). G2 makes this patch start with the neighbour's curvature -> expect a "
                      "bump of about {:.2f}. Better: build the FLATTER patch first and the curved one G2 to it "
                      "(the curvature then ramps inside the curved patch), or use G1 on this side.".format(
                          k + 1, 1.0 / abs(kn) if abs(kn) > 1e-9 else float("inf"),
                          1.0 / abs(ks) if abs(ks) > 1e-9 else float("inf"), bump))
        except Exception:
            pass

    def corner_check(self, sides, levels):
        """Corner k joins side k-1 (its end) and side k (its start)."""
        for k in range(4):
            a, b = sides[(k - 1) % 4], sides[k]
            if not (a.kind == "edge" and b.kind == "edge"): continue
            if levels[(k - 1) % 4] < 1 or levels[k] < 1: continue
            try:
                na = a.ev(1.0)[3]; nb = b.ev(0.0)[3]
                ang = math.degrees(math.acos(min(1.0, abs(v_dot(v_unit(na), v_unit(nb))))))
            except Exception:
                continue
            if ang > 0.05:
                print("  [WARN] corner {}: the two neighbour surfaces meet at {:.2f} deg there - "
                      "G1 near this corner cannot be better than that.".format(k + 1, ang))

    def build_check(self, P, prob, du, dv):
        """-> (CVs jumped over each other?, worst G1 kink in deg over the
        continuity sides, away from the corners)."""
        cross = cv_order_worst(P, du.n, dv.n)[0] < 0.0
        worst = 0.0
        for c in prob.conts:
            uvf, axis, sign = SIDE_MAP[c.side]
            for smp in c.samples:
                u, v = smp[0], smp[1]
                t = smp[9] if len(smp) >= 10 else 0.5
                if not (0.15 <= t <= 0.85):
                    continue
                D = srf_derivs(du, dv, P, u, v, 1)
                n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                E1 = smp[6] if len(smp) >= 10 else None
                if E1 is None:
                    continue
                nb = v_unit(v_cross(E1, smp[4]))
                if v_len(nb) < 0.5:
                    continue
                worst = max(worst, math.degrees(math.acos(min(1.0, abs(v_dot(n, nb))))))
        return cross, worst

    def clean_build(self, P, prob, levels, sides, du, dv, data_fn, iters):
        """A narrow patch tied G2 on its long sides AND on a short end at a
        corner of four patches cannot meet all of it - the leftover went into
        CVs sliding over each other. Then the SHORT end gives way (G2 -> G1 ->
        G0) and the patch is built again; it says so."""
        tmp = SPatch("_", du.p, dv.p, P)
        shorts = [c for c in prob.conts if short_side(tmp, c.side)]
        if not shorts or not any(getattr(c, "narrow", False) for c in prob.conts):
            return P
        cross, worst = self.build_check(P, prob, du, dv)
        if not cross and worst <= 2.0 * SEAM_ANG:
            return P
        snap = lambda: (list(prob.conts), [(c, c.level) for c in prob.conts], dict(levels) if isinstance(levels, dict)
                        else list(levels))
        best = (cross, worst, P, snap())
        for step in range(2):
            changed = []
            for c in shorts:
                if c not in prob.conts:
                    continue
                if c.level >= 2:
                    c.level = 1
                else:
                    prob.conts.remove(c)              # G0 only (its edge stays on the neighbour's)
                levels[c.side] = c.level if c in prob.conts else 0
                changed.append("side {} {}".format(c.side + 1, LEVEL_NAME[levels[c.side]]))
            if not changed:
                break
            for c in prob.conts:
                c.a = c.a_init; c.b = 0.0; c.beta = [0.0, 0.0]; c.gam = [0.0, 0.0]; c.a_fixed = False
            P2 = run_fit(prob, data_fn, iters)
            cross2, worst2 = self.build_check(P2, prob, du, dv)
            print("  [clean] short end of a narrow patch at a corner: {} -> CVs {}, kink on the long sides {:.2f} deg".format(
                ", ".join(changed), "cross" if cross2 else "in order", worst2))
            if (cross2, worst2) < (best[0], best[1]):
                best = (cross2, worst2, P2, snap())
            if not cross2 and worst2 <= 2.0 * SEAM_ANG:
                break
        P = best[2]
        conts_, lvls_, levels_ = best[3]
        prob.conts[:] = conts_
        for (c, lv) in lvls_:
            c.level = lv
        for k_ in range(len(levels)):
            levels[k_] = levels_[k_]
        if best[0]:
            print("  [WARN] the CV net of this narrow patch still crosses - rebuild it wider, or as ONE patch "
                  "across the split of its neighbours")
        return P

    def freedom_report(self, du, dv, sides, levels, prob):
        """How much of the patch the mesh can still shape."""
        locked = set()
        nu, nv = du.n, dv.n
        for k, s in enumerate(sides):
            L = levels[k] if s.kind == "edge" else 0      # scan/curve: boundary row follows the side
            for r in range(L + 1):
                for x in range(nu if k % 2 == 0 else nv):
                    if k == 0: locked.add((x, r))
                    elif k == 2: locked.add((x, nv - 1 - r))
                    elif k == 1: locked.add((nu - 1 - r, x))
                    else: locked.add((r, x))
        free = nu * nv - len(locked)
        bulges = len(prob.conts)
        if prob.conts and free == 0:
            print("  Mesh can shape: 0 inner CV + {} bulge value(s). The patch is defined by its neighbours "
                  "(class-A blend). If it is far from the mesh: make the patch narrower or split it.".format(bulges))
        elif prob.conts:
            print("  Mesh can shape: {} inner CV + {} bulge value(s).".format(free, bulges))

    def report(self, du, dv, P, ox, sides, prob, fit_mode, fit_pts):
        devs = []
        if fit_mode in ("Mesh", "NoScan"):
            n = 25
            for i in range(n):
                for j in range(n):
                    s = srf_point(du, dv, P, i / (n - 1.0), j / (n - 1.0))
                    q = self.scan_pt(rg.Point3d(s[0] + ox[0], s[1] + ox[1], s[2] + ox[2]))
                    if q is not None: devs.append(v_dist(s, v_sub(T(q), ox)))
            label = "scan" if fit_mode == "Mesh" else "mesh (NoScan - for information only)"
        else:
            for p in (fit_pts or []):
                tp = v_sub(T(p), ox)
                u, v = project_param(du, dv, P, tp)
                devs.append(v_dist(srf_point(du, dv, P, u, v), tp))
            label = "points"
        if devs:
            print("  Deviation to {}: P90 {}  max {}  avg {}".format(
                label, fl(percentile(devs, 0.9)), fl(max(devs)), fl(sum(devs) / len(devs))))
        for c in prob.conts:
            s = sides[c.side]
            uvf, axis, sign = SIDE_MAP[c.side]
            gap = ang = angc = dk = kmax = 0.0
            for i in range(21):
                t = i / 20.0
                u, v = uvf(t)
                D = srf_derivs(du, dv, P, u, v, 2)
                evt = s.ev(t)
                pt, D1, D2, nb = evt[:4]
                gap = max(gap, v_dist(D[(0, 0)], v_sub(pt, ox)))
                n = v_unit(v_cross(D[(1, 0)], D[(0, 1)]))
                a_ = math.degrees(math.acos(min(1.0, abs(v_dot(n, nb)))))
                if 0.15 <= t <= 0.85: ang = max(ang, a_)
                else: angc = max(angc, a_)
                if c.level >= 2:
                    Sc = v_mul(D[(1, 0)] if axis == 0 else D[(0, 1)], sign)
                    Scc = D[(2, 0)] if axis == 0 else D[(0, 2)]
                    k1 = v_dot(Scc, n) / max(v_dot(Sc, Sc), 1e-300)
                    k2 = normal_curv_neighbour(Sc, D1, D2, evt[4], evt[5], evt[6], n)
                    dk = max(dk, abs(k1 - k2)); kmax = max(kmax, abs(k2))
            line = "  Side {} {}: G0 gap {}  G1 angle {:.3f} deg (near corners {:.3f})".format(
                c.side + 1, LEVEL_NAME[c.level], fl(gap), ang, angc)
            if c.level >= 2:
                line += "  G2 dK {:.2e} ({:.1f}% of K)".format(dk, 100.0 * dk / kmax if kmax > 1e-12 else 0.0)
            print(line)
        if any(True for c in prob.conts) and len(prob.conts) >= 3:
            print("  Note: near corners G1 can only be as good as the neighbours agree with each other there.")


