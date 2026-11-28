# DEVNOTES – how the code is written (read before every change)

## Rule 1: one job = ONE shared function, used everywhere

If two places do the same job (a click, the point under the cursor, a message, a
check after a build), they call the SAME function. A fix in that function then
fixes every tool at once, and a bug cannot live in one tool and not in another.
Before writing new code: look for the function that already does the job and use
it. If it does almost the job, extend it (a parameter), do not copy it.

`tests/test_rules.py` checks the rules below on the sources. Run it with every
build; add a check when a new shared function gets a rule.

## Shared functions (rhino/scan_tools.py unless noted)

| job | function | never do instead |
|---|---|---|
| wait for a click (every tool, patches, TempPts, Fine drag) | `click_get(gp, mouse_up)` | `gp.Get()`, `gp.Constrain(...)` |
| keep the click plane right when the view changes (zoom wheel, Ctrl/Alt+drag, pan, rotate, named views) | `tracked_get` + `_reconstrain` + `view_constrain` (called by `click_get`) | own view-change handlers |
| the ray under the cursor (click, mouse move, hover over a patch) | `cursor_ray(vp, P, p2)`, `click_ray(gp)` | `GetFrustumLine` elsewhere, camera maths by hand |
| the point ON a mesh under the cursor | `mesh_ray_point(mesh, ray)`; in the patch session `mesh_under(...)` / `mesh_at_click(gp)` (session_view.py) | `gp.Point()` / `e.Point` as a mesh point, `GetPoint.Constrain(mesh)` |
| the scan/mesh point for a patch point (build fit, start surface, PullN, deviation) | `scan_pt` (session_scan.py): only faces turned like the patch, only UNDER the point along N (`under`, `along_normal`) | `ClosestMeshPoint` as a fit target (the nearest point of a hole's rim is BESIDE: the patch would be pulled sideways) |
| a side held on its curve (drawn curve, Sweep side, MIRROR side) | the side loop in `_build` (hard targets) | soft targets for a curve side |
| pick on several scan/meshes | `pick_on_scans`, `ray_hit_scans` | own loops |
| frame marks (center/floor), markers, setups | `frame_*`, `markers_*`, `floor_xf`, `relevel_after_center` | reading the user strings directly |
| hand-move check around a tool | `_with_hand_check` | per-tool checks |
| warnings after a build (folded CV net, corners) | `fold_warning`, `corner_check`, `build_check` (session_build.py), `cv_order_worst` (core) | own checks per fit mode |

## Why the "no entry" cursor came back

A GetPoint constrained to a mesh (`Constrain(mesh)`) shows Rhino's no-entry
cursor whenever the cursor is off the scan/mesh. While it shows, Rhino also gives
no MouseMove point, so hover, CV points and Graph stop updating. This happens
during zoom/scroll, between patches and for patches in the air. All clicks
constrain only to the camera-facing click plane (always under the cursor). The
point on the mesh comes from the ray (`mesh_ray_point`). Never constrain a
GetPoint to a mesh again.

## Other standing rules

- User texts: always "scan/mesh" (never "scan" or "mesh" alone).
- Esc texts say WHAT ends ("Esc = end MeshAlign", "Esc = end msh2classA").
- Test builds: `msh2classA_temp_testN.zip`. The repo version stays as it is until a release.
- The user pushes to GitHub himself: only give instructions.
- No half-done work: test, and mark what could not be tested (needs real Rhino).
