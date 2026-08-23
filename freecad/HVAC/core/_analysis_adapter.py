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

from ..analysis.model import ComponentModel, NetworkModel, NodeModel, PortModel, SectionModel, SegmentModel, AirState
from ..library.library_api import HVACLibraryAPI
from ..utils import hvaclib

# A segment's own RectangularSizingMode enum value ("UseNetworkDefault" means
# "no override") -> the plain mode string SegmentModel/SizingSettings use.
RECT_MODE_MAP = {
    "FixedAspectRatio": "aspect_ratio",
    "FixedHeight": "fixed_height",
    "FixedWidth": "fixed_width",
}


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
    default_roughness_mm = float(getattr(net_obj, "DefaultRoughness", 0.0) or 0.0)

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
        nodes[node_key] = _build_node_model(node_key, junction_obj, ja, segment_map, reg, air, component_map)

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

    roughness_mm = float(getattr(seg_obj, "Roughness", 0.0) or 0.0)
    if roughness_mm <= 0.0:
        roughness_mm = default_roughness_mm

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


def _build_node_model(node_key, junction_obj, ja, segment_map, reg, air, component_map):
    ports = _junction_ports_to_models(ja.connected_ports, node_key)
    design_flow_lps = float(getattr(junction_obj, "DesignFlowRate", 0.0) or 0.0)

    primary_obj = junction_obj.Proxy.getPrimaryComponent()
    primary_component = None
    if primary_obj is not None:
        component_id = _stable_component_id(primary_obj)
        primary_component = ComponentModel(
            component_id=component_id,
            role="primary",
            ports=_local_ports_to_models(getattr(primary_obj, "LocalPortsJson", "[]"), node_key, segment_map),
            loss_evaluator=build_loss_evaluator(reg, primary_obj, air, family=getattr(junction_obj, "Family", "")),
        )
        component_map[component_id] = primary_obj

    inline_chains = {}
    for edge_key, chain in (junction_obj.Proxy.getPortChains() or {}).items():
        models = []
        for comp_obj in chain:
            component_id = _stable_component_id(comp_obj)
            models.append(ComponentModel(
                component_id=component_id,
                role="inline",
                ports=_local_ports_to_models(getattr(comp_obj, "LocalPortsJson", "[]"), node_key, segment_map),
                # An Inline component has no Family of its own.
                loss_evaluator=build_loss_evaluator(reg, comp_obj, air, family=""),
            ))
            component_map[component_id] = comp_obj
        inline_chains[edge_key] = models

    return NodeModel(
        node_id=node_key, topology=ja.topology, degree=ja.degree, ports=ports,
        design_flow_lps=design_flow_lps, primary_component=primary_component, inline_chains=inline_chains,
    )


def build_loss_evaluator(reg, comp_obj, air, family=""):
    """
    A pure callable closing over everything FreeCAD/library-specific this
    component needs to evaluate its own loss -- see analysis/model.py's
    LossEvaluator type. Returns None if this component's type can't be
    resolved at all (analysis/pressure.py's K_DEFAULT fallback then applies,
    exactly like an unresolved type did before this refactor).
    """
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
            "center_point": center_point,
            "properties": properties,
            "connected_ports": connected_ports_ctx,
            "family": family,
            "type_id": type_id,
            "library_id": library_id,
            "air_density": air.density_kg_m3,
            "air_kinematic_viscosity": air.kinematic_viscosity_m2_s,
        }
        return reg.call_loss(library_id, type_def, context)

    return evaluate
