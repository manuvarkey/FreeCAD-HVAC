import math

import Part

HVAC_PARTSCRIPT_API = 1


def _inset_rectangular_port(api, port, thickness):
    """
    Copy of `port` with its Width/Height shrunk by 2*thickness (uniformly, on
    every side) -- position/direction/profile are unchanged. Used to sweep
    the inner-bore wire alongside the outer-wall wire.
    """
    width = api.port_width(port) - 2.0 * thickness
    height = api.port_height(port) - 2.0 * thickness
    if width <= 0.0 or height <= 0.0:
        raise ValueError("Thickness is too large for port Width/Height")

    out = api.copy_port(port)
    params = dict(api.port_section_params(port))
    params["Width"] = width
    params["Height"] = height
    out["section_params"] = params
    return out


def _make_flange(api, position, inward_direction, thickness, duct_width, duct_height, flange_height, profile_x_axis):
    outer_face = api.make_section_face(
        profile="Rectangular",
        section_params={
            "Width": duct_width + 2.0 * flange_height,
            "Height": duct_height + 2.0 * flange_height,
        },
        center=position,
        direction=inward_direction,
        profile_x_axis=profile_x_axis,
    )
    inner_face = api.make_section_face(
        profile="Rectangular",
        section_params={"Width": duct_width, "Height": duct_height},
        center=position,
        direction=inward_direction,
        profile_x_axis=profile_x_axis,
    )
    extrusion = api.unit(inward_direction) * thickness
    return outer_face.extrude(extrusion).cut(inner_face.extrude(extrusion))


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

    thickness = float(params.get("thickness", 0.8) or 0.8)
    flange_height = float(params.get("flange_height", 25.0) or 25.0)
    flange_thickness = float(params.get("flange_thickness", 1.0) or 1.0)
    show_flange1 = bool(params.get("ShowFlange1", True))
    show_flange2 = bool(params.get("ShowFlange2", True))

    # r_axis is a genuine design choice (fabrication radius), not something
    # dictated by the network -- kept as a plain user input, with the same
    # "too tight for the duct size" floor as the generic elbow.
    radius = float(params.get("r_axis", 0.0) or 0.0)
    size_hint = max(w0, h0, w1, h1, 1.0)
    if radius < size_hint / 2.0:
        radius = 0.6 * size_hint

    # Symmetric tangent trim distance from the virtual corner (same
    # derivation as generators/junctions.py:build_elbow).
    trim = radius / math.tan(theta / 2.0)
    c1, c2 = api.closest_points_on_lines(p0, u0 * -1.0, p1, u1 * -1.0)

    s0 = c1 + (u0 * trim)
    s1 = c2 + (u1 * trim)

    trim0 = max(0.0, (s0 - p0).dot(u0))
    trim1 = max(0.0, (s1 - p1).dot(u1))

    arc_center = api.arc_center_from_points_tangents_radius(s0, s1, u0, u1, radius)
    bisector = u0 + u1
    if bisector.Length <= 1e-12:
        raise ValueError("Elbow bisector is undefined")
    bisector.normalize()
    mid_point = arc_center - bisector * float(radius)

    arc_edge = Part.Arc(s0, mid_point, s1).toShape()
    path_wire = Part.Wire([arc_edge])

    sweep_port_0 = api.copy_port(port0, position=s0)
    sweep_port_1 = api.copy_port(port1, position=s1)

    outer_wire_1 = api.make_section_wire_from_port(sweep_port_0)
    outer_wire_2 = api.make_section_wire_from_port(sweep_port_1)
    outer_shape = api.make_pipe_shell(path_wire, [outer_wire_1, outer_wire_2])

    # Hollow sheet-metal wall: sweep a second, uniformly-inset profile along
    # the *same* centerline arc and cut it from the outer sweep. Schematic
    # constant-cross-section-inset approximation, not a true constant-
    # thickness offset surface -- matches the fidelity used elsewhere in
    # this library (see generators/junctions.py:build_elbow).
    inner_sweep_port_0 = _inset_rectangular_port(api, sweep_port_0, thickness)
    inner_sweep_port_1 = _inset_rectangular_port(api, sweep_port_1, thickness)
    inner_wire_1 = api.make_section_wire_from_port(inner_sweep_port_0)
    inner_wire_2 = api.make_section_wire_from_port(inner_sweep_port_1)
    inner_shape = api.make_pipe_shell(path_wire, [inner_wire_1, inner_wire_2])

    parts = [outer_shape.cut(inner_shape)]

    # Flanges are extruded inward from each tangent plane, into the elbow's
    # own body (overlapping the wall), matching the straight-duct convention.
    # port_direction() points *away* from the junction, along the connected
    # segment (see JunctionPort in NetworkParser.py) -- s0/s1 sit further out
    # along u0/u1 than the fitting's interior, so "into the elbow" from each
    # tangent plane is -u0 / -u1, not +u0/+u1.
    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        profile_x0 = api.port_profile_x_axis(sweep_port_0)
        parts.append(_make_flange(api, s0, u0 * -1.0, flange_thickness, w0, h0, flange_height, profile_x0))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        profile_x1 = api.port_profile_x_axis(sweep_port_1)
        parts.append(_make_flange(api, s1, u1 * -1.0, flange_thickness, w1, h1, flange_height, profile_x1))

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    # Reactive "as-built" parameters: the bend angle and axis offset are
    # dictated entirely by what's actually connected, not an independent
    # user choice, so they're reported back as read-only display properties
    # rather than read as an input (see through_elbow_rectangular.json's
    # editor_mode=1 fields).
    offset_vec = c2 - c1
    _, x_axis0, y_axis0, _ = api.make_profile_frame(u0, api.port_profile_x_axis(port0))
    computed_properties = {
        "d_h_axis_02": offset_vec.dot(x_axis0),
        "d_v_axis_02": offset_vec.dot(y_axis0),
        "angle": math.degrees(math.pi - theta),
    }

    return {
        "shape": shape,
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (port0, trim0),
                (port1, trim1),
            ]
        ),
        "computed_properties": computed_properties,
    }
