from operator import itemgetter

HVAC_PARTSCRIPT_API = 1


def generate(context):
    """
    A straight duct built from three concentric construction layers (see
    the type-def's own "construction" block): a perforated liner facing
    the airstream, an acoustic absorber fill, and an outer structural
    jacket. Demonstrates HVACLibraryAPI.build_concentric_layers() -- no
    special-casing needed in core/library_api.py beyond what circular_
    straight.py/build_elbow already used for a plain casing+insulation
    duct, just one more layer.
    """
    api, sp, ep, params = itemgetter("hvac_api", "start_point", "end_point", "params")(context)
    profile_x_axis = context.get("profile_x_axis")

    diameter = float(itemgetter("Diameter")(params))
    liner_thickness = float(params.get("LinerThickness", 1.0) or 1.0)
    absorber_thickness = float(params.get("AbsorberThickness", 25.0) or 25.0)
    jacket_thickness = float(params.get("Thickness", 0.8) or 0.8)

    start = api.vec(sp)
    end = api.vec(ep)
    axis = end - start
    length = axis.Length
    if length <= 1e-6:
        raise ValueError("Circular acoustic straight requires non-zero length")
    direction = api.unit(axis)

    # Diameter is the liner's own inner bore (the flow surface) -- every
    # layer's own offset is measured outward from that same nominal
    # section, matching HVACLibraryAPI.build_concentric_layers()'s own
    # "0 = nominal profile" convention.
    base_port = {
        "position": start,
        "direction": direction,
        "profile": "Circular",
        "section_params": {"Diameter": diameter},
        "profile_x_axis": profile_x_axis,
    }
    end_port = api.copy_port(base_port, position=end)

    liner_shape, absorber_shape, jacket_shape = api.build_concentric_layers(
        [base_port, end_port],
        [
            (0.0, liner_thickness),
            (liner_thickness, liner_thickness + absorber_thickness),
            (liner_thickness + absorber_thickness, liner_thickness + absorber_thickness + jacket_thickness),
        ],
    )

    return {
        "layers": {
            "liner": {"shape": liner_shape},
            "absorber": {"shape": absorber_shape},
            "jacket": {"shape": jacket_shape},
        }
    }
