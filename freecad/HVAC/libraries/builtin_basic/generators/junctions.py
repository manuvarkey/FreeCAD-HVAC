"""Common fitting recipes built only from HVACLibraryAPI geometry primitives.

Most fittings below are split into a small measurement function
(``measure_*``) and a build function (``build_*``/an internal ``_*``
builder), sharing whichever calculation actually determines a port's own
trim length -- a route/axis dict from an HVACLibraryAPI helper
(``make_elbow_path``/``make_radiussed_path``/``offset_transition_axis``), a
plain arithmetic result, or (for a radiused/mitered branch fitting whose
minimum body length genuinely depends on a swept bend's own extent) a
lightweight intermediate bend-stub Shape -- so there is exactly one place
each fitting's own dimensions/trims are computed, never two independently
maintained copies of the same formula. See freecad/HVAC/libraries/README.md's
"connection_lengths" section for the overall contract this supports
(HVACLibraryRegistry.measure_connection_lengths()).
"""

import math

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


def _port_axis_point(api, port, point):
    """Project a point onto the infinite axis defined by a port."""
    position = api.port_position(port)
    direction = api.unit(api.port_direction(port))
    return position + direction * ((api.vec(point) - position).dot(direction))


def _clip_branch_at_trunk_center(api, shape, trunk_center, branch):
    """Keep the branch-facing half of a stub at the trunk center plane."""
    return api.clip_plane(
        shape,
        (trunk_center, api.port_direction(branch)),
        side='positive',
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


def _terminal_body_layout(context):
    """
    Shared layout for a generic one-port terminal body (diffuser/grille/
    register, AHU/fan connection, intake/exhaust louver, ...): a plain
    extrusion of the connected port's own profile -- the interface profile
    is always the real connected duct port, never an independent neck
    size (a NeckSize a type-def may still declare feeds that type's own
    loss formula only, referenced to velocity at an equivalent neck --
    see HVACLossAPI.terminal_component_loss -- it is not a second,
    geometrically distinct profile applied here).
    """
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 1:
        raise ValueError("Generic terminal requires exactly one connected port")
    port = ports[0]
    props = _props(context)
    body_length = _positive(props.get("BodyLength"), 0.35 * _size(api, port))
    return port, body_length


def measure_terminal_body_generic(context):
    api = context["hvac_api"]
    port, body_length = _terminal_body_layout(context)
    return api.build_trim_rec_from_port_lengths([(port, body_length)])


def build_terminal_body_generic(context):
    api = context["hvac_api"]
    port, body_length = _terminal_body_layout(context)
    shape = api.extrude(api.profile_from_port(port), api.port_direction(port) * body_length, solid=True)
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(port, body_length)]),
    }


def _duct_closure_thickness(context):
    return _positive(_props(context).get("PlateThickness"), 3.0)


def measure_duct_closure_generic(context):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if not ports:
        return []
    return api.build_trim_rec_from_context_uniform(context, _duct_closure_thickness(context))


def build_duct_closure_generic(context):
    """A thin blanking plate/cap sealing off a single terminal port."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if not ports:
        return _marker(context, 150.0)
    p = ports[0]
    thickness = _duct_closure_thickness(context)
    shape = api.extrude(api.profile_from_port(p), api.port_direction(p) * thickness, solid=True)
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_context_uniform(context, thickness),
    }


def _elbow_route(context):
    """Shared layout for a radiused elbow: the two connected ports and the
    tangent-arc route (HVACLibraryAPI.make_elbow_path) between them -- the
    one calculation that determines both the swept shape's path AND each
    port's own trim length, so measure_elbow()/build_elbow() can never
    disagree."""
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    radius = _positive(p.get("CenterlineRadius"), 0.6 * size)
    radius = max(radius, 0.5 * size)
    route = api.make_elbow_path(ports[0], ports[1], radius)
    return ports, route


def measure_elbow(context):
    api = context["hvac_api"]
    ports, route = _elbow_route(context)
    return api.build_trim_rec_from_port_lengths(
        [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
    )


def build_elbow(context):
    api = context["hvac_api"]
    ports, route = _elbow_route(context)
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


def _profile_extent(profile):
    shape = profile.wire if hasattr(profile, "wire") else profile
    bb = shape.BoundBox
    return max(bb.DiagonalLength, bb.XLength, bb.YLength, bb.ZLength, 1.0)


def _mitered_bend(api, port0, port1, radius, cuts, profile0=None, profile1=None):
    """Build a faceted bend between two arbitrary ports.
    
    Used by through mitered elbows, the mitered-shoe tee, and mitered wye.
    The tangent-arc route defines the fitting-end positions and the reference
    radius; the actual body between those ends is constructed from straight
    gores separated by mitre planes.
    
    ``profile0``/``profile1`` optionally replace the exact tangent-end
    sections, allowing a split wye profile to transition through the gores.
    Returns ``(shape, [trim0, trim1])``.
    """
    u0 = api.port_direction(port0)
    u1 = api.port_direction(port1)

    # Establish the tangent ends of the fitting.
    route = api.make_elbow_path(port0, port1, radius)
    route_port0, route_port1 = route["ports"]
    s0 = api.port_position(route_port0)
    s1 = api.port_position(route_port1)
    d0 = api.unit(u0 * -1.0)
    d1 = api.unit(u1)
    deflection = api.angle_between(d0, d1)
    if deflection <= api.EPS:
        raise ValueError("Mitered bend requires a non-zero bend angle")
    bend_cross = d0.cross(d1)
    if bend_cross.Length <= api.EPS:
        raise ValueError("Mitered bend plane is undefined")
    bend_normal = api.unit(bend_cross)
    center = api.arc_center_from_points_tangents_radius(s0, s1, u0, u1, radius)

    # Straight gore directions.
    # NumberOfCuts is the number of internal mitre joints.
    # Therefore there are cuts + 1 gores.
    segment_angle = deflection / float(cuts)
    cos_half = math.cos(segment_angle / 2.0)
    if abs(cos_half) <= api.EPS:
        raise ValueError("Mitered bend segment angle is degenerate")
    leg_dirs = [api.unit(api.rotate_vector(d0, bend_normal, i * segment_angle)) for i in range(cuts + 1)]

    # Internal mitre planes.
    radius_vec0 = api.unit(s0 - center)
    joint_radius = radius / cos_half
    joint_positions = []
    joint_planes = []
    for i in range(cuts):
        angle = (i + 0.5) * segment_angle
        position = center + api.rotate_vector(radius_vec0, bend_normal, angle) * joint_radius
        normal = api.unit(leg_dirs[i] + leg_dirs[i + 1])
        joint_positions.append(position)
        joint_planes.append((position, normal))

    # Determine which clipping half-space contains a known point.
    def clip_keep_point(shape, plane, point):
        plane_origin, plane_normal = plane
        value = (api.vec(point) - api.vec(plane_origin)).dot(api.unit(plane_normal))
        side = "positive" if value >= 0.0 else "negative"
        return api.clip_plane(shape, plane, side=side)

    # Calculate the extrusion distance required to pass fully through
    # an inclined mitre plane.
    def reach_to_plane(origin, direction, plane, section):
        plane_origin, plane_normal = plane
        direction = api.unit(direction)
        plane_normal = api.unit(plane_normal)
        denom = direction.dot(plane_normal)
        if abs(denom) <= api.EPS:
            raise ValueError("Mitered bend geometry is degenerate: gore direction is parallel to mitre plane")
        reach = (plane_origin - origin).dot(plane_normal) / denom
        if reach <= api.EPS:
            raise ValueError("Mitered bend has zero or negative gore length")
        extent = _profile_extent(section)
        margin = 2.0 * extent / max(abs(denom), 0.1)
        return reach + margin

    # Build one constant-profile gore from an existing exact section
    # toward a target mitre plane.
    def extrude_to_plane(section, axis_origin, direction, target_plane):
        reach = reach_to_plane(axis_origin, direction, target_plane, section)
        solid = api.extrude(section, direction * reach, solid=True)
        solid = clip_keep_point(solid, target_plane, axis_origin)
        if solid is None or solid.isNull():
            raise RuntimeError("Mitered bend gore became null after clipping")
        cut_face = api.section_face(solid, target_plane)
        return solid, cut_face.OuterWire

    # Actual fitting-end profiles. These may be geometrically different.
    if profile0 is None:
        profile0 = api.profile_from_port(route_port0)
    if profile1 is None:
        profile1 = api.profile_from_port(route_port1)

    # Use one central gore as the finite transition between the exact
    # profiles propagated from each end.
    transition_gore = cuts // 2
    pieces = []

    # PORT 0 -> FORWARD
    left_section = profile0
    left_axis_point = s0
    for i in range(transition_gore):
        solid, next_section = extrude_to_plane(left_section, left_axis_point, leg_dirs[i], joint_planes[i])
        pieces.append(solid)
        left_section = next_section
        left_axis_point = joint_positions[i]

    # PORT 1 -> BACKWARD
    right_section = profile1
    right_axis_point = s1
    right_pieces = []
    for i in range(cuts, transition_gore, -1):
        target_plane = joint_planes[i - 1]
        backward_direction = leg_dirs[i] * -1.0
        solid, next_section = extrude_to_plane(right_section, right_axis_point, backward_direction, target_plane)
        right_pieces.append(solid)
        right_section = next_section
        right_axis_point = joint_positions[i - 1]

    # The transition gore is a finite loft between exact section wires
    # propagated from port0 and port1.
    transition = api.loft([left_section, right_section], solid=True, ruled=True)
    if transition is None or transition.isNull():
        raise RuntimeError("Mitered bend transition gore loft failed")
    pieces.append(transition)
    pieces.extend(right_pieces)
    shape = api.fuse(*pieces)
    return shape, [route["trim_lengths"][0], route["trim_lengths"][1]]


def _elbow_mitered_radius(context, ports):
    api = context["hvac_api"]
    size = max(_profile_extent(api.profile_from_port(ports[0])), _profile_extent(api.profile_from_port(ports[1])))
    props = _props(context)
    radius = _positive(props.get("CenterlineRadius"), 0.6 * size)
    return max(radius, 0.5 * size)


def measure_elbow_mitered(context):
    """The mitred bend's own trims come from the same tangent-arc route
    (api.make_elbow_path) _mitered_bend() uses internally, unaffected by
    NumberOfCuts -- so this can read them directly without paying for
    _mitered_bend()'s own gore/mitre shape construction at all."""
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    radius = _elbow_mitered_radius(context, ports)
    route = api.make_elbow_path(ports[0], ports[1], radius)
    return api.build_trim_rec_from_port_lengths(
        [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
    )


def build_elbow_mitered(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    port0, port1 = ports
    radius = _elbow_mitered_radius(context, ports)
    props = _props(context)
    cuts = max(int(props.get("NumberOfCuts", 1) or 1), 1)
    shape, trims = _mitered_bend(api, port0, port1, radius, cuts)
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(port0, trims[0]), (port1, trims[1])]),
    }


def _transition_layout(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("TransitionLength"), max(size, 100.0))
    trim = total / 2.0
    return ports, trim


def measure_transition(context):
    api = context["hvac_api"]
    ports, trim = _transition_layout(context)
    return api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)])


def build_transition(context):
    api = context["hvac_api"]
    ports, trim = _transition_layout(context)
    a, b = _trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)
    return {
        "shape": api.refine(_loft(api, [a, b], 0.0, ruled=True)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)]),
    }


def _transition_radiussed_route(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("TransitionLength"), max(size, 100.0))
    radius = _positive(p.get("TransitionRadius"), max(size, 10.0))
    # A bend radius smaller than the duct's own half-width folds the swept
    # surface back on itself on the inside of the bend (same minimum
    # build_elbow enforces on CenterlineRadius).
    radius = max(radius, 0.5 * size)
    route = api.make_radiussed_path(ports[0], ports[1], total, radius)
    return ports, route


def measure_transition_radiussed(context):
    api = context["hvac_api"]
    ports, route = _transition_radiussed_route(context)
    return api.build_trim_rec_from_port_lengths(
        [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
    )


def build_transition_radiussed(context):
    api = context["hvac_api"]
    ports, route = _transition_radiussed_route(context)
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


def _transition_mitered_layout(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("TransitionLength"), max(size, 100.0))

    # Shared axis geometry with the radiussed transition -- same end
    # points and theoretical sharp turn points, only the corner treatment
    # differs (a flat mitre cut here, an arc there).
    axis = api.offset_transition_axis(ports[0], ports[1], total)
    trim0 = max(0.0, (axis["s0"] - api.port_position(ports[0])).dot(api.port_direction(ports[0])))
    trim1 = max(0.0, (axis["s1"] - api.port_position(ports[1])).dot(api.port_direction(ports[1])))
    return ports, size, total, axis, trim0, trim1


def measure_transition_mitered(context):
    api = context["hvac_api"]
    ports, size, total, axis, trim0, trim1 = _transition_mitered_layout(context)
    return api.build_trim_rec_from_port_lengths([(ports[0], trim0), (ports[1], trim1)])


def build_transition_mitered(context):
    api = context["hvac_api"]
    ports, size, total, axis, trim0, trim1 = _transition_mitered_layout(context)
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

        # Extend each port's own profile from its end point toward the
        # transition's centre, then clip it back at its own turn point
        # with the mitre plane.
        reach = total / 2.0 + size
        stub_a = api.extrude(api.profile_from_port(end_a), d * reach, solid=True)
        stub_a = api.clip_plane(stub_a, (corner0, normal), side="negative")
        stub_b = api.extrude(api.profile_from_port(end_b), d * -reach, solid=True)
        stub_b = api.clip_plane(stub_b, (corner1, normal), side="positive")

        # Sweep between the stubs' own cut faces at the turn points -- not
        # a fresh, idealised profile wire, which sits on a different plane
        # than the mitre cut and would not line up with it once clipped.
        # Reading the real cut face back off each already-trimmed stub
        # guarantees the middle piece meets them exactly, and needs no
        # further clipping of its own.
        face_a = api.section_face(stub_a, (corner0, normal))
        face_b = api.section_face(stub_b, (corner1, normal))
        middle = api.loft([face_a.OuterWire, face_b.OuterWire], solid=True, ruled=True)

        shape = api.fuse(stub_a, stub_b, middle)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim0), (ports[1], trim1)]),
    }


def _inline_layout(context, factor, minimum):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    requested = p.get("BodyLength", p.get("DeviceLength", p.get("Length")))
    length = _positive(requested, max(minimum, factor * size))
    trim = length / 2.0
    return ports, trim


def measure_inline(context, factor, minimum):
    api = context["hvac_api"]
    ports, trim = _inline_layout(context, factor, minimum)
    return api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)])


def _inline(context, factor, minimum):
    api = context["hvac_api"]
    ports, trim = _inline_layout(context, factor, minimum)
    a, b = _trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)
    return {
        "shape": api.refine(_loft(api, [a, b], 0.0, ruled=True)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)]),
    }


def build_damper_generic(context):
    return _inline(context, 0.5, 100.0)


def measure_damper_generic(context):
    return measure_inline(context, 0.5, 100.0)


def build_vav_generic(context):
    return _inline(context, 1.0, 300.0)


def measure_vav_generic(context):
    return measure_inline(context, 1.0, 300.0)


def _extend_leg(api, shape, port, current_trim, requested_trim):
    """Fuse a plain straight extension onto a swept/mitred leg's outer end,
    so a user-requested trim length can grow past whatever minimum the
    bend geometry alone requires (``current_trim``) -- e.g. a tee/tap's
    branch leg, whose near end is pinned to the bend's own tangent point.
    A no-op (returns ``shape`` unchanged) when the request is already met.
    """
    extra = float(requested_trim) - float(current_trim)
    if extra <= _EPS:
        return shape
    end = _trimmed(api, port, current_trim)
    stub = api.extrude(api.profile_from_port(end), api.port_direction(port) * extra, solid=True)
    return api.fuse(shape, stub)


def _lean_port_from_profile_frame(api, run_a, run_b, branch):
    """Choose a physical run direction deterministically for a symmetric tee.

    The branch profile frame provides the local reference. Swapping run_a/run_b
    therefore does not mirror the fitting.
    """
    preferred_x = api.port_profile_x_axis(branch)

    if preferred_x is not None:
        _, x_axis, y_axis, _ = api.make_profile_frame(api.port_direction(branch), preferred_x)
        run_dir = api.unit(api.port_direction(run_a))

        local_x = run_dir.dot(x_axis)
        local_y = run_dir.dot(y_axis)

        if abs(local_x) >= abs(local_y):
            return run_a if local_x >= 0.0 else run_b
        return run_a if local_y >= 0.0 else run_b

    key_a = (str(run_a.get('edge_key', '') or ''), str(run_a.get('segment_end', '') or ''))
    key_b = (str(run_b.get('edge_key', '') or ''), str(run_b.get('segment_end', '') or ''))

    if key_a == ('', '') or key_b == ('', ''):
        raise ValueError('Symmetric tee bend direction requires profile_x_axis or stable run edge keys')

    return run_a if key_a > key_b else run_b


def _lean_port(api, run_a, run_b, branch, reverse=False):
    """Pick the run leg toward which an asymmetric branch bend is formed."""
    incoming = api.unit(api.port_direction(branch) * -1.0)
    dir_a = api.unit(api.port_direction(run_a))
    dir_b = api.unit(api.port_direction(run_b))

    score_a = incoming.dot(dir_a)
    score_b = incoming.dot(dir_b)

    if abs(score_a - score_b) > _EPS:
        selected = run_a if score_a > score_b else run_b
    else:
        selected = _lean_port_from_profile_frame(api, run_a, run_b, branch)

    if reverse:
        return run_b if selected is run_a else run_a

    return selected


def _star_junction_layout(context, default_factor):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) < 3:
        raise ValueError("Branch junction requires at least three connected ports")
    p = _props(context)
    center = sum((api.port_position(port) for port in ports), api.vec((0, 0, 0))) / len(ports)
    default_trim = max(_size(api, port) for port in ports) * default_factor
    trim = _positive(p.get("JunctionLength", p.get("TrimLength")), default_trim)
    return ports, center, trim


def measure_star_junction(context, default_factor=0.6):
    api = context["hvac_api"]
    ports, center, trim = _star_junction_layout(context, default_factor)
    return api.build_trim_rec_from_port_lengths([(port, trim) for port in ports])


def _star_junction(context, default_factor=0.6, align_branch=False):
    api = context["hvac_api"]
    ports, center, trim = _star_junction_layout(context, default_factor)
    branch = None
    if align_branch:
        pairs = api.collinear_port_index_pairs(context)
        if pairs:
            _, _, branch = api.run_branch_ports(context)
            center = api.center_from_context(context)
    trimmed = [_trimmed(api, port, trim) for port in ports]
    legs = []
    for source_port, port in zip(ports, trimmed):
        inner_position = _port_axis_point(api, source_port, center) if source_port is branch else center
        center_port = api.copy_port(
            port,
            position=inner_position,
            direction=api.port_direction(port) * -1.0,
        )
        leg = _loft(api, [port, center_port], 0.0, ruled=True)
        if source_port is branch:
            leg = _clip_branch_at_trunk_center(api, leg, center, branch)
        legs.append(leg)
    return {
        "shape": api.refine(api.fuse(*legs)),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(port, trim) for port in ports]),
    }


def measure_branch_generic(context):
    return measure_star_junction(context, 0.60)


def measure_cross(context):
    return measure_star_junction(context, 0.60)


def measure_manifold(context):
    return measure_star_junction(context, 0.65)


def _extra_trim(value, default):
    """Additional trim measured beyond the intrinsic fitting body."""
    if value is None:
        return max(float(default), 0.0)
    return max(float(value or 0.0), 0.0)


def _junction_minimums(api, center, ports):
    """Minimum visible body length along each port required to contain the
    other port profiles at the junction centre."""
    centered_profiles = [api.profile_from_port(api.copy_port(port, position=center)) for port in ports]
    minimums = []
    for port in ports:
        direction = api.unit(api.port_direction(port))
        center_projection = center.dot(direction)
        minimum = 0.0
        for profile in centered_profiles:
            _, p_max = api.profile_projection_bounds(profile, direction)
            minimum = max(minimum, p_max - center_projection)
        minimums.append(max(minimum, 1.0))
    return minimums


def _junction_shape_minimums(api, center, ports, shape, minimums=None):
    """Expand junction minimums to contain the actual generated intrinsic shape."""
    result = list(minimums or _junction_minimums(api, center, ports))
    for i, port in enumerate(ports):
        direction = api.unit(api.port_direction(port))
        _, p_max = api.profile_projection_bounds(shape, direction)
        result[i] = max(result[i], p_max - center.dot(direction))
    return result


def _junction_trims(api, p, ports, names, factors, minimums):
    """Total connection lengths = intrinsic fitting body + additional trims."""
    trims = []
    for port, name, factor, minimum in zip(ports, names, factors, minimums):
        extra = _extra_trim(p.get(name), factor * _size(api, port))
        trims.append(minimum + extra)
    return trims


def _clip_junction_to_body(api, shape, center, ports, trims):
    """Prevent overlap geometry from extending beyond connection planes."""
    for port, trim in zip(ports, trims):
        direction = api.unit(api.port_direction(port))
        plane_origin = api.port_position(port) + direction * trim
        side = "positive" if (center - plane_origin).dot(direction) >= 0.0 else "negative"
        shape = api.clip_plane(shape, (plane_origin, direction), side=side)
    return shape


def _star_body(api, center, ports, trims, minimums, branch=None):
    """Build robust straight-legged junction geometry with controlled overlap."""
    legs = []
    for port, trim, minimum in zip(ports, trims, minimums):
        direction = api.unit(api.port_direction(port))
        end = _trimmed(api, port, trim)
        embed = max(0.10 * minimum, 1.0)
        inner_center = _port_axis_point(api, port, center) if port is branch else center
        inner = api.copy_port(port, position=inner_center - direction * embed)
        leg = _loft(api, [end, inner], 0.0, ruled=True)
        if port is branch:
            leg = _clip_branch_at_trunk_center(api, leg, center, branch)
        legs.append(leg)
    shape = api.fuse(*legs)
    return _clip_junction_to_body(api, shape, center, ports, trims)


def _tap_geometry(context, api, run_a, run_b, branch):
    """Return common profile-independent geometry for tap fittings."""
    trunk_center = api.center_from_context(context)
    branch_dir = api.unit(api.port_direction(branch))
    lean = _lean_port(api, run_a, run_b, branch)
    lean_dir = api.unit(api.port_direction(lean))

    toe_dir = lean_dir - branch_dir * lean_dir.dot(branch_dir)
    if toe_dir.Length <= api.EPS:
        raise ValueError("Tap requires a non-degenerate branch/run angle")
    toe_dir = api.unit(toe_dir)

    branch_axis_center = _port_axis_point(api, branch, trunk_center)
    branch_center = api.copy_port(branch, position=branch_axis_center)
    branch_profile = api.profile_from_port(branch_center)
    branch_min, branch_max = api.profile_projection_bounds(branch_profile, toe_dir)
    branch_width = max(branch_max - branch_min, 1.0)

    center_projection = branch_axis_center.dot(branch_dir)
    near_projection = None
    far_projection = None
    for run_port in (run_a, run_b):
        centered_port = api.copy_port(run_port, position=trunk_center)
        run_profile = api.profile_from_port(centered_port)
        p_min, p_max = api.profile_projection_bounds(run_profile, branch_dir)
        near_projection = p_max if near_projection is None else max(near_projection, p_max)
        far_projection = p_min if far_projection is None else min(far_projection, p_min)

    run_depth = near_projection - far_projection
    if run_depth <= api.EPS:
        raise ValueError("Tap run profile has zero depth")

    run_surface_offset = near_projection - center_projection
    run_surface = branch_axis_center + branch_dir * run_surface_offset

    # Embed to the actual mid-depth of the run. This gives a substantial
    # boolean intersection without approaching the opposite surface.
    overlap = 0.5 * run_depth

    return trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface


def _tap_run_minimums(api, trunk_center, run_a, run_b, surface_profile):
    """Minimum run lengths required by the tap footprint at the run surface."""
    run_a_dir = api.unit(api.port_direction(run_a))
    run_b_dir = api.unit(api.port_direction(run_b))
    _, max_a = api.profile_projection_bounds(surface_profile, run_a_dir)
    _, max_b = api.profile_projection_bounds(surface_profile, run_b_dir)
    min_a = max(0.0, max_a - trunk_center.dot(run_a_dir))
    min_b = max(0.0, max_b - trunk_center.dot(run_b_dir))
    return min_a, min_b


def _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor):
    """Total connection lengths = intrinsic fitting dimensions + user trims."""
    min_a, min_b = _tap_run_minimums(api, trunk_center, run_a, run_b, surface_profile)
    branch_min = max(0.0, (tap_top - api.port_position(branch)).dot(branch_dir))
    extra_a = _extra_trim(p.get("TrimRunA"), run_factor * _size(api, run_a))
    extra_b = _extra_trim(p.get("TrimRunB"), run_factor * _size(api, run_b))
    extra_branch = _extra_trim(p.get("TrimBranch"), branch_factor * _size(api, branch))
    return min_a + extra_a, min_b + extra_b, branch_min + extra_branch


def _clip_tap_to_run_body(api, shape, trunk_center, run_a, run_b, trim_a, trim_b):
    """Limit tap geometry to the calculated run-body width."""
    for port, trim in ((run_a, trim_a), (run_b, trim_b)):
        direction = api.unit(api.port_direction(port))
        plane_origin = api.port_position(port) + direction * trim
        side = "positive" if (trunk_center - plane_origin).dot(direction) >= 0.0 else "negative"
        shape = api.clip_plane(shape, (plane_origin, direction), side=side)
    return shape


def _straight_tap_layout(context, run_factor, branch_factor):
    api = context["hvac_api"]
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(context, api, run_a, run_b, branch)
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.profile_from_port(surface_port)
    trim_a, trim_b, trim_branch = _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor)

    return {
        "run_a": run_a, "run_b": run_b, "branch": branch,
        "trunk_center": trunk_center, "branch_dir": branch_dir, "base_position": base_position,
        "trim_a": trim_a, "trim_b": trim_b, "trim_branch": trim_branch,
    }


def measure_straight_tap(context, run_factor, branch_factor):
    api = context["hvac_api"]
    layout = _straight_tap_layout(context, run_factor, branch_factor)
    return api.build_trim_rec_from_port_lengths([
        (layout["run_a"], layout["trim_a"]), (layout["run_b"], layout["trim_b"]),
        (layout["branch"], layout["trim_branch"]),
    ])


def _straight_tap(context, run_factor, branch_factor):
    """Straight tap. TapHeight defines the intrinsic collar height;
    TrimBranch starts above TapHeight and run trims start beyond the
    minimum tap footprint."""
    api = context["hvac_api"]
    layout = _straight_tap_layout(context, run_factor, branch_factor)
    run_a, run_b, branch = layout["run_a"], layout["run_b"], layout["branch"]
    trunk_center, branch_dir = layout["trunk_center"], layout["branch_dir"]
    base_position = layout["base_position"]
    trim_a, trim_b, trim_branch = layout["trim_a"], layout["trim_b"], layout["trim_branch"]

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)
    reach = max(0.0, (api.port_position(branch_end) - base_position).dot(branch_dir))
    stub = api.extrude(api.profile_from_port(branch_end), branch_dir * -reach, solid=True)
    stub = _clip_tap_to_run_body(api, stub, trunk_center, run_a, run_b, trim_a, trim_b)
    stub = _clip_branch_at_trunk_center(api, stub, trunk_center, branch)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _saddle_tap_layout(context, run_factor, branch_factor, flare_factor=0.6):
    api = context["hvac_api"]
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(context, api, run_a, run_b, branch)
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    growth = _positive(p.get("SaddleGrowth"), flare_factor * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.offset_profile(api.profile_from_port(surface_port), growth)

    trim_a, trim_b, trim_branch = _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor)

    return {
        "run_a": run_a, "run_b": run_b, "branch": branch,
        "trunk_center": trunk_center, "branch_dir": branch_dir,
        "tap_top": tap_top, "base_position": base_position,
        "growth": growth, "overlap": overlap, "tap_height": tap_height,
        "surface_profile": surface_profile,
        "trim_a": trim_a, "trim_b": trim_b, "trim_branch": trim_branch,
    }


def measure_saddle_tap(context, run_factor, branch_factor, flare_factor=0.6):
    api = context["hvac_api"]
    layout = _saddle_tap_layout(context, run_factor, branch_factor, flare_factor)
    return api.build_trim_rec_from_port_lengths([
        (layout["run_a"], layout["trim_a"]), (layout["run_b"], layout["trim_b"]),
        (layout["branch"], layout["trim_branch"]),
    ])


def _saddle_tap(context, run_factor, branch_factor, flare_factor=0.6):
    """Saddle tap. TapHeight defines the flare height; the embedded flare
    continues to the run mid-depth but is clipped to the tap body width."""
    api = context["hvac_api"]
    layout = _saddle_tap_layout(context, run_factor, branch_factor, flare_factor)
    run_a, run_b, branch = layout["run_a"], layout["run_b"], layout["branch"]
    trunk_center, branch_dir = layout["trunk_center"], layout["branch_dir"]
    tap_top, base_position = layout["tap_top"], layout["base_position"]
    growth, overlap, tap_height = layout["growth"], layout["overlap"], layout["tap_height"]
    surface_profile = layout["surface_profile"]
    trim_a, trim_b, trim_branch = layout["trim_a"], layout["trim_b"], layout["trim_branch"]

    top_port = api.copy_port(branch, position=tap_top)
    base_port = api.copy_port(branch, position=base_position)
    top_profile = api.profile_from_port(top_port)
    base_growth = growth * (tap_height + overlap) / tap_height
    base_profile = api.offset_profile(api.profile_from_port(base_port), base_growth)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)

    profiles = [top_profile, surface_profile, base_profile]
    if (api.port_position(branch_end) - tap_top).Length > api.EPS:
        profiles.insert(0, api.profile_from_port(branch_end))
    stub = api.loft(profiles, solid=True, ruled=True)
    stub = _clip_tap_to_run_body(api, stub, trunk_center, run_a, run_b, trim_a, trim_b)
    stub = _clip_branch_at_trunk_center(api, stub, trunk_center, branch)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _star_tee_layout(context, run_factor, branch_factor):
    api = context["hvac_api"]
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)

    center = api.center_from_context(context)
    tee_ports = [run_a, run_b, branch]
    minimums = _junction_minimums(api, center, tee_ports)
    trims = _junction_trims(api, p, tee_ports, ("TrimRunA", "TrimRunB", "TrimBranch"), (run_factor, run_factor, branch_factor), minimums)
    return run_a, run_b, branch, center, minimums, trims


def measure_star_tee(context, run_factor, branch_factor):
    api = context["hvac_api"]
    run_a, run_b, branch, center, minimums, trims = _star_tee_layout(context, run_factor, branch_factor)
    return api.build_trim_rec_from_port_lengths([(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])])


def _star_tee(context, run_factor, branch_factor):
    """Straight-legged tee with trims measured beyond the intrinsic junction body."""
    api = context["hvac_api"]
    run_a, run_b, branch, center, minimums, trims = _star_tee_layout(context, run_factor, branch_factor)
    shape = _star_body(
        api,
        center,
        [run_a, run_b, branch],
        trims,
        minimums,
        branch=branch,
    )

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }


def _run_surface_along_branch(api, center, run_a, run_b, branch_dir):
    """Find where a branch ray actually enters the run's surface, and how
    deep the run extends beyond that surface along the branch, from the
    real run profile geometry -- not a Width/Height or bounding-radius
    assumption, so this works the same for Circular/Rectangular/Oval runs.

    Mirrors the surface-intersection math ``_lateral_tee_layout()`` already
    uses for the lateral tee's own branch surface, generalized to also
    report the run's total depth along the branch ray (needed to tell
    whether a curved/mitred branch shoe can actually fit inside the run).

    Step 1: split the branch direction into the part along the run's own
    axis and the part transverse (perpendicular) to it -- a branch running
    parallel to the run has no well-defined surface to meet.
    Step 2: project the run's own profile onto that transverse direction to
    find its near (branch-side) and far surfaces, then rescale by the
    branch/run angle to turn those into real distances measured along the
    branch ray itself.
    """
    run_dir = api.unit(api.port_direction(run_a))
    transverse = branch_dir - run_dir * branch_dir.dot(run_dir)
    sin_angle = transverse.Length
    if sin_angle <= api.EPS:
        raise ValueError('Branch is parallel to the run; no run surface to meet')
    surface_dir = api.unit(transverse)

    center_projection = center.dot(surface_dir)
    near_projection = None
    far_projection = None
    for run_port in (run_a, run_b):
        centered_port = api.copy_port(run_port, position=center)
        run_profile = api.profile_from_port(centered_port)
        p_min, p_max = api.profile_projection_bounds(run_profile, surface_dir)
        near_projection = p_max if near_projection is None else max(near_projection, p_max)
        far_projection = p_min if far_projection is None else min(far_projection, p_min)

    normal_surface_depth = near_projection - center_projection
    normal_run_depth = near_projection - far_projection
    if normal_run_depth <= api.EPS:
        raise ValueError('Run profile has zero depth')

    branch_surface_distance = normal_surface_depth / sin_angle
    run_depth_along_branch = normal_run_depth / sin_angle
    run_surface = center + branch_dir * branch_surface_distance

    return run_surface, branch_surface_distance, run_depth_along_branch


def _radius_tee_layout(context, run_factor, branch_factor):
    """Layout for a rectangular tee with sections flush to opposite edges.

    The branch sweeps into one section; the minor trunk lofts into the other.
    Both sections use the main trunk's full height and their source widths,
    capped at the main trunk width.

    TrimBranch is additional straight length beyond the intrinsic curved
    fitting body, not distance measured from the run surface.
    """
    api = context['hvac_api']
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)
    center = api.center_from_context(context)
    tee_ports = [run_a, run_b, branch]
    if any(api.port_profile(port) != 'Rectangular' for port in tee_ports):
        raise ValueError('Radius tee requires rectangular profiles on all three ports')

    branch_size = _size(api, branch)
    radius = _positive(p.get('BranchRadius'), branch_factor * branch_size)
    radius = max(radius, 0.5 * branch_size)

    main = _lean_port(api, run_a, run_b, branch, reverse=bool(p.get('ReverseBranchBend', False)))
    minor = run_b if main is run_a else run_a
    # Cap only the internal sections; connected duct dimensions stay intact.
    main_width = api.port_width(main)
    branch_width = min(api.port_width(branch), main_width)
    minor_width = min(api.port_width(minor), main_width)
    main_height = api.port_height(main)
    if min(branch_width, minor_width, main_height) <= api.EPS:
        raise ValueError('Radius tee widths and main trunk height must be positive')
    _, horizontal, _, run_dir = api.make_profile_frame(
        api.port_direction(main), api.port_profile_x_axis(main), center,
    )
    branch_dir = api.unit(api.port_direction(branch))
    if abs(branch_dir.dot(horizontal)) <= api.EPS:
        raise ValueError('Radius tee branch must separate along the main trunk width')
    if branch_dir.dot(horizontal) < 0.0:
        horizontal = horizontal * -1.0

    # Anchor all inner sections to the main trunk axis. Branch alignment
    # must not change their height or the main profile's lateral position.
    inner_position = _port_axis_point(api, main, center)
    trunk_axis_port = api.copy_port(main, position=inner_position)
    trunk_axis_port['section_params'] = dict(
        api.port_section_params(main), Width=branch_width, Height=main_height,
    )

    # Set the lateral position from the main profile edge. This only places
    # the section across the width; its axial shift follows the main trunk.
    main_profile = api.profile_from_port(api.copy_port(main, position=inner_position))
    opposite_edge, main_edge = api.profile_projection_bounds(main_profile, horizontal)
    inner_profile = api.profile_from_port(trunk_axis_port)
    _, inner_edge = api.profile_projection_bounds(inner_profile, horizontal)
    shift = main_edge - inner_edge
    trunk_axis_port = api.copy_port(
        trunk_axis_port,
        position=inner_position + horizontal * shift,
    )

    route = api.make_elbow_path(branch, trunk_axis_port, radius)
    branch_route_trim = route['trim_lengths'][0]

    # Use one main-axis shift for both generated ports and the main inner
    # port, keeping all three on the same tangent plane.
    base_position = api.port_position(trunk_axis_port)
    tangent_position = api.port_position(route['ports'][1])
    axial_shift = run_dir * (tangent_position - base_position).dot(run_dir)
    branch_trunk_port = api.copy_port(
        trunk_axis_port, position=base_position + axial_shift,
    )
    route['ports'][1] = branch_trunk_port

    # Anchor the minor section to the opposite edge. Overlap is allowed.
    minor_shift = opposite_edge + 0.5 * minor_width - base_position.dot(horizontal)
    minor_trunk_port = api.copy_port(
        trunk_axis_port,
        position=base_position + horizontal * minor_shift + axial_shift,
    )
    minor_trunk_port['section_params'] = dict(
        api.port_section_params(main), Width=minor_width, Height=main_height,
    )
    # The main body keeps its own full section, independent of overlap
    # or a gap between the two incoming sections. Keep the tangent's height
    # and axial position so all three sections share the same body frame.
    main_midpoint = 0.5 * (opposite_edge + main_edge)
    main_inner_port = api.copy_port(
        main,
        position=(base_position
                  + horizontal * (main_midpoint - base_position.dot(horizontal))
                  + axial_shift),
    )

    # Size the layout from the actual path and connection sections, without
    # adding a guessed allowance for the swept body's intermediate sections.
    minimums = _junction_minimums(api, center, tee_ports)
    inner_ports = [*route['ports'], minor_trunk_port, main_inner_port]
    layout_shapes = [route['path']] + [api.profile_from_port(port) for port in inner_ports]
    for index, port in enumerate(tee_ports):
        direction = api.unit(api.port_direction(port))
        port_projection = api.port_position(port).dot(direction)
        for shape in layout_shapes:
            _, extent = api.profile_projection_bounds(shape, direction)
            minimums[index] = max(minimums[index], extent - port_projection)
    minimums[2] = max(minimums[2], branch_route_trim)

    main_index = 0 if main is run_a else 1
    # Leave space for the inner section to transition into the main port,
    # even when the requested extra trim is zero.
    main_reach = (api.port_position(main_inner_port) - api.port_position(main)).dot(run_dir)
    minimums[main_index] = max(minimums[main_index], main_reach + run_factor * _size(api, main))

    trims = _junction_trims(
        api,
        p,
        tee_ports,
        ('TrimRunA', 'TrimRunB', 'TrimBranch'),
        (run_factor, run_factor, branch_factor),
        minimums,
    )

    return {
        'run_a': run_a,
        'run_b': run_b,
        'branch': branch,
        'center': center,
        'main': main,
        'minor': minor,
        'minor_trunk_port': minor_trunk_port,
        'main_inner_port': main_inner_port,
        'route': route,
        'trims': trims,
    }


def measure_radius_tee(context, run_factor, branch_factor):
    api = context['hvac_api']
    layout = _radius_tee_layout(context, run_factor, branch_factor)

    return api.build_trim_rec_from_port_lengths(
        [
            (layout['run_a'], layout['trims'][0]),
            (layout['run_b'], layout['trims'][1]),
            (layout['branch'], layout['trims'][2]),
        ]
    )


def _radius_tee(context, run_factor, branch_factor):
    api = context['hvac_api']
    layout = _radius_tee_layout(context, run_factor, branch_factor)

    run_a = layout['run_a']
    run_b = layout['run_b']
    branch = layout['branch']
    center = layout['center']
    trims = layout['trims']
    route = layout['route']

    # Build the curved body only here; measurement uses the shared layout.
    stub = api.sweep(
        [api.profile_from_port(port) for port in route['ports']],
        route['path'],
        solid=True,
    )

    # Start the branch extension at the sweep's actual end section. A trim
    # length can be clamped, so reconstructing that section can leave a gap.
    branch_start = route['ports'][0]
    branch_end = _trimmed(api, branch, trims[2])
    branch_reach = api.port_position(branch_end) - api.port_position(branch_start)
    if branch_reach.Length > api.EPS:
        stub = api.fuse(stub, _loft(api, [branch_start, branch_end]))

    # Loft the minor trunk into its own section, then join both sections
    # to the full main trunk. The branch sweep keeps its entire profile.
    main_index = 0 if layout['main'] is run_a else 1
    minor_index = 1 - main_index
    minor_body = _loft(api, [
        _trimmed(api, layout['minor'], trims[minor_index]),
        layout['minor_trunk_port'],
    ])
    main_body = _loft(api, [
        layout['main_inner_port'],
        _trimmed(api, layout['main'], trims[main_index]),
    ])
    shape = api.fuse(main_body, minor_body, stub)
    shape = _clip_junction_to_body(api, shape, center, [run_a, run_b, branch], trims)

    return {
        'shape': api.refine(shape),
        'connection_lengths': api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }


def _star_wye_layout(context, factor=0.70):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    p = _props(context)

    center = api.center_from_context(context)
    minimums = _junction_minimums(api, center, ports)
    names = ("TrimBranchA", "TrimBranchB", "TrimBranchC")
    trims = _junction_trims(api, p, ports, names, (factor, factor, factor), minimums)
    return ports, center, minimums, trims


def measure_star_wye(context, factor=0.70):
    api = context["hvac_api"]
    ports, center, minimums, trims = _star_wye_layout(context, factor)
    return api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))


def _star_wye(context, factor=0.70):
    """Straight-legged wye with independent trims outside its minimum body."""
    api = context["hvac_api"]
    ports, center, minimums, trims = _star_wye_layout(context, factor)
    shape = _star_body(api, center, ports, trims, minimums)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(list(zip(ports, trims))),
    }


def measure_tee_radius(context):
    return measure_radius_tee(context, 0.4, 0.6)


def build_tee_radius(context):
    return _radius_tee(context, 0.4, 0.6)


def measure_tee_straight(context):
    return measure_star_tee(context, 0.60, 0.60)


def build_tee_straight(context):
    api = context["hvac_api"]
    run_a, run_b, branch, center, _, trims = _star_tee_layout(context, 0.60, 0.60)

    # Build the run as one continuous trunk, matching the radius tee.
    trunk = _loft(api, [_trimmed(api, run_a, trims[0]), _trimmed(api, run_b, trims[1])])

    # Join a straight branch stub at the trunk center plane.
    branch_end = _trimmed(api, branch, trims[2])
    branch_center = api.copy_port(branch, position=_port_axis_point(api, branch, center))
    stub = _loft(api, [branch_end, branch_center])
    stub = _clip_branch_at_trunk_center(api, stub, center, branch)

    shape = api.fuse(trunk, stub)
    shape = _clip_junction_to_body(api, shape, center, [run_a, run_b, branch], trims)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }


def _tee_mitered_shoe_layout(context):
    """Layout for a mitered-shoe tee.

    The theoretical bend corner lies on the branch axis. The mitered bend
    is limited at the trunk center plane before it is fused with the run.

    TrimBranch is additional length beyond the intrinsic mitered body.
    """
    api = context['hvac_api']
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)
    center = api.center_from_context(context)
    tee_ports = [run_a, run_b, branch]

    branch_size = _size(api, branch)
    radius = _positive(p.get('BranchRadius'), 0.6 * branch_size)
    radius = max(radius, 0.5 * branch_size)
    cuts = max(int(p.get('NumberOfCuts', 1) or 1), 1)

    lean = _lean_port(api, run_a, run_b, branch, reverse=bool(p.get('ReverseBranchBend', False)))
    inner_position = _port_axis_point(api, branch, center)
    trunk_axis_port = api.copy_port(
        branch,
        position=inner_position,
        direction=api.port_direction(lean),
    )

    bend_shape, route_trims = _mitered_bend(api, branch, trunk_axis_port, radius, cuts)
    branch_route_trim, lean_route_trim = route_trims

    minimums = _junction_minimums(api, center, tee_ports)
    minimums = _junction_shape_minimums(api, center, tee_ports, bend_shape, minimums)
    minimums[2] = max(minimums[2], branch_route_trim)

    lean_index = 0 if lean is run_a else 1
    minimums[lean_index] = max(minimums[lean_index], lean_route_trim)

    trims = _junction_trims(
        api,
        p,
        tee_ports,
        ('TrimRunA', 'TrimRunB', 'TrimBranch'),
        (0.4, 0.4, 0.6),
        minimums,
    )

    return {
        'run_a': run_a,
        'run_b': run_b,
        'branch': branch,
        'center': center,
        'lean': lean,
        'bend_shape': bend_shape,
        'branch_route_trim': branch_route_trim,
        'lean_route_trim': lean_route_trim,
        'trims': trims,
    }


def measure_tee_mitered_shoe(context):
    api = context['hvac_api']
    layout = _tee_mitered_shoe_layout(context)

    return api.build_trim_rec_from_port_lengths(
        [
            (layout['run_a'], layout['trims'][0]),
            (layout['run_b'], layout['trims'][1]),
            (layout['branch'], layout['trims'][2]),
        ]
    )


def build_tee_mitered_shoe(context):
    api = context['hvac_api']
    layout = _tee_mitered_shoe_layout(context)

    run_a = layout['run_a']
    run_b = layout['run_b']
    branch = layout['branch']
    center = layout['center']
    trims = layout['trims']

    bend_shape = _extend_leg(api, layout['bend_shape'], branch, layout['branch_route_trim'], trims[2])
    trunk = _loft(api, [_trimmed(api, run_a, trims[0]), _trimmed(api, run_b, trims[1])])

    bend_shape = _clip_branch_at_trunk_center(api, bend_shape, center, branch)
    shape = api.fuse(trunk, bend_shape)
    shape = _clip_junction_to_body(api, shape, center, [run_a, run_b, branch], trims)

    return {
        'shape': api.refine(shape),
        'connection_lengths': api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }


def _lateral_tee_layout(context):
    api = context["hvac_api"]
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)

    center = api.center_from_context(context)
    branch_dir = api.unit(api.port_direction(branch))
    branch_axis_center = _port_axis_point(api, branch, center)
    lean = _lean_port(api, run_a, run_b, branch)
    run_dir = api.unit(api.port_direction(lean))

    transverse = branch_dir - run_dir * branch_dir.dot(run_dir)
    sin_angle = transverse.Length
    if sin_angle <= api.EPS:
        raise ValueError("Lateral tee branch is parallel to the run")
    surface_dir = api.unit(transverse)

    center_projection = branch_axis_center.dot(surface_dir)
    surface_projection = None
    for run_port in (run_a, run_b):
        centered_port = api.copy_port(run_port, position=center)
        run_profile = api.profile_from_port(centered_port)
        _, p_max = api.profile_projection_bounds(run_profile, surface_dir)
        surface_projection = p_max if surface_projection is None else max(surface_projection, p_max)

    if surface_projection is None:
        raise ValueError("Could not determine run surface")

    normal_depth = surface_projection - center_projection
    branch_depth = normal_depth / sin_angle
    run_surface = branch_axis_center + branch_dir * branch_depth
    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.profile_from_port(surface_port)

    run_a_dir = api.unit(api.port_direction(run_a))
    run_b_dir = api.unit(api.port_direction(run_b))
    _, max_a = api.profile_projection_bounds(surface_profile, run_a_dir)
    _, max_b = api.profile_projection_bounds(surface_profile, run_b_dir)
    min_a = max(0.0, max_a - center.dot(run_a_dir))
    min_b = max(0.0, max_b - center.dot(run_b_dir))

    trim_a = min_a + _extra_trim(p.get("TrimRunA"), 0.65 * _size(api, run_a))
    trim_b = min_b + _extra_trim(p.get("TrimRunB"), 0.65 * _size(api, run_b))

    branch_min = max(0.0, (run_surface - api.port_position(branch)).dot(branch_dir))
    trim_branch = branch_min + _extra_trim(p.get("TrimBranch"), 0.65 * _size(api, branch))

    return {
        "run_a": run_a, "run_b": run_b, "branch": branch, "center": center,
        "branch_dir": branch_dir, "run_surface": run_surface, "surface_profile": surface_profile,
        "trim_a": trim_a, "trim_b": trim_b, "trim_branch": trim_branch,
    }


def measure_lateral_tee(context):
    api = context["hvac_api"]
    layout = _lateral_tee_layout(context)
    return api.build_trim_rec_from_port_lengths([
        (layout["run_a"], layout["trim_a"]), (layout["run_b"], layout["trim_b"]),
        (layout["branch"], layout["trim_branch"]),
    ])


def build_lateral_tee(context):
    """Straight lateral tee with a continuous run trunk and branch stub.

    TrimRunA/TrimRunB are measured beyond the minimum branch footprint.
    TrimBranch is measured outward from the inclined run surface.
    """
    api = context["hvac_api"]
    layout = _lateral_tee_layout(context)
    run_a, run_b, branch, center = layout["run_a"], layout["run_b"], layout["branch"], layout["center"]
    trim_a, trim_b, trim_branch = layout["trim_a"], layout["trim_b"], layout["trim_branch"]

    # Build the run as one continuous trunk, matching the straight tee.
    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])

    # Join a straight branch stub at the trunk center plane.
    branch_end = _trimmed(api, branch, trim_branch)
    branch_axis_center = _port_axis_point(api, branch, center)
    branch_center = api.copy_port(branch, position=branch_axis_center)
    branch_stub = _loft(api, [branch_end, branch_center])
    branch_dir = api.port_direction(branch)
    trunk_dir = api.port_direction(run_a)

    # Cut on a plane perpendicular to the wye plane. Its normal stays in
    # the wye plane and points from the trunk axis toward the branch.
    wye_plane_normal = api.unit(trunk_dir.cross(branch_dir))
    clip_normal = api.unit(wye_plane_normal.cross(trunk_dir))
    branch_stub = api.clip_plane(branch_stub, (branch_axis_center, clip_normal), side="positive")

    shape = api.fuse(trunk, branch_stub)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _wye_mitered_layout(context):
    """Lay out two split-profile faceted bends entering one common leg."""
    api = context['hvac_api']
    p = _props(context)
    cuts = max(int(p.get('NumberOfCuts', 2) or 2), 1)

    # Reuse the same split plane and feasible elbow paths as the radius wye.
    ports, center, main_center, split_routes = _wye_split_route_layout(context)
    main = ports[0]
    main_direction = api.unit(api.port_direction(main))
    routes = []
    intrinsic_shapes = []

    for branch, branch_index, split_face, route in split_routes:
        split_port = api.copy_port(
            main_center,
            position=split_face.CenterOfMass,
        )
        main_tangent = route['ports'][1]
        main_tangent_position = api.port_position(main_tangent)
        main_reach = main_tangent_position - split_face.CenterOfMass
        tangent_face = split_face
        split_shape = None
        if main_reach.Length > api.EPS:
            split_shape = api.extrude(split_face.OuterWire, main_reach, solid=True)
            tangent_face = api.section_face(
                split_shape,
                (main_tangent_position, api.port_direction(main_tangent)),
            )

        bend_shape, route_trims = _mitered_bend(
            api,
            branch,
            split_port,
            route['radius'],
            cuts,
            profile1=tangent_face.OuterWire,
        )

        routes.append(
            {
                'branch': branch,
                'branch_index': branch_index,
                'split_shape': split_shape,
                'bend_shape': bend_shape,
                'branch_trim': route_trims[0],
                'radius': route['radius'],
            }
        )
        intrinsic_shapes.extend((split_shape, bend_shape))

    intrinsic_shape = api.compound(intrinsic_shapes)

    minimums = _junction_minimums(api, center, ports)
    minimums = _junction_shape_minimums(api, center, ports, intrinsic_shape, minimums)
    main_split_trim = (
        api.port_position(main_center) - api.port_position(main)
    ).dot(main_direction)
    minimums[0] = max(minimums[0], main_split_trim)

    for route in routes:
        branch_index = route['branch_index']
        minimums[branch_index] = max(minimums[branch_index], route['branch_trim'])

    trims = _junction_trims(
        api,
        p,
        ports,
        ('TrimMain', 'TrimBranchA', 'TrimBranchB'),
        (0.70, 0.70, 0.70),
        minimums,
    )

    return {
        'ports': ports,
        'center': center,
        'main': main,
        'main_center': main_center,
        'routes': routes,
        'trims': trims,
    }


def measure_wye_mitered(context):
    api = context['hvac_api']
    layout = _wye_mitered_layout(context)

    return api.build_trim_rec_from_port_lengths(
        list(zip(layout['ports'], layout['trims']))
    )


def build_wye_mitered(context):
    """Mitered wye with each non-main branch entering the common leg through a faceted bend."""
    api = context['hvac_api']
    layout = _wye_mitered_layout(context)

    ports = layout['ports']
    main = layout['main']
    main_center = layout['main_center']
    trims = layout['trims']

    main_end = _trimmed(api, main, trims[0])
    main_reach = api.port_position(main_end) - api.port_position(main_center)
    main_tolerance = max(api.EPS, 1.0e-6 * _size(api, main))
    shape = None
    if main_reach.Length > main_tolerance:
        shape = _loft(api, [main_end, main_center], 0.0, ruled=True)

    for route in layout['routes']:
        branch = route['branch']
        branch_index = route['branch_index']

        bend_shape = _extend_leg(
            api,
            route['bend_shape'],
            branch,
            route['branch_trim'],
            trims[branch_index],
        )

        shape = api.fuse(shape, route['split_shape'], bend_shape)

    validation = api.validate(shape, require_solid=True)
    if not validation['valid'] or validation['solid_count'] != 1:
        raise RuntimeError(
            'Mitered wye geometry could not be fused into one valid solid '
            '(requested radius {:.3f} mm; cuts {})'.format(
                float(_props(context).get('BranchRadius') or 0.0),
                max(int(_props(context).get('NumberOfCuts', 2) or 2), 1),
            )
        )

    return {
        'shape': shape,
        'connection_lengths': api.build_trim_rec_from_port_lengths(
            list(zip(ports, trims))
        ),
    }


def _wye_common_index(api, ports):
    """
    Which of a 3-port wye's ports is the common/main leg -- from port
    geometry (each port's own outward direction) only, never from duct
    size: resizing one leg must never change which physical leg is
    common. The two branch legs are normally the pair whose outward
    directions are closest to each other (most nearly parallel); the
    remaining port is the common leg. Never uses flow direction -- this
    is a purely geometric/topological identity, unrelated to which way
    air happens to be moving.
    """
    if len(ports) != 3:
        raise ValueError("Wye requires exactly three ports")
    directions = [api.unit(api.port_direction(port)) for port in ports]
    pairs = ((0, 1), (0, 2), (1, 2))
    branch_pair = max(pairs, key=lambda pair: directions[pair[0]].dot(directions[pair[1]]))
    return next(index for index in range(3) if index not in branch_pair)


def _wye_branch_sort_key(api, main, branch):
    """Stable branch-side ordering in the main leg's local profile frame."""
    preferred_x = api.port_profile_x_axis(main)

    if preferred_x is not None:
        _, x_axis, y_axis, _ = api.make_profile_frame(api.port_direction(main), preferred_x)
        direction = api.unit(api.port_direction(branch))
        return round(direction.dot(x_axis), 12), round(direction.dot(y_axis), 12)

    key = (str(branch.get('edge_key', '') or ''), str(branch.get('segment_end', '') or ''))

    if key == ('', ''):
        raise ValueError('Wye branch ordering requires profile_x_axis or stable branch edge keys')

    return key


def _wye_port_roles(api, ports):
    """Return stable semantic ordering: common/main leg, branch A, branch B."""
    if len(ports) != 3:
        raise ValueError('Wye requires exactly three ports')

    main_index = _wye_common_index(api, ports)
    main = ports[main_index]
    branches = [ports[index] for index in range(3) if index != main_index]

    branches.sort(key=lambda branch: _wye_branch_sort_key(api, main, branch), reverse=True)
    return main, branches[0], branches[1]


def _wye_horizontal_extent(api, port):
    """Return the branch profile dimension along its horizontal axis."""
    profile = api.port_profile(port)
    if profile == 'Circular':
        extent = float(api.port_diameter(port))
    elif profile in {'Rectangular', 'Oval'}:
        extent = float(api.port_width(port))
    else:
        raise ValueError("Split-profile wye requires a circular, rectangular, or oval profile")

    if extent <= api.EPS:
        raise ValueError('Wye branch horizontal extent must be positive')
    return extent


def _wye_main_split_faces(api, main_center, branch_a, branch_b):
    """Split the main profile horizontally in proportion to the branches."""
    main_profile = api.profile_from_port(main_center)
    _, horizontal, _vertical, main_dir = api.make_profile_frame(
        api.port_direction(main_center),
        api.port_profile_x_axis(main_center),
        api.port_position(main_center),
    )

    # Keep the positive split face on branch A's side.
    branch_delta = api.unit(api.port_direction(branch_a)) - api.unit(
        api.port_direction(branch_b)
    )
    branch_delta = branch_delta - main_dir * branch_delta.dot(main_dir)
    side = branch_delta.dot(horizontal)
    if abs(side) <= api.EPS:
        raise ValueError('Wye branches do not separate along the horizontal profile axis')
    if side < 0.0:
        horizontal = horizontal * -1.0

    branch_a_extent = _wye_horizontal_extent(api, branch_a)
    branch_b_extent = _wye_horizontal_extent(api, branch_b)
    ratio = branch_a_extent / (branch_a_extent + branch_b_extent)
    positive, negative = api.split_profile_face_by_extent(
        main_profile,
        horizontal,
        ratio,
    )
    return {id(branch_a): positive, id(branch_b): negative}


def _wye_route_clearance(api, branch):
    """Small modeling clearance scaled to the branch profile."""
    return max(1.0e-2 * _size(api, branch), 0.1)


def _wye_elbow_route(api, branch, split_port, requested_radius):
    """Build a route whose tangent points stay on both usable leg sides."""
    branch_size = _size(api, branch)
    clearance = _wye_route_clearance(api, branch)

    # The half-size limit keeps the inside of the swept profile from folding
    # through the bend centerline.
    radius = max(float(requested_radius or 0.0), 0.5 * branch_size)
    route = api.make_elbow_path(branch, split_port, radius)

    # An offset split profile can need more radius before both tangent points
    # lie beyond their source ports. The signed distances vary linearly with R.
    signed_trims = [
        (
            api.port_position(route_port) - api.port_position(source_port)
        ).dot(api.unit(api.port_direction(source_port)))
        for route_port, source_port in zip(route['ports'], (branch, split_port))
    ]
    deficit = clearance - signed_trims[0]
    if deficit > 0.0:
        theta = api.angle_between(
            api.port_direction(branch),
            api.port_direction(split_port),
        )
        radius += deficit * math.tan(theta / 2.0)
        route = api.make_elbow_path(branch, split_port, radius)

    route['radius'] = radius
    return route


def _wye_split_route_layout(context):
    """Calculate shared port roles, split profiles, and feasible elbow paths."""
    api = context['hvac_api']
    raw_ports = list(api.connected_ports(context))
    if len(raw_ports) != 3:
        raise ValueError('Fitting requires exactly three connected ports')

    main, branch_a, branch_b = _wye_port_roles(api, raw_ports)
    ports = [main, branch_a, branch_b]
    center = api.center_from_context(context)
    branch_legs = [(1, branch_a), (2, branch_b)]
    radius = float(_props(context).get('BranchRadius') or 0.0)

    # Step 1: Build provisional paths at the junction center.
    main_direction = api.unit(api.port_direction(main))
    main_axis_center = _port_axis_point(api, main, center)
    main_center = api.copy_port(main, position=main_axis_center)

    def build_routes(split_center):
        split_faces = _wye_main_split_faces(api, split_center, branch_a, branch_b)
        result = []
        for branch_index, branch in branch_legs:
            split_face = split_faces[id(branch)]
            split_port = api.copy_port(
                split_center,
                position=split_face.CenterOfMass,
            )
            route = _wye_elbow_route(api, branch, split_port, radius)
            result.append((branch, branch_index, split_face, route))
        return result

    routes = build_routes(main_center)

    # Step 2: Large radii can put an elbow tangent beyond the split plane.
    # Move the plane outward so each split half approaches its arc from behind.
    main_shift = max(
        [0.0]
        + [
            (
                api.port_position(route['ports'][1]) - split_face.CenterOfMass
            ).dot(main_direction)
            + _wye_route_clearance(api, branch)
            for branch, _, split_face, route in routes
        ]
    )
    if main_shift > api.EPS:
        main_center = api.copy_port(
            main_center,
            position=api.port_position(main_center) + main_direction * main_shift,
        )
        routes = build_routes(main_center)

    return ports, center, main_center, routes


def _wye_radius_layout(context):
    """Calculate the split profiles, branch paths, and trims for a wye."""
    api = context['hvac_api']
    p = _props(context)
    ports, center, main_center, routes = _wye_split_route_layout(context)
    main = ports[0]
    main_direction = api.unit(api.port_direction(main))

    # Include the relocated split plane in the main-leg trim.
    minimums = _junction_minimums(api, center, ports)
    main_split_trim = (
        api.port_position(main_center) - api.port_position(main)
    ).dot(main_direction)
    minimums[0] = max(minimums[0], main_split_trim)
    for _branch, branch_index, _split_face, route in routes:
        minimums[branch_index] = max(
            minimums[branch_index],
            route['trim_lengths'][0],
        )

    trims = _junction_trims(
        api,
        p,
        ports,
        ('TrimMain', 'TrimBranchA', 'TrimBranchB'),
        (0.70, 0.70, 0.70),
        minimums,
    )
    return ports, main_center, routes, trims


def measure_wye_radius(context):
    api = context['hvac_api']
    ports, _main_center, _routes, trims = _wye_radius_layout(context)
    return api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))


def build_wye_radius(context):
    """Build a radiused wye from a horizontally divided main profile."""
    api = context['hvac_api']
    ports, main_center, routes, trims = _wye_radius_layout(context)

    # Sweep each split half of the main profile into its branch tangent.
    split_sweeps = []
    split_touch_sweeps = []
    branch_sweeps = []
    for branch, _branch_index, split_face, route in routes:
        main_tangent = route['ports'][1]
        main_tangent_position = api.port_position(main_tangent)
        main_reach = main_tangent_position - split_face.CenterOfMass
        tangent_face = split_face
        if main_reach.Length > api.EPS:
            route_overlap = 0.1 * _wye_route_clearance(api, branch)
            section_probe = api.extrude(split_face.OuterWire, main_reach, solid=True)
            probe = api.extrude(
                split_face.OuterWire,
                main_reach + api.unit(main_reach) * route_overlap,
                solid=True,
            )
            tangent_face = api.section_face(
                section_probe,
                (main_tangent_position, api.port_direction(main_tangent)),
            )
            split_sweeps.append(probe)
            split_touch_sweeps.append(section_probe)
        else:
            split_sweeps.append(None)
            split_touch_sweeps.append(None)

        branch_tangent = route['ports'][0]
        try:
            branch_sweeps.append(
                api.sweep(
                    [tangent_face.OuterWire, api.profile_from_port(branch_tangent)],
                    api.reverse(route['path']),
                    solid=True,
                )
            )
        except Exception as exc:
            raise RuntimeError(
                'Wye branch sweep failed at effective radius {:.3f} mm: {}'.format(
                    route['radius'], exc
                )
            ) from exc

    # A small scaled overlap avoids a coplanar boolean at the split plane;
    # the full main profile still stops at the split approach itself.
    main_overlap = 0.1 * min(
        _wye_route_clearance(api, branch) for branch, _, _, _ in routes
    )
    main_trim_start = api.copy_port(
        main_center,
        position=(
            api.port_position(main_center)
            - api.unit(api.port_direction(main_center)) * main_overlap
        ),
    )
    trim_starts = [main_trim_start] + [route['ports'][0] for _, _, _, route in routes]
    trim_sweeps = []
    for port, trim_start, trim in zip(ports, trim_starts, trims):
        trim_end = _trimmed(api, port, trim)
        trim_reach = api.port_position(trim_end) - api.port_position(trim_start)
        trim_tolerance = max(api.EPS, 1.0e-6 * _size(api, port))
        if trim_reach.Length <= trim_tolerance:
            trim_sweeps.append(None)
            continue
        trim_path = api.make_line(
            api.port_position(trim_start),
            api.port_position(trim_end),
        )
        try:
            trim_sweeps.append(
                api.sweep(
                    [api.profile_from_port(trim_start), api.profile_from_port(trim_end)],
                    trim_path,
                    solid=True,
                )
            )
        except Exception as exc:
            edge_key = str(port.get('edge_key', '') or '?')
            raise RuntimeError(
                "Wye trim sweep failed for port '{}' at {:.6f} mm: {}".format(
                    edge_key, trim_reach.Length, exc
                )
            ) from exc

    main_trim = trim_sweeps[0]
    fuse_orders = []
    for connector_sweeps in (split_sweeps, split_touch_sweeps):
        leg_a = (connector_sweeps[0], branch_sweeps[0], trim_sweeps[1])
        leg_b = (connector_sweeps[1], branch_sweeps[1], trim_sweeps[2])
        fuse_orders.extend((
            (main_trim, *leg_a, *leg_b),
            (main_trim, leg_a[0], leg_a[1], leg_b[0], leg_b[1], leg_a[2], leg_b[2]),
            (*leg_a, main_trim, *leg_b),
            (main_trim, *leg_b, *leg_a),
            (*connector_sweeps, *branch_sweeps, *trim_sweeps),
        ))

    # OCC booleans are order-sensitive when several swept solids share seams.
    # Keep the first sequence that produces exactly one valid solid.
    shape = None
    for fuse_order in fuse_orders:
        try:
            candidate = api.fuse(*fuse_order)
        except Exception:
            continue
        validation = api.validate(candidate, require_solid=True)
        if validation['valid'] and validation['solid_count'] == 1:
            shape = candidate
            break
    if shape is None:
        requested_radius = float(_props(context).get('BranchRadius') or 0.0)
        effective_radii = ', '.join(
            '{:.3f}'.format(route['radius']) for _, _, _, route in routes
        )
        raise RuntimeError(
            'Wye geometry could not be fused into one valid solid '
            '(requested radius {:.3f} mm; effective branch radii {} mm)'.format(
                requested_radius, effective_radii
            )
        )

    return {
        'shape': shape,
        'connection_lengths': api.build_trim_rec_from_port_lengths(list(zip(ports, trims))),
    }


def measure_tap_straight(context):
    return measure_straight_tap(context, 0.3, 0.5)


def build_tap_straight(context):
    return _straight_tap(context, 0.3, 0.5)


def measure_tap_saddle(context):
    return measure_saddle_tap(context, 0.3, 0.5)


def build_tap_saddle(context):
    return _saddle_tap(context, 0.3, 0.5)


def _tap_shoe_layout(context):
    api = context["hvac_api"]
    run_a, run_b, branch = api.run_branch_ports(context)
    p = _props(context)

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(context, api, run_a, run_b, branch)
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.stretch_profile_one_sided(api.profile_from_port(surface_port), toe_dir, tap_height)

    trim_a, trim_b, trim_branch = _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, 0.3, 0.5)

    return {
        "run_a": run_a, "run_b": run_b, "branch": branch,
        "trunk_center": trunk_center, "branch_dir": branch_dir, "toe_dir": toe_dir,
        "tap_top": tap_top, "base_position": base_position, "overlap": overlap, "tap_height": tap_height,
        "surface_profile": surface_profile,
        "trim_a": trim_a, "trim_b": trim_b, "trim_branch": trim_branch,
    }


def measure_tap_shoe(context):
    api = context["hvac_api"]
    layout = _tap_shoe_layout(context)
    return api.build_trim_rec_from_port_lengths([
        (layout["run_a"], layout["trim_a"]), (layout["run_b"], layout["trim_b"]),
        (layout["branch"], layout["trim_branch"]),
    ])


def build_tap_shoe(context):
    """45-degree shoe tap with profile-independent geometry. TapHeight
    defines both the vertical shoe height and, at 45 degrees, the toe
    extension at the run surface."""
    api = context["hvac_api"]
    layout = _tap_shoe_layout(context)
    run_a, run_b, branch = layout["run_a"], layout["run_b"], layout["branch"]
    trunk_center, branch_dir, toe_dir = layout["trunk_center"], layout["branch_dir"], layout["toe_dir"]
    tap_top, base_position = layout["tap_top"], layout["base_position"]
    overlap, tap_height = layout["overlap"], layout["tap_height"]
    surface_profile = layout["surface_profile"]
    trim_a, trim_b, trim_branch = layout["trim_a"], layout["trim_b"], layout["trim_branch"]

    top_port = api.copy_port(branch, position=tap_top)
    base_port = api.copy_port(branch, position=base_position)
    top_profile = api.profile_from_port(top_port)
    base_profile = api.stretch_profile_one_sided(api.profile_from_port(base_port), toe_dir, tap_height + overlap)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)

    profiles = [top_profile, surface_profile, base_profile]
    if (api.port_position(branch_end) - tap_top).Length > api.EPS:
        profiles.insert(0, api.profile_from_port(branch_end))
    stub = api.loft(profiles, solid=True, ruled=True)
    stub = _clip_tap_to_run_body(api, stub, trunk_center, run_a, run_b, trim_a, trim_b)
    stub = _clip_branch_at_trunk_center(api, stub, trunk_center, branch)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def build_branch_generic(context):
    return _star_junction(context, 0.60, align_branch=True)


def build_cross(context):
    return _star_junction(context, 0.60)


def build_manifold(context):
    return _star_junction(context, 0.65)


def _straight_is_transition(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    s0, s1 = api.port_section_params(ports[0]), api.port_section_params(ports[1])
    return api.port_profile(ports[0]) != api.port_profile(ports[1]) or s0 != s1


def measure_straight(context):
    if _straight_is_transition(context):
        return measure_transition(context)
    return measure_inline(context, 0.2, 30.0)


def build_straight(context):
    """Plain duct-to-duct connection: no bend, no special fitting.

    Two same-profile, same-size ports become a short coupling sleeve; two
    differently-sized/profiled ports fall back to a transition, since the
    "through.straight" family key is purely about the ports being
    collinear with zero eccentricity (see TOPOLOGY_CLASSIFICATION.md) --
    it says nothing about whether their sizes actually match.
    """
    if _straight_is_transition(context):
        return build_transition(context)
    return _inline(context, 0.2, 30.0)


def _through_generic_dispatch(context):
    """Which of build_through_generic's 4 cases applies: "elbow"
    (non-collinear ports), "transition" (collinear but different profile/
    size), "inline" (collinear, same profile/size), or "marker" (not
    exactly 2 ports -- a defensive fallback for a not-yet-composed node).
    Shared so measure_through_generic() can never disagree with
    build_through_generic() about which case it's in."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 2:
        return "marker"
    u0 = api.port_direction(ports[0])
    u1 = api.port_direction(ports[1])
    dot = max(-1.0, min(1.0, float(u0.dot(u1))))
    if dot > -0.985:
        return "elbow"
    s0, s1 = api.port_section_params(ports[0]), api.port_section_params(ports[1])
    if api.port_profile(ports[0]) != api.port_profile(ports[1]) or s0 != s1:
        return "transition"
    return "inline"


def measure_through_generic(context):
    kind = _through_generic_dispatch(context)
    if kind == "elbow":
        return measure_elbow(context)
    if kind == "transition":
        return measure_transition(context)
    if kind == "inline":
        return measure_inline(context, 0.35, 80.0)
    return []


def build_through_generic(context):
    kind = _through_generic_dispatch(context)
    if kind == "elbow":
        return build_elbow(context)
    if kind == "transition":
        return build_transition(context)
    if kind == "inline":
        return _inline(context, 0.35, 80.0)
    return _marker(context, 160.0)
