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
Works out how much air flows through every segment. Both AirflowSolver
(pressure/loss) and DuctSizer (duct dimensions) need this same answer
before they can do their own, separate calculation, so it's solved once
here and shared.

How it works: each connected sub-network must be a tree (no loops). Exactly
one open end (terminal) is left with no Design Flow Rate -- that's the
balancing terminal (e.g. the fan/AHU connection), and its flow is whatever
makes everything else balance. Every other terminal has a user-set design
flow rate (e.g. a diffuser). Starting from the terminals and working inward
towards the balancing terminal, each segment's flow is just conservation of
mass: flow out of a junction equals flow into it. A segment's own fixed
flow direction is read from its ports (flow_into_junction, already derived
from the base geometry's direction).

A sub-network that isn't a tree (has a loop) is reported as an error rather
than solved.

Topology (connectivity, degree, BFS order) is read straight from
parser.analysis_graph, the same graph NetworkParser already builds --
nothing is rebuilt here. Each edge on that graph carries a "key" attribute
holding the real EdgeRef, so a graph traversal can always be traced back to
the actual segment it represents. Two segments never run directly between
the same pair of junctions in a valid network, so a plain graph (not a
multigraph) is enough to represent every edge unambiguously.
"""

from dataclasses import dataclass, field

from ..utils.hvaclib import nx


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
    graph: object             # nx.Graph subgraph of parser.analysis_graph, scoped to comp_nodes; edge attr "key" -> EdgeRef
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

    warnings = []
    components = []
    for comp_nodes in nx.connected_components(parser.analysis_graph):
        subgraph = parser.analysis_graph.subgraph(comp_nodes)
        if subgraph.number_of_edges() == 0:
            continue
        try:
            components.append(
                _solve_component_flow(parser, comp_nodes, subgraph, segment_map, junction_map)
            )
        except FlowSolveError as exc:
            warnings.append(str(exc))

    return parser, junction_map, segment_map, components, warnings


# ----------------------------------------------------------------------------
# Per-component flow solve
# ----------------------------------------------------------------------------

def _solve_component_flow(parser, comp_nodes, graph, segment_map, junction_map):
    # Step 1: a tree with N nodes always has exactly N-1 edges -- if this
    # sub-network has more edges than that, it must contain a loop.
    comp_edges = {edge_ref for _u, _v, edge_ref in graph.edges(data="key")}
    n_nodes = len(comp_nodes)
    n_edges = len(comp_edges)
    if n_edges != n_nodes - 1:
        raise FlowSolveError(
            "Loop detected: a sub-network with {} junction(s) has {} duct segment(s); "
            "a tree requires exactly {}. Loops are not supported.".format(
                n_nodes, n_edges, n_nodes - 1
            )
        )

    # Step 2: resolve every node's FreeCAD junction object and port data up front.
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

    # Step 3: find every open end (terminal), and check exactly one of them
    # is the balancing terminal (no Design Flow Rate set).
    terminal_ids = [n for n in comp_nodes if graph.degree[n] == 1]
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

    # Step 4: walk the tree breadth-first from the balancing terminal, so we
    # know each node's parent and which edge connects them. This also fixes
    # the order flow gets solved in, in the next step.
    parent_node = {}
    parent_edge = {}
    order = [root_node_id]
    for u, v in nx.bfs_edges(graph, root_node_id):
        parent_node[v] = u
        parent_edge[v] = graph[u][v]["key"]
        order.append(v)

    # Step 5: solve each segment's flow, working from the leaves back to the
    # root (reversed BFS order) so a junction's other edges are always
    # already solved by the time we need them for the one going to its
    # parent.
    edge_flow_lps = {}
    for node_id in reversed(order[1:]):
        edge = parent_edge[node_id]

        if graph.degree[node_id] == 1:
            # A leaf's own edge simply carries its design flow rate.
            junction_obj = junction_map[parser.node_key(node_id)]
            edge_flow_lps[edge] = abs(float(getattr(junction_obj, "DesignFlowRate", 0.0) or 0.0))
            continue

        # Add up the flow on every OTHER edge at this junction (already
        # solved), split into what's flowing in vs. out.
        known_in = 0.0
        known_out = 0.0
        for _u, _v, edge_ref in graph.edges(node_id, data="key"):
            if edge_ref == edge:
                continue
            port = port_lookup[(node_id, edge_ref.tag)]
            mag = edge_flow_lps[edge_ref]
            if port.flow_into_junction:
                known_in += mag
            else:
                known_out += mag

        # Mass conservation: total in == total out, so whatever's missing on
        # the "parent" edge must make up the difference. Which side it's
        # missing from depends on which way that edge itself points.
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
        graph=graph,
        root_node_id=root_node_id,
        order=order,
        parent_node=parent_node,
        parent_edge=parent_edge,
        edge_flow_lps=edge_flow_lps,
        analysis_by_node=analysis_by_node,
        port_lookup=port_lookup,
        terminal_ids=terminal_ids,
    )
