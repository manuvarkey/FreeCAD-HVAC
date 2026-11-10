# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Taps: a branch collar attached to the surface of a continuous run
(straight, saddle and shoe taps).
"""

from ._common import (
    _clip_branch_at_trunk_center,
    _extra_trim,
    _lean_port,
    _loft,
    _port_axis_point,
    _positive,
    _props,
    _size,
    _trimmed,
)


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


def _tap_trims(
    api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor
):
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

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(
        context, api, run_a, run_b, branch
    )
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.profile_from_port(surface_port)
    trim_a, trim_b, trim_branch = _tap_trims(
        api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor
    )

    return {
        "run_a": run_a,
        "run_b": run_b,
        "branch": branch,
        "trunk_center": trunk_center,
        "branch_dir": branch_dir,
        "base_position": base_position,
        "trim_a": trim_a,
        "trim_b": trim_b,
        "trim_branch": trim_branch,
    }


def measure_straight_tap(context, run_factor, branch_factor):
    api = context["hvac_api"]
    layout = _straight_tap_layout(context, run_factor, branch_factor)
    return api.build_trim_rec_from_port_lengths(
        [
            (layout["run_a"], layout["trim_a"]),
            (layout["run_b"], layout["trim_b"]),
            (layout["branch"], layout["trim_branch"]),
        ]
    )


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

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(
        context, api, run_a, run_b, branch
    )
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    growth = _positive(p.get("SaddleGrowth"), flare_factor * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.offset_profile(api.profile_from_port(surface_port), growth)

    trim_a, trim_b, trim_branch = _tap_trims(
        api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, run_factor, branch_factor
    )

    return {
        "run_a": run_a,
        "run_b": run_b,
        "branch": branch,
        "trunk_center": trunk_center,
        "branch_dir": branch_dir,
        "tap_top": tap_top,
        "base_position": base_position,
        "growth": growth,
        "overlap": overlap,
        "tap_height": tap_height,
        "surface_profile": surface_profile,
        "trim_a": trim_a,
        "trim_b": trim_b,
        "trim_branch": trim_branch,
    }


def measure_saddle_tap(context, run_factor, branch_factor, flare_factor=0.6):
    api = context["hvac_api"]
    layout = _saddle_tap_layout(context, run_factor, branch_factor, flare_factor)
    return api.build_trim_rec_from_port_lengths(
        [
            (layout["run_a"], layout["trim_a"]),
            (layout["run_b"], layout["trim_b"]),
            (layout["branch"], layout["trim_branch"]),
        ]
    )


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

    trunk_center, branch_dir, toe_dir, branch_width, run_depth, overlap, run_surface = _tap_geometry(
        context, api, run_a, run_b, branch
    )
    tap_height = _positive(p.get("TapHeight"), 0.5 * branch_width)
    tap_top = run_surface + branch_dir * tap_height
    base_position = run_surface - branch_dir * overlap

    surface_port = api.copy_port(branch, position=run_surface)
    surface_profile = api.stretch_profile_one_sided(api.profile_from_port(surface_port), toe_dir, tap_height)

    trim_a, trim_b, trim_branch = _tap_trims(
        api, p, run_a, run_b, branch, trunk_center, branch_dir, tap_top, surface_profile, 0.3, 0.5
    )

    return {
        "run_a": run_a,
        "run_b": run_b,
        "branch": branch,
        "trunk_center": trunk_center,
        "branch_dir": branch_dir,
        "toe_dir": toe_dir,
        "tap_top": tap_top,
        "base_position": base_position,
        "overlap": overlap,
        "tap_height": tap_height,
        "surface_profile": surface_profile,
        "trim_a": trim_a,
        "trim_b": trim_b,
        "trim_branch": trim_branch,
    }


def measure_tap_shoe(context):
    api = context["hvac_api"]
    layout = _tap_shoe_layout(context)
    return api.build_trim_rec_from_port_lengths(
        [
            (layout["run_a"], layout["trim_a"]),
            (layout["run_b"], layout["trim_b"]),
            (layout["branch"], layout["trim_branch"]),
        ]
    )


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
