# Changes

## 0.1.2-alpha - 2026-11-27

In short (Rhino; details below):
- Scan/mesh tools: Center / Floor / Align / Setup in any order without losing the centre (guards, marks kept in
  the .3dm and in the setups), inch documents and setups between Rhino / Blender / FreeCAD, SHA-256 of the scan
  files, linking that works (no embedding, no PLY questions), point-to-plane Fit, no more "no entry" cursor.
- New: MeshAutoAlign (many scans/meshes at once, in spheres at the markers), Align Scale (AI meshes),
  MeshScale (the true size from a measured curve), photos (LoadPhotos, MeshPhoto), hand moves detected.
- Patches: Sweep (2 or 3 sides drawn, the rest made - holes, half a window at the mirror), a clear start
  (Draw patches / Back to the patches), ClearPts / UndoSrf explained, Esc in a patch clears only that patch.
- "scan/mesh" everywhere. FreeCAD: ScanScale. Tests: tests/ (fake Rhino, every order of the tools).

### Rhino
- **Center / Floor in any order, the centre is never lost.** Floor turns only about the axis across the symmetry
  plane, so it never moves the centre; Center after Floor used to tilt the floor (~0.4 mm on the test car). Now
  Floor remembers its clicks IN their scan/mesh blocks and Center levels the floor again with them by itself:
  Floor -> Center gives exactly the same as Center -> Floor (tested to 1e-12 mm, mm and inch documents, with the
  front/back 0 point). Floor with another SymPlane than the one Center used is refused (it would move the centre).
- **Align guard:** a scan/mesh placed by Center / Floor (or aligned to one) is never moved onto a scan/mesh that is
  not - REFUSED with a message to align the other way. A new scan/mesh aligned onto the centred set joins it. The
  marks are kept in the .3dm and in every saved setup (`_frame`), MeshSetup brings them back.
- **Clicks with the "no entry" cursor landed off the mesh** (MeshAlign 2nd click and any mesh tool at some view
  angles): Rhino looks for the point on the CPlane and, where the cursor ray misses it, shows the no-entry cursor
  and keeps the point where it last was. Every mesh-tool click is now constrained to a plane facing the camera,
  and the ray onto the scans/meshes goes through the point Rhino reports. Looking through one scan/mesh to the
  one asked for (Align) only within ~3 mm (was 1 % of the eye distance, 3-5 cm on a whole car).
- **Rhino 9: loaded scans/meshes did not show in the Layers panel until msh2classA ended** (the panel waits for
  the end of a command, and msh2classA is one command). After LoadMeshes a tiny `-Layer` command runs and ends
  at once so the panel shows them.
- **New: MeshAutoAlign** - many scans/meshes pulled together at once, only inside spheres (AutoFitRadius) at the
  markers: every Align pair now leaves its point as a marker (kept in the block, in the .3dm and in every setup),
  more can be clicked. Two lists (hidden ones too): MOVE and STAY. One least-squares system for all moving ones:
  each is pulled onto the staying ones and onto the other moving ones (point-to-plane). The staying ones never
  move. Test: B 33 mm and C 53 mm knocked off -> 0.000 mm, A (centred, stays) untouched.
- **Guard, finer:** besides "never moved onto a scan/mesh outside the centred set", a scan/mesh Center / Floor
  were CLICKED on (the core) never moves onto one they were not clicked on (Align and AutoAlign). Before, a scan
  aligned to the centred front (so in the set) could be used to pull the front itself away.
- **Center: the "no entry" cursor still came** (zoomed in near the mesh): the click plane went through the view
  target, which can lie outside the view's depth range. Now it sits in the middle of that range (near/far
  clipping), for every mesh-tool click and the Fine gumball.
- Tested: setups 'front' and 'rear' (two local centres), a scan/mesh aligned after 'front' was saved, switched
  back and forth 3x - exact, alignment the same in both.
- **Names: "scan/mesh" everywhere** in prompts, messages, help and panels (Rhino, Blender, FreeCAD) - the same
  thing, people know it under both names. Option *ScanUnits* is now *ScanMeshUnits*. Command / option names
  without a slash (MeshAlign, LoadMeshes, MeshTol, NoScan ...) stay.

### Core
- `meshgrid.TriMesh.closest`: walks only the shell of cells, inside the grid (was the whole cube per step) - far
  points were very slow (Blender / FreeCAD picking, tests). Same results (checked against brute force).

### Rhino - Sweep patches (holes in the scan/mesh)
- Option **Sweep** while drawing a patch: draw 2 or 3 sides, the rest is made like Sweep1 (no scan/mesh under it).
  3 sides: side 4 = side 2 carried onto corners 4 -> 1. 2 sides: side 1 = profile, side 2 = rail; side 3 = the
  profile carried along the rail (turning with it), side 4 = the path of corner 1, corner 4 made. A made side on
  the symmetry plane is a mirror side (G0/G1/G2 to its mirror image). Each made side takes the rows (CV count)
  of its opposite side. Fit: the scan/mesh where it is (hits through the hole dropped, also for the start),
  the Sweep shape where not; the CV count from the rows (no raise, no AutoSplit).
- tests/test_sweep.py: the made sides (straight and curved rail, at the mirror, 3 sides). The whole patch
  build is NOT tested outside Rhino.

### Rhino - clearer start of the patches
- The tools panel has a button on top: "Draw patches" (option DrawPatches): the only visible scan/mesh is taken,
  else you click one. Clicking the scan/mesh still works.
- After a scan/mesh tool started from the patches, msh2classA no longer jumps back into the patches by itself: the
  button "Back to the patches" (option BackToPatches) goes back; another tool can run first.
- Banners in the command history: "**** PATCHES: drawing patches on '...' ****" and "---- SCAN/MESH TOOL: ... ----".
- ClearSrf is now ClearPts (it clears the points of the unfinished patch - no surface). UndoSrf says what it
  removed: the LAST patch (several surfaces if that build used SrfSplit=AutoSplit), one per click.
- Align Fit says what it does when it runs. The Legend is off by default.
- tests/test_entry.py: the start (DrawPatches, click, BackToPatches after a tool, Esc).

### Rhino - panel order
- The scan/mesh tool buttons (and the help) in the order of the work: LoadMeshes, Center, Floor, Scale, then Align,
  AutoAlign, Setup, Mirror, then the photo tools.

### Rhino - "no entry" cursor in Center (again)
- After zooming with the wheel over the scan/mesh (Rhino 8 zooms TO the object under the cursor, the blue mirror
  points too) the camera moved during the prompt; the click plane, made once at the start of the prompt, ended up
  behind it - "no entry" over the mesh, more often on the mirrored side. The plane is now made again whenever the
  view changes, however it changes - wheel, Ctrl / Shift + right drag, rotating, Page Up / Down, arrow keys,
  touchpad, 3D mouse, a Zoom typed in: on every redraw (the tools' display conduit, only when the camera really
  changed), on RhinoView.Modified and on every mouse move (all mesh-tool clicks and the Fine gumball).

### Rhino - size, hand moves, photos
- **Align / AutoAlign: option Scale** (off by default): the moving scan/mesh also changes its size - pairs (3+:
  rotation + move + one factor, Umeyama), Fit and AutoAlign (point-to-plane with a size unknown). For AI meshes of
  the same car (Trellis front + rear) and photogrammetry without a true size. Tested: 8 % / 7 % / 4 % off -> exact.
- **MeshScale** (also the Center option ScaleByCurve): pick a curve drawn on the scan/mesh along a measured length,
  type the true length - all scans/meshes and the curve are scaled; about the world origin after MeshCenter, so the
  centre, the floor and front/back 0 stay. Setups keep the size (4x4). Rhino's sphere / closest-point search now
  works on scaled blocks (sizes per block, distances in the world).
- **Moved by hand** (gumball, Move ... outside the script): allowed. The next scan/mesh tool shows what moved and
  asks Keep (setups follow, like an Align) / KeepOnly / Back / Esc. Center / Floor marks: dropped when a scan/mesh
  they were clicked on moved, or when the whole set moved off the plane / floor; kept otherwise.
- **Photos (new): LoadPhotos + MeshPhoto** - picture frames behind the car in their view (front / rear / side /
  top). Scale by two points + a known distance; Center (LEFT/RIGHT pairs, front / rear / top); Floor (tyre
  contact); PhotoToMesh (photo onto the scans/meshes, in its plane, with its size); MeshToPhoto (a scan/mesh -
  e.g. a spoiler scanned apart - moved in the photo's plane to where the photo shows it). Photos may be moved by
  hand; setups keep them (corners). NOT tested in Rhino: making the picture frame (AddPictureFrame) - the rest on
  the test bench.
- **FreeCAD: ScanScale** - a Placement cannot scale; a setup with a size (AI mesh) now sets ScanScale and saves it
  back into the 4x4 (tested: same world point as Rhino, saved matrix = Rhino's).

### Rhino - every order of Align / AutoAlign / Center / Floor / Setup (tests/test_orders.py)
A model written apart from the script says after every step which scans/meshes must sit exactly on each other,
which are symmetric, which level, and what every saved setup holds; a checklist of 13 orders (mm + inch) and 40
random sequences of 12 steps check the script against it. Weak points it found, fixed:
- **Floor before any Center did not level the floor** (it turned only about the axis across the symmetry plane,
  to keep a centre there was none yet) - the floor stayed leaning sideways and the marks said "level". Now,
  without a centre, Floor turns the floor flat whatever way it leans; Center later levels it again (pitch only).
- **Floor on scans/meshes that are not centred while others are**: refused (levelling them would move the centre).
- **Center on scans/meshes the Floor points are not aligned to** (e.g. a rear Center on a rear not aligned to the
  front) levelled "again" with points that are not centred - the floor ended crooked, the marks said level. Now
  the floor mark is dropped with a WARN: MeshFloor again.
- **The marks were one list for Center and Floor**, never cleaned: after a second Center on another structure
  the first one still counted as centred. Now per tool, exactly the scans/meshes that are centred / levelled now
  (in the .3dm and in every setup); older marks are read as before.
- **Setups after an Align used ANY unmoved scan/mesh as reference** - if that one had moved against the one you
  aligned onto since the setup was saved, the new scan/mesh landed off in that setup. Now relative to the one it
  was aligned onto (AutoAlign: the staying ones).
- **Scans/meshes lying exactly on each other** (aligned before): a click took one of them by chance - e.g. D
  (aligned to A) instead of A, and D moved alone, leaving A. Now the one that carries the others is taken, and it
  says so ("hide it to click one alone").
- Center / Floor clicks on scans/meshes not aligned to each other: a WARN (the plane would fit none exactly).

### Rhino (earlier in this round)
- **Units (inch documents, inch scans):** scan files are taken as *ScanMeshUnits* (new option of the first prompt,
  default mm) and converted to the document's units. The scale Rhino's own importer used (it depends on Rhino's
  settings) is checked against the numbers in the file and replaced - tested for STL binary / ASCII, OBJ, PLY in mm,
  inch and metre documents. Every scan (also STL) is now linked through its cache .3dm, so reopening a project
  always gives the same scale.
- **Setups carry their units** (`_units`, `_mm_per_unit`) and the SHA-256 of every scan; applying a setup converts
  units and warns if a scan file changed. Inch documents and mm documents (and Blender / FreeCAD) read each
  other's setups right; older setups without units are taken as mm. After Align the setups are updated in their own
  units. (Blender / FreeCAD: applying a setup no longer changes the scan file units.)
- **MeshAlign Fit: point-to-plane ICP** (as AutoFit). The old point-to-point Fit slid on smooth surfaces and
  stopped short: from pairs 6 mm off it went 14 mm -> 12 mm; now 14 mm -> 0.000 mm.
- **Esc while drawing a patch** clears only that unfinished patch; Esc with nothing started ends msh2classA.
- **Esc / end texts say WHAT ends** - "Esc = end msh2classA", "Esc = cancel Align, meshes back", "Esc = undo this
  Fine round", "Esc = stop loading after this mesh" ... - and the busy notes show "(Esc = ...)" only where the code
  really reacts to Esc; elsewhere "(please wait)". MatchUVN and FreeEdgeEdt show their own prompt (before, the
  patch prompt was shown there, promising Esc = end the script while Esc only cancels that mode).
- **MeshFloor: Esc did the Floor** like Enter (with 3+ points the meshes were turned). Now Esc cancels, nothing
  changes; Esc at the front/back point too. New option UndoPt.
- **MeshAlign review:** Fit now stops on Esc (back to before Fit, Align goes on) - its note promised it; Fine turns
  about the middle of the pairs, not the middle of the whole scan (a ring swung the aligned place away); after
  Align the saved setups keep the other fields of the moved mesh's record (the layer was emptied, SHA-256 / units
  of other programs were lost); finishing with an unpaired point says that point is dropped.
- **Scans were never really linked - every scan block was EMBEDDED** (the cause of the huge files and slow saves).
  The script called `ModifySourceFilePath`, a RhinoCommon name that does not exist; the error was caught and
  reported as "Rhino refused the link". Now `InstanceDefinitionTable.ModifySourceArchive` (checked: the block
  really is Linked afterwards).
- **LoadMeshes, PLY / OBJ were EMBEDDED** in the project (Rhino refused to link them): big files, saving took very
  long and could freeze Rhino on a slow or cloud drive. Now the mesh of a PLY / OBJ is written once to
  `msh2classA_cache/<name>.3dm` next to the scans and the block is linked to that file - the project stays small,
  opening it asks no PLY / OBJ import questions. The original scan is only read; the cache is checked against it
  (SHA-256) and made again when the scan file differs. Setups still point to the original file.
- **Grey reference layers of the PLY / OBJ cache blocks:** Rhino 9 - the block is linked with layer style *Active*
  (the mesh sits on the scan's own layer, no grey layers). Rhino 8 cannot set the layer style from a script (only
  the Rhino 9 API can; a scripted `-Insert` was tried and dropped - its questions depend on Rhino's settings), so it
  keeps Rhino's default *Reference*: the grey layer is named "scan mesh (linked - not saved in the project, do not
  edit)" so it is clear what it is.
- **LoadMeshes, PLY:** no more `Set PLY import options` question for every PLY file. PLY is read through
  RhinoCommon's `FilePly.Read` (no question). The result is checked against the file itself (vertex count and the
  first vertex, read straight from the PLY): a unit scaling by Rhino is taken back, anything else -> the import is
  undone and the `-Import` command is used with `ModelUnits` = the document's units, answered by the script. Either
  way the file's coordinates are taken as they are - the same as STL, and as the Blender and FreeCAD versions read them.
- **MeshCenter / MeshAlign / MeshFloor:** Esc while "preparing the meshes ... (Esc = stop)" now really stops the
  tool. Before, the Esc was only remembered and then cancelled the tool's first prompt, so the tool seemed to do
  nothing and the next Esc ended the whole script.
- **MeshCenter / MeshAlign / MeshFloor: tags, mirror points and AutoFit spheres did not show, or only sometimes**
  (tags only after a few pairs, SpherePreview did nothing). They were drawn by the GetPoint's DynamicDraw, which
  Rhino calls irregularly or not at all when the tool asks only for a window point (the tools do, to avoid the
  'no entry' cursor). Now they are drawn by the display pipeline (like MeshMirror) on every redraw, switched off
  when the tool ends - also when it stops with Esc or an error. Clicks are taken on mouse up.
- **MeshCenter: mirror preview off until the first pair** (the mirror of a scan not centred yet shows nothing
  useful); it comes on by itself after the first pair, unless MirrorPreview was clicked before.
- **MeshAlign: which mesh stays, which moves.** The mesh that STAYS turns RED after the first click, the mesh that
  MOVES turns GREEN (with the meshes aligned to it before, which move with it); colours back at the end / cancel /
  error. Clearer prompts ("point on the RED mesh (stays)") and a short explanation at the start.
- **MeshAlign: a click lands on the mesh you click.** The front-most mesh under the cursor counts. Only where the
  two meshes lie on each other (within ~10 mm, after a pair) may the point go onto the asked-for mesh behind; a click
  clearly on the other mesh is refused with a message instead of being put onto a mesh behind it.
- **Blue mirror points (MeshMirror)** no longer stay on screen after the script ends (also cleared at start, if an
  earlier run was stopped).

## 0.1.1-alpha - 2026-11-26

- Rhino version (alpha).
- Blender extension and FreeCAD workbench - EXPERIMENTAL, not tested yet.
- One repository: shared `core/`, `rhino/`, `blender/`, `freecad/`, built by `tools/build.py`.
