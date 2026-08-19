import os

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from conftest import FakeVector

from freecad.HVAC.library import partscript_shapes

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "freecad", "HVAC", "libraries", "smacna",
    "models", "oval_straight.py",
)


class _FakeShape:
    def __init__(self, tag):
        self.tag = tag

    def isNull(self):
        return False

    def cut(self, other):
        return _FakeShape("cut({},{})".format(self.tag, other.tag))


class _FakeApi:
    @staticmethod
    def vec(v):
        return FakeVector(v)

    @staticmethod
    def unit(v):
        return v.normalize()

    @staticmethod
    def make_straight_shape(start_point, end_point, profile, section_params, profile_x_axis=None):
        return _FakeShape("{}:{}".format(profile, sorted(section_params.items())))


def _context(params, sp=(0.0, 0.0, 0.0), ep=(0.0, 0.0, 1000.0)):
    return {"hvac_api": _FakeApi, "start_point": sp, "end_point": ep, "params": params}


def test_generate_cuts_inner_oval_from_outer():
    result = partscript_shapes.execute_partscript(
        MODEL_PATH, _context({"Width": 200.0, "Height": 100.0})
    )
    tag = result["shape"].tag
    assert "cut(" in tag
    assert "Width', 200.0" in tag
    # Thickness defaults to 0.8 -> inner Width/Height shrink by 2*0.8 = 1.6
    assert "Width', 198.4" in tag
    assert "Height', 98.4" in tag


def test_generate_rejects_thickness_too_large_for_dimensions():
    try:
        partscript_shapes.execute_partscript(
            MODEL_PATH, _context({"Width": 20.0, "Height": 10.0, "Thickness": 10.0})
        )
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("Expected ValueError for oversized Thickness")


def test_generate_has_no_flange_geometry():
    # This model never reads any Show/Flange* param -- it's a hollow tube only.
    result = partscript_shapes.execute_partscript(
        MODEL_PATH,
        _context({"Width": 200.0, "Height": 100.0, "ShowFlange1": True, "ShowFlange2": True}),
    )
    assert "cut(" in result["shape"].tag
