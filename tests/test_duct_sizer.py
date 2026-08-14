"""
Tests for freecad.HVAC.core.DuctSizer against the same fake-object harness
used for AirflowSolver (see network_fixtures.py), verifying: constant-
velocity and constant-friction-rate sizing for all three profiles (circular,
rectangular, oval) and all three rectangular/oval modes, the preview-only
(non-mutating) solve()/apply() split, rounding, and the zero-flow and
missing-fixed-dimension edge cases.
"""

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from network_fixtures import (
    AIR_DENSITY,
    AIR_VISCOSITY,
    DEFAULT_ROUGHNESS_MM,
    FakeParser,
    base_tree,
    make_junction,
    make_net,
    make_segment,
)

from freecad.HVAC.core import airflow
from freecad.HVAC.core.DuctSizer import DuctSizer


def _two_node_tree(profile, diameter_mm=0.0, width_mm=0.0, height_mm=0.0,
                    length_mm=4000.0, roughness_mm=0.0, flow_lps=100.0, net_extra_props=None):
    """Single segment A: J1 (balancing terminal) --A--> J2 (leaf, flow_lps)."""
    node_ports = {1: [("A", "start")], 2: [("A", "end")]}
    edge_endpoints = {"A": (1, 2)}
    parser = FakeParser(node_ports, edge_endpoints)
    segment_map = {
        "A": make_segment("A", diameter_mm, length_mm, roughness_mm=roughness_mm, profile=profile,
                           width_mm=width_mm, height_mm=height_mm),
    }
    junction_map = {
        "N1": make_junction("J1", design_flow=0.0, type_id="end_terminal_marker"),
        "N2": make_junction("J2", design_flow=flow_lps, type_id="end_terminal_marker"),
    }
    net = make_net(parser, segment_map, junction_map, **(net_extra_props or {}))
    return net, segment_map, junction_map


def _sizing_props(method="ConstantVelocity", target_velocity=5.0, target_friction_rate=1.0,
                   rect_mode="FixedAspectRatio", aspect_ratio=2.0, rounding_mm=10.0,
                   default_width=0.0, default_height=0.0):
    return dict(
        SizingMethod=method,
        TargetVelocity=target_velocity,
        TargetFrictionRate=target_friction_rate,
        RectangularSizingMode=rect_mode,
        TargetAspectRatio=aspect_ratio,
        SizeRoundingIncrement=rounding_mm,
        DefaultWidth=default_width,
        DefaultHeight=default_height,
    )


def _round_up(value_mm, increment_mm):
    import math
    return math.ceil(value_mm / increment_mm - 1e-9) * increment_mm


# ----------------------------------------------------------------------------
# Constant velocity
# ----------------------------------------------------------------------------

def test_circular_constant_velocity_sizing():
    net, segment_map, _ = _two_node_tree(
        "Circular", diameter_mm=100.0, flow_lps=200.0,
        net_extra_props=_sizing_props(method="ConstantVelocity", target_velocity=5.0, rounding_mm=10.0),
    )
    result = DuctSizer(net).solve()

    assert result.warnings == []
    assert len(result.segments) == 1
    sres = result.segments[0]

    expected_d_m = airflow.circular_diameter_for_velocity(airflow.lps_to_m3s(200.0), 5.0)
    expected_mm = _round_up(expected_d_m * 1000.0, 10.0)
    assert sres.new_diameter_mm == pytest.approx(expected_mm)
    assert sres.old_diameter_mm == pytest.approx(100.0)
    assert sres.changed is True

    # Resulting velocity at the (rounded) new size should be <= target (rounding up never increases velocity).
    assert sres.velocity_ms <= 5.0 + 1e-6
    # segment_map object itself must be untouched until apply() is called.
    assert segment_map["A"].Diameter == pytest.approx(100.0)


def test_apply_writes_new_size_and_solve_does_not():
    net, segment_map, _ = _two_node_tree(
        "Circular", diameter_mm=100.0, flow_lps=200.0,
        net_extra_props=_sizing_props(target_velocity=5.0, rounding_mm=10.0),
    )
    sizer = DuctSizer(net)
    result = sizer.solve()
    assert segment_map["A"].Diameter == pytest.approx(100.0)  # solve() must not mutate

    changed_count = sizer.apply(result)
    assert changed_count == 1
    assert segment_map["A"].Diameter == pytest.approx(result.segments[0].new_diameter_mm)


def test_apply_skips_unchanged_segments():
    # Pre-size the duct to already match the target exactly (after rounding),
    # so solve() reports changed=False and apply() should leave it untouched.
    target_v = 5.0
    flow_lps = 200.0
    exact_mm = _round_up(airflow.circular_diameter_for_velocity(airflow.lps_to_m3s(flow_lps), target_v) * 1000.0, 10.0)
    net, segment_map, _ = _two_node_tree(
        "Circular", diameter_mm=exact_mm, flow_lps=flow_lps,
        net_extra_props=_sizing_props(target_velocity=target_v, rounding_mm=10.0),
    )
    result = DuctSizer(net).solve()
    assert result.segments[0].changed is False

    sizer = DuctSizer(net)
    changed_count = sizer.apply(result)
    assert changed_count == 0


@pytest.mark.parametrize("mode,rect_props", [
    ("FixedAspectRatio", {}),
    ("FixedHeight", {"height_mm": 300.0}),
    ("FixedWidth", {"width_mm": 500.0}),
])
def test_rectangular_constant_velocity_sizing_all_modes(mode, rect_props):
    net, segment_map, _ = _two_node_tree(
        "Rectangular", flow_lps=300.0,
        net_extra_props=_sizing_props(method="ConstantVelocity", target_velocity=6.0,
                                       rect_mode=mode, aspect_ratio=2.0, rounding_mm=10.0),
        **rect_props,
    )
    result = DuctSizer(net).solve()

    assert result.warnings == []
    sres = result.segments[0]
    assert sres.new_width_mm > 0.0
    assert sres.new_height_mm > 0.0
    if mode == "FixedHeight":
        assert sres.new_height_mm == pytest.approx(300.0)
    if mode == "FixedWidth":
        assert sres.new_width_mm == pytest.approx(500.0)
    if mode == "FixedAspectRatio":
        # both dimensions independently rounded, so only approximately the target ratio
        assert sres.new_width_mm / sres.new_height_mm == pytest.approx(2.0, rel=0.15)

    got_v = airflow.velocity_from_flow(
        airflow.lps_to_m3s(300.0),
        airflow.rectangular_area(airflow.mm_to_m(sres.new_width_mm), airflow.mm_to_m(sres.new_height_mm)),
    )
    assert got_v <= 6.0 + 1e-6


def test_oval_constant_velocity_sizing_fixed_aspect_ratio():
    net, segment_map, _ = _two_node_tree(
        "Oval", flow_lps=250.0,
        net_extra_props=_sizing_props(method="ConstantVelocity", target_velocity=6.0,
                                       rect_mode="FixedAspectRatio", aspect_ratio=1.8, rounding_mm=10.0),
    )
    result = DuctSizer(net).solve()

    assert result.warnings == []
    sres = result.segments[0]
    assert sres.new_width_mm >= sres.new_height_mm > 0.0


# ----------------------------------------------------------------------------
# Constant friction rate
# ----------------------------------------------------------------------------

def test_circular_constant_friction_rate_sizing():
    net, segment_map, _ = _two_node_tree(
        "Circular", diameter_mm=100.0, flow_lps=200.0,
        net_extra_props=_sizing_props(method="ConstantFrictionRate", target_friction_rate=1.0, rounding_mm=10.0),
    )
    result = DuctSizer(net).solve()

    assert result.warnings == []
    sres = result.segments[0]
    expected_d_m = airflow.circular_diameter_for_friction_rate(
        airflow.lps_to_m3s(200.0), 1.0, airflow.mm_to_m(DEFAULT_ROUGHNESS_MM), AIR_VISCOSITY, AIR_DENSITY
    )
    expected_mm = _round_up(expected_d_m * 1000.0, 10.0)
    assert sres.new_diameter_mm == pytest.approx(expected_mm)
    assert sres.friction_rate_pa_per_m <= 1.0 + 1e-3


@pytest.mark.parametrize("mode,rect_props", [
    ("FixedAspectRatio", {}),
    ("FixedHeight", {"height_mm": 250.0}),
    ("FixedWidth", {"width_mm": 400.0}),
])
def test_rectangular_constant_friction_rate_sizing_all_modes(mode, rect_props):
    net, segment_map, _ = _two_node_tree(
        "Rectangular", flow_lps=300.0,
        net_extra_props=_sizing_props(method="ConstantFrictionRate", target_friction_rate=1.0,
                                       rect_mode=mode, aspect_ratio=2.0, rounding_mm=10.0),
        **rect_props,
    )
    result = DuctSizer(net).solve()

    assert result.warnings == []
    sres = result.segments[0]
    assert sres.new_width_mm > 0.0
    assert sres.new_height_mm > 0.0
    if mode == "FixedHeight":
        assert sres.new_height_mm == pytest.approx(250.0)
    if mode == "FixedWidth":
        assert sres.new_width_mm == pytest.approx(400.0)


# ----------------------------------------------------------------------------
# Edge cases
# ----------------------------------------------------------------------------

def test_zero_flow_segment_is_left_unchanged():
    # A terminal's own DesignFlowRate can't legitimately be exactly 0 (that's
    # the "leave blank for the balancing terminal" sentinel -- see
    # FlowNetwork.py), so exercise the "flow_lps <= 0" defensive branch of
    # _size_segment directly rather than trying to construct an ambiguous
    # network topology.
    seg = make_segment("A", 150.0, 4000.0, profile="Circular")
    net = make_net(FakeParser({}, {}), {}, {}, **_sizing_props())

    sres = DuctSizer(net)._size_segment(
        seg, 0.0, "ConstantVelocity", "aspect_ratio", 2.0,
        5.0, 1.0, 10.0, AIR_VISCOSITY, AIR_DENSITY, DEFAULT_ROUGHNESS_MM, 0.0, 0.0,
    )

    assert sres.changed is False
    assert sres.new_diameter_mm == pytest.approx(150.0)


def test_fixed_height_mode_without_existing_or_default_height_warns():
    net, segment_map, _ = _two_node_tree(
        "Rectangular", flow_lps=300.0,
        net_extra_props=_sizing_props(rect_mode="FixedHeight", default_height=0.0),
        # no height_mm passed -> segment's own Height is 0, and DefaultHeight is 0 too
    )
    result = DuctSizer(net).solve()

    assert result.segments == []
    assert len(result.warnings) == 1
    assert "Height" in result.warnings[0]


def test_fixed_height_mode_falls_back_to_network_default_height():
    net, segment_map, _ = _two_node_tree(
        "Rectangular", flow_lps=300.0,
        net_extra_props=_sizing_props(rect_mode="FixedHeight", default_height=350.0),
    )
    result = DuctSizer(net).solve()

    assert result.warnings == []
    assert result.segments[0].new_height_mm == pytest.approx(350.0)


def test_unsupported_profile_warns_without_crashing():
    net, segment_map, _ = _two_node_tree("", flow_lps=300.0, net_extra_props=_sizing_props())
    result = DuctSizer(net).solve()

    assert result.segments == []
    assert len(result.warnings) == 1


def test_base_tree_all_segments_sized():
    # Sanity check against the shared 3-segment tee tree used by AirflowSolver tests.
    net, segment_map, _ = base_tree(net_extra_props=_sizing_props(target_velocity=5.0))
    result = DuctSizer(net).solve()

    assert result.warnings == []
    assert {s.key for s in result.segments} == {"A", "B", "C"}
    assert all(s.new_diameter_mm > 0.0 for s in result.segments)
