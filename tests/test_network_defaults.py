"""
Focused tests for DuctNetwork's own default-value properties added for the
casing/insulation material feature: DefaultCasingMaterial/
DefaultInsulationMaterial (Materials::PropertyMaterial, defaulted to this
addon's own Galvanized Steel/Nitrile Rubber cards) and
DefaultInsulationThickness -- plus the Network.applyNetworkTypeDefaults()/
applyMaterialSelection() callbacks that read/write them. See
ARCHITECTURE.md's "Component geometry & materials" section.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Materials/MatGui/PySide stubs

from freecad.HVAC.core import Network as network_mod


class FakeMaterial:
    def __init__(self, name):
        self.Name = name


class FakeNetworkObj:
    """
    Minimal stand-in for a FreeCAD DocumentObject, with Base/Geometry/
    Topology already set to dummy truthy values and Document=None, so
    DuctNetwork.setProperties()'s managed-folder creation takes its
    "already exists" branch instead of needing a real document.
    """

    def __init__(self):
        self.Document = None
        self.Name = "Network0"
        self.PropertiesList = []
        self._editor_modes = {}
        self._prop_attrs = {}
        # Pre-existing "folders" so setProperties() skips DuctManagedFolder.create().
        self.Base = object()
        self.Geometry = object()
        self.Topology = object()

    def addProperty(self, prop_type, name, group, description, attr=0):
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
            setattr(self, name, None)
        self._prop_attrs[name] = attr
        return self

    def setEditorMode(self, name, mode):
        self._editor_modes[name] = mode

    def touch(self):
        pass


def _bare_network():
    return network_mod.DuctNetwork.__new__(network_mod.DuctNetwork)


def _patch_library_lookups(monkeypatch):
    monkeypatch.setattr(
        network_mod.hvaclib.HVACLibraryService, "get_active_hvac_library", staticmethod(lambda: None)
    )


def test_setproperties_adds_default_material_and_insulation_thickness_properties(monkeypatch):
    _patch_library_lookups(monkeypatch)
    steel = FakeMaterial("Galvanized-Steel")
    wool = FakeMaterial("Nitrile-Rubber")

    def fake_get_material_by_uuid(uuid):
        return {
            network_mod.hvac_materials.GALVANIZED_STEEL_UUID: steel,
            network_mod.hvac_materials.NITRILE_RUBBER_UUID: wool,
        }.get(uuid)

    monkeypatch.setattr(network_mod.hvac_materials, "get_material_by_uuid", fake_get_material_by_uuid)

    obj = FakeNetworkObj()
    _bare_network().setProperties(obj)

    assert obj._prop_attrs["DefaultCasingMaterial"] == 16  # Prop_NoRecompute
    assert obj._prop_attrs["DefaultInsulationMaterial"] == 16
    assert "DefaultInsulationThickness" in obj.PropertiesList

    assert obj.DefaultCasingMaterial is steel
    assert obj.DefaultInsulationMaterial is wool
    assert obj.DefaultInsulationThickness == 25.0


def test_setproperties_leaves_manually_assigned_default_materials_alone(monkeypatch):
    _patch_library_lookups(monkeypatch)
    monkeypatch.setattr(network_mod.hvac_materials, "get_material_by_uuid", lambda uuid: FakeMaterial("SHOULD_NOT_BE_USED"))

    obj = FakeNetworkObj()
    _bare_network().setProperties(obj)

    custom = FakeMaterial("Aluminium")
    obj.DefaultCasingMaterial = custom

    # A second setProperties() call (e.g. onDocumentRestored) must not
    # clobber an already-assigned default.
    _bare_network().setProperties(obj)
    assert obj.DefaultCasingMaterial is custom


def test_setproperties_defaults_missing_material_gracefully(monkeypatch):
    # register_material_resources() hasn't run / the UUID isn't known yet --
    # must not raise, just leave the property unassigned.
    _patch_library_lookups(monkeypatch)
    monkeypatch.setattr(network_mod.hvac_materials, "get_material_by_uuid", lambda uuid: None)

    obj = FakeNetworkObj()
    _bare_network().setProperties(obj)  # must not raise

    assert obj.DefaultCasingMaterial is None


def test_setproperties_hides_internal_managed_folder_links(monkeypatch):
    """
    Base/Geometry/Topology are plain App::PropertyLink pointers to this
    network's own internal managed folders (see DuctManagedFolder) -- kept
    (never removed) so the addon's own code can still reach them, but
    hidden from the property editor since a user never needs to read or
    edit them directly (the folders themselves stay visible in the tree).
    """
    _patch_library_lookups(monkeypatch)

    obj = FakeNetworkObj()
    _bare_network().setProperties(obj)

    assert obj._editor_modes["Base"] == 2
    assert obj._editor_modes["Geometry"] == 2
    assert obj._editor_modes["Topology"] == 2


def test_apply_network_type_defaults_writes_insulation_thickness_and_materials():
    obj = FakeNetworkObj()
    obj.DefaultInsulationThickness = 0.0
    obj.DefaultCasingMaterial = None
    obj.DefaultInsulationMaterial = None

    steel = FakeMaterial("Galvanized-Steel")

    changed = network_mod.DuctNetwork.applyNetworkTypeDefaults(
        obj,
        default_insulation_thickness=40.0,
        default_casing_material=steel,
    )

    assert changed is True
    assert obj.DefaultInsulationThickness == 40.0
    assert obj.DefaultCasingMaterial is steel
    # insulation_material wasn't passed (None) -- must stay untouched.
    assert obj.DefaultInsulationMaterial is None


def test_apply_network_type_defaults_omitted_kwargs_are_a_noop():
    obj = FakeNetworkObj()
    obj.DefaultInsulationThickness = 25.0
    obj.DefaultCasingMaterial = FakeMaterial("Existing")
    obj.DefaultInsulationMaterial = FakeMaterial("Existing2")

    changed = network_mod.DuctNetwork.applyNetworkTypeDefaults(obj)

    assert changed is False
    assert obj.DefaultInsulationThickness == 25.0
    assert obj.DefaultCasingMaterial.Name == "Existing"
    assert obj.DefaultInsulationMaterial.Name == "Existing2"


# ----------------------------------------------------------------------
# applyMaterialSelection
# ----------------------------------------------------------------------

class FakeDuctObj:
    def __init__(self, casing=None, insulation=None):
        self.CasingMaterial = casing
        self.InsulationMaterial = insulation


def test_apply_material_selection_sets_only_the_given_properties():
    obj1 = FakeDuctObj(casing=FakeMaterial("Old1"), insulation=FakeMaterial("Old2"))
    obj2 = FakeDuctObj(casing=FakeMaterial("Old3"), insulation=FakeMaterial("Old4"))
    new_insulation = FakeMaterial("New-Insulation")

    network_mod.DuctNetwork.applyMaterialSelection(
        [obj1, obj2], insulation_material=new_insulation
    )

    assert obj1.CasingMaterial.Name == "Old1"  # untouched (casing_material=None)
    assert obj1.InsulationMaterial is new_insulation
    assert obj2.CasingMaterial.Name == "Old3"
    assert obj2.InsulationMaterial is new_insulation


def test_apply_material_selection_skips_none_objects_and_missing_properties():
    class NoMaterialProps:
        pass

    obj = NoMaterialProps()
    network_mod.DuctNetwork.applyMaterialSelection(
        [None, obj], casing_material=FakeMaterial("X")
    )  # must not raise
    assert not hasattr(obj, "CasingMaterial")


def test_apply_material_selection_both_none_is_a_true_noop():
    obj = FakeDuctObj(casing=FakeMaterial("Keep1"), insulation=FakeMaterial("Keep2"))
    network_mod.DuctNetwork.applyMaterialSelection([obj])
    assert obj.CasingMaterial.Name == "Keep1"
    assert obj.InsulationMaterial.Name == "Keep2"
