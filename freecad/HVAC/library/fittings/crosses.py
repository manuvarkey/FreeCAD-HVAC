# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Crosses: a continuous straight run with two branches leaving it, one on
each side (four ports).
"""

from ._common import (
    _clip_junction_to_body,
    _finish,
    _junction_minimums,
    _junction_trims,
    _loft,
    _port_axis_point,
    _props,
    _size,
    _trimmed,
)

_CROSS_TRIM_NAMES = ("TrimRunA", "TrimRunB", "TrimBranchA", "TrimBranchB")
_CROSS_ROLES = ("RunA", "RunB", "BranchA", "BranchB")


def _cross_tee_layout(context, run_factor=0.60, branch_factor=0.60):
    """
    Ports and trims of a straight cross. The run is the classifier's own
    collinear pair (api.port_roles), the other two ports are the branches.
    Each trim is the length needed to clear the other ports' sections at
    the centre, plus that port's own Trim<Role> property.
    """
    api = context["hvac_api"]
    roles = api.port_roles(context)
    if set(roles) != {"run_a", "run_b", "branch_a", "branch_b"}:
        raise ValueError("A straight cross needs four ports, two of them forming a straight run")
    ports = [roles["run_a"], roles["run_b"], roles["branch_a"], roles["branch_b"]]

    center = api.center_from_context(context)
    minimums = _junction_minimums(api, center, ports)
    factors = (run_factor, run_factor, branch_factor, branch_factor)
    trims = _junction_trims(api, _props(context), ports, _CROSS_TRIM_NAMES, factors, minimums)
    return ports, center, trims


def _cross_lengths(api, ports, trims):
    return api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))


def measure_cross_tee_straight(context):
    api = context["hvac_api"]
    ports, _center, trims = _cross_tee_layout(context)
    return _cross_lengths(api, ports, trims)


def build_cross_tee_straight(context):
    """
    Straight cross: one continuous trunk between the two run ports, and a
    straight stub for each branch, joined at the trunk.

    Meant for branches square to the run. The stubs are cut correctly at
    any angle, but the run trims only allow for a square branch's
    footprint, so a strongly inclined branch can reach past the run end.
    """
    api = context["hvac_api"]
    ports, center, trims = _cross_tee_layout(context)
    run_a, run_b = ports[0], ports[1]
    trunk_dir = api.unit(api.port_direction(run_a))

    # Step 1: per branch, the stub's two ends and the plane it is cut on.
    stubs = []
    for branch, trim in zip(ports[2:], trims[2:]):
        branch_dir = api.unit(api.port_direction(branch))
        axis_center = _port_axis_point(api, branch, center)
        # The cut plane contains the trunk axis and faces the branch, so an
        # inclined branch is cut flush with the trunk's mid-plane too.
        plane_normal = api.unit(api.unit(trunk_dir.cross(branch_dir)).cross(trunk_dir))
        # Run the stub slightly past the trunk axis: stopping exactly on it
        # leaves a knife edge after the cut, which the fuse can fail on.
        embed = max(0.01 * _size(api, branch), 2.0)
        inner = api.copy_port(branch, position=axis_center - branch_dir * embed)
        stubs.append((_trimmed(api, branch, trim), inner, axis_center, plane_normal))

    def envelope(offset):
        # Step 2: the continuous trunk.
        shape = _loft(api, [_trimmed(api, run_a, trims[0]), _trimmed(api, run_b, trims[1])], offset)
        # Step 3: each branch stub, cut at the trunk mid-plane and kept inside the run body.
        for outer, inner, axis_center, plane_normal in stubs:
            stub = _loft(api, [outer, inner], offset)
            stub = api.clip_plane(stub, (axis_center, plane_normal), side="positive")
            stub = _clip_junction_to_body(api, stub, center, [run_a, run_b], trims[:2])
            shape = api.fuse(shape, stub)
        return shape

    port_roles = {port.get("edge_key"): role for port, role in zip(ports, _CROSS_ROLES)}
    return _finish(context, envelope, _cross_lengths(api, ports, trims), port_roles)
