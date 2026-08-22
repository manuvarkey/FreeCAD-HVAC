"""
Tests for core/_geometry_apply.py's apply_geometry_result(): the one piece
of DuctSegment.execute()/DuctComponent.execute() that writes a GeometryResult
onto CasingShape/InsulationShape and derives Shape as their compound --
shared so the two execute() methods don't duplicate it.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core import _geometry_apply
from freecad.HVAC.library import geometry_result as gr


class _Shape:
    def __init__(self, null=False):
        self._null = null

    def isNull(self):
        return self._null


class _FakeObj:
    def __init__(self):
        self.CasingShape = None
        self.InsulationShape = None
        self.Shape = None


def test_apply_geometry_result_sets_casing_and_insulation_shapes():
    casing_shape = _Shape()
    insulation_shape = _Shape()
    result = gr.GeometryResult(components={
        "casing": gr.ComponentGeometry(shape=casing_shape, material_role="casing"),
        "insulation": gr.ComponentGeometry(shape=insulation_shape, material_role="insulation"),
    })

    obj = _FakeObj()
    _geometry_apply.apply_geometry_result(obj, result)

    assert obj.CasingShape is casing_shape
    assert obj.InsulationShape is insulation_shape


def test_apply_geometry_result_builds_shape_as_compound_of_non_null_shapes(monkeypatch):
    casing_shape = _Shape()
    result = gr.GeometryResult(components={
        "casing": gr.ComponentGeometry(shape=casing_shape, material_role="casing"),
        "insulation": gr.ComponentGeometry(shape=None, material_role="insulation"),
    })

    captured = {}

    def fake_make_compound(shapes):
        captured["shapes"] = list(shapes)
        return "COMPOUND"

    monkeypatch.setattr(_geometry_apply.Part, "makeCompound", fake_make_compound)

    obj = _FakeObj()
    _geometry_apply.apply_geometry_result(obj, result)

    # Only the non-null casing shape goes into the compound -- insulation is
    # absent (None), not a stand-in empty shape.
    assert captured["shapes"] == [casing_shape]
    assert obj.Shape == "COMPOUND"


def test_apply_geometry_result_treats_null_shape_as_absent(monkeypatch):
    null_shape = _Shape(null=True)
    result = gr.GeometryResult(components={
        "casing": gr.ComponentGeometry(shape=null_shape, material_role="casing"),
        "insulation": gr.ComponentGeometry(shape=None, material_role="insulation"),
    })

    captured = {}
    monkeypatch.setattr(
        _geometry_apply.Part, "makeCompound", lambda shapes: captured.setdefault("shapes", list(shapes))
    )

    obj = _FakeObj()
    _geometry_apply.apply_geometry_result(obj, result)

    assert captured["shapes"] == []


def test_apply_geometry_result_defaults_missing_shapes_to_empty_part_shape(monkeypatch):
    result = gr.GeometryResult(components={
        "casing": gr.ComponentGeometry(shape=None, material_role="casing"),
        "insulation": gr.ComponentGeometry(shape=None, material_role="insulation"),
    })

    monkeypatch.setattr(_geometry_apply.Part, "Shape", lambda: "EMPTY_SHAPE")
    monkeypatch.setattr(_geometry_apply.Part, "makeCompound", lambda shapes: list(shapes))

    obj = _FakeObj()
    _geometry_apply.apply_geometry_result(obj, result)

    assert obj.CasingShape == "EMPTY_SHAPE"
    assert obj.InsulationShape == "EMPTY_SHAPE"
    assert obj.Shape == []
