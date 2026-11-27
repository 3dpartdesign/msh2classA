# Tests

Run the REAL Rhino script (the built `dist/rhino/msh2classA.py`) outside Rhino, on a small stand-in for
RhinoCommon (`fakerhino.py`: real geometry, real mesh queries, scripted clicks as rays into the view).

```
python tools/build.py
python tests/test_scan_tools.py     # MeshAlign / MeshCenter / MeshFloor / MeshSetup in many orders, mm + inch
python tests/test_import_units.py   # LoadMeshes scale: STL / OBJ / PLY, mm / inch / m documents
```

Needs Python 3 with numpy. `test_scan_tools.py` takes about 5 minutes.

What `test_scan_tools.py` checks, on three overlapping pieces of a symmetric car-like scan, each put at a random
place and angle:
- Align B to A, C to B; Center; Floor; MeshSetup save / apply - exact clicks must give exact results
  (relative placement, symmetry about x = 0, level floor at 0) and every tool must keep what the others did
- the same in an inch document
- other orders: Center first, re-Align a mesh that carries another one
- Floor -> Center must give EXACTLY Center -> Floor (mm and inch); Floor with another SymPlane than Center is refused
- Align never moves a centred scan/mesh onto one that is not; a new one aligned onto the centred set joins it;
  the marks survive MeshSetup
- AutoAlign: B and C knocked 33 / 53 mm off come back to 0.000 mm onto the centred A (which stays); the guard;
  clicked markers; Esc
- setups 'front' / 'rear' (two centres) switched back and forth stay exact
- an Align that would make a loop is refused, nothing moves
- Esc / Cancel / UndoPair / UndoPt bring everything back exactly
- Fit (Align) and AutoFit (Center) from clicks 6-8 mm off must end within 0.5 mm
- a scan loaded later and aligned: the saved setups learn where it goes
- setups across programs: inch Rhino <-> mm Blender / FreeCAD, and old setups without units

## test_orders.py - every order

    python tests/test_orders.py              # checklist (13 orders, mm + inch) + 30 random sequences
    python tests/test_orders.py --seeds 40 --random-only

A model, written apart from the script, says after every step which scans/meshes must sit exactly on each
other, which are symmetric (Center), which are level (Floor), what the Center / Floor marks must say, and what
every saved setup must hold in scan_setups.json. Refused steps and Esc must change nothing (positions, marks,
setup file). Takes about 20 minutes.

## test_entry.py, test_sweep.py

    python tests/test_entry.py      # start: DrawPatches, click, BackToPatches after a tool, Esc
    python tests/test_sweep.py      # Sweep: the sides made from 2 or 3 drawn ones

