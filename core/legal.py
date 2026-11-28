# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Name, version, legal line and CAD Services text shared by the Blender and
FreeCAD versions. The Rhino build keeps its own copies (rhino/header.py,
rhino/panels.py); tools/build.py checks that they are the same."""

VERSION = "msh2classA 0.1.3-alpha"
LEGAL_LINE = ("msh2classA (C) 2026 3dPARTdesign - https://3dPARTdesign.com - "
              "free software under GPL-3.0 (see LICENSE, NOTICE), WITHOUT ANY WARRANTY")
EXPERIMENTAL = "EXPERIMENTAL - NOT TESTED YET. Check every result before you use it."

CAD_SERVICES_EMAIL = "info@3dPARTdesign.com"
CAD_SERVICES_URL = "https://3dPARTdesign.com"
CAD_SERVICES_QUESTIONS = ("Too much work and not enough time?",
                          "Shapes that won't come out the way you planned them?",
                          "Trouble with your scanner or a scan?",
                          "Not sure how to prepare a file for series production?")
CAD_SERVICES_TEXT = ("I can help: on-demand 3D modeling for production - custom body kits, "
                     "NURBS/SubD surfacing, 3D scan cleanup and production preparation.")


def banner(host):
    """Lines printed when a host version starts."""
    return ["=" * 50,
            "{} for {}   ({})".format(VERSION, host, EXPERIMENTAL),
            LEGAL_LINE,
            "=" * 50]
