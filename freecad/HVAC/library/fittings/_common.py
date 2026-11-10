# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Small helpers shared by every fitting module: property access, port sizing,
trim arithmetic, leg clipping and the mitered-bend builder.

Most fittings in this package are split into a small measurement function
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
    return api.copy_port(port, position=api.port_position(port) + api.port_direction(port) * float(length))


def _port_axis_point(api, port, point):
    """Project a point onto the infinite axis defined by a port."""
    position = api.port_position(port)
    direction = api.unit(api.port_direction(port))
    return position + direction * ((api.vec(point) - position).dot(direction))


def _clip_branch_at_trunk_center(api, shape, trunk_center, branch):
    """Keep the branch-facing half of a stub at the trunk center plane."""
    return api.clip_plane(shape, (trunk_center, api.port_direction(branch)), side="positive")


def _loft(api, ports, offset=0.0, ruled=True):
    return api.loft([api.profile_from_port(p, offset) for p in ports], solid=True, ruled=ruled)


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

    key_a = (str(run_a.get("edge_key", "") or ""), str(run_a.get("segment_end", "") or ""))
    key_b = (str(run_b.get("edge_key", "") or ""), str(run_b.get("segment_end", "") or ""))

    if key_a == ("", "") or key_b == ("", ""):
        raise ValueError("Symmetric tee bend direction requires profile_x_axis or stable run edge keys")

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
