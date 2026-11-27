# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# Drawing a patch: corners, sides, side bubbles, CV counts
# -----------------------------------------------------------------------------

class PolygonMixin(object):
    def reset_polygon(self):
        self.corners = []      # dicts {"pt": Point3d, "on": [(ci, t)]}
        self.shape = {}        # k -> [Point3d] scan points along side k (click order)
        self.pend = []         # scan points clicked for the side being drawn
        self.side_g = {}             # side k -> 0/1/2 continuity chosen in its bubble
        self.mirror_sides = []
        self.mirror_axis = None
        self.bubbles = {}            # side k -> Point3d of its clickable bubble
        self.hover_bubble = None
        self.picks = []        # undo stack
        self.sweep_sides = []  # sides made by Sweep (no scan/mesh under them)

    def corner(self, pt):
        """Corner + all edges/curves it lies on (within tolerance)."""
        on = []
        p = T(pt)
        for ci, c in enumerate(self.cands):
            if not c.near_bbox(p, self.tol_on): continue
            ok, t = c.crv.ClosestPoint(pt)
            if ok and c.crv.PointAt(t).DistanceTo(pt) < self.tol_on:
                on.append((ci, t))
        return {"pt": pt, "on": on}

    def is_mirror_side(self, k):
        """Side k (not on an edge/curve) with both corners near the symmetry plane."""
        ax = MIRROR_AXIS.get(self.s_mirror())
        if ax is None or len(self.corners) < 4 and not k < len(self.corners) - 1:
            return False
        a = self.corners[k]; b = self.corners[(k + 1) % 4]
        if self.common(a, b) is not None:
            return False
        tol = self.s_mtol()
        return abs(T(a["pt"])[ax]) <= tol and abs(T(b["pt"])[ax]) <= tol

    def preview_side(self, k):
        """(kind, cand) of side k as far as it is drawn, or (None, None)."""
        n = len(self.corners)
        if not (k < n - 1 or (n == 4 and k == 3)):
            return None, None
        if self.is_mirror_side(k):
            return "mirror", None
        shaped = self.shape.get(k) or (k == 3 and self.pend)
        if shaped:
            return "scan", None
        pick = self.common(self.corners[k], self.corners[(k + 1) % 4])
        if pick is None:
            return "scan", None
        c = self.cands[pick[0]]
        return c.kind, c

    def side_rows(self, k):
        """CV rows side k takes across the patch: G0 1, G1 2, G2 3.
        A side on mesh RESERVES rows for its future neighbour (free until then)."""
        kind, c = self.preview_side(k)
        if kind == "mirror":                       # like an invisible mirrored neighbour
            return self.level_for_side(k) + 1
        if kind in ("edge", "scan"):
            return self.layout_for_side(k) + 1
        return 1

    def dir_cv(self, d):
        """CV count in direction d (0 PINK = along sides 1,3; 1 BLUE = along 2,4).
        = rows of the two END sides of that direction; None = both on mesh
        (auto by MeshTol). Never below the CVs of an edge/curve along d."""
        ends = (1, 3) if d == 0 else (0, 2)
        along = 0
        for k in ((0, 2) if d == 0 else (1, 3)):
            kind, c = self.preview_side(k)
            if kind in ("edge", "curve") and c is not None and c.ncv and c.ncv <= 8:
                along = max(along, c.ncv)
        # class-A: CVs across = exactly the rows the two ends take
        # (G1+G1 = 4, G2+G2 = 6, G1+G2 = 5, G0 = 1 row) - no extra rows
        n = sum(self.side_rows(k) for k in ends)
        return max(n, along)

    def side_text(self, k, kind):
        if kind == "edge":
            return u"{} \u25BC".format(SIDE_LV_NAME[self.raw_side_level(k)])
        if kind == "curve":
            return "G0 crv"
        if kind == "mirror":
            return u"mirror | {} \u25BC".format(LEVEL_NAME[self.level_for_side(k)])
        return u"scan/mesh | {} later \u25BC".format(LEVEL_NAME[self.level_for_side(k)])

    def bubble_tip(self, k):
        kind, c = self.preview_side(k)
        d_across = 1 - (k % 2)
        ends = (1, 3) if d_across == 0 else (0, 2)
        rows = " + ".join(str(self.side_rows(e)) for e in ends)
        n = self.dir_cv(d_across)
        size = str(n)
        if all(self.preview_side(e)[0] == "scan" for e in ends):
            size += "+ (more if the scan/mesh needs it)"
        if kind == "edge":
            return "click: G0 -> G1 -> G2 to this neighbour   (across: {} = {} CV)".format(rows, size)
        if kind == "curve":
            return "follows the curve (G0), 1 row   (across: {} = {} CV)".format(rows, size)
        if kind == "mirror":
            return ("click: G0 -> G1 -> G2 to the mirrored copy ({} plane). G0 = edge on the plane, "
                    "G1 = 2nd row straight across it, G2 = 3rd row at twice its distance   (across: {} = {} CV)".format(
                        self.s_mirror(), rows, size))
        return ("click: reserve rows for a FUTURE G0 -> G1 -> G2 neighbour here; free (follow the scan/mesh) "
                "until then   (across: {} = {} CV)".format(rows, size))

    def cycle_cv(self, k):
        kind, c = self.preview_side(k)
        if kind not in ("edge", "scan", "mirror"):
            print("  side {}: follows a curve (G0) - nothing to switch".format(k + 1)); return
        cur = self.raw_side_level(k)
        if kind == "edge":                  # G0 -> G1 -> G2 -> G2>G0 -> G1>G0 -> G0
            self.side_g[k] = (cur + 1) % 5
            v = self.side_g[k]
            if v >= 3:
                print("  side {}: {} - laid out like {} ({} rows), only the edge row matched to the neighbour "
                      "(the seam is G0; MatchUVN can raise it later)".format(k + 1, SIDE_LV_NAME[v],
                                                                            LEVEL_NAME[self.layout_for_side(k)],
                                                                            self.layout_for_side(k) + 1))
            else:
                print("  side {}: {} to neighbour".format(k + 1, LEVEL_NAME[v]))
            return
        self.side_g[k] = (min(cur, 2) + 1) % 3
        if kind == "mirror":
            print("  side {}: {} to the mirrored copy".format(k + 1, LEVEL_NAME[self.side_g[k]]))
        else:
            print("  side {}: rows reserved for a future {} neighbour".format(k + 1, LEVEL_NAME[self.side_g[k]]))

    def size_text(self):
        return "{} x {} CV".format(self.dir_cv(0), self.dir_cv(1))

    def raw_side_level(self, k):
        """The bubble's choice 0..4 (SIDE_LV_NAME), default = option."""
        if k in self.side_g:
            return self.side_g[k]
        return 2 if self.s_cont() == "G2" else (1 if self.s_cont() == "G1" else 0)

    def level_for_side(self, k):
        """Continuity MATCHED to the neighbour on side k: G2>G0 / G1>G0 = G0."""
        v = self.raw_side_level(k)
        if v >= 3:
            kind, c = self.preview_side(k)
            return 0 if kind == "edge" else (2 if v == 3 else 1)
        return v

    def layout_for_side(self, k):
        """Continuity the side's ROWS are laid out for: G2>G0 = G2, G1>G0 = G1."""
        v = self.raw_side_level(k)
        return {3: 2, 4: 1}.get(v, v)

    def edge_snap_dist(self):
        """A corner / side point this close (model units) to an edge or its
        end goes ONTO it: 2 x MeshTol."""
        return max(2.0 * self.s_tol(), self.tol_on)

    def snap_to_edges(self, pt):
        """Mesh point near an edge/curve -> (point on it, distance), its END
        preferred (the neighbour's corner); None if nothing is near."""
        dmax = self.edge_snap_dist(); p = T(pt)
        best_end = None; best_on = None
        for c in self.cands:
            if not c.near_bbox(p, dmax): continue
            for q in (c.p0, c.p1):
                d = q.DistanceTo(pt)
                if d < dmax and (best_end is None or d < best_end[1]): best_end = (rg.Point3d(q), d)
            ok, t = c.crv.ClosestPoint(pt)
            if ok:
                q = c.crv.PointAt(t); d = q.DistanceTo(pt)
                if d < dmax and (best_on is None or d < best_on[1]): best_on = (q, d)
        return best_end or best_on

    def pend_on_edge(self, a, b, pend):
        """Side a -> b with clicked points pend: True if a and b lie on the same
        edge/curve and every point lies on it too (within the snap distance)
        - then the side simply follows that edge (the points were only
        clicked along it)."""
        pick = self.common(a, b)
        if pick is None or not pend: return False
        c = self.cands[pick[0]]; dmax = self.edge_snap_dist()
        for q in pend:
            ok, t = c.crv.ClosestPoint(q)
            if not ok or c.crv.PointAt(t).DistanceTo(q) > dmax: return False
        return True

    def add_corner(self, pt, forced_on=None):
        cc = self.corner(pt)
        if not cc["on"] and not forced_on:
            sn = self.snap_to_edges(pt)                # scan corner right beside an edge: put it ON the edge
            if sn is not None and sn[1] > self.tol_on:
                self.flash_at("[SNAP] corner {} pulled {} onto the edge next to it (it was closer than {}). "
                              "To leave a gap: click further away, or zoom in".format(
                                  len(self.corners) + 1, fl(sn[1]), fl(self.edge_snap_dist())), sn[0], 8.0)
                pt = sn[0]; cc = self.corner(pt)
        if self.corners and pt.DistanceTo(self.corners[-1]["pt"]) < self.tol_on:
            print("  [SKIP] same point as previous corner"); return False
        if len(self.corners) == 3 and pt.DistanceTo(self.corners[0]["pt"]) < self.tol_on:
            print("  [SKIP] that closes a triangle - a patch needs 4 corners"); return False
        for (ci, t) in (forced_on or []):
            if all(ci != x[0] for x in cc["on"]):
                cc["on"].append((ci, t))
        k = len(self.corners) - 1          # side that this corner ends
        moved = list(self.pend)
        if k >= 0 and self.pend:
            if self.pend_on_edge(self.corners[-1], cc, self.pend):
                self.flash_at("[SNAP] side {}: all its points are closer than {} to the edge - the side now FOLLOWS "
                              "the edge (no gap). Undo and click further away to leave a gap".format(
                                  k + 1, fl(self.edge_snap_dist())), cc["pt"], 10.0)
            else:
                self.shape[k] = list(self.pend)
        self.pend = []
        self.corners.append(cc)
        self.picks.append(("corner", k, moved, None))
        return True

    def undo(self):
        if not self.picks: return
        e = self.picks.pop()
        if e[0] == "pend":
            if self.pend: self.pend.pop()
        elif e[0] == "corner":
            _, k, moved, last = e
            if self.corners: self.corners.pop()
            self.shape.pop(k, None)
            self.pend = list(moved) + ([last] if last is not None else [])

    def end_side_here(self):
        """Right click while drawing side n: last mesh point becomes corner."""
        if not self.pend:
            return False
        last = self.pend.pop()
        moved = list(self.pend)
        if not self.add_corner(last):
            self.pend.append(last); return False
        e = self.picks.pop()
        self.picks.append(("corner", e[1], moved, last))
        return True

    def side_points(self, k):
        """Corner A, mesh points in click order, corner B."""
        a = self.corners[k]["pt"]; b = self.corners[(k + 1) % 4]["pt"]
        return [a] + list(self.shape.get(k, [])) + [b]

    def side_curve(self, k):
        pts = self.side_points(k)
        if len(pts) == 2:
            return rg.LineCurve(pts[0], pts[1])
        return smooth_curve(pts)

    def handle_pick(self, pt, hits, m=None, sx=None, sy=None):
        """Mesh click = point along the current side (first click = corner 1).
        Click on edge/curve/its end = corner right there, ends the side.
          1. an END of an edge/curve within the snap radius -> the nearest end
             on screen (= the neighbour's corner, exactly)
          2. two edges/curves that really MEET there (corner of a hole) ->
             the corner sits on both
          3. otherwise -> on the nearest edge/curve, under the cursor.
        Two edges merely running close (both sides of a narrow blend) are
        NOT joined: that put the corner in the gap between them."""
        if not hits:
            if not self.corners:
                self.add_corner(pt)               # corner 1
            else:
                self.pend.append(pt)              # point along current side
                self.picks.append(("pend",))
                sn = self.snap_to_edges(pt)
                if sn is not None:
                    self.flash_at("point only {} from the edge next to it (pulled onto it if the whole side is that "
                                  "close: closer than {}) - click further away to leave a gap".format(
                                      fl(sn[1]), fl(self.edge_snap_dist())), pt, 6.0)
            return
        if m is not None:
            ends = []
            for h in hits:
                if h["end"] is None: continue
                c = self.cands[h["ci"]]
                q = c.p0 if h["end"] == 0 else c.p1
                x, y = to_screen(m, T(q))
                ends.append((math.hypot(x - sx, y - sy), h, q))
            if ends:
                d_, h, q = min(ends, key=lambda e: e[0])
                c = self.cands[h["ci"]]
                self.add_corner(rg.Point3d(q), [(h["ci"], c.crv.Domain.T0 if h["end"] == 0 else c.crv.Domain.T1)])
                return
        h0 = hits[0]; c0 = self.cands[h0["ci"]]
        meet = max(self.tol_on, 0.5 * self.s_tol())
        for other in hits[1:]:
            if other["ci"] == h0["ci"]: continue
            c1 = self.cands[other["ci"]]
            P, ta, tb, gap = joint_point(c0.crv, h0["t"], c1.crv, other["t"])
            if gap <= meet:
                self.add_corner(P, [(h0["ci"], ta), (other["ci"], tb)])
                return
        if h0["end"] is not None:
            self.add_corner(rg.Point3d(c0.p0 if h0["end"] == 0 else c0.p1))
        else:
            self.add_corner(c0.crv.PointAt(h0["t"]), [(h0["ci"], h0["t"])])

    def common(self, a, b):
        """Edge/curve both corners lie on -> (ci, ta, tb). Edges beat curves."""
        best = None
        for (ci, ta) in a["on"]:
            for (cj, tb) in b["on"]:
                if ci != cj: continue
                c = self.cands[ci]
                rel = abs(tb - ta) / max(c.crv.Domain.Length, 1e-12)
                if rel < 1e-6: continue
                key = (0 if c.kind == "edge" else 1, rel)
                if best is None or key < best[0]:
                    best = (key, (ci, ta, tb))
        return best[1] if best else None

    def prompt(self):
        # what right click / Esc do here (see collect_polygon): in a mode only that mode ends
        em = getattr(self, "edge_mode", None)
        if em:
            if em.get("side") is None:
                return "FreeEdgeEdt: click the free edge to redraw <right click / Esc = cancel FreeEdgeEdt>"
            if em.get("mode") == "line":
                return "FreeEdgeEdt: points on the scan/mesh where the edge goes <right click = apply, Esc = cancel FreeEdgeEdt>"
            return "FreeEdgeEdt: start AT a corner dot, or BESIDE the patch for a new line <Esc = cancel FreeEdgeEdt>"
        if getattr(self, "match_mode", None):
            return "MatchUVN: click a seam bubble, Apply or Cancel <right click / Esc = cancel MatchUVN>"
        if getattr(self, "fillet_mode", None):
            fm_ = self.fillet_mode
            if fm_.get("kind") == "npatch":
                return "NPatch: corner {} <right click = build, Esc = cancel NPatch>".format(len(fm_["pts"]) + 1)
            nxt = "side B" if fm_["side"] == "A" else "radius"
            return "Fillet side {}: corner {} <right click = {}, Esc = cancel Fillet>".format(fm_["side"], len(fm_["pts"]) + 1, nxt)
        n = len(self.corners)
        info = "{} default, {} {}".format(self.s_cont(), self.s_fit(), self.s_spans())
        if n == 0:
            return "Patch {}: side 1 start ({}), or hover a patch: patch buttons <right click / Esc = end msh2classA>".format(
                len(self.patches) + 1, info)
        if n == 4:
            if self.s_sweep() and not self.pend:
                return ("Side 4 back to corner 1: points on scan/mesh <right click = BUILD - Sweep makes side 4 from "
                        "side 2, Esc = clear this patch>")
            return "Side 4 back to corner 1: points on scan/mesh <right click = BUILD, Esc = clear this patch>"
        if n == 3 and self.s_sweep() and not self.pend:
            return ("Side 3: click on scan/mesh, or right click = BUILD with Sweep (sides 3 and 4 made from 1 and 2) "
                    "<Undo, Esc = clear this patch>")
        if not self.pend:
            return "Side {}: click on scan/mesh (or edge/curve = corner) <Undo, Esc = clear this patch>".format(n)
        return "Side {}: more points on scan/mesh <right click = end side, Esc = clear this patch>".format(n)

    def collect_polygon(self):
        while True:
            gp = ric.GetPoint()
            gp.SetCommandPrompt(self.prompt())
            try: gp.Constrain(self.mesh, False)     # Rhino's own fast pick on the scan
            except Exception: pass
            gp.AcceptNothing(True)
            ui = self.add_options(gp)
            self.down = None
            gp.MouseMove += self.on_move
            gp.MouseDown += self.on_down
            gp.DynamicDraw += self.on_draw
            try: gp.AcceptCustomMessage(True)       # the panel's scan buttons
            except Exception: pass
            # scan buttons only while nothing is half done
            scan_tools_ready(not self.corners and not getattr(self, "match_mode", None)
                             and not getattr(self, "edge_mode", None) and not getattr(self, "fillet_mode", None))
            try:
                res = gp.Get()
            finally:
                scan_tools_ready(False)
            self.hover = None
            if res == Rhino.Input.GetResult.CustomMessage:
                nm = take_pending_tool()
                if nm and nm.startswith("SRF:"):            # Surfaces group: Fillet / NPatch / GlobalMatch
                    try:
                        self.start_surface_tool(nm[4:])
                    except Exception:
                        print("[{}] failed:".format(nm[4:])); print(traceback.format_exc())
                    continue
                if nm:
                    self.pending_tool = nm
                    return "scantool"
                continue
            if res == Rhino.Input.GetResult.Cancel:
                # Esc inside Match / FreeEdgeEdt ends only that mode (the patch
                # buttons come back), not the whole command
                if getattr(self, "edge_mode", None):
                    self.edge_mode = None; print("[FreeEdgeEdt] cancelled - patch buttons back"); continue
                if getattr(self, "fillet_mode", None):
                    self.fillet_mode = None; self.fillet_preview = None; continue
                if getattr(self, "match_mode", None):
                    self.match_mode = None; print("[MatchUVN] cancelled - patch buttons back"); continue
                if self.corners or self.pend:
                    # Esc with an unfinished patch: only that patch is cleared; Esc again ends msh2classA
                    self.reset_polygon()
                    print("[Esc] unfinished patch cleared (finished patches stay) - Esc again = end msh2classA")
                    try: self.refresh()
                    except Exception: pass
                    sc.doc.Views.Redraw()
                    continue
                return "exit"
            if res == Rhino.Input.GetResult.Nothing:
                if getattr(self, "edge_mode", None):
                    self.edge_finish(); continue
                if getattr(self, "fillet_mode", None):
                    try:
                        if self.corner_mode_enter():
                            continue                     # it built something / told what is missing
                    except Exception:
                        print("[Fillet/NPatch] failed:"); print(traceback.format_exc())
                    if getattr(self, "fillet_mode", None) is None:
                        continue
                    self.fillet_mode = None; self.fillet_preview = None; continue
                if getattr(self, "match_mode", None):
                    self.match_mode = None; print("[MatchUVN] cancelled"); continue
                if not self.corners:
                    return "exit"
                if len(self.corners) == 4:
                    if self.pend:
                        if self.pend_on_edge(self.corners[3], self.corners[0], self.pend):
                            print("  side 4: the clicked points lie on the edge - the side follows the edge")
                        else:
                            self.shape[3] = list(self.pend)
                        self.pend = []
                    return "build"
                if len(self.corners) == 3 and not self.pend and self.s_sweep():
                    return "build"                       # Sweep: 2 sides drawn, 3 and 4 are made
                if not self.end_side_here():
                    print("  Side {} needs at least one point (Undo / Clear{}).".format(
                        len(self.corners), "; Sweep=On: right click with no point = build from 2 sides" if
                        self.s_sweep() else ""))
                continue
            if res == Rhino.Input.GetResult.Option:
                self.handle_option(gp.Option(), ui)
                continue
            if res == Rhino.Input.GetResult.Point:
                if self.click_view_button():
                    continue                                    # it was a view button
                if self.down is not None:
                    vp, sx, sy = self.down
                elif self.last_mouse is not None:
                    vp, sx, sy = self.last_mouse
                else:
                    vp = gp.View().ActiveViewport
                    sx, sy = to_screen(xf_tuple(vp), T(gp.Point()))
                pt = gp.Point()
                m = xf_tuple(vp)
                bh = self.bubble_at(m, sx, sy)
                if bh is not None:
                    self.cycle_cv(bh[0]); continue
                pk, sn = self.pick_px(vp)
                hits = self.visible_hits(hit_test(self.cands, m, sx, sy, pk, sn), vp)
                if getattr(self, "match_mode", None) and not self.corners:
                    self.match_click(m, sx, sy); continue
                if getattr(self, "edge_mode", None) and not self.corners:
                    self.edge_click(pt, m, sx, sy); continue
                if getattr(self, "fillet_mode", None) and not self.corners:
                    try:
                        self.fillet_click(m, sx, sy)
                    except Exception:
                        print("[Fillet] failed:"); print(traceback.format_exc())
                        self.fillet_mode = None; self.fillet_preview = None
                    continue
                if not self.corners and not hits:
                    btn = self.srf_button_at(m, sx, sy)
                    if btn is not None:
                        try:
                            self.adjust_patch(btn[0], btn[1])
                        except Exception:
                            print("[{}] failed:".format(btn[1].upper())); print(traceback.format_exc())
                        continue
                if len(self.corners) == 4:
                    if hits:
                        print("  Side 4 closes at corner 1 - click on scan/mesh to bend it, right click = build.")
                    else:
                        self.pend.append(pt); self.picks.append(("pend",))
                    continue
                first = not getattr(self, "warmed", False) and not self.corners
                busy = show_busy("Please wait - preparing the tools (first click only)...") if first else None
                try:
                    self.handle_pick(pt, hits, m, sx, sy)
                    if first:
                        self.warm_hint(pt)
                finally:
                    if busy is not None:
                        hide_busy(busy)
                    self.warmed = True
                if len(self.corners) == 4:
                    print("  4 corners. Side 4: points on scan/mesh or right click = build.")
                continue
            return "exit"

    def collect_fit_points(self):
        self.fit_pts = []
        while True:
            gp = ric.GetPoint()
            gp.SetCommandPrompt("Fit point {} (Enter = build, Undo)".format(len(self.fit_pts) + 1))
            try: gp.Constrain(self.mesh, False)
            except Exception: pass
            gp.AcceptNothing(True)
            if self.fit_pts: gp.AddOption("UndoPt")
            gp.DynamicDraw += self.on_draw
            gp.MouseMove += self.on_move_view
            gp.MouseDown += self.on_down
            res = gp.Get()
            if res == Rhino.Input.GetResult.Point:
                if self.click_view_button():
                    continue
                self.fit_pts.append(gp.Point()); continue
            if res == Rhino.Input.GetResult.Option:
                if self.fit_pts: self.fit_pts.pop()
                continue
            if res == Rhino.Input.GetResult.Cancel:
                return None
            pts = list(self.fit_pts)
            return pts


