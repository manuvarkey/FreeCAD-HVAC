"""SMACNA construction-feature generators.

All section dimensions supplied by the library are clear-air dimensions.  A
feature attached to the sheet-metal casing therefore grows the port by the
casing thickness before creating its geometry.
"""


def _full_parameters(ctx):
    full = dict(ctx.context.get("params") or ctx.context.get("properties") or {})
    full.update(dict(ctx.parameters or {}))
    return full


def generate_transverse_flange(api, ctx):
    p = _full_parameters(ctx)
    thickness = max(float(p.get("Thickness", 0.0) or 0.0), 0.0)
    flange_t = max(float(p.get("FlangeThickness", 0.0) or 0.0), 0.0)
    flange_h = max(float(p.get("FlangeHeight", 0.0) or 0.0), 0.0)
    if flange_t <= 1.0e-7 or flange_h <= 1.0e-7:
        return None

    start = api.vec(ctx.context["start_point"])
    end = api.vec(ctx.context["end_point"])
    axis = api.unit(end - start)
    profile = str(p.get("Profile", "Circular"))
    if profile == "Circular":
        section = {"Diameter": float(p["Diameter"]) + 2.0 * thickness}
    else:
        section = {
            "Width": float(p["Width"]) + 2.0 * thickness,
            "Height": float(p["Height"]) + 2.0 * thickness,
        }

    port0 = {
        "position": start,
        "direction": axis * -1.0,
        "profile": profile,
        "section_params": section,
        "profile_x_axis": ctx.context.get("profile_x_axis"),
    }
    port1 = api.copy_port(port0, position=end, direction=axis)
    shapes = []
    if bool(p.get("ShowFlange1", True)):
        shapes.append(api.make_flange(port0, axis, flange_t, flange_h))
    if bool(p.get("ShowFlange2", True)):
        shapes.append(api.make_flange(port1, axis * -1.0, flange_t, flange_h))
    if not shapes:
        return None
    return api.refine(api.fuse(*shapes))
