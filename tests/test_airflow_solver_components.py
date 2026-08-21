"""
Focused tests for AirflowSolver's Phase E handling of a junction whose
DuctJunction carries a Primary plus one or more Inline devices attached to
individual real edges (see DuctJunction.getPortChains): the Primary is
evaluated once against its own full real multi-port context, and separately,
each real edge's own Inline chain is evaluated against THAT EDGE's own flow
-- each component's own K is converted to Pa immediately and summed, never
generically summed as raw K (spec: "do not sum K values unless they use the
same reference velocity").
"""

import json

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
import pytest

from freecad.HVAC.core import airflow
from freecad.HVAC.core.AirflowSolver import AirflowSolver
from freecad.HVAC.utils import hvaclib
from network_fixtures import FakeObj, FakeParser, make_net, make_segment

AIR_DENSITY = 1.204


def _port(edge_key, segment_end, diameter_mm, flow_into_junction):
    return {
        "edge_key": edge_key,
        "segment_end": segment_end,
        "position": [0.0, 0.0, 0.0],
        "direction": [1.0, 0.0, 0.0],
        "profile": "Circular",
        "section_params": {"Diameter": diameter_mm},
        "attachment": "Center",
        "user_offset": [0.0, 0.0, 0.0],
        "profile_x_axis": None,
        "flow_role": "inlet" if flow_into_junction else "outlet",
        "flow_direction": [1.0, 0.0, 0.0],
        "flow_into_junction": flow_into_junction,
    }


class _FakeChainJunctionProxy:
    def __init__(self, components):
        self._components = components

    def getComponents(self):
        return list(self._components)

    def getPrimaryComponent(self):
        return next((c for c in self._components if c.ComponentRole == "Primary"), None)

    def getPortChains(self):
        chains = {}
        for c in self._components:
            if c.ComponentRole != "Inline":
                continue
            edge_key = getattr(c, "AttachedEdgeKey", "")
            if edge_key:
                chains.setdefault(edge_key, []).append(c)
        for lst in chains.values():
            lst.sort(key=lambda c: int(getattr(c, "PortSequence", 0)))
        return chains


class _FakeCallLossRegistry:
    """resolve_type/call_loss dispatch keyed purely by TypeId, so each
    component's own K (dict-result for a junction fitting -- matching real
    elbow_loss/transition_loss/branch_loss shapes -- or a float-result for a
    damper, matching inline_device_loss) is independent of the others'."""

    def __init__(self, results_by_type_id):
        self._results = results_by_type_id

    def resolve_type(self, library_id, type_id):
        return object() if type_id in self._results else None

    def call_loss(self, library_id, type_def, context):
        return self._results[context["type_id"]]


def _single_component_junction(label, type_id, port):
    obj = FakeObj(Label=label, Topology="end")
    component = FakeObj(
        Label=label + "_Comp0", ComponentRole="Primary",
        LibraryId="testlib", TypeId=type_id, Family="",
        LocalPortsJson=json.dumps([port]),
    )
    obj.Proxy = _FakeChainJunctionProxy([component])
    return obj


def test_primary_and_chain_losses_on_the_same_edge_are_summed_as_pa(monkeypatch):
    """
    J1 (AHU, balancing terminal) --segA(200mm)--> J2 (Reducer[Primary] +
    Damper[Inline, attached to edge B]) --segB(180mm)--> J3 (leaf terminal).

    The reducer's Primary is always given the literal real ports (A, B)
    unchanged -- no synthetic intermediate size -- so both its own dict K
    (keyed to its own real outlet, B) and the damper's own K (attached to
    that same edge B) are converted to Pa using edge B's own real velocity
    and summed additively onto segB's fitting_loss_pa.
    """
    node_ports = {1: [("A", "start")], 2: [("A", "end"), ("B", "start")], 3: [("B", "end")]}
    edge_endpoints = {"A": (1, 2), "B": (2, 3)}
    parser = FakeParser(node_ports, edge_endpoints)

    segment_map = {
        "A": make_segment("A", 200.0, 5000.0),
        "B": make_segment("B", 180.0, 4000.0),
    }

    reducer = FakeObj(
        Label="Reducer", ComponentRole="Primary",
        LibraryId="testlib", TypeId="fake_reducer", Family="",
        LocalPortsJson=json.dumps([
            _port("A", "end", 200.0, True),
            _port("B", "start", 180.0, False),
        ]),
    )
    damper = FakeObj(
        Label="Damper", ComponentRole="Inline", AttachedEdgeKey="B", PortSequence=10,
        LibraryId="testlib", TypeId="fake_damper", Family="",
        LocalPortsJson=json.dumps([
            _port("N2#B_seam0", "end", 180.0, True),
            _port("B", "start", 180.0, False),
        ]),
    )

    junction_map = {
        "N1": _single_component_junction("J1", "end_terminal_marker", _port("A", "start", 200.0, False)),
        "N2": FakeObj(Label="J2", Topology="through"),
        "N3": _single_component_junction("J3", "end_terminal_marker", _port("B", "end", 180.0, True)),
    }
    junction_map["N1"].DesignFlowRate = 0.0
    junction_map["N3"].DesignFlowRate = 80.0
    junction_map["N2"].Proxy = _FakeChainJunctionProxy([reducer, damper])

    net = make_net(parser, segment_map, junction_map)

    K_REDUCER = 0.4
    K_DAMPER = 0.25
    registry = _FakeCallLossRegistry({
        "end_terminal_marker": None,
        # elbow_loss/transition_loss shape: a dict keyed to the Primary's
        # own real outlet edge_key.
        "fake_reducer": {"B": K_REDUCER},
        # inline_device_loss shape: a bare float, applied to the outlet.
        "fake_damper": K_DAMPER,
    })
    monkeypatch.setattr(
        hvaclib.HVACLibraryService, "get_hvac_library_registry", staticmethod(lambda: registry),
    )

    result = AirflowSolver(net).solve()
    assert not result.warnings

    seg_by_key = {s.key: s for comp in result.components for s in comp.segments}
    seg_b = seg_by_key["B"]

    flow_m3s = airflow.lps_to_m3s(80.0)
    v_b = airflow.velocity_from_flow(flow_m3s, airflow.circular_area(airflow.mm_to_m(180.0)))
    assert seg_b.velocity_ms == pytest.approx(v_b)

    expected_reducer_pa = K_REDUCER * airflow.velocity_pressure(AIR_DENSITY, v_b)
    expected_damper_pa = K_DAMPER * airflow.velocity_pressure(AIR_DENSITY, v_b)
    expected_total_pa = expected_reducer_pa + expected_damper_pa

    # Both contributions land on segB, added together -- the Primary's own
    # per-edge loss (evaluated once, over its real multi-port context) and
    # that same edge's own Inline chain loss (evaluated separately, using
    # edge B's own flow) are independent and additive.
    assert seg_b.fitting_loss_pa == pytest.approx(expected_total_pa)

    # Per-component results are stored on each DuctComponent.
    assert damper.CalcLossCoefficient == K_DAMPER
    assert damper.CalcVelocity == pytest.approx(v_b)
    assert damper.CalcPressureDrop == pytest.approx(expected_damper_pa)


def _tee_network(k_tee, k_run_damper, k_branch_damper):
    """
    J1 (AHU, balancing terminal) --segA(300mm, trunk)--> J2 (tee)
                                        |--segB(300mm, run)--> J3 (leaf, 700 L/s)
                                        '--segC(200mm, branch)--> J4 (leaf, 300 L/s)

    J2's Primary is a tee with real ports A (inlet), B (run outlet), C
    (branch outlet). A damper is attached to the run leg (B) and a
    different damper to the branch leg (C) -- two independent Inline
    chains on two different edges of the same junction.
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
        "A": make_segment("A", 300.0, 6000.0),
        "B": make_segment("B", 300.0, 4000.0),
        "C": make_segment("C", 200.0, 3000.0),
    }

    tee = FakeObj(
        Label="Tee", ComponentRole="Primary",
        LibraryId="testlib", TypeId="fake_tee", Family="branch.tee",
        LocalPortsJson=json.dumps([
            _port("A", "end", 300.0, True),
            _port("B", "start", 300.0, False),
            _port("C", "start", 200.0, False),
        ]),
    )
    run_damper = FakeObj(
        Label="RunDamper", ComponentRole="Inline", AttachedEdgeKey="B", PortSequence=10,
        LibraryId="testlib", TypeId="fake_run_damper", Family="",
        LocalPortsJson=json.dumps([
            _port("N2#B_seam0", "end", 300.0, True),
            _port("B", "start", 300.0, False),
        ]),
    )
    branch_damper = FakeObj(
        Label="BranchDamper", ComponentRole="Inline", AttachedEdgeKey="C", PortSequence=10,
        LibraryId="testlib", TypeId="fake_branch_damper", Family="",
        LocalPortsJson=json.dumps([
            _port("N2#C_seam0", "end", 200.0, True),
            _port("C", "start", 200.0, False),
        ]),
    )

    junction_map = {
        "N1": _single_component_junction("J1", "end_terminal_marker", _port("A", "start", 300.0, False)),
        "N2": FakeObj(Label="J2", Topology="branch"),
        "N3": _single_component_junction("J3", "end_terminal_marker", _port("B", "end", 300.0, True)),
        "N4": _single_component_junction("J4", "end_terminal_marker", _port("C", "end", 200.0, True)),
    }
    junction_map["N1"].DesignFlowRate = 0.0
    junction_map["N3"].DesignFlowRate = 700.0
    junction_map["N4"].DesignFlowRate = 300.0
    junction_map["N2"].Proxy = _FakeChainJunctionProxy([tee, run_damper, branch_damper])

    net = make_net(parser, segment_map, junction_map)

    registry = _FakeCallLossRegistry({
        "end_terminal_marker": None,
        # branch_loss shape: one K per real outlet port.
        "fake_tee": {"B": k_tee, "C": k_tee},
        "fake_run_damper": k_run_damper,
        "fake_branch_damper": k_branch_damper,
    })

    return net, registry, run_damper, branch_damper


def test_branch_leg_inline_component_uses_branch_flow_not_total_flow(monkeypatch):
    K_TEE = 0.2
    K_RUN_DAMPER = 0.15
    K_BRANCH_DAMPER = 0.3
    net, registry, run_damper, branch_damper = _tee_network(K_TEE, K_RUN_DAMPER, K_BRANCH_DAMPER)
    monkeypatch.setattr(
        hvaclib.HVACLibraryService, "get_hvac_library_registry", staticmethod(lambda: registry),
    )

    result = AirflowSolver(net).solve()
    assert not result.warnings

    seg_by_key = {s.key: s for comp in result.components for s in comp.segments}
    seg_b, seg_c = seg_by_key["B"], seg_by_key["C"]

    v_b = airflow.velocity_from_flow(airflow.lps_to_m3s(700.0), airflow.circular_area(airflow.mm_to_m(300.0)))
    v_c = airflow.velocity_from_flow(airflow.lps_to_m3s(300.0), airflow.circular_area(airflow.mm_to_m(200.0)))
    assert seg_b.velocity_ms == pytest.approx(v_b)
    assert seg_c.velocity_ms == pytest.approx(v_c)

    # The branch damper must be evaluated against edge C's OWN flow/velocity
    # (300 L/s through 200mm), never the trunk's total flow (1000 L/s) or
    # the run leg's own flow/size.
    assert branch_damper.CalcVelocity == pytest.approx(v_c)
    assert branch_damper.CalcVelocity != pytest.approx(v_b)

    expected_tee_b_pa = K_TEE * airflow.velocity_pressure(AIR_DENSITY, v_b)
    expected_tee_c_pa = K_TEE * airflow.velocity_pressure(AIR_DENSITY, v_c)
    expected_run_damper_pa = K_RUN_DAMPER * airflow.velocity_pressure(AIR_DENSITY, v_b)
    expected_branch_damper_pa = K_BRANCH_DAMPER * airflow.velocity_pressure(AIR_DENSITY, v_c)

    assert seg_b.fitting_loss_pa == pytest.approx(expected_tee_b_pa + expected_run_damper_pa)
    assert seg_c.fitting_loss_pa == pytest.approx(expected_tee_c_pa + expected_branch_damper_pa)

    # A naive implementation that (wrongly) evaluated the branch damper
    # against the trunk's total flow through the branch leg's own 200mm
    # size would give a different, wrong velocity/pressure-drop.
    naive_wrong_v_c = airflow.velocity_from_flow(airflow.lps_to_m3s(1000.0), airflow.circular_area(airflow.mm_to_m(200.0)))
    assert abs(branch_damper.CalcVelocity - naive_wrong_v_c) > 1e-6

    assert run_damper.CalcLossCoefficient == K_RUN_DAMPER
    assert branch_damper.CalcLossCoefficient == K_BRANCH_DAMPER


def test_inline_component_on_inlet_edge_derives_velocity_from_that_edges_own_flow(monkeypatch):
    """
    Merge/split direction check: an Inline component attached to a real
    INLET edge (flow_into_junction=True -- Segment -> Inline -> Primary)
    has its own OUTLET port on the synthetic, Primary-facing side, not on
    the real segment side -- _fillPortFlow must still resolve that
    synthetic port's velocity from the real edge's own flow (there's no
    matching real segment for a synthetic edge_key, so it falls to the
    "derive locally from this edge's own flow" branch), not silently drop
    it or use the wrong edge's flow.
    """
    node_ports = {1: [("A", "start")], 2: [("A", "end"), ("B", "start")], 3: [("B", "end")]}
    edge_endpoints = {"A": (1, 2), "B": (2, 3)}
    parser = FakeParser(node_ports, edge_endpoints)

    segment_map = {
        "A": make_segment("A", 250.0, 5000.0),
        "B": make_segment("B", 250.0, 4000.0),
    }

    primary = FakeObj(
        Label="Primary", ComponentRole="Primary",
        LibraryId="testlib", TypeId="fake_through", Family="",
        LocalPortsJson=json.dumps([
            _port("A", "end", 250.0, True),
            _port("B", "start", 250.0, False),
        ]),
    )
    # Damper attached to the INLET edge A: flow goes Segment(A) -> Damper ->
    # Primary, so the damper's own OUTLET (flow_into_junction=False) is its
    # inner, synthetic seam port -- not a real segment.
    inlet_damper = FakeObj(
        Label="InletDamper", ComponentRole="Inline", AttachedEdgeKey="A", PortSequence=10,
        LibraryId="testlib", TypeId="fake_inlet_damper", Family="",
        LocalPortsJson=json.dumps([
            _port("N2#A_seam0", "end", 250.0, False),  # inner: this component's own outlet
            _port("A", "start", 250.0, True),           # outer: real edge A, this component's own inlet
        ]),
    )

    junction_map = {
        "N1": _single_component_junction("J1", "end_terminal_marker", _port("A", "start", 250.0, False)),
        "N2": FakeObj(Label="J2", Topology="through"),
        "N3": _single_component_junction("J3", "end_terminal_marker", _port("B", "end", 250.0, True)),
    }
    junction_map["N1"].DesignFlowRate = 0.0
    junction_map["N3"].DesignFlowRate = 60.0
    junction_map["N2"].Proxy = _FakeChainJunctionProxy([primary, inlet_damper])

    net = make_net(parser, segment_map, junction_map)

    K_INLET_DAMPER = 0.2
    registry = _FakeCallLossRegistry({
        "end_terminal_marker": None,
        # A real (degree >= 2) Primary with no loss data would otherwise
        # trigger the K_DEFAULT-fallback warning path -- give it an
        # explicit zero so this test stays focused on the inlet damper.
        "fake_through": {"B": 0.0},
        "fake_inlet_damper": K_INLET_DAMPER,
    })
    monkeypatch.setattr(
        hvaclib.HVACLibraryService, "get_hvac_library_registry", staticmethod(lambda: registry),
    )

    result = AirflowSolver(net).solve()
    assert not result.warnings

    seg_by_key = {s.key: s for comp in result.components for s in comp.segments}
    seg_a = seg_by_key["A"]

    v_a = airflow.velocity_from_flow(airflow.lps_to_m3s(60.0), airflow.circular_area(airflow.mm_to_m(250.0)))
    assert seg_a.velocity_ms == pytest.approx(v_a)

    # The damper's own outlet (the synthetic inner port) must resolve to
    # edge A's own velocity, derived locally since there's no real segment
    # matching its synthetic edge_key.
    assert inlet_damper.CalcVelocity == pytest.approx(v_a)
    expected_pa = K_INLET_DAMPER * airflow.velocity_pressure(AIR_DENSITY, v_a)
    assert inlet_damper.CalcPressureDrop == pytest.approx(expected_pa)
    assert seg_a.fitting_loss_pa == pytest.approx(expected_pa)
