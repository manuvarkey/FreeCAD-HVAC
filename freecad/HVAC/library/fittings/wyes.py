# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Wyes: one main port splitting into two branch routes (mitered and radius).
"""

import math

from ._common import (
    _extend_leg,
    _junction_minimums,
    _junction_shape_minimums,
    _junction_trims,
    _loft,
    _mitered_bend,
    _port_axis_point,
    _props,
    _size,
    _trimmed,
)


def _wye_mitered_layout(context):
    """Lay out two split-profile faceted bends entering one common leg."""
    api = context["hvac_api"]
    p = _props(context)
    cuts = max(int(p.get("NumberOfCuts", 2) or 2), 1)

    # Reuse the same split plane and feasible elbow paths as the radius wye.
    ports, center, main_center, split_routes = _wye_split_route_layout(context)
    main = ports[0]
    main_direction = api.unit(api.port_direction(main))
    routes = []
    intrinsic_shapes = []

    for branch, branch_index, split_face, route in split_routes:
        split_port = api.copy_port(main_center, position=split_face.CenterOfMass)
        main_tangent = route["ports"][1]
        main_tangent_position = api.port_position(main_tangent)
        main_reach = main_tangent_position - split_face.CenterOfMass
        tangent_face = split_face
        split_shape = None
        if main_reach.Length > api.EPS:
            split_shape = api.extrude(split_face.OuterWire, main_reach, solid=True)
            tangent_face = api.section_face(split_shape, (main_tangent_position, api.port_direction(main_tangent)))

        bend_shape, route_trims = _mitered_bend(
            api, branch, split_port, route["radius"], cuts, profile1=tangent_face.OuterWire
        )

        routes.append(
            {
                "branch": branch,
                "branch_index": branch_index,
                "split_shape": split_shape,
                "bend_shape": bend_shape,
                "branch_trim": route_trims[0],
                "radius": route["radius"],
            }
        )
        intrinsic_shapes.extend((split_shape, bend_shape))

    intrinsic_shape = api.compound(intrinsic_shapes)

    minimums = _junction_minimums(api, center, ports)
    minimums = _junction_shape_minimums(api, center, ports, intrinsic_shape, minimums)
    main_split_trim = (api.port_position(main_center) - api.port_position(main)).dot(main_direction)
    minimums[0] = max(minimums[0], main_split_trim)

    for route in routes:
        branch_index = route["branch_index"]
        minimums[branch_index] = max(minimums[branch_index], route["branch_trim"])

    trims = _junction_trims(api, p, ports, ("TrimMain", "TrimBranchA", "TrimBranchB"), (0.70, 0.70, 0.70), minimums)

    return {
        "ports": ports,
        "center": center,
        "main": main,
        "main_center": main_center,
        "routes": routes,
        "trims": trims,
    }


def measure_wye_mitered(context):
    api = context["hvac_api"]
    layout = _wye_mitered_layout(context)

    return api.build_trim_rec_from_port_lengths(list(zip(layout["ports"], layout["trims"])))


def build_wye_mitered(context):
    """Mitered wye with each non-main branch entering the common leg through a faceted bend."""
    api = context["hvac_api"]
    layout = _wye_mitered_layout(context)

    ports = layout["ports"]
    main = layout["main"]
    main_center = layout["main_center"]
    trims = layout["trims"]

    main_end = _trimmed(api, main, trims[0])
    main_reach = api.port_position(main_end) - api.port_position(main_center)
    main_tolerance = max(api.EPS, 1.0e-6 * _size(api, main))
    shape = None
    if main_reach.Length > main_tolerance:
        shape = _loft(api, [main_end, main_center], 0.0, ruled=True)

    for route in layout["routes"]:
        branch = route["branch"]
        branch_index = route["branch_index"]

        bend_shape = _extend_leg(api, route["bend_shape"], branch, route["branch_trim"], trims[branch_index])

        shape = api.fuse(shape, route["split_shape"], bend_shape)

    validation = api.validate(shape, require_solid=True)
    if not validation["valid"] or validation["solid_count"] != 1:
        raise RuntimeError(
            "Mitered wye geometry could not be fused into one valid solid (requested radius {:.3f} mm; cuts {})".format(
                float(_props(context).get("BranchRadius") or 0.0),
                max(int(_props(context).get("NumberOfCuts", 2) or 2), 1),
            )
        )

    return {"shape": shape, "connection_lengths": api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))}


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

    key = (str(branch.get("edge_key", "") or ""), str(branch.get("segment_end", "") or ""))

    if key == ("", ""):
        raise ValueError("Wye branch ordering requires profile_x_axis or stable branch edge keys")

    return key


def _wye_port_roles(api, ports):
    """Return stable semantic ordering: common/main leg, branch A, branch B."""
    if len(ports) != 3:
        raise ValueError("Wye requires exactly three ports")

    main_index = _wye_common_index(api, ports)
    main = ports[main_index]
    branches = [ports[index] for index in range(3) if index != main_index]

    branches.sort(key=lambda branch: _wye_branch_sort_key(api, main, branch), reverse=True)
    return main, branches[0], branches[1]


def _wye_horizontal_extent(api, port):
    """Return the branch profile dimension along its horizontal axis."""
    profile = api.port_profile(port)
    if profile == "Circular":
        extent = float(api.port_diameter(port))
    elif profile in {"Rectangular", "Oval"}:
        extent = float(api.port_width(port))
    else:
        raise ValueError("Split-profile wye requires a circular, rectangular, or oval profile")

    if extent <= api.EPS:
        raise ValueError("Wye branch horizontal extent must be positive")
    return extent


def _wye_main_split_faces(api, main_center, branch_a, branch_b):
    """Split the main profile horizontally in proportion to the branches."""
    main_profile = api.profile_from_port(main_center)
    _, horizontal, _vertical, main_dir = api.make_profile_frame(
        api.port_direction(main_center), api.port_profile_x_axis(main_center), api.port_position(main_center)
    )

    # Keep the positive split face on branch A's side.
    branch_delta = api.unit(api.port_direction(branch_a)) - api.unit(api.port_direction(branch_b))
    branch_delta = branch_delta - main_dir * branch_delta.dot(main_dir)
    side = branch_delta.dot(horizontal)
    if abs(side) <= api.EPS:
        raise ValueError("Wye branches do not separate along the horizontal profile axis")
    if side < 0.0:
        horizontal = horizontal * -1.0

    branch_a_extent = _wye_horizontal_extent(api, branch_a)
    branch_b_extent = _wye_horizontal_extent(api, branch_b)
    ratio = branch_a_extent / (branch_a_extent + branch_b_extent)
    positive, negative = api.split_profile_face_by_extent(main_profile, horizontal, ratio)
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
        (api.port_position(route_port) - api.port_position(source_port)).dot(api.unit(api.port_direction(source_port)))
        for route_port, source_port in zip(route["ports"], (branch, split_port))
    ]
    deficit = clearance - signed_trims[0]
    if deficit > 0.0:
        theta = api.angle_between(api.port_direction(branch), api.port_direction(split_port))
        radius += deficit * math.tan(theta / 2.0)
        route = api.make_elbow_path(branch, split_port, radius)

    route["radius"] = radius
    return route


def _wye_split_route_layout(context):
    """Calculate shared port roles, split profiles, and feasible elbow paths."""
    api = context["hvac_api"]
    raw_ports = list(api.connected_ports(context))
    if len(raw_ports) != 3:
        raise ValueError("Fitting requires exactly three connected ports")

    main, branch_a, branch_b = _wye_port_roles(api, raw_ports)
    ports = [main, branch_a, branch_b]
    center = api.center_from_context(context)
    branch_legs = [(1, branch_a), (2, branch_b)]
    radius = float(_props(context).get("BranchRadius") or 0.0)

    # Step 1: Build provisional paths at the junction center.
    main_direction = api.unit(api.port_direction(main))
    main_axis_center = _port_axis_point(api, main, center)
    main_center = api.copy_port(main, position=main_axis_center)

    def build_routes(split_center):
        split_faces = _wye_main_split_faces(api, split_center, branch_a, branch_b)
        result = []
        for branch_index, branch in branch_legs:
            split_face = split_faces[id(branch)]
            split_port = api.copy_port(split_center, position=split_face.CenterOfMass)
            route = _wye_elbow_route(api, branch, split_port, radius)
            result.append((branch, branch_index, split_face, route))
        return result

    routes = build_routes(main_center)

    # Step 2: Large radii can put an elbow tangent beyond the split plane.
    # Move the plane outward so each split half approaches its arc from behind.
    main_shift = max(
        [0.0]
        + [
            (api.port_position(route["ports"][1]) - split_face.CenterOfMass).dot(main_direction)
            + _wye_route_clearance(api, branch)
            for branch, _, split_face, route in routes
        ]
    )
    if main_shift > api.EPS:
        main_center = api.copy_port(main_center, position=api.port_position(main_center) + main_direction * main_shift)
        routes = build_routes(main_center)

    return ports, center, main_center, routes


def _wye_radius_layout(context):
    """Calculate the split profiles, branch paths, and trims for a wye."""
    api = context["hvac_api"]
    p = _props(context)
    ports, center, main_center, routes = _wye_split_route_layout(context)
    main = ports[0]
    main_direction = api.unit(api.port_direction(main))

    # Include the relocated split plane in the main-leg trim.
    minimums = _junction_minimums(api, center, ports)
    main_split_trim = (api.port_position(main_center) - api.port_position(main)).dot(main_direction)
    minimums[0] = max(minimums[0], main_split_trim)
    for _branch, branch_index, _split_face, route in routes:
        minimums[branch_index] = max(minimums[branch_index], route["trim_lengths"][0])

    trims = _junction_trims(api, p, ports, ("TrimMain", "TrimBranchA", "TrimBranchB"), (0.70, 0.70, 0.70), minimums)
    return ports, main_center, routes, trims


def measure_wye_radius(context):
    api = context["hvac_api"]
    ports, _main_center, _routes, trims = _wye_radius_layout(context)
    return api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))


def build_wye_radius(context):
    """Build a radiused wye from a horizontally divided main profile."""
    api = context["hvac_api"]
    ports, main_center, routes, trims = _wye_radius_layout(context)

    # Sweep each split half of the main profile into its branch tangent.
    split_sweeps = []
    split_touch_sweeps = []
    branch_sweeps = []
    for branch, _branch_index, split_face, route in routes:
        main_tangent = route["ports"][1]
        main_tangent_position = api.port_position(main_tangent)
        main_reach = main_tangent_position - split_face.CenterOfMass
        tangent_face = split_face
        if main_reach.Length > api.EPS:
            route_overlap = 0.1 * _wye_route_clearance(api, branch)
            section_probe = api.extrude(split_face.OuterWire, main_reach, solid=True)
            probe = api.extrude(split_face.OuterWire, main_reach + api.unit(main_reach) * route_overlap, solid=True)
            tangent_face = api.section_face(section_probe, (main_tangent_position, api.port_direction(main_tangent)))
            split_sweeps.append(probe)
            split_touch_sweeps.append(section_probe)
        else:
            split_sweeps.append(None)
            split_touch_sweeps.append(None)

        branch_tangent = route["ports"][0]
        try:
            branch_sweeps.append(
                api.sweep(
                    [tangent_face.OuterWire, api.profile_from_port(branch_tangent)],
                    api.reverse(route["path"]),
                    solid=True,
                )
            )
        except Exception as exc:
            raise RuntimeError(
                "Wye branch sweep failed at effective radius {:.3f} mm: {}".format(route["radius"], exc)
            ) from exc

    # A small scaled overlap avoids a coplanar boolean at the split plane;
    # the full main profile still stops at the split approach itself.
    main_overlap = 0.1 * min(_wye_route_clearance(api, branch) for branch, _, _, _ in routes)
    main_trim_start = api.copy_port(
        main_center,
        position=(api.port_position(main_center) - api.unit(api.port_direction(main_center)) * main_overlap),
    )
    trim_starts = [main_trim_start] + [route["ports"][0] for _, _, _, route in routes]
    trim_sweeps = []
    for port, trim_start, trim in zip(ports, trim_starts, trims):
        trim_end = _trimmed(api, port, trim)
        trim_reach = api.port_position(trim_end) - api.port_position(trim_start)
        trim_tolerance = max(api.EPS, 1.0e-6 * _size(api, port))
        if trim_reach.Length <= trim_tolerance:
            trim_sweeps.append(None)
            continue
        trim_path = api.make_line(api.port_position(trim_start), api.port_position(trim_end))
        try:
            trim_sweeps.append(
                api.sweep([api.profile_from_port(trim_start), api.profile_from_port(trim_end)], trim_path, solid=True)
            )
        except Exception as exc:
            edge_key = str(port.get("edge_key", "") or "?")
            raise RuntimeError(
                "Wye trim sweep failed for port '{}' at {:.6f} mm: {}".format(edge_key, trim_reach.Length, exc)
            ) from exc

    main_trim = trim_sweeps[0]
    fuse_orders = []
    for connector_sweeps in (split_sweeps, split_touch_sweeps):
        leg_a = (connector_sweeps[0], branch_sweeps[0], trim_sweeps[1])
        leg_b = (connector_sweeps[1], branch_sweeps[1], trim_sweeps[2])
        fuse_orders.extend(
            (
                (main_trim, *leg_a, *leg_b),
                (main_trim, leg_a[0], leg_a[1], leg_b[0], leg_b[1], leg_a[2], leg_b[2]),
                (*leg_a, main_trim, *leg_b),
                (main_trim, *leg_b, *leg_a),
                (*connector_sweeps, *branch_sweeps, *trim_sweeps),
            )
        )

    # OCC booleans are order-sensitive when several swept solids share seams.
    # Keep the first sequence that produces exactly one valid solid.
    shape = None
    for fuse_order in fuse_orders:
        try:
            candidate = api.fuse(*fuse_order)
        except Exception:
            continue
        validation = api.validate(candidate, require_solid=True)
        if validation["valid"] and validation["solid_count"] == 1:
            shape = candidate
            break
    if shape is None:
        requested_radius = float(_props(context).get("BranchRadius") or 0.0)
        effective_radii = ", ".join("{:.3f}".format(route["radius"]) for _, _, _, route in routes)
        raise RuntimeError(
            "Wye geometry could not be fused into one valid solid "
            "(requested radius {:.3f} mm; effective branch radii {} mm)".format(requested_radius, effective_radii)
        )

    return {"shape": shape, "connection_lengths": api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))}
