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
  DRAWING  Side 1: click start, points on the mesh, right click = end
           side. Sides 2, 3: points, right click. Side 4 returns to
           corner 1: optional points, right click = BUILD.
           A click on an edge / curve / its end is always a corner and
           ends the side at once. Two corners on the same edge or curve
           -> that side follows it (G0/G1/G2 to a surface edge).
           Near an edge END the corner snaps to that end (the
           neighbour's corner); a corner made on the mesh within 2 x
           MeshTol of an edge is moved onto it; points clicked along
           an edge do not turn that side into a mesh side. Narrow
           blend: click its 4 corners = the 4 corners of the two
           patches, both long sides then follow the edges.
  SIDE BUBBLES  Click: G0 -> G1 -> G2.  On a surface edge = continuity
           to it. On the mesh = rows RESERVED for a future neighbour.
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
                           the mesh; shape stays
             PullN    N    fit the patch to the mesh, CVs only along the
                           normal (free edges also across the edge): the
                           CV layout (EvenUV / StrUV / RelaxUV) stays;
                           neighbours give way with their G0/G1/G2 rows.
                           A TempPts patch: onto its points, not the mesh
             RstN     N    the smoothest shape its edges / seams allow,
                           CVs only along the normal, no mesh, no points
                           (then PullN)
             FreeEdgeEdt   redraw a free edge (no neighbour / mirror): first
                           click the edge (the nearest one lights up), then
                           a) start AT a corner dot, points on the mesh, end
                              AT the other corner: only the middle of the
                              edge moves, both corners stay;
                           b) start BESIDE the patch, points on the mesh
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
                           themselves); the patch itself and the mesh are
                           not used to change anything
           Button colours: green fine, yellow could be better, red = worth
           a click (FairN: curvature bumps, EvenUV: spacing ratio, StrUV:
           row kinks, RelaxUV: skew, PullN: mesh deviation vs MeshTol,
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
  FitSrfTo       WholeMesh = fit the whole mesh under the patch
                 TempPts = click points the patch must follow (e.g. to
                           bridge a recess); the rest is as smooth as the
                           edges allow. The points are kept ON the patch:
                           every button later uses them, not the mesh
                           (RstN = smoothest the edges allow, then PullN =
                           onto the points).
                 NoScan  = only the edges lie on the mesh; the inside is
                           the smoothest surface the edges and G0/G1/G2
                           allow (smoothing has no mesh leash there)
  SrfSplit       OneSrf = one single-span patch
                 AutoSplit = as many single-span patches as the mesh needs
                 (cut from one smooth surface, C4 between them)
  MeshTol        allowed mesh deviation in model units: picking CVs,
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
                 Checked (all scale-free): mesh deviation (HintTolPct),
                 how far the mesh turns (4CV max 100 deg, 6CV max 130
                 deg) and that the patch would not wave. From side 3 on
                 the whole patch (inside and both directions) is checked.
  MESH TOOLS (buttons in the small panel - before the mesh is picked and
  while you work on patches (then the work is left for the tool and comes
  back with the mesh where it is now) - and options at 'Click mesh'; they
  move / turn only the mesh BLOCKS - the customer's files are never
  touched, the scale never changes):
    LoadMeshes   many STL / OBJ / PLY meshes at once as LINKED blocks
                 (embedded if Rhino refuses the link), each on its own
                 layer under 'Reference Meshes- Msh2classA', own colour; hidden after loading
                 except the last one. PLY point clouds are refused (mesh
                 them first)
    MeshAlign    meshes to each other: click a point on the mesh that STAYS,
                 then the same point on the mesh that MOVES; 3+ pairs =
                 turned + moved (the number = how far off). Fit = automatic,
                 ONLY inside the spheres of AutoFitRadius around the pairs
                 (as AutoFit of Center; the rest of the meshes is never
                 used), Fine = gumball. Right click = done, Esc = back. Works as a tree:
                 a mesh aligned to another remembers it; aligning a mesh
                 again moves every mesh hanging on it too. Saved setups
                 keep the new alignment
    MeshCenter   click pairs LEFT / RIGHT on the same features of the
                 visible meshes: after every pair ALL meshes (one aligned
                 structure, hidden ones too) turn + move so the symmetry plane of the
                 pairs is the MirrorPlane; blue points = the mirrored
                 meshes (always shown, MirrorPreview = on/off); the
                 number at every L / R tag = how far the mirrored
                 partner of the other side lies from it (how well the
                 mesh sits there), details in the command line; Enter =
                 done, Esc / Cancel = back as before.
                 Tags are pins: the cross-hair is the exact point; the
                 blue cross = the LEFT tag after the mirror, the number at
                 R = its distance to the right tag.
                 In Fine: r1 = L1 mirrored in the present centre, its
                 number = distance to the R1 you clicked (grey cross).
                 Fine (option): a small gumball in WORLD axes on the
                 symmetry plane - drag an arrow = move along X/Y/Z, a ring
                 = turn about it; MovePerPx / DegPerPx = step per pixel of
                 the drag (default 0.02 mm / 0.005 deg). Mirror and tags
                 follow live; all meshes move (hidden ones too).
                 Enter = keep, Esc = undo this Fine round. Fine is pinned to
                 the bottom right corner of the view (turns with the view);
                 the yellow cross = the point it turns about.
                 AutoFit (option, in Center and Fine): the mirror is fitted
                 to the mesh itself - mesh points mirrored and pulled onto
                 the mesh. With tags: only the points inside spheres of
                 AutoFitRadius (default 50 mm) around every tag - the
                 places you marked as symmetric; AutoFitRadius 0 = whole
                 mesh. Far points (holes, one-sided parts) are left out.
                 After a radius change the spheres show see-through for 3 s;
                 SpherePreview = keep them shown.
                 Order: pairs first (rough), then Fine and AutoFit in any
                 order, as often as you like. After the first Fine /
                 AutoFit the pairs are LOCKED (a new pair would undo that
                 work); ResetFine = back to the result of the pairs and
                 unlocks them
    MeshFloor    3+ points on the floor: turns only about the axis across
                 the symmetry plane, moves only up/down (+ front/back to a
                 clicked point) - the symmetry stays; repeat any time.
                 Click the floor on BOTH sides (not along one sill)
    MeshSetup    apply a saved setup (e.g. front bumper / spoiler / rear),
                 save the present positions under a name, or remove one.
                 Saved only WITH THE MESHES, at once: scan_setups.json in
                 the folder of the mesh files (nothing in the .3dm; any
                 file finds them after LoadMeshes of the same meshes). Per
                 mesh the 4x4 rotation+move of its block from its 0,0,0
    MeshMirror   mirrored copies of the visible meshes as blue points,
                 drawn only (again = off)
  VIEW PANEL (small window at the top right of the view, drag it where
  you like - the place is remembered; click to switch; below them the
  Meshes buttons, see MESH TOOLS):
                 Legend = this short option list (top left);  CV pts =
                 control points of the surface under the mouse, only the
                 ones on the side facing you;  EMap = with the image
                 reflection_matcap_3dPARTdesign_com.png next to the script
                 (if there) the patches are drawn with that matcap, no
                 Rhino EMap window; otherwise Rhino's EMap;  Zebra = Rhino's
                 analysis on the surfaces (not on the mesh) - they show
                 what is really on, also when switched on before the
                 command, and stay as you leave them;  Graph = curvature
                 combs on the patch under the mouse (highest tooth = half
                 the mean edge length);  Deviation = dots on all patches,
                 green 0, yellow half MeshTol, red MeshTol and more (blue
                 = mesh below), grey no mesh; labels: the biggest + and -;
                 nothing to set for either;  Mesh = hide the mesh
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
  ForceMirror  Every patch the mirror plane runs across is cut at it (the
           bigger part stays, no trim); one trimmed at the mirror is rebuilt
           untrimmed. Its side on the plane gets DefaultCont (G2 / G1 / G0).
           TempPoints on the other side are dropped. One UndoSrfEdit.
  Labels   On/off: CV-count and continuity bubbles of finished patches.
  UndoPt / ClearSrf / UndoSrf / UndoSrfEdit
                 last point / unfinished patch / last patch / last
                 patch-button step (FairN, SmthUVN, EvenUV, StrUV, RelaxUV,
                 ToDevUV, PullN, RstN, MatchUVN, FreeEdgeEdt, Fillet, NPatch,
                 GlobalMatch, Del)
  Right click with no corners = finish.  Esc = finish.
------------------------------------------------------------------"""


