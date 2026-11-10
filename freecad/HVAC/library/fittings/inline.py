# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Inline devices: a simple body sitting between two collinear ports (damper, VAV box).
"""

from ._common import _loft, _positive, _props, _size, _trimmed


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
