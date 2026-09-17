# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""Focused tests for HVACLibraryAPI context and port-role helpers."""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
import pytest

from freecad.HVAC.library.library_api import HVACLibraryAPI


def _context(pair=None, port_count=3):
    ports = [{"edge_key": chr(ord("A") + index)} for index in range(port_count)]
    analysis = {} if pair is None else {"collinear_pairs": [pair]}
    return {"connected_ports": ports, "analysis": analysis}, ports


def test_run_branch_ports_returns_classifier_order_and_remaining_port():
    context, ports = _context({"a": 2, "b": 0})

    run_a, run_b, branch = HVACLibraryAPI.run_branch_ports(context)

    assert (run_a, run_b, branch) == (ports[2], ports[0], ports[1])


def test_run_branch_ports_requires_three_connected_ports():
    context, _ = _context({"a": 0, "b": 1}, port_count=2)

    with pytest.raises(ValueError, match="exactly three connected ports"):
        HVACLibraryAPI.run_branch_ports(context)


def test_run_branch_ports_requires_classifier_pair():
    context, _ = _context()

    with pytest.raises(ValueError, match="collinear_pairs.*empty"):
        HVACLibraryAPI.run_branch_ports(context)


def test_run_branch_ports_rejects_invalid_pair_indices():
    context, _ = _context({"a": 0, "b": 3})

    with pytest.raises(ValueError, match="invalid connected-port indices"):
        HVACLibraryAPI.run_branch_ports(context)
