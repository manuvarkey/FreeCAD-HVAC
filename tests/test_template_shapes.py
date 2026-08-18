import os
from types import SimpleNamespace

import pytest

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.Library import HVACTypeDef
from freecad.HVAC.library.template_shapes import (
    TemplateNotFoundError,
    TemplateSchemaError,
    _apply_params,
    _port_names,
    _properties_with_computed_length,
    _real_ports_from_context,
    resolve_template_path,
)


def _lib(root_path):
    return SimpleNamespace(root_path=root_path)


def _type_def(**overrides):
    base = dict(id="t1", label="T1", category="junction", topology="generic", family=["x"])
    base.update(overrides)
    return HVACTypeDef(**base)


# ----------------------------------------------------------------------------
# resolve_template_path
# ----------------------------------------------------------------------------

def test_resolve_template_path_resolves_relative_to_library_root(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    f = models_dir / "damper.FCStd"
    f.write_text("")

    type_def = _type_def(generator_template_file="models/damper.FCStd")
    resolved = resolve_template_path(_lib(str(tmp_path)), type_def)

    assert resolved == os.path.normpath(str(f))


def test_resolve_template_path_missing_file_raises():
    type_def = _type_def(generator_template_file="models/missing.FCStd")
    try:
        resolve_template_path(_lib("/no/such/dir"), type_def)
        assert False, "expected TemplateNotFoundError"
    except TemplateNotFoundError:
        pass


def test_resolve_template_path_requires_file_key():
    type_def = _type_def(generator_template_file="")
    try:
        resolve_template_path(_lib("/anywhere"), type_def)
        assert False, "expected TemplateSchemaError"
    except TemplateSchemaError:
        pass


# ----------------------------------------------------------------------------
# _apply_params
# ----------------------------------------------------------------------------

class _FakeVarSet:
    """Stands in for an App::VarSet: pre-declared attributes are its typed
    properties (mirrors FreeCAD -- a VarSet only has the properties it was
    given in the GUI, so a name outside that set must be rejected)."""

    def __init__(self, **initial):
        for k, v in initial.items():
            setattr(self, k, v)


class _FakeDoc:
    def __init__(self, objects=None):
        self._objects = objects or {}
        self.recompute_called = 0
        self.Name = "FakeDoc"

    def getObjectsByLabel(self, name):
        obj = self._objects.get(name)
        return [obj] if obj is not None else []

    def recompute(self):
        self.recompute_called += 1
        return len(self._objects)


def test_apply_params_sets_varset_properties_from_properties():
    varset = _FakeVarSet(Diameter=0.0, Length=0.0)
    doc = _FakeDoc({"ParamsVarSet": varset})

    _apply_params(doc, {"Diameter": "Diameter", "BodyLength": "Length"}, {"Diameter": 150.0, "BodyLength": 300.0})

    assert varset.Diameter == 150.0
    assert varset.Length == 300.0
    assert doc.recompute_called == 1


def test_apply_params_skips_properties_the_template_does_not_use():
    varset = _FakeVarSet(Diameter=0.0)
    doc = _FakeDoc({"ParamsVarSet": varset})

    _apply_params(doc, {"Diameter": "Diameter"}, {})

    assert varset.Diameter == 0.0


def test_apply_params_dotted_target_overrides_varset_name():
    varset = _FakeVarSet(D=0.0)
    doc = _FakeDoc({"Other": varset})

    _apply_params(doc, {"Diameter": "Other.D"}, {"Diameter": 10.0})

    assert varset.D == 10.0


def test_apply_params_missing_varset_raises():
    doc = _FakeDoc({})
    try:
        _apply_params(doc, {"Diameter": "Diameter"}, {"Diameter": 1.0})
        assert False, "expected TemplateSchemaError"
    except TemplateSchemaError:
        pass


def test_apply_params_missing_property_on_varset_raises():
    varset = _FakeVarSet()  # no Diameter property declared
    doc = _FakeDoc({"ParamsVarSet": varset})
    try:
        _apply_params(doc, {"Diameter": "Diameter"}, {"Diameter": 1.0})
        assert False, "expected TemplateSchemaError"
    except TemplateSchemaError:
        pass


# ----------------------------------------------------------------------------
# _port_names
# ----------------------------------------------------------------------------

def test_port_names_defaults_to_indexed_sequence():
    type_def = _type_def(generator_template_ports=[])
    assert _port_names(type_def, 3) == ["Port0", "Port1", "Port2"]


def test_port_names_uses_json_override_when_present():
    type_def = _type_def(generator_template_ports=["Inlet", "Outlet"])
    assert _port_names(type_def, 2) == ["Inlet", "Outlet"]


# ----------------------------------------------------------------------------
# _real_ports_from_context
# ----------------------------------------------------------------------------

def test_real_ports_from_context_passes_through_junction_ports():
    ports = [{"position": (0, 0, 0), "direction": (1, 0, 0)}]
    context = {"connected_ports": ports}

    assert _real_ports_from_context(context) == ports


def test_real_ports_from_context_synthesizes_two_pseudo_ports_for_segments():
    context = {"start_point": (0.0, 0.0, 0.0), "end_point": (1000.0, 0.0, 0.0)}

    ports = _real_ports_from_context(context)

    assert len(ports) == 2
    start_dir = ports[0]["direction"]
    end_dir = ports[1]["direction"]
    assert (start_dir.x, start_dir.y, start_dir.z) == (-1.0, 0.0, 0.0)
    assert (end_dir.x, end_dir.y, end_dir.z) == (1.0, 0.0, 0.0)


# ----------------------------------------------------------------------------
# _properties_with_computed_length
# ----------------------------------------------------------------------------

def test_properties_with_computed_length_adds_length_for_two_ports():
    context = {"properties": {"Width": 200.0}}
    real_ports = [
        {"position": (0.0, 0.0, 0.0), "direction": (1, 0, 0)},
        {"position": (1500.0, 0.0, 0.0), "direction": (1, 0, 0)},
    ]

    properties = _properties_with_computed_length(context, real_ports)

    assert properties["Width"] == 200.0
    assert properties["Length"] == pytest.approx(1500.0)


def test_properties_with_computed_length_does_not_override_declared_length():
    context = {"properties": {"Length": 42.0}}
    real_ports = [
        {"position": (0.0, 0.0, 0.0), "direction": (1, 0, 0)},
        {"position": (1500.0, 0.0, 0.0), "direction": (1, 0, 0)},
    ]

    properties = _properties_with_computed_length(context, real_ports)

    assert properties["Length"] == 42.0


def test_properties_with_computed_length_skipped_for_non_two_port_types():
    context = {"properties": {"Width": 200.0}}
    real_ports = [
        {"position": (0.0, 0.0, 0.0), "direction": (1, 0, 0)},
        {"position": (1500.0, 0.0, 0.0), "direction": (1, 0, 0)},
        {"position": (0.0, 1500.0, 0.0), "direction": (0, 1, 0)},
    ]

    properties = _properties_with_computed_length(context, real_ports)

    assert "Length" not in properties
