from operator import itemgetter

HVAC_PARTSCRIPT_API = 1


def _hollow_prism(api, sp, ep, outer_width, outer_height, inner_width, inner_height, profile_x_axis):
    outer = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Rectangular",
        section_params={"Width": outer_width, "Height": outer_height},
        profile_x_axis=profile_x_axis,
    )
    inner = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Rectangular",
        section_params={"Width": inner_width, "Height": inner_height},
        profile_x_axis=profile_x_axis,
    )
    return outer.cut(inner)


def _make_flange(api, position, inward_direction, thickness, duct_width, duct_height, flange_height, profile_x_axis):
    outer_face = api.make_section_face(
        profile="Rectangular",
        section_params={
            "Width": duct_width + 2.0 * flange_height,
            "Height": duct_height + 2.0 * flange_height,
        },
        center=position,
        direction=inward_direction,
        profile_x_axis=profile_x_axis,
    )
    inner_face = api.make_section_face(
        profile="Rectangular",
        section_params={"Width": duct_width, "Height": duct_height},
        center=position,
        direction=inward_direction,
        profile_x_axis=profile_x_axis,
    )
    extrusion = api.unit(inward_direction) * thickness
    return outer_face.extrude(extrusion).cut(inner_face.extrude(extrusion))


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)

    width = float(itemgetter("Width")(params))
    height = float(itemgetter("Height")(params))
    thickness = float(params.get("Thickness", 0.8) or 0.8)
    flange_height = float(params.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(params.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", True))
    show_flange2 = bool(params.get("ShowFlange2", True))
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

    shape = api.fuse_shapes(parts)
    return {"shape": shape}
