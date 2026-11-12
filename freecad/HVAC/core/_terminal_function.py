# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Terminal function (ROADMAP 6.5): what an open duct end does in its system.

Each network is one system (`DuctNetwork.SystemType`). Each end junction
has `TerminalFunction`; `Auto` (the default) resolves from the system: the
balancing terminal (FlowBoundary `Auto`, where the system meets its fan or
equipment) is the `Source`, every other terminal plays the system's own
role. The resolved value goes into the node's qualifiers as
`terminal_function`, so type constraints and loss variants can read it, and
the flow solver checks the drawn air direction against it.

Plain Python, no FreeCAD.
"""

SYSTEM_TYPES = ("Supply", "Return", "Exhaust", "OutdoorAir")
TERMINAL_FUNCTIONS = ("Auto", "Supply", "Return", "Exhaust", "OutdoorAir", "Discharge", "Source")

# What a terminal of each system is, when not set: a supply outlet, a return
# inlet, an exhaust inlet, an outdoor-air intake.
_SYSTEM_TERMINAL = {"Supply": "Supply", "Return": "Return", "Exhaust": "Exhaust", "OutdoorAir": "OutdoorAir"}

# Which way air passes a terminal of each function, as the end node's
# flow_class: "exit" = air leaves the ducts there, "entry" = it comes in.
_FLOW_CLASS = {
    "Supply": "exit",  # into the room
    "Return": "entry",  # from the room, back to the equipment
    "Exhaust": "entry",  # from the room, to be thrown away
    "OutdoorAir": "entry",  # from outside
    "Discharge": "exit",  # out of the building
}


def resolve(system_type, setting, flow_boundary):
    """The terminal function of an end node: its own setting, or (Auto) the one its system and boundary imply."""
    setting = str(setting or "Auto")
    if setting != "Auto":
        return setting
    if str(flow_boundary or "Auto") == "Auto":
        return "Source"
    return _SYSTEM_TERMINAL.get(str(system_type or "Supply"), "Supply")


def expected_flow_class(function, system_type):
    """
    The end-node flow_class a terminal of this function should have:
    "entry" or "exit". A `Source` feeds a supply system (air comes in from
    the equipment) and drains every other system (air goes to the fan).
    """
    if function == "Source":
        return "entry" if str(system_type or "Supply") == "Supply" else "exit"
    return _FLOW_CLASS.get(function, "")


def direction_warning(label, function, system_type, flow_class):
    """
    A readable warning when an end node is drawn the wrong way for its
    function, else "". Unknown directions are not judged.
    """
    expected = expected_flow_class(function, system_type)
    if not expected or flow_class not in ("entry", "exit") or flow_class == expected:
        return ""
    drawn = "an inlet" if flow_class == "entry" else "an outlet"
    wanted = "an inlet" if expected == "entry" else "an outlet"
    return (
        "Terminal '{}' ({} in a {} system) is drawn as {} of the duct system but should be {}; "
        "reverse its duct with Edit Duct Directions, or change its TerminalFunction".format(
            label, function, system_type, drawn, wanted
        )
    )
