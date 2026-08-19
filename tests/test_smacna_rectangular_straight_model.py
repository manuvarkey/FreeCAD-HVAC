import os

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from conftest import FakeVector

from freecad.HVAC.library import partscript_shapes

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "freecad", "HVAC", "libraries", "smacna",
    "models", "rectangular_straight.py",
)


class _FakeShape:
    def __init__(self, tag):
        self.tag = tag

    def isNull(self):
        return False

    def cut(self, other):
        return _FakeShape("cut({},{})".format(self.tag, other.tag))

    def extrude(self, vec):
        return _FakeShape("extrude({},{})".format(self.tag, vec))


class _FakeApi:
    calls = []

    @staticmethod
    def vec(v):
        return FakeVector(v)

    @staticmethod
    def unit(v):
        return v.normalize()

    @staticmethod
    def make_straight_shape(start_point, end_point, profile, section_params, profile_x_axis=None):
        return _FakeShape("tube:{}:{}".format(profile, sorted(section_params.items())))

    @staticmethod
    def make_section_face(profile, section_params, center, direction, profile_x_axis=None):
        _FakeApi.calls.append((center, direction, sorted(section_params.items())))
        return _FakeShape("face:{}".format(sorted(section_params.items())))

    @staticmethod
    def fuse_shapes(shapes):
        return _FakeShape("fuse({})".format(len(shapes)))


def _context(params, sp=(0.0, 0.0, 0.0), ep=(0.0, 0.0, 1000.0)):
    return {"hvac_api": _FakeApi, "start_point": sp, "end_point": ep, "params": params}


def test_generate_builds_tube_and_both_flanges_by_default():
    _FakeApi.calls = []
    result = partscript_shapes.execute_partscript(
        MODEL_PATH, _context({"Width": 300.0, "Height": 150.0})
    )
    assert result["shape"].tag == "fuse(3)"

    # Flange1 anchored at start, pointing into the duct (+direction);
    # Flange2 anchored at end, pointing into the duct (-direction) -- neither
    # protrudes past the segment's own start/end points.
    face_calls = _FakeApi.calls
    assert len(face_calls) == 4  # flange1 outer+inner, flange2 outer+inner
    assert face_calls[0][0] == FakeVector(0.0, 0.0, 0.0)
    assert face_calls[0][1] == FakeVector(0.0, 0.0, 1.0)
    assert face_calls[2][0] == FakeVector(0.0, 0.0, 1000.0)
    assert face_calls[2][1] == FakeVector(0.0, 0.0, -1.0)

    # Flange outer/inner section sizes
    assert dict(face_calls[0][2]) == {"Width": 350.0, "Height": 200.0}  # +2*FlangeHeight(25)
    assert dict(face_calls[1][2]) == {"Width": 300.0, "Height": 150.0}


def test_generate_omits_flanges_when_disabled():
    _FakeApi.calls = []
    result = partscript_shapes.execute_partscript(
        MODEL_PATH,
        _context({"Width": 300.0, "Height": 150.0, "ShowFlange1": False, "ShowFlange2": False}),
    )
    assert result["shape"].tag == "fuse(1)"
    assert _FakeApi.calls == []


def test_generate_rejects_thickness_too_large_for_dimensions():
    try:
        partscript_shapes.execute_partscript(
            MODEL_PATH, _context({"Width": 20.0, "Height": 10.0, "Thickness": 10.0})
        )
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("Expected ValueError for oversized Thickness")
