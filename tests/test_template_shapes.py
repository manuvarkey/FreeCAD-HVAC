import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.template_shapes import (
    TemplateSchemaError,
    _apply_params,
    _port_names,
    _real_ports_from_context,
)


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


def test_apply_params_sets_varset_properties_from_dict():
    varset = _FakeVarSet(Diameter=0.0, Length=0.0)
    doc = _FakeDoc({"ParamsVarSet": varset})

    _apply_params(doc, {"Diameter": 150.0, "Length": 300.0})

    assert varset.Diameter == 150.0
    assert varset.Length == 300.0
    assert doc.recompute_called == 1


def test_apply_params_dotted_target_overrides_varset_name():
    varset = _FakeVarSet(D=0.0)
    doc = _FakeDoc({"Other": varset})

    _apply_params(doc, {"Other.D": 10.0})

    assert varset.D == 10.0


def test_apply_params_missing_varset_raises():
    doc = _FakeDoc({})
    try:
        _apply_params(doc, {"Diameter": 1.0})
        assert False, "expected TemplateSchemaError"
    except TemplateSchemaError:
        pass


def test_apply_params_missing_property_on_varset_raises():
    varset = _FakeVarSet()  # no Diameter property declared
    doc = _FakeDoc({"ParamsVarSet": varset})
    try:
        _apply_params(doc, {"Diameter": 1.0})
        assert False, "expected TemplateSchemaError"
    except TemplateSchemaError:
        pass


def test_apply_params_empty_still_recomputes():
    doc = _FakeDoc({})
    _apply_params(doc, {})
    assert doc.recompute_called == 1


# ----------------------------------------------------------------------------
# _port_names
# ----------------------------------------------------------------------------

def test_port_names_defaults_to_indexed_sequence():
    assert _port_names(None, 3) == ["Port0", "Port1", "Port2"]
    assert _port_names([], 3) == ["Port0", "Port1", "Port2"]


def test_port_names_uses_override_when_present():
    assert _port_names(["Inlet", "Outlet"], 2) == ["Inlet", "Outlet"]


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
