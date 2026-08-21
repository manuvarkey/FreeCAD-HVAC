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
Works out airflow and pressure drop for a whole network.

FlowNetwork.solve_flow_components does the first part: splits the network
into independently-solvable trees and works out how much air moves through
each segment (see that module for the balancing-terminal/conservation
model). This module takes it from there, per segment/junction:
  1. From a segment's own duct size and flow, work out its velocity,
     Reynolds number, and straight-duct friction loss (Darcy-Weisbach).
  2. At each junction, work out its fitting/dynamic loss -- each library
     type can supply its own loss formula (loss_module/loss_function); if
     it doesn't, a generic fallback coefficient is used instead.
  3. Add both losses up and propagate static pressure outward from the
     balancing terminal (which is fixed at 0 Pa as the reference point).
"""

import json
from dataclasses import dataclass, field

from ..utils import hvaclib
from . import airflow
from ..library.library_api import HVACLibraryAPI
from .FlowNetwork import FlowSolveError as AirflowSolveError
from .FlowNetwork import solve_flow_components


K_DEFAULT = 0.3


@dataclass
class SegmentResult:
    """One segment's solved flow, velocity, and pressure loss."""
    key: str
    obj: object
    flow_lps: float = 0.0
    velocity_ms: float = 0.0
    reynolds: float = 0.0
    friction_loss_pa: float = 0.0
    fitting_loss_pa: float = 0.0
    total_loss_pa: float = 0.0
    cumulative_pressure_pa: float = 0.0


@dataclass
class JunctionResult:
    """One junction's solved total flow and static pressure."""
    key: str
    obj: object
    total_flow_lps: float = 0.0
    static_pressure_pa: float = 0.0
    is_source: bool = False
    warning: str = ""


@dataclass
class ComponentResult:
    """Solved results for one independently-solvable tree (one balancing terminal)."""
    reference_terminal_key: str
    segments: list = field(default_factory=list)
    junctions: list = field(default_factory=list)
    critical_terminal_key: str = ""
    critical_pressure_pa: float = 0.0


@dataclass
class AirflowSolveResult:
    """Whole-network result: one ComponentResult per tree, plus any warnings."""
    components: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class AirflowSolver:
    """Solves flow rate, velocity and pressure drop for a DuctNetwork's segments/junctions."""

    def __init__(self, net_obj):
        self.net_obj = net_obj

    def solve(self):
        net = self.net_obj
        parser, junction_map, segment_map, components, warnings = solve_flow_components(net)

        result = AirflowSolveResult(warnings=list(warnings))
        for comp in components:
            try:
                comp_result = self._solve_component(parser, comp, segment_map, junction_map, result.warnings)
                result.components.append(comp_result)
            except AirflowSolveError as exc:
                result.warnings.append(str(exc))

        return result

    # ------------------------------------------------------------------
    # Per-component solve
    # ------------------------------------------------------------------

    def _solve_component(self, parser, comp, segment_map, junction_map, global_warnings):
        net = self.net_obj
        graph = comp.graph
        edge_flow_lps = comp.edge_flow_lps
        port_lookup = comp.port_lookup
        analysis_by_node = comp.analysis_by_node

        # Phase D: size every segment on its own (flow, velocity, Reynolds,
        # straight-duct friction loss) -- see _size_segment below.
        seg_result = {}
        for edge_ref in comp.comp_edges:
            seg_obj = segment_map[edge_ref.tag]
            seg_result[edge_ref.tag] = self._size_segment(net, seg_obj, edge_flow_lps[edge_ref])

        # Phase E: work out each junction's fitting/dynamic loss and add it
        # onto the segment(s) leaving that junction. A junction's physical
        # fitting(s) live on its DuctComponent children (see Component.py) --
        # the common case is exactly one Primary component, which behaves
        # identically to how a junction's own LibraryId/TypeId used to work
        # here. A simple through/2-port node can additionally carry Inline
        # components in series; each one's own loss is evaluated against its
        # own local ports/velocity and converted to Pa immediately (never
        # summing K across components that don't share a reference velocity
        # -- see DuctComponent.CalcPressureDrop), then the Pa contributions
        # are summed once onto the one real segment leaving the junction.
        junction_warning = {}
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        air_density = float(getattr(net, "AirDensity", 1.204) or 1.204)
        air_viscosity = float(getattr(net, "AirKinematicViscosity", 1.51e-5) or 1.51e-5)

        for node_id in comp.comp_nodes:
            degree = graph.degree[node_id]
            if degree < 1:
                continue

            junction_obj = junction_map[parser.node_key(node_id)]
            ja = analysis_by_node[node_id]
            components = junction_obj.Proxy.getComponents()
            if not components:
                continue

            # A simple through/2-port node's component chain may include
            # Inline components with synthetic (non-segment) internal ports;
            # anything else (branch/cross/multiport/end) always has exactly
            # one Primary component whose local ports are the real,
            # unmodified connected ports -- handled by the original,
            # single-component-loop logic below, unchanged in behavior.
            is_chain = getattr(junction_obj, "Topology", "") == "through" and degree == 2

            real_inlet = next((p for p in ja.connected_ports if p.flow_into_junction), None)
            chain_flow_lps = seg_result[real_inlet.edge_key].flow_lps if real_inlet else 0.0
            real_outlet = next((p for p in ja.connected_ports if p.flow_into_junction is False), None)

            warning = ""
            chain_total_pa = 0.0

            for comp_obj in components:
                library_id = getattr(comp_obj, "LibraryId", "")
                type_id = getattr(comp_obj, "TypeId", "")
                type_def = reg.resolve_type(library_id, type_id) if library_id and type_id else None

                # Build the same kind of "properties" dict a geometry backend
                # would see, so the loss formula can use the component's own
                # settings (e.g. an elbow's radius).
                properties = {}
                if type_def is not None:
                    for pdef in getattr(type_def, "properties", []) or []:
                        if hasattr(comp_obj, pdef.name):
                            properties[pdef.name] = getattr(comp_obj, pdef.name)
                        else:
                            properties[pdef.name] = getattr(pdef, "default", None)

                local_ports = json.loads(getattr(comp_obj, "LocalPortsJson", "") or "[]")
                for port in local_ports:
                    self._fillPortFlow(port, seg_result, chain_flow_lps, air_viscosity)

                context = {
                    "obj": comp_obj,
                    "center_point": HVACLibraryAPI.average_point([p["position"] for p in local_ports]),
                    "properties": properties,
                    "connected_ports": local_ports,
                    # Only a Primary's family-driven dispatch (e.g.
                    # through_generic) needs this; DuctComponent has no
                    # Family of its own, so read the junction's.
                    "family": getattr(junction_obj, "Family", "") if getattr(comp_obj, "ComponentRole", "") == "Primary" else "",
                    "type_id": type_id,
                    "library_id": library_id,
                    "air_density": air_density,
                    "air_kinematic_viscosity": air_viscosity,
                }

                # A loss function's result can take three shapes:
                #  - dict {edge_key: K}: one coefficient per port, for
                #    junctions where each inlet leg genuinely has its own
                #    loss (a merging junction has no single "the" outlet to
                #    put one K on), or a single-entry dict from a 2-port
                #    fitting (elbow/transition), keyed to its own outlet.
                #  - float: one coefficient applied to every outlet port
                #    alike (e.g. a damper/VAV's inline_device_loss).
                #  - None: no formula available -- fall back to K_DEFAULT.
                k_result = reg.call_loss(library_id, type_def, context) if type_def is not None else None

                if not is_chain:
                    # Original single-component behavior, unchanged: a
                    # branch/cross/multiport/end node's one component may
                    # have several REAL ports, each needing its own
                    # directly-attributed contribution.
                    if isinstance(k_result, dict):
                        junction_warning[node_id] = ""
                        for edge_key, k in k_result.items():
                            if k is None:
                                continue
                            sres = seg_result.get(edge_key)
                            if sres is None:
                                continue
                            port = next((p for p in local_ports if p.get("edge_key") == edge_key), None)
                            v_ref = port["velocity_ms"] if port else sres.velocity_ms
                            # += (not =): a segment can pick up loss from
                            # both ends -- its upstream fitting here, and
                            # separately its downstream terminal device
                            # (e.g. a diffuser) when that junction is
                            # visited later in this same loop. Since each
                            # junction is only visited once, this never
                            # double-counts either one.
                            sres.fitting_loss_pa += float(k) * airflow.velocity_pressure(air_density, v_ref)
                        continue

                    if k_result is None:
                        if degree == 1:
                            # Most open ends are placeholder markers with no
                            # real device modeled (e.g. the default
                            # end_terminal_marker), so "no loss" is the
                            # expected, normal case here -- not a
                            # missing-data problem worth a warning.
                            junction_warning[node_id] = ""
                            continue
                        # A real fitting (degree >= 2) always has some
                        # physical loss, so missing data here is worth
                        # flagging.
                        k_uniform = K_DEFAULT
                        warning = "No fitting-loss data for type '{}'; using generic default K={}.".format(
                            type_id or "(none)", K_DEFAULT
                        )
                        junction_warning[node_id] = warning
                        global_warnings.append("{}: {}".format(junction_obj.Label, warning))
                    else:
                        k_uniform = float(k_result)
                        junction_warning[node_id] = ""

                    for port in local_ports:
                        if port.get("flow_into_junction"):
                            continue  # inlet port -- fitting loss attributed at outlet ports only
                        sres = seg_result.get(port.get("edge_key"))
                        if sres is None:
                            continue
                        sres.fitting_loss_pa += k_uniform * airflow.velocity_pressure(air_density, port["velocity_ms"])
                    continue

                # Simple through/2-port chain: normalize this component's
                # own result to one (K, reference velocity) pair -- a 2-port
                # fitting's dict result always has exactly one entry, keyed
                # to its own outlet -- then convert to Pa using THIS
                # component's own local velocity and add to the chain total.
                # Never apply directly to seg_result here: an interior
                # component's own ports are synthetic (no matching real
                # segment), so the whole chain's total is attributed once,
                # after this loop, onto the one real outlet segment.
                outlet_port = next((p for p in local_ports if p.get("flow_into_junction") is False), None)
                v_ref = outlet_port["velocity_ms"] if outlet_port else 0.0

                if isinstance(k_result, dict):
                    k = k_result.get(outlet_port.get("edge_key")) if outlet_port else None
                    k = 0.0 if k is None else float(k)
                elif k_result is not None:
                    k = float(k_result)
                elif degree == 1:
                    k = 0.0
                else:
                    k = K_DEFAULT
                    warning = "No fitting-loss data for component '{}' (type '{}'); using generic default K={}.".format(
                        comp_obj.Label, type_id or "(none)", K_DEFAULT
                    )
                    global_warnings.append("{}: {}".format(junction_obj.Label, warning))

                dp_pa = k * airflow.velocity_pressure(air_density, v_ref)
                chain_total_pa += dp_pa
                comp_obj.CalcFlowRate = outlet_port.get("flow_rate_lps", 0.0) if outlet_port else 0.0
                comp_obj.CalcVelocity = v_ref
                comp_obj.CalcLossCoefficient = k
                comp_obj.CalcPressureDrop = dp_pa

            if is_chain:
                junction_warning[node_id] = warning
                if real_outlet is not None:
                    sres = seg_result.get(real_outlet.edge_key)
                    if sres is not None:
                        sres.fitting_loss_pa += chain_total_pa

        # Straight friction (Phase D) + fitting loss (just computed) = each segment's total.
        for sres in seg_result.values():
            sres.total_loss_pa = sres.friction_loss_pa + sres.fitting_loss_pa

        # Phase F: walk outward from the balancing terminal (fixed at 0 Pa)
        # and add up each segment's loss to get every node's static pressure.
        # Pressure drops in the direction of flow: if flow enters the junction
        # here (this port is an inlet), the junction is downstream of its
        # parent, so subtract the loss; otherwise it's upstream, so add it.
        static_pressure = {comp.root_node_id: 0.0}
        for node_id in comp.order[1:]:
            parent = comp.parent_node[node_id]
            edge = comp.parent_edge[node_id]
            port_at_node = port_lookup[(node_id, edge.tag)]
            loss = seg_result[edge.tag].total_loss_pa
            if port_at_node.flow_into_junction:
                static_pressure[node_id] = static_pressure[parent] - loss
                downstream_pressure = static_pressure[node_id]
            else:
                static_pressure[node_id] = static_pressure[parent] + loss
                downstream_pressure = static_pressure[parent]
            seg_result[edge.tag].cumulative_pressure_pa = downstream_pressure

        # Phase G: assemble results and write them back onto the FreeCAD
        # objects. Everything above only touched local Python data, so a
        # failure in any earlier phase never leaves a half-written object.
        junction_results = {}
        for node_id in comp.comp_nodes:
            junction_obj = junction_map[parser.node_key(node_id)]
            degree = graph.degree[node_id]

            # Add up flow in both directions across this junction's edges,
            # and note whether any edge actually points outward (a source).
            total_in = 0.0
            total_out = 0.0
            has_outlet_port = False
            for _u, _v, edge in graph.edges(node_id, data="key"):
                port = port_lookup[(node_id, edge.tag)]
                mag = edge_flow_lps[edge]
                if port.flow_into_junction:
                    total_in += mag
                else:
                    total_out += mag
                    has_outlet_port = True

            if degree == 1:
                # An open end has only one edge, so total_in/total_out is
                # really the same single flow value split across the two --
                # add them to get it back, regardless of which one it landed
                # in. is_source only checks direction (not magnitude), so it
                # stays correct even at a source with zero flow.
                total_flow = total_in + total_out
                is_source = has_outlet_port
            else:
                total_flow = max(total_in, total_out)
                is_source = False

            junction_results[node_id] = JunctionResult(
                key=parser.node_key(node_id),
                obj=junction_obj,
                total_flow_lps=total_flow,
                static_pressure_pa=static_pressure[node_id],
                is_source=is_source,
                warning=junction_warning.get(node_id, ""),
            )

        for edge_ref in comp.comp_edges:
            sres = seg_result[edge_ref.tag]
            obj = sres.obj
            obj.CalcFlowRate = sres.flow_lps
            obj.CalcVelocity = sres.velocity_ms
            obj.CalcReynoldsNumber = sres.reynolds
            obj.CalcFrictionLoss = sres.friction_loss_pa
            obj.CalcFittingLoss = sres.fitting_loss_pa
            obj.CalcTotalLoss = sres.total_loss_pa
            obj.CalcCumulativePressure = sres.cumulative_pressure_pa

        for node_id, jres in junction_results.items():
            jres.obj.CalcTotalFlowRate = jres.total_flow_lps
            jres.obj.CalcStaticPressure = jres.static_pressure_pa
            jres.obj.IsFlowSource = jres.is_source
            jres.obj.CalcLossWarning = jres.warning

        critical_node_id = max(comp.terminal_ids, key=lambda n: abs(static_pressure[n]))

        return ComponentResult(
            reference_terminal_key=parser.node_key(comp.root_node_id),
            segments=[seg_result[e.tag] for e in comp.comp_edges],
            junctions=[junction_results[n] for n in comp.comp_nodes],
            critical_terminal_key=parser.node_key(critical_node_id),
            critical_pressure_pa=abs(static_pressure[critical_node_id]),
        )

    # ------------------------------------------------------------------
    # Component-local port flow numbers (Phase E)
    #
    # A component's LocalPortsJson ports are either a real network edge
    # (already solved in Phase D -- reuse those exact numbers) or a
    # synthetic internal seam with no segment of its own (derive flow/
    # velocity/Reynolds locally from the chain's own conserved flow rate).
    # ------------------------------------------------------------------

    @staticmethod
    def _fillPortFlow(port, seg_result, chain_flow_lps, air_viscosity):
        sres = seg_result.get(port.get("edge_key"))
        if sres is not None:
            port["flow_rate_lps"] = sres.flow_lps
            port["velocity_ms"] = sres.velocity_ms
            port["reynolds"] = sres.reynolds
            return

        area_m2 = HVACLibraryAPI.port_area(port)
        velocity_ms = (
            airflow.velocity_from_flow(airflow.lps_to_m3s(chain_flow_lps), area_m2)
            if area_m2 > 0.0 else 0.0
        )
        dh_m = AirflowSolver._portHydraulicDiameter(port)
        reynolds = (
            airflow.reynolds_number(velocity_ms, dh_m, air_viscosity)
            if dh_m > 0.0 else 0.0
        )
        port["flow_rate_lps"] = chain_flow_lps
        port["velocity_ms"] = velocity_ms
        port["reynolds"] = reynolds

    @staticmethod
    def _portHydraulicDiameter(port):
        profile = HVACLibraryAPI.port_profile(port)
        if profile == "Circular":
            return airflow.mm_to_m(HVACLibraryAPI.port_diameter(port))
        if profile in ("Rectangular", "Oval"):
            width_m = airflow.mm_to_m(HVACLibraryAPI.port_width(port))
            height_m = airflow.mm_to_m(HVACLibraryAPI.port_height(port))
            if width_m <= 0.0 or height_m <= 0.0:
                return 0.0
            if profile == "Rectangular":
                return airflow.hydraulic_diameter_rectangular(width_m, height_m)
            return airflow.hydraulic_diameter_oval(width_m, height_m)
        return 0.0

    # ------------------------------------------------------------------
    # Per-segment flow/friction calculation
    #
    # Despite the name, this doesn't choose a duct size (see DuctSizer.py
    # for that) -- it reads the segment's EXISTING size and works out its
    # velocity, Reynolds number, and friction loss for that size.
    # ------------------------------------------------------------------

    def _size_segment(self, net, seg_obj, flow_lps):
        # Step 1: read the segment's current size and work out its
        # cross-section area and hydraulic diameter.
        profile = str(getattr(seg_obj, "Profile", "") or "")
        section_params = hvaclib.get_segment_section_params(seg_obj)

        if profile == "Circular":
            diameter_m = airflow.mm_to_m(section_params.get("Diameter", 0.0))
            if diameter_m <= 0.0:
                raise AirflowSolveError(
                    "Segment '{}' has no Diameter set; set duct dimensions before "
                    "calculating.".format(seg_obj.Label)
                )
            area_m2 = airflow.circular_area(diameter_m)
            dh_m = airflow.hydraulic_diameter_circular(diameter_m)
        elif profile in ("Rectangular", "Oval"):
            width_m = airflow.mm_to_m(section_params.get("Width", 0.0))
            height_m = airflow.mm_to_m(section_params.get("Height", 0.0))
            if width_m <= 0.0 or height_m <= 0.0:
                raise AirflowSolveError(
                    "Segment '{}' has no Width/Height set; set duct dimensions before "
                    "calculating.".format(seg_obj.Label)
                )
            if profile == "Rectangular":
                area_m2 = airflow.rectangular_area(width_m, height_m)
                dh_m = airflow.hydraulic_diameter_rectangular(width_m, height_m)
            else:
                area_m2 = airflow.oval_area(width_m, height_m)
                dh_m = airflow.hydraulic_diameter_oval(width_m, height_m)
        else:
            raise AirflowSolveError(
                "Segment '{}' has unsupported or unset Profile '{}'.".format(seg_obj.Label, profile)
            )

        key = getattr(seg_obj, "SegmentKey", "") or seg_obj.Name

        if flow_lps <= 1e-9:
            # No flow through this segment -- nothing to compute.
            return SegmentResult(key=key, obj=seg_obj, flow_lps=0.0)

        # Step 2: velocity, then Reynolds number and friction factor from it.
        velocity_ms = airflow.velocity_from_flow(airflow.lps_to_m3s(flow_lps), area_m2)

        roughness_mm = float(getattr(seg_obj, "Roughness", 0.0) or 0.0)
        if roughness_mm <= 0.0:
            roughness_mm = float(getattr(net, "DefaultRoughness", 0.0) or 0.0)
        roughness_m = airflow.mm_to_m(roughness_mm)

        viscosity = float(getattr(net, "AirKinematicViscosity", 1.51e-5) or 1.51e-5)
        density = float(getattr(net, "AirDensity", 1.204) or 1.204)

        reynolds = airflow.reynolds_number(velocity_ms, dh_m, viscosity)
        relative_roughness = roughness_m / dh_m
        friction_factor = airflow.friction_factor_altshul_tsal(reynolds, relative_roughness)

        # Step 3: straight-duct friction loss over the segment's real length.
        length_m = airflow.mm_to_m(float(getattr(seg_obj, "EffectiveLength", 0.0) or 0.0))
        friction_loss_pa = airflow.darcy_weisbach_pressure_loss(
            friction_factor, length_m, dh_m, density, velocity_ms
        )

        return SegmentResult(
            key=key,
            obj=seg_obj,
            flow_lps=flow_lps,
            velocity_ms=velocity_ms,
            reynolds=reynolds,
            friction_loss_pa=friction_loss_pa,
        )
