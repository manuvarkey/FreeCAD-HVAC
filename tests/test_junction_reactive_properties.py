"""
Focused tests for DuctJunction's remaining responsibilities after physical
fitting ownership moved onto DuctComponent: finding/ordering its component
chain (getComponents/getPrimaryComponent), aggregating the external trim
contract from that chain (aggregateConnectionLengths), and composing each
component's local inlet/outlet ports (composeComponents).
"""

import json

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core import Junction as junction_mod


class FakeGeometryFolder:
    def __init__(self, objs):
        self.OutList = list(objs)


class FakeNetworkObj:
    def __init__(self, objs):
        self.Geometry = FakeGeometryFolder(objs)


class FakeJunctionObj:
    def __init__(self, name="Junc0", topology="through", node_key="N1", connected_edge_keys=None):
        self.Name = name
        self.Topology = topology
        self.NodeKey = node_key
        self.ConnectedEdgeKeys = connected_edge_keys or []
        self.AnalysisJson = "{}"
        self.ConnectionLengthsJson = "[]"


class FakeComponentObj:
    """Minimal stand-in for a DuctComponent FreeCAD object."""

    def __init__(self, name, parent_name, role, sequence, library_id="", type_id=""):
        self.Name = name
        self.ParentJunctionName = parent_name
        self.ComponentRole = role
        self.Sequence = sequence
        self.LibraryId = library_id
        self.TypeId = type_id
        self.LocalPortsJson = "[]"
        self.ConnectionLengthsJson = "[]"
        self.Profile = ""


def _bare_junction(obj):
    dj = junction_mod.DuctJunction.__new__(junction_mod.DuctJunction)
    dj.Object = obj
    return dj


def _patch_component_lookup(monkeypatch, net, is_component=lambda o: isinstance(o, FakeComponentObj)):
    monkeypatch.setattr(junction_mod.hvaclib, "getOwnerNetwork", lambda obj: net)
    monkeypatch.setattr(junction_mod.hvaclib, "isDuctComponent", is_component)


def _port(edge_key, segment_end, position, direction, profile, section_params, flow_into_junction):
    return {
        "edge_key": edge_key,
        "segment_end": segment_end,
        "position": list(position),
        "direction": list(direction),
        "profile": profile,
        "section_params": section_params,
        "attachment": "Center",
        "user_offset": [0.0, 0.0, 0.0],
        "profile_x_axis": None,
        "flow_role": "inlet" if flow_into_junction else "outlet",
        "flow_direction": list(direction),
        "flow_into_junction": flow_into_junction,
    }


def test_get_components_returns_children_sorted_by_sequence(monkeypatch):
    junction = FakeJunctionObj()
    c_last = FakeComponentObj("C2", "Junc0", "Inline", 10)
    c_first = FakeComponentObj("C0", "Junc0", "Primary", 0)
    c_mid = FakeComponentObj("C1", "Junc0", "Inline", -10)
    other_junction_comp = FakeComponentObj("C3", "OtherJunc", "Primary", 0)
    not_a_component = object()

    net = FakeNetworkObj([c_last, c_first, c_mid, other_junction_comp, not_a_component])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    components = dj.getComponents()

    assert [c.Name for c in components] == ["C1", "C0", "C2"]


def test_get_primary_component_returns_the_primary_role(monkeypatch):
    junction = FakeJunctionObj()
    primary = FakeComponentObj("C0", "Junc0", "Primary", 0)
    inline = FakeComponentObj("C1", "Junc0", "Inline", 10)
    net = FakeNetworkObj([primary, inline])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    assert dj.getPrimaryComponent() is primary


def test_get_primary_component_returns_none_when_absent(monkeypatch):
    junction = FakeJunctionObj()
    inline = FakeComponentObj("C1", "Junc0", "Inline", 10)
    net = FakeNetworkObj([inline])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    assert dj.getPrimaryComponent() is None


def test_aggregate_connection_lengths_keeps_only_real_edge_keys(monkeypatch):
    """
    For a single-component junction (a plain fitting, or any
    branch/cross/multiport/end node -- never a through/2-port chain, which
    composeComponents' own Pass 4 handles instead, see
    test_compose_components_two_component_chain), aggregateConnectionLengths
    is a straight passthrough of that one component's own reported lengths,
    filtered to this junction's real ConnectedEdgeKeys.
    """
    junction = FakeJunctionObj(topology="branch", connected_edge_keys=["A", "B", "C"])
    only = FakeComponentObj("C0", "Junc0", "Primary", 0)
    only.ConnectionLengthsJson = json.dumps([
        {"edge_key": "A", "segment_end": "end", "length": 50.0},
        {"edge_key": "B", "segment_end": "start", "length": 20.0},
        {"edge_key": "C", "segment_end": "start", "length": 30.0},
    ])
    net = FakeNetworkObj([only])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.aggregateConnectionLengths()

    result = json.loads(junction.ConnectionLengthsJson)
    assert {(item["edge_key"], item["length"]) for item in result} == {("A", 50.0), ("B", 20.0), ("C", 30.0)}


def test_aggregate_connection_lengths_skips_multi_component_through_chain(monkeypatch):
    """
    A through/2-port chain's aggregate is written directly by
    composeComponents() (Pass 4), which has the exact cumulative anchor
    geometry a component's own post-execute ConnectionLengthsJson alone
    doesn't carry. aggregateConnectionLengths() must leave it alone rather
    than overwriting it with the (wrong, non-cumulative) naive
    first+last concatenation.
    """
    junction = FakeJunctionObj(topology="through", connected_edge_keys=["A", "B"])
    junction.ConnectionLengthsJson = json.dumps([
        {"edge_key": "A", "segment_end": "end", "length": 999.0},
        {"edge_key": "B", "segment_end": "start", "length": 888.0},
    ])
    first = FakeComponentObj("C0", "Junc0", "Primary", 0)
    first.ConnectionLengthsJson = json.dumps([{"edge_key": "A", "segment_end": "end", "length": 50.0}])
    last = FakeComponentObj("C1", "Junc0", "Inline", 10)
    last.ConnectionLengthsJson = json.dumps([{"edge_key": "B", "segment_end": "start", "length": 20.0}])
    net = FakeNetworkObj([first, last])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.aggregateConnectionLengths()

    result = json.loads(junction.ConnectionLengthsJson)
    assert {(item["edge_key"], item["length"]) for item in result} == {("A", 999.0), ("B", 888.0)}


def test_aggregate_connection_lengths_single_component_does_not_double_count(monkeypatch):
    junction = FakeJunctionObj(connected_edge_keys=["A", "B"])
    only = FakeComponentObj("C0", "Junc0", "Primary", 0)
    only.ConnectionLengthsJson = json.dumps([
        {"edge_key": "A", "segment_end": "end", "length": 50.0},
        {"edge_key": "B", "segment_end": "start", "length": 20.0},
    ])
    net = FakeNetworkObj([only])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.aggregateConnectionLengths()

    result = json.loads(junction.ConnectionLengthsJson)
    assert len(result) == 2
    assert {(item["edge_key"], item["length"]) for item in result} == {("A", 50.0), ("B", 20.0)}


def test_compose_components_single_component_passthrough(monkeypatch):
    """A branch node (or a through node with just its Primary) gets the
    junction's real connected_ports unchanged -- byte-identical to how a
    single fitting's context was built before this refactor."""
    ports = [
        _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 200.0}, True),
        _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 200.0}, False),
        _port("C", "start", (0, 0, 0), (0, 1, 0), "Circular", {"Diameter": 150.0}, False),
    ]
    junction = FakeJunctionObj(topology="branch", connected_edge_keys=["A", "B", "C"])
    junction.AnalysisJson = json.dumps({"connected_ports": ports})
    primary = FakeComponentObj("C0", "Junc0", "Primary", 0)
    net = FakeNetworkObj([primary])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.composeComponents()

    assert json.loads(primary.LocalPortsJson) == ports
    # Regression: the single-component branch used to never set Profile,
    # leaving it permanently "" for every branch/cross/multiport/end
    # junction -- breaking topology/profile-based type filtering in the
    # Edit Type UI for anything but a through/2-port chain.
    assert primary.Profile == "Circular"


class _ChainFakeRegistry:
    """Every component reports the same fixed (trim_left, trim_right),
    mirroring the position-independent trim of real 2-port generators
    (see composeComponents' docstring)."""

    def __init__(self, trim_left, trim_right):
        self._trim_left = trim_left
        self._trim_right = trim_right

    def resolve_type(self, lib_id, type_id):
        return object()

    def resolve_params(self, type_def, obj=None):
        return {}

    def build_geometry(self, lib_id, type_def, context):
        ports = context["connected_ports"]
        return {
            "shape": None,
            "connection_lengths": [
                {"edge_key": ports[0]["edge_key"], "segment_end": ports[0]["segment_end"], "length": self._trim_left},
                {"edge_key": ports[1]["edge_key"], "segment_end": ports[1]["segment_end"], "length": self._trim_right},
            ],
        }


def test_compose_components_two_component_chain(monkeypatch):
    port_a = _port("A", "end", (0, 0, 0), (-1, 0, 0), "Rectangular", {"Width": 600.0, "Height": 400.0}, True)
    port_b = _port("B", "start", (0, 0, 0), (1, 0, 0), "Rectangular", {"Width": 400.0, "Height": 300.0}, False)

    junction = FakeJunctionObj(connected_edge_keys=["A", "B"])
    junction.AnalysisJson = json.dumps({"connected_ports": [port_a, port_b]})

    reducer = FakeComponentObj("C0", "Junc0", "Primary", 0, library_id="smacna", type_id="through_transition_generic")
    damper = FakeComponentObj("C1", "Junc0", "Inline", 10, library_id="smacna", type_id="through_damper_generic")
    net = FakeNetworkObj([reducer, damper])
    _patch_component_lookup(monkeypatch, net)

    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _ChainFakeRegistry(trim_left=50.0, trim_right=30.0)),
    )

    dj = _bare_junction(junction)
    dj.composeComponents()

    reducer_ports = json.loads(reducer.LocalPortsJson)
    damper_ports = json.loads(damper.LocalPortsJson)

    # Reducer: left is the real inlet, right is a synthetic seam already
    # carrying the downstream (port_b) size -- the Primary is what changes
    # the duct size, exactly like a single-fitting reducer always did.
    assert reducer_ports[0]["edge_key"] == "A"
    assert reducer_ports[0]["section_params"] == {"Width": 600.0, "Height": 400.0}
    assert reducer_ports[0]["flow_into_junction"] is True
    assert reducer_ports[1]["edge_key"] == "N1#seam0"
    assert reducer_ports[1]["section_params"] == {"Width": 400.0, "Height": 300.0}
    assert reducer_ports[1]["flow_into_junction"] is False
    # Both of the Reducer's own ports share one coincident anchor.
    assert reducer_ports[0]["position"] == reducer_ports[1]["position"] == [0.0, 0.0, 0.0]

    # Damper: entirely downstream of the Primary -- both sides already at
    # the reduced (port_b) size. Its own anchor has advanced by the
    # Reducer's own right trim (30) plus the Damper's own left trim (50).
    assert damper_ports[0]["edge_key"] == "N1#seam0"
    assert damper_ports[0]["section_params"] == {"Width": 400.0, "Height": 300.0}
    assert damper_ports[0]["flow_into_junction"] is True
    assert damper_ports[1]["edge_key"] == "B"
    assert damper_ports[1]["section_params"] == {"Width": 400.0, "Height": 300.0}
    assert damper_ports[1]["flow_into_junction"] is False
    assert damper_ports[0]["position"] == damper_ports[1]["position"] == [80.0, 0.0, 0.0]

    # Aggregate external trim contract (composeComponents' Pass 4): the A
    # side is just the Reducer's own left push (50). The B side must be
    # the CUMULATIVE distance from port_b's real position to the Damper's
    # actual right face (110 = Reducer's right push 30 + Damper's own left
    # push 50 + Damper's own right push 30) -- NOT just the Damper's own
    # local right push (30) alone, which would silently ignore the
    # Reducer sitting upstream of it in the chain.
    lengths = {item["edge_key"]: item["length"] for item in json.loads(junction.ConnectionLengthsJson)}
    assert lengths == {"A": 50.0, "B": 110.0}

    assert reducer.Profile == "Rectangular"
    assert damper.Profile == "Rectangular"


def test_compose_components_reordering_flips_inherited_dimensions(monkeypatch):
    """An Inline component placed BEFORE the Primary keeps the upstream
    (port_a) size on both its sides, instead of the downstream size."""
    port_a = _port("A", "end", (0, 0, 0), (-1, 0, 0), "Rectangular", {"Width": 600.0, "Height": 400.0}, True)
    port_b = _port("B", "start", (0, 0, 0), (1, 0, 0), "Rectangular", {"Width": 400.0, "Height": 300.0}, False)

    junction = FakeJunctionObj(connected_edge_keys=["A", "B"])
    junction.AnalysisJson = json.dumps({"connected_ports": [port_a, port_b]})

    damper = FakeComponentObj("C1", "Junc0", "Inline", -10, library_id="smacna", type_id="through_damper_generic")
    reducer = FakeComponentObj("C0", "Junc0", "Primary", 0, library_id="smacna", type_id="through_transition_generic")
    net = FakeNetworkObj([damper, reducer])
    _patch_component_lookup(monkeypatch, net)

    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _ChainFakeRegistry(trim_left=50.0, trim_right=30.0)),
    )

    dj = _bare_junction(junction)
    dj.composeComponents()

    damper_ports = json.loads(damper.LocalPortsJson)
    reducer_ports = json.loads(reducer.LocalPortsJson)

    # Damper is now first (upstream of the Primary) -- both its sides carry
    # the upstream (port_a) size, the opposite of the previous test.
    assert damper_ports[0]["edge_key"] == "A"
    assert damper_ports[0]["section_params"] == {"Width": 600.0, "Height": 400.0}
    assert damper_ports[1]["section_params"] == {"Width": 600.0, "Height": 400.0}

    assert reducer_ports[0]["section_params"] == {"Width": 600.0, "Height": 400.0}
    assert reducer_ports[1]["edge_key"] == "B"
    assert reducer_ports[1]["section_params"] == {"Width": 400.0, "Height": 300.0}
