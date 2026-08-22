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

"""
Shared "apply a GeometryResult onto a FreeCAD object" logic for DuctSegment
and DuctComponent -- the one piece of execute() that was genuinely identical
between the two (everything else, like trim planes vs. connection lengths,
was already different). Internal to core/ -- not part of the stable
library_api.py surface for library/generator authors.
"""

import Part


def apply_geometry_result(obj, result):
    """
    Write CasingShape/InsulationShape from result.components onto `obj`,
    then derive Shape as the compound of whichever of those two actually
    have a real shape (in casing-then-insulation order, so the ViewProvider
    can work out per-component face ranges without guessing -- see
    core/_component_appearance.py).
    """
    casing_shape = _component_shape(result.casing)
    insulation_shape = _component_shape(result.insulation)

    obj.CasingShape = casing_shape if casing_shape is not None else Part.Shape()
    obj.InsulationShape = insulation_shape if insulation_shape is not None else Part.Shape()

    obj.Shape = Part.makeCompound(
        [s for s in (casing_shape, insulation_shape) if s is not None]
    )


def _component_shape(component):
    """A ComponentGeometry's own shape, or None if absent/null."""
    if component is None:
        return None
    shape = component.shape
    if shape is None:
        return None
    try:
        if shape.isNull():
            return None
    except Exception:
        pass
    return shape
