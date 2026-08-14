"""
Shared lightweight fake stand-ins for the FreeCAD DuctNetwork/DuctJunction/
DuctSegment objects and the DuctNetworkParser API surface that FlowNetwork.py
(and therefore both AirflowSolver and DuctSizer) depends on. Lets solver
tests run against a small synthetic tree network without a real FreeCAD
installation or real base geometry.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core.NetworkParser import EdgeRef, JunctionAnalysis, JunctionPort


AIR_DENSITY = 1.204
AIR_VISCOSITY = 1.51e-5
DEFAULT_ROUGHNESS_MM = 0.09


class FakeObj:
    """Minimal stand-in for a FreeCAD DocumentObject: free attribute get/set."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _edge(tag):
    return EdgeRef(obj_name="Line_{}".format(tag), local_index=0, tag=tag)


def _port(edge_key, segment_end, flow_into_junction):
    return JunctionPort(
        edge_key=edge_key,
        segment_end=segment_end,
        position=(0.0, 0.0, 0.0),
        direction=(1.0, 0.0, 0.0),
        profile="Circular",
        section_params={},
        attachment="Center",
        user_offset=(0.0, 0.0, 0.0),
        profile_x_axis=None,
        flow_role=("inlet" if flow_into_junction else "outlet"),
        flow_direction=(1.0, 0.0, 0.0),
        flow_into_junction=flow_into_junction,
    )


class FakeParser:
    """
    node_edges/edge_endpoints/node_key/build_junction_analysis, driven from a
    plain description of {node_id: [(edge_tag, "start"|"end"), ...]}.
    """

    def __init__(self, node_ports, edge_endpoints):
        # node_ports: {node_id: [(edge_tag, segment_end), ...]}
        self._node_ports = node_ports
        self._edge_endpoints = edge_endpoints  # {edge_tag: (u, v)}  (u = "start" node, v = "end" node)

    def nodes(self):
        return sorted(self._node_ports.keys())

    def node_edges(self, node_id):
        return [_edge(tag) for tag, _end in self._node_ports[node_id]]

    def edge_analysis_nodes(self, edge_ref):
        return self._edge_endpoints[edge_ref.tag]

    def node_key(self, node_id):
        return "N{}".format(node_id)

    def build_junction_analysis(self, node_id, segment_map):
        ports = [
            _port(tag, end, flow_into_junction=(end == "end"))
            for tag, end in self._node_ports[node_id]
        ]
        degree = len(ports)
        if degree <= 0:
            return None
        return JunctionAnalysis(
            topology=("end" if degree == 1 else "branch"),
            family="",
            family_tags=[],
            family_key="",
            connected_ports=ports,
            point=(0.0, 0.0, 0.0),
            degree=degree,
            port_origins=[],
            edge_vectors=[],
            edge_angles={},
            edge_eccentricities={},
            collinear_pairs=[],
            orthogonal_pairs=[],
            is_coplanar=True,
        )


class FakeProxy:
    def __init__(self, parser, segment_map, junction_map):
        self._parser = parser
        self._segment_map = segment_map
        self._junction_map = junction_map

    def getParser(self, rebuild=False):
        return self._parser

    def collectSegmentObjects(self):
        return dict(self._segment_map)

    def collectJunctionObjects(self):
        return dict(self._junction_map)


def make_segment(tag, diameter_mm, length_mm, roughness_mm=0.0, profile="Circular",
                  width_mm=0.0, height_mm=0.0):
    return FakeObj(
        Label=tag,
        SegmentKey=tag,
        Name=tag,
        Profile=profile,
        Diameter=diameter_mm,
        Width=width_mm,
        Height=height_mm,
        Roughness=roughness_mm,
        EffectiveLength=length_mm,
    )


def make_junction(label, design_flow=0.0, library_id="testlib", type_id="branch_tee_generic"):
    return FakeObj(
        Label=label,
        LibraryId=library_id,
        TypeId=type_id,
        Family="",
        DesignFlowRate=design_flow,
    )


def make_net(parser, segment_map, junction_map, **extra_props):
    props = dict(
        AirDensity=AIR_DENSITY,
        AirKinematicViscosity=AIR_VISCOSITY,
        DefaultRoughness=DEFAULT_ROUGHNESS_MM,
    )
    props.update(extra_props)
    net = FakeObj(**props)
    net.Proxy = FakeProxy(parser, segment_map, junction_map)
    return net


def base_tree(j3_flow=50.0, j4_flow=30.0, segA_len=5000.0, segB_len=3000.0, segC_len=6000.0,
              segA_dia=200.0, segB_dia=150.0, segC_dia=150.0,
              segA_kwargs=None, segB_kwargs=None, segC_kwargs=None, net_extra_props=None):
    """
    A simple supply tree, reused across solver tests:

        J1 (AHU, balancing terminal) --segA--> J2 (tee)
                                                    |--segB--> J3 (leaf, j3_flow L/s)
                                                    '--segC--> J4 (leaf, j4_flow L/s)
    """
    node_ports = {
        1: [("A", "start")],
        2: [("A", "end"), ("B", "start"), ("C", "start")],
        3: [("B", "end")],
        4: [("C", "end")],
    }
    edge_endpoints = {"A": (1, 2), "B": (2, 3), "C": (2, 4)}
    parser = FakeParser(node_ports, edge_endpoints)

    segment_map = {
        "A": make_segment("A", segA_dia, segA_len, **(segA_kwargs or {})),
        "B": make_segment("B", segB_dia, segB_len, **(segB_kwargs or {})),
        "C": make_segment("C", segC_dia, segC_len, **(segC_kwargs or {})),
    }
    junction_map = {
        "N1": make_junction("J1", design_flow=0.0, type_id="end_terminal_marker"),
        "N2": make_junction("J2", design_flow=0.0, type_id="branch_tee_generic"),
        "N3": make_junction("J3", design_flow=j3_flow, type_id="end_terminal_marker"),
        "N4": make_junction("J4", design_flow=j4_flow, type_id="end_terminal_marker"),
    }
    net = make_net(parser, segment_map, junction_map, **(net_extra_props or {}))
    return net, segment_map, junction_map
