"""
Integration-style tests for freecad.HVAC.core.AirflowSolver against a small
synthetic tree network, using lightweight fake stand-ins for the FreeCAD
DuctNetwork/DuctJunction/DuctSegment objects and the DuctNetworkParser API
surface the solver depends on (nodes/node_edges/edge_analysis_nodes/node_key/
build_junction_analysis). This avoids needing a real FreeCAD installation or
real base geometry while still exercising the actual solver algorithm
end-to-end (flow conservation, friction+fitting loss, pressure propagation,
and the error paths).

Network under test (a simple supply tree):

    J1 (AHU, balancing terminal) --segA(5m,200mm dia)--> J2 (tee)
                                                              |--segB(3m,150mm dia)--> J3 (leaf, 50 L/s)
                                                              '--segC(6m,150mm dia)--> J4 (leaf, 30 L/s)

Base-geometry direction convention: every segment's "start" is its upstream
(AHU-facing) end, "end" is its downstream end -- matching flow_into_junction
semantics in NetworkParser.build_junction_ports.
"""

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core import airflow
from freecad.HVAC.core.AirflowSolver import AirflowSolver, AirflowSolveError, K_DEFAULT
from freecad.HVAC.core.NetworkParser import EdgeRef, JunctionAnalysis, JunctionPort


AIR_DENSITY = 1.204
AIR_VISCOSITY = 1.51e-5
DEFAULT_ROUGHNESS_MM = 0.09
FITTING_K = 0.5


class FakeObj:
    """Minimal stand-in for a FreeCAD DocumentObject: free attribute get/set."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _edge(tag):
    return EdgeRef(obj_name="Line_{}".format(tag), local_index=0, tag=tag)


def _port(edge_key, segment_end, flow_into_junction):
    return JunctionPort(
        edge_key=edge_key,
        segment_end=segment_end,
        position=(0.0, 0.0, 0.0),
        direction=(1.0, 0.0, 0.0),
        profile="Circular",
        section_params={},
        attachment="Center",
        user_offset=(0.0, 0.0, 0.0),
        profile_x_axis=None,
        flow_role=("inlet" if flow_into_junction else "outlet"),
        flow_direction=(1.0, 0.0, 0.0),
        flow_into_junction=flow_into_junction,
    )


class FakeParser:
    """
    node_edges/edge_endpoints/node_key/build_junction_analysis, driven from a
    plain description of {node_id: [(edge_tag, "start"|"end"), ...]}.
    """

    def __init__(self, node_ports, edge_endpoints):
        # node_ports: {node_id: [(edge_tag, segment_end), ...]}
        self._node_ports = node_ports
        self._edge_endpoints = edge_endpoints  # {edge_tag: (u, v)}  (u = "start" node, v = "end" node)

    def nodes(self):
        return sorted(self._node_ports.keys())

    def node_edges(self, node_id):
        return [_edge(tag) for tag, _end in self._node_ports[node_id]]

    def edge_analysis_nodes(self, edge_ref):
        return self._edge_endpoints[edge_ref.tag]

    def node_key(self, node_id):
        return "N{}".format(node_id)

    def build_junction_analysis(self, node_id, segment_map):
        ports = [
            _port(tag, end, flow_into_junction=(end == "end"))
            for tag, end in self._node_ports[node_id]
        ]
        degree = len(ports)
        if degree <= 0:
            return None
        return JunctionAnalysis(
            topology=("end" if degree == 1 else "branch"),
            family="",
            family_tags=[],
            family_key="",
            connected_ports=ports,
            point=(0.0, 0.0, 0.0),
            degree=degree,
            port_origins=[],
            edge_vectors=[],
            edge_angles={},
            edge_eccentricities={},
            collinear_pairs=[],
            orthogonal_pairs=[],
            is_coplanar=True,
        )


class FakeProxy:
    def __init__(self, parser, segment_map, junction_map):
        self._parser = parser
        self._segment_map = segment_map
        self._junction_map = junction_map

    def getParser(self, rebuild=False):
        return self._parser

    def collectSegmentObjects(self):
        return dict(self._segment_map)

    def collectJunctionObjects(self):
        return dict(self._junction_map)


def _make_segment(tag, diameter_mm, length_mm, roughness_mm=0.0):
    return FakeObj(
        Label=tag,
        SegmentKey=tag,
        Name=tag,
        Profile="Circular",
        Diameter=diameter_mm,
        Width=0.0,
        Height=0.0,
        Roughness=roughness_mm,
        EffectiveLength=length_mm,
    )


def _make_junction(label, design_flow=0.0, library_id="testlib", type_id="branch_tee_generic"):
    return FakeObj(
        Label=label,
        LibraryId=library_id,
        TypeId=type_id,
        Family="",
        DesignFlowRate=design_flow,
    )


def _make_net(parser, segment_map, junction_map):
    net = FakeObj(
        AirDensity=AIR_DENSITY,
        AirKinematicViscosity=AIR_VISCOSITY,
        DefaultRoughness=DEFAULT_ROUGHNESS_MM,
    )
    net.Proxy = FakeProxy(parser, segment_map, junction_map)
    return net


def _base_tree(j3_flow=50.0, j4_flow=30.0, segA_len=5000.0, segB_len=3000.0, segC_len=6000.0,
                segA_dia=200.0, segB_dia=150.0, segC_dia=150.0):
    node_ports = {
        1: [("A", "start")],
        2: [("A", "end"), ("B", "start"), ("C", "start")],
        3: [("B", "end")],
        4: [("C", "end")],
    }
    edge_endpoints = {"A": (1, 2), "B": (2, 3), "C": (2, 4)}
    parser = FakeParser(node_ports, edge_endpoints)

    segment_map = {
        "A": _make_segment("A", segA_dia, segA_len),
        "B": _make_segment("B", segB_dia, segB_len),
        "C": _make_segment("C", segC_dia, segC_len),
    }
    junction_map = {
        "N1": _make_junction("J1", design_flow=0.0, type_id="end_terminal_marker"),
        "N2": _make_junction("J2", design_flow=0.0, type_id="branch_tee_generic"),
        "N3": _make_junction("J3", design_flow=j3_flow, type_id="end_terminal_marker"),
        "N4": _make_junction("J4", design_flow=j4_flow, type_id="end_terminal_marker"),
    }
    net = _make_net(parser, segment_map, junction_map)
    return net, segment_map, junction_map


class _FakeTypeDef:
    pass


class _FakeRegistry:
    """Registry stub: branch_tee_generic resolves to a fixed K; everything else -> None (fallback)."""

    def resolve_type(self, library_id, type_id):
        if type_id == "branch_tee_generic":
            return _FakeTypeDef()
        return None

    def call_loss(self, library_id, type_def, context):
        return FITTING_K


@pytest.fixture(autouse=True)
def _patch_registry(monkeypatch):
    from freecad.HVAC.core import AirflowSolver as solver_mod

    monkeypatch.setattr(
        solver_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _FakeRegistry()),
    )


def _expected_segment(diameter_mm, length_mm, flow_lps):
    d_m = airflow.mm_to_m(diameter_mm)
    area = airflow.circular_area(d_m)
    dh = airflow.hydraulic_diameter_circular(d_m)
    v = airflow.velocity_from_flow(airflow.lps_to_m3s(flow_lps), area)
    re = airflow.reynolds_number(v, dh, AIR_VISCOSITY)
    rel_rough = airflow.mm_to_m(DEFAULT_ROUGHNESS_MM) / dh
    f = airflow.friction_factor_altshul_tsal(re, rel_rough)
    friction = airflow.darcy_weisbach_pressure_loss(f, airflow.mm_to_m(length_mm), dh, AIR_DENSITY, v)
    return v, re, friction


def test_flow_conservation_and_sizing():
    net, segment_map, junction_map = _base_tree()
    result = AirflowSolver(net).solve()

    assert result.warnings == []
    assert len(result.components) == 1
    comp = result.components[0]

    segA, segB, segC = (segment_map["A"], segment_map["B"], segment_map["C"])

    # Flow magnitudes: leaves specify design flow, internal/root edges solved by conservation.
    assert segC.CalcFlowRate == pytest.approx(30.0)
    assert segB.CalcFlowRate == pytest.approx(50.0)
    assert segA.CalcFlowRate == pytest.approx(80.0)  # root/balancing terminal = sum of leaves

    # Junction totals.
    j1, j2, j3, j4 = (junction_map["N1"], junction_map["N2"], junction_map["N3"], junction_map["N4"])
    assert j1.CalcTotalFlowRate == pytest.approx(80.0)
    assert j1.IsFlowSource is True  # AHU: flow leaves the system at this terminal
    assert j2.CalcTotalFlowRate == pytest.approx(80.0)
    assert j3.CalcTotalFlowRate == pytest.approx(50.0)
    assert j3.IsFlowSource is False
    assert j4.CalcTotalFlowRate == pytest.approx(30.0)

    # Per-segment velocity/Reynolds/friction match an independently computed oracle.
    vA, reA, frictionA = _expected_segment(200.0, 5000.0, 80.0)
    vB, reB, frictionB = _expected_segment(150.0, 3000.0, 50.0)
    vC, reC, frictionC = _expected_segment(150.0, 6000.0, 30.0)

    assert segA.CalcVelocity == pytest.approx(vA)
    assert segA.CalcReynoldsNumber == pytest.approx(reA)
    assert segA.CalcFrictionLoss == pytest.approx(frictionA)
    assert segB.CalcVelocity == pytest.approx(vB)
    assert segB.CalcFrictionLoss == pytest.approx(frictionB)
    assert segC.CalcVelocity == pytest.approx(vC)
    assert segC.CalcFrictionLoss == pytest.approx(frictionC)

    # Fitting loss: J2 is a resolved branch_tee_generic (K=FITTING_K), applied to its
    # two outlet ports (segB, segC) but not its inlet port (segA).
    assert segA.CalcFittingLoss == pytest.approx(0.0)
    assert segB.CalcFittingLoss == pytest.approx(FITTING_K * airflow.velocity_pressure(AIR_DENSITY, vB))
    assert segC.CalcFittingLoss == pytest.approx(FITTING_K * airflow.velocity_pressure(AIR_DENSITY, vC))
    assert j2.CalcLossWarning == ""  # a real K was found, no fallback warning

    # Pressure propagation: 0 Pa at the balancing terminal (J1), dropping downstream.
    assert j1.CalcStaticPressure == pytest.approx(0.0)
    p_j2 = -(frictionA + 0.0)
    assert j2.CalcStaticPressure == pytest.approx(p_j2)
    p_j3 = p_j2 - segB.CalcFittingLoss - frictionB
    p_j4 = p_j2 - segC.CalcFittingLoss - frictionC
    assert j3.CalcStaticPressure == pytest.approx(p_j3)
    assert j4.CalcStaticPressure == pytest.approx(p_j4)

    # Critical path: whichever terminal has the larger magnitude static pressure.
    expected_critical = "N4" if abs(p_j4) > abs(p_j3) else "N3"
    assert comp.critical_terminal_key == expected_critical
    assert comp.critical_pressure_pa == pytest.approx(max(abs(p_j3), abs(p_j4)))
    assert comp.reference_terminal_key == "N1"


def test_per_port_dict_loss_contract_attributes_distinct_coefficients(monkeypatch):
    from freecad.HVAC.core import AirflowSolver as solver_mod

    class _DictRegistry:
        def resolve_type(self, library_id, type_id):
            return _FakeTypeDef() if type_id == "branch_tee_generic" else None

        def call_loss(self, library_id, type_def, context):
            # Distinct per-edge coefficients -- exercises the new dict contract,
            # which Phase E must NOT collapse into a single uniform K like the
            # legacy float/None contract does.
            return {"B": 0.9, "C": 0.1}

    monkeypatch.setattr(
        solver_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _DictRegistry()),
    )

    net, segment_map, junction_map = _base_tree()
    result = AirflowSolver(net).solve()

    assert result.warnings == []  # a dict return is real data, not a fallback
    segB, segC = segment_map["B"], segment_map["C"]
    vB, _re, _f = _expected_segment(150.0, 3000.0, 50.0)
    vC, _re, _f = _expected_segment(150.0, 6000.0, 30.0)
    assert segB.CalcFittingLoss == pytest.approx(0.9 * airflow.velocity_pressure(AIR_DENSITY, vB))
    assert segC.CalcFittingLoss == pytest.approx(0.1 * airflow.velocity_pressure(AIR_DENSITY, vC))
    assert junction_map["N2"].CalcLossWarning == ""


def test_fallback_loss_coefficient_and_warning():
    net, segment_map, junction_map = _base_tree()
    junction_map["N2"].TypeId = "unknown_type_not_wired"
    result = AirflowSolver(net).solve()

    assert result.warnings  # fallback warning recorded globally
    assert "K={}".format(K_DEFAULT) in result.warnings[0]
    assert junction_map["N2"].CalcLossWarning != ""

    segB = segment_map["B"]
    vB, _re, _f = _expected_segment(150.0, 3000.0, 50.0)
    assert segB.CalcFittingLoss == pytest.approx(K_DEFAULT * airflow.velocity_pressure(AIR_DENSITY, vB))


def test_loop_detected_and_reported_as_warning_not_exception():
    # Two nodes joined by two parallel edges -> a 2-node loop (not a tree: 2 nodes, 2 edges).
    node_ports = {
        1: [("A", "start"), ("B", "start")],
        2: [("A", "end"), ("B", "end")],
    }
    edge_endpoints = {"A": (1, 2), "B": (1, 2)}
    parser = FakeParser(node_ports, edge_endpoints)
    segment_map = {"A": _make_segment("A", 200.0, 5000.0), "B": _make_segment("B", 200.0, 5000.0)}
    junction_map = {
        "N1": _make_junction("J1", type_id="end_terminal_marker"),
        "N2": _make_junction("J2", type_id="end_terminal_marker"),
    }
    net = _make_net(parser, segment_map, junction_map)

    result = AirflowSolver(net).solve()

    assert result.components == []
    assert len(result.warnings) == 1
    assert "Loop detected" in result.warnings[0]


def test_all_terminals_specified_is_an_error():
    net, segment_map, junction_map = _base_tree()
    junction_map["N1"].DesignFlowRate = 80.0  # no terminal left unspecified

    result = AirflowSolver(net).solve()

    assert result.components == []
    assert len(result.warnings) == 1
    assert "Design Flow Rate" in result.warnings[0]


def test_missing_duct_size_is_an_error():
    net, segment_map, junction_map = _base_tree()
    segment_map["A"].Diameter = 0.0

    result = AirflowSolver(net).solve()

    assert result.components == []
    assert len(result.warnings) == 1
    assert "Diameter" in result.warnings[0]
