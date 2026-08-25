import math

HVAC_PARTSCRIPT_API = 1


def _inset_rectangular_port(api, port, thickness):
    """
    Copy of `port` with its Width/Height shrunk by 2*thickness (uniformly, on
    every side) -- position/direction/profile are unchanged. Used to sweep
    the inner-bore wire alongside the outer-wall wire.
    """
    return api.inset_port_section(port, thickness)


def _make_flange(api, position, inward_direction, thickness, duct_width, duct_height, flange_height, profile_x_axis):
    port = {
        "position": position, "direction": inward_direction, "profile": "Rectangular",
        "section_params": {"Width": duct_width, "Height": duct_height},
        "profile_x_axis": profile_x_axis,
    }
    return api.make_flange(port, inward_direction, thickness, flange_height)


def generate(context):
    api = context["hvac_api"]
    ports = list(context.get("connected_ports", []) or [])
    params = dict(context.get("params", {}) or {})

    if len(ports) != 2:
        raise ValueError("Rectangular elbow requires exactly 2 connected ports")

    port0, port1 = ports[0], ports[1]
    if api.port_profile(port0) != "Rectangular" or api.port_profile(port1) != "Rectangular":
        raise ValueError("Rectangular elbow requires Rectangular ports on both sides")

    p0 = api.port_position(port0)
    p1 = api.port_position(port1)
    u0 = api.port_direction(port0)
    u1 = api.port_direction(port1)

    theta = api.angle_between(u0, u1)
    if theta <= 1e-6:
        raise ValueError("Elbow requires non-collinear directions")
    if abs(theta - math.pi) <= 1e-6:
        raise ValueError("Elbow cannot be built for opposite directions")

    w0, h0 = api.port_width(port0), api.port_height(port0)
    w1, h1 = api.port_width(port1), api.port_height(port1)

    thickness = float(params.get("Thickness", 0.8) or 0.8)
    flange_height = float(params.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(params.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", True))
    show_flange2 = bool(params.get("ShowFlange2", True))
    insulation_thickness = float(params.get("InsulationThickness", 0.0) or 0.0)

    # CenterlineRadius is a genuine design choice (fabrication radius), not
    # something dictated by the network -- kept as a plain user input, with
    # the same "too tight for the duct size" floor as the generic elbow.
    radius = float(params.get("CenterlineRadius", 0.0) or 0.0)
    size_hint = max(w0, h0, w1, h1, 1.0)
    if radius < size_hint / 2.0:
        radius = 0.6 * size_hint

    elbow = api.make_elbow(port0, port1, radius, thickness)
    sweep_port_0, sweep_port_1 = elbow["ports"]
    trim0, trim1 = elbow["trim_lengths"]

    # Hollow sheet-metal wall: sweep a second, uniformly-inset profile along
    # the *same* centerline arc and cut it from the outer sweep. Schematic
    # constant-cross-section-inset approximation, not a true constant-
    # thickness offset surface -- matches the fidelity used elsewhere in
    # this library (see generators/junctions.py:build_elbow).
    parts = [elbow["shape"]]

    # Flanges are extruded inward from each tangent plane, into the elbow's
    # own body (overlapping the wall), matching the straight-duct convention.
    # port_direction() points *away* from the junction, along the connected
    # segment (see JunctionPort in NetworkParser.py). The primitive's trimmed
    # ports sit further out along u0/u1 than the fitting's interior, so
    # "into the elbow" from each tangent plane is -u0 / -u1.
    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        profile_x0 = api.port_profile_x_axis(sweep_port_0)
        parts.append(_make_flange(
            api, api.port_position(sweep_port_0), u0 * -1.0,
            flange_thickness, w0, h0, flange_height, profile_x0,
        ))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        profile_x1 = api.port_profile_x_axis(sweep_port_1)
        parts.append(_make_flange(
            api, api.port_position(sweep_port_1), u1 * -1.0,
            flange_thickness, w1, h1, flange_height, profile_x1,
        ))

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    insulation_shape = None
    if insulation_thickness > 0.0:
        insulation_shape = api.build_concentric_layers(
            [sweep_port_0, sweep_port_1],
            [(0.0, insulation_thickness)],
            path=elbow["path"],
        )[0]

    return {
        "layers": {
            "casing": {"shape": shape},
            "insulation": {"shape": insulation_shape},
        },
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (port0, trim0),
                (port1, trim1),
            ]
        ),
    }
