# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 50)
    print(VERSION + "   (Help option explains everything)")
    print(LEGAL_LINE)
    print("=" * 50)
    init_colors()
    preset = None; carry = None; n_total = 0
    while True:
        mesh = get_scan(preset)
        if mesh is None: break
        busy = show_busy("Preparing mesh...")
        try:
            check_esc()
        except UserAbort:
            hide_busy(busy); print("[STOPPED] Esc - script ended"); break
        try:
            v0 = mesh.Vertices[0]
            mesh.ClosestPoint(rg.Point3d(v0.X, v0.Y, v0.Z))     # builds search tree once
        except Exception:
            pass
        try:      # first call of the big helpers is slow (compiling) - do it now, not at the first click
            mesh.ClosestMeshPoint(rg.Point3d(v0.X, v0.Y, v0.Z), 0.0)
            prof = [(float(i), 0.0, 0.01 * i * i) for i in range(31)]
            single_span_reach(prof, 5, 0.2); single_span_reach(prof, 3, 0.2)
            patch_score([[(float(i), float(j), 0.01 * i * j) for j in range(9)] for i in range(9)], 5, 5, 0.2)
        except Exception:
            pass
        if esc_pressed():
            hide_busy(busy); print("[STOPPED] Esc - script ended"); break
        session = Session(mesh)
        session.scan_id = getattr(get_scan, "obj_id", None)
        if carry: session.patches = carry
        if not session.open_panel():
            print("[INFO] no view panel (Eto) - the view buttons are drawn in the top right corner instead")
        hide_busy(busy)
        print("[OK] {} edges/curves pickable".format(len(session.cands)))
        tool = None
        try:
            while True:
                r = session.collect_polygon()
                if r == "exit":
                    break
                if r == "scantool":
                    tool = session.pending_tool
                    break
                try:
                    session.build()
                except Exception:
                    print("[ERROR] build failed:")
                    print(traceback.format_exc())
                session.reset_polygon()
                session.refresh()
        finally:
            session.restore_view()
        n_total = sum(len(p) for p in session.patches)
        if tool is None:
            break
        run_scan_tool(tool)                      # the scans may have moved: the scan is read again
        preset = session.scan_id; carry = session.patches
        print("[{}] done - back to the patches (mesh read again where it is now)".format(tool))
    print("\n{} finished: {} patch(es) created.".format(VERSION, n_total))


if __name__ == "__main__" and IN_RHINO:
    hook_escape()
    try:
        try: scan_display_on()
        except Exception: pass
        main()
    except UserAbort:
        print("[STOPPED] Esc - script ended")
        try: Rhino.UI.StatusBar.HideProgressMeter()
        except Exception: pass
        sc.doc.Views.Redraw()
    finally:
        try: scan_display_off()                     # views back to the modes they had
        except Exception: pass
