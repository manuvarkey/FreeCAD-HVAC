"""
Focused tests for AirflowSolver's Phase E handling of a simple through/2-port
junction whose DuctJunction carries more than one DuctComponent (a Primary
plus one or more Inline devices, e.g. a reducer followed by a damper): each
component's own loss is evaluated against its own local port/velocity and
converted to Pa immediately, and the Pa contributions -- never the raw K
values -- are what gets summed to make up the junction's aggregate fitting
loss (spec: "do not generically sum K values unless they use the same
reference velocity").
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


class _FakeCallLossRegistry:
    """resolve_type/call_loss dispatch keyed purely by TypeId, so each
    component's own K (dict-result for the reducer, float-result for the
    damper -- matching real elbow_loss/transition_loss vs
    inline_device_loss shapes) is independent of the other's."""

    def __init__(self, results_by_type_id):
        self._results = results_by_type_id

    def resolve_type(self, library_id, type_id):
        return object() if type_id in self._results else None

    def call_loss(self, library_id, type_def, context):
        return self._results[context["type_id"]]


def _single_component_junction(label, type_id, port):
    obj = FakeObj(Label=label, Topology="end")
    component = FakeObj(
        Label=label + "_Comp0", ComponentRole="Primary", Sequence=0,
        LibraryId="testlib", TypeId=type_id, Family="",
        LocalPortsJson=json.dumps([port]),
    )
    obj.Proxy = _FakeChainJunctionProxy([component])
    return obj


def test_chain_component_losses_are_converted_to_pa_before_summing(monkeypatch):
    # J1 (AHU, balancing terminal) --segA(200mm)--> J2 (Reducer[Primary] +
    # Damper[Inline]) --segB(180mm)--> J3 (leaf terminal).
    node_ports = {1: [("A", "start")], 2: [("A", "end"), ("B", "start")], 3: [("B", "end")]}
    edge_endpoints = {"A": (1, 2), "B": (2, 3)}
    parser = FakeParser(node_ports, edge_endpoints)

    segment_map = {
        "A": make_segment("A", 200.0, 5000.0),
        "B": make_segment("B", 180.0, 4000.0),
    }

    reducer = FakeObj(
        Label="Reducer", ComponentRole="Primary", Sequence=0,
        LibraryId="testlib", TypeId="fake_reducer", Family="",
        LocalPortsJson=json.dumps([
            _port("A", "end", 200.0, True),
            _port("N2#seam0", "start", 150.0, False),
        ]),
    )
    damper = FakeObj(
        Label="Damper", ComponentRole="Inline", Sequence=10,
        LibraryId="testlib", TypeId="fake_damper", Family="",
        LocalPortsJson=json.dumps([
            _port("N2#seam0", "end", 150.0, True),
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
        # elbow_loss/transition_loss shape: a dict keyed to the component's
        # OWN outlet edge_key.
        "fake_reducer": {"N2#seam0": K_REDUCER},
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
    v_seam = airflow.velocity_from_flow(flow_m3s, airflow.circular_area(airflow.mm_to_m(150.0)))
    v_b = seg_b.velocity_ms

    expected_reducer_pa = K_REDUCER * airflow.velocity_pressure(AIR_DENSITY, v_seam)
    expected_damper_pa = K_DAMPER * airflow.velocity_pressure(AIR_DENSITY, v_b)
    expected_total_pa = expected_reducer_pa + expected_damper_pa

    # The whole chain's loss lands on segB (the real segment downstream of
    # the junction) -- as a Pa sum, not a K sum: K_REDUCER and K_DAMPER are
    # referenced to genuinely different velocities (150mm seam vs 180mm
    # real duct), so summing them into one K before applying velocity
    # pressure would give a different (wrong) number.
    assert seg_b.fitting_loss_pa == pytest.approx(expected_total_pa)
    naive_wrong_total = (K_REDUCER + K_DAMPER) * airflow.velocity_pressure(AIR_DENSITY, v_b)
    assert abs(seg_b.fitting_loss_pa - naive_wrong_total) > 1e-6

    # Per-component results are stored on each DuctComponent.
    assert reducer.CalcLossCoefficient == K_REDUCER
    assert reducer.CalcVelocity == pytest.approx(v_seam)
    assert reducer.CalcPressureDrop == pytest.approx(expected_reducer_pa)

    assert damper.CalcLossCoefficient == K_DAMPER
    assert damper.CalcVelocity == pytest.approx(v_b)
    assert damper.CalcPressureDrop == pytest.approx(expected_damper_pa)
