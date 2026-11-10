# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Star fittings: every port lofted straight to a shared centre. The generic
fallback shape for branch, cross and multiport nodes.
"""

from ._common import (
    _clip_branch_at_trunk_center,
    _clip_junction_to_body,
    _finish,
    _loft,
    _port_axis_point,
    _positive,
    _props,
    _size,
    _trimmed,
)

# Per-port trim property names for star fittings whose ports have no
# run/branch role. Letters follow the order of connected_ports.
_PORT_TRIM_NAMES = ("TrimPortA", "TrimPortB", "TrimPortC", "TrimPortD")


def _star_trims(p, names, default_trim):
    """
    One trim length per port of a star fitting.

    Each port uses its own named property when that is set. Otherwise it
    falls back to the shared TrimLength, and then to default_trim. A port
    with no name (None, e.g. the 5th port of a multiport) only has the
    shared fallback.
    """
    shared = _positive(p.get("JunctionLength", p.get("TrimLength")), default_trim)
    return [_positive(p.get(name), shared) if name else shared for name in names]


def _port_trim_names(count):
    """TrimPortA.. for the first ports, None for any port beyond the named ones."""
    return [_PORT_TRIM_NAMES[i] if i < len(_PORT_TRIM_NAMES) else None for i in range(count)]


def _star_junction_layout(context, default_factor):
    api = context["hvac_api"]
    ports = api.connected_ports(context)
    if len(ports) < 3:
        raise ValueError("Branch junction requires at least three connected ports")
    p = _props(context)
    center = api.average_point([api.port_position(port) for port in ports])
    default_trim = max(_size(api, port) for port in ports) * default_factor
    trims = _star_trims(p, _port_trim_names(len(ports)), default_trim)
    return ports, center, trims


def measure_star_junction(context, default_factor=0.6):
    api = context["hvac_api"]
    ports, center, trims = _star_junction_layout(context, default_factor)
    return api.build_trim_rec_from_port_lengths(list(zip(ports, trims)))


def _star_junction(context, default_factor=0.6, align_branch=False):
    api = context["hvac_api"]
    ports, center, trims = _star_junction_layout(context, default_factor)
    branch = None
    if align_branch:
        pairs = api.collinear_port_index_pairs(context)
        if pairs:
            _, _, branch = api.run_branch_ports(context)
            center = api.center_from_context(context)
    trimmed = [_trimmed(api, port, trim) for port, trim in zip(ports, trims)]

    def envelope(offset):
        legs = []
        for source_port, port in zip(ports, trimmed):
            inner_position = _port_axis_point(api, source_port, center) if source_port is branch else center
            center_port = api.copy_port(port, position=inner_position, direction=api.port_direction(port) * -1.0)
            leg = _loft(api, [port, center_port], offset, ruled=True)
            if source_port is branch:
                leg = _clip_branch_at_trunk_center(api, leg, center, branch)
            legs.append(leg)
        return api.fuse(*legs)

    return _finish(context, envelope, api.build_trim_rec_from_port_lengths(list(zip(ports, trims))))


def measure_branch_generic(context):
    return measure_star_junction(context, 0.60)


def measure_cross(context):
    return measure_star_junction(context, 0.60)


def measure_manifold(context):
    return measure_star_junction(context, 0.65)


def _star_body(api, center, ports, trims, minimums, branch=None, offset=0.0):
    """Build robust straight-legged junction geometry with controlled overlap."""
    legs = []
    for port, trim, minimum in zip(ports, trims, minimums):
        direction = api.unit(api.port_direction(port))
        end = _trimmed(api, port, trim)
        embed = max(0.10 * minimum, 1.0)
        inner_center = _port_axis_point(api, port, center) if port is branch else center
        inner = api.copy_port(port, position=inner_center - direction * embed)
        leg = _loft(api, [end, inner], offset, ruled=True)
        if port is branch:
            leg = _clip_branch_at_trunk_center(api, leg, center, branch)
        legs.append(leg)
    shape = api.fuse(*legs)
    return _clip_junction_to_body(api, shape, center, ports, trims)


def build_branch_generic(context):
    return _star_junction(context, 0.60, align_branch=True)


def build_cross(context):
    return _star_junction(context, 0.60)


def build_manifold(context):
    return _star_junction(context, 0.65)
