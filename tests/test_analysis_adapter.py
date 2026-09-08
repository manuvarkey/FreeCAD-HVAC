"""Focused tests for construction-derived analysis inputs and diagnostic labels."""

from types import SimpleNamespace

import conftest  # noqa: F401 -- installs FreeCAD/Materials stubs

from freecad.HVAC.core import _analysis_adapter


def test_humanize_diagnostics_prefers_number_then_label():
    segments = {
        "edge:internal": SimpleNamespace(Number="D012", Label="Supply duct", Name="Segment001"),
    }
    junctions = {
        "node:internal": SimpleNamespace(Number="", Label="Main tee", Name="Junction001"),
    }

    messages = _analysis_adapter.humanize_diagnostics(
        ["Segment 'edge:internal' meets node 'node:internal'."], segments, junctions
    )

    assert messages == ["Segment 'D012' meets node 'Main tee'."]
    assert "internal" not in messages[0]


def test_element_identifier_falls_back_to_internal_name():
    obj = SimpleNamespace(Number="", Label="", Name="Segment001")
    assert _analysis_adapter.element_identifier(obj) == "Segment001"


def test_segment_model_uses_construction_roughness_and_ignores_legacy_override(monkeypatch):
    segment = SimpleNamespace(
        Profile="Circular", Diameter=200.0, Roughness=9.9,
        SegmentKey="edge", Name="Segment001", EffectiveLength=1000.0,
        Velocity=0.0, RectangularSizingMode="UseNetworkDefault",
        TargetAspectRatio=0.0,
    )
    construction = SimpleNamespace(hydraulic_roughness=lambda default: 0.15)
    monkeypatch.setattr(_analysis_adapter, "construction_for", lambda obj: construction)

    model = _analysis_adapter._build_segment_model(segment, default_roughness_mm=0.09)

    assert model.roughness_mm == 0.15


def test_segment_model_uses_network_roughness_only_as_construction_fallback(monkeypatch):
    segment = SimpleNamespace(
        Profile="Circular", Diameter=200.0, Roughness=9.9,
        SegmentKey="edge", Name="Segment001", EffectiveLength=1000.0,
        Velocity=0.0, RectangularSizingMode="UseNetworkDefault",
        TargetAspectRatio=0.0,
    )
    construction = SimpleNamespace(hydraulic_roughness=lambda default: default)
    monkeypatch.setattr(_analysis_adapter, "construction_for", lambda obj: construction)

    model = _analysis_adapter._build_segment_model(segment, default_roughness_mm=0.09)

    assert model.roughness_mm == 0.09


def test_component_models_use_their_own_construction_roughness(monkeypatch):
    primary = SimpleNamespace(Name="Primary", LocalPortsJson="[]", LibraryId="", TypeId="")
    inline = SimpleNamespace(Name="Inline", LocalPortsJson="[]", LibraryId="", TypeId="")
    fallback = SimpleNamespace(Name="Fallback", LocalPortsJson="[]", LibraryId="", TypeId="")
    junction = SimpleNamespace(
        DesignFlowRate=0.0,
        Family="through",
        Proxy=SimpleNamespace(
            getPrimaryComponent=lambda: primary,
            getPortChains=lambda: {"edge": [inline, fallback]},
        ),
    )
    analysis = SimpleNamespace(
        connected_ports=[], topology="through", degree=2,
        flow_class="unknown", qualifiers={}, derived_values={},
    )
    registry = SimpleNamespace(resolve_type=lambda library_id, type_id: None)

    roughness = {"Primary": 0.12, "Inline": 0.24}
    monkeypatch.setattr(
        _analysis_adapter,
        "construction_for",
        lambda obj: SimpleNamespace(
            hydraulic_roughness=lambda default: roughness.get(obj.Name, default)
        ),
    )

    model = _analysis_adapter._build_node_model(
        "node", junction, analysis, {}, registry,
        SimpleNamespace(), {}, default_roughness_mm=0.09,
    )

    assert model.primary_component.roughness_mm == 0.12
    assert model.inline_chains["edge"][0].roughness_mm == 0.24
    assert model.inline_chains["edge"][1].roughness_mm == 0.09


def test_node_model_reads_flow_boundary_off_the_junction(monkeypatch):
    """
    NodeModel.flow_boundary must mirror DuctJunction.FlowBoundary exactly
    (defaulting to "Auto" if the property is missing, e.g. a non-terminal
    node or an older test double) -- see analysis/flow.py for how this
    tri-state replaces the old "DesignFlowRate == 0 means unset" heuristic.
    """
    monkeypatch.setattr(
        _analysis_adapter, "construction_for", lambda obj: SimpleNamespace(hydraulic_roughness=lambda default: default)
    )
    analysis = SimpleNamespace(
        connected_ports=[], topology="end", degree=1,
        flow_class="unknown", qualifiers={}, derived_values={},
    )
    registry = SimpleNamespace(resolve_type=lambda library_id, type_id: None)

    closed_junction = SimpleNamespace(DesignFlowRate=999.0, FlowBoundary="Closed", Family="", Proxy=SimpleNamespace(
        getPrimaryComponent=lambda: None, getPortChains=lambda: {},
    ))
    model = _analysis_adapter._build_node_model(
        "node", closed_junction, analysis, {}, registry, SimpleNamespace(), {}, default_roughness_mm=0.09,
    )
    assert model.flow_boundary == "Closed"
    assert model.design_flow_lps == 999.0  # preserved on the model -- flow.py is what ignores it for Closed

    no_boundary_junction = SimpleNamespace(DesignFlowRate=0.0, Family="", Proxy=SimpleNamespace(
        getPrimaryComponent=lambda: None, getPortChains=lambda: {},
    ))
    model = _analysis_adapter._build_node_model(
        "node", no_boundary_junction, analysis, {}, registry, SimpleNamespace(), {}, default_roughness_mm=0.09,
    )
    assert model.flow_boundary == "Auto"


def test_loss_evaluator_exposes_component_construction_and_roughness():
    captured = {}
    type_def = SimpleNamespace(properties=[])

    class Registry:
        @staticmethod
        def resolve_type(library_id, type_id):
            return type_def

        @staticmethod
        def call_loss(library_id, resolved_type, context):
            captured.update(context)
            return 0.3

    component = SimpleNamespace(
        LibraryId="smacna", TypeId="through_elbow_rectangular", LocalPortsJson="[]"
    )
    air = SimpleNamespace(density_kg_m3=1.204, kinematic_viscosity_m2_s=1.51e-5)
    construction = object()
    evaluator = _analysis_adapter.build_loss_evaluator(
        Registry(), component, air,
        construction=construction,
        hydraulic_roughness_mm=0.12,
    )

    assert evaluator({}) == 0.3
    assert captured["construction"] is construction
    assert captured["hydraulic_roughness_mm"] == 0.12
