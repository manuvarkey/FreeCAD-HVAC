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
Whole-network airflow and pressure-drop solver.

Assumes each connected sub-network of the duct network is a tree: exactly one
terminal (degree-1 junction) is left with no Design Flow Rate (the balancing
terminal, e.g. the AHU/fan connection); every other terminal carries a
user-specified design flow rate (e.g. a diffuser/grille). Flow magnitudes are
solved by mass conservation from the leaves toward the balancing terminal,
using the existing per-port flow_into_junction data (derived from base
geometry direction) to know each segment's fixed physical flow direction.
Static pressure is then propagated outward from the balancing terminal
(0 Pa reference) using straight-duct friction loss (Darcy-Weisbach with the
Altshul-Tsal friction factor) and per-fitting dynamic loss (pluggable via the
library's loss_module/loss_function, falling back to a generic coefficient).

Loops (non-tree sub-networks) are rejected with a clear error rather than
solved.
"""

from collections import deque
from dataclasses import asdict, dataclass, field

from ..utils import hvaclib
from . import airflow


K_DEFAULT = 0.3


class AirflowSolveError(Exception):
    """Raised when a sub-network cannot be solved (loop, bad boundary conditions, missing data)."""
    pass


@dataclass
class SegmentResult:
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
    key: str
    obj: object
    total_flow_lps: float = 0.0
    static_pressure_pa: float = 0.0
    is_source: bool = False
    warning: str = ""


@dataclass
class ComponentResult:
    reference_terminal_key: str
    segments: list = field(default_factory=list)
    junctions: list = field(default_factory=list)
    critical_terminal_key: str = ""
    critical_pressure_pa: float = 0.0


@dataclass
class AirflowSolveResult:
    components: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class AirflowSolver:
    """Solves flow rate, velocity and pressure drop for a DuctNetwork's segments/junctions."""

    def __init__(self, net_obj):
        self.net_obj = net_obj

    def solve(self):
        net = self.net_obj
        parser = net.Proxy.getParser(rebuild=True)
        segment_map = net.Proxy.collectSegmentObjects()
        junction_map = net.Proxy.collectJunctionObjects()

        adjacency = {node_id: list(parser.node_edges(node_id)) for node_id in parser.nodes()}
        edge_endpoints = {}
        for node_id, edge_refs in adjacency.items():
            for edge_ref in edge_refs:
                edge_endpoints[edge_ref] = parser.edge_analysis_nodes(edge_ref)

        result = AirflowSolveResult()
        for comp_nodes, comp_edges in self._find_components(adjacency, edge_endpoints):
            if not comp_edges:
                continue
            try:
                comp_result = self._solve_component(
                    parser, comp_nodes, comp_edges, adjacency, edge_endpoints,
                    segment_map, junction_map, result.warnings,
                )
                result.components.append(comp_result)
            except AirflowSolveError as exc:
                result.warnings.append(str(exc))

        return result

    # ------------------------------------------------------------------
    # Topology helpers
    # ------------------------------------------------------------------

    def _find_components(self, adjacency, edge_endpoints):
        """Group analysis nodes into connected components (node set, edge set)."""
        visited = set()
        components = []
        for start in adjacency:
            if start in visited:
                continue
            comp_nodes = set()
            comp_edges = set()
            stack = [start]
            visited.add(start)
            while stack:
                n = stack.pop()
                comp_nodes.add(n)
                for edge_ref in adjacency[n]:
                    comp_edges.add(edge_ref)
                    au, av = edge_endpoints[edge_ref]
                    other = av if au == n else au
                    if other not in visited:
                        visited.add(other)
                        stack.append(other)
            components.append((comp_nodes, comp_edges))
        return components

    # ------------------------------------------------------------------
    # Per-component solve
    # ------------------------------------------------------------------

    def _solve_component(self, parser, comp_nodes, comp_edges, adjacency, edge_endpoints,
                          segment_map, junction_map, global_warnings):
        net = self.net_obj

        n_nodes = len(comp_nodes)
        n_edges = len(comp_edges)
        if n_edges != n_nodes - 1:
            raise AirflowSolveError(
                "Loop detected: a sub-network with {} junction(s) has {} duct segment(s); "
                "a tree requires exactly {}. Loops are not supported for airflow calculation.".format(
                    n_nodes, n_edges, n_nodes - 1
                )
            )

        # Resolve FreeCAD objects and per-node port analysis up front.
        analysis_by_node = {}
        port_lookup = {}
        for node_id in comp_nodes:
            node_key = parser.node_key(node_id)
            junction_obj = junction_map.get(node_key)
            if junction_obj is None:
                raise AirflowSolveError(
                    "Junction data missing for node '{}'; recompute the network before "
                    "calculating airflow.".format(node_key)
                )
            ja = parser.build_junction_analysis(node_id, segment_map)
            if ja is None:
                raise AirflowSolveError("Could not analyze junction '{}'.".format(junction_obj.Label))
            analysis_by_node[node_id] = ja
            for port in ja.connected_ports:
                port_lookup[(node_id, port.edge_key)] = port

        for edge_ref in comp_edges:
            if segment_map.get(edge_ref.tag) is None:
                raise AirflowSolveError(
                    "Segment data missing for edge '{}'; recompute the network before "
                    "calculating airflow.".format(edge_ref.tag)
                )

        # Terminals and the balancing (unspecified) terminal.
        terminal_ids = [n for n in comp_nodes if len(adjacency[n]) == 1]
        if len(terminal_ids) < 2:
            any_label = junction_map[parser.node_key(next(iter(comp_nodes)))].Label
            raise AirflowSolveError(
                "Sub-network containing junction '{}' has fewer than 2 terminals; "
                "nothing to solve.".format(any_label)
            )

        specified = []
        unspecified = []
        for node_id in terminal_ids:
            junction_obj = junction_map[parser.node_key(node_id)]
            design = float(getattr(junction_obj, "DesignFlowRate", 0.0) or 0.0)
            if abs(design) > 1e-9:
                specified.append((node_id, junction_obj))
            else:
                unspecified.append((node_id, junction_obj))

        if len(unspecified) == 0:
            labels = ", ".join(j.Label for _, j in specified)
            raise AirflowSolveError(
                "All terminals in this sub-network have a Design Flow Rate set ({}). "
                "Leave exactly one terminal's Design Flow Rate blank to act as the "
                "balancing terminal.".format(labels)
            )
        if len(unspecified) > 1:
            labels = ", ".join(j.Label for _, j in unspecified)
            raise AirflowSolveError(
                "Multiple terminals have no Design Flow Rate set ({}). Set Design Flow Rate "
                "on all terminals except exactly one.".format(labels)
            )

        root_node_id, root_obj = unspecified[0]

        # BFS from the balancing terminal to get a rooted-tree traversal order.
        parent_node = {}
        parent_edge = {}
        order = [root_node_id]
        visited_bfs = {root_node_id}
        queue = deque([root_node_id])
        while queue:
            n = queue.popleft()
            for edge_ref in adjacency[n]:
                au, av = edge_endpoints[edge_ref]
                other = av if au == n else au
                if other in visited_bfs:
                    continue
                visited_bfs.add(other)
                parent_node[other] = n
                parent_edge[other] = edge_ref
                order.append(other)
                queue.append(other)

        # Phase C: flow-magnitude accumulation, leaves -> root.
        edge_flow_lps = {}
        for node_id in reversed(order[1:]):
            edge = parent_edge[node_id]

            if len(adjacency[node_id]) == 1:
                junction_obj = junction_map[parser.node_key(node_id)]
                edge_flow_lps[edge] = abs(float(getattr(junction_obj, "DesignFlowRate", 0.0) or 0.0))
                continue

            known_in = 0.0
            known_out = 0.0
            for edge_ref in adjacency[node_id]:
                if edge_ref == edge:
                    continue
                port = port_lookup[(node_id, edge_ref.tag)]
                mag = edge_flow_lps[edge_ref]
                if port.flow_into_junction:
                    known_in += mag
                else:
                    known_out += mag

            p_port = port_lookup[(node_id, edge.tag)]
            if p_port.flow_into_junction:
                p_mag = known_out - known_in
            else:
                p_mag = known_in - known_out

            if p_mag < -1e-6:
                junction_obj = junction_map[parser.node_key(node_id)]
                raise AirflowSolveError(
                    "Inconsistent flow direction at junction '{}': incoming and outgoing design "
                    "flows don't balance given the current segment directions. Check base geometry "
                    "direction (HVAC_ReverseGeometryDirection / Edit Duct Directions).".format(
                        junction_obj.Label
                    )
                )
            edge_flow_lps[edge] = max(p_mag, 0.0)

        # Phase D: per-segment sizing (flow, velocity, Reynolds, friction loss).
        seg_result = {}
        for edge_ref in comp_edges:
            seg_obj = segment_map[edge_ref.tag]
            seg_result[edge_ref.tag] = self._size_segment(net, seg_obj, edge_flow_lps[edge_ref])

        # Phase E: fitting/dynamic loss via pluggable per-type loss functions.
        junction_warning = {}
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        air_density = float(getattr(net, "AirDensity", 1.204) or 1.204)
        air_viscosity = float(getattr(net, "AirKinematicViscosity", 1.51e-5) or 1.51e-5)

        for node_id in comp_nodes:
            if len(adjacency[node_id]) <= 1:
                continue

            junction_obj = junction_map[parser.node_key(node_id)]
            ja = analysis_by_node[node_id]

            library_id = getattr(junction_obj, "LibraryId", "")
            type_id = getattr(junction_obj, "TypeId", "")
            type_def = reg.resolve_type(library_id, type_id) if library_id and type_id else None

            properties = {}
            if type_def is not None:
                for pdef in getattr(type_def, "properties", []) or []:
                    if hasattr(junction_obj, pdef.name):
                        properties[pdef.name] = getattr(junction_obj, pdef.name)
                    else:
                        properties[pdef.name] = getattr(pdef, "default", None)

            connected_ports_ctx = []
            for port in ja.connected_ports:
                port_dict = asdict(port)
                sres = seg_result.get(port.edge_key)
                port_dict["flow_rate_lps"] = sres.flow_lps if sres else 0.0
                port_dict["velocity_ms"] = sres.velocity_ms if sres else 0.0
                port_dict["reynolds"] = sres.reynolds if sres else 0.0
                connected_ports_ctx.append(port_dict)

            context = {
                "obj": junction_obj,
                "center_point": ja.point,
                "properties": properties,
                "connected_ports": connected_ports_ctx,
                "family": getattr(junction_obj, "Family", ""),
                "type_id": type_id,
                "library_id": library_id,
                "air_density": air_density,
                "air_kinematic_viscosity": air_viscosity,
            }

            # A loss function may return:
            #  - dict {edge_key: K}: per-port coefficients, each already referenced to
            #    that port's own velocity. Needed for converging (merging) junctions,
            #    where each inlet leg has a physically distinct coefficient -- there is
            #    no single "the" outlet to attribute a uniform K to.
            #  - float: a single coefficient applied uniformly to every outlet port
            #    (legacy/simple contract, still used by the generic cross/multiport
            #    placeholders).
            #  - None: no data available; fall back to a uniform K_DEFAULT on outlet ports.
            k_result = reg.call_loss(library_id, type_def, context) if type_def is not None else None

            if isinstance(k_result, dict):
                junction_warning[node_id] = ""
                for edge_key, k in k_result.items():
                    if k is None:
                        continue
                    sres = seg_result.get(edge_key)
                    if sres is None:
                        continue
                    sres.fitting_loss_pa = float(k) * airflow.velocity_pressure(air_density, sres.velocity_ms)
                continue

            if k_result is None:
                k_uniform = K_DEFAULT
                warning = "No fitting-loss data for type '{}'; using generic default K={}.".format(
                    type_id or "(none)", K_DEFAULT
                )
                junction_warning[node_id] = warning
                global_warnings.append("{}: {}".format(junction_obj.Label, warning))
            else:
                k_uniform = float(k_result)
                junction_warning[node_id] = ""

            for port in ja.connected_ports:
                if port.flow_into_junction:
                    continue  # inlet port -- fitting loss is attributed at outlet ports only
                sres = seg_result.get(port.edge_key)
                if sres is None:
                    continue
                sres.fitting_loss_pa = k_uniform * airflow.velocity_pressure(air_density, sres.velocity_ms)

        for sres in seg_result.values():
            sres.total_loss_pa = sres.friction_loss_pa + sres.fitting_loss_pa

        # Phase F: pressure propagation, root -> leaves (0 Pa reference at the balancing terminal).
        static_pressure = {root_node_id: 0.0}
        for node_id in order[1:]:
            parent = parent_node[node_id]
            edge = parent_edge[node_id]
            port_at_node = port_lookup[(node_id, edge.tag)]
            loss = seg_result[edge.tag].total_loss_pa
            if port_at_node.flow_into_junction:
                static_pressure[node_id] = static_pressure[parent] - loss
                downstream_pressure = static_pressure[node_id]
            else:
                static_pressure[node_id] = static_pressure[parent] + loss
                downstream_pressure = static_pressure[parent]
            seg_result[edge.tag].cumulative_pressure_pa = downstream_pressure

        # Phase G: assemble results and write back to FreeCAD properties. Everything
        # up to this point only touches local Python structures, so a failure in any
        # earlier phase never leaves a partial write on a junction/segment object.
        junction_results = {}
        for node_id in comp_nodes:
            junction_obj = junction_map[parser.node_key(node_id)]
            degree = len(adjacency[node_id])
            if degree == 1:
                edge = adjacency[node_id][0]
                total_flow = edge_flow_lps[edge]
                is_source = not port_lookup[(node_id, edge.tag)].flow_into_junction
            else:
                total_in = 0.0
                total_out = 0.0
                for edge in adjacency[node_id]:
                    port = port_lookup[(node_id, edge.tag)]
                    mag = edge_flow_lps[edge]
                    if port.flow_into_junction:
                        total_in += mag
                    else:
                        total_out += mag
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

        for edge_ref in comp_edges:
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

        critical_node_id = max(terminal_ids, key=lambda n: abs(static_pressure[n]))

        return ComponentResult(
            reference_terminal_key=parser.node_key(root_node_id),
            segments=[seg_result[e.tag] for e in comp_edges],
            junctions=[junction_results[n] for n in comp_nodes],
            critical_terminal_key=parser.node_key(critical_node_id),
            critical_pressure_pa=abs(static_pressure[critical_node_id]),
        )

    # ------------------------------------------------------------------
    # Per-segment sizing
    # ------------------------------------------------------------------

    def _size_segment(self, net, seg_obj, flow_lps):
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
            return SegmentResult(key=key, obj=seg_obj, flow_lps=0.0)

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
