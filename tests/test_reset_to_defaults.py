"""
Focused test for DuctNetwork.resetObjectsToNetworkDefaults(): an explicit
"Reset to Defaults" must also re-apply the owner network's current
DefaultCasingMaterial/DefaultInsulationMaterial onto the selected
segment/component -- unlike DuctSegment.applyOwnerDefaults()/DuctComponent.
applyOwnerDefaults() (only fill in a material a *new* object doesn't have
yet), this always overwrites, same "reset always wins" convention already
used here for LibraryId/TypeId. See ARCHITECTURE.md's "Component geometry &
materials" section.

Uses the same real-DuctSegment-against-a-fake-document approach as
test_network_component_sync.py, so hvaclib.isDuctSegment/getOwnerNetwork
and the real bundled smacna library type selection behave exactly as in
the real addon.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Materials/MatGui/PySide stubs

from freecad.HVAC.core import Network as network_mod
from freecad.HVAC.core.Segment import DuctSegment
from freecad.HVAC.utils import hvaclib


class FakeMaterial:
    def __init__(self, name):
        self.Name = name


class FakeViewObject:
    def __init__(self):
        self.Visibility = True


class FakeFCObj:
    """Minimal stand-in for a FreeCAD DocumentObject -- see
    test_network_component_sync.py's identical fixture for why this is
    enough for addProperty/setEditorMode/touch/doc.getObject to work."""

    def __init__(self, name, doc):
        self.Name = name
        self.Label = name
        self.Document = doc
        self.PropertiesList = []
        self._editor_modes = {}
        self.ViewObject = FakeViewObject()

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

    def touch(self):
        pass


class FakeGeometryFolder(FakeFCObj):
    def __init__(self, name, doc):
        super().__init__(name, doc)
        self.OutList = []

    def addObject(self, obj):
        if obj not in self.OutList:
            self.OutList.append(obj)


class FakeDoc:
    def __init__(self):
        self._objects = {}

    def addObject(self, type_str, name):
        obj = FakeFCObj(name, self)
        self._objects[name] = obj
        return obj

    def getObject(self, name):
        return self._objects.get(name)

    def recompute(self):
        pass


def _make_network(doc):
    net_obj = FakeFCObj("Network0", doc)
    net_obj.Geometry = FakeGeometryFolder("Geometry0", doc)
    doc._objects[net_obj.Name] = net_obj

    net_proxy = network_mod.DuctNetwork.__new__(network_mod.DuctNetwork)
    net_proxy.Object = net_obj
    net_obj.Proxy = net_proxy
    return net_obj


def test_reset_to_defaults_overwrites_existing_casing_and_insulation_material(monkeypatch):
    doc = FakeDoc()
    net_obj = _make_network(doc)
    net_obj.DefaultLibraryId = "smacna"
    net_obj.DefaultSegmentProfile = "Circular"

    default_casing = FakeMaterial("Galvanized-Steel")
    default_insulation = FakeMaterial("Nitrile-Rubber")
    net_obj.DefaultCasingMaterial = default_casing
    net_obj.DefaultInsulationMaterial = default_insulation

    segment = DuctSegment.create(doc, "Segment0", owner=net_obj, key="A", source_obj=None, source_index=0)
    segment.Proxy._allow_delete = True
    # A manually-assigned material, distinct from the network's own default --
    # the whole point of this test is that "Reset to Defaults" discards it.
    segment.CasingMaterial = FakeMaterial("Aluminium")
    segment.InsulationMaterial = FakeMaterial("Glass-Wool")

    monkeypatch.setattr(network_mod.FreeCAD, "ActiveDocument", doc)

    network_mod.DuctNetwork.resetObjectsToNetworkDefaults([segment])

    assert segment.CasingMaterial is default_casing
    assert segment.InsulationMaterial is default_insulation


def test_reset_to_defaults_tolerates_no_default_material_set(monkeypatch):
    # A network whose own DefaultCasingMaterial/DefaultInsulationMaterial
    # were never resolved (e.g. register_material_resources() hasn't run)
    # must not raise, and must leave the segment's existing material alone.
    doc = FakeDoc()
    net_obj = _make_network(doc)
    net_obj.DefaultLibraryId = "smacna"
    net_obj.DefaultSegmentProfile = "Circular"
    net_obj.DefaultCasingMaterial = None
    net_obj.DefaultInsulationMaterial = None

    segment = DuctSegment.create(doc, "Segment0", owner=net_obj, key="A", source_obj=None, source_index=0)
    segment.Proxy._allow_delete = True
    existing = FakeMaterial("Aluminium")
    segment.CasingMaterial = existing

    monkeypatch.setattr(network_mod.FreeCAD, "ActiveDocument", doc)

    network_mod.DuctNetwork.resetObjectsToNetworkDefaults([segment])  # must not raise

    assert segment.CasingMaterial is existing
