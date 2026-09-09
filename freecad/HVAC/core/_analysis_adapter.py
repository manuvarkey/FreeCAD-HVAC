# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

################################################################################
#                                                                              #
#   Copyright (c) 2026 Francisco Rosa                                          #
#                                                                              #
#   This addon is free software; you can redistribute it and/or modify it      #
#   under the terms of the GNU Lesser General Public License as published      #
#   by the Free Software Foundation; either version 2.1 of the License, or     #
#   (at your option) any later version.                                        #
#                                                                              #
#   This addon is distributed in the hope that it will be useful,              #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                       #
#                                                                              #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with this addon. If not, see https://www.gnu.org/licenses    #
#                                                                              #
################################################################################

"""
The one place a real FreeCAD DuctNetwork gets read into the pure
analysis/model.py dataclasses -- shared by FlowNetwork.py/AirflowSolver.py/
DuctSizer.py, so the "resolve a library type, build a properties dict, call
its loss formula" logic (previously copy-pasted between AirflowSolver.py's
and DuctSizer.py's own junction-loss code) lives in exactly one place.

Everything downstream of build_network_model() (analysis/flow.py,
pressure.py, sizing.py, paths.py, balancing.py) only ever sees plain
dataclasses/strings/callables -- never a FreeCAD object, a Proxy, a Shape,
or a library type-def.
"""

import json
import math
import re

from ..analysis.model import ComponentModel, NetworkModel, NodeModel, PortModel, SectionModel, SegmentModel, AirState
from ..library.library_api import HVACLibraryAPI
from ..utils import hvaclib
from .Construction import construction_for

# A segment's own RectangularSizingMode enum value ("UseNetworkDefault" means
# "no override") -> the plain mode string SegmentModel/SizingSettings use.
RECT_MODE_MAP = {
    "FixedAspectRatio": "aspect_ratio",
    "FixedHeight": "fixed_height",
    "FixedWidth": "fixed_width",
}


def element_identifier(obj):
    """User-facing element reference: documentation Number, then Label/Name."""
    number = str(getattr(obj, "Number", "") or "").strip()
    label = str(getattr(obj, "Label", "") or "").strip()
    name = str(getattr(obj, "Name", "") or "").strip()
    return number or label or name


def humanize_diagnostics(messages, segment_map, junction_map, component_map=None):
    """Replace internal edge/node/component keys in diagnostics with element IDs."""
    aliases = {}
    for mapping in (segment_map, junction_map, component_map or {}):
        for internal, obj in mapping.items():
            display = element_identifier(obj)
            if display and display != str(internal):
                aliases[str(internal)] = display
    patterns = [
        (re.compile(r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])".format(re.escape(key))), value)
        for key, value in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True)
    ]
    out = []
    for message in messages:
        text = str(message)
        for pattern, value in patterns:
            text = pattern.sub(value, text)
        out.append(text)
    return out


def build_network_model(net_obj):
    """
    Returns (network_model, segment_map, junction_map, component_map) --
    segment_map/junction_map/component_map keyed exactly like
    network_model's own edge_key/node_id/ComponentModel.component_id, so a
    caller can look up the real FreeCAD object for any pure result directly
    (e.g. to write Calc* properties back onto it). component_map covers
    every node's Primary and every edge's own Inline chain components.
    """
    parser = net_obj.Proxy.getParser(rebuild=True)
    segment_map = net_obj.Proxy.collectSegmentObjects()
    junction_map = net_obj.Proxy.collectJunctionObjects()
    reg = hvaclib.HVACLibraryService.get_hvac_library_registry()

    air = AirState(
        density_kg_m3=float(getattr(net_obj, "AirDensity", 1.204) or 1.204),
        kinematic_viscosity_m2_s=float(getattr(net_obj, "AirKinematicViscosity", 1.51e-5) or 1.51e-5),
    )
    default_roughness_mm = float(getattr(net_obj, "DefaultRoughness", 0.09) or 0.0)

    edges = {}
    for u, v, edge_ref in parser.analysis_graph.edges(data="key"):
        edges[edge_ref.tag] = (parser.node_key(u), parser.node_key(v))

    segments = {}
    for edge_key, seg_obj in segment_map.items():
        segments[edge_key] = _build_segment_model(seg_obj, default_roughness_mm)

    nodes = {}
    component_map = {}
    for node_id in parser.analysis_graph.nodes():
        if parser.analysis_graph.degree[node_id] == 0:
            continue  # isolated node, never reachable from any edge -- see flow.py
        node_key = parser.node_key(node_id)
        junction_obj = junction_map.get(node_key)
        if junction_obj is None:
            continue  # missing junction data -- analysis.flow reports this per-component, not fatal here
        ja = parser.build_junction_analysis(node_id, segment_map)
        if ja is None:
            continue
        nodes[node_key] = _build_node_model(
            node_key,
            junction_obj,
            ja,
            segment_map,
            reg,
            air,
            component_map,
            default_roughness_mm,
        )

    network = NetworkModel(nodes=nodes, segments=segments, edges=edges, air=air)
    return network, segment_map, junction_map, component_map


# ----------------------------------------------------------------------------
# FreeCAD object -> pure dataclass
# ----------------------------------------------------------------------------

def _section_from_params(profile, section_params):
    section_params = section_params or {}
    return SectionModel(
        profile=str(profile or ""),
        diameter_mm=float(section_params.get("Diameter", 0.0) or 0.0),
        width_mm=float(section_params.get("Width", 0.0) or 0.0),
        height_mm=float(section_params.get("Height", 0.0) or 0.0),
    )


def _build_segment_model(seg_obj, default_roughness_mm):
    section = _section_from_params(getattr(seg_obj, "Profile", ""), hvaclib.get_segment_section_params(seg_obj))

    # The material on the flow-surface layer is authoritative. The network
    # default exists only for an unassigned material or an older card that
    # does not expose HydraulicRoughness.
    roughness_mm = float(
        construction_for(seg_obj).hydraulic_roughness(default_roughness_mm)
    )

    rect_mode_raw = str(getattr(seg_obj, "RectangularSizingMode", "UseNetworkDefault") or "UseNetworkDefault")
    rect_mode_override = RECT_MODE_MAP.get(rect_mode_raw, "") if rect_mode_raw != "UseNetworkDefault" else ""

    edge_key = getattr(seg_obj, "SegmentKey", "") or seg_obj.Name
    return SegmentModel(
        edge_key=edge_key, section=section,
        length_mm=float(getattr(seg_obj, "EffectiveLength", 0.0) or 0.0),
        roughness_mm=roughness_mm,
        velocity_override_ms=float(getattr(seg_obj, "Velocity", 0.0) or 0.0),
        rectangular_mode_override=rect_mode_override,
        aspect_ratio_override=float(getattr(seg_obj, "TargetAspectRatio", 0.0) or 0.0),
    )


def _junction_ports_to_models(connected_ports, node_key):
    return [
        PortModel(
            edge_key=jp.edge_key, node_id=node_key, flow_into_node=jp.flow_into_junction,
            section=_section_from_params(jp.profile, jp.section_params), is_real_edge=True,
        )
        for jp in connected_ports
    ]


def _local_ports_to_models(local_ports_json, node_key, segment_map):
    ports = json.loads(local_ports_json or "[]")
    return [
        PortModel(
            edge_key=p.get("edge_key", ""), node_id=node_key, flow_into_node=p.get("flow_into_junction"),
            section=_section_from_params(p.get("profile", ""), p.get("section_params", {})),
            is_real_edge=p.get("edge_key", "") in segment_map,
        )
        for p in ports
    ]


def _stable_component_id(comp_obj):
    """
    A real DuctComponent always has a unique .Name -- fall back to Python's
    own object identity for anything that doesn't (e.g. a lightweight test
    double), so two distinct components can never collide onto the same
    key in component_map/analysis.pressure's per-component results.
    """
    name = getattr(comp_obj, "Name", "")
    return name if name else "id:{}".format(id(comp_obj))


def _build_node_model(
    node_key,
    junction_obj,
    ja,
    segment_map,
    reg,
    air,
    component_map,
    default_roughness_mm,
):
    ports = _junction_ports_to_models(ja.connected_ports, node_key)
    design_flow_lps = float(getattr(junction_obj, "DesignFlowRate", 0.0) or 0.0)
    flow_boundary = str(getattr(junction_obj, "FlowBoundary", "Auto") or "Auto")

    primary_obj = junction_obj.Proxy.getPrimaryComponent()
    primary_component = None
    if primary_obj is not None:
        component_id = _stable_component_id(primary_obj)
        construction = construction_for(primary_obj)
        roughness_mm = float(construction.hydraulic_roughness(default_roughness_mm))
        primary_component = ComponentModel(
            component_id=component_id,
            role="primary",
            ports=_local_ports_to_models(getattr(primary_obj, "LocalPortsJson", "[]"), node_key, segment_map),
            loss_evaluator=build_loss_evaluator(
                reg,
                primary_obj,
                air,
                family=getattr(junction_obj, "Family", ""),
                construction=construction,
                hydraulic_roughness_mm=roughness_mm,
                flow_class=ja.flow_class,
                qualifiers=ja.qualifiers,
                derived_values=ja.derived_values,
            ),
            roughness_mm=roughness_mm,
        )
        component_map[component_id] = primary_obj

    inline_chains = {}
    for edge_key, chain in (junction_obj.Proxy.getPortChains() or {}).items():
        models = []
        for comp_obj in chain:
            component_id = _stable_component_id(comp_obj)
            construction = construction_for(comp_obj)
            roughness_mm = float(construction.hydraulic_roughness(default_roughness_mm))
            models.append(ComponentModel(
                component_id=component_id,
                role="inline",
                ports=_local_ports_to_models(getattr(comp_obj, "LocalPortsJson", "[]"), node_key, segment_map),
                # An Inline component has no Family of its own.
                loss_evaluator=build_loss_evaluator(
                    reg,
                    comp_obj,
                    air,
                    family="",
                    construction=construction,
                    hydraulic_roughness_mm=roughness_mm,
                ),
                roughness_mm=roughness_mm,
            ))
            component_map[component_id] = comp_obj
        inline_chains[edge_key] = models

    return NodeModel(
        node_id=node_key, topology=ja.topology, degree=ja.degree, ports=ports,
        design_flow_lps=design_flow_lps, flow_boundary=flow_boundary,
        primary_component=primary_component, inline_chains=inline_chains,
    )


def build_loss_evaluator(
    reg,
    comp_obj,
    air,
    family="",
    construction=None,
    hydraulic_roughness_mm=0.0,
    flow_class="",
    qualifiers=None,
    derived_values=None,
):
    """
    A pure callable closing over everything FreeCAD/library-specific this
    component needs to evaluate its own loss -- see analysis/model.py's
    LossEvaluator type. Returns None if this component's type can't be
    resolved at all (analysis/pressure.py's K_DEFAULT fallback then applies,
    exactly like an unresolved type did before this refactor).

    flow_class/qualifiers/derived_values (NetworkParser.JunctionAnalysis --
    see TOPOLOGY_CLASSIFICATION.md) are only ever meaningful for a node's
    Primary component (an Inline component has no flow classification of
    its own, same reasoning as `family` above), and drive
    HVACLibraryRegistry.call_loss()'s own resolve_loss_variant() so one
    physical fitting TypeId can carry several flow-dependent loss formulas
    (e.g. an ordinary vs. bullhead tee) without a separate TypeId per case.

    If the component's own LossCoefficientSource is "Custom", the library's
    loss formula is bypassed entirely in favor of user-supplied per-port K
    values -- see _build_custom_loss_evaluator().
    """
    if str(getattr(comp_obj, "LossCoefficientSource", "Library") or "Library") == "Custom":
        return _build_custom_loss_evaluator(comp_obj)

    library_id = getattr(comp_obj, "LibraryId", "")
    type_id = getattr(comp_obj, "TypeId", "")
    type_def = reg.resolve_type(library_id, type_id) if library_id and type_id else None
    if type_def is None:
        return None

    properties = {}
    for pdef in getattr(type_def, "properties", []) or []:
        if hasattr(comp_obj, pdef.name):
            properties[pdef.name] = getattr(comp_obj, pdef.name)
        else:
            properties[pdef.name] = getattr(pdef, "default", None)

    local_ports = json.loads(getattr(comp_obj, "LocalPortsJson", "") or "[]")
    center_point = (
        HVACLibraryAPI.average_point([p["position"] for p in local_ports])
        if local_ports else (0.0, 0.0, 0.0)
    )

    def evaluate(port_velocities):
        connected_ports_ctx = []
        for p in local_ports:
            p = dict(p)
            v = port_velocities.get(p.get("edge_key"), {})
            p["velocity_ms"] = v.get("velocity_ms", 0.0)
            p["flow_rate_lps"] = v.get("flow_lps", 0.0)
            p["reynolds"] = v.get("reynolds", 0.0)
            connected_ports_ctx.append(p)

        context = {
            "obj": comp_obj,
            "construction": construction,
            "hydraulic_roughness_mm": float(hydraulic_roughness_mm),
            "center_point": center_point,
            "properties": properties,
            "connected_ports": connected_ports_ctx,
            "family": family,
            "flow_class": flow_class,
            "qualifiers": dict(qualifiers or {}),
            "derived_values": dict(derived_values or {}),
            "type_id": type_id,
            "library_id": library_id,
            "air_density": air.density_kg_m3,
            "air_kinematic_viscosity": air.kinematic_viscosity_m2_s,
        }
        return reg.call_loss(library_id, type_def, context)

    return evaluate


def applicable_loss_ports(local_ports):
    """
    The local ports CustomLossCoefficients needs one value for: a degree-1
    component's own single port (applied unconditionally, matching
    loss_api.py's own terminal_component_loss convention -- there's only
    ever one real duct connection to reference a loss against at all), or
    every outlet port (flow leaving the node into that edge) on a 2+-port
    component -- the same "fitting loss attributed at outlet ports only"
    convention build_loss_evaluator's own uniform/dict-K paths use, and
    the same shape loss_api.py's own branch_loss returns (an entry per
    downstream leg, never the inlet). An inlet port never receives a
    fitting-loss contribution at all, so it never needs (or gets) a
    CustomLossCoefficients slot either -- storing one for it would always
    be dead weight the solver could never actually apply. Which ports
    count as outlets is decided by each port's own flow_into_junction,
    already resolved for the current flow direction when LocalPortsJson
    was last composed -- Component.py's own _syncCustomLossCoefficients()
    re-derives this same set every sync, so it stays correct even if flow
    direction later changes.
    """
    if len(local_ports) == 1:
        return list(local_ports)
    return [p for p in local_ports if not p.get("flow_into_junction")]


def _build_custom_loss_evaluator(comp_obj):
    """
    LossCoefficientSource == "Custom" path: map CustomLossCoefficients onto
    each applicable port's own edge_key (see applicable_loss_ports()),
    entirely bypassing the library's own loss formula. CustomLossCoefficients
    and CustomLossCoefficientEdgeKeys are parallel lists -- the edge_key
    each K value belongs to is explicit, not positional, so this never
    depends on LocalPortsJson's own port order matching what's currently
    stored (Component.py's own sync keeps the two in step; this still
    validates it fresh here rather than trusting that blindly).

    Config is validated up front, not lazily inside the returned callable,
    so a bad Custom setup fails as soon as the network model is built --
    "fail clearly during analysis" per the feature's own contract, the same
    way library/validation.py raises ValueError for a malformed type-def
    property rather than silently falling back to a default.
    """
    local_ports = json.loads(getattr(comp_obj, "LocalPortsJson", "") or "[]")
    custom_k = list(getattr(comp_obj, "CustomLossCoefficients", None) or [])
    stored_edge_keys = list(getattr(comp_obj, "CustomLossCoefficientEdgeKeys", None) or [])
    label = element_identifier(comp_obj)

    expected_edge_keys = []
    for port in applicable_loss_ports(local_ports):
        edge_key = port.get("edge_key")
        if not edge_key:
            raise ValueError(
                "Component '{}': an applicable local port has no edge_key -- can't map its custom K.".format(label)
            )
        expected_edge_keys.append(edge_key)

    if len(custom_k) != len(stored_edge_keys):
        raise ValueError(
            "Component '{}': CustomLossCoefficients has {} value(s) but CustomLossCoefficientEdgeKeys has {} "
            "-- they must match 1:1.".format(label, len(custom_k), len(stored_edge_keys))
        )

    if set(stored_edge_keys) != set(expected_edge_keys) or len(stored_edge_keys) != len(expected_edge_keys):
        raise ValueError(
            "Component '{}': CustomLossCoefficients is out of sync with this component's current ports "
            "(expected K for {}, has K for {}) -- recompute the network to resync it.".format(
                label, sorted(expected_edge_keys), sorted(stored_edge_keys)
            )
        )

    for edge_key, raw_k in zip(stored_edge_keys, custom_k):
        try:
            k_value = float(raw_k)
        except (TypeError, ValueError):
            k_value = float("nan")
        if not math.isfinite(k_value) or k_value < 0.0:
            raise ValueError(
                "Component '{}': custom loss coefficient for port '{}' is '{}' -- K must be finite "
                "and >= 0.".format(label, edge_key, raw_k)
            )

    result = {edge_key: float(k) for edge_key, k in zip(stored_edge_keys, custom_k)}

    def evaluate(port_velocities):
        # Build {edge_key: K}, same contract pressure.py already consumes
        # for a library loss result -- already exactly right, no further
        # direction-based filtering needed (that's baked into how
        # expected_edge_keys/stored_edge_keys were derived above).
        return dict(result)

    return evaluate
