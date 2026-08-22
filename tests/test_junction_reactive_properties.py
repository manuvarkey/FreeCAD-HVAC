"""
Focused tests for DuctJunction's remaining responsibilities after physical
fitting ownership moved onto DuctComponent: finding/grouping its component
chain (getComponents/getPrimaryComponent/getPortChains/getInlineComponents),
aggregating the external trim contract from that chain
(aggregateConnectionLengths), and composing every component's local
inlet/outlet ports (composeComponents) -- one Primary that always gets every
real port unchanged, plus zero-or-more independent Inline chains, each
attached to exactly one real edge.
"""

import json

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
import pytest

from freecad.HVAC.core import Junction as junction_mod
from freecad.HVAC.library import geometry_result as geometry_result_mod


class FakeGeometryFolder:
    def __init__(self, objs):
        self.OutList = list(objs)


class FakeNetworkObj:
    def __init__(self, objs):
        self.Geometry = FakeGeometryFolder(objs)


class FakeJunctionObj:
    def __init__(self, name="Junc0", topology="through", node_key="N1", connected_edge_keys=None, family=""):
        self.Name = name
        self.Topology = topology
        self.Family = family
        self.NodeKey = node_key
        self.ConnectedEdgeKeys = connected_edge_keys or []
        self.AnalysisJson = "{}"
        self.ConnectionLengthsJson = "[]"


class FakeComponentObj:
    """Minimal stand-in for a DuctComponent FreeCAD object."""

    def __init__(self, name, parent_name, role, attached_edge_key="", port_sequence=0, library_id="", type_id=""):
        self.Name = name
        self.ParentJunctionName = parent_name
        self.ComponentRole = role
        self.AttachedEdgeKey = attached_edge_key
        self.PortSequence = port_sequence
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


# ----------------------------------------------------------------------
# getComponents / getPrimaryComponent / getPortChains / getInlineComponents
# ----------------------------------------------------------------------

def test_get_components_orders_primary_first_then_inline_by_edge_and_port_sequence(monkeypatch):
    junction = FakeJunctionObj()
    c_b_late = FakeComponentObj("C_B_late", "Junc0", "Inline", attached_edge_key="B", port_sequence=20)
    c_primary = FakeComponentObj("C0", "Junc0", "Primary")
    c_b_early = FakeComponentObj("C_B_early", "Junc0", "Inline", attached_edge_key="B", port_sequence=10)
    c_a = FakeComponentObj("C_A", "Junc0", "Inline", attached_edge_key="A", port_sequence=10)
    other_junction_comp = FakeComponentObj("C3", "OtherJunc", "Primary")
    not_a_component = object()

    net = FakeNetworkObj([c_b_late, c_primary, c_b_early, c_a, other_junction_comp, not_a_component])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    components = dj.getComponents()

    assert [c.Name for c in components] == ["C0", "C_A", "C_B_early", "C_B_late"]


def test_get_primary_component_returns_the_primary_role(monkeypatch):
    junction = FakeJunctionObj()
    primary = FakeComponentObj("C0", "Junc0", "Primary")
    inline = FakeComponentObj("C1", "Junc0", "Inline", attached_edge_key="B", port_sequence=10)
    net = FakeNetworkObj([primary, inline])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    assert dj.getPrimaryComponent() is primary


def test_get_primary_component_returns_none_when_absent(monkeypatch):
    junction = FakeJunctionObj()
    inline = FakeComponentObj("C1", "Junc0", "Inline", attached_edge_key="B", port_sequence=10)
    net = FakeNetworkObj([inline])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    assert dj.getPrimaryComponent() is None


def test_get_port_chains_groups_by_edge_key_port_sequence_order(monkeypatch):
    junction = FakeJunctionObj()
    primary = FakeComponentObj("C0", "Junc0", "Primary")
    b_late = FakeComponentObj("C_B_late", "Junc0", "Inline", attached_edge_key="B", port_sequence=20)
    b_early = FakeComponentObj("C_B_early", "Junc0", "Inline", attached_edge_key="B", port_sequence=10)
    c_only = FakeComponentObj("C_C", "Junc0", "Inline", attached_edge_key="C", port_sequence=10)
    net = FakeNetworkObj([primary, b_late, b_early, c_only])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    chains = dj.getPortChains()

    assert set(chains.keys()) == {"B", "C"}
    assert [c.Name for c in chains["B"]] == ["C_B_early", "C_B_late"]
    assert [c.Name for c in chains["C"]] == ["C_C"]

    assert dj.getInlineComponents("B") == chains["B"]
    assert dj.getInlineComponents("A") == []
    assert {c.Name for c in dj.getInlineComponents()} == {"C_B_late", "C_B_early", "C_C"}


# ----------------------------------------------------------------------
# aggregateConnectionLengths
# ----------------------------------------------------------------------

def test_aggregate_connection_lengths_keeps_only_real_edge_keys(monkeypatch):
    """
    For a junction with no Inline chains at all, aggregateConnectionLengths
    is a straight passthrough of the Primary's own reported lengths,
    filtered to this junction's real ConnectedEdgeKeys.
    """
    junction = FakeJunctionObj(topology="branch", connected_edge_keys=["A", "B", "C"])
    primary = FakeComponentObj("C0", "Junc0", "Primary")
    primary.ConnectionLengthsJson = json.dumps([
        {"edge_key": "A", "segment_end": "end", "length": 50.0},
        {"edge_key": "B", "segment_end": "start", "length": 20.0},
        {"edge_key": "C", "segment_end": "start", "length": 30.0},
    ])
    net = FakeNetworkObj([primary])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.aggregateConnectionLengths()

    result = json.loads(junction.ConnectionLengthsJson)
    assert {(item["edge_key"], item["length"]) for item in result} == {("A", 50.0), ("B", 20.0), ("C", 30.0)}


def test_aggregate_connection_lengths_leaves_chained_edge_untouched(monkeypatch):
    """
    A chained edge's value was already computed and written directly by
    composeComponents() (needs the exact cumulative chain anchor geometry,
    not recoverable from any single component's own post-execute
    ConnectionLengthsJson) -- aggregateConnectionLengths() must leave it
    alone. A non-chained edge on the SAME junction is refreshed from the
    Primary's own reported trim, same as always.
    """
    junction = FakeJunctionObj(topology="through", connected_edge_keys=["A", "B"])
    junction.ConnectionLengthsJson = json.dumps([
        {"edge_key": "B", "segment_end": "start", "length": 999.0},  # written by composeComponents()
    ])
    primary = FakeComponentObj("C0", "Junc0", "Primary")
    primary.ConnectionLengthsJson = json.dumps([
        {"edge_key": "A", "segment_end": "end", "length": 50.0},
        {"edge_key": "B", "segment_end": "start", "length": 20.0},  # would be WRONG if used -- chain grew it
    ])
    inline = FakeComponentObj("C1", "Junc0", "Inline", attached_edge_key="B", port_sequence=10)
    net = FakeNetworkObj([primary, inline])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.aggregateConnectionLengths()

    result = {item["edge_key"]: item["length"] for item in json.loads(junction.ConnectionLengthsJson)}
    assert result == {"A": 50.0, "B": 999.0}


def test_aggregate_connection_lengths_single_component_does_not_double_count(monkeypatch):
    junction = FakeJunctionObj(connected_edge_keys=["A", "B"])
    primary = FakeComponentObj("C0", "Junc0", "Primary")
    primary.ConnectionLengthsJson = json.dumps([
        {"edge_key": "A", "segment_end": "end", "length": 50.0},
        {"edge_key": "B", "segment_end": "start", "length": 20.0},
    ])
    net = FakeNetworkObj([primary])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.aggregateConnectionLengths()

    result = json.loads(junction.ConnectionLengthsJson)
    assert len(result) == 2
    assert {(item["edge_key"], item["length"]) for item in result} == {("A", 50.0), ("B", 20.0)}


# ----------------------------------------------------------------------
# composeComponents -- Primary passthrough (any degree/topology)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("topology,ports", [
    ("end", [_port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 200.0}, True)]),
    ("through", [
        _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 200.0}, True),
        _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 200.0}, False),
    ]),
    ("branch", [
        _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 200.0}, True),
        _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 200.0}, False),
        _port("C", "start", (0, 0, 0), (0, 1, 0), "Circular", {"Diameter": 150.0}, False),
    ]),
    ("cross", [
        _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 200.0}, True),
        _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 200.0}, False),
        _port("C", "start", (0, 0, 0), (0, 1, 0), "Circular", {"Diameter": 150.0}, False),
        _port("D", "start", (0, 0, 0), (0, -1, 0), "Circular", {"Diameter": 150.0}, False),
    ]),
    ("multiport", [
        _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 200.0}, True),
        _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 200.0}, False),
        _port("C", "start", (0, 0, 0), (0, 1, 0), "Circular", {"Diameter": 150.0}, False),
        _port("D", "start", (0, 0, 0), (0, -1, 0), "Circular", {"Diameter": 150.0}, False),
        _port("E", "start", (0, 0, 0), (0, 0, 1), "Circular", {"Diameter": 150.0}, False),
    ]),
])
def test_compose_components_primary_only_passthrough_any_degree(monkeypatch, topology, ports):
    """The Primary always gets every real port unchanged, no matter the
    topology/degree -- byte-identical to how a single fitting's context was
    built before this refactor, and unaffected by there being no Inline
    chains at all."""
    edge_keys = [p["edge_key"] for p in ports]
    junction = FakeJunctionObj(topology=topology, connected_edge_keys=edge_keys)
    junction.AnalysisJson = json.dumps({"connected_ports": ports})
    primary = FakeComponentObj("C0", "Junc0", "Primary")
    net = FakeNetworkObj([primary])
    _patch_component_lookup(monkeypatch, net)

    dj = _bare_junction(junction)
    dj.composeComponents()

    assert json.loads(primary.LocalPortsJson) == ports
    assert primary.Profile == "Circular"
    # No Inline chains -- composeComponents() has nothing left to do, so it
    # must not touch ConnectionLengthsJson at all.
    assert junction.ConnectionLengthsJson == "[]"


# ----------------------------------------------------------------------
# composeComponents -- chains on a through/2-port node
# ----------------------------------------------------------------------

class _ChainFakeRegistry:
    """Every component reports the same fixed (trim_left, trim_right) for
    its first two given ports, mirroring the position-independent trim of
    real 2-port generators (see composeComponents' docstring)."""

    def __init__(self, trim_left, trim_right):
        self._trim_left = trim_left
        self._trim_right = trim_right

    def resolve_type(self, lib_id, type_id):
        return object()

    def resolve_params(self, type_def, obj=None):
        return {}

    def build_geometry(self, lib_id, type_def, context):
        ports = context["connected_ports"]
        return geometry_result_mod.normalize({
            "shape": None,
            "connection_lengths": [
                {"edge_key": ports[0]["edge_key"], "segment_end": ports[0]["segment_end"], "length": self._trim_left},
                {"edge_key": ports[1]["edge_key"], "segment_end": ports[1]["segment_end"], "length": self._trim_right},
            ],
        })


def test_compose_chain_on_single_edge_of_through_node(monkeypatch):
    """A reducer Primary (A -> B) plus one Inline damper attached to edge B
    only: the Primary keeps its real, unmodified ports (its own shape is
    generated exactly as if no chain existed), and the damper inherits
    edge B's own real profile/section, anchored past the Primary's own
    trim on that port."""
    port_a = _port("A", "end", (0, 0, 0), (-1, 0, 0), "Rectangular", {"Width": 600.0, "Height": 400.0}, True)
    port_b = _port("B", "start", (0, 0, 0), (1, 0, 0), "Rectangular", {"Width": 400.0, "Height": 300.0}, False)

    junction = FakeJunctionObj(connected_edge_keys=["A", "B"])
    junction.AnalysisJson = json.dumps({"connected_ports": [port_a, port_b]})

    reducer = FakeComponentObj("C0", "Junc0", "Primary", library_id="smacna", type_id="through_transition_generic")
    damper = FakeComponentObj(
        "C1", "Junc0", "Inline", attached_edge_key="B", port_sequence=10,
        library_id="smacna", type_id="through_damper_generic",
    )
    net = FakeNetworkObj([reducer, damper])
    _patch_component_lookup(monkeypatch, net)

    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _ChainFakeRegistry(trim_left=50.0, trim_right=30.0)),
    )

    dj = _bare_junction(junction)
    dj.composeComponents()

    # Primary: always the literal real ports, unchanged -- a chain never
    # rewrites the Primary's own local geometry context.
    assert json.loads(reducer.LocalPortsJson) == [port_a, port_b]
    assert reducer.Profile == "Rectangular"

    damper_ports = json.loads(damper.LocalPortsJson)
    # Damper inherits edge B's own real profile on both sides.
    assert damper_ports[0]["edge_key"] == "N1#B_seam0"
    assert damper_ports[0]["section_params"] == {"Width": 400.0, "Height": 300.0}
    assert damper_ports[0]["flow_into_junction"] is True
    assert damper_ports[0]["direction"] == [-1.0, 0.0, 0.0]
    assert damper_ports[1]["edge_key"] == "B"
    assert damper_ports[1]["section_params"] == {"Width": 400.0, "Height": 300.0}
    assert damper_ports[1]["flow_into_junction"] is False
    assert damper_ports[1]["direction"] == [1.0, 0.0, 0.0]
    # Anchor = real port B position + dir_B * (primary's own trim on B (30)
    # + damper's own inner trim (50)) = (0,0,0) + (1,0,0)*80.
    assert damper_ports[0]["position"] == damper_ports[1]["position"] == [80.0, 0.0, 0.0]

    # Only edge B is chained -- composeComponents() only ever writes the
    # chained edge(s) directly; A is left for aggregateConnectionLengths()
    # to fill in later from the Primary's own post-execute report.
    lengths = {item["edge_key"]: item["length"] for item in json.loads(junction.ConnectionLengthsJson)}
    # B's cumulative external trim = primary's own push (30) + damper's own
    # inner push (50) + damper's own outer push (30) = 110.
    assert lengths == {"B": 110.0}

    assert damper.Profile == "Rectangular"


def test_compose_multi_component_chain_on_single_edge(monkeypatch):
    """Two Inline components stacked on the SAME edge -- the running-sum
    anchor math must chain them correctly, one after another, independent
    of the Primary."""
    port_a = _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 300.0}, True)
    port_b = _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 300.0}, False)

    junction = FakeJunctionObj(connected_edge_keys=["A", "B"])
    junction.AnalysisJson = json.dumps({"connected_ports": [port_a, port_b]})

    primary = FakeComponentObj("C0", "Junc0", "Primary", library_id="smacna", type_id="through_generic")
    damper1 = FakeComponentObj(
        "C1", "Junc0", "Inline", attached_edge_key="B", port_sequence=10,
        library_id="smacna", type_id="through_damper_generic",
    )
    damper2 = FakeComponentObj(
        "C2", "Junc0", "Inline", attached_edge_key="B", port_sequence=20,
        library_id="smacna", type_id="through_damper_generic",
    )
    net = FakeNetworkObj([primary, damper1, damper2])
    _patch_component_lookup(monkeypatch, net)

    # Uniform 10.0 trim on every port keeps the running-sum arithmetic easy
    # to verify by hand.
    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _ChainFakeRegistry(trim_left=10.0, trim_right=10.0)),
    )

    dj = _bare_junction(junction)
    dj.composeComponents()

    damper1_ports = json.loads(damper1.LocalPortsJson)
    damper2_ports = json.loads(damper2.LocalPortsJson)

    # damper1 anchor = pos_B + dir_B * (primary_trim(10) + damper1_inner(10)) = 20.
    assert damper1_ports[0]["position"] == damper1_ports[1]["position"] == [20.0, 0.0, 0.0]
    # damper2 anchor = damper1_anchor + dir_B * (damper1_outer(10) + damper2_inner(10)) = 40.
    assert damper2_ports[0]["position"] == damper2_ports[1]["position"] == [40.0, 0.0, 0.0]
    assert damper1_ports[1]["edge_key"] == damper2_ports[0]["edge_key"] == "N1#B_seam1"
    assert damper2_ports[1]["edge_key"] == "B"

    lengths = {item["edge_key"]: item["length"] for item in json.loads(junction.ConnectionLengthsJson)}
    # Final external trim on B = primary(10) + damper1 in/out(10+10) + damper2 in/out(10+10) = 50.
    assert lengths == {"B": 50.0}


# ----------------------------------------------------------------------
# composeComponents -- independent chains on a branch/cross node
# ----------------------------------------------------------------------

class _UniformFakeRegistry:
    """Every given port is trimmed by the same fixed amount -- simple,
    predictable arithmetic for N-port Primary/branch scenarios without
    needing to predict exact synthetic seam key strings."""

    def __init__(self, trim):
        self._trim = trim

    def resolve_type(self, lib_id, type_id):
        return object()

    def resolve_params(self, type_def, obj=None):
        return {}

    def build_geometry(self, lib_id, type_def, context):
        ports = context["connected_ports"]
        return geometry_result_mod.normalize({
            "shape": None,
            "connection_lengths": [
                {"edge_key": p["edge_key"], "segment_end": p["segment_end"], "length": self._trim}
                for p in ports
            ],
        })


def _branch_ports():
    return [
        _port("A", "end", (0, 0, 0), (-1, 0, 0), "Circular", {"Diameter": 300.0}, True),
        _port("B", "start", (0, 0, 0), (1, 0, 0), "Circular", {"Diameter": 300.0}, False),
        _port("C", "start", (0, 0, 0), (0, 1, 0), "Circular", {"Diameter": 150.0}, False),
    ]


def test_compose_chain_on_one_leg_of_a_branch(monkeypatch):
    """A tee's Primary keeps all 3 of its real ports literally unchanged
    while only its branch leg (C) grows a 1-component Inline chain."""
    port_a, port_b, port_c = _branch_ports()
    junction = FakeJunctionObj(topology="branch", connected_edge_keys=["A", "B", "C"], family="branch.tee")
    junction.AnalysisJson = json.dumps({"connected_ports": [port_a, port_b, port_c]})

    primary = FakeComponentObj("C0", "Junc0", "Primary", library_id="smacna", type_id="branch_tee_generic")
    branch_damper = FakeComponentObj(
        "C1", "Junc0", "Inline", attached_edge_key="C", port_sequence=10,
        library_id="smacna", type_id="through_damper_generic",
    )
    net = FakeNetworkObj([primary, branch_damper])
    _patch_component_lookup(monkeypatch, net)

    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _UniformFakeRegistry(trim=10.0)),
    )

    dj = _bare_junction(junction)
    dj.composeComponents()

    # Primary: all 3 real ports, byte-identical, regardless of the chain.
    assert json.loads(primary.LocalPortsJson) == [port_a, port_b, port_c]
    assert primary.Profile == "Circular"

    damper_ports = json.loads(branch_damper.LocalPortsJson)
    # Anchor along +Y (edge C's own direction) = primary_trim(10) + damper_inner(10) = 20.
    assert damper_ports[0]["position"] == damper_ports[1]["position"] == [0.0, 20.0, 0.0]
    assert damper_ports[0]["edge_key"] == "N1#C_seam0"
    assert damper_ports[1]["edge_key"] == "C"

    lengths = {item["edge_key"]: item["length"] for item in json.loads(junction.ConnectionLengthsJson)}
    # Only C is chained -- A/B are left for aggregateConnectionLengths().
    assert lengths == {"C": 30.0}  # 10 (primary) + 10 (inner) + 10 (outer)


def test_compose_independent_chains_on_two_edges_of_same_branch(monkeypatch):
    """Two different Inline components on two different legs of the same
    tee compose completely independently -- neither chain's math leaks
    into the other's anchors/ports."""
    port_a, port_b, port_c = _branch_ports()
    junction = FakeJunctionObj(topology="branch", connected_edge_keys=["A", "B", "C"], family="branch.tee")
    junction.AnalysisJson = json.dumps({"connected_ports": [port_a, port_b, port_c]})

    primary = FakeComponentObj("C0", "Junc0", "Primary", library_id="smacna", type_id="branch_tee_generic")
    run_damper = FakeComponentObj(
        "C1", "Junc0", "Inline", attached_edge_key="B", port_sequence=10,
        library_id="smacna", type_id="through_damper_generic",
    )
    branch_damper = FakeComponentObj(
        "C2", "Junc0", "Inline", attached_edge_key="C", port_sequence=10,
        library_id="smacna", type_id="through_damper_generic",
    )
    net = FakeNetworkObj([primary, run_damper, branch_damper])
    _patch_component_lookup(monkeypatch, net)

    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _UniformFakeRegistry(trim=10.0)),
    )

    dj = _bare_junction(junction)
    dj.composeComponents()

    run_ports = json.loads(run_damper.LocalPortsJson)
    branch_ports = json.loads(branch_damper.LocalPortsJson)

    # Run leg (B, +X direction): anchor at (20, 0, 0).
    assert run_ports[0]["position"] == run_ports[1]["position"] == [20.0, 0.0, 0.0]
    assert run_ports[1]["edge_key"] == "B"
    # Branch leg (C, +Y direction): anchor at (0, 20, 0) -- unaffected by
    # the run leg's own chain.
    assert branch_ports[0]["position"] == branch_ports[1]["position"] == [0.0, 20.0, 0.0]
    assert branch_ports[1]["edge_key"] == "C"

    # Neither chain's synthetic seam key ever appears on the other's ports.
    run_keys = {p["edge_key"] for p in run_ports}
    branch_keys = {p["edge_key"] for p in branch_ports}
    assert run_keys.isdisjoint(branch_keys)

    lengths = {item["edge_key"]: item["length"] for item in json.loads(junction.ConnectionLengthsJson)}
    assert lengths == {"B": 30.0, "C": 30.0}
