"""
Pure tests for freecad.HVAC.analysis.flow -- no FreeCAD/conftest stubbing
needed, since analysis/ has no FreeCAD dependency at all.
"""

from analysis_fixtures import base_tree, circular_section, port

from freecad.HVAC.analysis import flow
from freecad.HVAC.analysis.model import SegmentModel


def test_conservation_on_a_simple_tee():
    net = base_tree()
    components, warnings = flow.solve_flow_components(net)

    assert warnings == []
    assert len(components) == 1
    comp = components[0]
    assert comp.root_node_id == "N1"
    assert comp.edge_flow_lps == {"A": 80.0, "B": 50.0, "C": 30.0}
    assert set(comp.terminal_ids) == {"N1", "N3", "N4"}


def test_loop_detected_and_reported_as_warning():
    net = base_tree()
    # Add a 4th edge directly between N3 and N4, closing a loop (N2-N3-N4-N2).
    net.edges["D"] = ("N3", "N4")
    net.segments["D"] = SegmentModel("D", circular_section(150.0), 1000.0)
    net.nodes["N3"].ports.append(port("D", "N3", False))
    net.nodes["N4"].ports.append(port("D", "N4", True))

    components, warnings = flow.solve_flow_components(net)

    assert components == []
    assert len(warnings) == 1
    assert "Loop detected" in warnings[0]


def test_all_terminals_specified_is_an_error():
    net = base_tree()
    net.nodes["N1"].design_flow_lps = 80.0  # every terminal now has a design flow -- no balancing terminal left

    components, warnings = flow.solve_flow_components(net)
    assert components == []
    assert len(warnings) == 1
    assert "Design Flow Rate" in warnings[0]


def test_multiple_unspecified_terminals_is_an_error():
    net = base_tree()
    net.nodes["N3"].design_flow_lps = 0.0  # now both N1 and N3 look like balancing-terminal candidates

    components, warnings = flow.solve_flow_components(net)
    assert components == []
    assert len(warnings) == 1
    assert "Design Flow Rate" in warnings[0]


def test_inconsistent_flow_direction_is_an_error():
    net = base_tree()
    # Flip B's port direction at N2 so it now looks like flow ENTERS N2 from
    # B too (alongside C), leaving nothing to balance A's own outflow.
    net.nodes["N2"].ports = [
        port("A", "N2", True), port("B", "N2", True), port("C", "N2", False),
    ]

    components, warnings = flow.solve_flow_components(net)
    assert components == []
    assert len(warnings) == 1
    assert "Inconsistent flow direction" in warnings[0]


def test_missing_segment_data_is_an_error():
    net = base_tree()
    del net.segments["B"]

    components, warnings = flow.solve_flow_components(net)
    assert components == []
    assert len(warnings) == 1
    assert "Segment data missing" in warnings[0]
