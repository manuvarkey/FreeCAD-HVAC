# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Property/port handling shared by the fitting modules in library/fittings/:
which property a port's trim and flange switch come from, and how a layer's
thickness is read. No shape construction is exercised here.
"""

from types import SimpleNamespace

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from freecad.HVAC.library.fittings import _common, stars
from freecad.HVAC.library.library_api import HVACLibraryAPI


def _port(edge_key, direction):
    return {
        "edge_key": edge_key,
        "position": [0.0, 0.0, 0.0],
        "direction": list(direction),
        "profile": "Circular",
        "section_params": {"Diameter": 200.0},
        "profile_x_axis": None,
        "segment_end": "start",
    }


def _tee_context(params):
    # Port order on purpose is not run, run, branch: the branch comes first.
    ports = [_port("C", (0.0, 1.0, 0.0)), _port("A", (-1.0, 0.0, 0.0)), _port("B", (1.0, 0.0, 0.0))]
    return {
        "hvac_api": HVACLibraryAPI,
        "params": params,
        "connected_ports": ports,
        "center_point": (0.0, 0.0, 0.0),
        "analysis": {"collinear_pairs": [{"a": 1, "b": 2}]},
    }


def test_layer_thickness_reads_the_property_the_layer_names():
    layer = SimpleNamespace(thickness_property="Thickness")
    assert _common._layer_thickness(layer, {"Thickness": 1.2}) == 1.2
    assert _common._layer_thickness(layer, {}) == 0.0
    assert _common._layer_thickness(SimpleNamespace(thickness_property=None), {"Thickness": 1.2}) == 0.0


def test_flange_switch_prefers_role_then_number_then_shared():
    params = {"ShowFlangeBranch": False, "ShowFlange2": True, "ShowFlange": True}
    assert _common._flange_enabled(params, 1, "Branch") is False
    assert _common._flange_enabled({"ShowFlange2": False, "ShowFlange": True}, 1, "Branch") is False
    assert _common._flange_enabled({"ShowFlange": False}, 1, "Branch") is False


def test_flange_switch_defaults_to_on_when_nothing_is_declared():
    assert _common._flange_enabled({}, 0, None) is True
    assert _common._flange_enabled({"ShowFlangeRunA": False}, 0, "RunB") is True


def test_star_tee_uses_role_named_trims_matched_to_the_run_pair():
    context = _tee_context({"TrimRunA": 110.0, "TrimRunB": 120.0, "TrimBranch": 130.0})
    records = stars.measure_branch_generic(context)
    assert {rec["edge_key"]: rec["length"] for rec in records} == {"A": 110.0, "B": 120.0, "C": 130.0}


def test_star_tee_without_role_names_uses_port_letters_in_port_order():
    context = _tee_context({"TrimPortA": 110.0, "TrimPortB": 120.0, "TrimPortC": 130.0})
    records = stars.measure_branch_generic(context)
    assert {rec["edge_key"]: rec["length"] for rec in records} == {"C": 110.0, "A": 120.0, "B": 130.0}


def test_star_layout_reports_each_ports_role_for_flange_switches():
    context = _tee_context({"TrimRunA": 110.0, "TrimRunB": 120.0, "TrimBranch": 130.0})
    _ports, _center, _trims, suffixes = stars._star_junction_layout(context, 0.6, align_branch=True)
    assert suffixes == {"A": "RunA", "B": "RunB", "C": "Branch"}
