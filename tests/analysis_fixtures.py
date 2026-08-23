"""
Shared pure-Python builders for freecad.HVAC.analysis package tests. No
FreeCAD/conftest stubbing anywhere in this file or its callers -- proving
analysis/ genuinely has no such dependency (unlike every other test file in
this directory, which needs conftest's FreeCAD/Part/PySide stubs).
"""

from freecad.HVAC.analysis.model import (
    AirState, ComponentModel, NetworkModel, NodeModel, PortModel, SectionModel, SegmentModel,
)

AIR_DENSITY = 1.204
AIR_VISCOSITY = 1.51e-5
DEFAULT_ROUGHNESS_MM = 0.09


def circular_section(diameter_mm):
    return SectionModel(profile="Circular", diameter_mm=diameter_mm)


def port(edge_key, node_id, flow_into_node, section=None):
    return PortModel(edge_key=edge_key, node_id=node_id, flow_into_node=flow_into_node,
                      section=section or circular_section(0.0))


def primary(node_id, ports, loss_evaluator=None):
    return ComponentModel(component_id=node_id + "_Primary", role="primary", ports=ports,
                           loss_evaluator=loss_evaluator)


def air_state(density=AIR_DENSITY, viscosity=AIR_VISCOSITY):
    return AirState(density_kg_m3=density, kinematic_viscosity_m2_s=viscosity)


def base_tree(j3_flow=50.0, j4_flow=30.0, segA_len=5000.0, segB_len=3000.0, segC_len=6000.0,
              segA_dia=200.0, segB_dia=150.0, segC_dia=150.0, loss_evaluator=None,
              air=None, roughness_mm=DEFAULT_ROUGHNESS_MM):
    """
    Same supply-tree topology as tests/network_fixtures.py's base_tree(),
    built directly as pure analysis.model dataclasses instead of FreeCAD
    fakes:

        J1 (AHU, balancing terminal) --A--> J2 (tee)
                                                |--B--> J3 (leaf, j3_flow L/s)
                                                '--C--> J4 (leaf, j4_flow L/s)

    loss_evaluator, if given, is J2's Primary's own loss_evaluator (e.g. a
    fixed-K tee) -- J1/J3/J4 never have one (matching end_terminal_marker's
    "no loss data" default).
    """
    nodes = {
        "N1": NodeModel("N1", "end", 1, [port("A", "N1", False)], 0.0,
                         primary("N1", [port("A", "N1", False)])),
        "N2": NodeModel("N2", "branch", 3,
                         [port("A", "N2", True), port("B", "N2", False), port("C", "N2", False)], 0.0,
                         primary("N2", [port("A", "N2", True), port("B", "N2", False), port("C", "N2", False)],
                                 loss_evaluator)),
        "N3": NodeModel("N3", "end", 1, [port("B", "N3", True)], j3_flow,
                         primary("N3", [port("B", "N3", True)])),
        "N4": NodeModel("N4", "end", 1, [port("C", "N4", True)], j4_flow,
                         primary("N4", [port("C", "N4", True)])),
    }
    segments = {
        "A": SegmentModel("A", circular_section(segA_dia), segA_len, roughness_mm),
        "B": SegmentModel("B", circular_section(segB_dia), segB_len, roughness_mm),
        "C": SegmentModel("C", circular_section(segC_dia), segC_len, roughness_mm),
    }
    edges = {"A": ("N1", "N2"), "B": ("N2", "N3"), "C": ("N2", "N4")}
    return NetworkModel(nodes=nodes, segments=segments, edges=edges, air=air or air_state())
