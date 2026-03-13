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

import FreeCAD
import Part

from .... import hvaclib


def build_rectangular_straight(context):
    sp = context["start_point"]
    ep = context["end_point"]
    width = context["properties"].get("Width", 100.0)
    height = context["properties"].get("Height", 100.0)
    shape = hvaclib.create_rectangular_duct_geom(sp, ep, width, height)
    return {"shape": shape}


def build_circular_straight(context):
    sp = context["start_point"]
    ep = context["end_point"]
    diameter = context["properties"].get("Diameter", 100.0)
    shape = hvaclib.create_circular_duct_geom(sp, ep, diameter)
    return {"shape": shape}
