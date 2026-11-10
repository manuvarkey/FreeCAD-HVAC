# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
End-of-duct fittings: the generic terminal body and the duct closure (end cap).
"""

from ._common import _positive, _props, _size
from .markers import _marker


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
