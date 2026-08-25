import json
import sys
import types

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.Library import HVACLibrary, HVACTypeDef, HVACLibraryRegistry
from freecad.HVAC.library.construction import ConstructionLayerDef


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


def test_build_geometry_dispatches_legacy_generator_and_aliases_params_as_properties(monkeypatch):
    # Regression for the Library.py/Segment.py/Junction.py rewiring: every
    # existing generator function (smacna/builtin_basic) still reads
    # context["properties"], not context["params"] -- build_geometry must
    # keep aliasing the resolved params dict onto "properties" for the
    # legacy generator_module/generator_function backend.
    fake_module = types.ModuleType("fake_hvac_lib_pkg.junctions")
    captured = {}

    def build_elbow(context):
        captured["properties"] = context["properties"]
        captured["params"] = context["params"]
        return {"shape": "SHAPE"}

    fake_module.build_elbow = build_elbow
    sys.modules["fake_hvac_lib_pkg.junctions"] = fake_module
    try:
        lib = HVACLibrary(
            id="lib", label="Lib", root_path="", generators_package="fake_hvac_lib_pkg"
        )
        type_def = HVACTypeDef(
            id="elbow",
            label="Elbow",
            category="other",
            topology="generic",
            family=["through.elbow"],
            generator_module="junctions",
            generator_function="build_elbow",
        )
        lib.add_type(type_def)

        reg = HVACLibraryRegistry()
        reg.register_library(lib)

        result = reg.build_geometry("lib", type_def, {"params": {"Diameter": 100.0}})

        # build_geometry always normalizes a backend's raw return value into
        # a GeometryResult -- a legacy {"shape": ...} generator's shape
        # becomes a single layer, id "shape" (see library/geometry_result.py).
        assert result.layers["shape"].shape == "SHAPE"
        assert captured["params"] == {"Diameter": 100.0}
        assert captured["properties"] is captured["params"]
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.junctions", None)


def test_load_type_def_file_parses_generator_module_and_function(tmp_path):
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

    assert type_def.generator_module == "junctions"
    assert type_def.generator_function == "build_elbow"


def test_load_type_def_file_parses_construction_block(tmp_path):
    type_file = tmp_path / "type.json"
    type_file.write_text(json.dumps({
        "id": "circular_straight",
        "label": "Circular Straight",
        "category": "segment",
        "family": ["straight_segment"],
        "generator": {"module": "segments", "function": "build_circular_straight"},
        "construction": [
            {
                "id": "casing",
                "roles": ["flow_surface", "structural_shell"],
                "thickness_property": "Thickness",
            },
            {
                "id": "insulation",
                "roles": ["thermal_insulation"],
                "default_material_role": "thermal_insulation",
                "thickness_property": "InsulationThickness",
            },
        ],
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert [ldef.id for ldef in type_def.construction] == ["casing", "insulation"]
    assert type_def.construction[0].roles == ["flow_surface", "structural_shell"]
    assert type_def.construction[0].thickness_property == "Thickness"
    assert type_def.construction[1].default_material_role == "thermal_insulation"


def test_load_type_def_file_defaults_construction_to_empty_list(tmp_path):
    type_file = tmp_path / "type.json"
    type_file.write_text(json.dumps({
        "id": "circular_straight",
        "label": "Circular Straight",
        "category": "segment",
        "family": ["straight_segment"],
        "generator": {"module": "segments", "function": "build_circular_straight"},
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.construction == []


def test_build_geometry_stamps_layer_roles_from_construction_defs():
    fake_module = types.ModuleType("fake_hvac_lib_pkg.segments")

    def build(context):
        return {"layers": {"casing": {"shape": "CASING"}, "insulation": {"shape": "INSULATION"}}}

    fake_module.build = build
    sys.modules["fake_hvac_lib_pkg.segments"] = fake_module
    try:
        lib = HVACLibrary(
            id="lib", label="Lib", root_path="", generators_package="fake_hvac_lib_pkg"
        )
        type_def = HVACTypeDef(
            id="circular_straight",
            label="Circular Straight",
            category="segment",
            topology="generic",
            family=["straight_segment"],
            generator_module="segments",
            generator_function="build",
            construction=[
                ConstructionLayerDef(id="casing", roles=["flow_surface", "structural_shell"]),
                ConstructionLayerDef(id="insulation", roles=["thermal_insulation"]),
            ],
        )
        lib.add_type(type_def)

        reg = HVACLibraryRegistry()
        reg.register_library(lib)

        result = reg.build_geometry("lib", type_def, {"params": {}})

        assert result.layers["casing"].roles == ["flow_surface", "structural_shell"]
        assert result.layers["insulation"].roles == ["thermal_insulation"]
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.segments", None)


def test_build_geometry_leaves_roles_empty_for_layers_with_no_matching_construction_def():
    fake_module = types.ModuleType("fake_hvac_lib_pkg.segments_unmigrated")

    def build(context):
        return {"shape": "SHAPE"}

    fake_module.build = build
    sys.modules["fake_hvac_lib_pkg.segments_unmigrated"] = fake_module
    try:
        lib = HVACLibrary(
            id="lib", label="Lib", root_path="", generators_package="fake_hvac_lib_pkg"
        )
        type_def = HVACTypeDef(
            id="rectangular_straight",
            label="Rectangular Straight",
            category="segment",
            topology="generic",
            family=["straight_segment"],
            generator_module="segments_unmigrated",
            generator_function="build",
        )
        lib.add_type(type_def)

        reg = HVACLibraryRegistry()
        reg.register_library(lib)

        result = reg.build_geometry("lib", type_def, {"params": {}})

        assert result.layers["shape"].roles == []
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.segments_unmigrated", None)
