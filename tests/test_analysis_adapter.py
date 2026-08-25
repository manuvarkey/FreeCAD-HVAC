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


def test_segment_model_uses_construction_roughness_before_network_default(monkeypatch):
    segment = SimpleNamespace(
        Profile="Circular", Diameter=200.0, Roughness=0.0,
        SegmentKey="edge", Name="Segment001", EffectiveLength=1000.0,
        Velocity=0.0, RectangularSizingMode="UseNetworkDefault",
        TargetAspectRatio=0.0,
    )
    construction = SimpleNamespace(hydraulic_roughness=lambda default: 0.15)
    monkeypatch.setattr(_analysis_adapter, "construction_for", lambda obj: construction)

    model = _analysis_adapter._build_segment_model(segment, default_roughness_mm=0.09)

    assert model.roughness_mm == 0.15
