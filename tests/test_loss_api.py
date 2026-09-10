import math

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs (incl. FakeVector)

from freecad.HVAC.analysis import physics as airflow
from freecad.HVAC.library import smacna_loss
from freecad.HVAC.library.library_api import HVACLibraryAPI as geometry_api
from freecad.HVAC.library.loss_api import HVACLossAPI as api


def _port(edge_key, direction, flow_into_junction, profile="Circular", diameter=None,
          width=None, height=None, velocity_ms=0.0, reynolds=0.0, flow_rate_lps=0.0):
    section_params = {}
    if diameter is not None:
        section_params["Diameter"] = diameter
    if width is not None:
        section_params["Width"] = width
    if height is not None:
        section_params["Height"] = height
    return {
        "edge_key": edge_key,
        "direction": direction,
        "flow_into_junction": flow_into_junction,
        "profile": profile,
        "section_params": section_params,
        "velocity_ms": velocity_ms,
        "reynolds": reynolds,
        "flow_rate_lps": flow_rate_lps,
    }


def _paths_by_edge(evaluation):
    """{reference_edge_key: loss_coefficient} view of a LossEvaluation's own
    paths -- lets most tests keep comparing against the same {edge_key: K}
    shape the old dict/float contract used, on top of also checking status."""
    return {p.reference_edge_key: p.loss_coefficient for p in evaluation.paths}


def _path_for(evaluation, edge_key):
    return next(p for p in evaluation.paths if p.reference_edge_key == edge_key)


# ----------------------------------------------------------------------------
# port_area
# ----------------------------------------------------------------------------

def test_port_area_circular():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    expected = airflow.circular_area(airflow.mm_to_m(200.0))
    assert geometry_api.port_area(port) == pytest.approx(expected)


def test_port_area_rectangular():
    port = _port("A", (1, 0, 0), False, profile="Rectangular", width=400.0, height=200.0)
    expected = airflow.rectangular_area(airflow.mm_to_m(400.0), airflow.mm_to_m(200.0))
    assert geometry_api.port_area(port) == pytest.approx(expected)


def test_port_area_missing_dimensions_is_zero():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=0.0)
    assert geometry_api.port_area(port) == 0.0


# ----------------------------------------------------------------------------
# elbow_loss
# ----------------------------------------------------------------------------

def test_elbow_loss_round():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=200.0)
    outlet = _port("OUT", (0, 1, 0), False, profile="Circular", diameter=200.0)
    context = {
        "connected_ports": [inlet, outlet],
        "properties": {"CenterlineRadius": 200.0},  # r_on_d = 1.0
    }
    result = api.elbow_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"OUT": smacna_loss.elbow_zeta_round(1.0)})
    path = _path_for(result, "OUT")
    assert path.from_edge_key == "IN"
    assert path.to_edge_key == "OUT"


def test_elbow_loss_rectangular():
    inlet = _port("IN", (-1, 0, 0), True, profile="Rectangular", width=200.0, height=200.0)
    outlet = _port("OUT", (0, 1, 0), False, profile="Rectangular", width=200.0, height=200.0,
                    reynolds=1e6)
    context = {
        "connected_ports": [inlet, outlet],
        "properties": {"CenterlineRadius": 200.0},  # r_on_w = h_on_w = 1.0
    }
    result = api.elbow_loss(context)
    expected = smacna_loss.elbow_zeta_rect(h_on_w=1.0, r_on_w=1.0, reynolds=1e6)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"OUT": expected})


def test_elbow_loss_missing_radius_returns_unsupported():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=200.0)
    outlet = _port("OUT", (0, 1, 0), False, profile="Circular", diameter=200.0)
    context = {"connected_ports": [inlet, outlet], "properties": {}}
    result = api.elbow_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_elbow_loss_wrong_port_count_returns_unsupported():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    context = {"connected_ports": [port], "properties": {"CenterlineRadius": 100.0}}
    result = api.elbow_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


# ----------------------------------------------------------------------------
# transition_loss
# ----------------------------------------------------------------------------

def test_transition_loss_expansion_round():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=200.0, reynolds=3e5)
    outlet = _port("OUT", (1, 0, 0), False, profile="Circular", diameter=400.0)
    context = {
        "connected_ports": [inlet, outlet],
        "properties": {"TransitionLength": 1000.0},
    }
    result = api.transition_loss(context)

    area_in = airflow.circular_area(airflow.mm_to_m(200.0))
    area_out = airflow.circular_area(airflow.mm_to_m(400.0))
    area_ratio = area_out / area_in
    d_eq_in = 2.0 * math.sqrt(area_in / math.pi)
    d_eq_out = 2.0 * math.sqrt(area_out / math.pi)
    theta_deg = math.degrees(2.0 * math.atan(abs(d_eq_out - d_eq_in) / (2.0 * 1.0)))
    expected = smacna_loss.expansion_zeta_round(theta_deg, area_ratio, 3e5)

    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"OUT": expected})
    path = _path_for(result, "OUT")
    assert path.from_edge_key == "IN"
    assert path.to_edge_key == "OUT"


def test_transition_loss_contraction_uses_outlet_reference():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=400.0)
    outlet = _port("OUT", (1, 0, 0), False, profile="Circular", diameter=200.0)
    context = {
        "connected_ports": [inlet, outlet],
        "properties": {"TransitionLength": 1000.0},
    }
    result = api.transition_loss(context)

    area_in = airflow.circular_area(airflow.mm_to_m(400.0))
    area_out = airflow.circular_area(airflow.mm_to_m(200.0))
    area_ratio = area_in / area_out
    d_eq_in = 2.0 * math.sqrt(area_in / math.pi)
    d_eq_out = 2.0 * math.sqrt(area_out / math.pi)
    theta_deg = math.degrees(2.0 * math.atan(abs(d_eq_out - d_eq_in) / (2.0 * 1.0)))
    expected = smacna_loss.contraction_zeta(theta_deg, area_ratio)

    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"OUT": expected})


def test_transition_loss_same_size_is_negligible():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=300.0)
    outlet = _port("OUT", (1, 0, 0), False, profile="Circular", diameter=300.0)
    context = {"connected_ports": [inlet, outlet], "properties": {"TransitionLength": 300.0}}
    result = api.transition_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == {"OUT": 0.0}


def test_transition_loss_expansion_rectangular():
    inlet = _port("IN", (-1, 0, 0), True, profile="Rectangular", width=200.0, height=200.0)
    outlet = _port("OUT", (1, 0, 0), False, profile="Rectangular", width=400.0, height=400.0)
    context = {"connected_ports": [inlet, outlet], "properties": {"TransitionLength": 1000.0}}
    result = api.transition_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result)["OUT"] > 0.0


def test_transition_loss_oval_profile_returns_unsupported_not_a_rectangular_guess():
    # Oval/custom profiles: geometry classification stays valid (see
    # NetworkParser), but no validated SMACNA table exists for a
    # straight-axis Oval area change -- must cleanly return UNSUPPORTED
    # rather than silently reusing the rectangular table (expansion_zeta_rect
    # is a literal SMACNA A8B rectangular-only table).
    inlet = _port("IN", (-1, 0, 0), True, profile="Oval", width=200.0, height=100.0)
    outlet = _port("OUT", (1, 0, 0), False, profile="Oval", width=400.0, height=200.0)
    context = {"connected_ports": [inlet, outlet], "properties": {"TransitionLength": 1000.0}}
    result = api.transition_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


# ----------------------------------------------------------------------------
# branch_loss
# ----------------------------------------------------------------------------

def test_branch_loss_diverging_tee90_identifies_branch_and_straight():
    # Inlet (common/primary) points backward (-x); straight outlet continues
    # forward (+x, anti-parallel to primary => "straightest"); branch outlet
    # goes sideways (+y, perpendicular).
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    straight = _port("STRAIGHT", (1, 0, 0), False, diameter=300.0, velocity_ms=4.0)
    branch = _port("BRANCH", (0, 1, 0), False, diameter=150.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}

    result = api.branch_loss(context)

    ab_on_ac = (150.0 / 300.0) ** 2
    vb_on_vc = 3.0 / 5.0
    vs_on_vc = 4.0 / 5.0
    zeta_branch, zeta_straight = smacna_loss.diverging_branch_zetas(90.0, ab_on_ac, vb_on_vc, vs_on_vc)

    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"BRANCH": zeta_branch, "STRAIGHT": zeta_straight})
    # A diverging tee's loss belongs to its outlet legs -- from the single
    # common inlet (IN) to each outlet.
    assert _path_for(result, "BRANCH").from_edge_key == "IN"
    assert _path_for(result, "BRANCH").to_edge_key == "BRANCH"
    assert _path_for(result, "STRAIGHT").from_edge_key == "IN"
    assert _path_for(result, "STRAIGHT").to_edge_key == "STRAIGHT"


def test_branch_loss_converging_wye45():
    # Two inlets merging into one outlet (primary). Branch enters at a
    # shallow angle (dot with straight's direction close to -1 for straight,
    # less negative for the 45 deg branch).
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    straight = _port("STRAIGHT", (-1, 0, 0), True, diameter=300.0, velocity_ms=4.0)
    cos45 = math.cos(math.radians(45.0))
    sin45 = math.sin(math.radians(45.0))
    branch = _port("BRANCH", (-cos45, sin45, 0.0), True, diameter=150.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}

    result = api.branch_loss(context)

    ab_on_ac = (150.0 / 300.0) ** 2
    vb_on_vc = 3.0 / 5.0
    vs_on_vc = 4.0 / 5.0
    # angle between branch and straight directions: dot = (-cos45)(-1) = cos45 -> 45 deg apart
    # -> theta = 180 - 45 = 135... but branch should resolve near 45 deg entry.
    dot = (-cos45) * (-1.0) + sin45 * 0.0
    angle_deg = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    zeta_branch, zeta_straight = smacna_loss.converging_branch_zetas(angle_deg, ab_on_ac, vb_on_vc, vs_on_vc)

    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"BRANCH": zeta_branch, "STRAIGHT": zeta_straight})
    # A converging tee's loss belongs to its INLET legs -- this is exactly
    # the case the old outlet-only convention couldn't represent: each path
    # goes from its own inlet leg to the single common outlet (OUT).
    assert _path_for(result, "BRANCH").from_edge_key == "BRANCH"
    assert _path_for(result, "BRANCH").to_edge_key == "OUT"
    assert _path_for(result, "STRAIGHT").from_edge_key == "STRAIGHT"
    assert _path_for(result, "STRAIGHT").to_edge_key == "OUT"


def test_branch_loss_zero_common_flow_returns_zero_not_unsupported():
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=0.0)
    straight = _port("STRAIGHT", (-1, 0, 0), True, diameter=300.0, velocity_ms=0.0)
    branch = _port("BRANCH", (0, 1, 0), True, diameter=150.0, velocity_ms=0.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}
    result = api.branch_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == {"BRANCH": 0.0, "STRAIGHT": 0.0}


def test_branch_loss_wrong_port_count_returns_unsupported():
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [primary], "properties": {}}
    result = api.branch_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_branch_loss_ambiguous_flow_pattern_returns_unsupported():
    # 3 inlets, 0 outlets -- not a valid tee/wye flow pattern.
    p1 = _port("A", (1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    p2 = _port("B", (0, 1, 0), True, diameter=300.0, velocity_ms=5.0)
    p3 = _port("C", (0, 0, 1), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2, p3], "properties": {}}
    result = api.branch_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


# ----------------------------------------------------------------------------
# branch_loss_bullhead (qualifiers["common_leg"] == "branch" -- see
# NetworkParser.classify_flow / TOPOLOGY_CLASSIFICATION.md)
# ----------------------------------------------------------------------------

def test_branch_loss_bullhead_diverging_covers_both_run_legs():
    # Single inlet on the geometric branch leg (-x), splitting into two
    # collinear run legs (+y/-y) -- a bullhead dividing tee.
    common = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    run_a = _port("RUN_A", (0, 1, 0), False, diameter=200.0, velocity_ms=3.0)
    run_b = _port("RUN_B", (0, -1, 0), False, diameter=250.0, velocity_ms=2.0)
    context = {"connected_ports": [common, run_a, run_b], "properties": {}}

    result = api.branch_loss_bullhead(context)

    def _expected(velocity_ms, diameter):
        a_on_ac = (diameter / 300.0) ** 2
        v_on_vc = velocity_ms / 5.0
        # common points -x, leg points +-y -> perpendicular either way.
        zeta, _ = smacna_loss.diverging_branch_zetas(90.0, a_on_ac, v_on_vc, v_on_vc)
        return zeta

    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == pytest.approx({
        "RUN_A": _expected(3.0, 200.0),
        "RUN_B": _expected(2.0, 250.0),
    })
    assert _path_for(result, "RUN_A").from_edge_key == "IN"
    assert _path_for(result, "RUN_A").to_edge_key == "RUN_A"


def test_branch_loss_bullhead_converging_covers_both_run_legs():
    # Two collinear run legs (+y/-y) merging into a single outlet on the
    # geometric branch leg (+x) -- a bullhead combining tee.
    common = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    run_a = _port("RUN_A", (0, 1, 0), True, diameter=200.0, velocity_ms=3.0)
    run_b = _port("RUN_B", (0, -1, 0), True, diameter=250.0, velocity_ms=2.0)
    context = {"connected_ports": [common, run_a, run_b], "properties": {}}

    result = api.branch_loss_bullhead(context)

    def _expected(velocity_ms, diameter):
        a_on_ac = (diameter / 300.0) ** 2
        v_on_vc = velocity_ms / 5.0
        zeta, _ = smacna_loss.converging_branch_zetas(90.0, a_on_ac, v_on_vc, v_on_vc)
        return zeta

    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == pytest.approx({
        "RUN_A": _expected(3.0, 200.0),
        "RUN_B": _expected(2.0, 250.0),
    })
    assert _path_for(result, "RUN_A").from_edge_key == "RUN_A"
    assert _path_for(result, "RUN_A").to_edge_key == "OUT"


def test_branch_loss_bullhead_zero_common_flow_returns_zero_not_unsupported():
    common = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=0.0)
    run_a = _port("RUN_A", (0, 1, 0), False, diameter=200.0, velocity_ms=0.0)
    run_b = _port("RUN_B", (0, -1, 0), False, diameter=200.0, velocity_ms=0.0)
    context = {"connected_ports": [common, run_a, run_b], "properties": {}}
    result = api.branch_loss_bullhead(context)
    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == {"RUN_A": 0.0, "RUN_B": 0.0}


def test_branch_loss_bullhead_wrong_port_count_returns_unsupported():
    common = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [common], "properties": {}}
    result = api.branch_loss_bullhead(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_branch_loss_bullhead_ambiguous_flow_pattern_returns_unsupported():
    p1 = _port("A", (1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    p2 = _port("B", (0, 1, 0), True, diameter=300.0, velocity_ms=5.0)
    p3 = _port("C", (0, 0, 1), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2, p3], "properties": {}}
    result = api.branch_loss_bullhead(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


# ----------------------------------------------------------------------------
# wye_loss -- a true Wye has no Tee-style common/straight leg (see
# NetworkParser's qualifiers["common_leg"], never assigned to a wye), so
# this is a distinct API entry point from branch_loss()/branch_loss_bullhead
# rather than routing a Wye through either Tee-oriented method. Internally
# it shares branch_loss_bullhead's "no independent straight leg" math (both
# fittings have that same shape), but the two are architecturally separate
# entry points for two different physical fittings.
# ----------------------------------------------------------------------------

def test_wye_loss_diverging_treats_symmetric_legs_symmetrically():
    # A genuine Wye: two outlet legs at the same angle off the common inlet,
    # neither one a "straight-through continuation" of the other. Unlike
    # branch_loss() (which picks one secondary as "straight" via a
    # dot-product comparison and would tie-break this exact symmetric case
    # arbitrarily, giving the two identical legs different K values),
    # wye_loss() must return equal K for both.
    cos30 = math.cos(math.radians(30.0))
    sin30 = math.sin(math.radians(30.0))
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    leg_a = _port("LEG_A", (cos30, sin30, 0.0), False, diameter=200.0, velocity_ms=3.0)
    leg_b = _port("LEG_B", (cos30, -sin30, 0.0), False, diameter=200.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, leg_a, leg_b], "properties": {}}

    result = api.wye_loss(context)

    a_on_ac = (200.0 / 300.0) ** 2
    v_on_vc = 3.0 / 5.0
    dot = (-1.0) * cos30
    angle_deg = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    zeta_leg, _ = smacna_loss.diverging_branch_zetas(angle_deg, a_on_ac, v_on_vc, v_on_vc)

    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == pytest.approx({"LEG_A": zeta_leg, "LEG_B": zeta_leg})


def test_wye_loss_converging_treats_symmetric_legs_symmetrically():
    cos30 = math.cos(math.radians(30.0))
    sin30 = math.sin(math.radians(30.0))
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    leg_a = _port("LEG_A", (-cos30, sin30, 0.0), True, diameter=200.0, velocity_ms=3.0)
    leg_b = _port("LEG_B", (-cos30, -sin30, 0.0), True, diameter=200.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, leg_a, leg_b], "properties": {}}

    result = api.wye_loss(context)

    a_on_ac = (200.0 / 300.0) ** 2
    v_on_vc = 3.0 / 5.0
    dot = (1.0) * (-cos30)
    angle_deg = 180.0 - math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    zeta_leg, _ = smacna_loss.converging_branch_zetas(angle_deg, a_on_ac, v_on_vc, v_on_vc)

    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == pytest.approx({"LEG_A": zeta_leg, "LEG_B": zeta_leg})
    assert _path_for(result, "LEG_A").from_edge_key == "LEG_A"
    assert _path_for(result, "LEG_A").to_edge_key == "OUT"


def test_wye_loss_zero_common_flow_returns_zero_not_unsupported():
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=0.0)
    leg_a = _port("LEG_A", (1, 1, 0), False, diameter=200.0, velocity_ms=0.0)
    leg_b = _port("LEG_B", (1, -1, 0), False, diameter=200.0, velocity_ms=0.0)
    context = {"connected_ports": [primary, leg_a, leg_b], "properties": {}}
    result = api.wye_loss(context)
    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == {"LEG_A": 0.0, "LEG_B": 0.0}


def test_wye_loss_wrong_port_count_returns_unsupported():
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [primary], "properties": {}}
    result = api.wye_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_wye_loss_ambiguous_flow_pattern_returns_unsupported():
    p1 = _port("A", (1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    p2 = _port("B", (0, 1, 0), True, diameter=300.0, velocity_ms=5.0)
    p3 = _port("C", (0, 0, 1), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2, p3], "properties": {}}
    result = api.wye_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


# ----------------------------------------------------------------------------
# manifold_loss
# ----------------------------------------------------------------------------

def _flow_lps(velocity_ms, diameter_mm):
    area = airflow.circular_area(airflow.mm_to_m(diameter_mm))
    return airflow.m3s_to_lps(velocity_ms * area)


def test_manifold_loss_diverging_matches_branch_loss_at_two_secondaries():
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0,
                     flow_rate_lps=_flow_lps(5.0, 300.0))
    straight = _port("STRAIGHT", (1, 0, 0), False, diameter=300.0, velocity_ms=4.0)
    branch = _port("BRANCH", (0, 1, 0), False, diameter=150.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}

    manifold_result = api.manifold_loss(context)
    branch_result = api.branch_loss(context)
    assert manifold_result.status == api.APPROXIMATION
    assert _paths_by_edge(manifold_result) == pytest.approx(_paths_by_edge(branch_result))


def test_manifold_loss_converging_matches_branch_loss_at_two_secondaries():
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    straight = _port("STRAIGHT", (-1, 0, 0), True, diameter=300.0, velocity_ms=4.0)
    branch = _port("BRANCH", (0, 1, 0), True, diameter=150.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}

    manifold_result = api.manifold_loss(context)
    branch_result = api.branch_loss(context)
    assert manifold_result.status == api.APPROXIMATION
    assert _paths_by_edge(manifold_result) == pytest.approx(_paths_by_edge(branch_result))


def test_manifold_loss_diverging_cross_covers_all_secondaries():
    # 1 inlet -> 3 outlets (a header/cross with one straightest continuation
    # and two side takeoffs), flows conserved: 200 = 80 + 70 + 50.
    q_in = 200.0
    primary = _port("IN", (-1, 0, 0), True, diameter=400.0,
                     velocity_ms=airflow.velocity_from_flow(airflow.lps_to_m3s(q_in), airflow.circular_area(0.4)),
                     flow_rate_lps=q_in)

    def outlet(edge_key, direction, diameter, q):
        v = airflow.velocity_from_flow(airflow.lps_to_m3s(q), airflow.circular_area(airflow.mm_to_m(diameter)))
        return _port(edge_key, direction, False, diameter=diameter, velocity_ms=v, flow_rate_lps=q)

    straight = outlet("STRAIGHT", (1, 0, 0), 300.0, 80.0)
    branch_a = outlet("BRANCH_A", (0, 1, 0), 200.0, 70.0)
    branch_b = outlet("BRANCH_B", (0, 0, 1), 200.0, 50.0)

    context = {"connected_ports": [primary, straight, branch_a, branch_b], "properties": {}}
    result = api.manifold_loss(context)

    assert result.status == api.APPROXIMATION
    paths = _paths_by_edge(result)
    assert set(paths.keys()) == {"STRAIGHT", "BRANCH_A", "BRANCH_B"}
    assert all(isinstance(v, float) for v in paths.values())
    # A diverging cross's loss belongs to its outlet legs, from the single
    # common inlet.
    for edge_key in paths:
        assert _path_for(result, edge_key).from_edge_key == "IN"
        assert _path_for(result, edge_key).to_edge_key == edge_key


def test_manifold_loss_converging_cross_covers_all_secondaries():
    # 3 inlets merging -> 1 outlet, flows conserved: 80 + 70 + 50 = 200.
    q_out = 200.0
    primary = _port("OUT", (1, 0, 0), False, diameter=400.0,
                     velocity_ms=airflow.velocity_from_flow(airflow.lps_to_m3s(q_out), airflow.circular_area(0.4)),
                     flow_rate_lps=q_out)

    def inlet(edge_key, direction, diameter, q):
        v = airflow.velocity_from_flow(airflow.lps_to_m3s(q), airflow.circular_area(airflow.mm_to_m(diameter)))
        return _port(edge_key, direction, True, diameter=diameter, velocity_ms=v, flow_rate_lps=q)

    straight = inlet("STRAIGHT", (-1, 0, 0), 300.0, 80.0)
    branch_a = inlet("BRANCH_A", (0, 1, 0), 200.0, 70.0)
    branch_b = inlet("BRANCH_B", (0, 0, 1), 200.0, 50.0)

    context = {"connected_ports": [primary, straight, branch_a, branch_b], "properties": {}}
    result = api.manifold_loss(context)

    assert result.status == api.APPROXIMATION
    paths = _paths_by_edge(result)
    assert set(paths.keys()) == {"STRAIGHT", "BRANCH_A", "BRANCH_B"}
    assert all(isinstance(v, float) for v in paths.values())
    # A converging cross's loss belongs to its INLET legs, into the single
    # common outlet.
    for edge_key in paths:
        assert _path_for(result, edge_key).from_edge_key == edge_key
        assert _path_for(result, edge_key).to_edge_key == "OUT"


def test_manifold_loss_mixed_flow_pattern_returns_unsupported():
    # 2 inlets, 2 outlets -- a true cross with no single trunk to decompose.
    p1 = _port("IN1", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    p2 = _port("IN2", (0, -1, 0), True, diameter=300.0, velocity_ms=5.0)
    p3 = _port("OUT1", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    p4 = _port("OUT2", (0, 1, 0), False, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2, p3, p4], "properties": {}}
    result = api.manifold_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_manifold_loss_wrong_port_count_returns_unsupported():
    p1 = _port("A", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    p2 = _port("B", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2], "properties": {}}
    result = api.manifold_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_manifold_loss_zero_primary_flow_returns_zero_for_all_secondaries():
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=0.0, flow_rate_lps=0.0)
    straight = _port("STRAIGHT", (1, 0, 0), False, diameter=300.0, velocity_ms=0.0)
    branch_a = _port("BRANCH_A", (0, 1, 0), False, diameter=150.0, velocity_ms=0.0)
    branch_b = _port("BRANCH_B", (0, 0, 1), False, diameter=150.0, velocity_ms=0.0)
    context = {"connected_ports": [primary, straight, branch_a, branch_b], "properties": {}}
    result = api.manifold_loss(context)
    assert result.status == api.APPROXIMATION
    assert _paths_by_edge(result) == {"STRAIGHT": 0.0, "BRANCH_A": 0.0, "BRANCH_B": 0.0}


# ----------------------------------------------------------------------------
# terminal_component_loss
# ----------------------------------------------------------------------------

def test_terminal_component_loss_neck_matches_duct_size_gives_k_unchanged():
    # Neck same size as the connecting duct -> neck velocity == duct velocity
    # -> K_effective == K exactly (no conversion needed).
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0, flow_rate_lps=_flow_lps(5.0, 200.0))
    context = {
        "connected_ports": [port],
        "properties": {"NeckSize": 200.0, "LossCoefficient": 1.5},
    }
    result = api.terminal_component_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"A": 1.5})
    path = _path_for(result, "A")
    assert path.from_edge_key is None
    assert path.to_edge_key == "A"


def test_terminal_component_loss_smaller_neck_increases_effective_k():
    # A neck narrower than the duct means higher velocity at the neck, so
    # more of the *duct's own* velocity pressure the coefficient is scaled
    # against -- K_effective must come out larger than the raw K.
    duct_flow_lps = _flow_lps(5.0, 200.0)
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0, flow_rate_lps=duct_flow_lps)
    context = {
        "connected_ports": [port],
        "properties": {"NeckSize": 100.0, "LossCoefficient": 1.5},
    }
    result = api.terminal_component_loss(context)

    neck_v = airflow.velocity_from_flow(airflow.lps_to_m3s(duct_flow_lps), airflow.circular_area(0.1))
    expected_k = 1.5 * (neck_v / 5.0) ** 2
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"A": expected_k})
    assert expected_k > 1.5


def test_terminal_component_loss_larger_neck_decreases_effective_k():
    duct_flow_lps = _flow_lps(5.0, 200.0)
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0, flow_rate_lps=duct_flow_lps)
    context = {
        "connected_ports": [port],
        "properties": {"NeckSize": 400.0, "LossCoefficient": 1.5},
    }
    result = api.terminal_component_loss(context)

    neck_v = airflow.velocity_from_flow(airflow.lps_to_m3s(duct_flow_lps), airflow.circular_area(0.4))
    expected_k = 1.5 * (neck_v / 5.0) ** 2
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"A": expected_k})
    assert expected_k < 1.5


def test_terminal_component_loss_missing_neck_size_returns_unsupported():
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    context = {"connected_ports": [port], "properties": {"LossCoefficient": 1.5}}
    result = api.terminal_component_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_terminal_component_loss_missing_loss_coefficient_returns_unsupported():
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    context = {"connected_ports": [port], "properties": {"NeckSize": 200.0}}
    result = api.terminal_component_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_terminal_component_loss_wrong_port_count_returns_unsupported():
    p1 = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    p2 = _port("B", (-1, 0, 0), True, diameter=200.0, velocity_ms=5.0)
    context = {
        "connected_ports": [p1, p2],
        "properties": {"NeckSize": 200.0, "LossCoefficient": 1.5},
    }
    result = api.terminal_component_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_terminal_component_loss_zero_flow_returns_zero_not_unsupported():
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=0.0, flow_rate_lps=0.0)
    context = {
        "connected_ports": [port],
        "properties": {"NeckSize": 200.0, "LossCoefficient": 1.5},
    }
    result = api.terminal_component_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == {"A": 0.0}


# ----------------------------------------------------------------------------
# inline_device_loss -- resolves its 2 connected ports (inlet/outlet) itself
# now, unlike the old bare-float contract which ignored ports entirely.
# ----------------------------------------------------------------------------

def _inline_ports():
    inlet = _port("IN", (-1, 0, 0), True, diameter=200.0, velocity_ms=5.0)
    outlet = _port("OUT", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    return [inlet, outlet]


def test_inline_device_loss_returns_one_exact_path_referenced_to_outlet():
    # No neck-size conversion -- the coefficient is applied as-is against
    # the outlet's own velocity (matches the inline-chain convention
    # analysis/pressure.py already uses).
    context = {"connected_ports": _inline_ports(), "properties": {"LossCoefficient": 0.35}}
    result = api.inline_device_loss(context)
    assert result.status == api.EXACT
    assert _paths_by_edge(result) == pytest.approx({"OUT": 0.35})
    path = _path_for(result, "OUT")
    assert path.from_edge_key == "IN"
    assert path.to_edge_key == "OUT"


def test_inline_device_loss_missing_coefficient_returns_unsupported():
    context = {"connected_ports": _inline_ports(), "properties": {}}
    result = api.inline_device_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_inline_device_loss_zero_coefficient_returns_unsupported():
    context = {"connected_ports": _inline_ports(), "properties": {"LossCoefficient": 0.0}}
    result = api.inline_device_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_inline_device_loss_negative_coefficient_returns_unsupported():
    context = {"connected_ports": _inline_ports(), "properties": {"LossCoefficient": -1.0}}
    result = api.inline_device_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


def test_inline_device_loss_wrong_port_count_returns_unsupported():
    context = {"connected_ports": _inline_ports()[:1], "properties": {"LossCoefficient": 0.35}}
    result = api.inline_device_loss(context)
    assert result.status == api.UNSUPPORTED
    assert result.paths == []


# ----------------------------------------------------------------------------
# uniform_fallback_loss
# ----------------------------------------------------------------------------

def test_uniform_fallback_loss_applies_k_to_every_outlet_port():
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    straight = _port("STRAIGHT", (1, 0, 0), False, diameter=300.0, velocity_ms=4.0)
    branch = _port("BRANCH", (0, 1, 0), False, diameter=150.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}

    result = api.uniform_fallback_loss(context, 0.75)

    assert result.status == api.FALLBACK
    assert _paths_by_edge(result) == pytest.approx({"STRAIGHT": 0.75, "BRANCH": 0.75})
    for edge_key in ("STRAIGHT", "BRANCH"):
        path = _path_for(result, edge_key)
        # uniform_fallback_loss has no single common "other side" to
        # reference (it applies uniformly across however many outlets a
        # fitting has), so from_edge_key is always None -- see its own
        # docstring in library/loss_api.py.
        assert path.from_edge_key is None
        assert path.to_edge_key == edge_key


def test_uniform_fallback_loss_carries_through_optional_warning():
    context = {"connected_ports": [], "properties": {}}
    result = api.uniform_fallback_loss(context, 1.0, warning="mixed cross")
    assert result.status == api.FALLBACK
    assert result.warning == "mixed cross"
    assert result.paths == []


# ----------------------------------------------------------------------------
# grow_port_section
# ----------------------------------------------------------------------------

def test_grow_port_section_circular_grows_diameter_by_twice_delta():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    grown = geometry_api.grow_port_section(port, 25.0)
    assert grown["section_params"]["Diameter"] == pytest.approx(250.0)
    # Position/direction/profile/edge_key are otherwise unchanged.
    assert grown["profile"] == "Circular"
    assert grown["edge_key"] == "A"


def test_grow_port_section_rectangular_grows_width_and_height_by_twice_delta():
    port = _port("A", (1, 0, 0), False, profile="Rectangular", width=300.0, height=150.0)
    grown = geometry_api.grow_port_section(port, 50.0)
    assert grown["section_params"]["Width"] == pytest.approx(400.0)
    assert grown["section_params"]["Height"] == pytest.approx(250.0)


def test_grow_port_section_oval_grows_width_and_height_by_twice_delta():
    port = _port("A", (1, 0, 0), False, profile="Oval", width=300.0, height=150.0)
    grown = geometry_api.grow_port_section(port, 50.0)
    assert grown["section_params"]["Width"] == pytest.approx(400.0)
    assert grown["section_params"]["Height"] == pytest.approx(250.0)


def test_grow_port_section_unsupported_profile_raises():
    port = _port("A", (1, 0, 0), False, profile="Weird")
    with pytest.raises(ValueError):
        geometry_api.grow_port_section(port, 10.0)


def test_grow_port_section_does_not_mutate_original_port():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    geometry_api.grow_port_section(port, 25.0)
    assert port["section_params"]["Diameter"] == 200.0
