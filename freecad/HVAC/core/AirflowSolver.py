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

Flow distribution (which sub-networks are solvable trees, and how much air
moves through each segment) is solved by FlowNetwork.solve_flow_components;
see that module for the balancing-terminal/conservation model. This module
takes it from there: given a segment's own duct size, compute its velocity/
Reynolds number/friction loss (Darcy-Weisbach with the Altshul-Tsal friction
factor), compute each junction's fitting/dynamic loss (pluggable via the
library's loss_module/loss_function, falling back to a generic coefficient),
and propagate static pressure outward from the balancing terminal (0 Pa
reference).
"""

from dataclasses import asdict, dataclass, field

from ..utils import hvaclib
from . import airflow
from .FlowNetwork import FlowSolveError as AirflowSolveError
from .FlowNetwork import solve_flow_components


K_DEFAULT = 0.3


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
        adjacency = comp.adjacency
        edge_flow_lps = comp.edge_flow_lps
        port_lookup = comp.port_lookup
        analysis_by_node = comp.analysis_by_node

        # Phase D: per-segment sizing (flow, velocity, Reynolds, friction loss).
        seg_result = {}
        for edge_ref in comp.comp_edges:
            seg_obj = segment_map[edge_ref.tag]
            seg_result[edge_ref.tag] = self._size_segment(net, seg_obj, edge_flow_lps[edge_ref])

        # Phase E: fitting/dynamic loss via pluggable per-type loss functions.
        junction_warning = {}
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        air_density = float(getattr(net, "AirDensity", 1.204) or 1.204)
        air_viscosity = float(getattr(net, "AirKinematicViscosity", 1.51e-5) or 1.51e-5)

        for node_id in comp.comp_nodes:
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

        # Phase G: assemble results and write back to FreeCAD properties. Everything
        # up to this point only touches local Python structures, so a failure in any
        # earlier phase never leaves a partial write on a junction/segment object.
        junction_results = {}
        for node_id in comp.comp_nodes:
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
