import math
import os

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from conftest import FakeVector

from freecad.HVAC.library import partscript_shapes

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "freecad", "HVAC", "libraries", "smacna",
    "models", "rectangular_elbow.py",
)


class _Shape:
    def __init__(self, tag):
        self.tag = tag

    def isNull(self):
        return False

    def cut(self, other):
        return _Shape("cut({},{})".format(self.tag, other.tag))

    def extrude(self, vec):
        return _Shape("extrude({},{})".format(self.tag, vec))


class _FakeApi:
    pipe_shell_calls = []
    face_calls = []

    @staticmethod
    def port_position(port):
        return FakeVector(port["position"])

    @staticmethod
    def port_direction(port):
        return FakeVector(port["direction"]).normalize()

    @staticmethod
    def port_profile(port):
        return port.get("profile", "")

    @staticmethod
    def port_section_params(port):
        return dict(port.get("section_params", {}) or {})

    @staticmethod
    def port_profile_x_axis(port):
        return port.get("profile_x_axis")

    @staticmethod
    def port_width(port):
        return float(_FakeApi.port_section_params(port).get("Width", 0.0) or 0.0)

    @staticmethod
    def port_height(port):
        return float(_FakeApi.port_section_params(port).get("Height", 0.0) or 0.0)

    @staticmethod
    def copy_port(port, position=None, direction=None, profile_x_axis=None):
        out = dict(port)
        if position is not None:
            out["position"] = position
        if direction is not None:
            out["direction"] = direction
        if profile_x_axis is not None:
            out["profile_x_axis"] = profile_x_axis
        return out

    @staticmethod
    def unit(v):
        return v.normalize()

    @staticmethod
    def angle_between(u0, u1):
        dot = max(-1.0, min(1.0, u0.dot(u1)))
        return math.acos(dot)

    @staticmethod
    def closest_points_on_lines(p0, d0, p1, d1):
        # Trivial stand-in -- the exact tangent-point math is HVACLibraryAPI's
        # own concern (covered elsewhere), not this model's.
        return p0, p1

    @staticmethod
    def arc_center_from_points_tangents_radius(s0, s1, u0, u1, radius):
        return (s0 + s1) * 0.5

    @staticmethod
    def make_profile_frame(direction, preferred_x=None, origin=None):
        # Simplified, deterministic stand-in (real projection math lives in
        # hvaclib.make_profile_frame and isn't this model's concern) -- fixed
        # axes so d_h_axis_02/d_v_axis_02 are predictable in the assertions.
        return None, FakeVector(0.0, 1.0, 0.0), FakeVector(0.0, 0.0, 1.0), direction

    @staticmethod
    def make_section_wire_from_port(port):
        params = _FakeApi.port_section_params(port)
        return "wire:{}:{}".format(port["profile"], sorted(params.items()))

    @staticmethod
    def make_pipe_shell(spine_wire, profile_wires, make_solid=True, is_frenet=False):
        _FakeApi.pipe_shell_calls.append(list(profile_wires))
        return _Shape("sweep({})".format(",".join(profile_wires)))

    @staticmethod
    def make_section_face(profile, section_params, center, direction, profile_x_axis=None):
        _FakeApi.face_calls.append((center, direction, sorted(section_params.items())))
        return _Shape("face:{}".format(sorted(section_params.items())))

    @staticmethod
    def fuse_shapes(shapes):
        return _Shape("fuse({})".format(len(shapes)))

    @staticmethod
    def build_trim_rec_from_port_lengths(port_lengths):
        return [
            {"edge_key": p.get("edge_key"), "segment_end": p.get("segment_end"), "length": length}
            for p, length in port_lengths
        ]


def _ports():
    port0 = {
        "id": "P0",
        "position": (0.0, 0.0, 0.0),
        "direction": (1.0, 0.0, 0.0),
        "profile": "Rectangular",
        "section_params": {"Width": 300.0, "Height": 200.0},
        "edge_key": "E0",
        "segment_end": "start",
    }
    port1 = {
        "id": "P1",
        "position": (500.0, 500.0, 0.0),
        "direction": (0.0, 1.0, 0.0),
        "profile": "Rectangular",
        "section_params": {"Width": 250.0, "Height": 180.0},
        "edge_key": "E1",
        "segment_end": "end",
    }
    return [port0, port1]


def _context(params):
    _FakeApi.pipe_shell_calls = []
    _FakeApi.face_calls = []
    return {"hvac_api": _FakeApi, "connected_ports": _ports(), "params": params}


def test_generate_hollows_with_default_thickness():
    result = partscript_shapes.execute_partscript(MODEL_PATH, _context({"ShowFlange1": False, "ShowFlange2": False}))

    assert result["shape"].tag.startswith("cut(sweep(")
    assert len(_FakeApi.pipe_shell_calls) == 2
    assert _FakeApi.face_calls == []

    outer_wires, inner_wires = _FakeApi.pipe_shell_calls
    assert "Width', 300.0" in outer_wires[0]
    assert "Height', 200.0" in outer_wires[0]
    assert "Width', 250.0" in outer_wires[1]
    # Default thickness = 0.8 -> shrink by 2*0.8 = 1.6
    assert "Width', 298.4" in inner_wires[0]
    assert "Height', 198.4" in inner_wires[0]
    assert "Width', 248.4" in inner_wires[1]


def test_generate_includes_both_flanges_by_default():
    result = partscript_shapes.execute_partscript(MODEL_PATH, _context({}))
    assert result["shape"].tag == "fuse(3)"
    assert len(_FakeApi.face_calls) == 4  # flange1 outer+inner, flange2 outer+inner

    # Flange1 uses side1's own (outer) size + 2*flange_height (default 25.0)
    assert dict(_FakeApi.face_calls[0][2]) == {"Width": 350.0, "Height": 250.0}
    assert dict(_FakeApi.face_calls[1][2]) == {"Width": 300.0, "Height": 200.0}
    # Flange2 uses side2's own size
    assert dict(_FakeApi.face_calls[2][2]) == {"Width": 300.0, "Height": 230.0}
    assert dict(_FakeApi.face_calls[3][2]) == {"Width": 250.0, "Height": 180.0}

    # Both flanges extrude towards the elbow's own interior: port_direction()
    # points *away* from the junction (u0=(1,0,0), u1=(0,1,0) in this
    # fixture), so "into the elbow" is -u0 / -u1 at each tangent plane.
    assert _FakeApi.face_calls[0][1] == FakeVector(-1.0, 0.0, 0.0)
    assert _FakeApi.face_calls[2][1] == FakeVector(0.0, -1.0, 0.0)


def test_generate_omits_flanges_when_disabled():
    result = partscript_shapes.execute_partscript(
        MODEL_PATH, _context({"ShowFlange1": False, "ShowFlange2": False})
    )
    assert result["shape"].tag.startswith("cut(sweep(")
    assert _FakeApi.face_calls == []


def test_generate_omits_only_start_flange():
    result = partscript_shapes.execute_partscript(MODEL_PATH, _context({"ShowFlange1": False}))
    assert result["shape"].tag == "fuse(2)"
    assert len(_FakeApi.face_calls) == 2  # only flange2's outer+inner
    assert dict(_FakeApi.face_calls[0][2]) == {"Width": 300.0, "Height": 230.0}


def test_generate_reports_reactive_computed_properties():
    result = partscript_shapes.execute_partscript(MODEL_PATH, _context({}))
    computed = result["computed_properties"]

    assert set(computed.keys()) == {"d_h_axis_02", "d_v_axis_02", "angle"}
    # theta = angle_between((1,0,0), (0,1,0)) = 90 deg -> bend angle = 180-90 = 90 deg
    assert computed["angle"] == pytest.approx(90.0)
    # offset_vec = c2 - c1 = (500,500,0) - (0,0,0) = (500,500,0);
    # fake frame axes are fixed to (0,1,0)/(0,0,1) -> dot products are y/z.
    assert computed["d_h_axis_02"] == pytest.approx(500.0)
    assert computed["d_v_axis_02"] == pytest.approx(0.0)


def test_generate_connection_lengths_from_tangent_trim():
    result = partscript_shapes.execute_partscript(MODEL_PATH, _context({}))
    lengths = {rec["edge_key"]: rec["length"] for rec in result["connection_lengths"]}
    # size_hint = max(300,200,250,180,1) = 300 -> default radius = 0.6*300 = 180
    # trim = radius / tan(theta/2) = 180 / tan(45deg) = 180
    assert lengths["E0"] == pytest.approx(180.0)
    assert lengths["E1"] == pytest.approx(180.0)


def test_generate_rejects_thickness_too_large():
    try:
        partscript_shapes.execute_partscript(MODEL_PATH, _context({"thickness": 200.0}))
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("Expected ValueError for oversized thickness")


def test_generate_rejects_non_rectangular_port():
    ctx = _context({})
    ctx["connected_ports"][1]["profile"] = "Circular"
    try:
        partscript_shapes.execute_partscript(MODEL_PATH, ctx)
    except ValueError as exc:
        assert "Rectangular" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-Rectangular port")
