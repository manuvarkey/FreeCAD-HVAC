"""SMACNA fitting recipes using clear-air boundary dimensions and geometry API v2."""

_EPS = 1.0e-7


def _props(context):
    return dict(context.get("params") or context.get("properties") or {})


def _size(api, port):
    profile = api.port_profile(port)
    if profile == "Circular":
        return max(float(api.port_diameter(port)), 1.0)
    return max(float(api.port_width(port)), float(api.port_height(port)), 1.0)


def _positive(value, fallback):
    value = float(value or 0.0)
    return value if value > _EPS else float(fallback)


def _trimmed(api, port, length):
    return api.copy_port(port, position=api.port_position(port) + api.port_direction(port) * float(length))


def _loft(api, ports, offset=0.0, ruled=True):
    return api.loft([api.profile_from_port(port, offset) for port in ports], solid=True, ruled=ruled)


def _layer_shapes(api, boundary_builder, thickness, insulation):
    t = max(float(thickness or 0.0), 0.0)
    ins = max(float(insulation or 0.0), 0.0)
    air = api.refine(boundary_builder(0.0))
    casing_outer = api.refine(boundary_builder(t))
    casing = api.refine(api.cut(casing_outer, air)) if t > _EPS else None
    insulation_shape = None
    insulation_outer = casing_outer
    if ins > _EPS:
        insulation_outer = api.refine(boundary_builder(t + ins))
        insulation_shape = api.refine(api.cut(insulation_outer, casing_outer))
    return air, casing_outer, insulation_outer, casing, insulation_shape


def _result_layers(casing, insulation):
    layers = {"casing": {"shape": casing}}
    if insulation is not None:
        layers["insulation"] = {"shape": insulation}
    return layers


def _flange(api, port, casing_thickness, flange_thickness, flange_height, inward):
    if flange_thickness <= _EPS or flange_height <= _EPS:
        return None
    casing_port = api.grow_port_section(port, casing_thickness)
    return api.make_flange(casing_port, inward, flange_thickness, flange_height)


def _add_flanges(api, casing, ports, casing_thickness, p):
    ft = float(p.get("FlangeThickness", 0.0) or 0.0)
    fh = float(p.get("FlangeHeight", 0.0) or 0.0)
    if ft <= _EPS or fh <= _EPS or casing is None:
        return casing
    flags = [p.get(f"ShowFlange{i + 1}", True) for i in range(len(ports))]
    flanges = []
    for i, port in enumerate(ports):
        if not bool(flags[i]):
            continue
        flange = _flange(api, port, casing_thickness, ft, fh, api.port_direction(port) * -1.0)
        if flange is not None:
            flanges.append(flange)
    return api.refine(api.fuse(casing, *flanges)) if flanges else casing


def _marker(context, diameter, trim=0.0):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    center = (
        sum((api.port_position(port) for port in ports), api.vec((0, 0, 0))) / len(ports)
        if ports
        else api.vec(context.get("position", (0, 0, 0)))
    )
    result = {"shape": api.make_sphere(center, diameter)}
    if ports and trim > 0:
        result["connection_lengths"] = api.build_trim_rec_from_port_lengths([(port, trim) for port in ports])
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
    p = _props(context)
    port = ports[0]
    length = _positive(p.get("Length"), 0.35 * _size(api, port))
    return {"shape": api.refine(api.extrude(api.profile_from_port(port), api.port_direction(port) * length, solid=True))}


def build_elbow(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    thickness = float(p.get("Thickness", 0.8) or 0.0)
    insulation = float(p.get("InsulationThickness", 0.0) or 0.0)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    radius = max(_positive(p.get("CenterlineRadius"), 0.6 * size), 0.5 * size)
    route = api.make_elbow_path(ports[0], ports[1], radius)

    def boundary(offset):
        return api.sweep(
            [api.profile_from_port(route["ports"][0], offset), api.profile_from_port(route["ports"][1], offset)],
            route["path"],
            solid=True,
        )

    _, _, _, casing, ins_shape = _layer_shapes(api, boundary, thickness, insulation)
    casing = _add_flanges(api, casing, route["ports"], thickness, p)
    return {
        "layers": _result_layers(casing, ins_shape),
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
    thickness = float(p.get("Thickness", 0.8) or 0.0)
    insulation = float(p.get("InsulationThickness", 0.0) or 0.0)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("Length", p.get("TransitionLength")), max(size, 100.0))
    trim = total / 2.0
    ends = [_trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)]

    def boundary(offset):
        return _loft(api, ends, offset, ruled=True)

    _, _, _, casing, ins_shape = _layer_shapes(api, boundary, thickness, insulation)
    casing = _add_flanges(api, casing, ends, thickness, p)
    return {
        "layers": _result_layers(casing, ins_shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)]),
    }


def _inline(context, factor, minimum):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    length = _positive(p.get("BodyLength", p.get("DeviceLength", p.get("Length"))), max(minimum, factor * size))
    trim = length / 2.0
    ends = [_trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)]
    # Inline devices remain monolithic component geometry.  Nominal port
    # dimensions are nevertheless clear-air dimensions.
    return {
        "shape": api.refine(_loft(api, ends, 0.0, ruled=True)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)]),
    }


def build_damper_generic(context):
    return _inline(context, 0.5, 100.0)


def build_vav_generic(context):
    return _inline(context, 1.0, 300.0)


def _star_layered(context, factor):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) < 3:
        raise ValueError("Branch fitting requires at least three connected ports")
    p = _props(context)
    thickness = float(p.get("Thickness", 0.8) or 0.0)
    insulation = float(p.get("InsulationThickness", 0.0) or 0.0)
    center = sum((api.port_position(port) for port in ports), api.vec((0, 0, 0))) / len(ports)
    trim = _positive(p.get("JunctionLength", p.get("TrimLength")), factor * max(_size(api, port) for port in ports))
    ends = [_trimmed(api, port, trim) for port in ports]

    def boundary(offset):
        legs = []
        for end in ends:
            center_port = api.copy_port(end, position=center, direction=api.port_direction(end) * -1.0)
            legs.append(_loft(api, [end, center_port], offset, ruled=True))
        return api.fuse(*legs)

    _, _, _, casing, ins_shape = _layer_shapes(api, boundary, thickness, insulation)
    casing = _add_flanges(api, casing, ends, thickness, p)
    return {
        "layers": _result_layers(casing, ins_shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(port, trim) for port in ports]),
    }


def build_tee(context):
    return _star_layered(context, 0.60)


def build_wye(context):
    return _star_layered(context, 0.70)


def build_cross(context):
    return _star_layered(context, 0.60)


def build_manifold(context):
    return _star_layered(context, 0.65)


def build_through_generic(context):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) == 2:
        u0, u1 = api.port_direction(ports[0]), api.port_direction(ports[1])
        dot = max(-1.0, min(1.0, float(u0.dot(u1))))
        if dot > -0.985:
            return build_elbow(context)
        if api.port_profile(ports[0]) != api.port_profile(ports[1]) or api.port_section_params(ports[0]) != api.port_section_params(ports[1]):
            return build_transition(context)
        return _inline(context, 0.35, 80.0)
    return _marker(context, 160.0)
