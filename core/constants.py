# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# <<< dev-only
"""Constants the core shares with the Rhino part. In the Rhino build they come
from rhino/candidates.py; tools/build.py checks that both values agree."""
SEAM_ANG = 0.1      # deg: below = G1
SEAM_K = 0.02       # relative curvature difference: below = G2
