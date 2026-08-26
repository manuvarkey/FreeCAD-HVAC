HVAC_PARTSCRIPT_API = 2


def generate(context):
    api = context["hvac_api"]
    p = dict(context.get("params") or context.get("properties") or {})
    start = api.vec(context["start_point"])
    end = api.vec(context["end_point"])
    axis = end - start
    if axis.Length <= 1.0e-7:
        raise ValueError("Rectangular straight duct length must be positive")

    # Width/Height are the clear-air boundary dimensions.
    air = api.make_profile(
        "Rectangular",
        {"Width": p["Width"], "Height": p["Height"]},
        center=start,
        direction=axis,
        profile_x_axis=context.get("profile_x_axis"),
    )
    t = max(float(p.get("Thickness", 0.8) or 0.0), 0.0)
    ins = max(float(p.get("InsulationThickness", 0.0) or 0.0), 0.0)

    b0 = api.extrude(air, axis, solid=True)
    b1 = api.extrude(api.offset_profile(air, t), axis, solid=True)
    casing = api.refine(api.cut(b1, b0)) if t > 1.0e-7 else None

    layers = {"casing": {"shape": casing}}
    if ins > 1.0e-7:
        b2 = api.extrude(api.offset_profile(air, t + ins), axis, solid=True)
        layers["insulation"] = {"shape": api.refine(api.cut(b2, b1))}

    # Preserve model-level rectangular flanges when the type exposes them.
    ft = float(p.get("FlangeThickness", 0.0) or 0.0)
    fh = float(p.get("FlangeHeight", 0.0) or 0.0)
    if casing is not None and ft > 1.0e-7 and fh > 1.0e-7:
        direction = api.unit(axis)
        start_port = {
            "position": start,
            "direction": direction * -1.0,
            "profile": "Rectangular",
            "section_params": {"Width": p["Width"] + 2 * t, "Height": p["Height"] + 2 * t},
            "profile_x_axis": context.get("profile_x_axis"),
        }
        end_port = api.copy_port(start_port, position=end, direction=direction)
        flanges = []
        if bool(p.get("ShowFlange1", True)):
            flanges.append(api.make_flange(start_port, direction, ft, fh))
        if bool(p.get("ShowFlange2", True)):
            flanges.append(api.make_flange(end_port, direction * -1.0, ft, fh))
        if flanges:
            layers["casing"]["shape"] = api.refine(api.fuse(casing, *flanges))

    return {"layers": layers}
