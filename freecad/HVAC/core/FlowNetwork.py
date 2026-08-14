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
Whole-network flow-distribution solve, shared by AirflowSolver (pressure/loss
calculation) and DuctSizer (dimension calculation) -- both need exactly the
same "how much air moves through each segment" answer before doing anything
size- or pressure-specific with it.

Assumes each connected sub-network of the duct network is a tree: exactly one
terminal (degree-1 junction) is left with no Design Flow Rate (the balancing
terminal, e.g. the AHU/fan connection); every other terminal carries a
user-specified design flow rate (e.g. a diffuser/grille). Flow magnitudes are
solved by mass conservation from the leaves toward the balancing terminal,
using the existing per-port flow_into_junction data (derived from base
geometry direction) to know each segment's fixed physical flow direction.

Loops (non-tree sub-networks) are rejected with a clear error rather than
solved.
"""

from collections import deque
from dataclasses import dataclass, field


class FlowSolveError(Exception):
    """Raised when a sub-network's flow distribution cannot be solved (loop, bad boundary conditions, missing data)."""
    pass


@dataclass
class FlowComponent:
    """
    One connected, successfully-solved sub-network: its topology (as a
    rooted tree from the balancing terminal) plus the solved flow magnitude
    through every segment. Callers (AirflowSolver, DuctSizer) do their own
    size- or pressure-specific work per segment/junction from here.
    """
    comp_nodes: set
    comp_edges: set
    adjacency: dict          # node_id -> [edge_ref, ...]
    edge_endpoints: dict     # edge_ref -> (u, v) analysis node ids
    root_node_id: int
    order: list              # BFS order from root (root first)
    parent_node: dict        # node_id -> parent node_id (root excluded)
    parent_edge: dict        # node_id -> edge_ref connecting to parent (root excluded)
    edge_flow_lps: dict      # edge_ref -> solved flow magnitude (L/s)
    analysis_by_node: dict   # node_id -> JunctionAnalysis
    port_lookup: dict        # (node_id, edge_key) -> JunctionPort
    terminal_ids: list = field(default_factory=list)  # node_ids with degree == 1


def solve_flow_components(net_obj):
    """
    Resolve every connected sub-network of net_obj into a FlowComponent.

    Returns (parser, junction_map, segment_map, components, warnings):
      - components: list of FlowComponent, one per successfully-solved
        sub-network.
      - warnings: human-readable messages for any sub-network that could NOT
        be solved (loop, bad boundary conditions, missing junction/segment
        data) -- these are skipped, not raised, so one bad sub-network
        doesn't block others in the same document.
    """
    parser = net_obj.Proxy.getParser(rebuild=True)
    segment_map = net_obj.Proxy.collectSegmentObjects()
    junction_map = net_obj.Proxy.collectJunctionObjects()

    adjacency = {node_id: list(parser.node_edges(node_id)) for node_id in parser.nodes()}
    edge_endpoints = {}
    for node_id, edge_refs in adjacency.items():
        for edge_ref in edge_refs:
            edge_endpoints[edge_ref] = parser.edge_analysis_nodes(edge_ref)

    warnings = []
    components = []
    for comp_nodes, comp_edges in _find_node_edge_components(adjacency, edge_endpoints):
        if not comp_edges:
            continue
        try:
            components.append(
                _solve_component_flow(
                    parser, comp_nodes, comp_edges, adjacency, edge_endpoints, segment_map, junction_map
                )
            )
        except FlowSolveError as exc:
            warnings.append(str(exc))

    return parser, junction_map, segment_map, components, warnings


# ----------------------------------------------------------------------------
# Topology helpers
# ----------------------------------------------------------------------------

def _find_node_edge_components(adjacency, edge_endpoints):
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


# ----------------------------------------------------------------------------
# Per-component flow solve
# ----------------------------------------------------------------------------

def _solve_component_flow(parser, comp_nodes, comp_edges, adjacency, edge_endpoints, segment_map, junction_map):
    n_nodes = len(comp_nodes)
    n_edges = len(comp_edges)
    if n_edges != n_nodes - 1:
        raise FlowSolveError(
            "Loop detected: a sub-network with {} junction(s) has {} duct segment(s); "
            "a tree requires exactly {}. Loops are not supported.".format(
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
            raise FlowSolveError(
                "Junction data missing for node '{}'; recompute the network before "
                "calculating.".format(node_key)
            )
        ja = parser.build_junction_analysis(node_id, segment_map)
        if ja is None:
            raise FlowSolveError("Could not analyze junction '{}'.".format(junction_obj.Label))
        analysis_by_node[node_id] = ja
        for port in ja.connected_ports:
            port_lookup[(node_id, port.edge_key)] = port

    for edge_ref in comp_edges:
        if segment_map.get(edge_ref.tag) is None:
            raise FlowSolveError(
                "Segment data missing for edge '{}'; recompute the network before "
                "calculating.".format(edge_ref.tag)
            )

    # Terminals and the balancing (unspecified) terminal.
    terminal_ids = [n for n in comp_nodes if len(adjacency[n]) == 1]
    if len(terminal_ids) < 2:
        any_label = junction_map[parser.node_key(next(iter(comp_nodes)))].Label
        raise FlowSolveError(
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
        raise FlowSolveError(
            "All terminals in this sub-network have a Design Flow Rate set ({}). "
            "Leave exactly one terminal's Design Flow Rate blank to act as the "
            "balancing terminal.".format(labels)
        )
    if len(unspecified) > 1:
        labels = ", ".join(j.Label for _, j in unspecified)
        raise FlowSolveError(
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

    # Flow-magnitude accumulation, leaves -> root.
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
            raise FlowSolveError(
                "Inconsistent flow direction at junction '{}': incoming and outgoing design "
                "flows don't balance given the current segment directions. Check base geometry "
                "direction (HVAC_ReverseGeometryDirection / Edit Duct Directions).".format(
                    junction_obj.Label
                )
            )
        edge_flow_lps[edge] = max(p_mag, 0.0)

    return FlowComponent(
        comp_nodes=comp_nodes,
        comp_edges=comp_edges,
        adjacency=adjacency,
        edge_endpoints=edge_endpoints,
        root_node_id=root_node_id,
        order=order,
        parent_node=parent_node,
        parent_edge=parent_edge,
        edge_flow_lps=edge_flow_lps,
        analysis_by_node=analysis_by_node,
        port_lookup=port_lookup,
        terminal_ids=terminal_ids,
    )
