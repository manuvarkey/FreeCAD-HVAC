from operator import itemgetter

HVAC_PARTSCRIPT_API = 1

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
    casing_shape = api.make_hollow_straight(
        start, end, "Circular", {"Diameter": diameter}, thickness,
        context.get("profile_x_axis"),
    )

    # Insulation, when enabled, is a second tube wrapped around the outside
    # of the casing -- same span, from the casing's own outer diameter out
    # to outer diameter + 2*InsulationThickness.
    insulation_shape = None
    if insulation_thickness > 0.0:
        insulation_shape = api.make_hollow_straight(
            start, end, "Circular", {"Diameter": diameter + 2.0 * insulation_thickness},
            insulation_thickness, context.get("profile_x_axis")
        )

    return {
        "layers": {
            "casing": {"shape": casing_shape},
            "insulation": {"shape": insulation_shape},
        }
    }
