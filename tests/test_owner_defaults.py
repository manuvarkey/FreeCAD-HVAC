"""
Focused tests for DuctSegment.applyOwnerDefaults()/DuctComponent.
applyOwnerDefaults(): a newly-created segment/component picks up its owner
network's DefaultCasingMaterial/DefaultInsulationMaterial (and, for
segments, DefaultInsulationThickness) unless it already has its own value
-- see ARCHITECTURE.md's "Component geometry & materials" section and
Network.py's DefaultCasingMaterial/DefaultInsulationMaterial/
DefaultInsulationThickness properties.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Materials/MatGui/PySide stubs

from freecad.HVAC.core import Segment as segment_mod
from freecad.HVAC.core import Component as component_mod
from freecad.HVAC.utils import hvaclib


class FakeMaterial:
    def __init__(self, name):
        self.Name = name


class FakeOwner:
    def __init__(self, casing_material=None, insulation_material=None, insulation_thickness=0.0):
        self.DefaultCasingMaterial = casing_material
        self.DefaultInsulationMaterial = insulation_material
        self.DefaultInsulationThickness = insulation_thickness
        self.DefaultAttachment = "Center"
        self.DefaultOffset = None  # falls back to FreeCAD.Vector(0,0,0) inside applyOwnerDefaults
        self.DefaultDiameter = 100.0
        self.DefaultWidth = 100.0
        self.DefaultHeight = 100.0


class FakeSegmentObj:
    def __init__(self, casing_material=None, insulation_material=None, insulation_thickness=0.0):
        self.CasingMaterial = casing_material
        self.InsulationMaterial = insulation_material
        self.InsulationThickness = insulation_thickness
        self.Diameter = 0.0
        self.Width = 0.0
        self.Height = 0.0
        self.ProfileXAxis = None
        self.Attachment = None
        self.Offset = None


def _bare_segment():
    return segment_mod.DuctSegment.__new__(segment_mod.DuctSegment)


def test_segment_applies_owner_default_materials_when_unassigned():
    owner = FakeOwner(
        casing_material=FakeMaterial("Galvanized-Steel"),
        insulation_material=FakeMaterial("Nitrile-Rubber"),
        insulation_thickness=25.0,
    )
    obj = FakeSegmentObj()

    _bare_segment().applyOwnerDefaults(obj, owner)

    assert obj.CasingMaterial.Name == "Galvanized-Steel"
    assert obj.InsulationMaterial.Name == "Nitrile-Rubber"
    assert obj.InsulationThickness == 25.0


def test_segment_never_overwrites_its_own_already_assigned_materials():
    owner = FakeOwner(
        casing_material=FakeMaterial("Galvanized-Steel"),
        insulation_material=FakeMaterial("Nitrile-Rubber"),
        insulation_thickness=25.0,
    )
    obj = FakeSegmentObj(
        casing_material=FakeMaterial("Aluminium"),
        insulation_material=FakeMaterial("Glass-Wool"),
        insulation_thickness=40.0,
    )

    _bare_segment().applyOwnerDefaults(obj, owner)

    assert obj.CasingMaterial.Name == "Aluminium"
    assert obj.InsulationMaterial.Name == "Glass-Wool"
    assert obj.InsulationThickness == 40.0


def test_segment_owner_defaults_tolerate_unset_network_defaults():
    # A network created before this feature (or with materials never
    # resolved) has DefaultCasingMaterial/DefaultInsulationMaterial as None
    # -- must not raise, just leave the segment's own materials unset.
    owner = FakeOwner(casing_material=None, insulation_material=None)
    obj = FakeSegmentObj()

    _bare_segment().applyOwnerDefaults(obj, owner)  # must not raise

    assert obj.CasingMaterial is None
    assert obj.InsulationMaterial is None


def test_segment_owner_defaults_noop_without_an_owner():
    obj = FakeSegmentObj()
    _bare_segment().applyOwnerDefaults(obj, None)  # must not raise
    assert obj.CasingMaterial is None


# ----------------------------------------------------------------------
# DuctComponent
# ----------------------------------------------------------------------

class FakeComponentObj:
    def __init__(self, casing_material=None, insulation_material=None):
        self.CasingMaterial = casing_material
        self.InsulationMaterial = insulation_material


def _bare_component():
    return component_mod.DuctComponent.__new__(component_mod.DuctComponent)


def test_component_applies_owner_default_materials_when_unassigned(monkeypatch):
    owner = FakeOwner(
        casing_material=FakeMaterial("Galvanized-Steel"),
        insulation_material=FakeMaterial("Nitrile-Rubber"),
    )
    monkeypatch.setattr(hvaclib, "getOwnerNetwork", lambda parent: owner)

    obj = FakeComponentObj()
    _bare_component().applyOwnerDefaults(obj, parent_junction=object())

    assert obj.CasingMaterial.Name == "Galvanized-Steel"
    assert obj.InsulationMaterial.Name == "Nitrile-Rubber"


def test_component_never_overwrites_its_own_already_assigned_materials(monkeypatch):
    owner = FakeOwner(
        casing_material=FakeMaterial("Galvanized-Steel"),
        insulation_material=FakeMaterial("Nitrile-Rubber"),
    )
    monkeypatch.setattr(hvaclib, "getOwnerNetwork", lambda parent: owner)

    obj = FakeComponentObj(casing_material=FakeMaterial("Stainless-Steel"))
    _bare_component().applyOwnerDefaults(obj, parent_junction=object())

    assert obj.CasingMaterial.Name == "Stainless-Steel"
    assert obj.InsulationMaterial.Name == "Nitrile-Rubber"


def test_component_owner_defaults_noop_without_a_parent_junction():
    obj = FakeComponentObj()
    _bare_component().applyOwnerDefaults(obj, parent_junction=None)  # must not raise
    assert obj.CasingMaterial is None


def test_component_owner_defaults_noop_when_owner_network_unresolvable(monkeypatch):
    monkeypatch.setattr(hvaclib, "getOwnerNetwork", lambda parent: None)
    obj = FakeComponentObj()
    _bare_component().applyOwnerDefaults(obj, parent_junction=object())  # must not raise
    assert obj.CasingMaterial is None
