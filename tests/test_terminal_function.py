"""Terminal function (ROADMAP 6.5): resolution, direction warnings, type filtering."""

import json
from types import SimpleNamespace

import pytest
from test_shipped_type_references import _load_registry

import conftest  # noqa: F401 -- installs FreeCAD stubs
from freecad.HVAC.core import _terminal_function as tf
from freecad.HVAC.core.FlowNetwork import terminal_direction_warnings


@pytest.mark.parametrize(
    "system, setting, boundary, expected",
    [
        ("Supply", "Auto", "Fixed", "Supply"),
        ("Return", "Auto", "Fixed", "Return"),
        ("Exhaust", "Auto", "Closed", "Exhaust"),
        ("OutdoorAir", "Auto", "Fixed", "OutdoorAir"),
        ("Supply", "Auto", "Auto", "Source"),  # the balancing terminal
        ("Supply", "Discharge", "Auto", "Discharge"),  # an explicit setting wins
        ("Return", "Supply", "Fixed", "Supply"),
    ],
)
def test_resolve(system, setting, boundary, expected):
    assert tf.resolve(system, setting, boundary) == expected


@pytest.mark.parametrize(
    "function, system, flow_class",
    [
        ("Supply", "Supply", "exit"),
        ("Return", "Return", "entry"),
        ("Exhaust", "Exhaust", "entry"),
        ("OutdoorAir", "OutdoorAir", "entry"),
        ("Discharge", "Exhaust", "exit"),
        ("Source", "Supply", "entry"),
        ("Source", "Return", "exit"),
    ],
)
def test_expected_flow_class(function, system, flow_class):
    assert tf.expected_flow_class(function, system) == flow_class


def test_direction_warning_only_when_drawn_the_wrong_way():
    assert tf.direction_warning("T1", "Supply", "Supply", "exit") == ""
    assert tf.direction_warning("T1", "Supply", "Supply", "unknown") == ""
    message = tf.direction_warning("T1", "Supply", "Supply", "entry")
    assert "T1" in message and "inlet" in message and "outlet" in message


def _junction(label, flow_class, function, topology="end"):
    return SimpleNamespace(
        Number="",
        Label=label,
        Name=label,
        Topology=topology,
        AnalysisJson=json.dumps({"flow_class": flow_class, "qualifiers": {"terminal_function": function}}),
    )


def test_network_warnings_name_each_wrong_terminal():
    net = SimpleNamespace(SystemType="Supply")
    junctions = {
        "a": _junction("Diffuser 1", "exit", "Supply"),
        "b": _junction("Diffuser 2", "entry", "Supply"),
        "c": _junction("AHU", "entry", "Source"),
        "d": _junction("Tee", "diverging", "", topology="branch"),
    }
    warnings = terminal_direction_warnings(net, junctions)
    assert len(warnings) == 1 and "Diffuser 2" in warnings[0]


def test_type_list_follows_the_terminal_function():
    lib = _load_registry().get_library("builtin_basic")

    def ids(function):
        classification = {"flow_class": "", "qualifiers": {"terminal_function": function}, "derived_values": {}}
        ports = [{"profile": "Circular", "section_params": {"Diameter": 200.0}}]
        types = lib.list_types(
            category="junction",
            family="end.terminal",
            connected_ports=ports,
            classification=classification,
            include_placeholders=False,
        )
        return {t.id for t in types}

    supply, outdoor = ids("Supply"), ids("OutdoorAir")
    assert "end_diffuser_round" in supply and "end_diffuser_round" not in outdoor
    assert "end_hood_intake" in outdoor and "end_hood_intake" not in supply
    assert "end_entry_plain" in supply and "end_entry_plain" in outdoor  # unconstrained
