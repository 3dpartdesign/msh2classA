# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# -----------------------------------------------------------------------------
# SESSION
# -----------------------------------------------------------------------------

HELP = """
------------------------------------------------------------------
msh2classA - how it works
  DRAWING  Side 1: click start, points on the scan/mesh, right click = end
           side. Sides 2, 3: points, right click. Side 4 returns to
           corner 1: optional points, right click = BUILD.
           A click on an edge / curve / its end is always a corner and
           ends the side at once. Two corners on the same edge or curve
           -> that side follows it (G0/G1/G2 to a surface edge).
           Near an edge END the corner snaps to that end (the
           neighbour's corner); a corner made on the scan/mesh within 2 x
           MeshTol of an edge is moved onto it; points clicked along
           an edge do not turn that side into a scan/mesh side. Narrow
           blend: click its 4 corners = the 4 corners of the two
           patches, both long sides then follow the edges.
  SURFACES THAT TAKE PART  every VISIBLE surface near the scan/mesh, on
           any layer but the scan/mesh and photo layers (new patches go to
           the CURRENT layer - if that is a scan/mesh or photo layer, to
           'Patches- Msh2classA'; the start of each run says how many and
           which layers):
             msh2classA patches: everything (their G levels are stored).
             other single-span surfaces (up to 8x8 CV, untrimmed, not
               rational): new patches match them, a neighbour's buttons
               may move their CVs; no stored G levels: GlobalMatch leaves
               their seams.
             trimmed / several spans / rational / over 8 CV (e.g. 20x20):
               never changed, only their EDGES are used - a bad edge gives
               a bad seam. Untrimmed ones: button Rebuild on it (or
               RebuildAll) = one clean 6x6 CV patch on it, then it takes
               part.
           Keep surfaces out: HIDE their layer before msh2classA. LOCKED:
           only their edges are used, never changed.
  SWEEP    (option Sweep=On) where the scan/mesh has a hole (a window,
           a missing part): draw only 2 or 3 sides, the rest is made like
           Rhino's Sweep1 - no scan/mesh under them:
             3 sides drawn (corner 4 exists): right click = build; side 4
               = side 2 carried onto corners 4 -> 1 (the same arc)
             2 sides drawn: at side 3 right click with no point = build;
               side 1 = profile, side 2 = rail: side 3 = side 1 carried
               along side 2 (turning with it), side 4 = the path of corner
               1; corner 4 is made. A made side on the symmetry plane is a
               MIRROR side (G0/G1/G2 to its mirror image): half a window
               to the mirror line, ForceMirror not needed.
           Each made side takes the rows (G0 / G1 / G2, so the CV count) of
           its opposite side. Inside, in every FitSrfTo: WholeMesh = the
           scan/mesh where there is one (hits through the hole dropped),
           the Sweep shape where not; TempPts = through your points, the
           Sweep shape elsewhere; NoScan = the Sweep shape (like Sweep1).
           FreeEdgeEdt can redraw a made side afterwards.
  SIDE BUBBLES  Click: G0 -> G1 -> G2.  On a surface edge = continuity
           to it. On the scan/mesh = rows RESERVED for a future neighbour.
           Rows across the patch: G0/curve 1, G1 2, G2 3; CV count =
           sum of the two opposite sides (centre label shows the size).
           Bubble on an EDGE side: G0 -> G1 -> G2 -> G2>G0 -> G1>G0.
           G2>G0 / G1>G0: the rows are laid out like G2 / G1 (3 / 2 rows),
           but only the edge row is matched to the neighbour - the seam
           is G0 (the buttons treat it as G0); MatchUVN can raise it later.
  PATCH BUTTONS  Before the first corner, hover a finished patch: small
           buttons in its centre, one step per click (click again for more).
           Direction the CVs move:  N = up / down (along the normal, off
           the patch), UV = sideways (in the patch), UVN = both.
             FairN    N    smoother shape: waves, noise; the CV layout
                           stays (free edges: also UV across the edge)
             SmthUVN  UVN  FairN + EvenUV + StrUV at once (strongest)
             EvenUV   UV   rows a step towards even spacing; shape stays.
                           Rows along a seam that is only PART of an edge
                           (T-joint, patches of different length) stay: an
                           exact G1/G2 fixes them. Even long patches BEFORE
                           shorter ones are built against them
             StrUV    UV   rows / columns straight seen from above (only
                           sideways across the row); shape stays
             RelaxUV  UV   less skew: rows and columns cross as the four
                           edges imply (Coons layout); shape stays
             ToDevUV  UV   the nearest row (+ the one behind) moves onto
                           the place where a WHOLE row is furthest from
                           the scan/mesh; shape stays
             PullN    N    fit the patch to the scan/mesh, CVs only along the
                           normal (free edges also across the edge): the
                           CV layout (EvenUV / StrUV / RelaxUV) stays;
                           neighbours give way with their G0/G1/G2 rows.
                           A TempPts patch: onto its points, not the scan/mesh
             RstN     N    the smoothest shape its edges / seams allow,
                           CVs only along the normal, no scan/mesh, no points
                           (then PullN)
             FreeEdgeEdt   redraw a free edge (no neighbour / mirror): first
                           click the edge (the nearest one lights up), then
                           a) start AT a corner dot, points on the scan/mesh, end
                              AT the other corner: only the middle of the
                              edge moves, both corners stay;
                           b) start BESIDE the patch, points on the scan/mesh
                              where the edge should be, right click: the
                              WHOLE edge (corners too) goes onto that line,
                              the rows behind it follow (fading to the
                              middle); neighbours at its corners come along
                              and keep their G0/G1/G2
             Del           delete the patch; neighbours stay as they are;
                           UndoSrfEdit brings it back
             MatchUVN UVN  after hand edits of the patch: bubbles on its seams
                           (and on sides NEAR the mirror plane - e.g. drawn
                           without snapping: Apply puts them onto the plane)
                           (click = G0 / G1 / G2), Apply -> the NEIGHBOURS
                           come back to this patch (and stay matched among
                           themselves); the patch itself and the scan/mesh are
                           not used to change anything
           Button colours: green fine, yellow could be better, red = worth
           a click (FairN: curvature bumps, EvenUV: spacing ratio, StrUV:
           row kinks, RelaxUV: skew, PullN: scan/mesh deviation vs MeshTol,
           MatchUVN: seams open or below their G). The numbers are at the
           bottom of the legend. Usual order: row 1 (the CV net), row 2
           (the shape), row 3 when needed - click where it is red.
           Every seam is KEPT at the level it was built with (G0/G1/G2,
           stored on the patch) or set by Match - never lower; older
           patches: a seam that drifted a little still counts as before.
           Seams opened a little (up to 2 x MeshTol) are closed again.
           Neighbours change only their continuity rows - except rows that
           CROSS a seam in EvenUV / ToDevUV: the neighbour's matching rows move
           along (no 'S' at the seam). Every seam keeps its G level, the
           mirror stays a mirror; UndoSrfEdit undoes any of them.
  ORDER    Build flat / gently curved patches first, fillets G2 to them.
           A strip = ONE patch with SrfSplit=AutoSplit.
options
  DefaultCont    G0Pos / G1Tan / G2Curv: default for every new side.
  FitSrfTo       WholeMesh = fit the whole scan/mesh under the patch
                 TempPts = click points the patch must follow (e.g. to
                           bridge a recess); the rest is as smooth as the
                           edges allow. The points are kept ON the patch:
                           every button later uses them, not the scan/mesh
                           (RstN = smoothest the edges allow, then PullN =
                           onto the points).
                 NoScan  = only the edges lie on the scan/mesh; the inside is
                           the smoothest surface the edges and G0/G1/G2
                           allow (smoothing has no scan/mesh leash there)
  SrfSplit       OneSrf = one single-span patch
                 AutoSplit = as many single-span patches as the scan/mesh needs
                 (cut from one smooth surface, C4 between them)
  MeshTol        allowed scan/mesh deviation in model units: picking CVs,
                 AutoSplit, the smoothing leash. Compared with 90% of the
                 samples. The Deviation colours use it too.
  HintTolPct     SideLenHint only: allowed deviation in % of the side /
                 patch size (default 0.2 %), so the size advice depends on
                 the SHAPE, not on how big it is.
  CornerFlow     where a corner sits on the END of a neighbour's edge:
                 KinkOK = the new side may leave at any angle (kink in
                 the outline is allowed, the seam is still G1/G2)
                 Smooth = the new side continues the neighbour's outline
                 edge with the seam's G1/G2 (no kink in the outline)
  MirrorPlane    Off / XZ (y=0) / YZ (x=0) / XY (z=0). A pink "mirror"
                 dot shows when the cursor is within snap distance. Sides within
  MirrorSnapDist of the plane snap onto it. The mirror is matched like
                 an invisible mirrored copy (MatchSrf, average): G0 = edge
                 on the plane, G1 = 2nd row straight across, G2 = 3rd row
                 at twice the 2nd row's distance (rows 1/2/3, as any G).
  SeamLabels     seam bubbles (G2 green, G1 yellow, G0 kink orange, gap red)
                 and grey CV counts on edges / curves
  SideLenHint    while drawing: green = one patch surely works, yellow =
                 works but near the limit, red = end the side at the dot.
                 Checked (all scale-free): scan/mesh deviation (HintTolPct),
                 how far the scan/mesh turns (4CV max 100 deg, 6CV max 130
                 deg) and that the patch would not wave. From side 3 on
                 the whole patch (inside and both directions) is checked.
                 Only with FitSrfTo=WholeMesh: with TempPts / NoScan the
                 patch does not follow the scan/mesh, so it says nothing.
  SCAN/MESH TOOLS (buttons in the small panel - before the scan/mesh is
  picked and while you work on patches: the work waits and comes back with
  the scan/mesh where it is now). They move / turn only the scan/mesh
  BLOCKS - the customer's files are never touched, the scale never changes.
  Every click goes onto the scan/mesh under the cursor, from any view angle
  (no 'no entry' cursor).
    LoadMeshes   many STL / OBJ / PLY scans/meshes at once as LINKED blocks
                 (embedded if Rhino refuses the link), each on its own layer
                 under 'Reference Meshes- Msh2classA', own colour; hidden
                 after loading except the last one. PLY point clouds are
                 refused (make a scan/mesh of them first). Every scan/mesh is
                 linked through a small .3dm in msh2classA_cache next to the
                 files (written once, checked by SHA-256): the same scale at
                 every opening, no import questions.
                 UNITS: scan/mesh files are taken as ScanMeshUnits (option
                 of the first prompt, default mm - what scanners write) and
                 converted to the document's units, whatever Rhino's
                 importer does. Setups say their units: inch and mm
                 documents (and Blender / FreeCAD) read each other's setups.
    MeshCenter   pairs LEFT / RIGHT on the same features of the visible
                 scans/meshes: after every pair ALL of them (one aligned
                 structure, hidden ones too) turn + move so the symmetry
                 plane of the pairs is the MirrorPlane. Blue points = the
                 mirrored scans/meshes (from the first pair on,
                 MirrorPreview = on/off). The number at R = how far the
                 mirrored L lands from it. Enter = done, Esc / Cancel =
                 cancel Center, scans/meshes back.
                 Fine (option): gumball in WORLD axes on the symmetry plane
                 (arrow = move, ring = turn; MovePerPx / DegPerPx = step per
                 pixel, default 0.02 mm / 0.005 deg); Enter = keep, Esc =
                 undo this Fine round. In Fine r1 = L1 mirrored in the
                 present centre, number = distance to the R1 you clicked.
                 FitInSpheres (option): the mirror fitted to the scan/mesh
                 itself BY ITS SHAPE, only inside spheres of SphereRadius
                 (default 50 mm) around the tags (0 = whole scan/mesh);
                 holes and one-sided parts are left out. ShowSpheres = show
                 the spheres. Pairs first, then Fine / FitInSpheres as
                 often as you like; after
                 that the pairs are LOCKED (ResetFine = back to the pairs).
                 If MeshFloor was done before, Center levels the floor again
                 by itself with the same Floor points - Floor -> Center gives
                 exactly what Center -> Floor gives.
    MeshFloor    3+ points on the floor, on BOTH sides (not along one sill).
                 After Center: turns ONLY about the axis across the symmetry
                 plane, moves only up/down (+ front/back to a clicked point)
                 - the centre is never touched. Before any Center: the floor
                 is turned flat whatever way it leans (nothing to keep yet;
                 Center later levels it again by itself). REFUSED on
                 scans/meshes that are not centred while others are (Align
                 them to the centred ones first), and with another SymPlane
                 than Center used. Repeat any time.
                 A later Center on scans/meshes the Floor points are not
                 aligned to drops the floor (it says so): MeshFloor again.
    MeshScale    the TRUE size of AI / photogrammetry scans/meshes: pick a
                 curve you drew on it along a measured length (lamp to lamp
                 over the bonnet, with a tape) and type that length - ALL
                 scans/meshes (hidden ones too) and the curve are scaled by
                 true / curve length: about the world origin once MeshCenter
                 is done (centre, floor and front/back 0 stay), else about
                 the middle of the curve. The alignment stays (one factor
                 for all); saved setups get it when you save. Also as the
                 option ScaleByCurve of MeshCenter (Center done, then this).
                 The size of the scans/meshes always comes from such a curve
                 - a photo is only fitted to them.
    MeshAlign    scans/meshes to each other. Click a point on the scan/mesh
                 that STAYS (it turns RED), then the same point on the
                 scan/mesh that MOVES (it turns GREEN, with the ones aligned
                 to it before). Then the clicks go only onto the RED / GREEN
                 one (through the other only where both lie on each other,
                 within ~3 mm). 3+ pairs = turned + moved (number = how far
                 off). FitInSpheres = the GREEN one pulled onto the RED one
                 BY THEIR SHAPE (your clicks need not be exact), only inside
                 the SphereRadius spheres around the pairs; Fine = gumball.
                 Right click = done, Esc = cancel Align (scans/meshes back).
                 EVERY visible scan/mesh takes part (each is prepared for
                 clicking: slow the first time with many). With more than 2
                 visible and some not prepared yet it asks first: Enter = go
                 on with all, Esc = stop - hide the ones you do not need
                 (their layers) and start MeshAlign again. Hidden ones that
                 hang on the moving one still move with it.
                 A tree: aligning a scan/mesh again moves everything hanging
                 on it too; saved setups keep the new alignment.
                 GUARD: a scan/mesh already placed by Center / Floor never
                 moves onto one that is not, and one Center / Floor were
                 CLICKED on never moves onto one they were not clicked on
                 (the centre would be lost) - Align the other way: it stays
                 (RED), the other moves and joins the centred set.
                 Scale (option, off by default): the moving scan/mesh also
                 changes its SIZE (3+ pairs, FitInSpheres) - two AI meshes of the
                 same car (Trellis front + rear), photogrammetry without a
                 true size. The factor is shown with the pairs.
                 Every pair leaves a MARKER (its point, kept in the block)
                 for MeshAutoAlign. Where scans/meshes lie exactly on each
                 other (aligned before), a click takes the one that carries
                 the others (it says which) - hide it to click one alone.
    MeshAutoAlign  many scans/meshes pulled together AT ONCE. Two lists
                 (hidden ones too): 1) which ones MOVE, 2) which ones STAY
                 (e.g. the centred front; preselected: the centred ones).
                 Spheres of SphereRadius at all markers (the Align pairs
                 so far) - click more markers on the overlaps if you like
                 (UndoPt). FitInSpheres = the same as in MeshAlign, but for
                 MANY at once: every moving one is pulled onto the ones that
                 stay AND onto the other moving ones, all together, only
                 inside the spheres; FitInSpheres again = finer.
                 MeshAlign = ONE scan/mesh onto ONE, by your pairs (then
                 FitInSpheres); MeshAutoAlign = MANY together, no new pairs
                 needed (they are already roughly in place).
                 The ones that stay never move. Scale (option): each moving
                 one also gets its own size factor. Not on a list: moves with
                 the listed one it hangs on (Align tree). Enter = keep,
                 Esc = cancel (all back). Saved setups keep the result.
                 Typical: Center the front, Align the others roughly (3
                 pairs each), AutoAlign all onto the front, then a second
                 local Center at the rear and save it as another setup.
    MeshSetup    apply a saved setup (e.g. front bumper / spoiler / rear),
                 save the present positions under a name, or remove one.
                 Saved only WITH THE SCANS/MESHES, at once: scan_setups.json
                 in the folder of the files (any .3dm finds them after
                 LoadMeshes of the same files): per scan/mesh the 4x4
                 rotation+move of its block, the units, the SHA-256 of the
                 file and what Center / Floor set (MeshSetup brings it back).
    MeshMirror   mirrored copies of the visible scans/meshes as blue points,
                 drawn only (again = off)
    LoadPhotos   front / rear / side / top photos (PNG, JPEG) as picture
                 frames on their own layers under 'Reference Photos-
                 Msh2classA', BEHIND the car in their view, about car size.
                 Each asks its view (Front, Rear, Right, Left, Top).
    MeshPhoto    Scale = two points on a photo + their true distance.
                 Center = LEFT/RIGHT pairs on a front / rear / top photo:
                 it turns in its plane so they mirror across the symmetry
                 plane. Floor = 2+ points where the tyres touch the floor:
                 level (a centred photo only moves up / down) at z = 0.
                 PhotoToMesh = pairs: a point on the photo, then the same
                 point on the scan/mesh - the photo is turned / moved /
                 scaled (Scale on) in its own plane onto the car.
                 MeshToPhoto = pairs: a point on a scan/mesh, then where the
                 photo shows it - the scan/mesh (and what is aligned to it)
                 moves in the photo's plane (e.g. a spoiler scanned apart,
                 put on the car from the side photo: up/down, front/back,
                 turned about the side view's axis; not across). Refused
                 for scans/meshes placed by Center / Floor.
                 Photos may be moved by hand any time (their place is read
                 from the frame); saved setups keep them too.
    MOVED BY HAND  you may move / turn a scan/mesh block yourself (gumball,
                 Move...). The next scan/mesh tool shows what moved and asks:
                 Keep = keep it there, saved setups follow (its new place
                 against the scan/mesh it is aligned to); KeepOnly = keep,
                 setups as they were; Back = put it back; Esc = the tool
                 does not start (asked again next time). A hand move of a
                 scan/mesh Center / Floor were clicked on drops that mark
                 (MeshCenter / MeshFloor again); the whole set moved together
                 keeps the marks only if the move keeps the plane / floor.
  VIEW PANEL (small window at the top right of the view, drag it where
  you like - the place is remembered; click to switch; below them the
  Scans/meshes buttons, see SCAN/MESH TOOLS):
                 Legend = this short option list (top left);  CV pts =
                 control points of the surface under the mouse, only the
                 ones on the side facing you;  EMap = with the image
                 reflection_matcap_3dPARTdesign_com.png next to the script
                 (if there) the patches are drawn with that matcap, no
                 Rhino EMap window; otherwise Rhino's EMap;  Zebra = Rhino's
                 analysis on the surfaces (not on the scan/mesh) - they show
                 what is really on, also when switched on before the
                 command, and stay as you leave them;  Graph = curvature
                 combs on the patch under the mouse (highest tooth = half
                 the mean edge length);  Deviation = dots on all patches,
                 green 0, yellow half MeshTol, red MeshTol and more (blue
                 = scan/mesh below), grey no scan/mesh; labels: the biggest + and -;
                 nothing to set for either;  Scan/mesh = hide the scan/mesh
                 (points still go onto it), shown again at the end;
                 Mirror = real mirrored copies of the surfaces over
                 MirrorPlane (Zebra / EMap on them too), kept up to date,
                 deleted when switched off and when the command ends.
  DISPLAY  while the plugin runs every view uses the display mode
           '3dPARTdesign_ScanCAD' (or 'ScanCAD'); if it is missing it is
           imported from msh2classA_3dPARTdesign_com.ini next to the script and
           stays in your display modes. At the end the views get back the
           modes they had (the ScanCAD mode is not removed).
  SnapPctOfView  catch radius for corners/edges, % of the view (zoom in to
                 click right beside a corner on purpose)
  SURFACES (view panel)   left click = corner, right click = go on,
           UndoPt = last corner back, Esc = cancel.
  Fillet   Side A: click its first and last corner (the edges between are
           found; click a corner in between only to choose the way), right
           click. Side B the same, right click (any number of patches).
           Radius step with preview - Radius, RadiusEnd, Cont, Shape (0 =
           auto); right click = apply, Esc = back to the corners. The big
           patches are extended / cut back, no trim; the fillet is split at
           the corners of both sides, G2 everywhere.
  NPatch   Click the corners round the hole (corners between are found
           along free edges; a side may be part of an edge). Right click =
           build: one clean 6x6 span, trimmed.
           Neighbours with a whole edge on it come to it (DefaultCont).
  GlobalMatch  Seams below their stored level (e.g. after hand edits)
           are brought back; the patch with most broken seams stays.
  RebuildAll  Every surface near the scan/mesh that can not take part
           (several spans, rational, over 8 CV - e.g. 20x20 from another
           program; not trimmed) -> one clean 6x6 CV patch on it, like the
           Rebuild button on one: corners exact, edges close, the deviation
           is printed (warned above MeshTol). One UndoSrfEdit = all back.
  ForceMirror  Every patch the mirror plane runs across is cut at it (the
           bigger part stays, no trim); one trimmed at the mirror is rebuilt
           untrimmed. Its side on the plane gets DefaultCont (G2 / G1 / G0).
           TempPoints on the other side are dropped. One UndoSrfEdit.
  Labels   On/off: CV-count and continuity bubbles of finished patches.
  UndoPt / ClearPts / UndoSrf / UndoSrfEdit
                 last point / all points of the unfinished patch (no surface
                 is touched) / the LAST built patch, one per click (a build
                 with SrfSplit=AutoSplit is several surfaces) / last
                 patch-button step (FairN, SmthUVN, EvenUV, StrUV, RelaxUV,
                 ToDevUV, PullN, RstN, MatchUVN, FreeEdgeEdt, Fillet, NPatch,
                 GlobalMatch, Rebuild, RebuildAll, Del)
  Right click with no corners = end msh2classA.  Esc = clear the unfinished patch;
  Esc with nothing started = end msh2classA (finished patches stay in the file).
------------------------------------------------------------------"""


