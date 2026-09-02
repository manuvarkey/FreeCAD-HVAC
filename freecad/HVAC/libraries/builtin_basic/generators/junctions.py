"""Common fitting recipes built only from HVACLibraryAPI geometry primitives."""

_EPS = 1.0e-7


def _props(context):
    return dict(context.get("params") or context.get("properties") or {})


def _size(api, port):
    profile = api.port_profile(port)
    if profile == "Circular":
        return max(float(api.port_diameter(port)), 1.0)
    if profile in {"Rectangular", "Oval"}:
        return max(float(api.port_width(port)), float(api.port_height(port)), 1.0)
    params = api.port_section_params(port)
    return max([abs(float(v or 0.0)) for v in params.values() if isinstance(v, (int, float))] or [1.0])


def _positive(value, fallback):
    value = float(value or 0.0)
    return value if value > _EPS else float(fallback)


def _trimmed(api, port, length):
    return api.copy_port(
        port,
        position=api.port_position(port) + api.port_direction(port) * float(length),
    )


def _loft(api, ports, offset=0.0, ruled=True):
    return api.loft([api.profile_from_port(p, offset) for p in ports], solid=True, ruled=ruled)


def _marker(context, diameter, trim=0.0):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if ports:
        center = sum((api.port_position(p) for p in ports), api.vec((0, 0, 0))) / len(ports)
    else:
        center = api.vec(context.get("position", (0, 0, 0)))
    result = {"shape": api.make_sphere(center, diameter)}
    if ports and trim > 0:
        result["connection_lengths"] = api.build_trim_rec_from_port_lengths([(p, trim) for p in ports])
    return result


def build_terminal_marker(context):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    props = _props(context)
    center = api.port_position(ports[0])

    diameter = float(props.get("MarkerDiameter", 200.0) or 200.0)
    if diameter <= 0.0:
        raise ValueError("Marker diameter must be > 0")

    port_direction = api.port_direction(ports[0])
    z_reference = api.vec((0, 0, 1))
    x_reference = api.vec((1, 0, 0))
    reference = z_reference if abs(port_direction.dot(z_reference)) < 0.95 else x_reference

    first_axis = port_direction.cross(reference)
    if api.is_zero(first_axis):
        first_axis = port_direction.cross(api.vec((0, 1, 0)))
    first_axis = api.unit(first_axis)
    second_axis = api.unit(port_direction.cross(first_axis))

    radius = diameter / 2.0
    first_line = api.make_line(center - first_axis * radius, center + first_axis * radius)
    second_line = api.make_line(center - second_axis * radius, center + second_axis * radius)

    return {
        "shape": api.compound([first_line, second_line]),
        "connection_lengths": api.build_trim_rec_from_context_uniform(context, 0.0),
    }


def build_transition_marker(context):
    return _marker(context, 160.0, 60.0)


def build_elbow_marker(context):
    return _marker(context, 180.0, 70.0)


def build_tee_marker(context):
    return _marker(context, 200.0, 80.0)


def build_wye_marker(context):
    return _marker(context, 200.0, 80.0)


def build_cross_marker(context):
    return _marker(context, 220.0, 90.0)


def build_manifold_marker(context):
    return _marker(context, 240.0, 90.0)


def build_diffuser_generic(context):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if not ports:
        return _marker(context, 150.0)
    p = ports[0]
    length = _positive(_props(context).get("Length"), 0.35 * _size(api, p))
    shape = api.extrude(api.profile_from_port(p), api.port_direction(p) * length, solid=True)
    return {"shape": api.refine(shape)}


def build_elbow(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    radius = _positive(p.get("CenterlineRadius"), 0.6 * size)
    radius = max(radius, 0.5 * size)
    route = api.make_elbow_path(ports[0], ports[1], radius)
    shape = api.sweep(
        [api.profile_from_port(route["ports"][0]), api.profile_from_port(route["ports"][1])],
        route["path"],
        solid=True,
    )
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
        ),
    }


def build_transition(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("Length", p.get("TransitionLength")), max(size, 100.0))
    trim = total / 2.0
    a, b = _trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)
    return {
        "shape": api.refine(_loft(api, [a, b], 0.0, ruled=True)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)]),
    }


def build_transition_radiussed(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("Length", p.get("TransitionLength")), max(size, 100.0))
    radius = _positive(p.get("Length", p.get("TransitionRadius")), max(size, 10.0))
    route = api.make_radiussed_path(ports[0], ports[1], total, radius)
    shape = api.sweep(
        [api.profile_from_port(route["ports"][0]), api.profile_from_port(route["ports"][1])],
        route["path"],
        solid=True,
    )
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
        ),
    }


def build_transition_mitered(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("Length", p.get("TransitionLength")), max(size, 100.0))

    # Step 1: work out the generated end points and the theoretical sharp
    # turn points shared with the radiussed transition -- same axis, only
    # the corner treatment differs (a flat mitre cut here, an arc there).
    axis = api.offset_transition_axis(ports[0], ports[1], total)
    d = axis["d"]
    s0, s1 = axis["s0"], axis["s1"]
    corner0, corner1 = axis["corner0"], axis["corner1"]
    diagonal, turn_angle = axis["diagonal"], axis["turn_angle"]

    end_a = api.copy_port(ports[0], position=s0)
    end_b = api.copy_port(ports[1], position=s1)

    if turn_angle <= 1e-6:
        # No actual lateral offset: an ordinary straight loft, there is no
        # corner to mitre.
        shape = _loft(api, [end_a, end_b], 0.0, ruled=True)
    else:
        # The mitre plane bisects the straight-run direction and the
        # diagonal, so a straight stub and the diagonal middle piece meet
        # flush -- both turn points share this same bisector as their
        # plane normal, only the plane's own origin differs.
        normal = api.unit(d + diagonal)

        # Step 2: extend each port's own profile from its end point toward
        # the transition's centre, then clip it back at its own turn point
        # with the mitre plane.
        reach = total / 2.0 + size
        stub_a = api.extrude(api.profile_from_port(end_a), d * reach, solid=True)
        stub_a = api.clip_plane(stub_a, (corner0, normal), side="negative")
        stub_b = api.extrude(api.profile_from_port(end_b), d * -reach, solid=True)
        stub_b = api.clip_plane(stub_b, (corner1, normal), side="positive")

        # Step 3: sweep between the stubs' own cut faces at the turn
        # points -- not a fresh, idealised profile wire, which sits on a
        # different plane than the mitre cut and would not line up with
        # it once clipped. Reading the real cut face back off each
        # already-trimmed stub guarantees the middle piece meets them
        # exactly, and needs no further clipping of its own.
        face_a = api.section_face(stub_a, (corner0, normal))
        face_b = api.section_face(stub_b, (corner1, normal))
        middle = api.loft([face_a.OuterWire, face_b.OuterWire], solid=True, ruled=True)

        shape = api.fuse(stub_a, stub_b, middle)

    trim0 = max(0.0, (s0 - api.port_position(ports[0])).dot(api.port_direction(ports[0])))
    trim1 = max(0.0, (s1 - api.port_position(ports[1])).dot(api.port_direction(ports[1])))
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim0), (ports[1], trim1)]),
    }


def _inline(context, factor, minimum):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    requested = p.get("BodyLength", p.get("DeviceLength", p.get("Length")))
    length = _positive(requested, max(minimum, factor * size))
    trim = length / 2.0
    a, b = _trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)
    return {
        "shape": api.refine(_loft(api, [a, b], 0.0, ruled=True)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)]),
    }


def build_damper_generic(context):
    return _inline(context, 0.5, 100.0)


def build_vav_generic(context):
    return _inline(context, 1.0, 300.0)


def _star_junction(context, default_factor=0.6):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) < 3:
        raise ValueError("Branch junction requires at least three connected ports")
    p = _props(context)
    center = sum((api.port_position(port) for port in ports), api.vec((0, 0, 0))) / len(ports)
    default_trim = max(_size(api, port) for port in ports) * default_factor
    trim = _positive(p.get("JunctionLength", p.get("TrimLength")), default_trim)
    trimmed = [_trimmed(api, port, trim) for port in ports]
    legs = []
    for port in trimmed:
        center_port = api.copy_port(
            port,
            position=center,
            direction=api.port_direction(port) * -1.0,
        )
        legs.append(_loft(api, [port, center_port], 0.0, ruled=True))
    return {
        "shape": api.refine(api.fuse(*legs)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(port, trim) for port in ports]),
    }


def build_tee(context):
    return _star_junction(context, 0.60)


def build_wye(context):
    return _star_junction(context, 0.70)


def build_cross(context):
    return _star_junction(context, 0.60)


def build_manifold(context):
    return _star_junction(context, 0.65)


def build_through_generic(context):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) == 2:
        u0 = api.port_direction(ports[0])
        u1 = api.port_direction(ports[1])
        dot = max(-1.0, min(1.0, float(u0.dot(u1))))
        if dot > -0.985:
            return build_elbow(context)
        s0, s1 = api.port_section_params(ports[0]), api.port_section_params(ports[1])
        if api.port_profile(ports[0]) != api.port_profile(ports[1]) or s0 != s1:
            return build_transition(context)
        return _inline(context, 0.35, 80.0)
    return _marker(context, 160.0)
