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
FreeCAD-facing adapter over analysis/flow.py's pure flow-distribution solve
-- see that module for the actual algorithm (tree/loop check, balancing-
terminal/conservation model). This module's only job is turning a real
DuctNetwork into a NetworkModel (via _analysis_adapter.build_network_model)
and solving it; AirflowSolver.py and DuctSizer.py both import
solve_flow_components() from here to share that one step.
"""

from . import _analysis_adapter
from ..analysis import flow as _flow

FlowSolveError = _flow.FlowSolveError
FlowComponent = _flow.FlowComponent


def solve_flow_components(net_obj):
    """
    Resolve every connected sub-network of net_obj into a FlowComponent.

    Returns (network_model, segment_map, junction_map, component_map, components, warnings):
      - network_model: the pure NetworkModel built from net_obj -- pass this
        (plus `components`) straight into analysis.pressure.PressureSolver/
        analysis.sizing.*/analysis.balancing.PressureBalanceCoordinator.
      - segment_map/junction_map/component_map: edge_key/node_id/
        ComponentModel.component_id -> real FreeCAD object, for mapping pure
        results back.
      - components: list of analysis.flow.FlowComponent, one per
        successfully-solved sub-network.
      - warnings: human-readable messages for any sub-network that could NOT
        be solved (loop, bad boundary conditions, missing data) -- these are
        skipped, not raised, so one bad sub-network doesn't block others in
        the same document.
    """
    network_model, segment_map, junction_map, component_map = _analysis_adapter.build_network_model(net_obj)
    components, warnings = _flow.solve_flow_components(network_model)
    return network_model, segment_map, junction_map, component_map, components, warnings
