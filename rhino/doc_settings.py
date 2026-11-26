# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# Fragment of the Rhino build: tools/build.py joins the files listed in
# build_order.txt into one msh2classA.py. Not importable on its own.
# <<< dev-only
# =============================================================================
#                              RHINO PART
# =============================================================================

SIDE_G_KEY = "msh2classA_sideG"
PTS_KEY = "msh2classA_pts"            # user text on a patch built through TempPoints: the points (JSON)
NPATCH_KEY = "msh2classA_npatch"      # user text on an NPatch: "sides;deviation"        # user text on a patch: {"side": level} wanted at its seams


def side_levels(oid):
    """{side: 0/1/2} stored on patch oid (sides built to / matched with a neighbour)."""
    try:
        txt = rs.GetUserText(oid, SIDE_G_KEY)
        return dict((int(k), int(v)) for k, v in json.loads(txt).items()) if txt else {}
    except Exception:
        return {}


def set_side_level(oid, side, level):
    try:
        d = side_levels(oid); d[int(side)] = int(level)
        rs.SetUserText(oid, SIDE_G_KEY, json.dumps(dict((str(k), v) for k, v in d.items())))
    except Exception:
        pass


# Settings that are LENGTHS are kept in millimetres, so a value set in a mm
# file stays the same real size in an inch or metre file (same Rhino session).
LENGTH_KEYS = ("autotol", "mtol", "fit_radius", "fine_mv")


def _mm_per_unit():
    try:
        return Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, Rhino.UnitSystem.Millimeters)
    except Exception:
        return 1.0


def opt_get(key, default):
    v = sc.sticky.get("M2CA_" + key, None)
    if v is None: return default
    if key in LENGTH_KEYS and isinstance(v, tuple) and len(v) == 2 and v[0] == "mm":
        return v[1] / _mm_per_unit()
    return v


def opt_set(key, val):
    if key in LENGTH_KEYS:
        sc.sticky["M2CA_" + key] = ("mm", float(val) * _mm_per_unit())
    else:
        sc.sticky["M2CA_" + key] = val


def T(p):
    return (p.X, p.Y, p.Z)

def P3(t):
    return rg.Point3d(t[0], t[1], t[2])

def col(r, g, b):
    return sd.Color.FromArgb(r, g, b)

C_RED = None; C_BLUE = None; C_HOVER = None; C_PT = None; C_GRAY = None

def init_colors():
    global C_RED, C_BLUE, C_HOVER, C_PT, C_GRAY
    C_RED = col(235, 0, 190); C_BLUE = col(30, 60, 230)   # PINK / BLUE directions (red = hint)
    C_HOVER = col(255, 200, 0); C_PT = col(0, 170, 0); C_GRAY = col(90, 90, 90)

class UserAbort(Exception):
    """Esc pressed during a long wait."""
    pass

_ESC = [False]

def _on_escape(sender, e):
    _ESC[0] = True

def hook_escape():
    """Esc key -> flag (works while Rhino processes events); old hook removed
    first so repeated runs do not stack handlers."""
    old = sc.sticky.get("M2CA_esc_hook")
    try:
        if old is not None: Rhino.RhinoApp.EscapeKeyPressed -= old
    except Exception: pass
    try:
        Rhino.RhinoApp.EscapeKeyPressed += _on_escape
        sc.sticky["M2CA_esc_hook"] = _on_escape
    except Exception: pass

def esc_pressed():
    try:
        if sc.escape_test(False): _ESC[0] = True
    except Exception:
        try: Rhino.RhinoApp.Wait()
        except Exception: pass
    return _ESC[0]

def check_esc():
    if esc_pressed():
        _ESC[0] = False
        raise UserAbort()

def show_busy(msg="Working..."):
    _ESC[0] = False
    msg = msg + "   (Esc = stop)"
    try:
        Rhino.RhinoApp.SetCommandPrompt(msg)
    except Exception: pass
    try:
        vp = sc.doc.Views.ActiveView.ActiveViewport
        dot = rs.AddTextDot(msg, vp.CameraTarget)
        rs.ObjectColor(dot, (255, 200, 0))
        sc.doc.Views.Redraw()
        return dot
    except Exception:
        Rhino.RhinoApp.SetCommandPrompt(msg)
        return None

def hide_busy(dot_id):
    if dot_id is not None:
        try:
            rs.DeleteObject(dot_id); sc.doc.Views.Redraw()
        except Exception:
            pass

