# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Focused tests for DuctNetworkParser's flow classification layer
(classify_flow / classify_junction_family's degree-2 family split) -- see
freecad/HVAC/core/NetworkParser.py and TOPOLOGY_CLASSIFICATION.md.

These call the parser's own pure classification helpers directly against
hand-built JunctionPort/EdgePair data, rather than driving the full
geometric-graph pipeline -- the geometric graph (collinear_pairs/
edge_eccentricities/...) is already covered by the existing family
classification behavior; only the new flow_class/qualifiers/derived_values
logic layered on top of it is under test here.
"""
import math

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
import pytest

from freecad.HVAC.core.NetworkParser import DuctNetworkParser, EdgePair, JunctionPort


def _parser():
    p = DuctNetworkParser.__new__(DuctNetworkParser)
    p.tol = 1e-6
    p.angle_tol_degree = 2.0
    return p


def _port(profile, w=None, h=None, d=None, position=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0),
          profile_x_axis=(1.0, 0.0, 0.0), flow_role="unknown", edge_key="A"):
    section = {"Diameter": d} if profile == "Circular" else {"Width": w, "Height": h}
    return JunctionPort(
        edge_key=edge_key, segment_end="end", position=position, direction=direction,
        profile=profile, section_params=section, attachment="Center",
        user_offset=(0.0, 0.0, 0.0), profile_x_axis=profile_x_axis,
        flow_role=flow_role, flow_direction=direction, flow_into_junction=None,
    )


class _NodeAnalysis:
    """Minimal duck-typed stand-in -- only the attributes the classifiers
    under test actually read (classify_junction_family reads several
    geometric fields unconditionally before branching on degree)."""

    def __init__(self, connected_ports, collinear_pairs=(), edge_eccentricities=None, degree=None):
        self.connected_ports = connected_ports
        self.collinear_pairs = list(collinear_pairs)
        self.edge_eccentricities = dict(edge_eccentricities or {})
        self.degree = degree if degree is not None else len(connected_ports)
        self.port_origins = [p.position for p in connected_ports]
        self.edge_vectors = [p.direction for p in connected_ports]
        self.edge_angles = {}
        self.orthogonal_pairs = []
        self.is_coplanar = True


# ----------------------------------------------------------------------
# classify_junction_family: degree-2 straight/offset/transition split
# ----------------------------------------------------------------------

def test_degree2_same_section_zero_eccentricity_is_straight():
    parser = _parser()
    ports = [_port("Circular", d=300.0), _port("Circular", d=300.0)]
    analysis = _NodeAnalysis(
        ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=0.0)],
        edge_eccentricities={str((0, 1)): 0.0},
    )
    family, tags = parser.classify_junction_family(analysis)
    assert (family, tags) == ("straight", [])


def test_degree2_same_section_nonzero_eccentricity_is_offset():
    parser = _parser()
    ports = [_port("Circular", d=300.0), _port("Circular", d=300.0)]
    analysis = _NodeAnalysis(
        ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=5.0)],
        edge_eccentricities={str((0, 1)): 5.0},
    )
    family, tags = parser.classify_junction_family(analysis)
    assert (family, tags) == ("offset", [])


def test_degree2_different_size_is_transition_even_with_zero_eccentricity():
    parser = _parser()
    ports = [_port("Circular", d=300.0), _port("Circular", d=200.0)]
    analysis = _NodeAnalysis(
        ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=0.0)],
        edge_eccentricities={str((0, 1)): 0.0},
    )
    family, tags = parser.classify_junction_family(analysis)
    assert (family, tags) == ("transition", [])


def test_degree2_different_profile_is_transition():
    parser = _parser()
    ports = [_port("Circular", d=300.0), _port("Rectangular", w=300.0, h=300.0)]
    analysis = _NodeAnalysis(
        ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=0.0)],
        edge_eccentricities={str((0, 1)): 0.0},
    )
    family, tags = parser.classify_junction_family(analysis)
    assert (family, tags) == ("transition", [])


# ----------------------------------------------------------------------
# classify_flow (through, degree 2): expansion/contraction/constant + area_ratio
# ----------------------------------------------------------------------

def test_through_flow_expansion_area_ratio():
    parser = _parser()
    inlet = _port("Circular", d=200.0, flow_role="inlet")
    outlet = _port("Circular", d=300.0, flow_role="outlet")
    flow_class, qualifiers, derived = parser._classify_through_flow(inlet, outlet)
    assert flow_class == "expansion"
    assert derived["area_ratio"] == pytest.approx((300.0 ** 2) / (200.0 ** 2))
    assert qualifiers["profile_relation"] == "same"


def test_through_flow_contraction():
    parser = _parser()
    inlet = _port("Circular", d=300.0, flow_role="inlet")
    outlet = _port("Circular", d=200.0, flow_role="outlet")
    flow_class, _, derived = parser._classify_through_flow(inlet, outlet)
    assert flow_class == "contraction"
    assert derived["area_ratio"] == pytest.approx((200.0 ** 2) / (300.0 ** 2))


def test_through_flow_constant_same_area():
    parser = _parser()
    inlet = _port("Circular", d=300.0, flow_role="inlet")
    outlet = _port("Circular", d=300.0, flow_role="outlet")
    flow_class, _, derived = parser._classify_through_flow(inlet, outlet)
    assert flow_class == "constant"
    assert derived["area_ratio"] == pytest.approx(1.0)


def test_through_flow_mixed_profile_relation_and_transition_form():
    parser = _parser()
    inlet = _port("Rectangular", w=300.0, h=200.0, flow_role="inlet")
    outlet = _port("Circular", d=250.0, flow_role="outlet")
    _, qualifiers, _ = parser._classify_through_flow(inlet, outlet)
    assert qualifiers["profile_relation"] == "mixed"
    assert qualifiers["transition_form"] == "profile_change"


# ----------------------------------------------------------------------
# Rectangular transition alignment (concentric/eccentric/double_eccentric/offset)
# ----------------------------------------------------------------------
# inlet is a 400x300 rectangle with its local frame pinned by profile_x_axis
# so local_x == world +X ("left") and local_y == world +Y ("top") -- see
# hvaclib.compute_port_position's own "X -> Left, Y -> Top" convention.
# outlet is a smaller 200x150 rectangle offset in the inlet's local frame.

def _rect_ports(dx, dy, w_out=200.0, h_out=150.0):
    inlet = _port(
        "Rectangular", w=400.0, h=300.0, position=(0.0, 0.0, 0.0),
        direction=(0.0, 0.0, 1.0), profile_x_axis=(1.0, 0.0, 0.0), flow_role="inlet",
    )
    outlet = _port(
        "Rectangular", w=w_out, h=h_out, position=(dx, dy, 0.0),
        direction=(0.0, 0.0, -1.0), flow_role="outlet",
    )
    return inlet, outlet


def test_alignment_concentric():
    parser = _parser()
    inlet, outlet = _rect_ports(0.0, 0.0)
    _, qualifiers, _ = parser._classify_through_flow(inlet, outlet)
    assert qualifiers["alignment"] == "concentric"


def test_alignment_eccentric_top():
    parser = _parser()
    # half_dh = (150-300)/2 = -75; top alignment needs delta_y = -half_dh = 75.
    inlet, outlet = _rect_ports(0.0, 75.0)
    _, qualifiers, _ = parser._classify_through_flow(inlet, outlet)
    assert qualifiers["alignment"] == "eccentric"
    assert qualifiers["aligned_side"] == "top"


def test_alignment_eccentric_right():
    parser = _parser()
    # half_dw = (200-400)/2 = -100; right alignment needs delta_x = half_dw = -100.
    inlet, outlet = _rect_ports(-100.0, 0.0)
    _, qualifiers, _ = parser._classify_through_flow(inlet, outlet)
    assert qualifiers["alignment"] == "eccentric"
    assert qualifiers["aligned_side"] == "right"


def test_alignment_double_eccentric_top_left():
    parser = _parser()
    inlet, outlet = _rect_ports(100.0, 75.0)
    _, qualifiers, _ = parser._classify_through_flow(inlet, outlet)
    assert qualifiers["alignment"] == "double_eccentric"
    assert qualifiers["aligned_corner"] == "top_left"


def test_alignment_offset_when_no_edges_line_up():
    parser = _parser()
    inlet, outlet = _rect_ports(50.0, 20.0)
    _, qualifiers, _ = parser._classify_through_flow(inlet, outlet)
    assert qualifiers["alignment"] == "offset"
    assert "aligned_side" not in qualifiers
    assert "aligned_corner" not in qualifiers


def test_transition_form_single_plane_vs_pyramidal():
    parser = _parser()
    single_plane_in, single_plane_out = _rect_ports(0.0, 0.0, w_out=200.0, h_out=300.0)
    _, q1, _ = parser._classify_through_flow(single_plane_in, single_plane_out)
    assert q1["transition_form"] == "single_plane"

    pyramidal_in, pyramidal_out = _rect_ports(0.0, 0.0, w_out=200.0, h_out=150.0)
    _, q2, _ = parser._classify_through_flow(pyramidal_in, pyramidal_out)
    assert q2["transition_form"] == "pyramidal"


# ----------------------------------------------------------------------
# classify_flow (branch, degree 3): diverging/converging + common_leg
# ----------------------------------------------------------------------

def test_branch_ordinary_dividing_tee_common_leg_is_run():
    parser = _parser()
    ports = [
        _port("Circular", d=300.0, flow_role="inlet", edge_key="run_a"),
        _port("Circular", d=300.0, flow_role="outlet", edge_key="run_b"),
        _port("Circular", d=200.0, flow_role="outlet", edge_key="branch"),
    ]
    analysis = _NodeAnalysis(ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=0.0)])
    flow_class, qualifiers, derived = parser._classify_branch_flow(ports, analysis)
    assert flow_class == "diverging"
    assert qualifiers["common_leg"] == "run"
    assert derived["area_ratio"] == pytest.approx((200.0 ** 2) / (300.0 ** 2))


def test_branch_bullhead_dividing_tee_common_leg_is_branch():
    parser = _parser()
    ports = [
        _port("Circular", d=300.0, flow_role="outlet", edge_key="run_a"),
        _port("Circular", d=300.0, flow_role="outlet", edge_key="run_b"),
        _port("Circular", d=200.0, flow_role="inlet", edge_key="branch"),
    ]
    analysis = _NodeAnalysis(ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=0.0)])
    flow_class, qualifiers, derived = parser._classify_branch_flow(ports, analysis)
    assert flow_class == "diverging"
    assert qualifiers["common_leg"] == "branch"


def test_branch_converging_tee_common_leg_is_run():
    parser = _parser()
    ports = [
        _port("Circular", d=300.0, flow_role="inlet", edge_key="run_a"),
        _port("Circular", d=300.0, flow_role="outlet", edge_key="run_b"),
        _port("Circular", d=200.0, flow_role="inlet", edge_key="branch"),
    ]
    analysis = _NodeAnalysis(ports, collinear_pairs=[EdgePair(a=0, b=1, angle=180.0, eccentricity=0.0)])
    flow_class, qualifiers, _ = parser._classify_branch_flow(ports, analysis)
    assert flow_class == "converging"
    assert qualifiers["common_leg"] == "run"


def test_branch_wye_has_no_common_leg():
    # No collinear pair at all -- a symmetric wye has no geometrically
    # identified run, so common_leg is never set.
    parser = _parser()
    ports = [
        _port("Circular", d=200.0, flow_role="inlet"),
        _port("Circular", d=200.0, flow_role="outlet"),
        _port("Circular", d=200.0, flow_role="outlet"),
    ]
    analysis = _NodeAnalysis(ports, collinear_pairs=[])
    flow_class, qualifiers, derived = parser._classify_branch_flow(ports, analysis)
    assert flow_class == "diverging"
    assert "common_leg" not in qualifiers
    assert "area_ratio" not in derived
