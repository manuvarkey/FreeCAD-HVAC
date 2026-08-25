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

from . import _construction_schema


def apply_geometry_result(obj, result):
    """
    Write Layer_<id>_Shape (see core/_construction_schema.py) for every
    layer in obj.ConstructionLayerIds from result.layers, then derive Shape
    as the compound of whichever layers actually have a real shape, in that
    same declared order -- so the ViewProvider can work out per-layer face
    ranges without guessing (see core/_component_appearance.py).
    """
    layer_ids = list(getattr(obj, "ConstructionLayerIds", []) or [])
    ordered_shapes = []

    for layer_id in layer_ids:
        layer_geometry = result.layers.get(layer_id)
        shape = _layer_shape(layer_geometry)
        setattr(obj, _construction_schema.shape_property_name(layer_id), shape if shape is not None else Part.Shape())
        if shape is not None:
            ordered_shapes.append(shape)

    obj.Shape = Part.makeCompound(ordered_shapes)


def _layer_shape(layer_geometry):
    """A LayerGeometry's own shape, or None if absent/null."""
    if layer_geometry is None:
        return None
    shape = layer_geometry.shape
    if shape is None:
        return None
    try:
        if shape.isNull():
            return None
    except Exception:
        pass
    return shape
