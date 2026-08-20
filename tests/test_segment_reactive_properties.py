"""
Focused tests for DuctSegment.applyTypeSchema's stale-property cleanup:
switching TypeId to a model with a different property schema must drop
properties the new type doesn't declare, while Diameter/Width/Height stay
(they're permanent core dimensional properties shared across every segment
type -- see the comment in Segment.py's applyTypeSchema).
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core import Segment as segment_mod
from freecad.HVAC.library.Library import HVACPropertyDef


class FakeSegmentObj:
    """Minimal stand-in for a FreeCAD DocumentObject's dynamic-property API."""

    def __init__(self):
        self.PropertiesList = []
        self._editor_modes = {}

    def addProperty(self, prop_type, name, group, description):
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
