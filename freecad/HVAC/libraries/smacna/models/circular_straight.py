from operator import itemgetter

import Part

HVAC_PARTSCRIPT_API = 1


def _make_tube(position, direction, length, outer_diameter, inner_diameter):
    outer = Part.makeCylinder(outer_diameter * 0.5, length, position, direction)
    if inner_diameter <= 0.0:
        return outer
    inner = Part.makeCylinder(inner_diameter * 0.5, length, position, direction)
    return outer.cut(inner)


def _make_flange(position, inward_direction, thickness, duct_outer_diameter, flange_height):
    outer = Part.makeCylinder(
        duct_outer_diameter * 0.5 + flange_height, thickness, position, inward_direction
    )
    inner = Part.makeCylinder(duct_outer_diameter * 0.5, thickness, position, inward_direction)
    return outer.cut(inner)


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)

    diameter = float(itemgetter("Diameter")(params))
    thickness = float(params.get("Thickness", 0.8) or 0.8)
    flange_height = float(params.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(params.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", True))
    show_flange2 = bool(params.get("ShowFlange2", True))

    start = api.vec(sp)
    end = api.vec(ep)
    axis = end - start
    length = axis.Length
    if length <= 1e-6:
        raise ValueError("Circular straight (PartScript) requires non-zero length")
    direction = api.unit(axis)

    inner_diameter = diameter - 2.0 * thickness
    if inner_diameter <= 0.0:
        raise ValueError("Circular straight Thickness is too large for Diameter")

    parts = [_make_tube(start, direction, length, diameter, inner_diameter)]

    # Flanges are extruded inward from each port plane, into the duct's own
    # length (overlapping the tube's wall), rather than protruding past the
    # port into the neighboring segment/junction's space.
    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(start, direction, flange_thickness, diameter, flange_height))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(end, direction * -1.0, flange_thickness, diameter, flange_height))

    shape = api.fuse_shapes(parts)
    return {"shape": shape}
