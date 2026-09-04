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


def _star_body(api, center, ports, trims, minimums):
    """Build robust straight-legged junction geometry with controlled overlap."""
    legs = []
    for port, trim, minimum in zip(ports, trims, minimums):
        direction = api.unit(api.port_direction(port))
        end = _trimmed(api, port, trim)
        embed = max(0.10 * minimum, 1.0)
        inner = api.copy_port(port, position=center - direction * embed)
        legs.append(_loft(api, [end, inner], 0.0, ruled=True))
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

    branch_center = api.copy_port(branch, position=trunk_center)
    branch_profile = api.profile_from_port(branch_center)
    branch_min, branch_max = api.profile_projection_bounds(branch_profile, toe_dir)
    branch_width = max(branch_max - branch_min, 1.0)

    center_projection = trunk_center.dot(branch_dir)
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
    run_surface = trunk_center + branch_dir * run_surface_offset

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


def _straight_tap(context, run_factor, branch_factor):
    """Straight tap. TapHeight defines the intrinsic collar height;
    TrimBranch starts above TapHeight and run trims start beyond the
    minimum tap footprint."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(context, api, run_a, run_b, branch)
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.profile_from_port(surface_port)
    trim_a, trim_b, trim_branch = _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor)

    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)
    reach = max(0.0, (api.port_position(branch_end) - base_position).dot(branch_dir))
    stub = api.extrude(api.profile_from_port(branch_end), branch_dir * -reach, solid=True)
    stub = _clip_tap_to_run_body(api, stub, trunk_center, run_a, run_b, trim_a, trim_b)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _saddle_tap(context, run_factor, branch_factor, flare_factor=0.6):
    """Saddle tap. TapHeight defines the flare height; the embedded flare
    continues to the run mid-depth but is clipped to the tap body width."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(context, api, run_a, run_b, branch)
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    growth = _positive(p.get("SaddleGrowth"), flare_factor * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    top_port = api.copy_port(branch, position=tap_top)
    surface_port = api.copy_port(branch, position=run_surface)
    base_port = api.copy_port(branch, position=base_position)

    top_profile = api.profile_from_port(top_port)
    surface_profile = api.offset_profile(api.profile_from_port(surface_port), growth)
    base_growth = growth * (tap_height + overlap) / tap_height
    base_profile = api.offset_profile(api.profile_from_port(base_port), base_growth)

    trim_a, trim_b, trim_branch = _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor)
    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)

    profiles = [top_profile, surface_profile, base_profile]
    if (api.port_position(branch_end) - tap_top).Length > api.EPS:
        profiles.insert(0, api.profile_from_port(branch_end))
    stub = api.loft(profiles, solid=True, ruled=True)
    stub = _clip_tap_to_run_body(api, stub, trunk_center, run_a, run_b, trim_a, trim_b)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


def _star_tee(context, run_factor, branch_factor):
    """Straight-legged tee with trims measured beyond the intrinsic junction body."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)

    center = api.center_from_context(context)
    tee_ports = [run_a, run_b, branch]
    minimums = _junction_minimums(api, center, tee_ports)
    trims = _junction_trims(api, p, tee_ports, ("TrimRunA", "TrimRunB", "TrimBranch"), (run_factor, run_factor, branch_factor), minimums)
    shape = _star_body(api, center, tee_ports, trims, minimums)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }
    

def _radius_tee(context, run_factor, branch_factor):
    """Radiused tee with trims measured beyond the actual generated bend body."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)

    center = api.center_from_context(context)
    tee_ports = [run_a, run_b, branch]
    branch_size = _size(api, branch)
    radius = _positive(p.get("BranchRadius"), branch_factor * branch_size)
    radius = max(radius, 0.5 * branch_size)

    lean = _lean_port(api, run_a, run_b, branch)
    trunk_axis_port = api.copy_port(branch, position=center, direction=api.port_direction(lean))
    route = api.make_elbow_path(branch, trunk_axis_port, radius)
    branch_route_trim, lean_route_trim = route["trim_lengths"]
    stub = api.sweep([api.profile_from_port(route["ports"][0]), api.profile_from_port(route["ports"][1])], route["path"], solid=True)

    minimums = _junction_minimums(api, center, tee_ports)
    minimums = _junction_shape_minimums(api, center, tee_ports, stub, minimums)
    minimums[2] = max(minimums[2], branch_route_trim)
    lean_index = 0 if lean is run_a else 1
    minimums[lean_index] = max(minimums[lean_index], lean_route_trim)

    trims = _junction_trims(api, p, tee_ports, ("TrimRunA", "TrimRunB", "TrimBranch"), (run_factor, run_factor, branch_factor), minimums)
    stub = _extend_leg(api, stub, branch, branch_route_trim, trims[2])

    trunk = _loft(api, [_trimmed(api, run_a, trims[0]), _trimmed(api, run_b, trims[1])])
    shape = api.fuse(trunk, stub)
    shape = _clip_junction_to_body(api, shape, center, tee_ports, trims)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }

def _star_wye(context, factor=0.70):
    """Straight-legged wye with independent trims outside its minimum body."""
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    p = _props(context)

    center = api.center_from_context(context)
    minimums = _junction_minimums(api, center, ports)
    names = ("TrimBranchA", "TrimBranchB", "TrimBranchC")
    trims = _junction_trims(api, p, ports, names, (factor, factor, factor), minimums)
    shape = _star_body(api, center, ports, trims, minimums)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(list(zip(ports, trims))),
    }
    

def build_tee_radius(context):
    return _radius_tee(context, 0.4, 0.6)


def build_tee_mitered(context):
    return _star_tee(context, 0.60, 0.60)


def build_tee_mitered_shoe(context):
    """Mitered branch tee with trims outside the generated bend body."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Tee fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)

    center = api.center_from_context(context)
    tee_ports = [run_a, run_b, branch]
    branch_size = _size(api, branch)
    radius = _positive(p.get("BranchRadius"), 0.6 * branch_size)
    radius = max(radius, 0.5 * branch_size)
    cuts = max(int(p.get("NumberOfCuts", 1) or 1), 1)

    lean = _lean_port(api, run_a, run_b, branch)
    trunk_axis_port = api.copy_port(branch, position=center, direction=api.port_direction(lean))
    bend_shape, route_trims = _mitered_bend(api, branch, trunk_axis_port, radius, cuts)
    branch_route_trim, lean_route_trim = route_trims

    minimums = _junction_minimums(api, center, tee_ports)
    minimums = _junction_shape_minimums(api, center, tee_ports, bend_shape, minimums)
    minimums[2] = max(minimums[2], branch_route_trim)
    lean_index = 0 if lean is run_a else 1
    minimums[lean_index] = max(minimums[lean_index], lean_route_trim)

    trims = _junction_trims(api, p, tee_ports, ("TrimRunA", "TrimRunB", "TrimBranch"), (0.4, 0.4, 0.6), minimums)
    bend_shape = _extend_leg(api, bend_shape, branch, branch_route_trim, trims[2])

    trunk = _loft(api, [_trimmed(api, run_a, trims[0]), _trimmed(api, run_b, trims[1])])
    shape = api.fuse(trunk, bend_shape)
    shape = _clip_junction_to_body(api, shape, center, tee_ports, trims)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trims[0]), (run_b, trims[1]), (branch, trims[2])]
        ),
    }


def build_lateral_tee(context):
    return _star_tee(context, 0.65, 0.65)


def build_wye(context):
    return _star_wye(context, 0.70)


def build_wye_mitered(context):
    return _star_wye(context, 0.70)


def build_wye_radius(context):
    """Radiused wye with each trim measured beyond the actual generated body."""
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    p = _props(context)
    names = ("TrimBranchA", "TrimBranchB", "TrimBranchC")

    center = api.center_from_context(context)
    legs = sorted(((_size(api, port), names[i], i, port) for i, port in enumerate(ports)), key=lambda leg: leg[0], reverse=True)
    main_size, main_name, main_index, main = legs[0]
    branch_legs = legs[1:]

    smallest_branch_size = min(size for size, _, _, _ in branch_legs)
    radius = _positive(p.get("BranchRadius"), 0.6 * smallest_branch_size)
    radius = max(radius, 0.5 * smallest_branch_size)

    main_dir = api.unit(api.port_direction(main))
    routes = []
    route_shapes = []
    for branch_size, branch_name, branch_index, branch in branch_legs:
        branch_radius = max(radius, 0.5 * branch_size)
        trunk_axis_port = api.copy_port(branch, position=center, direction=main_dir)
        route = api.make_elbow_path(branch, trunk_axis_port, branch_radius)
        stub = api.sweep([api.profile_from_port(route["ports"][0]), api.profile_from_port(route["ports"][1])], route["path"], solid=True)
        routes.append((branch, branch_name, branch_index, branch_size, route, stub))
        route_shapes.append(stub)

    intrinsic_shape = api.fuse(*route_shapes)
    minimums = _junction_minimums(api, center, ports)
    minimums = _junction_shape_minimums(api, center, ports, intrinsic_shape, minimums)

    for branch, branch_name, branch_index, branch_size, route, stub in routes:
        minimums[branch_index] = max(minimums[branch_index], route["trim_lengths"][0])
        minimums[main_index] = max(minimums[main_index], route["trim_lengths"][1])

    trims = _junction_trims(api, p, ports, names, (0.70, 0.70, 0.70), minimums)

    main_end = _trimmed(api, main, trims[main_index])
    main_center = api.copy_port(main, position=center)
    shape = _loft(api, [main_end, main_center], 0.0, ruled=True)

    for branch, branch_name, branch_index, branch_size, route, stub in routes:
        stub = _extend_leg(api, stub, branch, route["trim_lengths"][0], trims[branch_index])
        shape = api.fuse(shape, stub)

    shape = _clip_junction_to_body(api, shape, center, ports, trims)

    return {
        "shape": api.refine(shape),
        "connection_lengths": api.build_trim_rec_from_port_lengths(list(zip(ports, trims))),
    }


def build_tap_straight(context):
    return _straight_tap(context, 0.3, 0.5)


def build_tap_saddle(context):
    return _saddle_tap(context, 0.3, 0.5)


def build_tap_shoe(context):
    """45-degree shoe tap with profile-independent geometry. TapHeight
    defines both the vertical shoe height and, at 45 degrees, the toe
    extension at the run surface."""
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")
    run_a, run_b, branch = _find_run_pair(context, api, ports)
    p = _props(context)

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(context, api, run_a, run_b, branch)
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    top_port = api.copy_port(branch, position=tap_top)
    surface_port = api.copy_port(branch, position=run_surface)
    base_port = api.copy_port(branch, position=base_position)

    top_profile = api.profile_from_port(top_port)
    surface_profile = api.stretch_profile_one_sided(api.profile_from_port(surface_port), toe_dir, tap_height)
    base_profile = api.stretch_profile_one_sided(api.profile_from_port(base_port), toe_dir, tap_height + overlap)

    trim_a, trim_b, trim_branch = _tap_trims(api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, 0.3, 0.5)
    trunk = _loft(api, [_trimmed(api, run_a, trim_a), _trimmed(api, run_b, trim_b)])
    branch_end = _trimmed(api, branch, trim_branch)

    profiles = [top_profile, surface_profile, base_profile]
    if (api.port_position(branch_end) - tap_top).Length > api.EPS:
        profiles.insert(0, api.profile_from_port(branch_end))
    stub = api.loft(profiles, solid=True, ruled=True)
    stub = _clip_tap_to_run_body(api, stub, trunk_center, run_a, run_b, trim_a, trim_b)

    return {
        "shape": api.refine(api.fuse(trunk, stub)),
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(run_a, trim_a), (run_b, trim_b), (branch, trim_branch)]
        ),
    }


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
