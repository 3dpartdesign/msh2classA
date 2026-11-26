# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# =============================================================================
# DISPLAY MODE FOR SCAN WORK (3dPARTdesign_ScanCAD) - on while the plugin runs
# =============================================================================

def _keep_display_mode(m):
    """A display mode imported by a script lives only in memory: Rhino drops
    it at the next restart, SaveAs or ImportLayout (it was never written to
    the settings). Rhino writes the settings only when it sees a change -
    so: flip InMenu, update, flip back, update. The mode then stays in
    Options > View > Display modes like one you imported yourself."""
    DMD = Rhino.Display.DisplayModeDescription
    try:
        was = m.InMenu
        m.InMenu = not was
        DMD.UpdateDisplayMode(m)
        m.InMenu = True if not was else was
        DMD.UpdateDisplayMode(m)
    except Exception:
        try: DMD.UpdateDisplayMode(m)
        except Exception: pass


def scan_display_mode():
    """The ScanCAD display mode: found by name, otherwise imported once from
    msh2classA_3dPARTdesign_com.ini next to the script (and kept in Rhino's list of
    display modes for good). None if neither exists."""
    DMD = Rhino.Display.DisplayModeDescription
    for nm in SCAN_DISPLAY_NAMES:
        try:
            m = DMD.FindByName(nm)
            if m is not None:
                if not sc.sticky.get("M2CA_display_kept"):      # once per session: make sure it is saved
                    _keep_display_mode(m)
                    sc.sticky["M2CA_display_kept"] = True
                return m
        except Exception:
            pass
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, SCAN_DISPLAY_FILE)
    except Exception:
        path = None
    if path and os.path.isfile(path):
        try:
            mid = DMD.ImportFromFile(path)
            m = DMD.GetDisplayMode(mid)
            if m is not None:
                _keep_display_mode(m)
                sc.sticky["M2CA_display_kept"] = True
                print("[Display] mode '{}' imported from {} - it stays in your display modes".format(
                    m.EnglishName, SCAN_DISPLAY_FILE))
                return m
        except Exception:
            print("[Display] could not import {}".format(path))
    print("[Display] no display mode '{}' and no {} next to the script - the views stay as they are".format(
        SCAN_DISPLAY_NAMES[0], SCAN_DISPLAY_FILE))
    return None


def scan_display_on():
    """Every view to the ScanCAD mode; the modes they had are remembered (also
    in sticky - after a crash the next run still restores the right ones)."""
    mode = scan_display_mode()
    if mode is None: return
    prev = sc.sticky.get("M2CA_prev_display")
    if prev is None:
        prev = {}
        for v in sc.doc.Views.GetViewList(True, False):
            try: prev[str(v.ActiveViewportID)] = v.ActiveViewport.DisplayMode.Id
            except Exception: pass
        sc.sticky["M2CA_prev_display"] = prev
    for v in sc.doc.Views.GetViewList(True, False):
        try:
            if v.ActiveViewport.DisplayMode.Id != mode.Id:
                v.ActiveViewport.DisplayMode = mode
        except Exception:
            pass
    sc.doc.Views.Redraw()


def scan_display_off():
    """Every view back to the mode it had before the plugin. The ScanCAD
    mode itself is NOT removed - it stays in the list of display modes."""
    prev = sc.sticky.get("M2CA_prev_display")
    if not prev: return
    DMD = Rhino.Display.DisplayModeDescription
    for v in sc.doc.Views.GetViewList(True, False):
        try:
            mid = prev.get(str(v.ActiveViewportID))
            m = DMD.GetDisplayMode(mid) if mid is not None else None
            if m is not None and v.ActiveViewport.DisplayMode.Id != m.Id:
                v.ActiveViewport.DisplayMode = m
        except Exception:
            pass
    sc.sticky["M2CA_prev_display"] = None
    sc.doc.Views.Redraw()


