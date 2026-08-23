"""
Pure tests for freecad.HVAC.analysis.balancing -- no FreeCAD/conftest
stubbing needed, since analysis/ has no FreeCAD dependency at all.
"""

from analysis_fixtures import base_tree

from freecad.HVAC.analysis import flow
from freecad.HVAC.analysis.balancing import PressureBalanceCoordinator
from freecad.HVAC.analysis.model import SizingSettings
from freecad.HVAC.analysis.pressure import PressureSolver
from freecad.HVAC.analysis.sizing import LocalStaticRegainSizer


def _components(net):
    components, warnings = flow.solve_flow_components(net)
    assert warnings == []
    return components


def _max_deficit(net, sections):
    working_net = net.with_segment_sections(sections)
    trees, warnings = PressureSolver().solve(working_net, _components(working_net))
    assert warnings == []
    return max(p.pressure_deficit_pa for p in trees[0].paths)


def _imbalanced_tree(j3_flow=50.0, j4_flow=50.0):
    # B is short (little friction, so its path is naturally "too easy"
    # relative to the AHU's required total pressure) while C is very long
    # (naturally the critical path) -- classic static-regain imbalance.
    return base_tree(
        j3_flow=j3_flow, j4_flow=j4_flow, segB_dia=200.0, segC_dia=200.0,
        segA_dia=300.0, segB_len=500.0, segC_len=30000.0, loss_evaluator=lambda pv: 0.0,
    )


def test_balancing_reduces_the_worst_path_deficit():
    net = _imbalanced_tree()
    settings = SizingSettings(
        method="StaticRegain", target_velocity_ms=5.0, min_velocity_ms=2.5, regain_factor=0.75,
        rounding_mm=0.0, balance_tolerance_pa=5.0, balance_max_iterations=15,
    )
    components = _components(net)

    base = LocalStaticRegainSizer().size(net, components, settings)
    base_sections = {edge_key: r.new_section for edge_key, r in base.segments.items()}
    base_deficit = _max_deficit(net, base_sections)

    result = PressureBalanceCoordinator().size(net, components, settings)
    final_sections = {edge_key: r.new_section for edge_key, r in result.segments.items()}
    final_deficit = _max_deficit(net, final_sections)

    assert final_deficit < base_deficit
    # Balancing must only ever shrink the easy branch (B), never touch the
    # critical one (C) -- it has nothing to gain by making the worst path
    # worse.
    assert result.segments["B"].new_section.diameter_mm < base.segments["B"].new_section.diameter_mm
    assert result.segments["C"].new_section.diameter_mm == base.segments["C"].new_section.diameter_mm


def test_balancing_reports_a_requirement_when_it_cant_fully_close_the_gap():
    net = _imbalanced_tree()
    settings = SizingSettings(
        method="StaticRegain", target_velocity_ms=5.0, min_velocity_ms=2.5, regain_factor=0.75,
        rounding_mm=0.0, balance_tolerance_pa=0.01,  # near-zero tolerance -- forces the velocity ceiling to bind
        balance_max_iterations=15,
    )
    result = PressureBalanceCoordinator().size(net, _components(net), settings)

    assert len(result.balancing_requirements) == 1
    req = result.balancing_requirements[0]
    assert req.junction_id == "N2"
    assert req.branch_port == "B"
    assert req.pressure_deficit_pa > 0.0
    assert req.required_k > 0.0


def test_balanced_network_needs_no_requirement():
    # A generous tolerance means the base sizer's own result is already
    # "close enough" -- no adjustment, no requirement.
    net = base_tree(loss_evaluator=lambda pv: 0.0)
    settings = SizingSettings(
        method="StaticRegain", target_velocity_ms=5.0, min_velocity_ms=2.5, regain_factor=0.75,
        rounding_mm=0.0, balance_tolerance_pa=1e6, balance_max_iterations=5,
    )
    result = PressureBalanceCoordinator().size(net, _components(net), settings)

    assert result.balancing_requirements == []
    assert not any("Pressure balancing" in w for w in result.warnings)
