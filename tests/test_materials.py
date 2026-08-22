"""
Tests for utils/materials.py: registering this addon's own .FCMat cards
with FreeCAD's native Material subsystem, and the small read-only helpers
core/ code uses to pull physical/appearance values off a
Materials::PropertyMaterial value -- see ARCHITECTURE.md's "Component
geometry & materials" section.
"""

import math

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Materials/PySide stubs

from freecad.HVAC.utils import materials as hvac_materials


class FakeParam:
    """Stand-in for the FreeCAD.ParamGet(...) group object SetString is called on."""

    def __init__(self):
        self.values = {}

    def SetString(self, key, value):
        self.values[key] = value


class FakeQuantity:
    """Stand-in for Base.Quantity -- getPhysicalValue's real return type."""

    def __init__(self, value):
        self.Value = value


class FakeMaterial:
    """Stand-in for a Materials.Material value (what obj.CasingMaterial
    actually is at runtime) -- both the "unassigned" and "real" shapes."""

    def __init__(self, name="", physical=None, appearance=None):
        self.Name = name
        self._physical = physical or {}
        self._appearance = appearance or {}

    def hasPhysicalProperty(self, name):
        return name in self._physical

    def getPhysicalValue(self, name):
        return self._physical.get(name)

    def hasAppearanceProperty(self, name):
        return name in self._appearance

    def getAppearanceValue(self, name):
        return self._appearance.get(name)


# ----------------------------------------------------------------------
# register_material_resources
# ----------------------------------------------------------------------

def test_register_material_resources_sets_module_dir_to_shipped_folder(monkeypatch):
    captured = {}

    def fake_param_get(path):
        captured["path"] = path
        param = FakeParam()
        captured["param"] = param
        return param

    monkeypatch.setattr(hvac_materials.FreeCAD, "ParamGet", fake_param_get)

    hvac_materials.register_material_resources()

    assert captured["path"] == (
        "User parameter:BaseApp/Preferences/Mod/Material/Resources/Modules/FreeCAD-HVAC"
    )
    materials_path = captured["param"].values["ModuleDir"]
    assert materials_path.endswith(("Resources/Materials", "Resources\\Materials"))
    import os
    assert os.path.isdir(materials_path)


def test_register_material_resources_is_a_noop_if_resources_dir_missing(monkeypatch):
    monkeypatch.setattr(hvac_materials.hvaclib, "get_materials_base_path", lambda: "/no/such/dir")
    called = []
    monkeypatch.setattr(hvac_materials.FreeCAD, "ParamGet", lambda path: called.append(path))

    hvac_materials.register_material_resources()

    assert called == []


# ----------------------------------------------------------------------
# get_physical_value
# ----------------------------------------------------------------------

def test_get_physical_value_returns_plain_float_from_quantity():
    material = FakeMaterial(name="Steel", physical={"Density": FakeQuantity(7.86e-06)})
    assert hvac_materials.get_physical_value(material, "Density") == 7.86e-06


def test_get_physical_value_none_for_unassigned_material():
    material = FakeMaterial(name="", physical={"Density": FakeQuantity(1.0)})
    assert hvac_materials.get_physical_value(material, "Density") is None


def test_get_physical_value_none_for_missing_material():
    assert hvac_materials.get_physical_value(None, "Density") is None


def test_get_physical_value_none_for_property_not_modeled():
    material = FakeMaterial(name="Steel", physical={})
    assert hvac_materials.get_physical_value(material, "ThermalConductivity") is None


def test_get_physical_value_none_for_nan_value():
    # FreeCAD reports a modeled-but-never-set property as NaN, not missing.
    material = FakeMaterial(name="Steel", physical={"ThermalConductivity": FakeQuantity(float("nan"))})
    assert hvac_materials.get_physical_value(material, "ThermalConductivity") is None


# ----------------------------------------------------------------------
# get_view_appearance
# ----------------------------------------------------------------------

def test_get_view_appearance_builds_material_struct_from_diffuse_color(monkeypatch):
    built = {}

    class FakeAppMaterial:
        def __init__(self):
            built["instance"] = self

    monkeypatch.setattr(hvac_materials.FreeCAD, "Material", FakeAppMaterial)

    material = FakeMaterial(
        name="Glass Wool",
        appearance={
            "DiffuseColor": "(0.9200, 0.8700, 0.5500, 1.0)",
            "Transparency": "0.0",
        },
    )
    appearance = hvac_materials.get_view_appearance(material)

    assert appearance is built["instance"]
    assert appearance.DiffuseColor == (0.92, 0.87, 0.55, 1.0)
    assert appearance.Transparency == 0.0


def test_get_view_appearance_none_for_unassigned_material():
    material = FakeMaterial(name="", appearance={"DiffuseColor": "(1,1,1,1)"})
    assert hvac_materials.get_view_appearance(material) is None


def test_get_view_appearance_none_without_diffuse_color():
    material = FakeMaterial(name="Steel", appearance={})
    assert hvac_materials.get_view_appearance(material) is None


def test_get_view_appearance_none_for_missing_material():
    assert hvac_materials.get_view_appearance(None) is None


def test_get_view_appearance_ignores_unparseable_secondary_fields(monkeypatch):
    monkeypatch.setattr(hvac_materials.FreeCAD, "Material", lambda: type("M", (), {})())

    material = FakeMaterial(
        name="Odd",
        appearance={"DiffuseColor": "(0.1, 0.2, 0.3, 1.0)", "Shininess": "not-a-number"},
    )
    appearance = hvac_materials.get_view_appearance(material)
    assert appearance.DiffuseColor == (0.1, 0.2, 0.3, 1.0)
    assert not hasattr(appearance, "Shininess")
