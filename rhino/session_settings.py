# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# Settings stored between runs (sc.sticky)
# -----------------------------------------------------------------------------

# Command-line options: (name, sticky key, shown values, stored values, default)
# Names say what they do; abbreviations: Srf surface, Msh mesh, Pt(s) point(s),
# Cont continuity, Pct percent, Len length, Opt option.
OPT_LISTS = [
    ("DefaultCont", "cont", ["G0Pos", "G1Tan", "G2Curv"], ["G0", "G1", "G2"], "G2"),
    ("FitSrfTo", "fit", ["WholeMesh", "TempPts", "NoScan"], ["Mesh", "Points", "NoScan"], "Mesh"),
    ("SrfSplit", "spans", ["OneSrf", "AutoSplit"], ["Single", "Auto"], "Single"),
    ("MirrorPlane", "mirror", ["Off", "XZ", "YZ", "XY"], ["Off", "XZ", "YZ", "XY"], "Off"),
    ("CornerFlow", "flow", ["KinkOK", "Smooth"], ["Kink", "Smooth"], "Kink"),
]
OPT_TOGGLES = [   # (name, sticky key, default)
    ("SeamLabels", "labels", True),
    ("SideLenHint", "sidehint", True),
]


class SettingsMixin(object):
    def add_options(self, gp):
        """Put all options on the command line; returns name -> handler info."""
        ui = {}
        for (nm, key, shown, vals, dflt) in OPT_LISTS:
            cur = opt_get(key, dflt)
            gp.AddOptionList(nm, shown, vals.index(cur) if cur in vals else vals.index(dflt))
            ui[nm] = ("list", key, vals)
        nums = [("MeshTol", "autotol", self.s_tol(), 1e-6, 1e6, True),
                ("HintTolPct", "tolpct", self.s_tolpct(), 0.01, 10.0, self.s_sidehint()),
                ("MirrorSnapDist", "mtol", self.s_mtol(), 0.0, 1e6, self.s_mirror() != "Off"),
                ("SnapPctOfView", "snap", self.s_snap(), 0.0, 20.0, True)]
        for (nm, key, val, lo, hi, show) in nums:
            if show:
                o = ric.OptionDouble(val, lo, hi); gp.AddOptionDouble(nm, o); ui[nm] = ("num", key, o)
        for (nm, key, dflt) in OPT_TOGGLES:
            o = ric.OptionToggle(bool(opt_get(key, dflt)), "No", "Yes"); gp.AddOptionToggle(nm, o)
            ui[nm] = ("tog", key, o)
        fm_ = getattr(self, "fillet_mode", None)
        if self.corners:
            gp.AddOption("UndoPt"); gp.AddOption("ClearSrf")
        elif fm_ and (fm_["pts"] or fm_.get("ptsA")):
            gp.AddOption("UndoPt")
        if self.patches: gp.AddOption("UndoSrf")
        if self.smooth_undo: gp.AddOption("UndoSrfEdit")
        gp.AddOption("Help")
        return ui

    def handle_option(self, opt, ui):
        name = opt.EnglishName
        h = ui.get(name)
        if h is not None:
            if h[0] == "list": opt_set(h[1], h[2][opt.CurrentListOptionIndex])
            else: opt_set(h[1], h[2].CurrentValue)
            return
        if name == "UndoPt":
            if getattr(self, "fillet_mode", None): self.fillet_undo_pt()
            else: self.undo()
        elif name == "ClearSrf": self.reset_polygon()
        elif name == "UndoSrf": self.undo_patch()
        elif name == "UndoSrfEdit": self.undo_smooth()
        elif name == "Help": print(HELP)

    def s_sidehint(self): return bool(opt_get("sidehint", True))

    def s_fit(self): return opt_get("fit", "Mesh")

    def s_spans(self): return opt_get("spans", "Single")


    def s_snap(self): return opt_get("snap", 3.0)

    def s_legend(self): return bool(opt_get("legend", True))

    def s_showcv(self): return bool(opt_get("showcv", False))

    def pick_px(self, vp):
        """Catch radius as % of the view: same on screen at any zoom, so
        zooming in lets you click NEXT to a corner on purpose."""
        try:
            sz = vp.Size
            diag = math.hypot(sz.Width, sz.Height)
        except Exception:
            diag = 1500.0
        snap = max(SNAP_PX, self.s_snap() / 100.0 * diag)
        return max(PICK_PX, 0.4 * snap), snap

    def s_cont(self): return opt_get("cont", "G2")

    def s_flow(self): return opt_get("flow", "Kink")

    def s_labels(self): return bool(opt_get("labels", True))

    def s_mirror(self): return opt_get("mirror", "Off")

    def s_mtol(self):
        v = opt_get("mtol", None)
        if v is None:
            v = 3.0 * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
        return v

    def s_tolpct(self): return opt_get("tolpct", 0.2)

    def s_tol(self):
        """Allowed mesh deviation in model units (CVs, AutoSplit, leash):
        0.5 mm unless you set it."""
        default = 0.5 * Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Millimeters, sc.doc.ModelUnitSystem)
        return opt_get("autotol", default)


