HVAC_PARTSCRIPT_API = 2


def generate(context):
    api = context["hvac_api"]
    p = dict(context.get("params") or context.get("properties") or {})
    start = api.vec(context["start_point"])
    end = api.vec(context["end_point"])
    axis = end - start
    if axis.Length <= 1.0e-7:
        raise ValueError("Oval straight duct length must be positive")

    # Width/Height are the clear-air boundary dimensions.
    air = api.make_profile(
        "Oval",
        {"Width": p["Width"], "Height": p["Height"]},
        center=start,
        direction=axis,
        profile_x_axis=context.get("profile_x_axis"),
    )

    def build_envelope(offset):
        return api.extrude(api.offset_profile(air, offset), axis, solid=True)

    return api.build_layered_geometry(build_envelope, context["construction_layers"], p)
