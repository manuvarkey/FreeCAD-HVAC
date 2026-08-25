from operator import itemgetter

HVAC_PARTSCRIPT_API = 1


def _hollow_prism(api, sp, ep, outer_width, outer_height, inner_width, inner_height, profile_x_axis):
    thickness = 0.5 * (outer_width - inner_width)
    return api.make_hollow_straight(
        sp, ep, "Rectangular", {"Width": outer_width, "Height": outer_height},
        thickness, profile_x_axis,
    )


def _make_flange(api, position, inward_direction, thickness, duct_width, duct_height, flange_height, profile_x_axis):
    port = {
        "position": position, "direction": inward_direction, "profile": "Rectangular",
        "section_params": {"Width": duct_width, "Height": duct_height},
        "profile_x_axis": profile_x_axis,
    }
    return api.make_flange(port, inward_direction, thickness, flange_height)


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)

    width = float(itemgetter("Width")(params))
    height = float(itemgetter("Height")(params))
    thickness = float(params.get("Thickness", 0.8) or 0.8)
    flange_height = float(params.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(params.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", True))
    show_flange2 = bool(params.get("ShowFlange2", True))
    insulation_thickness = float(params.get("InsulationThickness", 0.0) or 0.0)
    profile_x_axis = context.get("profile_x_axis")

    start = api.vec(sp)
    end = api.vec(ep)
    axis = end - start
    length = axis.Length
    if length <= 1e-6:
        raise ValueError("Rectangular straight (PartScript) requires non-zero length")
    direction = api.unit(axis)

    inner_width = width - 2.0 * thickness
    inner_height = height - 2.0 * thickness
    if inner_width <= 0.0 or inner_height <= 0.0:
        raise ValueError("Rectangular straight Thickness is too large for Width/Height")

    parts = [_hollow_prism(api, sp, ep, width, height, inner_width, inner_height, profile_x_axis)]

    # Flanges are extruded inward from each port plane, into the duct's own
    # length (overlapping the tube's wall), rather than protruding past the
    # port into the neighboring segment/junction's space.
    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(
            _make_flange(api, start, direction, flange_thickness, width, height, flange_height, profile_x_axis)
        )
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(
            _make_flange(
                api, end, direction * -1.0, flange_thickness, width, height, flange_height, profile_x_axis
            )
        )

    casing_shape = api.fuse_shapes(parts)
    insulation_shape = None
    if insulation_thickness > 0.0:
        insulation_shape = api.make_hollow_straight(
            start, end, "Rectangular",
            {"Width": width + 2.0 * insulation_thickness, "Height": height + 2.0 * insulation_thickness},
            insulation_thickness, profile_x_axis,
        )

    return {
        "layers": {
            "casing": {"shape": casing_shape},
            "insulation": {"shape": insulation_shape},
        }
    }
