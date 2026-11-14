# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Radius wye: the rule that refuses a wall where the split of the main section
crosses a curved side (checked before any geometry is built).
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from freecad.HVAC.library.fittings import wyes
from freecad.HVAC.library.library_api import HVACLibraryAPI


def _port(profile, **section):
    return {
        "edge_key": "E",
        "position": [0.0, 0.0, 0.0],
        "direction": [1.0, 0.0, 0.0],
        "profile": profile,
        "section_params": section,
        "profile_x_axis": None,
    }


def _crosses(main, branch_a, branch_b):
    return wyes._wye_split_crosses_curve(HVACLibraryAPI, main, branch_a, branch_b)


def test_a_round_main_duct_is_always_split_across_its_curve():
    branch = _port("Circular", Diameter=200.0)
    assert _crosses(_port("Circular", Diameter=300.0), branch, branch)


def test_a_rectangular_main_duct_never_is():
    branch = _port("Rectangular", Width=300.0, Height=200.0)
    assert not _crosses(_port("Rectangular", Width=400.0, Height=250.0), branch, branch)


def test_a_wide_oval_split_through_its_flat_sides_is_fine():
    branch = _port("Oval", Width=300.0, Height=150.0)
    assert not _crosses(_port("Oval", Width=400.0, Height=200.0), branch, branch)


def test_a_wide_oval_split_in_a_rounded_end_is_not():
    # Branches 900 and 100 wide put the cut 160 mm off centre; the flat sides end 100 mm out.
    wide, narrow = _port("Oval", Width=900.0, Height=150.0), _port("Oval", Width=100.0, Height=50.0)
    assert _crosses(_port("Oval", Width=400.0, Height=200.0), wide, narrow)


def test_a_tall_oval_is_split_through_its_rounded_ends():
    branch = _port("Oval", Width=150.0, Height=300.0)
    assert _crosses(_port("Oval", Width=200.0, Height=400.0), branch, branch)
