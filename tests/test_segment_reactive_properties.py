"""
Focused tests for DuctSegment.applyTypeSchema's stale-property cleanup:
switching TypeId to a model with a different property schema must drop
properties the new type doesn't declare, while Diameter/Width/Height stay
(they're permanent core dimensional properties shared across every segment
type -- see the comment in Segment.py's applyTypeSchema).
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

import FreeCAD

from freecad.HVAC.core import Segment as segment_mod
from freecad.HVAC.library.Library import HVACPropertyDef
from freecad.HVAC.library import geometry_result as geometry_result_mod


class FakeSegmentObj:
    """Minimal stand-in for a FreeCAD DocumentObject's dynamic-property API."""

    def __init__(self):
        self.PropertiesList = []
        self._editor_modes = {}

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
    def __init__(self, type_def):
        self._type_def = type_def

    def resolve_type(self, lib_id, type_id):
        return self._type_def


def _patch_registry(monkeypatch, registry):
    monkeypatch.setattr(
        segment_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: registry),
    )


def _bare_segment(obj):
    ds = segment_mod.DuctSegment.__new__(segment_mod.DuctSegment)
    ds.Object = obj
    return ds


def test_setproperties_hides_internal_bookkeeping_and_json_properties(monkeypatch):
    """
    Internal bookkeeping fields and JSON blobs are kept (never removed) for
    the addon's own use, but hidden from the property editor -- a user
    never needs to read or edit them directly. Geometric diagnostics and
    human-meaningful identity labels stay visible read-only.
    """
    monkeypatch.setattr(
        segment_mod.hvaclib.HVACLibraryService, "get_active_hvac_library", staticmethod(lambda: None)
    )
    obj = FakeSegmentObj()
    _bare_segment(obj).setProperties(obj)

    for name in (
        "OwnerNetworkName", "SegmentKey", "SourceObjectName", "SourceIndex",
        "StartNode", "EndNode", "PathKind", "AnalysisJson",
        "TypeSchemaPropertyNames", "StartTrimPlaneJson", "EndTrimPlaneJson",
    ):
        assert obj._editor_modes[name] == 2, name

    for name in ("StartPoint", "EndPoint", "CenterlineLength", "Family", "Profile"):
        assert obj._editor_modes[name] == 1, name


def test_apply_type_schema_removes_stale_non_core_properties(monkeypatch):
    circular_properties = [
        HVACPropertyDef(name="Diameter", prop_type="App::PropertyLength", group="Dimensions", description="", default=100.0),
        HVACPropertyDef(name="Thickness", prop_type="App::PropertyLength", group="Dimensions", description="", default=0.8),
        HVACPropertyDef(name="ShowFlange1", prop_type="App::PropertyBool", group="Options", description="", default=False),
    ]
    rectangular_properties = [
        HVACPropertyDef(name="Width", prop_type="App::PropertyLength", group="Dimensions", description="", default=100.0),
        HVACPropertyDef(name="Height", prop_type="App::PropertyLength", group="Dimensions", description="", default=100.0),
    ]

    obj = FakeSegmentObj()
    obj.LibraryId = "smacna"
    obj.TypeId = "circular_straight"
    # Diameter/Width/Height are permanent core properties, added unconditionally
    # by setProperties -- simulate that here rather than going through the
    # full setProperties (which needs a real hvaclib active-library lookup).
    for name in ("Diameter", "Width", "Height"):
        obj.addProperty("App::PropertyLength", name, "Dimensions", "")

    registry = _FakeRegistry(_FakeTypeDef(circular_properties))
    _patch_registry(monkeypatch, registry)
    ds = _bare_segment(obj)
    ds.applyTypeSchema()

    assert {"Thickness", "ShowFlange1"} <= set(obj.PropertiesList)

    # Switch to a type that doesn't use Thickness/ShowFlange1 at all.
    obj.TypeId = "rectangular_straight"
    registry._type_def = _FakeTypeDef(rectangular_properties)
    ds.applyTypeSchema()

    assert "Thickness" not in obj.PropertiesList
    assert "ShowFlange1" not in obj.PropertiesList
    assert not hasattr(obj, "Thickness")

    # Diameter/Width/Height must never be removed, even though the new type
    # doesn't declare Diameter.
    assert {"Diameter", "Width", "Height"} <= set(obj.PropertiesList)


def test_apply_type_schema_never_removes_core_dimensions_via_editor_mode(monkeypatch):
    rectangular_properties = [
        HVACPropertyDef(name="Width", prop_type="App::PropertyLength", group="Dimensions", description="", default=100.0),
        HVACPropertyDef(name="Height", prop_type="App::PropertyLength", group="Dimensions", description="", default=100.0),
    ]

    obj = FakeSegmentObj()
    obj.LibraryId = "smacna"
    obj.TypeId = "rectangular_straight"
    for name in ("Diameter", "Width", "Height"):
        obj.addProperty("App::PropertyLength", name, "Dimensions", "")

    _patch_registry(monkeypatch, _FakeRegistry(_FakeTypeDef(rectangular_properties)))
    ds = _bare_segment(obj)
    ds.applyTypeSchema()

    assert "Diameter" in obj.PropertiesList
    # Diameter isn't part of this type's schema -> read-only, not removed.
    assert obj._editor_modes["Diameter"] == 1
    assert obj._editor_modes["Width"] == 0


# ----------------------------------------------------------------------
# execute(): applying reactive/read-only computed_properties -- the same
# generic mechanism DuctComponent.execute() uses (see
# test_component_reactive_properties.py), closing the gap where
# DuctSegment.execute() built geometry but never synced computed_properties
# back onto the object.
# ----------------------------------------------------------------------

class _FakeExecuteRegistry:
    def __init__(self, type_def, geometry_result):
        self._type_def = type_def
        self._geometry_result = geometry_result

    def resolve_type(self, lib_id, type_id):
        return self._type_def

    def resolve_params(self, type_def, obj=None):
        return {}

    def build_geometry(self, lib_id, type_def, context):
        return geometry_result_mod.normalize(self._geometry_result)


def _give_single_implicit_layer(obj):
    obj.ConstructionLayerIds = [geometry_result_mod.LEGACY_SHAPE_LAYER_ID]
    obj.addProperty("Part::PropertyPartShape", "Layer_shape_Shape", "Geometry", "")
    obj.addProperty("Materials::PropertyMaterial", "Layer_shape_Material", "Materials", "")


def test_execute_applies_computed_properties_to_matching_object_property(monkeypatch):
    # No live source edge -- execute() falls back to obj's own
    # StartPoint/EndPoint, same as an as-yet-unrouted segment.
    monkeypatch.setattr(segment_mod.DuctSegment, "resolveSourceEdge", lambda self: None)

    properties = [
        HVACPropertyDef(
            name="angle", prop_type="App::PropertyAngle", group="Bend geometry",
            description="", default=0.0, editor_mode=1,
        ),
    ]
    obj = FakeSegmentObj()
    obj.addProperty("App::PropertyAngle", "angle", "Bend geometry", "")
    obj.LibraryId = "smacna"
    obj.TypeId = "circular_straight"
    obj.StartPoint = FreeCAD.Vector(0.0, 0.0, 0.0)
    obj.EndPoint = FreeCAD.Vector(1000.0, 0.0, 0.0)
    obj.Label = "Segment"
    _give_single_implicit_layer(obj)

    geometry_result = {
        "shape": object(),
        "connection_lengths": [],
        "computed_properties": {"angle": 42.0, "not_a_real_property": 99.0},
    }
    _patch_registry(monkeypatch, _FakeExecuteRegistry(_FakeTypeDef(properties), geometry_result))

    ds = _bare_segment(obj)
    ds.execute(obj)

    assert obj.angle == 42.0
    assert not hasattr(obj, "not_a_real_property")
    assert obj.Layer_shape_Shape is geometry_result["shape"]
