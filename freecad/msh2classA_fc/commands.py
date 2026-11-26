# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Workbench commands (GUI). EXPERIMENTAL - NOT TESTED YET."""
import os
import traceback

import FreeCAD as App
import FreeCADGui as Gui

from . import ICON
from . import patches as m2p
from . import scanref
from .core import builder as m2b
from .core import legal

PARAMS = "User parameter:BaseApp/Preferences/Mod/msh2classA"


def params():
    return App.ParamGet(PARAMS)


def _qt():
    from PySide import QtCore, QtGui
    return QtCore, QtGui


def _doc():
    return App.ActiveDocument or App.newDocument("msh2classA")


def _msg(text):
    App.Console.PrintMessage(text + "\n")


class _Cmd(object):
    NAME = ""
    TEXT = ""
    TIP = ""

    def GetResources(self):
        return {"Pixmap": ICON, "MenuText": self.TEXT, "ToolTip": self.TIP + "  (EXPERIMENTAL)"}

    def IsActive(self):
        return True


class LoadScans(_Cmd):
    NAME = "M2CA_LoadScans"
    TEXT = "Load scans"
    TIP = "Link scan meshes (STL / OBJ / PLY). The files are only read and never saved into the .FCStd"

    def Activated(self):
        QtCore, QtGui = _qt()
        res = QtGui.QFileDialog.getOpenFileNames(None, "msh2classA - load scans", "", "Meshes (*.stl *.obj *.ply)")
        files = res[0] if isinstance(res, tuple) else res
        if not files:
            return
        doc = _doc()
        mm = params().GetFloat("ScanMM", 1.0)
        for f in files:
            try:
                scanref.add_scan(doc, f, mm)
            except Exception as e:
                App.Console.PrintError("[msh2classA] {}: {}\n".format(f, e))
        doc.recompute()
        Gui.SendMsgToActiveView("ViewFit")
        _msg("[msh2classA] {} scan(s) linked (files untouched)".format(len(files)))


class ApplySetup(_Cmd):
    NAME = "M2CA_ApplySetup"
    TEXT = "Apply setup"
    TIP = "Put the scans where a saved setup has them (scan_setups.json next to the scans, the same file Rhino uses)"

    def Activated(self):
        QtCore, QtGui = _qt()
        doc = _doc()
        names = scanref.setup_names(doc)
        if not names:
            QtGui.QMessageBox.information(None, "msh2classA", "No setups next to the loaded scans.")
            return
        name, ok = QtGui.QInputDialog.getItem(None, "msh2classA", "Setup:", names, 0, False)
        if ok and name:
            scanref.apply_setup(doc, name)
            doc.recompute()


class SaveSetup(_Cmd):
    NAME = "M2CA_SaveSetup"
    TEXT = "Save setup"
    TIP = "Save the present scan positions under a name (scan_setups.json next to the scans)"

    def Activated(self):
        QtCore, QtGui = _qt()
        name, ok = QtGui.QInputDialog.getText(None, "msh2classA", "Setup name:", text="front")
        if ok and name.strip():
            scanref.save_setup(_doc(), name.strip())


class CheckScans(_Cmd):
    NAME = "M2CA_CheckScans"
    TEXT = "Check scan files"
    TIP = "Are the scan files still the ones you loaded? (SHA-256)"

    def Activated(self):
        from .core import setups as m2setups
        bad = []
        for o in scanref.scan_objects(_doc()):
            try:
                if m2setups.file_sha256(o.File) != o.Sha256:
                    bad.append(os.path.basename(o.File))
            except Exception:
                bad.append(os.path.basename(o.File) + " (missing)")
        QtCore, QtGui = _qt()
        QtGui.QMessageBox.information(None, "msh2classA", "CHANGED since loaded: " + ", ".join(bad) if bad
                                      else "All scan files unchanged.")


class Settings(_Cmd):
    NAME = "M2CA_Settings"
    TEXT = "Settings"
    TIP = "Continuity of new sides, mesh tolerance, snap distance, scan units"

    def Activated(self):
        QtCore, QtGui = _qt()
        p = params()
        items = ["G0", "G1", "G2"]
        c, ok = QtGui.QInputDialog.getItem(None, "msh2classA", "Continuity of new sides:", items,
                                           p.GetInt("Cont", 2), False)
        if not ok:
            return
        p.SetInt("Cont", items.index(c))
        t, ok = QtGui.QInputDialog.getDouble(None, "msh2classA", "Mesh tolerance (mm):", p.GetFloat("TolMM", 0.5),
                                             0.001, 100.0, 3)
        if ok:
            p.SetFloat("TolMM", t)
        s, ok = QtGui.QInputDialog.getDouble(None, "msh2classA", "Snap to patch corners / edges (mm):",
                                             p.GetFloat("SnapMM", 2.0), 0.0, 100.0, 3)
        if ok:
            p.SetFloat("SnapMM", s)
        u, ok = QtGui.QInputDialog.getDouble(None, "msh2classA", "Scan file units (mm per unit):",
                                             p.GetFloat("ScanMM", 1.0), 1e-6, 1e6, 6)
        if ok:
            p.SetFloat("ScanMM", u)


class _PickSession(object):
    """Click 4 corners on the scan -> one patch; again for the next one. Esc / right click = finish."""

    def __init__(self):
        from pivy import coin
        self.doc = _doc()
        self.view = Gui.ActiveDocument.ActiveView
        scans = scanref.scan_objects(self.doc, visible_only=True)
        _msg("[msh2classA] preparing the scan for picking (large scans take a while) ...")
        self.mesh = scanref.world_mesh(scans)
        self.pts = []
        self.root = coin.SoSeparator()
        col = coin.SoBaseColor(); col.rgb = (1.0, 0.2, 0.2)
        self.coords = coin.SoCoordinate3()
        style = coin.SoDrawStyle(); style.pointSize = 9; style.lineWidth = 2
        self.markers = coin.SoPointSet()
        self.lines = coin.SoLineSet()
        for n in (col, style, self.coords, self.markers, self.lines):
            self.root.addChild(n)
        self.view.getSceneGraph().addChild(self.root)
        self.cb_m = self.view.addEventCallback("SoMouseButtonEvent", self.on_mouse)
        self.cb_k = self.view.addEventCallback("SoKeyboardEvent", self.on_key)
        self.prompt()

    def prompt(self):
        _msg("[msh2classA] click corner {} of 4 on the scan   (Backspace = undo point, Esc / right click = "
             "finish)".format(len(self.pts) + 1))

    def redraw(self):
        pts = [list(p) for p in self.pts]
        self.coords.point.setValues(0, len(pts), pts) if pts else self.coords.point.setNum(0)
        self.markers.numPoints = len(pts)
        self.lines.numVertices.setValues(0, 1, [len(pts)]) if len(pts) >= 2 else self.lines.numVertices.setNum(0)

    def on_mouse(self, info):
        if info.get("State") != "DOWN":
            return
        if info.get("Button") == "BUTTON2":
            self.finish(); return
        if info.get("Button") != "BUTTON1":
            return
        hit = self.view.getObjectInfo(info["Position"])
        if not hit:
            _msg("[msh2classA] nothing under the mouse - click ON the scan")
            return
        self.pts.append((hit["x"], hit["y"], hit["z"]))
        self.redraw()
        if len(self.pts) == 4:
            self.build()
            self.pts = []
            self.redraw()
        self.prompt()

    def on_key(self, info):
        if info.get("State") != "DOWN":
            return
        k = info.get("Key")
        if k == "ESCAPE":
            self.finish()
        elif k == "BACKSPACE" and self.pts:
            self.pts.pop(); self.redraw(); self.prompt()

    def build(self):
        p = params()
        pats = [q for q in (m2p.as_core_patch(o) for o in m2p.patch_objects(self.doc)) if q is not None]
        b = m2b.PatchBuilder(self.mesh, pats, cont=p.GetInt("Cont", 2), tol=p.GetFloat("TolMM", 0.5),
                             snap=p.GetFloat("SnapMM", 2.0), model_mm=1.0, log=_msg)
        try:
            res = b.build(self.pts)
        except Exception:
            App.Console.PrintError("[msh2classA] patch failed:\n" + traceback.format_exc())
            return
        self.doc.openTransaction("msh2classA patch")
        obj = m2p.make_patch(self.doc, res)
        self.doc.commitTransaction()
        self.doc.recompute()
        _msg("[msh2classA] {}: {} x {} CV".format(obj.Label, res["pu"] + 1, res["pv"] + 1))

    def finish(self):
        QtCore, QtGui = _qt()

        def later():
            try:
                self.view.removeEventCallback("SoMouseButtonEvent", self.cb_m)
                self.view.removeEventCallback("SoKeyboardEvent", self.cb_k)
                self.view.getSceneGraph().removeChild(self.root)
            except Exception:
                pass
        QtCore.QTimer.singleShot(0, later)           # never remove a callback from inside it
        _msg("[msh2classA] patch tool finished")


class NewPatch(_Cmd):
    NAME = "M2CA_NewPatch"
    TEXT = "New patch"
    TIP = "Click 4 corners on the scan: a single-span patch, G0/G1/G2 to the patches it touches"

    def Activated(self):
        doc = _doc()
        if not scanref.scan_objects(doc, visible_only=True):
            QtCore, QtGui = _qt()
            QtGui.QMessageBox.information(None, "msh2classA", "Load a scan first.")
            return
        _PickSession()

    def IsActive(self):
        return Gui.ActiveDocument is not None


class Export(_Cmd):
    NAME = "M2CA_Export"
    TEXT = "Export patches"
    TIP = "Export the patches (selected, or all) as STEP or IGES"

    def Activated(self):
        QtCore, QtGui = _qt()
        doc = _doc()
        sel = [o for o in Gui.Selection.getSelection() if m2p.is_patch(o)] or m2p.patch_objects(doc)
        if not sel:
            QtGui.QMessageBox.information(None, "msh2classA", "No patches."); return
        res = QtGui.QFileDialog.getSaveFileName(None, "msh2classA - export", "patches.step",
                                                "STEP (*.step *.stp);;IGES (*.igs *.iges)")
        path = res[0] if isinstance(res, tuple) else res
        if path:
            m2p.export(sel, path)
            _msg("[msh2classA] {} patch(es) -> {}".format(len(sel), path))


class CadServices(_Cmd):
    NAME = "M2CA_CadServices"
    TEXT = "CAD Services"
    TIP = "Who made msh2classA and how to reach him"

    def Activated(self):
        QtCore, QtGui = _qt()
        box = QtGui.QMessageBox()
        box.setWindowTitle("CAD Services")
        box.setText("<b>" + "<br>".join(legal.CAD_SERVICES_QUESTIONS) + "</b>")
        box.setInformativeText(legal.CAD_SERVICES_TEXT + "\n\nE-mail: " + legal.CAD_SERVICES_EMAIL +
                               "\nPortfolio: 3dPARTdesign.com")
        copy = box.addButton("Copy e-mail", QtGui.QMessageBox.ActionRole)
        web = box.addButton("Open website", QtGui.QMessageBox.ActionRole)
        box.addButton(QtGui.QMessageBox.Close)
        box.exec_() if hasattr(box, "exec_") else box.exec()
        if box.clickedButton() == copy:
            QtGui.QApplication.clipboard().setText(legal.CAD_SERVICES_EMAIL)
        elif box.clickedButton() == web:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(legal.CAD_SERVICES_URL))


COMMANDS = [LoadScans, ApplySetup, SaveSetup, CheckScans, Settings, NewPatch, Export, CadServices]
NAMES = [c.NAME for c in COMMANDS]

for _c in COMMANDS:
    Gui.addCommand(_c.NAME, _c())
