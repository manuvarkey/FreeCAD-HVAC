"""
Tests for the component-geometry/material property contract added to
DuctSegment/DuctComponent (CasingShape/InsulationShape/CasingMaterial/
InsulationMaterial), and that DuctJunction stays geometry-free -- see
ARCHITECTURE.md's "Component geometry & materials" section.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core import Segment as segment_mod
from freecad.HVAC.core import Component as component_mod
from freecad.HVAC.core import Junction as junction_mod


class FakeObj:
    """Minimal stand-in for a FreeCAD DocumentObject's dynamic-property API,
    additionally recording each property's declared FreeCAD type so tests
    can check it (the reactive-property test fakes elsewhere don't need
    this, since they never assert on prop_type)."""

    def __init__(self):
        self.PropertiesList = []
        self._prop_types = {}
        self._prop_attrs = {}
        self._editor_modes = {}

    def addProperty(self, prop_type, name, group, description, attr=0):
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
            setattr(self, name, None)
        self._prop_types[name] = prop_type
        self._prop_attrs[name] = attr
        return self

    def removeProperty(self, name):
        if name in self.PropertiesList:
            self.PropertiesList.remove(name)
        if hasattr(self, name):
            delattr(self, name)
        return True

    def setEditorMode(self, name, mode):
        self._editor_modes[name] = mode


def _bare_segment(obj):
    ds = segment_mod.DuctSegment.__new__(segment_mod.DuctSegment)
    ds.Object = obj
    return ds


def _bare_component(obj):
    dc = component_mod.DuctComponent.__new__(component_mod.DuctComponent)
    dc.Object = obj
    return dc


def test_segment_setproperties_adds_casing_and_insulation_geometry_and_materials(monkeypatch):
    monkeypatch.setattr(
        segment_mod.hvaclib.HVACLibraryService, "get_active_hvac_library", staticmethod(lambda: None)
    )
    obj = FakeObj()
    ds = _bare_segment(obj)
    ds.setProperties(obj)

    assert obj._prop_types["CasingShape"] == "Part::PropertyPartShape"
    assert obj._prop_types["InsulationShape"] == "Part::PropertyPartShape"
    assert obj._prop_types["CasingMaterial"] == "Materials::PropertyMaterial"
    assert obj._prop_types["InsulationMaterial"] == "Materials::PropertyMaterial"

    # Shapes are read-only in the property editor; materials use FreeCAD's
    # own native material editor, so they're never read-only here.
    assert obj._editor_modes["CasingShape"] == 1
    assert obj._editor_modes["InsulationShape"] == 1
    assert "CasingMaterial" not in obj._editor_modes
    assert "InsulationMaterial" not in obj._editor_modes

    # Prop_NoRecompute (16): picking a material never changes this object's
    # own geometry, only its ViewProvider's rendered appearance -- it must
    # not force a recompute.
    assert obj._prop_attrs["CasingMaterial"] == 16
    assert obj._prop_attrs["InsulationMaterial"] == 16


def test_component_setproperties_adds_casing_and_insulation_geometry_and_materials(monkeypatch):
    monkeypatch.setattr(
        component_mod.hvaclib.HVACLibraryService, "get_active_hvac_library", staticmethod(lambda: None)
    )
    obj = FakeObj()
    dc = _bare_component(obj)
    dc.setProperties(obj)

    assert obj._prop_types["CasingShape"] == "Part::PropertyPartShape"
    assert obj._prop_types["InsulationShape"] == "Part::PropertyPartShape"
    assert obj._prop_types["CasingMaterial"] == "Materials::PropertyMaterial"
    assert obj._prop_types["InsulationMaterial"] == "Materials::PropertyMaterial"

    assert obj._editor_modes["CasingShape"] == 1
    assert obj._editor_modes["InsulationShape"] == 1
    assert "CasingMaterial" not in obj._editor_modes
    assert "InsulationMaterial" not in obj._editor_modes

    assert obj._prop_attrs["CasingMaterial"] == 16
    assert obj._prop_attrs["InsulationMaterial"] == 16


def test_junction_setproperties_has_no_geometry_or_material_properties():
    # DuctJunction is purely logical -- physical fitting geometry/materials
    # belong to its DuctComponent children, never to the junction itself.
    obj = FakeObj()
    dj = junction_mod.DuctJunction.__new__(junction_mod.DuctJunction)
    dj.Object = obj
    dj.setProperties(obj)

    for name in ("CasingShape", "InsulationShape", "CasingMaterial", "InsulationMaterial"):
        assert name not in obj.PropertiesList


def test_setproperties_never_creates_a_document_object_for_materials(monkeypatch):
    """
    Regression guard for the native-material migration: a
    Materials::PropertyMaterial value lives directly on CasingMaterial/
    InsulationMaterial (see the type-assertions above) -- setProperties()
    must never create a separate per-element material document object
    (the old App::MaterialObjectPython/App::PropertyLinkGlobal pattern).
    FakeObj has no addObject()/doc-creation method at all, so any such call
    would raise AttributeError rather than silently succeed.
    """
    monkeypatch.setattr(
        segment_mod.hvaclib.HVACLibraryService, "get_active_hvac_library", staticmethod(lambda: None)
    )
    obj = FakeObj()
    assert not hasattr(obj, "addObject")
    _bare_segment(obj).setProperties(obj)  # must not raise
