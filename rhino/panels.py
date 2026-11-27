# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# ---- scan tools as panel BUTTONS (and command-line options) -----------------
# A button click only leaves a note and wakes up the waiting Get (custom
# message); the tool then runs from the command loop - never from inside
# the click (a tool has its own clicking).
def scan_tools_ready(on):
    sc.sticky["M2CA_tools_ok"] = bool(on)


def post_scan_tool(name):
    if name == "MeshMirror":                     # only drawing on/off: straight away, nothing to leave
        run_scan_tool(name); return
    if not sc.sticky.get("M2CA_tools_ok"):
        print("  [{}] finish the step you are in first (or clear the patch you are drawing)".format(name)); return
    sc.sticky["M2CA_pending_tool"] = name
    try:
        ric.GetBaseClass.PostCustomMessage(name)
    except Exception:
        sc.sticky["M2CA_pending_tool"] = None
        print("  [{}] buttons cannot start it here - use the command-line option {}".format(name, name))


def take_pending_tool():
    nm = sc.sticky.get("M2CA_pending_tool")
    sc.sticky["M2CA_pending_tool"] = None
    return nm


def run_scan_tool(nm, note=""):
    fn = dict(SCAN_TOOLS).get(nm)
    if fn is None: return
    if nm != "MeshMirror":
        print("")
        print("-" * 64)
        print("-  SCAN/MESH TOOL: {}{}".format(SCAN_TOOL_LABEL.get(nm, nm), "   ({})".format(note) if note else ""))
        print("-" * 64)
    try:
        fn()
    except UserAbort:
        print("[{}] stopped by Esc".format(nm))
    except Exception:
        print("[{}] failed:".format(nm)); print(traceback.format_exc())
    finally:
        _ScanDraw.off_all()                 # never leave a tool's drawing on
        _MIRROR_HIDE[0] = False; sc.doc.Views.Redraw()
        _ESC[0] = False                     # an Esc inside the tool ends the tool, not the plugin


SURFACE_TOOLS = (("Fillet", "Fillet"), ("NPatch", "NPatch"), ("GlobalMatch", "GlobalMatch"),
                 ("ForceMirror", "ForceMirror"))


def post_surface_tool(name):
    """A button of the Surfaces group: run in the main loop (like the mesh tools)."""
    if not sc.sticky.get("M2CA_tools_ok"):
        print("  [{}] finish the step you are in first (or clear the patch you are drawing)".format(name)); return
    sc.sticky["M2CA_pending_tool"] = "SRF:" + name
    try:
        ric.GetBaseClass.PostCustomMessage(name)
    except Exception:
        sc.sticky["M2CA_pending_tool"] = None
        print("  [{}] the button cannot start it here".format(name))


def surface_tool_buttons(ef, lay):
    """The 'Surfaces' group of the panel: tools that work on many patches."""
    try:
        lb = ef.Label(); lb.Text = "Surfaces"
        lay.AddRow(lb)
    except Exception:
        pass
    for (nm, label) in SURFACE_TOOLS:
        b = ef.Button(); b.Text = label
        b.Click += (lambda n_: (lambda sender, e: post_surface_tool(n_)))(nm)
        lay.AddRow(b)


def scan_tool_buttons(ef, lay):
    """The 'Meshes' group of the panel: one button per mesh tool."""
    try:
        lb = ef.Label(); lb.Text = "Scans/meshes"
        lay.AddRow(lb)
    except Exception:
        pass
    for (nm, fn) in SCAN_TOOLS:
        b = ef.Button(); b.Text = SCAN_TOOL_LABEL.get(nm, nm)
        b.Click += (lambda n_: (lambda sender, e: post_scan_tool(n_)))(nm)
        lay.AddRow(b)


CAD_SERVICES_EMAIL = "info@3dPARTdesign.com"
CAD_SERVICES_URL = "https://3dPARTdesign.com"
CAD_SERVICES_QUESTIONS = ("Too much work and not enough time?",
                          "Shapes that won't come out the way you planned them?",
                          "Trouble with your scanner or a scan?",
                          "Not sure how to prepare a file for series production?")
CAD_SERVICES_TEXT = ("I can help: on-demand 3D modeling for production - custom body kits, "
                     "NURBS/SubD surfacing, 3D scan cleanup and production preparation.")


def _open_url(url):
    try:
        import Eto.Forms as ef
        ef.Application.Instance.Open(url)
        return True
    except Exception:
        pass
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        print("  Open in your browser: " + url)
        return False


def _wrap_lines(text, width):
    """Text broken into lines of at most width characters (at spaces)."""
    lines = []; cur = ""
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur: lines.append(cur)
    return "\n".join(lines)


def show_cad_services():
    """Popup: who made the plugin and how to reach him."""
    try:
        import Eto.Forms as ef
        import Eto.Drawing as ed
        import Rhino.UI as rui
        dlg = ef.Dialog()
        dlg.Title = "CAD Services"
        dlg.Resizable = False
        dlg.Padding = ed.Padding(14)
        lay = ef.DynamicLayout(); lay.Spacing = ed.Size(6, 8)
        for n_, qtxt in enumerate(CAD_SERVICES_QUESTIONS):
            t = ef.Label(); t.Text = qtxt
            try: t.Font = ed.Font(t.Font.Family, t.Font.Size + (1 if n_ == 0 else 0), ed.FontStyle.Bold)
            except Exception: pass
            lay.AddRow(t)
        b = ef.Label(); b.Text = _wrap_lines(CAD_SERVICES_TEXT, 62)   # own line breaks: the dialog sizes right
        lay.AddRow(b)
        e = ef.Label(); e.Text = "E-mail: " + CAD_SERVICES_EMAIL
        lay.AddRow(e)
        w = ef.Label(); w.Text = "Portfolio: 3dPARTdesign.com"
        lay.AddRow(w)
        info = ef.Label(); info.Text = " "
        bc = ef.Button(); bc.Text = "Copy e-mail"
        bo = ef.Button(); bo.Text = "Open website"
        def on_copy(sender, ev):
            try:
                cb = ef.Clipboard()
                cb.Text = CAD_SERVICES_EMAIL
                info.Text = "E-mail copied."
            except Exception:
                info.Text = CAD_SERVICES_EMAIL
        def on_open(sender, ev):
            _open_url(CAD_SERVICES_URL)
        bc.Click += on_copy
        bo.Click += on_open
        row = ef.DynamicLayout(); row.Spacing = ed.Size(8, 0)
        row.AddRow(bc, bo, None)
        lay.AddRow(row)
        lay.AddRow(info)
        dlg.Content = lay
        dlg.MinimumSize = ed.Size(440, 0)
        try:
            dlg.ShowModal(rui.RhinoEtoApp.MainWindow)
        except Exception:
            dlg.ShowModal()
    except Exception:
        print("CAD Services - " + " ".join(CAD_SERVICES_QUESTIONS) + " " + CAD_SERVICES_TEXT)
        print("  E-mail: {}   Portfolio: {}".format(CAD_SERVICES_EMAIL, CAD_SERVICES_URL))


def cad_services_button(ef, lay):
    """At the very bottom of both panels."""
    try:
        sp = ef.Label(); sp.Text = " "
        lay.AddRow(sp)
        b = ef.Button(); b.Text = "CAD Services"
        b.Click += lambda sender, e: show_cad_services()
        lay.AddRow(b)
    except Exception:
        pass


def open_scan_panel(start_label=None):
    """Small panel with the mesh tools while you pick the mesh; on top the
    button that starts (or goes back to) the patches."""
    try:
        import Eto.Forms as ef
        import Eto.Drawing as ed
        import Rhino.UI as rui
        f = ef.Form()
        f.Title = "msh2classA"
        f.WindowStyle = ef.WindowStyle.Utility
        f.Resizable = False; f.Minimizable = False; f.Maximizable = False
        f.ShowInTaskbar = False
        try: f.Owner = rui.RhinoEtoApp.MainWindow
        except Exception:
            try: f.Topmost = True
            except Exception: pass
        lay = ef.DynamicLayout(); lay.Padding = ed.Padding(4); lay.Spacing = ed.Size(2, 2)
        if start_label:
            bs = ef.Button(); bs.Text = start_label
            try: bs.Font = ed.Font(bs.Font.Family, bs.Font.Size + 2, ed.FontStyle.Bold)
            except Exception: pass
            bs.Click += lambda sender, e: post_scan_tool("DrawPatches")
            lay.AddRow(bs)
            sp = ef.Label(); sp.Text = " "
            lay.AddRow(sp)
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
        return f
    except Exception:
        return None


def close_scan_panel(f):
    if f is None: return
    try: sc.sticky["M2CA_panel_loc"] = (f.Location.X, f.Location.Y)
    except Exception: pass
    try: f.Close()
    except Exception: pass


def _patch_target(preset_id):
    """The scan/mesh to draw the patches on: the one of the patches you left,
    the only visible one, or the one you click."""
    if preset_id is not None and sc.doc.Objects.FindId(preset_id) is not None:
        return preset_id
    vis = [o for o in all_scan_insts() if scan_visible(o)]
    if len(vis) == 1:
        return vis[0].Id
    go = ric.GetObject()
    go.SetCommandPrompt("Click the scan/mesh to draw the patches on <Esc = back to the scan/mesh tools>")
    try:
        go.GeometryFilter = ro.ObjectType.Mesh | ro.ObjectType.InstanceReference
    except Exception:
        import System
        go.GeometryFilter = System.Enum.ToObject(ro.ObjectType, 32 | 4096)
    if go.Get() != Rhino.Input.GetResult.Object:
        return None
    return go.Object(0).ObjectId


def get_scan(preset_id=None):
    """Pick the mesh to work on - or run a mesh tool first (they move only
    the mesh BLOCKS; afterwards pick the mesh). preset_id: the mesh of the
    session that was left for a mesh tool: NOT taken again by itself - the
    button / option BackToPatches goes back to it (after a tool you may want
    another one first)."""
    obj_id = None
    back = preset_id is not None and sc.doc.Objects.FindId(preset_id) is not None
    start_opt = "BackToPatches" if back else "DrawPatches"
    panel = open_scan_panel(">>  Back to the patches  <<" if back else ">>  Draw patches  <<")
    if back:
        print("  Patches wait: the button 'Back to the patches' (or BackToPatches) goes on with them; "
              "or another scan/mesh tool")
    while obj_id is None:
        go = ric.GetObject()
        go.SetCommandPrompt(("BackToPatches = go on with the patches, or a scan/mesh tool <Esc = end msh2classA>")
                            if back else
                            ("Click the scan/mesh (or DrawPatches) to draw patches, or a scan/mesh tool "
                             "<Esc = end msh2classA>"))
        try:
            go.GeometryFilter = ro.ObjectType.Mesh | ro.ObjectType.InstanceReference
        except Exception:
            import System
            go.GeometryFilter = System.Enum.ToObject(ro.ObjectType, 32 | 4096)
        go.EnablePreSelect(True, True)
        idx = {}
        o_start = go.AddOption(start_opt)
        for (nm, fn) in SCAN_TOOLS:
            idx[go.AddOption(nm)] = nm
        su = str(sc.sticky.get("M2CA_scan_units", "mm"))
        o_su = go.AddOptionList("ScanMeshUnits", list(SCAN_UNIT_CHOICES),
                                list(SCAN_UNIT_CHOICES).index(su) if su in SCAN_UNIT_CHOICES else 0)
        try: go.AcceptCustomMessage(True)            # the panel's scan buttons
        except Exception: pass
        scan_tools_ready(True)
        try:
            res = go.Get()
        finally:
            scan_tools_ready(False)
        if res == Rhino.Input.GetResult.CustomMessage:            # a scan button
            nm = take_pending_tool()
            if nm == "DrawPatches":
                obj_id = _patch_target(preset_id if back else None); continue
            if nm: run_scan_tool(nm)
            continue
        if res == Rhino.Input.GetResult.Option:
            if go.Option().Index == o_start:
                obj_id = _patch_target(preset_id if back else None); continue
            if go.Option().Index == o_su:
                sc.sticky["M2CA_scan_units"] = SCAN_UNIT_CHOICES[go.Option().CurrentListOptionIndex]
                print("  ScanMeshUnits = {} - the scan/mesh FILES are in {} (LoadMeshes converts them to the document's "
                      "units)".format(sc.sticky["M2CA_scan_units"], sc.sticky["M2CA_scan_units"]))
                continue
            nm = idx.get(go.Option().Index)
            if nm: run_scan_tool(nm)
            continue
        if res == Rhino.Input.GetResult.Nothing:          # Enter / right click (e.g. left over from a file
            continue                                      # dialog): not a reason to quit - only Esc quits
        if res != Rhino.Input.GetResult.Object:
            close_scan_panel(panel)
            return None
        obj_id = go.Object(0).ObjectId
    close_scan_panel(panel)
    if obj_id is None:
        return None
    obj = sc.doc.Objects.FindId(obj_id)
    if obj is None:
        return None
    get_scan.obj_id = obj_id             # for the Scan on/off view button
    if isinstance(obj.Geometry, rg.Mesh):
        print("[OK] Scan/mesh ({} faces)".format(obj.Geometry.Faces.Count))
        return obj.Geometry
    if not isinstance(obj, ro.InstanceObject):
        print("[ERROR] Not a scan/mesh or block."); return None
    xf = obj.InstanceXform
    key = "{}|{}|{}".format(sc.doc.RuntimeSerialNumber, obj_id,
                            ",".join("{:.6g}".format(xf[i, j]) for i in range(4) for j in range(4)))
    cache = sc.sticky.get("M2CA_meshcache")
    if cache and cache[0] == key:
        print("[OK] Scan/mesh from block (cached, {} faces)".format(cache[1].Faces.Count))
        return cache[1]
    busy = show_busy("Extracting scan/mesh from block (once)...", esc="end msh2classA")
    meshes = []

    def collect(idef, x):
        for i in range(idef.ObjectCount):
            check_esc()
            piece = idef.Object(i)
            if piece is None: continue
            if isinstance(piece.Geometry, rg.Mesh):
                m = piece.Geometry.Duplicate(); m.Transform(x); meshes.append(m)
            elif isinstance(piece, ro.InstanceObject):
                collect(piece.InstanceDefinition, x * piece.InstanceXform)
    try:
        collect(obj.InstanceDefinition, xf)
    finally:
        hide_busy(busy)
    if not meshes:
        print("[ERROR] No scan/mesh in block."); return None
    joined = rg.Mesh()
    for m in meshes:
        joined.Append(m)
    joined.Compact(); joined.RebuildNormals()
    sc.sticky["M2CA_meshcache"] = (key, joined)
    print("[OK] Scan/mesh from block ({} faces) - cached for next runs".format(joined.Faces.Count))
    return joined

