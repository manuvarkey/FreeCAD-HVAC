from operator import itemgetter

HVAC_PARTSCRIPT_API = 1


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)

    width = float(itemgetter("Width")(params))
    height = float(itemgetter("Height")(params))
    thickness = float(params.get("Thickness", 0.8) or 0.8)
    flange_height = float(params.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(params.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", False))
    show_flange2 = bool(params.get("ShowFlange2", False))
    insulation_thickness = float(params.get("InsulationThickness", 0.0) or 0.0)
    profile_x_axis = context.get("profile_x_axis")

    start = api.vec(sp)
    end = api.vec(ep)
    axis = end - start
    if axis.Length <= 1e-6:
        raise ValueError("Oval straight (PartScript) requires non-zero length")
    direction = api.unit(axis)

    inner_width = width - 2.0 * thickness
    inner_height = height - 2.0 * thickness
    if inner_width <= 0.0 or inner_height <= 0.0:
        raise ValueError("Oval straight Thickness is too large for Width/Height")

    casing_parts = [api.make_hollow_straight(
        start, end, "Oval", {"Width": width, "Height": height},
        thickness, profile_x_axis,
    )]

    start_port = {
        "position": start, "direction": direction, "profile": "Oval",
        "section_params": {"Width": width, "Height": height},
        "profile_x_axis": profile_x_axis,
    }
    end_port = api.copy_port(start_port, position=end, direction=direction * -1.0)
    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        casing_parts.append(api.make_flange(start_port, direction, flange_thickness, flange_height))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        casing_parts.append(api.make_flange(end_port, direction * -1.0, flange_thickness, flange_height))

    casing_shape = api.fuse_shapes(casing_parts) if len(casing_parts) > 1 else casing_parts[0]
    insulation_shape = None
    if insulation_thickness > 0.0:
        insulation_shape = api.make_hollow_straight(
            start, end, "Oval",
            {"Width": width + 2.0 * insulation_thickness, "Height": height + 2.0 * insulation_thickness},
            insulation_thickness, profile_x_axis,
        )

    return {
        "layers": {
            "casing": {"shape": casing_shape},
            "insulation": {"shape": insulation_shape},
        }
    }
