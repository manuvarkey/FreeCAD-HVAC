from operator import itemgetter

HVAC_PARTSCRIPT_API = 1


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)

    width = float(itemgetter("Width")(params))
    height = float(itemgetter("Height")(params))
    thickness = float(params.get("Thickness", 0.8) or 0.8)
    profile_x_axis = context.get("profile_x_axis")

    inner_width = width - 2.0 * thickness
    inner_height = height - 2.0 * thickness
    if inner_width <= 0.0 or inner_height <= 0.0:
        raise ValueError("Oval straight Thickness is too large for Width/Height")

    outer = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Oval",
        section_params={"Width": width, "Height": height},
        profile_x_axis=profile_x_axis,
    )
    inner = api.make_straight_shape(
        start_point=sp,
        end_point=ep,
        profile="Oval",
        section_params={"Width": inner_width, "Height": inner_height},
        profile_x_axis=profile_x_axis,
    )
    shape = outer.cut(inner)
    return {"shape": shape}
