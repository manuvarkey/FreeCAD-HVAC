"""
Pure tests for freecad.HVAC.analysis.pressure -- no FreeCAD/conftest
stubbing needed, since analysis/ has no FreeCAD dependency at all.
"""

from analysis_fixtures import AIR_DENSITY, AIR_VISCOSITY, DEFAULT_ROUGHNESS_MM, base_tree

from freecad.HVAC.analysis import flow, physics
from freecad.HVAC.analysis.pressure import K_DEFAULT, PressureSolver

FITTING_K = 0.5


def _expected_segment(diameter_mm, length_mm, flow_lps):
    d_m = physics.mm_to_m(diameter_mm)
    area = physics.circular_area(d_m)
    dh = physics.hydraulic_diameter_circular(d_m)
    v = physics.velocity_from_flow(physics.lps_to_m3s(flow_lps), area)
    re = physics.reynolds_number(v, dh, AIR_VISCOSITY)
    rel_rough = physics.mm_to_m(DEFAULT_ROUGHNESS_MM) / dh
    f = physics.friction_factor_altshul_tsal(re, rel_rough)
    friction = physics.darcy_weisbach_pressure_loss(f, physics.mm_to_m(length_mm), dh, AIR_DENSITY, v)
    return v, re, friction


def _solve(net):
    components, flow_warnings = flow.solve_flow_components(net)
    assert flow_warnings == []
    trees, warnings = PressureSolver().solve(net, components)
    return trees[0], warnings


def test_flow_velocity_friction_match_independent_oracle():
    net = base_tree(loss_evaluator=lambda pv: FITTING_K)
    tree, warnings = _solve(net)
    assert warnings == []

    segA, segB, segC = tree.segments["A"], tree.segments["B"], tree.segments["C"]
    assert segA.flow_lps == 80.0 and segB.flow_lps == 50.0 and segC.flow_lps == 30.0

    vA, reA, frA = _expected_segment(200.0, 5000.0, 80.0)
    vB, reB, frB = _expected_segment(150.0, 3000.0, 50.0)
    vC, reC, frC = _expected_segment(150.0, 6000.0, 30.0)

    assert segA.velocity_ms == vA and segA.reynolds == reA and segA.friction_loss_pa == frA
    assert segB.velocity_ms == vB and segB.friction_loss_pa == frB
    assert segC.velocity_ms == vC and segC.friction_loss_pa == frC

    # J2's fixed-K tee applies at its two outlet ports (B, C) only, never at
    # its own inlet (A).
    assert segA.junction_loss_pa == 0.0
    assert segB.junction_loss_pa == FITTING_K * physics.velocity_pressure(AIR_DENSITY, vB)
    assert segC.junction_loss_pa == FITTING_K * physics.velocity_pressure(AIR_DENSITY, vC)
    assert tree.junctions["N2"].warning == ""  # a real K was found -- no fallback warning


def test_static_pressure_propagates_from_balancing_terminal():
    net = base_tree(loss_evaluator=lambda pv: FITTING_K)
    tree, _ = _solve(net)

    frA = tree.segments["A"].friction_loss_pa
    assert tree.junctions["N1"].static_pressure_pa == 0.0  # the reference point
    p_j2 = -frA
    assert tree.junctions["N2"].static_pressure_pa == p_j2
    p_j3 = p_j2 - tree.segments["B"].total_loss_pa
    p_j4 = p_j2 - tree.segments["C"].total_loss_pa
    assert tree.junctions["N3"].static_pressure_pa == p_j3
    assert tree.junctions["N4"].static_pressure_pa == p_j4


def test_missing_loss_data_falls_back_to_k_default_with_warning():
    # No loss_evaluator on J2's Primary at all -- a real branch (degree 3)
    # always has SOME physical loss, so K_DEFAULT applies with a warning
    # (unlike a degree-1 terminal, where "no loss" is the normal case).
    net = base_tree(loss_evaluator=None)
    tree, warnings = _solve(net)

    vB = tree.segments["B"].velocity_ms
    vC = tree.segments["C"].velocity_ms
    assert tree.segments["B"].junction_loss_pa == K_DEFAULT * physics.velocity_pressure(AIR_DENSITY, vB)
    assert tree.segments["C"].junction_loss_pa == K_DEFAULT * physics.velocity_pressure(AIR_DENSITY, vC)
    assert len(warnings) == 1
    assert "K_DEFAULT" not in warnings[0]  # human-readable message, not the raw constant name
    assert "N2" in warnings[0]
    assert tree.junctions["N2"].warning == warnings[0]


def test_terminal_with_no_loss_evaluator_gets_no_fallback_warning():
    # Degree-1 terminals (J1/J3/J4) never get a loss_evaluator in base_tree()
    # -- confirm they produce NO warning (matching a real, un-modeled
    # end_terminal_marker), unlike a real branch node.
    net = base_tree(loss_evaluator=lambda pv: FITTING_K)
    tree, warnings = _solve(net)

    assert warnings == []
    assert tree.junctions["N1"].warning == ""
    assert tree.junctions["N3"].warning == ""
    assert tree.junctions["N4"].warning == ""


def test_per_port_dict_loss_result_attributes_distinct_coefficients():
    # A dict loss result (one K per outlet port) lets sibling branches carry
    # different coefficients, unlike a single uniform float.
    k_by_edge = {"B": 0.2, "C": 0.6}
    net = base_tree(loss_evaluator=lambda pv: dict(k_by_edge))
    tree, warnings = _solve(net)
    assert warnings == []

    vB = tree.segments["B"].velocity_ms
    vC = tree.segments["C"].velocity_ms
    assert tree.segments["B"].junction_loss_pa == 0.2 * physics.velocity_pressure(AIR_DENSITY, vB)
    assert tree.segments["C"].junction_loss_pa == 0.6 * physics.velocity_pressure(AIR_DENSITY, vC)


def test_component_result_retains_distinct_per_leg_k_for_a_multiport_tee():
    # N2's tee has 3 real ports (A inlet, B/C outlets) -- its own retained
    # ComponentResult must carry one entry per outlet, each with its own K,
    # never a single collapsed scalar (see analysis/pressure.py's
    # ComponentPortResult/ComponentResult docstrings).
    k_by_edge = {"B": 0.18, "C": 1.05}
    net = base_tree(loss_evaluator=lambda pv: dict(k_by_edge))
    tree, warnings = _solve(net)
    assert warnings == []

    cres = tree.components["N2_Primary"]
    assert set(cres.port_results.keys()) == {"B", "C"}
    assert cres.port_results["B"].loss_coefficient == 0.18
    assert cres.port_results["C"].loss_coefficient == 1.05
    # The inlet (A) never receives a fitting-loss contribution -- see
    # pressure.py's own "inlet port -- fitting loss attributed at outlet
    # ports only" convention -- so it has no entry here either.
    assert "A" not in cres.port_results


def test_component_port_result_static_pressure_derived_per_leg_not_copied_from_node():
    # Each outlet leg's own static pressure = the node's single common
    # value minus that SAME leg's own already-retained pressure_drop_pa --
    # a real per-leg derivation, not the node's value blindly copied onto
    # every port (which would show the same number for both legs).
    k_by_edge = {"B": 0.2, "C": 0.6}
    net = base_tree(loss_evaluator=lambda pv: dict(k_by_edge))
    tree, _ = _solve(net)

    node_static = tree.junctions["N2"].static_pressure_pa
    cres = tree.components["N2_Primary"]
    assert cres.port_results["B"].static_pressure_pa == node_static - cres.port_results["B"].pressure_drop_pa
    assert cres.port_results["C"].static_pressure_pa == node_static - cres.port_results["C"].pressure_drop_pa
    # Different K on each leg -> different pressure_drop_pa -> genuinely
    # different static pressure per leg, not the same value at both.
    assert cres.port_results["B"].static_pressure_pa != cres.port_results["C"].static_pressure_pa


def test_component_port_result_pressure_drop_matches_segment_loss_exactly():
    # The retained ComponentPortResult must be a record of exactly what was
    # already added onto the segment's own junction_loss_pa -- never a
    # second, independently-computed value (which could silently drift or
    # double count).
    k_by_edge = {"B": 0.2, "C": 0.6}
    net = base_tree(loss_evaluator=lambda pv: dict(k_by_edge))
    tree, _ = _solve(net)

    cres = tree.components["N2_Primary"]
    assert cres.port_results["B"].pressure_drop_pa == tree.segments["B"].junction_loss_pa
    assert cres.port_results["C"].pressure_drop_pa == tree.segments["C"].junction_loss_pa
    assert cres.port_results["B"].velocity_ms == tree.segments["B"].velocity_ms
    assert cres.port_results["B"].flow_lps == tree.segments["B"].flow_lps


def test_component_result_records_each_outlet_for_a_uniform_float_k():
    # A uniform (non-dict) K is applied identically to every outlet port by
    # the solver -- the retained ComponentResult must still carry one
    # ComponentPortResult per outlet (not one merged value), migrating the
    # old scalar-only Inline-component representation onto the same
    # per-port architecture a dict result already used.
    net = base_tree(loss_evaluator=lambda pv: FITTING_K)
    tree, warnings = _solve(net)
    assert warnings == []

    cres = tree.components["N2_Primary"]
    assert set(cres.port_results.keys()) == {"B", "C"}
    assert cres.port_results["B"].loss_coefficient == FITTING_K
    assert cres.port_results["C"].loss_coefficient == FITTING_K
    assert cres.port_results["B"].pressure_drop_pa == tree.segments["B"].junction_loss_pa


def test_component_result_is_always_present_but_empty_when_no_loss_applies():
    # N1 is a degree-1 terminal with no loss_evaluator at all -- "no loss" is
    # the expected, normal case there. Its ComponentResult must still exist
    # (not be omitted), with an empty port_results, so a caller can always
    # rely on every component's own result being freshly replaced each
    # solve (see ComponentResult's own docstring on why this matters for
    # clearing stale data).
    net = base_tree(loss_evaluator=lambda pv: FITTING_K)
    tree, _ = _solve(net)

    assert "N1_Primary" in tree.components
    assert tree.components["N1_Primary"].port_results == {}


def test_missing_duct_size_is_reported_as_a_warning_not_raised():
    net = base_tree()
    net.segments["A"].section.diameter_mm = 0.0

    components, flow_warnings = flow.solve_flow_components(net)
    assert flow_warnings == []
    trees, warnings = PressureSolver().solve(net, components)

    assert trees == []
    assert len(warnings) == 1
    assert "duct dimensions" in warnings[0]
