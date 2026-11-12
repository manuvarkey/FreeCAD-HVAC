# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

################################################################################
#                                                                              #
#   Copyright (c) 2026 Francisco Rosa                                          #
#                                                                              #
#   This addon is free software; you can redistribute it and/or modify it      #
#   under the terms of the GNU Lesser General Public License as published      #
#   by the Free Software Foundation; either version 2.1 of the License, or     #
#   (at your option) any later version.                                        #
#                                                                              #
#   This addon is distributed in the hope that it will be useful,              #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                       #
#                                                                              #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with this addon. If not, see https://www.gnu.org/licenses    #
#                                                                              #
################################################################################

"""Stable public helpers and geometry primitives for HVAC libraries.

Library generators and PartScripts receive this class as
``context["hvac_api"]``. The API is grouped from low-level, non-shape
helpers through direct Part geometry operations to HVAC-specific convenience
recipes. Library code must use this surface instead of importing FreeCAD,
Part, or internal HVAC modules directly.
"""


from . import _api_booleans, _api_ports, _api_profiles, _api_recipes, _api_solids
from ._api_booleans import _BooleansMixin
from ._api_common import _EPS, HVACProfile  # noqa: F401 -- re-exported for library code
from ._api_ports import _PortsMixin
from ._api_profiles import _ProfilesMixin
from ._api_recipes import _RecipesMixin
from ._api_solids import _SolidsMixin


class HVACLibraryAPI(_PortsMixin, _ProfilesMixin, _SolidsMixin, _BooleansMixin, _RecipesMixin):
    """Stable geometry and context API exposed to HVAC library code.

    The methods are written in topic modules, from low-level helpers to HVAC
    recipes: _api_ports (vectors, contexts, ports, trim records),
    _api_profiles (frames, sections), _api_solids (extrusions, sweeps,
    lofts), _api_booleans (trims, booleans, topology, finishing) and
    _api_recipes (layered builds, standard fittings, external geometry).
    This class is still the one public surface: library code only ever
    sees HVACLibraryAPI.
    """

    API_VERSION = 2
    EPS = 1.0e-9


for _module in (_api_ports, _api_profiles, _api_solids, _api_booleans, _api_recipes):
    _module.HVACLibraryAPI = HVACLibraryAPI
del _module
