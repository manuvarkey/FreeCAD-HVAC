"""Builtin segment generators using HVACLibraryAPI geometry v2."""


def _properties(context):
    return dict(context.get("params") or context.get("properties") or {})


def _build(context, profile, section):
    api = context["hvac_api"]
    start = api.vec(context["start_point"])
    end = api.vec(context["end_point"])
    path = context.get("path_edge")
    direction = context.get("start_direction") or (end - start)
    x_axis = context.get("profile_x_axis")

    p0 = api.make_profile(
        profile,
        section,
        center=start,
        direction=direction,
        profile_x_axis=x_axis,
    )
    shape = api.sweep(p0, path, solid=True) if path is not None else api.extrude(p0, end - start, solid=True)
    return {"shape": api.refine(shape)}


def build_rectangular_straight(context):
    p = _properties(context)
    return _build(context, "Rectangular", {"Width": p["Width"], "Height": p["Height"]})


def build_circular_straight(context):
    p = _properties(context)
    return _build(context, "Circular", {"Diameter": p["Diameter"]})


def build_oval_straight(context):
    p = _properties(context)
    return _build(context, "Oval", {"Width": p["Width"], "Height": p["Height"]})


def build_rectangular_curved(context):
    return build_rectangular_straight(context)


def build_circular_curved(context):
    return build_circular_straight(context)


def build_oval_curved(context):
    return build_oval_straight(context)
