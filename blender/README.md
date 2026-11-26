# msh2classA for Blender

> ⚠️ **EXPERIMENTAL - NOT TESTED YET in daily work.** Check every result before you use it. Feedback welcome: info@3dPARTdesign.com

Single-span Class-A patches on scans and AI meshes, G0 / G1 / G2 to each other - the same math core as the Rhino version.

## Install

Blender 4.2 or newer. Download `msh2classA-blender-<version>.zip` from [Releases](https://github.com/3dpartdesign/msh2classA/releases), then **Edit > Preferences > Get Extensions > ⌄ > Install from Disk** and choose the zip. The panel is in the 3D Viewport sidebar (**N**), tab **msh2classA**.

## What it does (this version)

- **Scans as references.** Load STL / OBJ / PLY: the files are only read, never written, and the mesh is **not saved into the .blend** (only the path, SHA-256 and position). It is read again when you open the file. *Check scan files* tells you if a file changed.
- **Same positions as Rhino.** *Apply setup* / *Save setup* use `scan_setups.json` next to the scans - the same file the Rhino version uses.
- **New patch.** Click 4 corners on the scan. Clicks near a patch corner or edge snap onto it; a side whose two corners lie on a patch's side joins it with G0 / G1 / G2. Esc or right click finishes.
- **Edit the CVs.** Every patch has a `_CV` object (a grid mesh): move its points with any mesh tool, the patch follows when you leave Edit Mode (or press *Update patches*).
- **Export IGES** - exact NURBS surfaces in mm for Rhino, FreeCAD, CATIA, NX, Alias ...
- **Reflection matcap** and the **CAD Services** button.

Units: set **Scene > Units > Unit Scale** (0.001 = 1 Blender unit is 1 mm). The scan files are taken as mm unless you change *Scan units*.

## Not in this version yet

Mirror plane, smoothing buttons (FairN, EvenUV ...), fillets, N-sided patches, mesh alignment tools, side bubbles per side, native Blender NURBS objects, SubD → Class-A.

## What was tested

Headless in Blender 5.3 (no window): install from the zip, linking a scan, saving / applying a setup, two patches with a G2 seam (gap 0, angle 0°, curvature difference 0.0%), editing a CV, saving and reopening the .blend (the scan mesh is not in the file and comes back from the scan file), IGES export read back by OpenCascade, the scan file unchanged byte for byte.
**Not tested:** clicking in the viewport, the preview drawing, the panel layout, Windows / macOS.

Note: Blender's autosave may still keep a copy of the scan mesh in its temporary autosave file.
