"""
Tests for core/_component_appearance.py: rendering CasingShape/
InsulationShape from their own native CasingMaterial/InsulationMaterial via
FreeCAD's per-face ViewObject.ShapeAppearance -- see ARCHITECTURE.md's
"Component geometry & materials" section for why the face-count split
(len(CasingShape.Faces)) is an exact count derived from the same two shapes
Shape was built from, not a hardcoded/guessed index.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Materials/PySide stubs

from freecad.HVAC.core import _component_appearance as appearance_mod


class FakeShape:
    def __init__(self, n_faces, null=False):
        self.Faces = list(range(n_faces))
        self._null = null

    def isNull(self):
        return self._null


class FakeMaterial:
    def __init__(self, name=""):
        self.Name = name


class FakeObj:
    def __init__(self, casing_shape=None, insulation_shape=None, casing_material=None, insulation_material=None):
        self.CasingShape = casing_shape
        self.InsulationShape = insulation_shape
        self.CasingMaterial = casing_material
        self.InsulationMaterial = insulation_material


class FakeViewObject:
    def __init__(self, obj, has_shape_appearance=True):
        self.Object = obj
        if has_shape_appearance:
            self.ShapeAppearance = None


def _patch_view_appearance(monkeypatch, mapping):
    """mapping: {material_object: appearance_or_None}, matched by identity."""
    def fake_get_view_appearance(material):
        for key, value in mapping:
            if material is key:
                return value
        return None
    monkeypatch.setattr(appearance_mod.hvac_materials, "get_view_appearance", fake_get_view_appearance)


def test_apply_component_appearance_builds_per_face_list_in_casing_then_insulation_order(monkeypatch):
    casing_material = FakeMaterial("Galvanized Steel")
    insulation_material = FakeMaterial("Glass Wool")
    casing_appearance = object()
    insulation_appearance = object()
    _patch_view_appearance(monkeypatch, [
        (casing_material, casing_appearance),
        (insulation_material, insulation_appearance),
    ])

    obj = FakeObj(
        casing_shape=FakeShape(3),
        insulation_shape=FakeShape(2),
        casing_material=casing_material,
        insulation_material=insulation_material,
    )
    vobj = FakeViewObject(obj)

    appearance_mod.apply_component_appearance(vobj)

    assert vobj.ShapeAppearance == [casing_appearance] * 3 + [insulation_appearance] * 2


def test_apply_component_appearance_fills_missing_side_with_default(monkeypatch):
    casing_material = FakeMaterial("Galvanized Steel")
    casing_appearance = object()
    _patch_view_appearance(monkeypatch, [(casing_material, casing_appearance)])

    default_appearance = object()
    monkeypatch.setattr(appearance_mod.FreeCAD, "Material", lambda: default_appearance)

    obj = FakeObj(
        casing_shape=FakeShape(2),
        insulation_shape=FakeShape(1),
        casing_material=casing_material,
        insulation_material=None,  # no insulation material assigned
    )
    vobj = FakeViewObject(obj)

    appearance_mod.apply_component_appearance(vobj)

    # List length must still equal the compound's total face count (3), so
    # the insulation face keeps its position, just at the default appearance.
    assert vobj.ShapeAppearance == [casing_appearance, casing_appearance, default_appearance]


def test_apply_component_appearance_does_nothing_without_any_faces():
    obj = FakeObj(casing_shape=None, insulation_shape=None)
    vobj = FakeViewObject(obj)

    appearance_mod.apply_component_appearance(vobj)

    assert not hasattr(vobj, "ShapeAppearance") or vobj.ShapeAppearance is None


def test_apply_component_appearance_does_nothing_when_neither_material_resolves(monkeypatch):
    monkeypatch.setattr(appearance_mod.hvac_materials, "get_view_appearance", lambda material: None)

    obj = FakeObj(casing_shape=FakeShape(4), casing_material=FakeMaterial("Something"))
    vobj = FakeViewObject(obj)

    appearance_mod.apply_component_appearance(vobj)

    assert vobj.ShapeAppearance is None


def test_apply_component_appearance_noop_without_shape_appearance_property():
    # Defensive: an older FreeCAD without the native per-face property must
    # not raise -- just leave the object's appearance untouched.
    obj = FakeObj(casing_shape=FakeShape(2), casing_material=FakeMaterial("Steel"))
    vobj = FakeViewObject(obj, has_shape_appearance=False)

    appearance_mod.apply_component_appearance(vobj)  # must not raise

    assert not hasattr(vobj, "ShapeAppearance")


def test_apply_component_appearance_treats_null_shape_as_zero_faces(monkeypatch):
    casing_material = FakeMaterial("Steel")
    casing_appearance = object()
    _patch_view_appearance(monkeypatch, [(casing_material, casing_appearance)])

    obj = FakeObj(
        casing_shape=FakeShape(3),
        insulation_shape=FakeShape(5, null=True),
        casing_material=casing_material,
    )
    vobj = FakeViewObject(obj)

    appearance_mod.apply_component_appearance(vobj)

    assert vobj.ShapeAppearance == [casing_appearance] * 3


def test_apply_component_appearance_guards_against_freecad_reentrancy(monkeypatch):
    """
    Regression: on real FreeCAD, querying a Materials::PropertyMaterial
    value's own appearance (e.g. hasAppearanceProperty()) can synchronously
    re-fire ViewProvider.updateData() for that same property *before* the
    original call returns -- which called apply_component_appearance()
    again, recursing until the interpreter's stack limit crashed it
    (confirmed against a real FreeCAD 1.1 install: assigning a material
    triggered thousands of nested calls before RecursionError). This must
    be a genuine no-op guard -- not just idempotent output -- so the test
    counts both get_view_appearance() calls and ShapeAppearance writes,
    which only distinguish a guarded single render from an unguarded nested
    one (both would otherwise produce the same final list).
    """
    casing_material = FakeMaterial("Steel")
    casing_appearance = object()
    calls = {"get_view_appearance": 0, "shape_appearance_writes": 0}

    def fake_get_view_appearance(material):
        calls["get_view_appearance"] += 1
        if calls["get_view_appearance"] == 1:
            # Simulate FreeCAD's reentrant updateData() firing mid-call, for
            # the very same object, before this call even returns.
            appearance_mod.apply_component_appearance(vobj)
        return casing_appearance if material is casing_material else None

    monkeypatch.setattr(appearance_mod.hvac_materials, "get_view_appearance", fake_get_view_appearance)

    class CountingViewObject(FakeViewObject):
        def __setattr__(self, name, value):
            if name == "ShapeAppearance":
                calls["shape_appearance_writes"] += 1
            super().__setattr__(name, value)

    obj = FakeObj(casing_shape=FakeShape(2), casing_material=casing_material)
    vobj = CountingViewObject(obj)
    calls["shape_appearance_writes"] = 0  # discount FakeViewObject.__init__'s own initial assignment

    appearance_mod.apply_component_appearance(vobj)  # must not raise/recurse

    # Guarded: the reentrant inner call bails out before calling
    # get_view_appearance() at all (2 calls total: casing + insulation, from
    # the outer call only) and renders exactly once. Unguarded, the inner
    # call would run to completion first (2 more get_view_appearance()
    # calls, 1 more write) before the outer call resumed and repeated it.
    assert calls["get_view_appearance"] == 2
    assert calls["shape_appearance_writes"] == 1
    assert vobj.ShapeAppearance == [casing_appearance] * 2
