# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
HVAC equipment joining several networks (ROADMAP 6.5): an AHU with a
supply outlet and a return inlet, a fan with an intake and a discharge.

The equipment is the user's model itself: an App::Part holding the modelled
unit and one port marker per connection (the same
`Part::LocalCoordinateSystem` markers custom fittings use, see
core/_custom_fitting.py), each with an `EquipmentPortFunction`. "Create
Equipment" adds the equipment properties to that App::Part, so selecting
the equipment selects its geometry and markers, and the ports sit directly
in it. (Documents from before keep a separate equipment object that links
its App::Part through `Model`; model_of() reads both.) A port is connected to one terminal of one
network ("Connect to Equipment"); that terminal becomes the network's
balancing terminal (its Source) and gets the `end_equipment_connection`
type, a collar carrying the fan-connection loss.

Each network is still solved on its own. After a network's airflow
calculation the equipment collects, per port, the flow and the worst path
of that network, and sums them up (`equipment_summary`).
"""

import json

import FreeCAD

from . import _custom_fitting

PORT_FUNCTIONS = ["SupplyOutlet", "ReturnInlet", "OutdoorAirInlet", "ExhaustOutlet"]
# The system a network connected to each kind of port should be.
PORT_SYSTEM = {
    "SupplyOutlet": "Supply",
    "ReturnInlet": "Return",
    "OutdoorAirInlet": "OutdoorAir",
    "ExhaustOutlet": "Exhaust",
}
CONNECTION_TYPE_ID = "end_equipment_connection"
POSITION_TOLERANCE_MM = 1.0


# ----------------------------------------------------------------------------
# Pure summary
# ----------------------------------------------------------------------------


def equipment_summary(port_results, internal_pressure_drop_pa=0.0):
    """
    Totals over an equipment's ports. `port_results` is a list of dicts
    with "function", "flow_lps" and "worst_path_pa" (None when that
    network has no result yet). Returns:

        {"supply_lps", "return_lps", "outdoor_air_lps", "exhaust_lps",
         "outdoor_air_is_estimate", "supply_fan_pa", "return_fan_pa"}

    Outdoor air is the outdoor-air ports' own flow, or (no such port)
    supply minus return when the unit has both. The supply fan works
    against the unit's internal drop plus the worst supply path and the
    worst outdoor-air path; a return or exhaust fan against the worst
    return path plus the worst exhaust path. Fan figures are None until a
    supply (or return) network has been calculated.
    """

    def total(function):
        values = [r["flow_lps"] for r in port_results if r["function"] == function and r.get("flow_lps") is not None]
        return sum(values) if values else None

    def worst(function):
        values = [
            r["worst_path_pa"] for r in port_results if r["function"] == function and r.get("worst_path_pa") is not None
        ]
        return max(values) if values else None

    supply, ret = total("SupplyOutlet"), total("ReturnInlet")
    outdoor, exhaust = total("OutdoorAirInlet"), total("ExhaustOutlet")
    estimate = False
    if outdoor is None and supply is not None and ret is not None:
        outdoor, estimate = supply - ret, True

    supply_fan = None
    if worst("SupplyOutlet") is not None:
        supply_fan = float(internal_pressure_drop_pa or 0.0) + worst("SupplyOutlet") + (worst("OutdoorAirInlet") or 0.0)
    return_fan = None
    if worst("ReturnInlet") is not None or worst("ExhaustOutlet") is not None:
        return_fan = (worst("ReturnInlet") or 0.0) + (worst("ExhaustOutlet") or 0.0)
    return {
        "supply_lps": supply,
        "return_lps": ret,
        "outdoor_air_lps": outdoor,
        "exhaust_lps": exhaust,
        "outdoor_air_is_estimate": estimate,
        "supply_fan_pa": supply_fan,
        "return_fan_pa": return_fan,
    }


def report_lines(port_results, summary):
    """Human-readable report: one line per port, then the totals."""

    def fmt(value, unit):
        return "-" if value is None else "{:.1f} {}".format(value, unit)

    lines = [
        "{} ({}, network {}): {}, worst path {}".format(
            r["function"],
            r["port"],
            r.get("network") or "-",
            fmt(r.get("flow_lps"), "L/s"),
            fmt(r.get("worst_path_pa"), "Pa"),
        )
        for r in port_results
    ]
    lines.append(
        "Supply {}, return {}, outdoor air {}{}".format(
            fmt(summary["supply_lps"], "L/s"),
            fmt(summary["return_lps"], "L/s"),
            fmt(summary["outdoor_air_lps"], "L/s"),
            " (supply minus return)" if summary["outdoor_air_is_estimate"] else "",
        )
    )
    lines.append(
        "Supply fan static {}, return / exhaust fan static {}".format(
            fmt(summary["supply_fan_pa"], "Pa"), fmt(summary["return_fan_pa"], "Pa")
        )
    )
    return lines


# ----------------------------------------------------------------------------
# The document object
# ----------------------------------------------------------------------------


class HVACEquipment:
    """
    A unit (AHU, fan) whose ports feed terminals of one or more networks.

    New equipment is an App::Part carrying the properties of setProperties()
    (make_equipment); this proxy class is kept so documents with the older,
    separate equipment object still load.
    """

    TYPE = "HVACEquipment"

    def __init__(self, obj):
        obj.Proxy = self
        self.Object = obj
        self.setProperties(obj)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self.Object = obj
        self.setProperties(obj)

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def execute(self, obj):
        pass

    @staticmethod
    def setProperties(obj):
        def add(prop_type, name, group, description, mode=0):
            if name not in obj.PropertiesList:
                obj.addProperty(prop_type, name, group, description)
            try:
                obj.setEditorMode(name, mode)
            except Exception:
                pass

        add("App::PropertyString", "EquipmentType", "HVAC", "Internal: marks an HVAC equipment object", 2)
        if getattr(obj, "EquipmentType", "") != HVACEquipment.TYPE:
            obj.EquipmentType = HVACEquipment.TYPE
        add(
            "App::PropertyFloat",
            "InternalPressureDrop",
            "HVAC Equipment",
            "Pressure drop inside the unit at design flow, in Pa (coils, filters, casing); added to the supply fan's duty",
        )
        add(
            "App::PropertyStringList",
            "Connections",
            "HVAC",
            "Internal: one 'port marker name|network name|node key' entry per connected port",
            2,
        )
        add("App::PropertyString", "PortResultsJson", "HVAC", "Internal: per-port results of the last calculations", 2)
        for name, description in (
            ("SupplyAirflow", "Total supply airflow, L/s (last calculation)"),
            ("ReturnAirflow", "Total return airflow, L/s"),
            ("OutdoorAirflow", "Outdoor airflow, L/s: the outdoor-air ports, or supply minus return"),
            ("SupplyFanStatic", "Supply fan static pressure, Pa: internal drop + worst supply and outdoor-air paths"),
            ("ReturnFanStatic", "Return / exhaust fan static pressure, Pa: worst return and exhaust paths"),
        ):
            add("App::PropertyFloat", name, "HVAC Results", description, 1)
        add(
            "App::PropertyStringList",
            "Report",
            "HVAC Results",
            "Per-port flows and fan duties from the last calculations",
            1,
        )


def is_equipment(obj):
    return getattr(obj, "EquipmentType", "") == HVACEquipment.TYPE


def make_equipment(part):
    """
    Turn an App::Part (the modelled unit) into HVAC equipment: add the
    equipment properties to it and give every port marker already in it an
    EquipmentPortFunction. Safe to call again. Returns the part.
    """
    HVACEquipment.setProperties(part)
    for marker in _custom_fitting.container_ports(part):
        ensure_equipment_port(marker)
    return part


def create_equipment(doc, model=None, name="Equipment"):
    """Equipment from `model` (an App::Part), or from a new empty App::Part named `name`."""
    if model is None:
        model = doc.addObject("App::Part", name)
    return make_equipment(model)


def model_of(equipment):
    """The App::Part holding an equipment's geometry and ports: the equipment itself, or an older object's Model."""
    model = getattr(equipment, "Model", None)
    return model if model is not None else equipment


def equipment_ports(equipment):
    """An equipment's port markers, in a stable order."""
    return _custom_fitting.container_ports(model_of(equipment))


def equipment_of_marker(marker):
    """The equipment a port marker belongs to, or None."""
    doc = getattr(marker, "Document", None)
    if doc is None:
        return None
    for obj in doc.Objects:
        if is_equipment(obj) and marker in equipment_ports(obj):
            return obj
    return None


class HVACEquipmentViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def claimChildren(self):
        obj = getattr(self, "Object", None)
        model = getattr(obj, "Model", None) if obj is not None else None
        return [model] if model is not None else []

    def getIcon(self):
        from ..utils import hvaclib

        return hvaclib.get_icon_path("DuctsIcon.svg")


# ----------------------------------------------------------------------------
# Port markers and connections
# ----------------------------------------------------------------------------


def ensure_equipment_port(lcs, function=None):
    """Give a port marker its EquipmentPortFunction (and the ordinary port properties)."""
    _custom_fitting.ensure_port_properties(lcs)
    if "EquipmentPortFunction" not in lcs.PropertiesList:
        lcs.addProperty(
            "App::PropertyEnumeration",
            "EquipmentPortFunction",
            _custom_fitting.PORT_GROUP,
            "What this equipment port does: SupplyOutlet, ReturnInlet, OutdoorAirInlet or ExhaustOutlet",
        )
        lcs.EquipmentPortFunction = list(PORT_FUNCTIONS)
        lcs.EquipmentPortFunction = "SupplyOutlet"
    if function:
        lcs.EquipmentPortFunction = function


def _parse_connections(equipment):
    out = []
    for entry in list(getattr(equipment, "Connections", []) or []):
        parts = str(entry).split("|")
        if len(parts) == 3:
            out.append({"port": parts[0], "network": parts[1], "node_key": parts[2]})
    return out


def _write_connections(equipment, connections):
    equipment.Connections = ["{}|{}|{}".format(c["port"], c["network"], c["node_key"]) for c in connections]


def connect(equipment, port_marker, junction):
    """
    Connect an equipment port (one of its port markers) to a network
    terminal. The terminal becomes the network's Source: FlowBoundary Auto,
    TerminalFunction Auto, its fitting the equipment connection collar.
    Returns a list of warnings (position or system mismatches); raises
    ValueError when the pieces cannot be connected at all.
    """
    from ..utils import hvaclib
    from .Network import DuctNetwork

    if port_marker not in equipment_ports(equipment):
        raise ValueError("'{}' is not a port marker of equipment '{}'".format(port_marker.Label, equipment.Label))
    if not hvaclib.isDuctJunction(junction) or getattr(junction, "Topology", "") != "end":
        raise ValueError("Pick an open duct end (a terminal node) to connect")
    net = DuctNetwork.getOwnerNetwork(junction)
    if net is None:
        raise ValueError("The terminal belongs to no network")
    ensure_equipment_port(port_marker)

    warnings = []
    offset = (FreeCAD.Vector(junction.CenterPoint) - port_marker.getGlobalPlacement().Base).Length
    if offset > POSITION_TOLERANCE_MM:
        warnings.append(
            "The duct end is {:.1f} mm from port '{}': move the base geometry's end point onto the port".format(
                offset, port_marker.Label
            )
        )
    wanted = PORT_SYSTEM.get(str(port_marker.EquipmentPortFunction), "")
    if wanted and str(getattr(net, "SystemType", "")) != wanted:
        warnings.append(
            "Port '{}' is a {} but network '{}' is a {} system".format(
                port_marker.Label, port_marker.EquipmentPortFunction, net.Label, getattr(net, "SystemType", "?")
            )
        )

    node_key = str(getattr(junction, "NodeKey", ""))
    connections = [
        c
        for c in _parse_connections(equipment)
        if c["port"] != port_marker.Name and not (c["network"] == net.Name and c["node_key"] == node_key)
    ]
    connections.append({"port": port_marker.Name, "network": net.Name, "node_key": node_key})
    _write_connections(equipment, connections)

    if str(getattr(junction, "FlowBoundary", "Auto")) != "Auto":
        junction.FlowBoundary = "Auto"
    if str(getattr(junction, "TerminalFunction", "Auto")) != "Auto":
        junction.TerminalFunction = "Auto"
    primary = junction.Proxy.getPrimaryComponent()
    library_id = getattr(primary, "LibraryId", "") if primary is not None else ""
    library_id = library_id or getattr(net, "DefaultLibraryId", "")
    reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
    if reg.resolve_type(library_id, CONNECTION_TYPE_ID) is None:
        warnings.append(
            "Library '{}' has no {} type; the builtin_basic one is used".format(library_id, CONNECTION_TYPE_ID)
        )
        library_id = "builtin_basic"
    DuctNetwork.applyTypeSelection([junction], library_id=library_id, type_id=CONNECTION_TYPE_ID)
    return warnings


def equipment_for_network(net):
    """The equipment objects with at least one port connected to `net`."""
    doc = getattr(net, "Document", None)
    if doc is None:
        return []
    return [
        obj
        for obj in doc.Objects
        if is_equipment(obj) and any(c["network"] == net.Name for c in _parse_connections(obj))
    ]


def update_from_airflow(net, result):
    """
    After `net`'s airflow calculation (an AirflowSolveResult): store, for
    every equipment port connected to it, the terminal's flow and the
    network's worst path, then refresh the equipment's totals and report.
    Ports on other networks keep their last values.
    """
    junctions = net.Proxy.collectJunctionObjects()
    for equipment in equipment_for_network(net):
        try:
            stored = json.loads(getattr(equipment, "PortResultsJson", "") or "{}")
        except ValueError:
            stored = {}
        doc = equipment.Document
        for connection in _parse_connections(equipment):
            marker = doc.getObject(connection["port"])
            if marker is None:
                continue
            entry = {
                "port": marker.Label,
                "function": str(getattr(marker, "EquipmentPortFunction", "") or "SupplyOutlet"),
                "network": connection["network"],
                "flow_lps": None,
                "worst_path_pa": None,
            }
            if connection["network"] == net.Name:
                junction = junctions.get(connection["node_key"])
                for component in result.components:
                    match = next((j for j in component.junctions if j.obj is junction), None) if junction else None
                    if match is not None:
                        entry["flow_lps"] = float(match.total_flow_lps)
                        entry["worst_path_pa"] = float(component.critical_pressure_pa)
                        break
            else:
                entry = dict(stored.get(connection["port"], entry), port=marker.Label, function=entry["function"])
            stored[connection["port"]] = entry
        port_results = [stored[c["port"]] for c in _parse_connections(equipment) if c["port"] in stored]
        summary = equipment_summary(port_results, float(getattr(equipment, "InternalPressureDrop", 0.0) or 0.0))
        equipment.PortResultsJson = json.dumps(stored, sort_keys=True)
        equipment.SupplyAirflow = summary["supply_lps"] or 0.0
        equipment.ReturnAirflow = summary["return_lps"] or 0.0
        equipment.OutdoorAirflow = summary["outdoor_air_lps"] or 0.0
        equipment.SupplyFanStatic = summary["supply_fan_pa"] or 0.0
        equipment.ReturnFanStatic = summary["return_fan_pa"] or 0.0
        equipment.Report = report_lines(port_results, summary)
