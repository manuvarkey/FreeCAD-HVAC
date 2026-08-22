import math

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs (incl. FakeVector)

from freecad.HVAC.core import airflow
from freecad.HVAC.library import smacna_loss
from freecad.HVAC.library.library_api import HVACLibraryAPI as api


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


# ----------------------------------------------------------------------------
# port_area
# ----------------------------------------------------------------------------

def test_port_area_circular():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    expected = airflow.circular_area(airflow.mm_to_m(200.0))
    assert api.port_area(port) == pytest.approx(expected)


def test_port_area_rectangular():
    port = _port("A", (1, 0, 0), False, profile="Rectangular", width=400.0, height=200.0)
    expected = airflow.rectangular_area(airflow.mm_to_m(400.0), airflow.mm_to_m(200.0))
    assert api.port_area(port) == pytest.approx(expected)


def test_port_area_missing_dimensions_is_zero():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=0.0)
    assert api.port_area(port) == 0.0


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
    assert result == pytest.approx({"OUT": smacna_loss.elbow_zeta_round(1.0)})


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
    assert result == pytest.approx({"OUT": expected})


def test_elbow_loss_missing_radius_returns_none():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=200.0)
    outlet = _port("OUT", (0, 1, 0), False, profile="Circular", diameter=200.0)
    context = {"connected_ports": [inlet, outlet], "properties": {}}
    assert api.elbow_loss(context) is None


def test_elbow_loss_wrong_port_count_returns_none():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    context = {"connected_ports": [port], "properties": {"CenterlineRadius": 100.0}}
    assert api.elbow_loss(context) is None


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

    assert result == pytest.approx({"OUT": expected})


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

    assert result == pytest.approx({"OUT": expected})


def test_transition_loss_same_size_is_negligible():
    inlet = _port("IN", (-1, 0, 0), True, profile="Circular", diameter=300.0)
    outlet = _port("OUT", (1, 0, 0), False, profile="Circular", diameter=300.0)
    context = {"connected_ports": [inlet, outlet], "properties": {"TransitionLength": 300.0}}
    assert api.transition_loss(context) == {"OUT": 0.0}


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

    assert result == pytest.approx({"BRANCH": zeta_branch, "STRAIGHT": zeta_straight})


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

    assert result == pytest.approx({"BRANCH": zeta_branch, "STRAIGHT": zeta_straight})


def test_branch_loss_zero_common_flow_returns_zero_not_none():
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=0.0)
    straight = _port("STRAIGHT", (-1, 0, 0), True, diameter=300.0, velocity_ms=0.0)
    branch = _port("BRANCH", (0, 1, 0), True, diameter=150.0, velocity_ms=0.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}
    assert api.branch_loss(context) == {"BRANCH": 0.0, "STRAIGHT": 0.0}


def test_branch_loss_wrong_port_count_returns_none():
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [primary], "properties": {}}
    assert api.branch_loss(context) is None


def test_branch_loss_ambiguous_flow_pattern_returns_none():
    # 3 inlets, 0 outlets -- not a valid tee/wye flow pattern.
    p1 = _port("A", (1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    p2 = _port("B", (0, 1, 0), True, diameter=300.0, velocity_ms=5.0)
    p3 = _port("C", (0, 0, 1), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2, p3], "properties": {}}
    assert api.branch_loss(context) is None


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

    assert api.manifold_loss(context) == pytest.approx(api.branch_loss(context))


def test_manifold_loss_converging_matches_branch_loss_at_two_secondaries():
    primary = _port("OUT", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    straight = _port("STRAIGHT", (-1, 0, 0), True, diameter=300.0, velocity_ms=4.0)
    branch = _port("BRANCH", (0, 1, 0), True, diameter=150.0, velocity_ms=3.0)
    context = {"connected_ports": [primary, straight, branch], "properties": {}}

    assert api.manifold_loss(context) == pytest.approx(api.branch_loss(context))


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

    assert result is not None
    assert set(result.keys()) == {"STRAIGHT", "BRANCH_A", "BRANCH_B"}
    assert all(isinstance(v, float) for v in result.values())


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

    assert result is not None
    assert set(result.keys()) == {"STRAIGHT", "BRANCH_A", "BRANCH_B"}
    assert all(isinstance(v, float) for v in result.values())


def test_manifold_loss_mixed_flow_pattern_returns_none():
    # 2 inlets, 2 outlets -- a true cross with no single trunk to decompose.
    p1 = _port("IN1", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    p2 = _port("IN2", (0, -1, 0), True, diameter=300.0, velocity_ms=5.0)
    p3 = _port("OUT1", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    p4 = _port("OUT2", (0, 1, 0), False, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2, p3, p4], "properties": {}}
    assert api.manifold_loss(context) is None


def test_manifold_loss_wrong_port_count_returns_none():
    p1 = _port("A", (1, 0, 0), False, diameter=300.0, velocity_ms=5.0)
    p2 = _port("B", (-1, 0, 0), True, diameter=300.0, velocity_ms=5.0)
    context = {"connected_ports": [p1, p2], "properties": {}}
    assert api.manifold_loss(context) is None


def test_manifold_loss_zero_primary_flow_returns_zero_for_all_secondaries():
    primary = _port("IN", (-1, 0, 0), True, diameter=300.0, velocity_ms=0.0, flow_rate_lps=0.0)
    straight = _port("STRAIGHT", (1, 0, 0), False, diameter=300.0, velocity_ms=0.0)
    branch_a = _port("BRANCH_A", (0, 1, 0), False, diameter=150.0, velocity_ms=0.0)
    branch_b = _port("BRANCH_B", (0, 0, 1), False, diameter=150.0, velocity_ms=0.0)
    context = {"connected_ports": [primary, straight, branch_a, branch_b], "properties": {}}
    assert api.manifold_loss(context) == {"STRAIGHT": 0.0, "BRANCH_A": 0.0, "BRANCH_B": 0.0}


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
    assert result == pytest.approx({"A": 1.5})


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
    assert result == pytest.approx({"A": expected_k})
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
    assert result == pytest.approx({"A": expected_k})
    assert expected_k < 1.5


def test_terminal_component_loss_missing_neck_size_returns_none():
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    context = {"connected_ports": [port], "properties": {"LossCoefficient": 1.5}}
    assert api.terminal_component_loss(context) is None


def test_terminal_component_loss_missing_loss_coefficient_returns_none():
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    context = {"connected_ports": [port], "properties": {"NeckSize": 200.0}}
    assert api.terminal_component_loss(context) is None


def test_terminal_component_loss_wrong_port_count_returns_none():
    p1 = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=5.0)
    p2 = _port("B", (-1, 0, 0), True, diameter=200.0, velocity_ms=5.0)
    context = {
        "connected_ports": [p1, p2],
        "properties": {"NeckSize": 200.0, "LossCoefficient": 1.5},
    }
    assert api.terminal_component_loss(context) is None


def test_terminal_component_loss_zero_flow_returns_zero_not_none():
    port = _port("A", (1, 0, 0), False, diameter=200.0, velocity_ms=0.0, flow_rate_lps=0.0)
    context = {
        "connected_ports": [port],
        "properties": {"NeckSize": 200.0, "LossCoefficient": 1.5},
    }
    assert api.terminal_component_loss(context) == {"A": 0.0}


# ----------------------------------------------------------------------------
# inline_device_loss
# ----------------------------------------------------------------------------

def test_inline_device_loss_returns_raw_coefficient():
    # No neck-size conversion -- the coefficient is returned as-is, to be
    # applied by the solver against the connecting duct's own velocity.
    context = {"properties": {"LossCoefficient": 0.35}}
    assert api.inline_device_loss(context) == pytest.approx(0.35)


def test_inline_device_loss_missing_coefficient_returns_none():
    context = {"properties": {}}
    assert api.inline_device_loss(context) is None


def test_inline_device_loss_zero_coefficient_returns_none():
    context = {"properties": {"LossCoefficient": 0.0}}
    assert api.inline_device_loss(context) is None


def test_inline_device_loss_negative_coefficient_returns_none():
    context = {"properties": {"LossCoefficient": -1.0}}
    assert api.inline_device_loss(context) is None


# ----------------------------------------------------------------------------
# grow_port_section
# ----------------------------------------------------------------------------

def test_grow_port_section_circular_grows_diameter_by_twice_delta():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    grown = api.grow_port_section(port, 25.0)
    assert grown["section_params"]["Diameter"] == pytest.approx(250.0)
    # Position/direction/profile/edge_key are otherwise unchanged.
    assert grown["profile"] == "Circular"
    assert grown["edge_key"] == "A"


def test_grow_port_section_rectangular_grows_width_and_height_by_twice_delta():
    port = _port("A", (1, 0, 0), False, profile="Rectangular", width=300.0, height=150.0)
    grown = api.grow_port_section(port, 50.0)
    assert grown["section_params"]["Width"] == pytest.approx(400.0)
    assert grown["section_params"]["Height"] == pytest.approx(250.0)


def test_grow_port_section_oval_grows_width_and_height_by_twice_delta():
    port = _port("A", (1, 0, 0), False, profile="Oval", width=300.0, height=150.0)
    grown = api.grow_port_section(port, 50.0)
    assert grown["section_params"]["Width"] == pytest.approx(400.0)
    assert grown["section_params"]["Height"] == pytest.approx(250.0)


def test_grow_port_section_unsupported_profile_raises():
    port = _port("A", (1, 0, 0), False, profile="Weird")
    with pytest.raises(ValueError):
        api.grow_port_section(port, 10.0)


def test_grow_port_section_does_not_mutate_original_port():
    port = _port("A", (1, 0, 0), False, profile="Circular", diameter=200.0)
    api.grow_port_section(port, 25.0)
    assert port["section_params"]["Diameter"] == 200.0
