import json
from types import SimpleNamespace

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library import partscript_shapes, validation
from freecad.HVAC.library.Library import HVACLibraryRegistry


class _Shape:
    def isNull(self):
        return False


def test_resolve_params_normalizes_and_validates():
    type_def = SimpleNamespace(
        id="round",
        properties=[
            SimpleNamespace(
                name="Diameter",
                prop_type="App::PropertyLength",
                default=100.0,
                required=True,
                validation={"exclusiveMinimum": 0.0},
            )
        ],
    )
    obj = SimpleNamespace(Diameter=250.0)
    assert validation.resolve_params(type_def, obj=obj) == {"Diameter": 250.0}


def test_resolve_params_rejects_invalid_value():
    type_def = SimpleNamespace(
        id="round",
        properties=[
            SimpleNamespace(
                name="Diameter",
                prop_type="App::PropertyLength",
                default=100.0,
                required=True,
                validation={"exclusiveMinimum": 0.0},
            )
        ],
    )
    obj = SimpleNamespace(Diameter=0.0)
    try:
        validation.resolve_params(type_def, obj=obj)
    except ValueError as exc:
        assert "Diameter" in str(exc)
    else:
        raise AssertionError("Expected validation failure")


def test_partscript_optional_validate_and_generate(tmp_path):
    script = tmp_path / "model.py"
    script.write_text(
        "HVAC_PARTSCRIPT_API = 1\n"
        "def validate(context):\n"
        "    if context['params']['D'] <= 0: raise ValueError('D')\n"
        "def generate(context):\n"
        "    return {'shape': context['test_shape'], 'tag': 'ok'}\n"
    )
    result = partscript_shapes.execute_partscript(
        str(script), {"params": {"D": 1.0}, "test_shape": _Shape()}
    )
    assert result["tag"] == "ok"


def test_validate_context_generic_profile_accepts_any_connected_profile():
    # Profile-agnostic placeholder types (e.g. topology marker fittings)
    # declare profiles=["Generic"] and must still accept junctions whose
    # connected ports carry a real duct profile (Circular/Rectangular/...).
    type_def = SimpleNamespace(
        id="through_marker",
        category="junction",
        topology="through",
        profiles=["Generic"],
        constraints={},
    )
    context = {
        "connected_ports": [
            {"profile": "Circular"},
            {"profile": "Rectangular"},
        ],
        "topology": "through",
    }
    validation.validate_context(type_def, context)  # must not raise


def test_validate_context_rejects_unsupported_profile_for_non_generic_type():
    type_def = SimpleNamespace(
        id="branch_wye",
        category="junction",
        topology="branch",
        profiles=["Circular"],
        constraints={},
    )
    context = {"connected_ports": [{"profile": "Rectangular"}], "topology": "branch"}
    try:
        validation.validate_context(type_def, context)
    except ValueError as exc:
        assert "Rectangular" in str(exc)
    else:
        raise AssertionError("Expected validation failure")


def test_validate_context_enforces_degree_min_constraint():
    type_def = SimpleNamespace(
        id="multiport_generic",
        category="junction",
        topology="multiport",
        profiles=[],
        constraints={"degree_min": 5},
    )
    context = {"connected_ports": [{}] * 4, "topology": "multiport"}
    try:
        validation.validate_context(type_def, context)
    except ValueError as exc:
        assert "degree" in str(exc).lower()
    else:
        raise AssertionError("Expected degree_min validation failure")


def test_type_loader_parses_geometry_and_validation(tmp_path):
    type_file = tmp_path / "round.json"
    type_file.write_text(json.dumps({
        "id": "round",
        "label": "Round",
        "category": "segment",
        "family": ["straight_segment"],
        "profiles": ["Circular"],
        "properties": [{
            "name": "Diameter",
            "prop_type": "App::PropertyLength",
            "default": 100.0,
            "validation": {"exclusiveMinimum": 0.0}
        }],
        "geometry": {"backend": "partscript", "file": "models/round.py"}
    }))

    type_def = HVACLibraryRegistry()._load_type_def_file(str(type_file))
    assert type_def.geometry.backend == "partscript"
    assert type_def.geometry.file == "models/round.py"
    assert type_def.properties[0].validation == {"exclusiveMinimum": 0.0}
