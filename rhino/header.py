# -*- coding: utf-8 -*-
# msh2classA 0.1.1-alpha   (the version is also in VERSION below and printed at start)
# Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign - https://3dPARTdesign.com
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, with the
# additional terms in the NOTICE file (GPL-3.0 section 7: the names
# "msh2classA" and "3dPARTdesign", and the author attribution).
# It is distributed WITHOUT ANY WARRANTY. See LICENSE and NOTICE.
"""
msh2classA - class-A single-span patches on a mesh, patch after patch,
without leaving the command.
The mesh (plain or block) is loaded once and cached for the Rhino session.
The "Help" option prints the full description; the Legend button shows a short one.

File layout
  math core (no Rhino):  vectors, B-spline basis, least squares, curve fit,
                         Bezier split, patch fit with G0/G1/G2 + mirror,
                         three-step fit (mesh -> match -> mesh), smoothing
                         of a patch with its neighbours
  Rhino part:            mesh loading, pick candidates (edges/curves),
                         sides, and the Session = SettingsMixin + ScanMixin
                         + PolygonMixin + DrawMixin + BuildMixin + SmoothMixin

Rhino 8 / 9, Windows and Mac (IronPython 2.7 or Python 3). Pure-Python
math, no numpy. Any model unit (mm, cm, m, inch, ...): defaults are given
in mm and converted; lengths in messages get the decimals the unit needs.
"""

from __future__ import print_function, division

VERSION = "msh2classA 0.1.1-alpha"
import json
import math
import os
import traceback
import time

MATCAP_FILE = "reflection_matcap_3dPARTdesign_com.png"     # next to this script: own EMap


SCAN_DISPLAY_FILE = "msh2classA_3dPARTdesign_com.ini"      # next to this script: the display mode for scan work
SCAN_DISPLAY_NAMES = ("3dPARTdesign_ScanCAD",)   # the display mode looked for


def matcap_path():
    """The matcap image next to the script, or None."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, MATCAP_FILE)
        return p if os.path.isfile(p) else None
    except Exception:
        return None

try:
    import Rhino
    import Rhino.Geometry as rg
    import Rhino.Input.Custom as ric
    import Rhino.DocObjects as ro
    import rhinoscriptsyntax as rs
    import scriptcontext as sc
    import System.Drawing as sd
    IN_RHINO = True
except ImportError:
    IN_RHINO = False

LEGAL_LINE = ("msh2classA (C) 2026 3dPARTdesign - https://3dPARTdesign.com - "
              "free software under GPL-3.0 (see LICENSE, NOTICE), WITHOUT ANY WARRANTY")

