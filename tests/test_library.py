import json

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.Library import HVACLibrary, HVACTypeDef, HVACLibraryRegistry


def _type_def(id_, category, family, profiles=None):
    return HVACTypeDef(
        id=id_,
        label=id_,
        category=category,
        topology="generic",
        family=family,
        profiles=list(profiles or []),
    )


def _library_with(*type_defs):
    lib = HVACLibrary(id="lib", label="Lib", root_path="", generators_package="")
    for t in type_defs:
        lib.add_type(t)
    return lib


def test_list_types_matches_exact_family():
    lib = _library_with(_type_def("a", "junction", ["end.terminal.diffuser"]))
    assert [t.id for t in lib.list_types(category="junction", family="end.terminal.diffuser")] == ["a"]


def test_list_types_matches_family_subtree_but_not_unrelated_sibling():
    lib = _library_with(
        _type_def("a", "junction", ["end.terminal.diffuser"]),
        _type_def("b", "junction", ["end.terminal"]),
        _type_def("c", "junction", ["end.source.fan"]),
    )
    matched = {t.id for t in lib.list_types(category="junction", family="end.terminal")}
    assert matched == {"a", "b"}


def test_list_profiles_matches_single_element_family_list():
    # Regression: segment type defs carry a single-element family list
    # (e.g. ["straight_segment"]) rather than a hierarchical dotted path.
    # A previous bug iterated a bare *string* family value character-by-
    # character instead of as a one-item list, silently returning no
    # matches for every segment profile lookup.
    lib = _library_with(
        _type_def("circular_straight", "segment", ["straight_segment"], profiles=["Circular"]),
        _type_def("rectangular_straight", "segment", ["straight_segment"], profiles=["Rectangular"]),
        _type_def("circular_generic", "segment", ["curved_segment"], profiles=["Circular"]),
    )
    assert lib.list_profiles(category="segment", family="straight_segment") == ["Circular", "Rectangular"]


def test_load_type_def_file_normalizes_string_family_to_list(tmp_path):
    # The on-disk JSON schema historically allowed "family" to be authored
    # as a bare string (still true for the shipped segment type files) even
    # though HVACTypeDef.family is typed as list[str] -- the loader must
    # normalize this so callers can always safely iterate t.family.
    type_file = tmp_path / "circular_straight.json"
    type_file.write_text(json.dumps({
        "id": "circular_straight",
        "label": "Circular Straight",
        "category": "segment",
        "family": "straight_segment",
        "profiles": ["Circular"],
    }))

    reg = HVACLibraryRegistry()
    lib = HVACLibrary(id="lib", label="Lib", root_path=str(tmp_path), generators_package="")
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.family == ["straight_segment"]

    lib.add_type(type_def)
    assert lib.list_profiles(category="segment", family="straight_segment") == ["Circular"]


def test_load_type_def_file_defaults_generator_type_to_python(tmp_path):
    type_file = tmp_path / "elbow.json"
    type_file.write_text(json.dumps({
        "id": "elbow",
        "label": "Elbow",
        "category": "junction",
        "family": ["through.elbow"],
        "generator": {"module": "junctions", "function": "build_elbow"},
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.generator_type == "python"
    assert type_def.generator_module == "junctions"
    assert type_def.generator_function == "build_elbow"
    assert type_def.generator_template_file == ""


def test_load_type_def_file_parses_template_generator_block(tmp_path):
    type_file = tmp_path / "damper.json"
    type_file.write_text(json.dumps({
        "id": "damper_template",
        "label": "Damper (template)",
        "category": "junction",
        "family": ["through.straight.damper"],
        "generator": {
            "type": "template",
            "file": "models/damper_generic.FCStd",
            "result_object": "Body",
            "params": {"Diameter": "Diameter", "BodyLength": "Length"},
            "ports": ["Inlet", "Outlet"],
            "placement_tolerance_mm": 1.0,
            "placement_tolerance_deg": 2.0,
        },
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.generator_type == "template"
    assert type_def.generator_template_file == "models/damper_generic.FCStd"
    assert type_def.generator_template_result_object == "Body"
    assert type_def.generator_template_params == {"Diameter": "Diameter", "BodyLength": "Length"}
    assert type_def.generator_template_ports == ["Inlet", "Outlet"]
    assert type_def.generator_template_tol_mm == 1.0
    assert type_def.generator_template_tol_deg == 2.0


def test_load_type_def_file_template_generator_defaults(tmp_path):
    type_file = tmp_path / "damper_defaults.json"
    type_file.write_text(json.dumps({
        "id": "damper_template_defaults",
        "label": "Damper (template, defaults)",
        "category": "junction",
        "family": ["through.straight.damper"],
        "generator": {"type": "template", "file": "models/damper.FCStd"},
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.generator_template_result_object == "ResultObject"
    assert type_def.generator_template_params == {}
    assert type_def.generator_template_ports == []
    assert type_def.generator_template_tol_mm == 0.5
    assert type_def.generator_template_tol_deg == 0.5
