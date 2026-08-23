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
FreeCAD-facing adapter over analysis/pressure.py's pure PressureSolver --
see that module (and analysis/flow.py, analysis/paths.py) for the actual
airflow/pressure-drop algorithm. This module's job is only: build a
NetworkModel from the real DuctNetwork (via FlowNetwork.solve_flow_components,
which itself delegates to core/_analysis_adapter.py), run PressureSolver,
write the results back onto the real segment/junction/component Calc*
properties, and hand back the same FreeCAD-facing result dataclasses this
module has always had (SegmentResult/JunctionResult/ComponentResult/
AirflowSolveResult) so existing callers (ui/TaskPanel.py, ui/Observer.py)
don't need to change.
"""

from dataclasses import dataclass, field

from .FlowNetwork import FlowSolveError as AirflowSolveError
from .FlowNetwork import solve_flow_components
from ..analysis.pressure import K_DEFAULT, PressureSolver


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
        network_model, segment_map, junction_map, component_map, components, flow_warnings = (
            solve_flow_components(self.net_obj)
        )

        result = AirflowSolveResult(warnings=list(flow_warnings))
        trees, pressure_warnings = PressureSolver().solve(network_model, components)
        result.warnings.extend(pressure_warnings)

        for tree in trees:
            result.components.append(
                self._map_component_result(tree, segment_map, junction_map, component_map)
            )
        return result

    # ------------------------------------------------------------------
    # Map a pure ComponentTreeResult back onto real FreeCAD objects
    # ------------------------------------------------------------------

    @staticmethod
    def _map_component_result(tree, segment_map, junction_map, component_map):
        seg_results = []
        for edge_key, sres in tree.segments.items():
            obj = segment_map[edge_key]
            # fitting_loss_pa keeps its historical meaning here: everything
            # except straight-duct friction -- a node's own Primary
            # contribution plus that edge's own Inline chain, kept as two
            # separate fields in the pure layer (see SegmentModel) so
            # analysis.paths can report them separately, but combined again
            # for this FreeCAD-facing result since existing callers
            # (ui/TaskPanel.py) only ever show the combined figure.
            fitting_loss_pa = sres.junction_loss_pa + sres.component_loss_pa

            obj.CalcFlowRate = sres.flow_lps
            obj.CalcVelocity = sres.velocity_ms
            obj.CalcReynoldsNumber = sres.reynolds
            obj.CalcFrictionLoss = sres.friction_loss_pa
            obj.CalcFittingLoss = fitting_loss_pa
            obj.CalcTotalLoss = sres.total_loss_pa
            obj.CalcCumulativePressure = sres.cumulative_pressure_pa

            seg_results.append(SegmentResult(
                key=edge_key, obj=obj, flow_lps=sres.flow_lps, velocity_ms=sres.velocity_ms,
                reynolds=sres.reynolds, friction_loss_pa=sres.friction_loss_pa,
                fitting_loss_pa=fitting_loss_pa, total_loss_pa=sres.total_loss_pa,
                cumulative_pressure_pa=sres.cumulative_pressure_pa,
            ))

        junc_results = []
        for node_id, jres in tree.junctions.items():
            obj = junction_map[node_id]
            obj.CalcTotalFlowRate = jres.total_flow_lps
            obj.CalcStaticPressure = jres.static_pressure_pa
            obj.IsFlowSource = jres.is_source
            obj.CalcLossWarning = jres.warning

            junc_results.append(JunctionResult(
                key=node_id, obj=obj, total_flow_lps=jres.total_flow_lps,
                static_pressure_pa=jres.static_pressure_pa, is_source=jres.is_source, warning=jres.warning,
            ))

        # Per-Inline-component results -- written directly onto each
        # component's own Calc* properties (a Primary's own contribution
        # has no single "the" component to attribute it to when it has
        # several real ports, so it's only ever reported via the segment(s)
        # it touches above, matching how this always worked).
        for component_id, cres in tree.components.items():
            comp_obj = component_map.get(component_id)
            if comp_obj is None:
                continue
            comp_obj.CalcFlowRate = cres.flow_lps
            comp_obj.CalcVelocity = cres.velocity_ms
            comp_obj.CalcLossCoefficient = cres.loss_coefficient
            comp_obj.CalcPressureDrop = cres.pressure_drop_pa

        critical = tree.critical_path
        return ComponentResult(
            reference_terminal_key=tree.reference_terminal_id,
            segments=seg_results,
            junctions=junc_results,
            critical_terminal_key=critical.terminal_node_id if critical is not None else "",
            critical_pressure_pa=critical.path.loss.total_pa if critical is not None else 0.0,
        )
