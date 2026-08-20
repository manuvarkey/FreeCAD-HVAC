import math

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from conftest import FakeVector

from freecad.HVAC.libraries.smacna.generators import junctions as smacna_junctions


class _Shape:
    def __init__(self, tag):
        self.tag = tag

    def cut(self, other):
        return _Shape("cut({},{})".format(self.tag, other.tag))


class _FakeApi:
    pipe_shell_calls = []

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
    def port_diameter(port):
        return float(_FakeApi.port_section_params(port).get("Diameter", 0.0) or 0.0)

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
    def angle_between(u0, u1):
        dot = max(-1.0, min(1.0, u0.dot(u1)))
        return math.acos(dot)

    @staticmethod
    def closest_points_on_lines(p0, d0, p1, d1):
        # Trivial stand-in -- build_elbow only needs *some* pair of points on
        # the two centerlines, the exact tangent-point math is HVACLibraryAPI's
        # own concern (tested elsewhere), not build_elbow's.
        return p0, p1

    @staticmethod
    def arc_center_from_points_tangents_radius(s0, s1, u0, u1, radius):
        return (s0 + s1) * 0.5

    @staticmethod
    def make_section_wire_from_port(port):
        params = _FakeApi.port_section_params(port)
        return "wire:{}:{}".format(port["profile"], sorted(params.items()))

    @staticmethod
    def make_pipe_shell(spine_wire, profile_wires, make_solid=True, is_frenet=False):
        _FakeApi.pipe_shell_calls.append(list(profile_wires))
        return _Shape("sweep({})".format(",".join(profile_wires)))

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
        "profile": "Circular",
        "section_params": {"Diameter": 300.0},
        "edge_key": "E0",
        "segment_end": "start",
    }
    port1 = {
        "id": "P1",
        "position": (500.0, 500.0, 0.0),
        "direction": (0.0, 1.0, 0.0),
        "profile": "Circular",
        "section_params": {"Diameter": 300.0},
        "edge_key": "E1",
        "segment_end": "end",
    }
    return [port0, port1]


def _context(props):
    return {"hvac_api": _FakeApi, "connected_ports": _ports(), "properties": props}


def test_build_elbow_hollows_with_default_thickness():
    _FakeApi.pipe_shell_calls = []
    result = smacna_junctions.build_elbow(_context({}))

    assert result["shape"].tag.startswith("cut(sweep(")
    assert len(_FakeApi.pipe_shell_calls) == 2

    outer_wires, inner_wires = _FakeApi.pipe_shell_calls
    assert "Diameter', 300.0" in outer_wires[0]
    assert "Diameter', 300.0" in outer_wires[1]
    # Default Thickness = 0.8 -> inner diameter shrinks by 2*0.8 = 1.6
    assert "Diameter', 298.4" in inner_wires[0]
    assert "Diameter', 298.4" in inner_wires[1]


def test_build_elbow_uses_custom_thickness():
    _FakeApi.pipe_shell_calls = []
    smacna_junctions.build_elbow(_context({"Thickness": 50.0}))

    _outer_wires, inner_wires = _FakeApi.pipe_shell_calls
    # Diameter 300 - 2*50 = 200
    assert "Diameter', 200.0" in inner_wires[0]
    assert "Diameter', 200.0" in inner_wires[1]


def test_build_elbow_connection_lengths_unaffected_by_thickness():
    result = smacna_junctions.build_elbow(_context({"Thickness": 50.0}))
    lengths = {rec["edge_key"]: rec["length"] for rec in result["connection_lengths"]}
    assert lengths["E0"] == pytest.approx(180.0)
    assert lengths["E1"] == pytest.approx(180.0)


def test_build_elbow_rejects_thickness_too_large_for_diameter():
    try:
        smacna_junctions.build_elbow(_context({"Thickness": 200.0}))
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("Expected ValueError for oversized Thickness")


def test_inset_port_shrinks_circular_section():
    port = {"profile": "Circular", "section_params": {"Diameter": 300.0}}
    inset = smacna_junctions._inset_port(_FakeApi, port, 10.0)
    assert inset["section_params"]["Diameter"] == 280.0


def test_inset_port_shrinks_rectangular_section():
    port = {"profile": "Rectangular", "section_params": {"Width": 400.0, "Height": 200.0}}
    inset = smacna_junctions._inset_port(_FakeApi, port, 10.0)
    assert inset["section_params"] == {"Width": 380.0, "Height": 180.0}


def test_inset_port_rejects_thickness_too_large():
    port = {"profile": "Circular", "section_params": {"Diameter": 10.0}}
    try:
        smacna_junctions._inset_port(_FakeApi, port, 10.0)
    except ValueError as exc:
        assert "Thickness" in str(exc)
    else:
        raise AssertionError("Expected ValueError for oversized Thickness")
