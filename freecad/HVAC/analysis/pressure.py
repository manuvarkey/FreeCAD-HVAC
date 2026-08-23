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
Works out airflow and pressure drop for a whole NetworkModel -- a pure port
of core/AirflowSolver.py's algorithm.

flow.solve_flow_components does the first part: splits the network into
independently-solvable trees and works out how much air moves through each
segment (see that module for the balancing-terminal/conservation model).
This module takes it from there, per segment/node:
  1. From a segment's own section and flow, work out its velocity, Reynolds
     number, and straight-duct friction loss (Darcy-Weisbach).
  2. At each node, work out its fitting/dynamic loss -- each node's Primary
     component (and, independently, each edge's own Inline chain) supplies
     its own loss_evaluator (built by the FreeCAD adapter from the actual
     library type -- this module never resolves one itself); if none is
     given, a generic fallback coefficient is used instead.
  3. Add the losses up (kept separate as friction/junction/component so
     paths.py can report them separately) and propagate static pressure
     outward from the balancing terminal (fixed at 0 Pa as the reference).
"""

from dataclasses import dataclass, field
from typing import Dict, List

from . import paths as paths_mod
from . import physics
from .flow import FlowComponent, FlowSolveError
from .model import NetworkModel, PortModel

K_DEFAULT = 0.3


@dataclass
class SegmentResult:
    """One segment's solved flow, velocity, and pressure loss."""
    edge_key: str
    flow_lps: float = 0.0
    velocity_ms: float = 0.0
    reynolds: float = 0.0
    friction_loss_pa: float = 0.0
    junction_loss_pa: float = 0.0
    component_loss_pa: float = 0.0
    cumulative_pressure_pa: float = 0.0

    @property
    def total_loss_pa(self):
        return self.friction_loss_pa + self.junction_loss_pa + self.component_loss_pa


@dataclass
class JunctionResult:
    """One node's solved total flow and static pressure."""
    node_id: str
    total_flow_lps: float = 0.0
    static_pressure_pa: float = 0.0
    is_source: bool = False
    warning: str = ""


@dataclass
class ComponentResult:
    """One Inline component's solved flow/velocity/loss (a Primary's own contributions are attributed onto segments directly, not reported here)."""
    component_id: str
    flow_lps: float = 0.0
    velocity_ms: float = 0.0
    loss_coefficient: float = 0.0
    pressure_drop_pa: float = 0.0


@dataclass
class ComponentTreeResult:
    """Solved results for one independently-solvable tree (one balancing terminal)."""
    reference_terminal_id: str
    segments: Dict[str, SegmentResult] = field(default_factory=dict)
    junctions: Dict[str, JunctionResult] = field(default_factory=dict)
    components: Dict[str, ComponentResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    paths: List["paths_mod.FlowPathResult"] = field(default_factory=list)
    critical_path: "paths_mod.CriticalPathResult" = None


class PressureSolver:
    """Solves flow rate, velocity, and pressure drop for a NetworkModel's segments/nodes."""

    def solve(self, network: NetworkModel, components: List[FlowComponent]):
        """Returns (list[ComponentTreeResult], warnings) -- one result per successfully-solved tree."""
        results = []
        warnings = []
        for comp in components:
            try:
                results.append(self._solve_component(network, comp, warnings))
            except FlowSolveError as exc:
                warnings.append(str(exc))
        return results, warnings

    # ------------------------------------------------------------------
    # Per-component solve
    # ------------------------------------------------------------------

    def _solve_component(self, network: NetworkModel, comp: FlowComponent, global_warnings: List[str]):
        air = network.air

        # Phase D: size every segment on its own (flow, velocity, Reynolds,
        # straight-duct friction loss).
        seg_results = {}
        for edge_key in comp.edge_keys:
            seg = network.segments[edge_key]
            seg_results[edge_key] = self._size_segment(seg, air, comp.edge_flow_lps[edge_key])

        # Phase E: work out each node's fitting/dynamic loss and add it onto
        # the segment(s) leaving that node. A node's physical fitting(s)
        # are its Primary component -- evaluated once against its own full
        # real multi-port context -- plus, independently, each real edge's
        # own chain of Inline components in series, evaluated against just
        # that edge's own flow. Each contribution is converted to Pa using
        # ITS OWN reference velocity before being summed (never summing raw
        # K values across components that don't share a reference velocity).
        component_results = {}
        junction_warning = {}
        component_tree_warnings = []

        for node_id in comp.node_ids:
            node = network.nodes[node_id]
            degree = len(node.ports)
            if degree < 1:
                continue

            primary = node.primary_component
            if primary is None:
                continue

            warning = ""
            port_velocities = self._port_velocities(primary.ports, seg_results, 0.0, air)
            k_result = primary.loss_evaluator(port_velocities) if primary.loss_evaluator else None

            if isinstance(k_result, dict):
                for edge_key, k in k_result.items():
                    if k is None:
                        continue
                    sres = seg_results.get(edge_key)
                    if sres is None:
                        continue
                    v_ref = port_velocities.get(edge_key, {}).get("velocity_ms", sres.velocity_ms)
                    sres.junction_loss_pa += float(k) * physics.velocity_pressure(air.density_kg_m3, v_ref)
            elif k_result is None and degree == 1:
                # Most open ends are placeholder markers with no real device
                # modeled -- "no loss" is the expected, normal case here.
                pass
            else:
                if k_result is None:
                    k_uniform = K_DEFAULT
                    warning = "No fitting-loss data for node '{}'; using generic default K={}.".format(node_id, K_DEFAULT)
                    global_warnings.append(warning)
                    component_tree_warnings.append(warning)
                else:
                    k_uniform = float(k_result)

                for port in primary.ports:
                    if port.flow_into_node:
                        continue  # inlet port -- fitting loss attributed at outlet ports only
                    sres = seg_results.get(port.edge_key)
                    if sres is None:
                        continue
                    v_ref = port_velocities.get(port.edge_key, {}).get("velocity_ms", 0.0)
                    sres.junction_loss_pa += k_uniform * physics.velocity_pressure(air.density_kg_m3, v_ref)

            # -- Each real edge's own independent Inline chain, using THAT
            # EDGE'S OWN flow (a branch leg's damper must see only that
            # leg's flow, not the whole node's).
            for edge_key, chain in node.inline_chains.items():
                sres_edge = seg_results.get(edge_key)
                if sres_edge is None or not chain:
                    continue
                chain_flow_lps = sres_edge.flow_lps
                chain_total_pa = 0.0
                chain_warning = ""

                for comp_model in chain:
                    c_port_velocities = self._port_velocities(comp_model.ports, seg_results, chain_flow_lps, air)
                    c_k_result = comp_model.loss_evaluator(c_port_velocities) if comp_model.loss_evaluator else None

                    # Normalize this component's own result to one (K,
                    # reference velocity) pair -- a 2-port fitting's dict
                    # result always has exactly one entry, keyed to its own
                    # outlet.
                    outlet_port = next((p for p in comp_model.ports if p.flow_into_node is False), None)
                    v_ref = (
                        c_port_velocities.get(outlet_port.edge_key, {}).get("velocity_ms", 0.0)
                        if outlet_port is not None else 0.0
                    )

                    if isinstance(c_k_result, dict):
                        k = c_k_result.get(outlet_port.edge_key) if outlet_port is not None else None
                        k = 0.0 if k is None else float(k)
                    elif c_k_result is not None:
                        k = float(c_k_result)
                    elif degree == 1:
                        k = 0.0
                    else:
                        k = K_DEFAULT
                        chain_warning = "No fitting-loss data for component '{}'; using generic default K={}.".format(
                            comp_model.component_id, K_DEFAULT
                        )
                        global_warnings.append(chain_warning)
                        component_tree_warnings.append(chain_warning)

                    dp_pa = k * physics.velocity_pressure(air.density_kg_m3, v_ref)
                    chain_total_pa += dp_pa
                    flow_here = (
                        c_port_velocities.get(outlet_port.edge_key, {}).get("flow_lps", 0.0)
                        if outlet_port is not None else 0.0
                    )
                    component_results[comp_model.component_id] = ComponentResult(
                        component_id=comp_model.component_id, flow_lps=flow_here, velocity_ms=v_ref,
                        loss_coefficient=k, pressure_drop_pa=dp_pa,
                    )

                # += : a real edge can legitimately receive a contribution
                # from both the Primary's own per-port k_result above AND
                # this edge's own Inline chain -- additive, never double
                # counted, since each is only ever computed once.
                sres_edge.component_loss_pa += chain_total_pa
                if chain_warning:
                    warning = (warning + "; " + chain_warning) if warning else chain_warning

            junction_warning[node_id] = warning

        # Phase F: walk outward from the balancing terminal (fixed at 0 Pa)
        # and add up each segment's loss to get every node's static
        # pressure. Pressure drops in the direction of flow: if flow enters
        # the node here (this port is an inlet), the node is downstream of
        # its parent, so subtract the loss; otherwise it's upstream, so add it.
        static_pressure = {comp.root_node_id: 0.0}
        for node_id in comp.order[1:]:
            parent = comp.parent_node[node_id]
            edge_key = comp.parent_edge[node_id]
            port_at_node = self._port_for_edge(network.nodes[node_id].ports, edge_key)
            loss = seg_results[edge_key].total_loss_pa
            if port_at_node.flow_into_node:
                static_pressure[node_id] = static_pressure[parent] - loss
                downstream_pressure = static_pressure[node_id]
            else:
                static_pressure[node_id] = static_pressure[parent] + loss
                downstream_pressure = static_pressure[parent]
            seg_results[edge_key].cumulative_pressure_pa = downstream_pressure

        # Phase G: assemble every node's total flow/source flag.
        junction_results = {}
        for node_id in comp.node_ids:
            node = network.nodes[node_id]
            degree = len(node.ports)

            total_in = 0.0
            total_out = 0.0
            has_outlet_port = False
            for port in node.ports:
                mag = comp.edge_flow_lps[port.edge_key]
                if port.flow_into_node:
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
                node_id=node_id,
                total_flow_lps=total_flow,
                static_pressure_pa=static_pressure[node_id],
                is_source=is_source,
                warning=junction_warning.get(node_id, ""),
            )

        tree_result = ComponentTreeResult(
            reference_terminal_id=comp.root_node_id,
            segments=seg_results,
            junctions=junction_results,
            components=component_results,
            warnings=component_tree_warnings,
        )
        tree_result.paths = paths_mod.build_terminal_paths(comp, tree_result)
        tree_result.critical_path = paths_mod.find_critical_path(tree_result.paths)
        return tree_result

    # ------------------------------------------------------------------
    # Component-local port flow numbers (Phase E)
    #
    # A component's own ports are either a real network edge (already
    # solved in Phase D -- reuse those exact numbers) or a synthetic
    # internal seam with no segment of its own (derive flow/velocity/
    # Reynolds locally from the chain's own conserved flow rate).
    # ------------------------------------------------------------------

    @staticmethod
    def _port_velocities(ports: List[PortModel], seg_results: Dict[str, SegmentResult],
                          chain_flow_lps: float, air):
        """{edge_key: {"velocity_ms", "flow_lps", "reynolds"}} for a component's own local ports."""
        out = {}
        for port in ports:
            if port.is_real_edge:
                sres = seg_results.get(port.edge_key)
                if sres is not None:
                    out[port.edge_key] = {
                        "velocity_ms": sres.velocity_ms, "flow_lps": sres.flow_lps, "reynolds": sres.reynolds,
                    }
                    continue

            area_m2 = physics.section_area_m2(port.section)
            velocity_ms = (
                physics.velocity_from_flow(physics.lps_to_m3s(chain_flow_lps), area_m2) if area_m2 > 0.0 else 0.0
            )
            dh_m = physics.section_hydraulic_diameter_m(port.section)
            reynolds = (
                physics.reynolds_number(velocity_ms, dh_m, air.kinematic_viscosity_m2_s) if dh_m > 0.0 else 0.0
            )
            out[port.edge_key] = {"velocity_ms": velocity_ms, "flow_lps": chain_flow_lps, "reynolds": reynolds}
        return out

    @staticmethod
    def _port_for_edge(ports: List[PortModel], edge_key: str) -> PortModel:
        return next(p for p in ports if p.edge_key == edge_key)

    # ------------------------------------------------------------------
    # Per-segment flow/friction calculation
    #
    # Despite the name, this doesn't choose a duct size (see sizing.py for
    # that) -- it reads the segment's EXISTING section and works out its
    # velocity, Reynolds number, and friction loss for that size.
    # ------------------------------------------------------------------

    @staticmethod
    def _size_segment(seg, air, flow_lps: float) -> SegmentResult:
        area_m2 = physics.section_area_m2(seg.section)
        dh_m = physics.section_hydraulic_diameter_m(seg.section)
        if area_m2 <= 0.0 or dh_m <= 0.0:
            raise FlowSolveError(
                "Segment '{}' has no valid duct dimensions set; set duct size before calculating.".format(
                    seg.edge_key
                )
            )

        if flow_lps <= 1e-9:
            # No flow through this segment -- nothing to compute.
            return SegmentResult(edge_key=seg.edge_key, flow_lps=0.0)

        velocity_ms = physics.velocity_from_flow(physics.lps_to_m3s(flow_lps), area_m2)
        roughness_m = physics.mm_to_m(seg.roughness_mm)
        reynolds = physics.reynolds_number(velocity_ms, dh_m, air.kinematic_viscosity_m2_s)
        relative_roughness = roughness_m / dh_m
        friction_factor = physics.friction_factor_altshul_tsal(reynolds, relative_roughness)

        length_m = physics.mm_to_m(seg.length_mm)
        friction_loss_pa = physics.darcy_weisbach_pressure_loss(
            friction_factor, length_m, dh_m, air.density_kg_m3, velocity_ms
        )

        return SegmentResult(
            edge_key=seg.edge_key, flow_lps=flow_lps, velocity_ms=velocity_ms,
            reynolds=reynolds, friction_loss_pa=friction_loss_pa,
        )
