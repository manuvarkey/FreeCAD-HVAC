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


def build_rectangular_straight(context):
    api = context["hvac_api"]
    
    sp = context["start_point"]
    ep = context["end_point"]
    props = dict(context.get("properties", {}) or {})

    width = float(props.get("Width", 100.0))
    height = float(props.get("Height", 100.0))
    profile_x_axis = context.get("profile_x_axis")

    shape = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Rectangular",
        section_params={
            "Width": width,
            "Height": height,
        },
        profile_x_axis=profile_x_axis,
    )
    return {"shape": shape}


def build_rectangular_straight_fcstd(context):
    """
    FCStd-template-backed rectangular straight duct. Calls
    HVACLibraryAPI.shape_from_fcstd directly (rather than a JSON-declared
    template dispatch), so the template file, its VarSet params, and port
    names are all chosen here in Python -- e.g. this is where a future
    variant could branch to a different .FCStd file based on size range or
    construction class, not just always use the one file below.
    """
    api = context["hvac_api"]

    sp = api.vec(context["start_point"])
    ep = api.vec(context["end_point"])
    props = dict(context.get("properties", {}) or {})

    width = float(props.get("Width", 100.0) or 100.0)
    height = float(props.get("Height", 100.0) or 100.0)
    thickness = float(props.get("Thickness", 0.8) or 0.8)
    flange_height = float(props.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(props.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(props.get("ShowFlange1", True))
    show_flange2 = bool(props.get("ShowFlange2", True))
    length = (ep - sp).Length
    if length <= 1e-6:
        raise ValueError("Rectangular straight (FCStd) requires non-zero length")

    fcstd_path = api.resolve_library_file(context, "models/rectangular_straight.FCStd")
    shape = api.shape_from_fcstd(
        fcstd_path,
        context,
        params={
            "Duct_Width": width,
            "Duct_Height": height,
            "Duct_SheetThickness": thickness,
            "Flange_Height": flange_height,
            "Flange_Thickness": flange_thickness,
            "Duct_Length": length,
            "Flange_Flange1": show_flange1,
            "Flange_Flange2": show_flange2,
        },
        result_object="ResultObject",
    )

    return {"shape": shape}


def build_circular_straight(context):
    api = context["hvac_api"]

    sp = context["start_point"]
    ep = context["end_point"]
    props = dict(context.get("properties", {}) or {})

    diameter = float(props.get("Diameter", 100.0))
    profile_x_axis = context.get("profile_x_axis")

    shape = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Circular",
        section_params={
            "Diameter": diameter,
        },
        profile_x_axis=profile_x_axis,
    )
    return {"shape": shape}


def build_circular_straight_openscad(context):
    """
    OpenSCAD-backed circular straight duct -- test type exercising
    HVACLibraryAPI.shape_from_openscad (see openscad_shapes.py). The .scad
    model (models/circular_straight.scad) is authored in a canonical local
    frame -- origin at the start port, +Z along the duct axis toward the
    end port -- and placed into the real network with the same
    "frame from direction + origin" transform every other generator uses,
    via api.make_profile_frame.
    """
    api = context["hvac_api"]

    sp = api.vec(context["start_point"])
    ep = api.vec(context["end_point"])
    props = dict(context.get("properties", {}) or {})

    diameter = float(props.get("Diameter", 100.0) or 100.0)
    thickness = float(props.get("Thickness", 0.8) or 0.8)
    length = (ep - sp).Length
    if length <= 1e-6:
        raise ValueError("Circular straight (OpenSCAD) requires non-zero length")

    scad_path = api.resolve_library_file(context, "models/circular_straight.scad")
    shape = api.shape_from_openscad(
        scad_path,
        params={
            "diameter": diameter,
            "thickness": thickness,
            "length": length,
        },
    )

    direction = api.unit(ep - sp)
    profile_x_axis = context.get("profile_x_axis")
    placement, _x, _y, _z = api.make_profile_frame(direction, preferred_x=profile_x_axis, origin=sp)
    shape.transformShape(placement.toMatrix(), True, False)

    return {"shape": shape}


def build_oval_straight(context):
    api = context["hvac_api"]
    
    sp = context["start_point"]
    ep = context["end_point"]

    props = dict(context.get("properties", {}) or {})
    width = float(props.get("Width", 200.0))
    height = float(props.get("Height", 100.0))
    profile_x_axis = context.get("profile_x_axis")

    shape = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Oval",
        section_params={
            "Width": width,
            "Height": height,
        },
        profile_x_axis=profile_x_axis,
    )

    return {"shape": shape}
