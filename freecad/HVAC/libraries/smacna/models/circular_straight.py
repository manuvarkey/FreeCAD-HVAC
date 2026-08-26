HVAC_PARTSCRIPT_API = 2


def generate(context):
    api = context["hvac_api"]
    p = dict(context.get("params") or context.get("properties") or {})
    start = api.vec(context["start_point"])
    end = api.vec(context["end_point"])
    axis = end - start
    if axis.Length <= 1.0e-7:
        raise ValueError("Circular straight duct length must be positive")

    # Diameter is the clear-air boundary diameter.
    air = api.make_profile(
        "Circular",
        {"Diameter": p["Diameter"]},
        center=start,
        direction=axis,
        profile_x_axis=context.get("profile_x_axis"),
    )
    t = max(float(p.get("Thickness", 0.8) or 0.0), 0.0)
    ins = max(float(p.get("InsulationThickness", 0.0) or 0.0), 0.0)
    b0 = api.extrude(air, axis, solid=True)
    b1 = api.extrude(api.offset_profile(air, t), axis, solid=True)
    layers = {"casing": {"shape": api.refine(api.cut(b1, b0)) if t > 1.0e-7 else None}}
    if ins > 1.0e-7:
        b2 = api.extrude(api.offset_profile(air, t + ins), axis, solid=True)
        layers["insulation"] = {"shape": api.refine(api.cut(b2, b1))}
    return {"layers": layers}
