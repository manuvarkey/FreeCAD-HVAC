import os
from unittest.mock import MagicMock

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from conftest import FakeVector

from freecad.HVAC.library import partscript_shapes

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "freecad", "HVAC", "libraries", "smacna",
    "models", "circular_straight.py",
)


class _FusedShape:
    def __init__(self, parts):
        self.parts = list(parts)

    def isNull(self):
        return False


class _FakeApi:
    @staticmethod
    def vec(v):
        return FakeVector(v)

    @staticmethod
    def unit(v):
        return v.normalize()

    @staticmethod
    def fuse_shapes(shapes):
        return _FusedShape(shapes)


def _context(params, sp=(0.0, 0.0, 0.0), ep=(0.0, 0.0, 500.0)):
    return {"hvac_api": _FakeApi, "start_point": sp, "end_point": ep, "params": params}


def _patch_make_cylinder(monkeypatch):
    import Part
    calls = []

    def fake_make_cylinder(radius, height, pnt, direction):
        calls.append((radius, height, pnt, direction))
        return MagicMock(name="cyl-{}".format(len(calls)))

    monkeypatch.setattr(Part, "makeCylinder", fake_make_cylinder)
    return calls


def test_generate_builds_tube_and_both_flanges_by_default(monkeypatch):
    calls = _patch_make_cylinder(monkeypatch)

    result = partscript_shapes.execute_partscript(
        MODEL_PATH, _context({"Diameter": 250.0})
    )

    # tube (outer + inner) + flange1 (outer + inner) + flange2 (outer + inner)
    assert len(calls) == 6
    assert calls[0][0] == pytest.approx(125.0)          # duct outer radius
    assert calls[0][1] == pytest.approx(500.0)           # duct length
    assert calls[1][0] == pytest.approx(124.2)           # duct inner radius (Thickness=0.8)
    assert calls[2][0] == pytest.approx(150.0)           # flange1 outer radius (+FlangeHeight=25)
    assert calls[2][1] == pytest.approx(1.0)              # flange1 thickness
    assert calls[3][0] == pytest.approx(125.0)           # flange1 inner radius == duct outer
    assert calls[4][0] == pytest.approx(150.0)           # flange2 outer radius
    assert calls[5][0] == pytest.approx(125.0)           # flange2 inner radius

    # Flanges extrude inward from each port, into the duct's own length --
    # flange1 (at start) points the same way as the tube; flange2 (at end)
    # points back against the tube's direction. Neither protrudes past the
    # segment's start/end points into the neighboring segment/junction.
    assert calls[2][2] == FakeVector(0.0, 0.0, 0.0)   # flange1 anchored at start
    assert calls[2][3] == FakeVector(0.0, 0.0, 1.0)   # flange1 points into the duct (+direction)
    assert calls[4][2] == FakeVector(0.0, 0.0, 500.0)  # flange2 anchored at end
    assert calls[4][3] == FakeVector(0.0, 0.0, -1.0)  # flange2 points into the duct (-direction)

    assert result["shape"].parts and len(result["shape"].parts) == 3


def test_generate_omits_flanges_when_disabled(monkeypatch):
    calls = _patch_make_cylinder(monkeypatch)

    result = partscript_shapes.execute_partscript(
        MODEL_PATH,
        _context({"Diameter": 250.0, "ShowFlange1": False, "ShowFlange2": False}),
    )

    assert len(calls) == 2  # tube outer + inner only
    assert len(result["shape"].parts) == 1


def test_generate_omits_only_start_flange(monkeypatch):
    _patch_make_cylinder(monkeypatch)

    result = partscript_shapes.execute_partscript(
        MODEL_PATH, _context({"Diameter": 250.0, "ShowFlange1": False})
    )

    assert len(result["shape"].parts) == 2


def test_generate_rejects_thickness_too_large_for_diameter(monkeypatch):
    _patch_make_cylinder(monkeypatch)

    try:
        partscript_shapes.execute_partscript(
            MODEL_PATH, _context({"Diameter": 10.0, "Thickness": 10.0})
        )
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("Expected ValueError for oversized Thickness")


def test_generate_rejects_zero_length():
    result_context = _context({"Diameter": 250.0}, sp=(0.0, 0.0, 0.0), ep=(0.0, 0.0, 0.0))
    try:
        partscript_shapes.execute_partscript(MODEL_PATH, result_context)
    except ValueError as exc:
        assert "length" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for zero-length segment")
