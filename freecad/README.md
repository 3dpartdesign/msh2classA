# msh2classA for FreeCAD

> ⚠️ **EXPERIMENTAL - NOT TESTED YET in daily work.** Check every result before you use it. Feedback welcome: info@3dPARTdesign.com

Single-span Class-A patches on scans and AI meshes, G0 / G1 / G2 to each other - the same math core as the Rhino version, real B-spline faces in FreeCAD.

## Install

Download `msh2classA-freecad-<version>.zip` from [Releases](https://github.com/3dpartdesign/msh2classA/releases) and unzip it into FreeCAD's **Mod** folder (FreeCAD: *Help > About > Copy to clipboard* shows the user data folder; `Mod` is inside it), so you get `Mod/msh2classA/InitGui.py`. Restart FreeCAD and choose the workbench **msh2classA (experimental)**.

Do not install this folder straight from the repository: the shared core is added by `tools/build.py`.

## What it does (this version)

- **Scans as references.** *Load scans* (STL / OBJ / PLY): the files are only read, never written; the mesh is **not stored in the .FCStd** (only the path, SHA-256 and Placement) and is read again when the document opens. *Check scan files* tells you if a file changed.
- **Same positions as Rhino and Blender.** *Apply setup* / *Save setup* use `scan_setups.json` next to the scans.
- **New patch.** Click 4 corners on the scan; corners near a patch corner / edge snap onto it (*Settings*: snap distance), a side on a patch's side joins it with G0 / G1 / G2. Esc or right click finishes.
- **Export** the patches as STEP or IGES (FreeCAD's own exporter).
- **Settings** (continuity, mesh tolerance, snap, scan units) and the **CAD Services** button.

## Not in this version yet

Editing CVs with continuity kept, mirror plane, smoothing buttons, fillets, N-sided patches, mesh alignment tools, SubD → Class-A. Large scans (millions of triangles) are slow to prepare: the mesh search is pure Python here.

## What was tested

In FreeCAD 26.3 (conda-forge, Linux): headless - linking a scan, applying the setup saved by the Blender version, two patches with a G2 seam (gap 1e-10 mm), STEP and IGES export read back, saving and reopening the .FCStd (5 KB, no scan inside; the scan comes back from its file), the scan file unchanged. With the GUI running off-screen: the workbench and its 8 commands load, the scan is drawn, 4 picks on the scan build a patch.
**Not tested:** the dialogs, real mouse clicks, Windows / macOS.
