"""
Focused tests for DuctComponent's execute(): building the geometry-backend
context from LocalPortsJson, applying the returned Shape/ConnectionLengthsJson,
and applying reactive/read-only computed_properties back onto matching object
properties -- the same "as-built" mechanism DuctJunction.execute() used to
implement directly before physical-fitting responsibilities moved onto
DuctComponent.
"""

import json

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
import pytest

from freecad.HVAC.core import Component as component_mod
from freecad.HVAC.library.Library import HVACPropertyDef
from freecad.HVAC.library import geometry_result as geometry_result_mod


class FakeDuctObj:
    """Minimal stand-in for a FreeCAD DocumentObject's dynamic-property API."""

    def __init__(self):
        self.PropertiesList = []
        self._editor_modes = {}
        self.Document = None

    def addProperty(self, prop_type, name, group, description, attr=0):
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
            setattr(self, name, None)
        return self

    def removeProperty(self, name):
        if name in self.PropertiesList:
            self.PropertiesList.remove(name)
        if hasattr(self, name):
            delattr(self, name)
        return True

    def setEditorMode(self, name, mode):
        self._editor_modes[name] = mode


class _FakeTypeDef:
    def __init__(self, properties):
        self.properties = properties


class _FakeRegistry:
    def __init__(self, type_def, geometry_result=None):
        self._type_def = type_def
        self._geometry_result = geometry_result
        self.last_context = None

    def resolve_type(self, lib_id, type_id):
        return self._type_def

    def resolve_params(self, type_def, obj=None):
        return {}

    def build_geometry(self, lib_id, type_def, context):
        self.last_context = context
        # Mirrors the real HVACLibraryRegistry.build_geometry(): normalize
        # whatever raw dict the test supplied into a real GeometryResult, so
        # DuctComponent.execute() sees exactly the same contract it would in
        # production (see library/geometry_result.py).
        return geometry_result_mod.normalize(self._geometry_result)


def _patch_registry(monkeypatch, registry):
    monkeypatch.setattr(
        component_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: registry),
    )


def _bare_component(obj):
    dc = component_mod.DuctComponent.__new__(component_mod.DuctComponent)
    dc.Object = obj
    dc._mirroring_design_flow_rate = False
    return dc


def test_setproperties_hides_internal_bookkeeping_and_json_properties(monkeypatch):
    """
    Internal bookkeeping fields and JSON blobs are kept (never removed) for
    the addon's own use, but hidden from the property editor -- a user
    never needs to read or edit them directly. ComponentRole/Profile stay
    visible read-only, and PortSequence stays visible AND editable -- it's
    a documented user-facing reordering mechanism for an edge's own Inline
    chain (see Network.py's applyAddInlineComponent).
    """
    monkeypatch.setattr(
        component_mod.hvaclib.HVACLibraryService, "get_active_hvac_library", staticmethod(lambda: None)
    )
    obj = FakeDuctObj()
    _bare_component(obj).setProperties(obj)

    for name in (
        "OwnerNetworkName", "ParentJunctionName", "AttachedEdgeKey",
        "TypeSchemaPropertyNames", "LocalPortsJson", "ConnectionLengthsJson",
    ):
        assert obj._editor_modes[name] == 2, name

    for name in ("ComponentRole", "Profile"):
        assert obj._editor_modes[name] == 1, name

    assert "PortSequence" not in obj._editor_modes


def _port(edge_key, segment_end, flow_into_junction):
    return {
        "edge_key": edge_key,
        "segment_end": segment_end,
        "position": [0.0, 0.0, 0.0],
        "direction": [1.0, 0.0, 0.0],
        "profile": "Circular",
        "section_params": {"Diameter": 200.0},
        "flow_into_junction": flow_into_junction,
    }


def test_execute_applies_computed_properties_to_matching_object_property(monkeypatch):
    properties = [
        HVACPropertyDef(
            name="angle", prop_type="App::PropertyAngle", group="Bend geometry",
            description="", default=0.0, editor_mode=1,
        ),
    ]
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyAngle", "angle", "Bend geometry", "")
    obj.LibraryId = "smacna"
    obj.TypeId = "through_elbow_rectangular"
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = ""
    obj.LocalPortsJson = json.dumps([_port("A", "end", True), _port("B", "start", False)])
    obj.ConnectionLengthsJson = "[]"
    obj.Label = "Component"

    geometry_result = {
        "shape": object(),
        "connection_lengths": [],
        # "not_a_real_property" mirrors a computed value the object doesn't
        # (yet) declare a property for -- execute() must silently skip it
        # rather than error, since applyTypeSchema is what adds properties.
        "computed_properties": {"angle": 42.0, "not_a_real_property": 99.0},
    }
    _patch_registry(monkeypatch, _FakeRegistry(_FakeTypeDef(properties), geometry_result))

    dc = _bare_component(obj)
    dc.execute(obj)

    assert obj.angle == 42.0
    assert not hasattr(obj, "not_a_real_property")
    assert obj.CasingShape is geometry_result["shape"]


def test_execute_writes_connection_lengths(monkeypatch):
    obj = FakeDuctObj()
    obj.LibraryId = "smacna"
    obj.TypeId = "through_damper_generic"
    obj.ComponentRole = "Inline"
    obj.ParentJunctionName = ""
    obj.LocalPortsJson = json.dumps([_port("A", "end", True), _port("B", "start", False)])
    obj.ConnectionLengthsJson = "[]"
    obj.Label = "Damper"

    lengths = [{"edge_key": "A", "segment_end": "end", "length": 50.0}]
    geometry_result = {"shape": object(), "connection_lengths": lengths, "computed_properties": {}}
    _patch_registry(monkeypatch, _FakeRegistry(_FakeTypeDef([]), geometry_result))

    dc = _bare_component(obj)
    dc.execute(obj)

    assert json.loads(obj.ConnectionLengthsJson) == lengths


def test_execute_does_nothing_without_library_or_type_id(monkeypatch):
    obj = FakeDuctObj()
    obj.LibraryId = ""
    obj.TypeId = ""
    obj.LocalPortsJson = "[]"

    registry = _FakeRegistry(_FakeTypeDef([]))
    _patch_registry(monkeypatch, registry)

    dc = _bare_component(obj)
    dc.execute(obj)

    assert not hasattr(obj, "Shape")


def test_execute_bails_when_local_ports_json_is_empty(monkeypatch):
    obj = FakeDuctObj()
    obj.LibraryId = "smacna"
    obj.TypeId = "through_damper_generic"
    obj.LocalPortsJson = "[]"

    registry = _FakeRegistry(_FakeTypeDef([]), {"shape": object(), "connection_lengths": []})
    _patch_registry(monkeypatch, registry)

    dc = _bare_component(obj)
    dc.execute(obj)

    assert not hasattr(obj, "Shape")


def test_execute_builds_shape_for_a_non_two_port_primary(monkeypatch):
    """
    A Primary component standing in for a whole branch/cross/multiport/end
    junction (not a through/2-port chain) carries however many real ports
    that node has -- 1 for a terminal device, 3 for a tee, etc. -- not
    always 2. execute() must still build geometry for these (regression:
    it used to bail whenever LocalPortsJson wasn't exactly 2 ports, so
    every tee/diffuser/cross silently never got a Shape).
    """
    for ports in (
        [_port("A", "end", True)],  # a degree-1 terminal (e.g. a diffuser)
        [_port("A", "end", True), _port("B", "start", False), _port("C", "start", False)],  # a tee
    ):
        obj = FakeDuctObj()
        obj.LibraryId = "smacna"
        obj.TypeId = "branch_tee_generic"
        obj.LocalPortsJson = json.dumps(ports)

        geometry_result = {"shape": object(), "connection_lengths": [], "computed_properties": {}}
        _patch_registry(monkeypatch, _FakeRegistry(_FakeTypeDef([]), geometry_result))

        dc = _bare_component(obj)
        dc.execute(obj)

        assert obj.CasingShape is geometry_result["shape"]


def test_execute_context_family_resolved_from_parent_for_primary_only(monkeypatch):
    """
    DuctComponent has no Family of its own -- only a Primary's family-driven
    dispatch (e.g. through_generic) needs it, resolved from the parent
    junction; Inline components always get "".
    """
    class FakeParent:
        Family = "through.bend"

    class FakeDoc:
        def getObject(self, name):
            return FakeParent() if name == "Junc0" else None

    obj = FakeDuctObj()
    obj.Document = FakeDoc()
    obj.LibraryId = "smacna"
    obj.TypeId = "through_generic"
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = "Junc0"
    obj.LocalPortsJson = json.dumps([_port("A", "end", True), _port("B", "start", False)])

    registry = _FakeRegistry(_FakeTypeDef([]), {"shape": object(), "connection_lengths": []})
    _patch_registry(monkeypatch, registry)

    dc = _bare_component(obj)
    dc.execute(obj)
    assert registry.last_context["family"] == "through.bend"

    # Same parent, but an Inline component never reads it.
    obj2 = FakeDuctObj()
    obj2.Document = FakeDoc()
    obj2.LibraryId = "smacna"
    obj2.TypeId = "through_damper_generic"
    obj2.ComponentRole = "Inline"
    obj2.ParentJunctionName = "Junc0"
    obj2.LocalPortsJson = json.dumps([_port("A", "end", True), _port("B", "start", False)])

    dc2 = _bare_component(obj2)
    dc2.execute(obj2)
    assert registry.last_context["family"] == ""


def test_execute_context_topology_resolved_from_parent_not_hardcoded(monkeypatch):
    """
    Regression: context["topology"] was hardcoded to "through", which
    validate_context checks against the type-def's own declared topology
    (e.g. "branch" for a tee) -- any non-through Primary (tee/cross/
    multiport/end) would fail that check and silently never get a Shape.
    """
    class FakeParent:
        Topology = "branch"
        Family = "branch.tee"

    class FakeDoc:
        def getObject(self, name):
            return FakeParent() if name == "Junc0" else None

    obj = FakeDuctObj()
    obj.Document = FakeDoc()
    obj.LibraryId = "smacna"
    obj.TypeId = "branch_tee_generic"
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = "Junc0"
    obj.LocalPortsJson = json.dumps([
        _port("A", "end", True), _port("B", "start", False), _port("C", "start", False),
    ])

    registry = _FakeRegistry(_FakeTypeDef([]), {"shape": object(), "connection_lengths": []})
    _patch_registry(monkeypatch, registry)

    dc = _bare_component(obj)
    dc.execute(obj)

    assert registry.last_context["topology"] == "branch"


def test_execute_inline_always_synthesizes_topology_through(monkeypatch):
    """
    An Inline component is always a physically two-port device, no matter
    what its parent junction's real topology is -- a damper attached to one
    leg of a branch/cross tee must still validate as "through", not
    "branch", or its type-def's declared topology check would wrongly
    reject it.
    """
    class FakeParent:
        Topology = "branch"
        Family = "branch.tee"

    class FakeDoc:
        def getObject(self, name):
            return FakeParent() if name == "Junc0" else None

    obj = FakeDuctObj()
    obj.Document = FakeDoc()
    obj.LibraryId = "smacna"
    obj.TypeId = "through_damper_generic"
    obj.ComponentRole = "Inline"
    obj.ParentJunctionName = "Junc0"
    obj.LocalPortsJson = json.dumps([_port("N0#C_seam0", "end", True), _port("C", "start", False)])

    registry = _FakeRegistry(_FakeTypeDef([]), {"shape": object(), "connection_lengths": []})
    _patch_registry(monkeypatch, registry)

    dc = _bare_component(obj)
    dc.execute(obj)

    assert registry.last_context["topology"] == "through"
    assert registry.last_context["family"] == ""


def test_execute_context_includes_parent_analysis(monkeypatch):
    """
    Regression: context["analysis"] was dropped entirely from
    DuctComponent.execute() -- builtin_basic's build_tee needs
    analysis["collinear_pairs"] (via _find_run_pair) to identify which two
    of a tee's three ports form the straight run, so every real tee failed
    with "Could not identify run pair" and never got a Shape.
    """
    analysis_dict = {"collinear_pairs": [{"a": 0, "b": 1, "angle": 3.14, "eccentricity": 0.0}]}

    class FakeParent:
        Topology = "branch"
        Family = "branch.tee"
        AnalysisJson = json.dumps(analysis_dict)

    class FakeDoc:
        def getObject(self, name):
            return FakeParent() if name == "Junc0" else None

    obj = FakeDuctObj()
    obj.Document = FakeDoc()
    obj.LibraryId = "builtin_basic"
    obj.TypeId = "branch_tee_generic"
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = "Junc0"
    obj.LocalPortsJson = json.dumps([
        _port("A", "end", True), _port("B", "start", False), _port("C", "start", False),
    ])

    registry = _FakeRegistry(_FakeTypeDef([]), {"shape": object(), "connection_lengths": []})
    _patch_registry(monkeypatch, registry)

    dc = _bare_component(obj)
    dc.execute(obj)

    assert registry.last_context["analysis"] == analysis_dict


# ----------------------------------------------------------------------
# DesignFlowRate: two-way proxy for the parent junction's own property
# (see Junction.py's DesignFlowRate/onChanged) -- a junction has no Shape
# and can't be selected in the 3D view, so a terminal's design flow rate is
# exposed here on its visible Primary fitting instead.
# ----------------------------------------------------------------------

class _FakeParentJunction:
    def __init__(self, topology, design_flow_rate=0.0):
        self.Topology = topology
        self.DesignFlowRate = design_flow_rate


class _FakeParentDoc:
    def __init__(self, parent):
        self._parent = parent

    def getObject(self, name):
        return self._parent if name == "Junc0" else None


@pytest.mark.parametrize("role,topology,expected_mode", [
    ("Primary", "end", 0),
    ("Primary", "through", 2),
    ("Primary", "branch", 2),
    ("Inline", "end", 2),
])
def test_sync_design_flow_rate_editable_only_for_end_topology_primary(role, topology, expected_mode):
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyFloat", "DesignFlowRate", "Airflow", "")
    obj.ComponentRole = role
    obj.ParentJunctionName = "Junc0"
    obj.Document = _FakeParentDoc(_FakeParentJunction(topology))

    dc = _bare_component(obj)
    dc._syncDesignFlowRate(obj)

    assert obj._editor_modes["DesignFlowRate"] == expected_mode


def test_sync_design_flow_rate_pulls_down_current_parent_value():
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyFloat", "DesignFlowRate", "Airflow", "")
    obj.DesignFlowRate = 0.0
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = "Junc0"
    obj.Document = _FakeParentDoc(_FakeParentJunction("end", design_flow_rate=250.0))

    dc = _bare_component(obj)
    dc._syncDesignFlowRate(obj)

    assert obj.DesignFlowRate == 250.0


def test_on_changed_pushes_primary_edit_up_to_parent_junction():
    parent = _FakeParentJunction("end", design_flow_rate=0.0)
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyFloat", "DesignFlowRate", "Airflow", "")
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = "Junc0"
    obj.Document = _FakeParentDoc(parent)
    obj.DesignFlowRate = 500.0

    dc = _bare_component(obj)
    dc.onChanged(obj, "DesignFlowRate")

    assert parent.DesignFlowRate == 500.0


def test_on_changed_ignores_properties_other_than_design_flow_rate():
    parent = _FakeParentJunction("end", design_flow_rate=0.0)
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyFloat", "DesignFlowRate", "Airflow", "")
    obj.ComponentRole = "Primary"
    obj.ParentJunctionName = "Junc0"
    obj.Document = _FakeParentDoc(parent)
    obj.DesignFlowRate = 500.0

    dc = _bare_component(obj)
    dc.onChanged(obj, "TypeId")

    assert parent.DesignFlowRate == 0.0


def test_on_changed_never_pushes_an_inline_components_own_edit():
    """An Inline component (e.g. a damper) is never a terminal -- its own
    DesignFlowRate has no meaning and must never overwrite the parent
    junction's value."""
    parent = _FakeParentJunction("end", design_flow_rate=0.0)
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyFloat", "DesignFlowRate", "Airflow", "")
    obj.ComponentRole = "Inline"
    obj.ParentJunctionName = "Junc0"
    obj.Document = _FakeParentDoc(parent)
    obj.DesignFlowRate = 999.0

    dc = _bare_component(obj)
    dc.onChanged(obj, "DesignFlowRate")

    assert parent.DesignFlowRate == 0.0
