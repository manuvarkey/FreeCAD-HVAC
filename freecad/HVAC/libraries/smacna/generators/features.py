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
Construction feature generators for the smacna library -- resolved and
invoked by HVACLibraryRegistry.build_geometry()'s own feature-generation
pass (see freecad/HVAC/library/Library.py), never called directly by core.
Every function here matches the generic `generate_<name>(api, ctx)`
interface a type-def's own "construction.features" block references by
name -- see freecad/HVAC/libraries/README.md's "Construction features"
section for the full contract.
"""

import Part


def _make_flange_collar(position, inward_direction, thickness, duct_outer_diameter, flange_height):
    """
    A flat, circular flange collar at `position`'s own cross-section,
    extruded `thickness` along `inward_direction` -- into the duct's own
    length (overlapping the wall), rather than protruding past the port
    into the neighboring segment/junction's space.
    """
    outer = Part.makeCylinder(
        duct_outer_diameter * 0.5 + flange_height, thickness, position, inward_direction
    )
    inner = Part.makeCylinder(duct_outer_diameter * 0.5, thickness, position, inward_direction)
    return outer.cut(inner)


def generate_transverse_flange(api, ctx):
    """
    Circular transverse-joint flange collar(s) at a straight duct's own two
    port planes -- migrated from smacna/models/circular_straight.py's old
    inline flange-fusing (now a standalone construction feature; see
    smacna/types/segments/circular_straight.json's "construction.features"
    block). Builds whichever of the two ports' collars ShowFlange1/
    ShowFlange2 enables, as one compound shape (a feature always returns a
    single Part.Shape or None -- see library/construction.py's
    ConstructionFeatureDef/library/Library.py's build_geometry()).
    """
    params = ctx.parameters
    diameter = float(params["Diameter"])
    flange_height = float(params.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(params.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", True))
    show_flange2 = bool(params.get("ShowFlange2", True))

    start = api.vec(ctx.context["start_point"])
    end = api.vec(ctx.context["end_point"])
    direction = api.unit(end - start)

    parts = []
    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange_collar(start, direction, flange_thickness, diameter, flange_height))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange_collar(end, direction * -1.0, flange_thickness, diameter, flange_height))

    if not parts:
        return None

    return api.fuse_shapes(parts) if len(parts) > 1 else parts[0]
