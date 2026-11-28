# msh2classA

**3D scans and AI-generated meshes → Class-A NURBS surfaces.**

msh2classA builds clean, single-span NURBS patches on a mesh, patch after patch, with G0 / G1 / G2 continuity between them. Few control points, even CV layout, easy to edit later. Made for automotive reverse engineering, industrial design and turning AI meshes into real CAD.

By [3dPARTdesign](https://3dPARTdesign.com) (Marcin Bartoszewski).

| Version | Status | Download |
|---|---|---|
| **Rhino** 8 / 9 | alpha, in use | `msh2classA-rhino-<version>.zip` |
| **Blender** 4.2+ | ⚠️ **experimental, not tested yet** - see [blender/README.md](blender/README.md) | `msh2classA-blender-<version>.zip` |
| **FreeCAD** | ⚠️ **experimental, not tested yet** - see [freecad/README.md](freecad/README.md) | `msh2classA-freecad-<version>.zip` |

All downloads: [Releases](https://github.com/3dpartdesign/msh2classA/releases).

## What it does

- Click four corners on the mesh and get a single-span patch fitted to it, G0 / G1 / G2 to its neighbours.
- Smooth, even out, straighten or relax a patch while its neighbours keep their continuity (Rhino).
- Symmetry: mirror plane, mirrored patches, patches cut at the plane (Rhino).
- Fillets with no trims, N-sided patches, global seam matching (Rhino).
- Patches over holes in the scan/mesh: draw 2 or 3 sides, the rest is made like Sweep1 (Rhino).
- Scan/mesh tools (Rhino): load, centre on the symmetry plane, put on the floor, true size from a measured
  curve (AI / photogrammetry meshes), align (also with scaling) and auto-align many at once, save named setups,
  photos as guides (front / side / rear: centre, floor, scale, put loose parts in place). Done in any order
  without losing the centre.
- **The customer's scan files are never touched** - in every version. Meshes are linked references, not imported into your file. Their positions are kept in `scan_setups.json` next to the scans, the **same file for Rhino, Blender and FreeCAD**: work in Rhino, go on in Blender or FreeCAD, the scans stay where they were.

## Rhino

Unzip `msh2classA-rhino-<version>.zip` and keep the files together (the script looks for the `.ini` display mode and the matcap image next to itself). In Rhino: `_-RunPythonScript` and choose `msh2classA.py`. The `Help` option explains everything.

**Which surfaces take part:** every *visible* surface near the scan/mesh, on any layer except the scan/mesh and photo layers (new patches go to the current layer; if that is a scan/mesh or photo layer, to `Patches- Msh2classA`). Single-span surfaces with up to 8x8 CV take part fully, also ones not made by msh2classA: a neighbour's buttons may move their CVs. Trimmed, multi-span or rational ones (e.g. 20x20 CV) are never changed: only their edges are matched, so a bad edge gives a bad seam. The `Rebuild` button on such a surface (or `RebuildAll`) turns an untrimmed one into a clean 6x6 CV patch that takes part. To keep surfaces out, hide their layer before you start; lock it to use only their edges. The start of every run lists what takes part.

## Repository layout

```
core/      shared, no host program needed (pure Python):
             the math of the Rhino version - B-splines, least squares,
             G0/G1/G2 fitting, smoothing, fillets, N-sided patches
             + builder.py (a patch from 4 corners), setups.py (scan_setups.json),
               meshio.py (STL/OBJ/PLY reader), meshgrid.py (mesh queries),
               iges.py (IGES export), legal.py
rhino/     the Rhino version: fragments of one script, NOT runnable on their own
blender/   the Blender extension (experimental)
freecad/   the FreeCAD workbench (experimental)
assets/    matcap image
tools/     build.py - builds all three versions
tests/     test bench for the Rhino scan/mesh tools (a fake Rhino), see tests/README.md
```

Build everything (Python 3):

```
python tools/build.py
```

`dist/` then holds the Rhino script with its files and the three zips for a release. The order of the Rhino parts is in `build_order.txt`.

## CAD services

Too much work and not enough time? Shapes that won't come out the way you planned them? Trouble with a scan, or a file to prepare for series production?
On-demand 3D modeling for production: custom body kits, NURBS / SubD surfacing, 3D scan cleanup and production preparation.
**info@3dPARTdesign.com** · [3dPARTdesign.com](https://3dPARTdesign.com)

## License

GPL-3.0 with additional terms (GPL-3.0 section 7): see [LICENSE](LICENSE) and [NOTICE](NOTICE). The names "msh2classA" and "3dPARTdesign" are not licensed for modified versions. Every copy must keep the NOTICE file and the line the program prints at start.
