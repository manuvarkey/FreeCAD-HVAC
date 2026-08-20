"""
Focused tests for the small DuctJunction framework hooks added to support
reactive/read-only display properties (e.g. through_elbow_rectangular's
angle/d_h_axis_02): applyTypeSchema honoring HVACPropertyDef.editor_mode,
execute() applying a geometry backend's optional result['computed_properties'],
and applyTypeSchema removing properties left over from a previously-selected
type that the newly-selected type doesn't declare.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs
from conftest import FakeVector

from freecad.HVAC.core import Junction as junction_mod
from freecad.HVAC.library.Library import HVACPropertyDef


class FakeDuctObj:
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
    def __init__(self, type_def, geometry_result=None):
        self._type_def = type_def
        self._geometry_result = geometry_result

    def resolve_type(self, lib_id, type_id):
        return self._type_def

    def resolve_params(self, type_def, obj=None):
        return {}

    def build_geometry(self, lib_id, type_def, context):
        return self._geometry_result


def _patch_registry(monkeypatch, registry):
    monkeypatch.setattr(
        junction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: registry),
    )


def _bare_junction(obj):
    dj = junction_mod.DuctJunction.__new__(junction_mod.DuctJunction)
    dj.Object = obj
    return dj


def test_apply_type_schema_honors_editor_mode(monkeypatch):
    properties = [
        HVACPropertyDef(
            name="w_side_1", prop_type="App::PropertyLength", group="Side 1",
            description="", default=0.0, editor_mode=1,
        ),
        HVACPropertyDef(
            name="r_axis", prop_type="App::PropertyLength", group="Bend geometry",
            description="", default=300.0, editor_mode=0,
        ),
    ]
    obj = FakeDuctObj()
    obj.LibraryId = "smacna"
    obj.TypeId = "through_elbow_rectangular"

    _patch_registry(monkeypatch, _FakeRegistry(_FakeTypeDef(properties)))

    dj = _bare_junction(obj)
    dj.applyTypeSchema()

    assert obj._editor_modes["w_side_1"] == 1
    assert obj._editor_modes["r_axis"] == 0
    assert obj.w_side_1 == 0.0
    assert obj.r_axis == 300.0


def test_execute_applies_computed_properties_to_matching_object_property(monkeypatch):
    properties = [
        HVACPropertyDef(
            name="angle", prop_type="App::PropertyAngle", group="Bend geometry",
            description="", default=0.0, editor_mode=1,
        ),
    ]
    obj = FakeDuctObj()
    obj.addProperty("App::PropertyAngle", "angle", "Bend geometry", "")
    obj.CenterPoint = FakeVector(0.0, 0.0, 0.0)
    obj.LibraryId = "smacna"
    obj.TypeId = "through_elbow_rectangular"
    obj.AnalysisJson = "{}"
    obj.ConnectionLengthsJson = "[]"
    obj.Label = "Junction"

    geometry_result = {
        "shape": object(),
        "connection_lengths": [],
        # "not_a_real_property" mirrors a computed value the object doesn't
        # (yet) declare a property for -- execute() must silently skip it
        # rather than error, since applyTypeSchema is what adds properties.
        "computed_properties": {"angle": 42.0, "not_a_real_property": 99.0},
    }
    _patch_registry(monkeypatch, _FakeRegistry(_FakeTypeDef(properties), geometry_result))

    dj = _bare_junction(obj)
    dj.execute(obj)

    assert obj.angle == 42.0
    assert not hasattr(obj, "not_a_real_property")
    assert obj.Shape is geometry_result["shape"]


def test_apply_type_schema_removes_properties_from_a_previous_type(monkeypatch):
    old_properties = [
        HVACPropertyDef(
            name="r_axis", prop_type="App::PropertyLength", group="Bend geometry",
            description="", default=300.0,
        ),
        HVACPropertyDef(
            name="flange_height", prop_type="App::PropertyLength", group="Flanges",
            description="", default=25.0,
        ),
    ]
    new_properties = [
        HVACPropertyDef(
            name="NeckSize", prop_type="App::PropertyLength", group="Dimensions",
            description="", default=150.0,
        ),
    ]
    obj = FakeDuctObj()
    obj.LibraryId = "smacna"
    obj.TypeId = "through_elbow_rectangular"

    registry = _FakeRegistry(_FakeTypeDef(old_properties))
    _patch_registry(monkeypatch, registry)
    dj = _bare_junction(obj)
    dj.applyTypeSchema()

    assert {"r_axis", "flange_height"} <= set(obj.PropertiesList)

    # Simulate switching TypeId to a different fitting whose schema doesn't
    # declare r_axis/flange_height at all.
    obj.TypeId = "end_diffuser_generic"
    registry._type_def = _FakeTypeDef(new_properties)
    dj.applyTypeSchema()

    assert "r_axis" not in obj.PropertiesList
    assert "flange_height" not in obj.PropertiesList
    assert not hasattr(obj, "r_axis")
    assert "NeckSize" in obj.PropertiesList
