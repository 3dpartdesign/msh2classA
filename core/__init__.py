# >>> dev-only: removed by tools/build.py
# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
# <<< dev-only
"""msh2classA math core: B-spline / Bezier patches, least squares fitting with
G0/G1/G2 seams, smoothing, fillets, N-sided patches. Pure Python, no host program."""
from .vectors import *  # noqa
from .bspline import *  # noqa
from .evaluate import *  # noqa
from .lsq import *  # noqa
from .curve_fit import *  # noqa
from .bezier import *  # noqa
from .patch_fit import *  # noqa
from .smoothing import *  # noqa
from .fillet import *  # noqa
from .npatch import *  # noqa
from .gap_fillet import *  # noqa
from .cv_order import *  # noqa
