"""
Tests for core/Construction.py: the semantic construction query API
(Construction.layers_with_role/flow_surface/structural_layers/
thermal_layers/acoustic_layers), and construction_for() which builds one
from a real object's own Layer_<id>_Shape/Layer_<id>_Material properties
plus its resolved type-def's declared construction. Downstream modules must
only ever query by role, never by a library-chosen layer id -- these tests
exercise arbitrary layer counts and multiple roles on one layer to make
sure that holds.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.core import Construction as construction_mod
from freecad.HVAC.library.construction import ConstructionLayerDef


class FakeObj:
    def __init__(self, library_id, type_id, layer_ids, shapes=None, materials=None):
        self.LibraryId = library_id
        self.TypeId = type_id
        self.ConstructionLayerIds = list(layer_ids)
        for layer_id in layer_ids:
            setattr(self, "Layer_{}_Shape".format(layer_id), (shapes or {}).get(layer_id))
            setattr(self, "Layer_{}_Material".format(layer_id), (materials or {}).get(layer_id))


class _FakeTypeDef:
    def __init__(self, construction):
        self.construction = construction


class _FakeRegistry:
    def __init__(self, type_def):
        self._type_def = type_def

    def resolve_type(self, lib_id, type_id):
        return self._type_def


def _patch_registry(monkeypatch, type_def):
    monkeypatch.setattr(
        construction_mod.hvaclib.HVACLibraryService,
        "get_hvac_library_registry",
        staticmethod(lambda: _FakeRegistry(type_def)),
    )


def test_single_layer_duct_flow_surface_is_also_structural(monkeypatch):
    _patch_registry(monkeypatch, _FakeTypeDef([
        ConstructionLayerDef(id="casing", roles=["flow_surface", "structural_shell"]),
    ]))
    casing_shape, casing_material = object(), object()
    obj = FakeObj("builtin_basic", "circular_straight", ["casing"],
                  shapes={"casing": casing_shape}, materials={"casing": casing_material})

    construction = construction_mod.construction_for(obj)

    flow = construction.flow_surface()
    assert flow.id == "casing"
    assert flow.shape is casing_shape
    assert flow.material is casing_material
    assert construction.structural_layers() == [flow]
    assert construction.thermal_layers() == []
    assert construction.acoustic_layers() == []


def test_casing_and_insulation_duct_separates_roles(monkeypatch):
    _patch_registry(monkeypatch, _FakeTypeDef([
        ConstructionLayerDef(id="casing", roles=["flow_surface", "structural_shell"]),
        ConstructionLayerDef(id="insulation", roles=["thermal_insulation"]),
    ]))
    obj = FakeObj("smacna", "circular_straight", ["casing", "insulation"])

    construction = construction_mod.construction_for(obj)

    assert construction.flow_surface().id == "casing"
    assert [layer.id for layer in construction.structural_layers()] == ["casing"]
    assert [layer.id for layer in construction.thermal_layers()] == ["insulation"]
    assert construction.acoustic_layers() == []


def test_acoustic_three_layer_duct_liner_is_flow_surface_not_structural(monkeypatch):
    _patch_registry(monkeypatch, _FakeTypeDef([
        ConstructionLayerDef(id="jacket", roles=["outer_jacket", "structural_shell"]),
        ConstructionLayerDef(id="absorber", roles=["acoustic_absorber"]),
        ConstructionLayerDef(id="liner", roles=["flow_surface", "acoustic_liner"]),
    ]))
    obj = FakeObj("smacna", "acoustic_circular_straight", ["jacket", "absorber", "liner"])

    construction = construction_mod.construction_for(obj)

    assert construction.flow_surface().id == "liner"
    assert [layer.id for layer in construction.structural_layers()] == ["jacket"]
    assert {layer.id for layer in construction.acoustic_layers()} == {"absorber", "liner"}
    assert construction.thermal_layers() == []


def test_layers_with_multiple_roles_are_returned_from_every_matching_query(monkeypatch):
    _patch_registry(monkeypatch, _FakeTypeDef([
        ConstructionLayerDef(id="liner", roles=["flow_surface", "acoustic_liner", "fire_protection"]),
    ]))
    obj = FakeObj("smacna", "acoustic_circular_straight", ["liner"])

    construction = construction_mod.construction_for(obj)
    liner = construction.layer("liner")

    assert liner in construction.layers_with_role("fire_protection")
    assert liner in construction.acoustic_layers()
    assert liner is construction.flow_surface()
    assert liner.has_role("acoustic_liner")
    assert not liner.has_role("thermal_insulation")


def test_construction_for_a_not_yet_migrated_type_has_no_roles(monkeypatch):
    # Empty construction list -- construction_for() still builds one layer
    # per obj.ConstructionLayerIds (populated by apply_construction_schema's
    # single-implicit-layer fallback), just with no roles at all.
    _patch_registry(monkeypatch, _FakeTypeDef([]))
    obj = FakeObj("builtin_basic", "rectangular_straight", ["shape"])

    construction = construction_mod.construction_for(obj)

    assert construction.flow_surface() is None
    assert construction.structural_layers() == []
    assert construction.layer("shape").roles == []


def test_construction_for_object_with_no_layers_at_all(monkeypatch):
    _patch_registry(monkeypatch, _FakeTypeDef([]))
    obj = FakeObj("", "", [])

    construction = construction_mod.construction_for(obj)

    assert construction.layers() == []
    assert construction.flow_surface() is None
