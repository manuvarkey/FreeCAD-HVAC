"""Common fitting recipes built only from HVACLibraryAPI geometry primitives."""

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


def _profile_extent(profile):
    shape = profile.wire if hasattr(profile, "wire") else profile
    bb = shape.BoundBox
    return max(bb.DiagonalLength, bb.XLength, bb.YLength, bb.ZLength, 1.0)


def _mitered_bend(api, port0, port1, radius, cuts):
    """Build a flat-cut (mitre-gore) bend between two arbitrary ports.

    Shared by ``build_elbow_mitered`` (a real degree-2 through fitting) and
    the branch "mitered shoe" builders, which mitre a branch leg into a
    synthetic port lying on a main-run axis rather than a real connected
    port. Returns ``(shape, [trim0, trim1])``, mirroring
    ``api.make_elbow_path``'s own trim-length pairing.
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
    profile0 = api.profile_from_port(route_port0)
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


def build_elbow_mitered(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    port0, port1 = ports
    size = max(_profile_extent(api.profile_from_port(port0)), _profile_extent(api.profile_from_port(port1)))
    props = _props(context)
    radius = _positive(props.get("CenterlineRadius"), 0.6 * size)
    radius = max(radius, 0.5 * size)
    cuts = max(int(props.get("NumberOfCuts", 1) or 1), 1)
    shape, trims = _mitered_bend(api, port0, port1, radius, cuts)
    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths([(port0, trims[0]), (port1, trims[1])]),
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
    # A bend radius smaller than the duct's own half-width folds the swept
    # surface back on itself on the inside of the bend (same minimum
    # build_elbow enforces on CenterlineRadius).
    radius = max(radius, 0.5 * size)
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


def _find_run_pair(context, api, ports):
    """Split a degree-3 node's ports into ``(run_a, run_b, branch)``.

    ``run_a``/``run_b`` are the collinear trunk run a "branch.tee"/
    "branch.lateral_tee" node is guaranteed to have (see
    ``freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md``); the remaining port is
    the branch/tap leg. Read straight off the network classifier's own
    answer (``api.collinear_port_index_pairs``, sourced from the parent
    junction's ``AnalysisJson`` -- see the "Generator context" section of
    ``freecad/HVAC/libraries/samples/README.md``) rather than re-deriving
    collinearity here: that keeps this to one, single source of truth for
    "which two ports form the straight run", and it's always available --
    every real caller of these generators is a Primary component on an
    actual branch.tee/branch.lateral_tee junction, which is only ever
    selected once the classifier has already found exactly this pair.
    """
    pairs = api.collinear_port_index_pairs(context)
    if not pairs:
        raise ValueError("Could not identify run pair: context['analysis']['collinear_pairs'] is empty")
    a, b = pairs[0]
    branch_index = next(i for i in range(len(ports)) if i not in (a, b))
    return ports[a], ports[b], ports[branch_index]


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


def _lean_port(api, run_a, run_b, branch):
    """Pick whichever run leg a branch leg should curve/mitre toward.

    There is no geometric signal for which way a branch should lean (flow
    direction isn't known at generator time), so this picks the run
    direction the branch's own incoming direction is already closer to
    aligned with -- a deterministic, reproducible choice.
    """
    incoming = api.port_direction(branch) * -1.0
    da = api.port_direction(run_a)
    db = api.port_direction(run_b)
    return run_a if incoming.dot(da) >= incoming.dot(db) else run_b


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


def _run_trims(api, p, run_a, run_b, factor):
    """Independent (TrimRunA, TrimRunB) trim lengths for a tee/tap's two
    collinear run ports, each defaulting off its own port's own size."""
    trim_a = _positive(p.get("TrimRunA"), factor * _size(api, run_a))
    trim_b = _positive(p.get("TrimRunB"), factor * _size(api, run_b))
    return trim_a, trim_b


def _star_tee(context, run_factor, branch_factor):
    """Like ``_star_junction``, but a tee/lateral-tee's run/branch legs
    (found via ``_find_run_pair``) each read their own independent
    TrimRunA/TrimRunB/TrimBranch property instead of one uniform value."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)
    trim_a, trim_b = _run_trims(api, p, run_a, run_b, run_factor)
    trim_branch = _positive(p.get("TrimBranch"), branch_factor * _size(api, branch))
    center = sum((api.port_position(port) for port in ports), api.vec((0, 0, 0))) / len(ports)
    legs = []
    for port, trim in ((run_a, trim_a), (run_b, trim_b), (branch, trim_branch)):
        end = _trimmed(api, port, trim)
        hub = api.copy_port(port, position=center, direction=api.port_direction(port) * -1.0)
        legs.append(_loft(api, [end, hub], 0.0, ruled=True))
    return {
        "shape": api.refine(api.fuse(*legs)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _radius_tee(context, run_factor, branch_factor, run_margin=0.2):
    """Tee/tap with the run left straight and the branch swept along a
    tangent arc that leans into the run (the branch-topology analogue of
    ``build_elbow``'s smooth swept-arc bend). TrimRunA/TrimRunB/TrimBranch
    are each independently settable; the run leg the branch leans toward
    is floored at whatever length the arc's own tangent point requires
    (``run_margin`` extra clearance beyond that), and the branch leg is
    extended past the arc's tangent point (``_extend_leg``) if TrimBranch
    asks for more than the arc alone provides."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)
    run_a_size, run_b_size = _size(api, run_a), _size(api, run_b)
    branch_size = _size(api, branch)
    trim_a, trim_b = _run_trims(api, p, run_a, run_b, run_factor)
    trim_branch = _positive(p.get("TrimBranch"), branch_factor * branch_size)
    radius = _positive(p.get("BranchRadius"), branch_factor * branch_size)
    radius = max(radius, 0.5 * branch_size)

    trunk_center = api.center_from_context(context)
    lean = _lean_port(api, run_a, run_b, branch)
    trunk_axis_port = api.copy_port(branch, position=trunk_center, direction=api.port_direction(lean))
    route = api.make_elbow_path(branch, trunk_axis_port, radius)
    branch_route_trim, lean_route_trim = route["trim_lengths"]
    if lean is run_a:
        trim_a = max(trim_a, lean_route_trim + run_margin * run_a_size)
    else:
        trim_b = max(trim_b, lean_route_trim + run_margin * run_b_size)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    stub = api.sweep([api.profile_from_port(route["ports"][0]), api.profile_from_port(route["ports"][1])], route["path"], solid=True)
    stub = _extend_leg(api, stub, branch, branch_route_trim, trim_branch)
    trim_branch = max(trim_branch, branch_route_trim)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _straight_tap(context, run_factor, branch_factor):
    """Run left straight; branch is a plain constant-profile stub fused
    straight in, with no shaping at the crotch -- a bare pipe-in-pipe tap.
    TrimRunA/TrimRunB/TrimBranch are each independently settable, with no
    geometric floor -- the branch stub is a plain extrusion, unconstrained
    by any bend radius."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)
    branch_size = _size(api, branch)
    main_size = max(_size(api, run_a), _size(api, run_b))
    trim_a, trim_b = _run_trims(api, p, run_a, run_b, run_factor)
    trim_branch = _positive(p.get("TrimBranch"), branch_factor * branch_size)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)
    trunk_center = api.center_from_context(context)
    branch_dir = api.port_direction(branch)
    overlap = max(0.05 * min(branch_size, main_size), 1.0)
    reach = max(0.0, (api.port_position(branch_end) - trunk_center).dot(branch_dir)) + overlap
    stub = api.extrude(api.profile_from_port(branch_end), branch_dir * -reach, solid=True)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _saddle_tap(context, run_factor, branch_factor, flare_factor=0.6):
    """Run left straight; branch stub flares (via ``offset_profile``) as it
    nears the run, approximating a saddle-coped base conforming to the
    run's own surface. TrimRunA/TrimRunB/TrimBranch are each independently
    settable, with no geometric floor."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)
    branch_size = _size(api, branch)
    main_size = max(_size(api, run_a), _size(api, run_b))
    trim_a, trim_b = _run_trims(api, p, run_a, run_b, run_factor)
    trim_branch = _positive(p.get("TrimBranch"), branch_factor * branch_size)
    growth = _positive(p.get("SaddleGrowth"), flare_factor * branch_size)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)
    trunk_center = api.center_from_context(context)
    branch_dir = api.port_direction(branch)
    overlap = max(0.05 * min(branch_size, main_size), 1.0)
    inner = api.copy_port(branch, position=trunk_center - branch_dir * overlap)
    inner_profile = api.offset_profile(api.profile_from_port(inner), growth)
    stub = api.loft([api.profile_from_port(branch_end), inner_profile], solid=True, ruled=True)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def build_tee_radius(context):
    return _radius_tee(context, 0.4, 0.6)


def build_tee_mitered(context):
    return _star_tee(context, 0.60, 0.60)


def build_tee_mitered_shoe(context):
    """Like ``build_tee_radius``, but the branch leans into the run via a
    flat mitre cut (``_mitered_bend`` with a single joint) instead of a
    smooth swept arc -- the branch-topology analogue of
    ``build_elbow_mitered``. TrimRunA/TrimRunB/TrimBranch are each
    independently settable, with the same floor/extension treatment
    ``_radius_tee`` applies (the run leg the branch leans toward can't be
    shorter than the mitre's own tangent point; the branch leg extends
    past it via ``_extend_leg`` on request)."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Tee fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)
    run_a_size, run_b_size = _size(api, run_a), _size(api, run_b)
    branch_size = _size(api, branch)
    trim_a, trim_b = _run_trims(api, p, run_a, run_b, 0.4)
    trim_branch = _positive(p.get("TrimBranch"), 0.6 * branch_size)
    radius = _positive(p.get("BranchRadius"), 0.6 * branch_size)
    radius = max(radius, 0.5 * branch_size)
    cuts = max(int(p.get("NumberOfCuts", 1) or 1), 1)

    trunk_center = (api.port_position(run_a) + api.port_position(run_b)) / 2.0
    lean = _lean_port(api, run_a, run_b, branch)
    trunk_axis_port = api.copy_port(branch, position=trunk_center, direction=api.port_direction(lean))
    bend_shape, trims = _mitered_bend(api, branch, trunk_axis_port, radius, cuts)
    branch_route_trim, lean_route_trim = trims
    if lean is run_a:
        trim_a = max(trim_a, lean_route_trim + 0.2 * run_a_size)
    else:
        trim_b = max(trim_b, lean_route_trim + 0.2 * run_b_size)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    bend_shape = _extend_leg(api, bend_shape, branch, branch_route_trim, trim_branch)
    trim_branch = max(trim_branch, branch_route_trim)

    return {
        "shape": api.refine(api.fuse(trunk, bend_shape)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def build_lateral_tee(context):
    return _star_tee(context, 0.65, 0.65)


def build_wye(context):
    return _star_junction(context, 0.70)


def _star_wye(context, factor, growth_factor=None):
    """Like ``_star_junction``, but each of a wye's three legs reads its
    own independent TrimBranchA/TrimBranchB/TrimBranchC property (in
    ``connected_ports(context)`` order -- a wye has no run/branch
    distinction, unlike a tee's TrimRunA/TrimRunB/TrimBranch, so there's no
    meaningful role to name each leg after beyond its position).

    When ``growth_factor`` is given, each leg's hub-facing end is also
    flared (grown via ``offset_profile``, by property ``FilletRadius``)
    instead of staying the leg's own size -- a true wye has no single
    collinear trunk to bend a leg into (unlike a tee -- see
    ``_radius_tee``), so "radius" here means every leg widens into a
    rounder, blended hub instead of tapering to a sharp-crotch
    intersection.
    """
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    p = _props(context)
    names = ("TrimBranchA", "TrimBranchB", "TrimBranchC")
    trims = [_positive(p.get(name), factor * _size(api, port)) for name, port in zip(names, ports)]
    center = sum((api.port_position(port) for port in ports), api.vec((0, 0, 0))) / len(ports)
    growth = None
    if growth_factor is not None:
        growth = _positive(p.get("FilletRadius"), growth_factor * max(_size(api, port) for port in ports))
    legs = []
    for port, trim in zip(ports, trims):
        end = _trimmed(api, port, trim)
        hub = api.copy_port(port, position=center, direction=api.port_direction(port) * -1.0)
        hub_profile = api.profile_from_port(hub)
        if growth is not None:
            hub_profile = api.offset_profile(hub_profile, growth)
        legs.append(api.loft([api.profile_from_port(end), hub_profile], solid=True, ruled=True))
    return {
        "shape": api.refine(api.fuse(*legs)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(list(zip(ports, trims))),
    }


def build_wye_mitered(context):
    return _star_wye(context, 0.70)


def build_wye_radius(context):
    return _star_wye(context, 0.70, growth_factor=0.35)


def build_tap_straight(context):
    return _straight_tap(context, 0.3, 0.5)


def build_tap_saddle(context):
    return _saddle_tap(context, 0.3, 0.5)


def build_tap_shoe(context):
    return _radius_tee(context, 0.3, 0.75)


def build_cross(context):
    return _star_junction(context, 0.60)


def build_manifold(context):
    return _star_junction(context, 0.65)


def build_straight(context):
    """Plain duct-to-duct connection: no bend, no special fitting.

    Two same-profile, same-size ports become a short coupling sleeve; two
    differently-sized/profiled ports fall back to a transition, since the
    "through.straight" family key is purely about the ports being
    collinear with zero eccentricity (see TOPOLOGY_CLASSIFICATION.md) --
    it says nothing about whether their sizes actually match.
    """
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    s0, s1 = api.port_section_params(ports[0]), api.port_section_params(ports[1])
    if api.port_profile(ports[0]) != api.port_profile(ports[1]) or s0 != s1:
        return build_transition(context)
    return _inline(context, 0.2, 30.0)


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
