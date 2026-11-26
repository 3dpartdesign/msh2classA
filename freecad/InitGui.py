# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# msh2classA workbench for FreeCAD - EXPERIMENTAL, NOT TESTED YET.
import FreeCADGui as Gui


class Msh2classAWorkbench(Gui.Workbench):
    MenuText = "msh2classA (experimental)"
    ToolTip = "Scans and AI meshes to Class-A NURBS patches - EXPERIMENTAL, NOT TESTED YET"

    def __init__(self):
        import msh2classA_fc
        self.__class__.Icon = msh2classA_fc.ICON

    def Initialize(self):
        from msh2classA_fc import commands
        self.appendToolbar("msh2classA", commands.NAMES)
        self.appendMenu("msh2classA", commands.NAMES)

    def Activated(self):
        import FreeCAD as App
        from msh2classA_fc.core import legal
        for line in legal.banner("FreeCAD"):
            App.Console.PrintMessage(line + "\n")

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(Msh2classAWorkbench())
