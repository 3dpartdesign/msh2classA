# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# Smoothing finished patches (neighbours keep continuity)
# -----------------------------------------------------------------------------

# patch buttons in the order of the work:
#   row 1  the CV net (UV, shape stays)     EvenUV  StrUV  RelaxUV
#   row 2  the shape (N, net stays)         PullN   FairN  SmthUVN (both at once)  RstN
#   row 3  special                          ToDevUV MatchUVN FreeEdgeEdt Del
#   (Fillet, NPatch, GlobalMatch: the Surfaces group of the view panel - they work on many patches)
SRF_ROWS = ((("even", "EvenUV"), ("straight", "StrUV"), ("relax", "RelaxUV")),
            (("pull", "PullN"), ("fair", "FairN"), ("smooth", "SmthUVN"), ("reset", "RstN")),
            (("todev", "ToDevUV"), ("match", "MatchUVN"), ("edge", "FreeEdgeEdt"), ("del", "Del")))


# a surface that can not take part (several spans, rational, over 8 CV; untrimmed): only these
SRF_ROWS_REBUILD = ((("rebuild", "Rebuild"), ("del", "Del")),)
REBUILD_CV = 6                   # Rebuild / RebuildAll: CV per direction of the clean patch


def dot_half_width(text):
    """Half the width (px) of a DrawDot with this text - for every clickable dot (patch buttons,
    MatchUVN bubbles): what is drawn is what is clicked."""
    return 3.5 * len(text) + 9


def srf_button_rects(cx, cy, rows=SRF_ROWS):
    """[(mode, label, centre x, centre y, half width)] around the patch centre."""
    out = []; H = 22
    y0 = cy - H * (len(rows) - 1) / 2.0
    for r, row in enumerate(rows):
        ws = [dot_half_width(lb) for (_, lb) in row]
        total = sum(2 * w for w in ws) + 8 * (len(row) - 1)
        x = cx - total / 2.0
        for (mode, lb), w in zip(row, ws):
            out.append((mode, lb, x + w, y0 + H * r, w))
            x += 2 * w + 8
    return out   # (mode, label, dy px)


class SmoothMixin(object):
    """Hover a finished patch: small buttons in its centre (see SRF_ROWS) -
    one step per click; all keep every seam at its G level."""

    def patch_quality(self, oid):
        """Numbers + green / yellow / red per patch button (cached until the
        next change). FairN waves, EvenUV spacing, StrUV kink, RelaxUV skew,
        PullN mesh deviation, MatchUVN seams open or below their G level."""
        cache = self.__dict__.setdefault("qcache", {})
        if oid in cache: return cache[oid]
        res = None
        try:
            got = self.load_patches(oid, "")
            if got is not None:
                A, sp, freeze, ox, tol = got
                allp = [v[0] for v in sp.values()]
                seams, mine = match_seams(A, allp, max(tol, 3.0 * self.s_tol()))
                fixed = set(); open_ = 0
                for (key, sm, B, k, lv) in mine:
                    fine = seam_measure(dict(sm), tol)
                    if fine < lv: open_ += 1             # open, or below the G it should keep
                    if fine < 0: fine = 0
                    for r in range(fine + 1):
                        fixed.update(side_row_indices(A.du, A.dv, k, r))
                q = net_quality(A, fixed)
                saved = self.refs; self.refs = []
                try:
                    dev = self.patch_dev(A, A.P, ox)
                finally:
                    self.refs = saved
                tolm = self.s_tol()
                st = {"fair": quality_state("waves", q["waves"]),
                      "even": quality_state("spacing", q["spacing"]),
                      "straight": quality_state("kink", q["kink"]),
                      "relax": quality_state("skew", q["skew"]),
                      "pull": "red" if dev > tolm else ("yellow" if dev > 0.5 * tolm else "green"),
                      "match": "red" if open_ else "green"}
                txt = "this patch:  waves {} | spacing {:.1f}x | kink {:.0f} deg | skew {:.0f}% | scan/mesh {} | seams {}".format(
                    q["waves"], q["spacing"], q["kink"], q["skew"], fl(dev),
                    "{} not at their G (MatchUVN)".format(open_) if open_ else "ok")
                res = {"state": st, "text": txt}
        except Exception:
            res = None
        cache[oid] = res
        return res

    def srf_centre(self, oid):
        cache = self.__dict__.setdefault("centre_cache", {})
        if oid not in cache:
            for (sid, brep, bb, ok) in self.surfs:
                if sid == oid:
                    f = brep.Faces[0]
                    cache[oid] = f.PointAt(f.Domain(0).Mid, f.Domain(1).Mid)
        return cache.get(oid)

    def srf_rows(self, oid, ok):
        """The buttons a surface gets: all (it takes part), Rebuild + Del (it
        can not: several spans, rational, over 8 CV), none (trimmed - an
        NPatch, or a trimmed surface: Rebuild would bring back what was cut)."""
        if ok: return SRF_ROWS
        return SRF_ROWS_REBUILD if self.can_rebuild(oid) else ()

    def can_rebuild(self, oid):
        for (sid, brep, bb, ok) in self.surfs:
            if sid == oid:
                try:
                    return (not ok) and brep.IsSurface and not rs.GetUserText(oid, NPATCH_KEY)
                except Exception:
                    return False
        return False

    def _btn_under(self, m, sx, sy):
        hs = self.hover_srf
        if hs is None: return None
        rows = self.srf_rows(hs[0], hs[2])
        if not rows: return None
        cx, cy = to_screen(m, T(hs[1]))
        for (mode, label, x, y, w) in srf_button_rects(cx, cy, rows):
            if abs(sx - x) < w and abs(sy - y) < 10:
                return mode
        return None

    def update_srf_buttons(self, vp, m, sx, sy):
        """Keep the buttons of the patch while the mouse is on them, else
        show the buttons of the patch under the cursor."""
        if getattr(self, "match_mode", None):
            self.hover_srf = None
            mm = self.match_mode; mm["hot"] = self._match_under(m, sx, sy)
            return
        if getattr(self, "fillet_mode", None):
            self.hover_srf = None
            fm = self.fillet_mode
            try:
                fm["hot"] = self._fm_corner_at(m, sx, sy)
            except Exception:
                fm["hot"] = None
            return
        if getattr(self, "edge_mode", None):
            self.hover_srf = None
            return
        if self.hover_srf is not None:
            hot = self._btn_under(m, sx, sy)
            if hot is not None:
                self.hover_srf = (self.hover_srf[0], self.hover_srf[1], self.hover_srf[2], hot)
                return
        hs = self.surface_under(vp, m, sx, sy)
        if hs is None:
            self.hover_srf = None; return
        c = self.srf_centre(hs[0]) or hs[1]
        self.hover_srf = (hs[0], c, hs[2], None)

    def srf_button_at(self, m, sx, sy):
        """(object id, mode) of the button clicked, or None."""
        mode = self._btn_under(m, sx, sy)
        return None if mode is None else (self.hover_srf[0], mode)

    def draw_srf_buttons(self, d, vp):
        hs = self.hover_srf
        if hs is None: return
        if getattr(self, "fillet_mode", None) or getattr(self, "match_mode", None) or getattr(self, "edge_mode", None):
            return
        m = xf_tuple(vp)
        cx, cy = to_screen(m, T(hs[1]))
        if not hs[2]:
            try:
                isnp = bool(rs.GetUserText(hs[0], NPATCH_KEY))
            except Exception:
                isnp = False
            rows = self.srf_rows(hs[0], False)
            if not rows:
                d.DrawDot(hs[1], "NPatch" if isnp else "trimmed - can not be changed", C_GRAY, col(255, 255, 255))
                return
            d.DrawDot(float(cx), float(cy - 24), "can not take part (several spans / rational / over 8 CV)",
                      C_GRAY, col(255, 255, 255))
            for (mode, label, x, y, w) in srf_button_rects(cx, cy, rows):
                hot = hs[3] == mode
                bg = C_HOVER if hot else (col(255, 222, 110) if mode == "rebuild" else col(80, 80, 80))
                fg = col(255, 255, 255) if (mode == "del" and not hot) else col(0, 0, 0)
                d.DrawDot(float(x), float(y), label, bg, fg)
            return
        qs = self.patch_quality(hs[0])
        colours = {"green": col(150, 225, 150), "yellow": col(255, 222, 110), "red": col(245, 110, 110)}
        for (mode, label, x, y, w) in srf_button_rects(cx, cy):
            hot = hs[3] == mode
            st = (qs or {}).get("state", {}).get(mode)
            bg = C_HOVER if hot else colours.get(st, col(255, 255, 255))
            fg = col(0, 0, 0)
            if mode == "del" and not hot:               # set apart: dark, not a 'fix' button
                bg = col(80, 80, 80); fg = col(255, 255, 255)
            try:
                d.DrawDot(float(x), float(y), label, bg, fg)
            except Exception:
                d.DrawDot(hs[1], label, bg, col(0, 0, 0))

    def surface_under(self, vp, m, sx, sy):
        """(object id, hit point, smoothable?) of the surface under the cursor."""
        cand = []
        for (oid, brep, bb, ok) in self.surfs:
            xs = []; ys = []
            for x in (bb[0][0], bb[1][0]):
                for y in (bb[0][1], bb[1][1]):
                    for z in (bb[0][2], bb[1][2]):
                        a, b = to_screen(m, (x, y, z)); xs.append(a); ys.append(b)
            if min(xs) <= sx <= max(xs) and min(ys) <= sy <= max(ys):
                cand.append((oid, brep, ok))
        if not cand:
            return None
        ray = cursor_ray(vp, None, rg.Point2d(sx, sy))     # the ONE cursor ray (window point -> frustum line)
        if ray is None:
            return None
        lc = rg.LineCurve(ray[0], ray[0] + ray[1])
        cam = vp.CameraLocation
        best = None
        for (oid, brep, ok) in cand:
            r = rg.Intersect.Intersection.CurveBrep(lc, brep, sc.doc.ModelAbsoluteTolerance)
            pts = r[2] if isinstance(r, tuple) else []
            for pt in pts or []:
                d = pt.DistanceTo(cam)
                if best is None or d < best[0]:
                    best = (d, oid, pt, ok)
        return None if best is None else (best[1], best[2], best[3])

    def adjust_patch(self, oid, mode):
        """One Smooth or Pull2Msh step on patch oid. Same machinery for both
        (adjust_step): only the target for the patch itself differs. Direct
        neighbours change only their continuity rows, all seams are kept,
        the mesh leash is the same, UndoSrfEdit reverts either."""
        if mode in ("even", "straight", "relax", "todev"):
            return self.layout_patch(oid, mode)
        if mode == "match":
            return self.enter_match(oid)
        if mode == "edge":
            return self.enter_edge(oid)
        if mode == "del":
            return self.delete_patch(oid)
        if mode == "rebuild":
            return self.rebuild_one(oid)
        if mode == "reset":
            return self.reset_patch(oid)
        tag = {"smooth": "[SmthUVN]", "fair": "[FairN]", "pull": "[PullN]"}[mode]
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        saved_refs = self.refs; self.refs = []           # no clicks of an old polygon here
        try:
            if mode in ("smooth", "fair"):
                data_fn = None
                target = fair_target(0.5) if mode == "smooth" else fair_normal_target(0.5)
            else:
                # PullN: fit to the scan, but the CVs move only along the normal
                # (free edges also across the edge) - the CV layout from EvenUV /
                # StrUV / RelaxUV stays; neighbours give way with their rows
                data = self.scan_samples(A, A.P, ox)
                if len(data) < A.n():
                    print(tag + " too little scan/mesh under this patch ({} samples). Nothing changed.".format(len(data)))
                    return
                data_fn = None
                target = normal_only(scan_target(data, 1.0))
            allp_ = [v[0] for v in sp.values()]

            def compute(keep_levels):
                moving, used, new = adjust_step(A, allp_, target, self.seam_tol(tol), freeze, data_fn,
                                                self.mirror_spec(ox), None, None, None, keep_levels)
                if not A.move:
                    return None
                # leash: scan deviation may not grow above max(MeshTol, before).
                # PullN: only for the pulled patch - the neighbours may give way
                old = dict((p.key, list(p.P)) for p in moving)
                free_keys = set()                         # NoScan patches: no scan leash
                if mode == "pull":
                    free_keys.update(p.key for p in moving if p is not A)
                for p in moving:
                    try:
                        if rs.GetUserText(p.key, "msh2classA_fit") == "NoScan" and not (mode == "pull" and p is A):
                            free_keys.add(p.key)
                    except Exception:
                        pass
                before = dict((p.key, self.patch_dev(p, old[p.key], ox)) for p in moving)
                alpha = 1.0; after = before; trial = old
                for _ in range(6):
                    trial = dict((p.key, [v_lerp(a, b, alpha) for a, b in zip(old[p.key], new[p.key])]) for p in moving)
                    after = dict((p.key, self.patch_dev(p, trial[p.key], ox)) for p in moving)
                    if all(after[k] <= max(self.s_tol(), before[k]) + 1e-9 or k in free_keys for k in after):
                        break
                    alpha *= 0.5
                return moving, used, trial, alpha, before, after
            res = compute(None)
            if res is None:
                print(tag + " nothing can move: every CV of this patch belongs to a continuity with a patch "
                      "that is not a direct neighbour (e.g. 6x6 with G2 on all sides). Nothing changed.")
                return
        finally:
            self.refs = saved_refs
        moving, used, trial, alpha, before, after = res
        if not self.write_cvs(moving, trial, sp, ox, quiet=True):
            # a seam that is broken already (a fillet built badly) could only be
            # repaired at the neighbours' cost: leave it as it is and do the rest
            keep_all = {}
            for sm in find_seams(allp_, self.seam_tol(tol)):
                keep_all[seam_key(sm)] = seam_level(sm, self.seam_tol(tol))
            saved_refs = self.refs; self.refs = []
            try:
                res = compute(keep_all)
            finally:
                self.refs = saved_refs
            if res is None:
                return
            moving, used, trial, alpha, before, after = res
            if not self.write_cvs(moving, trial, sp, ox):
                return
            print("    broken seam(s) were left as they are (repairing them would spoil the neighbours) - "
                  "MatchUVN on the narrow patch rebuilds it")
        nb = [p for p in moving if p is not A]
        print("{} {} neighbour(s) adjusted (only their continuity rows){}".format(
            tag, len(nb), "" if alpha == 1.0 else ", step reduced to {:.0%} by the scan/mesh leash".format(alpha)))
        for sm in used:
            print("    seam kept {}".format(LEVEL_NAME.get(sm["level"], "?")))
        print("    scan/mesh deviation of this patch (max): {} -> {} (MeshTol {})".format(
            fl(before[A.key]), fl(after[A.key]), fl(self.s_tol())))
        for p in nb:
            if after[p.key] > before[p.key] + 1e-4:
                print("    neighbour gave way: its scan/mesh deviation {} -> {}{}".format(
                    fl(before[p.key]), fl(after[p.key]), "  (pull it too if that is too much)" if after[p.key] > self.s_tol() else ""))
        if freeze:
            print("    rows next to trimmed / multi-span surfaces were kept as they are")
        self.refresh()

    def load_patches(self, oid, tag, extra=None):
        """Patch oid + nearby single-span surfaces as SPatch (centred by ox),
        and rows to keep next to surfaces that cannot take part.
        -> (A, {id: (SPatch, NurbsSurface)}, freeze, ox, seam tol) or None."""
        tol = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 2e-6)
        A_obj = None; near = []; bad = []
        for (sid, brep, bb, ok) in self.surfs:
            if sid == oid: A_obj = (sid, brep, bb, ok)
        if A_obj is None or not A_obj[3]:
            print(tag + " this surface cannot be changed (trimmed, multi-span or rational)."); return None
        bbA = A_obj[2]
        if extra:                                  # several patches at once (all sides of a hole)
            lo = list(bbA[0]); hi = list(bbA[1])
            for (sid, brep, bb, ok) in self.surfs:
                if sid in extra:
                    for c in range(3):
                        lo[c] = min(lo[c], bb[0][c]); hi[c] = max(hi[c], bb[1][c])
            bbA = (tuple(lo), tuple(hi))
        pad = 0.05 * v_dist(bbA[0], bbA[1])
        for item in self.surfs:
            sid, brep, bb, ok = item
            if sid == oid: continue
            if any(bb[1][c] < bbA[0][c] - pad or bb[0][c] > bbA[1][c] + pad for c in range(3)): continue
            (near if ok else bad).append(item)
        ox = tuple(0.5 * (bbA[0][c] + bbA[1][c]) for c in range(3))
        sp = {}
        for (sid, brep, bb, ok) in [A_obj] + near:
            ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
            P = [v_sub(T(ns.Points.GetControlPoint(i, j).Location), ox)
                 for i in range(ns.Points.CountU) for j in range(ns.Points.CountV)]
            sp[sid] = (SPatch(sid, ns.Degree(0), ns.Degree(1), P), ns)
            sp[sid][0].want = side_levels(sid)
        A = sp[oid][0]
        # sides lying on surfaces we cannot handle: keep 3 rows there
        freeze = {}
        btol = {}
        for (bid, bbrep, bbb, bok) in bad:        # an NPatch fits the edges only to a tolerance
            try:
                btol[bid] = self.np_tol(tol) if rs.GetUserText(bid, NPATCH_KEY) else tol
            except Exception:
                btol[bid] = tol
        for sid, (p, ns) in sp.items():
            for k in range(4):
                hits = 0
                for t in (0.2, 0.4, 0.6, 0.8):
                    q = v_add(p.val(k, t, "pt"), ox)
                    for (bid, bbrep, bbb, bok) in bad:
                        cp = bbrep.ClosestPoint(P3(q))
                        if cp.DistanceTo(P3(q)) < btol.get(bid, tol):
                            hits += 1; break
                if hits >= 3:
                    freeze.setdefault(sid, []).append((k, 3))
        return A, sp, freeze, ox, tol

    def seam_tol(self, tol):
        """Seams are FOUND with this (an earlier step may have opened one a
        little - it must still count as a seam and be closed again)."""
        return max(tol, 2.0 * self.s_tol())

    def mirror_spec(self, ox):
        """(axis, plane value in centred coords, tol) of MirrorPlane, or None."""
        ax = MIRROR_AXIS.get(self.s_mirror())
        if ax is None: return None
        return (ax, -ox[ax], max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 1e-5))

    # ---- Match: neighbours back to a hand-edited patch -----------------------
    def enter_match(self, oid):
        """Seam bubbles on every side of the patch where a neighbour is (also
        if hand edits opened the seam); click a bubble = G0 -> G1 -> G2, then
        Apply. The patch stays, the neighbours come to it. No mesh."""
        tag = "[MatchUVN]"
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        loose = max(tol, 3.0 * self.s_tol())          # hand edits may have opened the seam
        allp = [v[0] for v in sp.values()]
        seams, mine = match_seams(A, allp, loose)
        dflt = {"G0": 0, "G1": 1, "G2": 2}.get(self.s_cont(), 2)
        items = []
        ms = self.mirror_spec(ox)
        if ms:                                      # sides near the mirror plane (drawn without snapping)
            axis, val, mt = ms
            diag = v_dist(tuple(min(q[c] for q in A.P) for c in range(3)), tuple(max(q[c] for q in A.P) for c in range(3)))
            near = max(self.s_mtol(), loose, 0.03 * diag)
            taken = set(k for (key, sm, B, k, lv) in mine)
            exact = dict((k, (2 if r2 else (1 if r1 else 0))) for (k, r0, r1, r2) in mirror_rows(A, ms))
            for k in range(4):
                if k in taken: continue
                r0 = side_row_indices(A.du, A.dv, k, 0)
                off_ = max(abs(A.P[i][axis] - val) for i in r0)
                if off_ > near: continue
                q = v_add(A.val(k, 0.5, "pt"), ox)
                now = ("mirror " + LEVEL_NAME[exact[k]]) if k in exact else "{} off the mirror".format(fl(off_))
                items.append({"key": ("mirror", k), "pos": P3(q), "now": now, "want": dflt})
        if not ms:                                  # MirrorPlane Off: is a side near x=0 / y=0 / z=0?
            for ax_, nm_ in ((0, "YZ"), (1, "XZ"), (2, "XY")):
                for k in range(4):
                    r0 = side_row_indices(A.du, A.dv, k, 0)
                    if max(abs(A.P[i][ax_] + ox[ax_]) for i in r0) <= max(self.s_mtol(), loose):
                        self.flash("side {} lies near the {} plane - set MirrorPlane={} and click MatchUVN again".format(
                            k + 1, nm_, nm_), oid)
                        return
        if not mine and not items:
            self.flash("MatchUVN: no neighbour and no mirror plane along the sides of this patch", oid); return
        for (key, sm, B, k, lv) in mine:
            ta, tb = (sm["ta"], sm["tb"]) if sm["X"] is A else (sm["sa"], sm["sb"])
            q = v_add(A.val(k, 0.5 * (ta + tb), "pt"), ox)
            fine = seam_measure(dict(sm), tol)
            now = "gap {}".format(fl(sm["gap"])) if fine < 0 else LEVEL_NAME.get(fine, "?")
            # proposed: the level this seam was BUILT / last matched with (stored
            # on the patches) - after hand edits that is what it should go back to
            stored = max(A.want.get(k, -1) if hasattr(A, "want") else -1,
                         B.want.get(sm["l"] if sm["X"] is A else sm["k"], -1) if hasattr(B, "want") else -1)
            items.append({"key": key, "pos": P3(q), "now": now, "want": stored if stored >= 0 else dflt,
                          "src": "stored" if stored >= 0 else "not stored - set it"})
        self.match_mode = {"oid": oid, "items": items, "loose": loose, "hot": None,
                           "centre": self.srf_centre(oid)}
        print(tag + " {} seam(s): click a bubble to choose G0 / G1 / G2, then Apply "
              "(right click = cancel). The patch stays; the neighbours come to it.".format(len(items)))

    def flash_at(self, text, pt, secs=6.0):
        """flash() at a point of the model (not at a patch centre)."""
        print("  " + text)
        self.flash_msg = (text, pt, time.time() + secs)
        sc.doc.Views.Redraw()

    def flash(self, text, oid=None, secs=6.0):
        """A message on screen at the patch centre for a few seconds (and in
        the console) - so 'nothing happens' never happens silently."""
        print("  " + text)
        pos = self.srf_centre(oid) if oid is not None else None
        self.flash_msg = (text, pos, time.time() + secs)
        sc.doc.Views.Redraw()

    def draw_flash(self, d):
        fm = getattr(self, "flash_msg", None)
        if not fm: return
        if time.time() > fm[2]:
            self.flash_msg = None; return
        try:
            if fm[1] is not None:
                try:                       # ABOVE the patch buttons (they sit around the centre)
                    sp_ = d.Viewport.WorldToClient(fm[1])
                    d.DrawDot(float(sp_.X), float(sp_.Y) - 75.0, fm[0], col(255, 235, 150), col(0, 0, 0))
                except Exception:
                    d.DrawDot(fm[1], fm[0], col(255, 235, 150), col(0, 0, 0))
            else:
                lm = getattr(self, "last_mouse", None)
                if lm is not None:
                    d.DrawDot(float(lm[1]) + 20.0, float(lm[2]) - 30.0, fm[0], col(255, 235, 150), col(0, 0, 0))
                else:
                    d.Draw2dText(fm[0], col(0, 0, 0), rg.Point2d(40, 40), False, 14)
        except Exception:
            pass

    def _match_text(self, it):
        return u"{}{} \u25BC  (now {}{})".format("mirror " if it["key"][0] == "mirror" else "",
                                                  LEVEL_NAME[it["want"]], it["now"],
                                                  ", " + it["src"] if it.get("src") else "")

    def _match_rects(self, m):
        """(kind, n, x, y, half width) of every MatchUVN bubble - drawn AND
        clicked by this one list, so the click area is the bubble itself."""
        mm = self.match_mode
        out = []
        for n, it in enumerate(mm["items"]):
            x, y = to_screen(m, T(it["pos"]))
            out.append(("seam", n, x, y, dot_half_width(self._match_text(it))))
        c = mm.get("centre")
        if c is not None:
            cx, cy = to_screen(m, T(c))
            out.append(("apply", None, cx - 40, cy, dot_half_width("Apply")))
            out.append(("cancel", None, cx + 40, cy, dot_half_width("Cancel")))
        return out

    def label_free(self, vp):
        """-> f(point): may a label be drawn there? Not under a MatchUVN bubble."""
        mm = getattr(self, "match_mode", None)
        if not mm:
            return lambda p: True
        m = xf_tuple(vp)
        rects = self._match_rects(m)

        def f(p):
            x, y = to_screen(m, T(p))
            return not any(abs(x - rx) < w + 40 and abs(y - ry) < 20 for (_, _, rx, ry, w) in rects)
        return f

    def _match_under(self, m, sx, sy):
        """Nearest Match bubble / Apply / Cancel to the click (a bit generous:
        the bubbles sit on top of seam labels and are easy to miss)."""
        best = None
        for (kind, n, x, y, w) in self._match_rects(m):
            if abs(sx - x) < w + 4 and abs(sy - y) < 13:
                d = abs(sy - y) * 3.0 + abs(sx - x)
                if best is None or d < best[0]: best = (d, (kind, n))
        return None if best is None else best[1]

    def match_click(self, m, sx, sy):
        hit = self._match_under(m, sx, sy)
        mm = self.match_mode
        if hit is None:
            print("  [MatchUVN] click a seam bubble, Apply or Cancel (right click / Esc = cancel MatchUVN; the patch "
                  "buttons are hidden while MatchUVN is open)"); return
        kind, n = hit
        if kind == "seam":
            it = mm["items"][n]; it["want"] = (it["want"] + 1) % 3
        elif kind == "cancel":
            self.match_mode = None; print("[MatchUVN] cancelled")
        else:
            self.match_mode = None
            try:
                self.apply_match(mm)
            except Exception:
                print("[MatchUVN] failed:"); print(traceback.format_exc())
        sc.doc.Views.Redraw()

    def apply_match(self, mm):
        tag = "[MatchUVN]"
        got = self.load_patches(mm["oid"], tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        want = dict((it["key"], it["want"]) for it in mm["items"] if it["key"][0] != "mirror")
        msides = dict((it["key"][1], it["want"]) for it in mm["items"] if it["key"][0] == "mirror")
        moved, new, txt = match_step(A, allp, want, mm["loose"], freeze, self.mirror_spec(ox), msides)
        if not moved:
            print(tag + " " + txt); return
        if not self.write_cvs(moved, new, sp, ox, want):
            return
        for (xk, k, yk, l), lv in want.items():           # remembered: every button keeps this level from now on
            set_side_level(xk, k, lv); set_side_level(yk, l, lv)
        print(tag + " " + txt + " (neighbours: only their continuity rows; this patch: only its rows at the mirror; "
              "UndoSrfEdit undoes it)")
        self.refresh()

    def draw_match(self, d, vp):
        mm = getattr(self, "match_mode", None)
        if not mm: return
        m = xf_tuple(vp); hot = mm.get("hot")
        for (kind, n, x, y, w) in self._match_rects(m):
            if kind == "seam":
                it = mm["items"][n]
                txt = self._match_text(it)
                bg = C_HOVER if hot == ("seam", n) else col(120, 220, 120)
            else:
                txt = "Apply" if kind == "apply" else "Cancel"
                bg = C_HOVER if hot == (kind, None) else col(255, 255, 255)
            try:
                d.DrawDot(float(x), float(y), txt, bg, col(0, 0, 0))
            except Exception:
                pass

    # ---- FreeEdgeEdt: redraw a free edge of a finished patch ------------------
    def enter_edge(self, oid):
        """1. click the free edge to change (the nearest one lights up);
        2a. start AT one of its corners, points on the mesh, end AT the other
            corner: only the middle of the edge moves, the corners stay;
        2b. start BESIDE the patch: points on the mesh along where the edge
            should be, right click / Enter: the WHOLE edge (corners too) goes
            onto that line; the patch follows near the edge, neighbours at
            its corners come along and keep their G0/G1/G2."""
        tag = "[FreeEdgeEdt]"
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        free = free_sides_of(A, allp, self.seam_tol(tol), self.mirror_spec(ox), hold_neighbours=False)
        nps_ = self._npatches()
        if nps_:
            free = [k for k in free if not self._side_on_npatch(A, k, ox, nps_, self.np_tol(tol))]
        if not free:
            self.flash(tag + " this patch has no free edge (every side has a neighbour, the mirror or an NPatch)", oid); return
        edges = {}
        for k in free:
            edges[k] = [P3(v_add(A.val(k, a / 24.0, "pt"), ox)) for a in range(25)]
        self.edge_mode = {"oid": oid, "free": free, "edges": edges, "side": None, "mode": None,
                          "start": None, "pts": [], "corners": {}}
        print(tag + " click the free edge you want to change (it lights up when the mouse is near). "
              "Right click = cancel.")

    def _edge_side_at(self, m, sx, sy, rad=60.0):
        """Nearest free edge on screen (generous - easy to hit)."""
        em = self.edge_mode; best = None
        for k, pts in em["edges"].items():
            scr = [to_screen(m, T(p)) for p in pts]
            for i in range(len(scr) - 1):
                (x0, y0), (x1, y1) = scr[i], scr[i + 1]
                dx, dy = x1 - x0, y1 - y0; l2 = dx * dx + dy * dy
                f = 0.0 if l2 < 1e-9 else max(0.0, min(1.0, ((sx - x0) * dx + (sy - y0) * dy) / l2))
                dd = math.hypot(x0 + f * dx - sx, y0 + f * dy - sy)
                if dd < rad and (best is None or dd < best[0]): best = (dd, k)
        return None if best is None else best[1]

    def _corner_at(self, m, sx, sy):
        em = self.edge_mode; best = None
        rad = 25.0                              # pixels
        for i, p in em["corners"].items():
            x, y = to_screen(m, T(p))
            dd = math.hypot(x - sx, y - sy)
            if dd < rad and (best is None or dd < best[0]): best = (dd, i)
        return None if best is None else best[1]

    def edge_click(self, pt, m, sx, sy):
        em = self.edge_mode
        if em["side"] is None:                       # 1. which edge
            k = self._edge_side_at(m, sx, sy)
            if k is None:
                print("  [FreeEdgeEdt] click closer to a free edge of the patch"); return
            em["side"] = k
            got = self.load_patches(em["oid"], "[FreeEdgeEdt]")
            if got is None: self.edge_mode = None; return
            A, sp, freeze, ox, tol = got
            r0 = side_row_indices(A.du, A.dv, k, 0)
            em["corners"] = {r0[0]: P3(v_add(A.P[r0[0]], ox)), r0[-1]: P3(v_add(A.P[r0[-1]], ox))}
            print("  [FreeEdgeEdt] edge {}: start AT a corner dot and end AT the other one = only the middle moves; "
                  "start BESIDE the patch = the whole edge goes onto your line (right click = done)".format(k + 1))
            return
        if em["mode"] is None:                       # 2. how: from a corner, or a free line
            c = self._corner_at(m, sx, sy)
            if c is not None:
                em["mode"] = "corner"; em["start"] = c
            else:
                if pt is None: return
                em["mode"] = "line"; em["pts"] = [pt]
            return
        if em["mode"] == "line":
            if pt is not None: em["pts"].append(pt)
            return
        c = self._corner_at(m, sx, sy)
        if c is None or c == em["start"]:
            if pt is not None: em["pts"].append(pt)
            return
        self._edge_apply_corner(c)

    def edge_finish(self):
        """Right click / Enter in FreeEdgeEdt: a free line is applied, else cancel."""
        em = self.edge_mode
        if em and em.get("mode") == "line" and len(em["pts"]) >= 2:
            self._edge_apply_line(); return True
        self.edge_mode = None
        print("[FreeEdgeEdt] cancelled - patch buttons back")
        return False

    def _edge_samples(self, crv, ox, t0=None, t1=None, n=41):
        dom = crv.Domain
        a0 = dom.T0 if t0 is None else t0; a1 = dom.T1 if t1 is None else t1
        out = []
        for a in range(n):
            q = crv.PointAt(a0 + (a1 - a0) * a / (n - 1.0))
            mp = self.mesh.ClosestPoint(q)
            out.append(v_sub(T(mp if mp.IsValid else q), ox))
        return out

    def _edge_commit(self, A, sp, allp, freeze, ox, tol, newP, what):
        loose = self.seam_tol(tol)
        old = dict((p.key, list(p.P)) for p in allp)
        seams0, mine0 = match_seams(A, allp, loose)
        want = {}
        for (key, sm, B, k, lv) in mine0:            # the G each seam must keep
            want[key] = max(lv, 0)
        shift = max(v_dist(a_, b_) for a_, b_ in zip(A.P, newP))
        A.P = newP
        # the seams must still be found after the corner moved away from them
        moved, new, txt = match_step(A, allp, want, max(loose, 1.2 * shift), freeze,
                                     self.mirror_spec(ox)) if want else ([], {}, "")
        for p in allp:
            p.P = old[p.key]                         # write_cvs compares with the old CVs
        new[A.key] = newP
        if A not in moved: moved = [A] + list(moved)
        if not self.write_cvs(moved, new, sp, ox):
            return
        nb = [p for p in moved if p is not A]
        print("[FreeEdgeEdt] {}{}".format(what, "" if not nb else
              "; {} neighbour(s) came along with their continuity rows (same G)".format(len(nb))))
        self.refresh()

    def _edge_apply_corner(self, c):
        em = self.edge_mode; side = em["side"]
        got = self.load_patches(em["oid"], "[FreeEdgeEdt]")
        self.edge_mode = None
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        path = [em["corners"][em["start"]]] + list(em["pts"]) + [em["corners"][c]]
        samp = self._edge_samples(smooth_curve(path), ox)
        samp[0] = A.P[em["start"]]; samp[-1] = A.P[c]
        free = free_sides_of(A, allp, self.seam_tol(tol), self.mirror_spec(ox), hold_neighbours=False)
        newP = refit_free_edge(A, side, samp, free.get(side, (0, 0)))
        self._edge_commit(A, sp, allp, freeze, ox, tol, newP,
                          "edge {} redrawn between its corners (corners stayed)".format(side + 1))

    def _edge_apply_line(self):
        em = self.edge_mode; side = em["side"]
        got = self.load_patches(em["oid"], "[FreeEdgeEdt]")
        self.edge_mode = None
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        crv = smooth_curve(list(em["pts"]))
        try:                                         # a little longer, so both corners find their place on it
            ext = crv.Extend(rg.CurveEnd.Both, 0.5 * crv.GetLength(), rg.CurveExtensionStyle.Line)
            if ext is not None: crv = ext
        except Exception:
            pass
        r0 = side_row_indices(A.du, A.dv, side, 0)
        c0 = P3(v_add(A.P[r0[0]], ox)); c1 = P3(v_add(A.P[r0[-1]], ox))
        ok0, t0 = crv.ClosestPoint(c0); ok1, t1 = crv.ClosestPoint(c1)
        if not (ok0 and ok1) or abs(t1 - t0) < 1e-9 * max(1.0, crv.Domain.Length):
            print("[FreeEdgeEdt] the line does not run along this edge - nothing changed"); return
        samp = self._edge_samples(crv, ox, t0, t1)
        newP = move_free_edge(A, side, samp, self.mirror_spec(ox))
        self._edge_commit(A, sp, allp, freeze, ox, tol, newP,
                          "edge {} moved onto your line (corners too); the patch followed near the edge".format(side + 1))

    def draw_edge_mode(self, d, vp):
        em = getattr(self, "edge_mode", None)
        if not em: return
        hot = em["side"]
        if hot is None and self.last_mouse is not None:
            try:
                vp_, sx, sy = self.last_mouse
                hot = self._edge_side_at(xf_tuple(vp_), sx, sy)
            except Exception:
                hot = None
        for k, pts in em["edges"].items():
            if k == hot:
                d.DrawPolyline(pts, C_HOVER, 5)
            elif em["side"] is None:
                d.DrawPolyline(pts, col(255, 255, 255), 2)
        for i, p in em["corners"].items():
            d.DrawDot(p, "start" if i == em["start"] else "corner", C_HOVER if i == em["start"] else col(255, 255, 255),
                      col(0, 0, 0))
        path = list(em["pts"])
        if em["mode"] == "corner" and em["start"] is not None:
            path = [em["corners"][em["start"]]] + path
        cur = getattr(self, "cur_pt", None)
        if em["mode"] is not None and cur is not None: path = path + [cur]
        if len(path) > 1:
            draw_smooth(d, path, col(40, 170, 70), 2)
        for p in em["pts"]:
            d.DrawPoint(p, col(0, 0, 0))

    def layout_patch(self, oid, mode):
        """Even / Straight / ToDev: CVs move along the surface, the shape stays
        (<= 0.3 MeshTol). Rows crossing a seam take the neighbour's matching
        rows along; seams are made exact again (same G as before)."""
        tag = {"even": "[EvenUV]", "straight": "[StrUV]", "relax": "[RelaxUV]", "todev": "[ToDevUV]"}[mode]
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp = [v[0] for v in sp.values()]
        devs = None
        if mode == "todev":
            saved_refs = self.refs; self.refs = []
            try:
                data = self.scan_samples(A, A.P, ox, 21)
            finally:
                self.refs = saved_refs
            if len(data) < A.n():
                print(tag + " too little scan/mesh under this patch. Nothing changed."); return
            devs = [(u, v, v_dist(srf_point(A.du, A.dv, A.P, u, v), q)) for (u, v, q) in data]
        old = dict((p.key, list(p.P)) for p in allp)
        new, txt, moved = layout_step(A, allp, mode, self.seam_tol(tol), freeze, devs, 0.3 * self.s_tol(),
                                      self.mirror_spec(ox))
        for p in allp:
            p.P = old[p.key]                     # write_cvs compares with the old CVs
        if new is None:
            self.flash(tag + " " + txt, oid, 10.0); return
        if not self.write_cvs(moved, new, sp, ox, quiet=True):
            # broken seams (a badly built fillet) stay as they are - only the rest
            new, txt, moved = layout_step(A, allp, mode, self.seam_tol(tol), freeze, devs, 0.3 * self.s_tol(),
                                          self.mirror_spec(ox), True)
            for p in allp:
                p.P = old[p.key]
            if new is None:
                self.flash(tag + " " + txt, oid, 10.0); return
            if not self.write_cvs(moved, new, sp, ox):
                return
            txt += "; broken seam(s) left as they are - MatchUVN on the narrow patch rebuilds it"
        print(tag + " " + txt)
        if "NOT moved" in txt:                   # half done: say on screen why, not only in the console
            self.flash(tag + " only partly: " + "; ".join(x for x in txt.split("; ") if "NOT moved" in x), oid, 10.0)
        self.refresh()

    def write_cvs(self, moving, cvs, sp, ox, allow=None, quiet=False, skip_seams=False, whole=False):
        """Put new CVs into the document, one undo record for the whole step.
        CV order rule first (cv_order_ok): the step is shortened until no CV
        jumps over / squeezes against the CV next to it in UV; if even 1/8 of
        it breaks the rule nothing is written. -> share of the step written
        (0 = nothing)."""
        alpha = 1.0
        why = ""
        allp_ = [v[0] for v in sp.values()]
        ftol = self.seam_tol(self.s_tol())
        gtol = max(0.05 * self.s_tol(), 1e-6)
        try:
            st0 = None if skip_seams else seam_states(allp_, ftol, gtol)
        except Exception:
            st0 = None
        for _ in range(4):
            bad = ""
            if st0 is not None:                # rule 2: no seam gets worse (also between neighbours)
                keep_ = dict((p.key, p.P) for p in moving)
                try:
                    for p in moving:
                        p.P = [v_lerp(a, b, alpha) for a, b in zip(keep_[p.key], cvs[p.key])]
                    ok_, t_ = seams_not_worse(st0, seam_states(allp_, ftol, gtol), allow, gtol)
                finally:
                    for p in moving:
                        p.P = keep_[p.key]
                if not ok_:
                    bad = t_
            for p in moving:
                if bad:
                    break
                tr = [v_lerp(a, b, alpha) for a, b in zip(p.P, cvs[p.key])]
                ok, t_ = cv_order_ok(p.P, tr, p.du.n, p.dv.n)
                if not ok:
                    nm = ""
                    try: nm = sc.doc.Objects.FindId(p.key).Attributes.Name or ""
                    except Exception: pass
                    bad = "{}: {}".format(nm or ("this patch" if p is moving[0] else "a neighbour"), t_)
                    break
            if not bad:
                break
            why = bad
            alpha *= 0.5
        else:
            if quiet:
                return 0.0
            txt_ = ("nothing changed - " + why + ". A narrow patch (fillet) tied G2 on all its rows cannot be "
                    "cleaned without spoiling its neighbours: MatchUVN on it (it is rebuilt from them), at G1 "
                    "or G0 on its short end")
            try: self.flash(txt_, moving[0].key if moving else None, 10.0)
            except Exception: print("  " + txt_)
            return 0.0
        if alpha < 1.0 and whole:              # all or nothing (a fillet is built for the whole step)
            self.flash("Not applied: " + why, None, 8.0)
            return 0.0
        if alpha < 1.0:
            cvs = dict((p.key, [v_lerp(a, b, alpha) for a, b in zip(p.P, cvs[p.key])]) for p in moving)
            print("  step shortened to {:.0%} ({})".format(alpha, why))
        undo = []
        for p in moving:
            if max(v_dist(a, b) for a, b in zip(p.P, cvs[p.key])) < 1e-9: continue
            obj = sc.doc.Objects.FindId(p.key)
            try:
                if obj.GripsOn: obj.GripsOn = False     # Rhino control points on: grips would be stale
            except Exception:
                pass
            undo.append((p.key, obj.Geometry.Duplicate()))
            ns = sp[p.key][1]
            nu, nv = ns.Points.CountU, ns.Points.CountV
            for i in range(nu):
                for j in range(nv):
                    q = v_add(cvs[p.key][i * nv + j], ox)
                    ns.Points.SetControlPoint(i, j, rg.ControlPoint(P3(q)))
            if isinstance(obj.Geometry, rg.Brep):
                sc.doc.Objects.Replace(p.key, ns.ToBrep())
            else:
                sc.doc.Objects.Replace(p.key, ns)
        if undo:
            self.smooth_undo.append(undo)
        sc.doc.Views.Redraw()
        return alpha

    def side_sign(self, p, P, ox):
        """Orientation of patch p's normal towards the mesh side we work on:
        stored at build; otherwise majority of the nearest faces."""
        try:
            s = rs.GetUserText(p.key, "msh2classA_side")
            if s in ("1", "-1"): return float(s)
        except Exception:
            pass
        votes = 0.0
        for (u, v) in ((0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
            D = srf_derivs(p.du, p.dv, P, u, v, 1)
            n0 = v_cross(D[(1, 0)], D[(0, 1)])
            mp = self.mesh.ClosestMeshPoint(P3(v_add(D[(0, 0)], ox)), 0.0)
            if mp is None: continue
            fn = self.mesh.FaceNormals[mp.FaceIndex]
            votes += 1.0 if fn.X * n0[0] + fn.Y * n0[1] + fn.Z * n0[2] >= 0 else -1.0
        return 1.0 if votes >= 0 else -1.0

    def reset_patch(self, oid):
        """RstN: the patch as smooth as its edges allow - CVs only along the
        normal, the mesh and the TempPoints NOT used; the rows that hold its
        seams (G0 / G1 / G2) stay, the neighbours keep their seams. Then
        PullN brings it to the mesh / its TempPoints."""
        tag = "[RstN]"
        got = self.load_patches(oid, tag)
        if got is None: return
        A, sp, freeze, ox, tol = got
        allp_ = [v[0] for v in sp.values()]
        orig = dict((p.key, list(p.P)) for p in allp_)
        moved = {}
        target = fair_normal_target(1.0)
        try:
            for it in range(40):
                moving, used, new = adjust_step(A, allp_, target, self.seam_tol(tol), freeze, None,
                                                self.mirror_spec(ox), None, None, None, None)
                if not A.move:
                    break
                step = 0.0
                for p in moving:
                    step = max(step, max(v_dist(a, b) for a, b in zip(p.P, new[p.key])))
                    p.P = list(new[p.key]); moved[p.key] = p
                if step < 1e-6 * self.size:
                    break
            final = dict((k, list(p.P)) for k, p in moved.items())
        finally:
            for p in allp_:
                p.P = orig[p.key]
        if not moved:
            self.flash("RstN: every CV holds a seam - nothing to reset", oid, 5.0); return
        if not self.write_cvs(list(moved.values()), final, sp, ox):
            return
        self.flash("RstN: smoothest the edges allow - now PullN", oid, 5.0)
        self.refresh()

    def patch_points(self, oid):
        """The TempPoints a patch was built through ([(x, y, z)] world), or None."""
        cache = self.__dict__.setdefault("pts_cache", {})
        if oid not in cache:
            try:
                txt = rs.GetUserText(oid, PTS_KEY)
                cache[oid] = [tuple(q) for q in json.loads(txt)] if txt else None
            except Exception:
                cache[oid] = None
        return cache[oid]

    def scan_samples(self, p, P, ox, n=None):
        """[(u, v, mesh point - ox)] under patch p, visible side only. A patch
        built through TempPoints: its points instead of the mesh."""
        pts = self.patch_points(p.key)
        if pts:
            out = []
            for q in pts:
                ql = v_sub(q, ox)
                u, v = project_param(p.du, p.dv, P, ql)
                if -1e-3 <= u <= 1.0 + 1e-3 and -1e-3 <= v <= 1.0 + 1e-3:
                    out.append((min(1.0, max(0.0, u)), min(1.0, max(0.0, v)), ql))
            return out
        sgn = self.side_sign(p, P, ox)
        n = n or max(9, 2 * max(p.du.n, p.dv.n) + 3)
        out = []
        for i in range(n):
            for j in range(n):
                u, v = i / (n - 1.0), j / (n - 1.0)
                D = srf_derivs(p.du, p.dv, P, u, v, 1)
                q = self.scan_pt(P3(v_add(D[(0, 0)], ox)), v_mul(v_cross(D[(1, 0)], D[(0, 1)]), sgn), fit=True)
                if q is not None:
                    out.append((u, v, v_sub(T(q), ox)))
        return out

    def patch_dev(self, p, P, ox):
        """Max distance of patch p (CVs P, centred by ox) to the mesh."""
        dmax = 0.0
        for (u, v, q) in self.scan_samples(p, P, ox, 9):
            dmax = max(dmax, v_dist(srf_point(p.du, p.dv, P, u, v), q))
        return dmax

    def rebuild_surface(self, oid):
        """THE rebuild (Rebuild button and RebuildAll): surface oid -> a clean
        single-span REBUILD_CV x REBUILD_CV patch on it (rebuild_fit), the same
        object (attributes, layer) - one undo entry. -> (undo entry,
        deviation, deviation on the edges, old CV count) or None."""
        for (sid, brep, bb, ok) in self.surfs:
            if sid == oid: break
        else:
            return None
        if not self.can_rebuild(oid):
            return None
        obj = sc.doc.Objects.FindId(oid)
        if obj is None: return None
        ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
        old = "{}x{} CV{}{}".format(ns.Points.CountU, ns.Points.CountV,
                                    ", {}x{} spans".format(ns.SpanCount(0), ns.SpanCount(1))
                                    if ns.SpanCount(0) > 1 or ns.SpanCount(1) > 1 else "",
                                    ", rational" if ns.IsRational else "")
        ox = tuple(0.5 * (bb[0][c] + bb[1][c]) for c in range(3))
        d0, d1 = ns.Domain(0), ns.Domain(1)

        def fn(u, v):
            return v_sub(T(ns.PointAt(d0.ParameterAt(u), d1.ParameterAt(v))), ox)
        P, dev, dev_e = rebuild_fit(fn, REBUILD_CV, REBUILD_CV)
        du = BDir(REBUILD_CV - 1, 1); dv = BDir(REBUILD_CV - 1, 1)
        new = make_nurbs(du, dv, P, ox)
        if new is None or not new.IsValid:
            return None
        try:
            if obj.GripsOn: obj.GripsOn = False
        except Exception:
            pass
        entry = (oid, obj.Geometry.Duplicate())
        sc.doc.Objects.Replace(oid, new.ToBrep() if isinstance(obj.Geometry, rg.Brep) else new)
        try:                             # (no msh2classA_fit: it has no stored G levels - an UndoSrfEdit leaves no tag)
            rs.SetUserText(oid, "msh2classA_side", str(self.scan_side_sign(new)))
        except Exception:
            pass
        return entry, dev, dev_e, old

    def rebuild_note(self, dev, dev_e):
        if dev <= self.s_tol():
            return "off the old surface by {} (edges {})".format(fl(dev), fl(dev_e))
        return ("off the old surface by {} (edges {}) - MORE than MeshTol: {} CV can not carry its shape "
                "(UndoSrfEdit, or draw new patches over it)".format(fl(dev), fl(dev_e), REBUILD_CV))

    def rebuild_one(self, oid):
        """Rebuild (button on a surface that can not take part)."""
        r = self.rebuild_surface(oid)
        if r is None:
            print("[Rebuild] this surface can not be rebuilt (trimmed: the part cut away would come back)"); return
        entry, dev, dev_e, old = r
        self.smooth_undo.append([entry])
        self.refresh(); sc.doc.Views.Redraw()
        print("[Rebuild] {} -> one clean {}x{} CV patch, {}. It takes part now: MatchUVN brings its seams "
              "to G1 / G2. UndoSrfEdit = back".format(old, REBUILD_CV, REBUILD_CV, self.rebuild_note(dev, dev_e)))
        self.flash("Rebuild: {}x{} CV, off by {}".format(REBUILD_CV, REBUILD_CV, fl(dev)), oid, 6.0)

    def rebuild_all(self):
        """RebuildAll (Surfaces group): every visible surface near the scan/mesh
        that can not take part (several spans, rational, over 8 CV; not
        trimmed) is rebuilt like Rebuild - one UndoSrfEdit for all."""
        tag = "[RebuildAll]"
        todo = [sid for (sid, brep, bb, ok) in self.surfs if self.can_rebuild(sid)]
        if not todo:
            print(tag + " nothing to rebuild: every surface near the scan/mesh takes part (or is trimmed)"); return
        busy = show_busy("RebuildAll...")
        step = []; worst = 0.0
        try:
            for k, sid in enumerate(todo):
                r = self.rebuild_surface(sid)
                if r is None:
                    print("  {}: can not be rebuilt".format(k + 1)); continue
                entry, dev, dev_e, old = r
                step.append(entry); worst = max(worst, dev)
                print("  {}: {} -> {}x{} CV, {}".format(k + 1, old, REBUILD_CV, REBUILD_CV, self.rebuild_note(dev, dev_e)))
        finally:
            hide_busy(busy)
        if step:
            self.smooth_undo.append(step)
        self.refresh(); sc.doc.Views.Redraw()
        print(tag + " {} surface(s) rebuilt, worst {} - they take part now (MatchUVN brings their seams to G1 / G2). "
              "One UndoSrfEdit = all back".format(len(step), fl(worst)))
        self.flash("RebuildAll: {} surface(s), worst {}".format(len(step), fl(worst)), None, 8.0)

    def delete_patch(self, oid):
        """Del: the patch is deleted (UndoSrfEdit brings it back). Its
        neighbours are not changed."""
        obj = sc.doc.Objects.FindId(oid)
        if obj is None: return
        geo = obj.Geometry.Duplicate(); at = obj.Attributes.Duplicate()
        sc.doc.Objects.Delete(oid, True)
        self.smooth_undo.append([("deleted", geo, at)])
        for ids in self.patches:
            if oid in ids: ids.remove(oid)
        self.patches = [ids for ids in self.patches if ids]
        self.hover_srf = None
        print("[Del] patch deleted (UndoSrfEdit brings it back)")
        self.refresh(); sc.doc.Views.Redraw()

    def undo_smooth(self):
        # any half-done mode ends first: its edges / preview go, the patch buttons come back
        was = [nm for nm in ("fillet_mode", "edge_mode", "match_mode") if getattr(self, nm, None)]
        self.fillet_mode = None; self.edge_mode = None; self.match_mode = None
        self.fillet_preview = None; self.hover_srf = None
        if not self.smooth_undo:
            print("[UNDO] nothing to undo"); sc.doc.Views.Redraw(); return
        for entry in reversed(self.smooth_undo.pop()):
            if len(entry) == 3 and entry[0] == "deleted":
                nid = sc.doc.Objects.Add(entry[1], entry[2])
                if nid and str(nid) != "00000000-0000-0000-0000-000000000000": self.patches.append([nid])
                continue
            if len(entry) == 2 and entry[0] == "added":
                sc.doc.Objects.Delete(entry[1], True)
                for ids in self.patches:
                    if entry[1] in ids: ids.remove(entry[1])
                self.patches = [ids for ids in self.patches if ids]
                continue
            oid, geo = entry
            sc.doc.Objects.Replace(oid, geo)
        sc.doc.Views.Redraw(); self.refresh()
        print("[UNDO] last patch-button step undone{}".format(
            " (Fillet / Match / FreeEdgeEdt mode ended - patch buttons back)" if was else ""))

    # ---- Fillet -----------------------------------------------------------
    def _all_spatches(self):
        """Every changeable single-span patch of the document (no fillets) as
        SPatch, centred by one ox. -> (list, ox, tol)."""
        tol = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 2e-6)
        items = [it for it in self.surfs if it[3]]
        if not items: return [], (0.0, 0.0, 0.0), tol
        lo = [min(it[2][0][c] for it in items) for c in range(3)]
        hi = [max(it[2][1][c] for it in items) for c in range(3)]
        ox = tuple(0.5 * (lo[c] + hi[c]) for c in range(3))
        out = []
        for (sid, brep, bb, ok) in items:
            try:
                isf = bool(rs.GetUserText(sid, "msh2classA_fillet"))
            except Exception:
                isf = False
            ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
            P = [v_sub(T(ns.Points.GetControlPoint(i, j).Location), ox)
                 for i in range(ns.Points.CountU) for j in range(ns.Points.CountV)]
            sp_ = SPatch(sid, ns.Degree(0), ns.Degree(1), P)
            sp_.is_fillet = isf
            out.append(sp_)
        return out, ox, tol

    # ---- Fillet / NPatch: you click patch corners -------------------------
    # One scheme, as for drawing a patch: left click = corner, right click =
    # go on, UndoPt = last corner back, Esc = cancel.
    def _corner_mode(self, kind, oid=None):
        allp, ox, tol = self._all_spatches()
        st = self.seam_tol(tol)
        ct = max(5.0 * tol, 1e-3)          # corners of patches meeting are snapped: tight (a gap is bigger)
        self.hover_srf = None
        self.fillet_mode = {"kind": kind, "oid": oid, "allp": allp, "ox": ox, "tol": tol, "st": st, "ct": ct,
                            "corners": patch_corners(allp, ct), "pts": [], "segs": [],
                            "side": "A", "ptsA": [], "segsA": [], "clicks": [], "clicksA": [], "graph": None,
                            "hot": None, "fillets": [p for p in allp if getattr(p, "is_fillet", False)]}
        self.fillet_preview = None
        self.flash("Fillet: first and last corner of side A" if kind == "fillet" else
                   "NPatch: click the corners round the hole", None, 6.0)

    def enter_fillet(self, oid=None):
        self._corner_mode("fillet", oid)

    def enter_npatch(self, oid=None):
        self._corner_mode("npatch", oid)

    def _fm_corner_at(self, m, sx, sy, rad=22.0):
        fm = self.fillet_mode
        ox = fm["ox"]; best = None
        for i, c in enumerate(fm["corners"]):
            x, y = to_screen(m, v_add(c, ox))
            dd = math.hypot(x - sx, y - sy)
            if dd < rad and (best is None or dd < best[0]): best = (dd, i)
        return None if best is None else best[1]

    def _fm_edge(self, i, j):
        fm = self.fillet_mode
        return edge_between(fm["allp"], fm["corners"][i], fm["corners"][j], fm["ct"])

    def fillet_click(self, m, sx, sy):
        """Left click in Fillet / NPatch: a corner."""
        fm = self.fillet_mode
        i = self._fm_corner_at(m, sx, sy)
        if i is None:
            self.flash("Click a corner", None, 3.0); return
        pts = fm["pts"]
        if i in pts:
            self.flash("Corner already used", None, 3.0); return
        if not pts:
            pts.append(i); fm["segs"].append(None); fm["clicks"].append(1); return
        seg, why = self._fm_edge(pts[-1], i)
        if seg is not None and fm["kind"] == "fillet" and not (min(seg[2], seg[3]) < 1e-3 and max(seg[2], seg[3]) > 1.0 - 1e-3):
            seg = None
        if seg is not None:
            run = [(i, seg)]
        else:                                  # several edges: along the free edges between the corners
            if fm["graph"] is None:
                busy = show_busy("Edges...")
                try:
                    fm["graph"] = free_edge_graph(fm["allp"], fm["corners"], fm["ct"])
                finally:
                    hide_busy(busy)
            run = edge_path(fm["graph"], pts[-1], i)
            if not run:
                self.flash("No free edges to this corner", None, 4.0); return
        if fm["kind"] == "fillet" and fm["side"] == "B" and any(s_[0] is t_[0] for (c_, s_) in run
                                                               for t_ in fm["segsA"][1:]):
            self.flash("This edge is on side A", None, 4.0); return
        if any(c_ in pts for (c_, s_) in run):
            self.flash("Corner already used", None, 3.0); return
        for (c_, s_) in run:
            pts.append(c_); fm["segs"].append(s_)
        fm["clicks"].append(len(run))

    def fillet_undo_pt(self):
        """The last CLICK back (with the edges it took)."""
        fm = self.fillet_mode
        if fm["pts"]:
            n = fm["clicks"].pop() if fm["clicks"] else 1
            del fm["pts"][-n:]; del fm["segs"][-n:]
        elif fm["kind"] == "fillet" and fm["side"] == "B":          # back to side A
            fm["pts"], fm["segs"], fm["clicks"] = fm["ptsA"], fm["segsA"], fm["clicksA"]
            fm["ptsA"], fm["segsA"], fm["clicksA"] = [], [], []; fm["side"] = "A"

    def corner_mode_enter(self):
        """Right click in Fillet / NPatch. -> True = stay in the mode."""
        fm = self.fillet_mode
        pts = fm["pts"]
        if not pts and not (fm["kind"] == "fillet" and fm["side"] == "B"):
            return False
        if fm["kind"] == "npatch":
            if len(pts) < 3:
                self.flash("NPatch: at least 3 corners", None, 4.0); return True
            close, why = self._fm_edge(pts[-1], pts[0])
            if close is None:
                self.flash("NPatch: no free edge back to corner 1", None, 4.0); return True
            L = []
            for seg in fm["segs"][1:] + [close]:
                p, k, ta, tb = seg
                L.append((p, k, min(ta, tb), max(ta, tb), ta < tb))
            try:
                self.npatch_build(L, fm["ox"], fm["tol"])
            except Exception:
                print("[NPatch] failed:"); print(traceback.format_exc())
            self.fillet_mode = None
            return True
        # fillet
        if len(pts) < 2:
            self.flash("Fillet: at least 2 corners on side {}".format(fm["side"]), None, 4.0); return True
        if fm["side"] == "A":
            fm["ptsA"], fm["segsA"], fm["clicksA"] = pts, fm["segs"], fm["clicks"]
            fm["pts"], fm["segs"], fm["clicks"] = [], [], []; fm["side"] = "B"
            self.flash("Fillet: first and last corner of side B", None, 5.0)
            return True
        try:
            done = self.gap_fillet_step()
        except Exception:
            print("[Fillet] failed:"); print(traceback.format_exc()); done = False
        if done:
            self.fillet_mode = None
        return True

    def _fillet_next_to(self, chain):
        """A fillet already made that this gap runs into (at a station):
        -> (R, level, shape) of it, or None. The new pieces take its radius
        and section so the two meet cleanly."""
        fm = self.fillet_mode
        ends = []
        for q in (chain[0], chain[-1]):
            ends.extend(_edge_ends(q[0], q[1])); ends.extend(_edge_ends(q[2], q[3]))
        tol_ = fm.get("ct", 1e-3)
        for F in fm.get("fillets", []):
            nu, nv = F.du.n, F.dv.n
            cor = [F.P[0], F.P[nv - 1], F.P[(nu - 1) * nv], F.P[nu * nv - 1]]
            if sum(1 for c in cor if any(v_dist(c, e) < tol_ for e in ends)) < 2:
                continue
            try:
                txt = rs.GetUserText(F.key, "msh2classA_fillet") or ""
                a = txt.split(";")
                R0 = float(a[0]); R1 = float(a[1]) if a[1] else None
                lv = int(a[2]); sh = float(a[3])
                if len(a) < 5 or a[4] != "fixed":
                    sh = None              # it was Shape 0 = auto: the new pieces are auto too
            except Exception:
                continue
            if R1 is not None:          # its radius at the end that touches this gap
                c0 = [F.P[0], F.P[nv - 1]]
                at0 = sum(1 for c in c0 if any(v_dist(c, e) < tol_ for e in ends)) >= 2
                R0 = R0 if at0 else R1
            return R0, lv, sh
        return None

    # ---- Fillet: side A corners, right click, side B corners, right click --
    def _fillet_chains(self, sp):
        """The clicked corners -> (A chain, B chain) of patches of sp:
        [(patch, side, forward)], B running the same way as A."""
        fm = self.fillet_mode
        def chain(segs):
            out = []
            for (p, k, ta, tb) in segs[1:]:
                out.append((sp[p.key][0], k, ta < tb))
            return out
        A_ = chain(fm["segsA"]); B_ = chain(fm["segs"])
        C = fm["corners"]
        a0, a1 = C[fm["ptsA"][0]], C[fm["ptsA"][-1]]
        b0, b1 = C[fm["pts"][0]], C[fm["pts"][-1]]
        if v_dist(a0, b0) + v_dist(a1, b1) > v_dist(a0, b1) + v_dist(a1, b0):
            B_ = [(p, k, not f) for (p, k, f) in reversed(B_)]
        return A_, B_

    def gap_fillet_step(self):
        """Right click after side B: radius step with preview, right click =
        apply, Esc = back to the corners. -> True when applied."""
        fm = self.fillet_mode
        keys = [s_[0].key for s_ in fm["segsA"][1:] + fm["segs"][1:]]
        got = self.load_patches(keys[0], "[Fillet]", extra=keys[1:])
        if got is None:
            return False
        A0, sp, freeze, ox, tol = got
        if any(k_ not in sp for k_ in keys):
            self.flash("Fillet: a patch cannot be changed", None, 5.0); return False
        Ach, Bch = self._fillet_chains(sp)
        # radius: the gap measured along every A edge against the B edge across
        Rs = []
        for (X, kX, f) in Ach:
            q = side_eval(X, kX, 0.5, 0.0, 1)["p"]
            Y, kY, fy = min(Bch, key=lambda b: v_dist(side_eval(b[0], b[1], _closest_t_edge(b[0], b[1], q), 0.0, 1)["p"], q))
            try:
                r_ = radius_from_gap(X, kX, Y, kY)[0]
                if r_: Rs.append(r_)
            except Exception:
                pass
        if not Rs:
            self.flash("Fillet: the sides do not face each other", None, 5.0); return False
        Rm = sum(Rs) / len(Rs)
        ends = [(Ach[0][0], Ach[0][1], Bch[0][0], Bch[0][1], 0), (Ach[-1][0], Ach[-1][1], Bch[-1][0], Bch[-1][1], 0)]
        nextto = self._fillet_next_to(ends)
        last = sc.sticky.get("M2CA_fillet") or {}
        level = last.get("level", 2); shape = None; R1 = None
        if nextto is not None:
            R0, level, shape = nextto
        elif last and abs(last["R0"] - Rm) <= 0.25 * Rm:
            R0 = last["R0"]
        else:
            R0 = round(Rm, 1)
        print("[Fillet] gap {}, R {}".format(fl(Rm), fl(R0)))
        cache = {}

        def _pv(sender, e):
            for ns_ in (getattr(self, "fillet_preview", None) or []):
                try: e.Display.DrawSurface(ns_, col(230, 120, 0), 2)
                except Exception: pass
        Rhino.Display.DisplayPipeline.PostDrawObjects += _pv
        try:
            while True:
                key = (R0, R1, level, shape)
                if key not in cache:
                    busy = show_busy("Rolling the ball...")
                    try:
                        res, why = gap_fillet(Ach, Bch, R0, R1, level, shape)
                    except Exception:
                        res, why = None, "failed"; print(traceback.format_exc())
                    finally:
                        hide_busy(busy)
                    if res is not None:
                        bad = [n_ + 1 for n_, pc in enumerate(res["pieces"])
                               if cv_order_worst(pc["F"], pc["deg"][0] + 1, 6)[0] < 0.0]
                        if bad:
                            why = "CV net crosses in piece {} - Shape 0 or another radius".format(bad[0]); res = None
                    pv = []
                    if res is not None:
                        for pc in res["pieces"]:
                            ns_ = make_nurbs(BDir(pc["deg"][0], 1), BDir(5, 1), pc["F"], ox)
                            if ns_ is not None: pv.append(ns_)
                    cache[key] = (res, why, pv)
                res, why, pv = cache[key]
                self.fillet_preview = pv; sc.doc.Views.Redraw()
                if res is None:
                    print("[Fillet] " + why)
                go = Rhino.Input.Custom.GetOption()
                go.SetCommandPrompt("Fillet R {}{} {}{} <right click = apply, Esc = back to the corners>".format(
                    fl(R0), "" if R1 is None else "-" + fl(R1), LEVEL_NAME.get(level, "G2"),
                    "" if res is None else ", {} pieces".format(len(res["pieces"]))))
                oR = Rhino.Input.Custom.OptionDouble(R0, 1e-6, 1e9)
                oR1 = Rhino.Input.Custom.OptionDouble(R1 if R1 is not None else R0, 1e-6, 1e9)
                oC = Rhino.Input.Custom.OptionToggle(level >= 2, "G1", "G2")
                oS = Rhino.Input.Custom.OptionDouble(shape or 0.0, 0.0, 3.0)
                go.AddOptionDouble("Radius", oR)
                go.AddOptionDouble("RadiusEnd", oR1)
                go.AddOptionToggle("Cont", oC)
                go.AddOptionDouble("Shape", oS)
                go.AcceptNothing(True)
                r = go.Get()
                if r == Rhino.Input.GetResult.Option:
                    nm_ = go.Option().EnglishName
                    if nm_ == "Radius":
                        R0 = oR.CurrentValue
                        if R1 is not None and abs(R1 - R0) < 1e-9: R1 = None
                    elif nm_ == "RadiusEnd":
                        R1 = oR1.CurrentValue if abs(oR1.CurrentValue - R0) > 1e-9 else None
                    elif nm_ == "Cont":
                        level = 2 if oC.CurrentValue else 1
                    elif nm_ == "Shape":
                        shape = oS.CurrentValue if oS.CurrentValue > 0 else None
                    continue
                if r == Rhino.Input.GetResult.Nothing:
                    if res is None:
                        continue
                    self.fillet_preview = None
                    return self.apply_gap(Ach, Bch, res, sp, ox, tol, R0, R1, level, shape is None)
                return False
        finally:
            Rhino.Display.DisplayPipeline.PostDrawObjects -= _pv
            self.fillet_preview = None
            sc.doc.Views.Redraw()

    def apply_gap(self, Ach, Bch, res, sp, ox, tol, R0, R1, level, auto):
        """Write the moved big patches and add the fillet pieces; one
        UndoSrfEdit for all."""
        gt = max(0.05 * self.s_tol(), 1e-4)
        new = dict(res["A"]); new.update(res["B"])
        moving = [X for (X, k, f) in Ach] + [Y for (Y, k, f) in Bch]
        cvs = dict(new)
        # neighbours at the ends of the run: their corner follows
        allp = [v[0] for v in sp.values()]
        for (P_old, k_, f_) in Ach + Bch:
            P_new = SPatch(P_old.key, P_old.du.p, P_old.dv.p, new[P_old.key])
            seams_, mine_ = match_seams(P_old, allp, self.seam_tol(tol))
            for t_end, adj in ((0.0, (k_ + 3) % 4), (1.0, (k_ + 1) % 4)):
                c_old = side_eval(P_old, k_, t_end, 0.0, 1)["p"]
                c_new = side_eval(P_new, k_, t_end, 0.0, 1)["p"]
                if v_dist(c_old, c_new) < gt:
                    continue
                for (key_, sm_, N, kk, lv_) in mine_:
                    if kk != adj or N in moving:
                        continue
                    lN = sm_["l"] if sm_["X"] is P_old else sm_["k"]
                    try:
                        freeN = free_sides_of(N, allp, self.seam_tol(tol), self.mirror_spec(ox), hold_neighbours=False)
                    except Exception:
                        freeN = {}
                    for kN in ((lN + 1) % 4, (lN + 3) % 4):
                        if kN not in freeN:
                            continue
                        for te in (0.0, 1.0):
                            if v_dist(side_eval(N, kN, te, 0.0, 1)["p"], c_old) < 10.0 * gt + 1e-3:
                                PN = follow_corner(N, kN, te, c_new)
                                if PN is not None:
                                    moving.append(N); cvs[N.key] = PN
                                break
                        if N.key in cvs:
                            break
        n0 = len(self.smooth_undo)
        if not self.write_cvs(moving, cvs, sp, ox, skip_seams=True, whole=True):
            return False
        fids = []
        for pc in res["pieces"]:
            ia = pc["A"][0]
            X = Ach[ia][0]
            fid = self._add_fillet_srf(pc, X, Ach[ia][1], new[X.key], ox, R0, R1, level, res["shape"], auto)
            if fid is not None: fids.append(fid)
        for (P_, k_, f_) in Ach + Bch:
            try: set_side_level(P_.key, k_, level)
            except Exception: pass
        if len(self.smooth_undo) > n0:
            step = []
            for st_ in self.smooth_undo[n0:]: step.extend(st_)
            del self.smooth_undo[n0:]
        else:
            step = []
        step.extend(("added", f_) for f_ in fids)
        self.smooth_undo.append(step)
        sc.sticky["M2CA_fillet"] = {"R0": R0, "R1": R1, "level": level, "shape": res["shape"]}
        self.refresh()
        if fids:
            self.join_fillets(fids[0], level)
            if len(fids) > 1: self.join_fillets(fids[-1], level)
        self.flash("Fillet: {} piece(s), R {}".format(len(fids), fl(R0)), None, 6.0)
        sc.doc.Views.Redraw()
        return True

    def _add_fillet_srf(self, pc, X, kX, PX, ox, R0, R1, level, shape, auto):
        """One fillet piece into the document, facing the same way as X."""
        pal = pc["deg"][0]
        du_f = BDir(pal, 1); dv_f = BDir(5, 1)
        PF = list(pc["F"])
        try:
            sgn = -1.0 if rs.GetUserText(X.key, "msh2classA_side") == "-1" else 1.0
        except Exception:
            sgn = 1.0
        DF = srf_derivs(du_f, dv_f, PF, 0.5, 0.0, 1)
        nF = v_cross(DF[(1, 0)], DF[(0, 1)])
        ua, va = SIDE_MAP[kX][0](0.5)
        DA = srf_derivs(X.du, X.dv, PX, ua, va, 1)
        if v_dot(nF, v_mul(v_cross(DA[(1, 0)], DA[(0, 1)]), sgn)) < 0:
            nal = pal + 1
            PF = [PF[(nal - 1 - i) * 6 + j] for i in range(nal) for j in range(6)]
        ns = make_nurbs(du_f, dv_f, PF, ox)
        if ns is None or not ns.IsValid:
            print("[Fillet] [ERROR] invalid fillet surface"); return None
        try:
            attrs = sc.doc.Objects.FindId(X.key).Attributes.Duplicate(); attrs.Name = ""
            fid = sc.doc.Objects.AddSurface(ns, attrs)
        except Exception:
            fid = sc.doc.Objects.AddSurface(ns)
        try:
            rs.SetUserText(fid, "msh2classA_fit", "NoScan")
            rs.SetUserText(fid, "msh2classA_side", "1")
            rs.SetUserText(fid, "msh2classA_fillet", "{:.6g};{};{};{:.3f};{}".format(
                R0, "" if R1 is None else "{:.6g}".format(R1), level, shape, "auto" if auto else "fixed"))
        except Exception:
            pass
        for k_ in (0, 2):
            try: set_side_level(fid, k_, level)
            except Exception: pass
        self.patches.append([fid])
        return fid

    def join_fillets(self, fid, level):
        """A fillet that meets an earlier one at a station: both get G2 (or
        G1) there in one joint solve of their inner rows."""
        got = self.load_patches(fid, "[Fillet]")
        if got is None: return
        F, sp, freeze, ox, tol = got
        fl_ = []; bigs = []
        for k_, (p, ns_) in sp.items():
            try:
                isf = bool(rs.GetUserText(k_, "msh2classA_fillet"))
            except Exception:
                isf = False
            (fl_ if isf else bigs).append(p)
        others = [p for p in fl_ if p is not F]
        touching = [p for p in others if any(F in (sm["X"], sm["Y"]) and p in (sm["X"], sm["Y"])
                                            for sm in find_seams([F, p], self.seam_tol(tol)))]
        if not touching:
            return
        chain = [F] + touching
        gt = max(0.05 * self.s_tol(), 1e-4)
        def levels(patches):
            out = {}
            for sm in find_seams(patches, self.seam_tol(tol)):
                if sm["X"] in chain or sm["Y"] in chain:
                    out[(sm["X"].key, sm["Y"].key)] = (seam_measure(dict(sm), gt), sm)
            return out
        before = levels(chain + bigs)
        ff = [v for (k_, v) in before.items() if v[1]["X"] in chain and v[1]["Y"] in chain]
        if ff and all(v[0] >= level for v in ff):
            print("[Fillet] meets the fillet next to it {} already".format(LEVEL_NAME.get(level, "G2")))
            return
        new = smooth_fillet_chain(chain, bigs, level, self.seam_tol(tol))
        if not new: return
        trial = [SPatch(p.key, p.du.p, p.dv.p, new.get(p.key, p.P)) for p in chain]
        after = levels(trial + bigs)
        worse = [k_ for k_, v in before.items() if k_ in after and after[k_][0] < v[0]]
        if worse:
            print("[Fillet] the joint to the fillet next to it is left as it is (joining would make the seam "
                  "to a big patch worse)")
            return
        if self.write_cvs(chain, new, sp, ox, skip_seams=True, whole=True):
            if len(self.smooth_undo) >= 2:
                last = self.smooth_undo.pop()
                self.smooth_undo[-1].extend(last)                     # one undo step with the fillet
            print("[Fillet] joined {} to the fillet next to it ({})".format("G2" if level >= 2 else "G1",
                                                                       len(touching)))
            self.refresh()

    # ---- NPatch: an N-sided hole --------------------------------------------
    def _npatches(self):
        """[(id, brep)] of the NPatches in the document."""
        out = []
        for (sid, brep, bb, ok) in self.surfs:
            try:
                if rs.GetUserText(sid, NPATCH_KEY):
                    out.append((sid, brep))
            except Exception:
                pass
        return out

    def np_tol(self, tol):
        """A side counts as lying on an NPatch within this (Patch fits the
        edges only to a tolerance)."""
        return max(self.seam_tol(tol), 0.5 * self.s_tol())


    def _side_on_npatch(self, p, k, ox, nps, tol_):
        for (sid, brep) in nps:
            hits = 0
            for t in (0.25, 0.5, 0.75):
                q = P3(v_add(p.val(k, t, "pt"), ox))
                try:
                    if brep.ClosestPoint(q).DistanceTo(q) < tol_: hits += 1
                except Exception:
                    pass
            if hits >= 2:
                return True
        return False

    def npatch_build(self, L, ox, tol):
        tag = "[NPatch]"
        nS = len(L)
        level = {"G0": 0, "G1": 1}.get(self.s_cont(), 2)
        real, flat = loop_corners(L)
        if real == 4 and flat:
            print(tag + " 4 real corners: a normal patch would fit too (untrimmed)")
        whole = lambda e_: e_[2] < 1e-3 and e_[3] > 1.0 - 1e-3
        # ---- the hole's boundary: points on the neighbours' edges + their normals
        bp = []; bn = []; side_of = []
        NS_ = 16
        for j_, e_ in enumerate(L):
            p, k, t0, t1, f = e_
            for i in range(NS_):
                s_ = i / float(NS_)
                t = t0 + (t1 - t0) * (s_ if f else 1.0 - s_)
                E = side_eval(p, k, t, 0.0, 1)
                bp.append(E["p"]); bn.append(v_unit(v_cross(E["t"], E["e"]))); side_of.append(j_)
        poly = list(bp)
        cen = tuple(sum(q[c] for q in poly) / len(poly) for c in range(3))
        hole = max(v_dist(q, cen) for q in poly)
        inner = []
        if self.s_fit() != "NoScan":                    # the scan inside the hole
            c_, ex, ey, Nn = npatch_frame(poly)
            shrink = [v_add(cen, v_mul(v_sub(q, cen), 0.85)) for q in poly]
            for a in range(15):
                for b in range(15):
                    q = v_add(cen, v_add(v_mul(ex, hole * (a / 7.0 - 1.0)), v_mul(ey, hole * (b / 7.0 - 1.0))))
                    if not point_in_loop(shrink, q): continue
                    w = P3(v_add(q, ox))
                    mp = self.mesh.ClosestPoint(w)
                    if mp.IsValid and mp.DistanceTo(w) < 0.5 * hole:
                        inner.append(v_sub(T(mp), ox))
        nscan = len(inner)
        tol_d = sc.doc.ModelAbsoluteTolerance
        busy = show_busy("NPatch ({} sides)...".format(nS))
        try:
            # OUR fit: one clean 6x6 span lined up with the hole (not Rhino's Patch:
            # its big rebuilt surface has a noisy CV net the neighbours would copy)
            S_, fr = fit_npatch(bp, bn, inner)
        finally:
            hide_busy(busy)
        reb = make_nurbs(S_.du, S_.dv, S_.P, ox)
        if reb is None:
            self.flash("NPatch: surface failed", None, 6.0); return False
        # ---- trim: the boundary drawn in the surface's own (u, v) - exactly ON it
        c_, ex, ey, Nn, x0, x1, y0, y1 = fr
        def uv_of(q):
            d = v_sub(q, c_)
            return (min(1.0, max(0.0, (v_dot(d, ex) - x0) / (x1 - x0))), min(1.0, max(0.0, (v_dot(d, ey) - y0) / (y1 - y0))))
        loop2d = []
        for j_, e_ in enumerate(L):
            p, k, t0, t1, f = e_
            pts2 = []
            for i in range(NS_ + 1):
                s_ = i / float(NS_)
                t = t0 + (t1 - t0) * (s_ if f else 1.0 - s_)
                u_, v_ = uv_of(side_eval(p, k, t, 0.0, 1)["p"])
                pts2.append(rg.Point3d(u_, v_, 0.0))
            c2 = rg.Curve.CreateInterpolatedCurve(pts2, 3)
            if c2 is not None: loop2d.append(c2)
        nb = None; c3 = []
        try:
            c3 = [reb.Pushup(c2, tol_d) for c2 in loop2d]
            c3 = [c for c in c3 if c is not None]
            joined = rg.Curve.JoinCurves(c3, 10.0 * tol_d)
            base = reb.ToBrep()
            pieces = base.Faces[0].Split(joined, tol_d)
            if pieces is not None and pieces.Faces.Count > 1:
                for fi in range(pieces.Faces.Count):
                    pc = pieces.Faces[fi].DuplicateFace(False)
                    try:
                        cc = v_sub(T(rg.AreaMassProperties.Compute(pc).Centroid), ox)
                    except Exception:
                        continue
                    if point_in_loop(poly, cc):
                        nb = pc; break
        except Exception:
            print(traceback.format_exc())
        if nb is None:
            try:
                pieces = rg.Brep.CutUpSurface(reb, c3, True, tol_d)
            except Exception:
                pieces = None
            for pc in (pieces or []):
                try:
                    cc = v_sub(T(rg.AreaMassProperties.Compute(pc).Centroid), ox)
                except Exception:
                    continue
                if point_in_loop(poly, cc):
                    nb = pc; break
        if nb is None or not nb.IsValid:
            self.flash("NPatch: trim failed", None, 6.0); return False
        # how well it sits on the neighbours' edges (before they move)
        dev = 0.0; kink = 0.0
        for (p, k, t0, t1, f) in L:
            for s_ in (0.1, 0.3, 0.5, 0.7, 0.9):
                E = side_eval(p, k, t0 + (t1 - t0) * s_, 0.0, 1)
                q = P3(v_add(E["p"], ox))
                ok_, u_, v_ = reb.ClosestPoint(q)
                if not ok_: continue
                dev = max(dev, reb.PointAt(u_, v_).DistanceTo(q))
                n_ = reb.NormalAt(u_, v_); nN = v_unit(v_cross(E["t"], E["e"]))
                kink = max(kink, math.degrees(math.acos(min(1.0, abs(n_.X * nN[0] + n_.Y * nN[1] + n_.Z * nN[2])))))
        try:
            attrs = sc.doc.Objects.FindId(L[0][0].key).Attributes.Duplicate(); attrs.Name = ""
            nid = sc.doc.Objects.AddBrep(nb, attrs)
        except Exception:
            nid = sc.doc.Objects.AddBrep(nb)
        if nid is None or str(nid) == "00000000-0000-0000-0000-000000000000":
            self.flash("NPatch: could not add it", None, 6.0); return False
        try:
            rs.SetUserText(nid, NPATCH_KEY, "{};{:.4g}".format(nS, dev))
            rs.SetUserText(nid, "msh2classA_fit", "NoScan" if not nscan else "Mesh")
        except Exception:
            pass
        n0 = len(self.smooth_undo)
        self.smooth_undo.append([("added", nid)])
        self.patches.append([nid])
        if dev > 2.0 * self.s_tol():
            print(tag + " [WARN] {} off the edges".format(fl(dev)))
        self.refresh()
        # ---- the neighbours come to it (MatchUVN against a strip on its surface)
        res = []
        Lw = [(e_[0], e_[1]) for e_ in L if whole(e_)]
        if level >= 1 and Lw:
            res.append(self._match_to_npatch(Lw, reb, level, hole, tag))
        if len(self.smooth_undo) - n0 > 1:                          # one UndoSrfEdit for all
            merged = []
            for st_ in self.smooth_undo[n0:]: merged.extend(st_)
            del self.smooth_undo[n0:]
            self.smooth_undo.append(merged)
        for r_ in res:
            if r_.startswith("not matched"):
                print(tag + " " + r_)
        self.refresh(); sc.doc.Views.Redraw()
        try:                                   # the seams all round (also the labels on screen)
            obj_ = sc.doc.Objects.FindId(nid)
            got = analyze_npatch_seams(self.cands, [(nid, obj_.Geometry)], sc.doc.ModelAbsoluteTolerance,
                                       1.0 / (100.0 * self.size), 0.5 * self.s_tol())
            cnt = {}
            for (p_, t_, c_) in got:
                k_ = t_.split(" ")[0]; cnt[k_] = cnt.get(k_, 0) + 1
            msg = "NPatch {} sides: ".format(nS) + ", ".join("{} {}".format(k_, cnt[k_]) for k_ in sorted(cnt))
            if len(got) < nS:
                msg += ", {} not touching".format(nS - len(got))
            self.flash(msg, None, 8.0)
        except Exception:
            pass
        return True

    def _match_to_npatch(self, L, reb, level, hole, tag):
        """All neighbours around the hole -> brought to the NPatch surface reb
        at level, in ONE solve (a strip on the NPatch along every side is a
        master that never moves; seams between the neighbours keep their
        level). -> one line for the report."""
        keys = [p.key for (p, k) in L]
        got = self.load_patches(keys[0], tag, extra=keys[1:])
        if got is None:
            return "not matched: a neighbour cannot be changed (trimmed / multi-span)"
        A_, sp, freeze, oxl, tol_l = got
        if any(k_ not in sp for k_ in keys):
            return "not matched: a neighbour was not found"

        class _RhSurf(object):
            def __init__(s_, srf, ox_):
                s_.s = srf; s_.ox = ox_; s_.d0 = srf.Domain(0); s_.d1 = srf.Domain(1)
            def closest(s_, q):
                r_ = s_.s.ClosestPoint(P3(v_add(q, s_.ox)))
                return (r_[1], r_[2]) if r_[0] else None
            def derivs(s_, u_, v_):
                r_ = s_.s.Evaluate(u_, v_, 1)
                pt_, ders_ = r_[1], r_[2]
                n_ = s_.s.NormalAt(u_, v_)
                return v_sub(T(pt_), s_.ox), T(ders_[0]), T(ders_[1]), (n_.X, n_.Y, n_.Z)
            def inside(s_, u_, v_):
                e0 = 1e-9 * max(1.0, s_.d0.Length); e1 = 1e-9 * max(1.0, s_.d1.Length)
                return s_.d0.T0 - e0 <= u_ <= s_.d0.T1 + e0 and s_.d1.T0 - e1 <= v_ <= s_.d1.T1 + e1

        S = _RhSurf(reb, oxl)

        def kink_of(N_, k_, P_=None):
            X_ = SPatch(N_.key, N_.du.p, N_.dv.p, P_ if P_ is not None else N_.P)
            a = 0.0
            for t in (0.1, 0.3, 0.5, 0.7, 0.9):
                E = side_eval(X_, k_, t, 0.0, 1); uv = S.closest(E["p"])
                if uv is None: continue
                nS = S.derivs(*uv)[3]; nN = v_unit(v_cross(E["t"], E["e"]))
                a = max(a, math.degrees(math.acos(min(1.0, abs(v_dot(nS, nN))))))
            return a
        masters = []; sides = []
        for i, (p, k) in enumerate(L):
            Nn = sp[p.key][0]
            Ls = v_dist(Nn.val(k, 0.0, "pt"), Nn.val(k, 1.0, "pt"))
            px = None; why = ""
            for fW in (0.3, 0.15, 0.07):
                px, why = proxy_strip(Nn, k, S, min(fW * Ls, 0.4 * hole), key="_npstrip{}".format(i))
                if px is not None: break
            if px is None:
                return "not matched: side {}: {}".format(i + 1, why)
            masters.append((px, [Nn], level)); sides.append((Nn, k))
        before = [kink_of(N_, k_) for (N_, k_) in sides]
        fz = {}
        for key_, lst in freeze.items():               # the hole's sides are matched now (later buttons keep them)
            fz[key_] = [(kk, r) for (kk, r) in lst if (key_, kk) not in set((N_.key, k_) for (N_, k_) in sides)]
        loose = max(tol_l, 3.0 * self.s_tol())
        A0, o0, _ = masters[0]
        allp = [v[0] for v in sp.values()]
        w0 = dict((key_, level) for (key_, sm, B, kk, lv) in match_seams(A0, [A0] + allp, loose)[1] if B in o0)
        if not w0:
            return "not matched: the seam to the strip is not found"
        moved, new, txt = match_step(A0, allp, w0, loose, fz, self.mirror_spec(oxl), {}, no_narrow=True,
                                     only=o0, extra=masters[1:])
        real = [p for p in moved if p.key in sp]
        if not real:
            return "not matched: " + txt
        if not self.write_cvs(real, new, sp, oxl, whole=True):
            return "not matched (see above)"
        after = [kink_of(N_, k_, new.get(N_.key)) for (N_, k_) in sides]
        return "{} - kink before -> after: {}".format(txt, ", ".join("{:.2f} -> {:.3f} deg".format(a, b)
                                                                     for a, b in zip(before, after)))

    def start_surface_tool(self, nm):
        if nm == "Fillet":
            self.enter_fillet(None)
        elif nm == "NPatch":
            self.enter_npatch(None)
        elif nm == "GlobalMatch":
            self.global_match()
        elif nm == "RebuildAll":
            self.rebuild_all()
        elif nm == "ForceMirror":
            self.force_mirror()
        sc.doc.Views.Redraw()

    def force_mirror(self):
        """ForceMirror: every patch the mirror plane runs across is cut at it
        (the bigger part stays; the same surface, no trim); a patch trimmed
        at the mirror is rebuilt untrimmed from its surface. Then its side on
        the plane gets the mirror continuity of DefaultCont (G2: the 3rd row
        mirrors smoothly). TempPoints on the wrong side are dropped."""
        tag = "[ForceMirror]"
        ax = MIRROR_AXIS.get(self.s_mirror())
        if ax is None:
            self.flash("ForceMirror: set MirrorPlane first", None, 5.0); return
        level = {"G0": 0, "G1": 1}.get(self.s_cont(), 2)
        eps = max(sc.doc.ModelAbsoluteTolerance * 10.0, self.size * 1e-5)
        n0 = len(self.smooth_undo)
        cut = []; failed = 0
        busy = show_busy("ForceMirror...")
        try:
            # trimmed single-span patches at the mirror: rebuilt untrimmed from their surface
            for (sid, brep, bb, ok) in list(self.surfs):
                if ok or bb[0][ax] > eps or bb[1][ax] < -eps: continue
                try:
                    if rs.GetUserText(sid, NPATCH_KEY): continue
                    ns = brep.Faces[0].UnderlyingSurface().ToNurbsSurface()
                except Exception:
                    continue
                if ns.Points.CountU != ns.Degree(0) + 1 or ns.Points.CountV != ns.Degree(1) + 1:
                    continue                                    # not one span
                P = [T(ns.Points.GetControlPoint(i, j).Location)
                     for i in range(ns.Points.CountU) for j in range(ns.Points.CountV)]
                A = SPatch(sid, ns.Degree(0), ns.Degree(1), P)
                try:
                    keep = 1.0 if rg.AreaMassProperties.Compute(brep).Centroid[ax] >= 0 else -1.0
                except Exception:
                    keep = None
                r = cut_at_plane(A, ax, 0.0, keep=keep)
                if r[0] is None:
                    failed += 1; continue
                k, P2, dev, keep = r
                ns2 = make_nurbs(A.du, A.dv, P2, (0.0, 0.0, 0.0))
                if ns2 is None or not ns2.IsValid:
                    failed += 1; continue
                obj = sc.doc.Objects.FindId(sid)
                self.smooth_undo.append([(sid, obj.Geometry.Duplicate())])
                sc.doc.Objects.Replace(sid, ns2.ToBrep())
                cut.append((sid, k, keep))
            self.refresh()
            # untrimmed patches the plane runs across
            for (sid, brep, bb, ok) in list(self.surfs):
                if not ok or any(sid == c_[0] for c_ in cut): continue
                if bb[0][ax] > -eps or bb[1][ax] < eps: continue
                got = self.load_patches(sid, tag)
                if got is None: continue
                A, sp, freeze, ox, tol = got
                r = cut_at_plane(A, ax, -ox[ax])
                if r[0] is None:
                    failed += 1; continue
                k, P2, dev, keep = r
                if not self.write_cvs([A], {A.key: P2}, sp, ox, skip_seams=True, whole=True):
                    failed += 1; continue
                cut.append((sid, k, keep))
                self.refresh()
            # TempPoints only on the side that stays
            for (sid, k, keep) in cut:
                pts = self.patch_points(sid)
                if pts:
                    kept = [q for q in pts if keep * q[ax] >= 0.0]
                    try: rs.SetUserText(sid, PTS_KEY, json.dumps([[round(c, 6) for c in q] for q in kept]))
                    except Exception: pass
                    self.__dict__.setdefault("pts_cache", {}).pop(sid, None)
            # the side on the plane: mirror continuity
            for (sid, k, keep) in cut:
                got = self.load_patches(sid, tag)
                if got is None: continue
                A, sp, freeze, ox, tol = got
                loose = max(tol, 3.0 * self.s_tol())
                moved, new, txt = match_step(A, [v[0] for v in sp.values()], {}, loose, freeze,
                                             self.mirror_spec(ox), {k: level})
                if moved:
                    self.write_cvs(moved, new, sp, ox, whole=True)
                self.refresh()
        finally:
            hide_busy(busy)
        if len(self.smooth_undo) - n0 > 1:                          # one UndoSrfEdit for all
            merged = []
            for st_ in self.smooth_undo[n0:]: merged.extend(st_)
            del self.smooth_undo[n0:]
            self.smooth_undo.append(merged)
        sc.doc.Views.Redraw()
        if cut:
            self.flash("ForceMirror: {} patch(es) cut, {} on the mirror{}".format(
                len(cut), LEVEL_NAME[level], ", {} failed".format(failed) if failed else ""), None, 6.0)
        else:
            self.flash("ForceMirror: no patch crosses the mirror{}".format(
                " ({} could not be cut)".format(failed) if failed else ""), None, 5.0)

    def global_match(self):
        """GlobalMatch: every seam that is below the level stored for it (it
        was built / matched with that) - e.g. after hand edits of CVs - is
        brought back. The patch with the most broken seams stays (the edited
        one, usually) and its neighbours come to it, like MatchUVN; up to 3
        rounds. Seams with no stored level are left as they are."""
        tag = "[GlobalMatch]"
        n0 = len(self.smooth_undo); fixed = 0; refused = 0
        busy = show_busy("GlobalMatch...")
        try:
            for rnd in range(3):
                todo = []
                for (sid, brep, bb, ok) in self.surfs:
                    if not ok: continue
                    got = self.load_patches(sid, tag)
                    if got is None: continue
                    A, sp, freeze, ox, tol = got
                    loose = max(tol, 3.0 * self.s_tol())
                    seams, mine = match_seams(A, [v[0] for v in sp.values()], loose)
                    n = 0
                    for (key, sm, B, k, lv) in mine:
                        stored = max(A.want.get(k, -1) if hasattr(A, "want") else -1,
                                     B.want.get(sm["l"] if sm["X"] is A else sm["k"], -1) if hasattr(B, "want") else -1)
                        if stored >= 0 and seam_measure(dict(sm), tol) < stored:
                            n += 1
                    if n: todo.append((n, sid))
                if not todo: break
                todo.sort(key=lambda x: -x[0])
                done_round = 0
                for (n, sid) in todo:
                    got = self.load_patches(sid, tag)
                    if got is None: continue
                    A, sp, freeze, ox, tol = got
                    loose = max(tol, 3.0 * self.s_tol())
                    allp = [v[0] for v in sp.values()]
                    seams, mine = match_seams(A, allp, loose)
                    want = {}
                    for (key, sm, B, k, lv) in mine:
                        stored = max(A.want.get(k, -1) if hasattr(A, "want") else -1,
                                     B.want.get(sm["l"] if sm["X"] is A else sm["k"], -1) if hasattr(B, "want") else -1)
                        if stored >= 0 and seam_measure(dict(sm), tol) < stored:
                            want[key] = stored
                    if not want: continue
                    moved, new, txt = match_step(A, allp, want, loose, freeze, self.mirror_spec(ox), {})
                    if moved and self.write_cvs(moved, new, sp, ox, want, quiet=True):
                        fixed += len(want); done_round += 1
                    else:
                        refused += 1
                    self.refresh()
                if not done_round: break
        finally:
            hide_busy(busy)
        if len(self.smooth_undo) - n0 > 1:                          # one UndoSrfEdit for all
            merged = []
            for st_ in self.smooth_undo[n0:]: merged.extend(st_)
            del self.smooth_undo[n0:]
            self.smooth_undo.append(merged)
        self.refresh(); sc.doc.Views.Redraw()
        if fixed or refused:
            self.flash("GlobalMatch: {} seam(s) fixed{}".format(fixed, ", {} refused".format(refused) if refused else ""),
                       None, 8.0)
        else:
            self.flash("GlobalMatch: all seams OK", None, 5.0)

    def draw_fillet_mode(self, d, vp):
        fm = getattr(self, "fillet_mode", None)
        if not fm: return
        ox = fm["ox"]; C = fm["corners"]
        W = lambda i: P3(v_add(C[i], ox))
        def seg_line(seg, colour, w):
            if seg is None: return
            p, k, ta, tb = seg
            d.DrawPolyline([P3(v_add(side_eval(p, k, ta + (tb - ta) * i / 20.0, 0.0, 1)["p"], ox)) for i in range(21)],
                           colour, w)
        try:
            for c in C:
                d.DrawPoint(P3(v_add(c, ox)), Rhino.Display.PointStyle.RoundSimple, 5, col(150, 150, 150))
            if fm["kind"] == "npatch":
                runs = [("", fm["pts"], fm["segs"], col(255, 140, 0))]
            else:
                runs = [("A", fm["ptsA"], fm["segsA"], col(230, 60, 40))] if fm["side"] == "B" else []
                runs.append((fm["side"], fm["pts"], fm["segs"], col(230, 60, 40) if fm["side"] == "A" else col(40, 90, 230)))
            for (lab, pts, segs, colour) in runs:
                for seg in segs:
                    seg_line(seg, colour, 4)
                for n_, i in enumerate(pts):
                    d.DrawDot(W(i), "{}{}".format(lab, n_ + 1), colour, col(255, 255, 255))
            pts = fm["pts"]; h = fm.get("hot")
            if pts and h is not None and h not in pts:          # where the next click goes
                d.DrawDottedLine(W(pts[-1]), W(h), col(255, 200, 0))
            if h is not None:
                d.DrawPoint(W(h), Rhino.Display.PointStyle.RoundSimple, 9, col(255, 200, 0))
        except Exception:
            pass
        for ns_ in (getattr(self, "fillet_preview", None) or []):
            try:
                d.DrawSurface(ns_, col(230, 120, 0), 4)
            except Exception:
                pass

