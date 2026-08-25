from operator import itemgetter

import Part

HVAC_PARTSCRIPT_API = 1


def _make_tube(position, direction, length, outer_diameter, inner_diameter):
    outer = Part.makeCylinder(outer_diameter * 0.5, length, position, direction)
    if inner_diameter <= 0.0:
        return outer
    inner = Part.makeCylinder(inner_diameter * 0.5, length, position, direction)
    return outer.cut(inner)


def generate(context):
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)

    diameter = float(itemgetter("Diameter")(params))
    thickness = float(params.get("Thickness", 0.8) or 0.8)
    insulation_thickness = float(params.get("InsulationThickness", 0.0) or 0.0)

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

    # The transverse-joint flange collars are a separate construction
    # feature now (see smacna/generators/features.py::generate_transverse_flange
    # and this type-def's own "construction.features" block) -- casing is
    # just the bare tube.
    casing_shape = _make_tube(start, direction, length, diameter, inner_diameter)

    # Insulation, when enabled, is a second tube wrapped around the outside
    # of the casing -- same span, from the casing's own outer diameter out
    # to outer diameter + 2*InsulationThickness.
    insulation_shape = None
    if insulation_thickness > 0.0:
        insulation_shape = _make_tube(
            start, direction, length, diameter + 2.0 * insulation_thickness, diameter
        )

    return {
        "layers": {
            "casing": {"shape": casing_shape},
            "insulation": {"shape": insulation_shape},
        }
    }
