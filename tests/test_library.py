import json
import sys
import types

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.Library import HVACLibrary, HVACTypeDef, HVACLibraryRegistry
from freecad.HVAC.library.construction import ConstructionLayerDef, ConstructionFeatureDef


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
        "construction": {
            "layers": [
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
        },
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert [ldef.id for ldef in type_def.construction] == ["casing", "insulation"]
    assert type_def.construction[0].roles == ["flow_surface", "structural_shell"]
    assert type_def.construction[0].thickness_property == "Thickness"
    assert type_def.construction[1].default_material_role == "thermal_insulation"
    assert type_def.features == []


def test_load_type_def_file_parses_features_block(tmp_path):
    type_file = tmp_path / "type.json"
    type_file.write_text(json.dumps({
        "id": "circular_straight",
        "label": "Circular Straight",
        "category": "segment",
        "family": ["straight_segment"],
        "generator": {"module": "segments", "function": "build_circular_straight"},
        "construction": {
            "layers": [{"id": "casing", "roles": ["flow_surface", "structural_shell"]}],
            "features": [
                {
                    "id": "transverse_flange",
                    "role": "transverse_joint",
                    "host_layer": "casing",
                    "generator": "generate_transverse_flange",
                    "enabled_parameter": "FlangeEnabled",
                    "visible_parameter": "FlangeVisible",
                    "parameters": ["FlangeDepth", "FlangeThickness"],
                },
            ],
        },
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert len(type_def.features) == 1
    fdef = type_def.features[0]
    assert fdef.id == "transverse_flange"
    assert fdef.role == "transverse_joint"
    assert fdef.host_layer == "casing"
    assert fdef.generator == "generate_transverse_flange"
    assert fdef.enabled_parameter == "FlangeEnabled"
    assert fdef.visible_parameter == "FlangeVisible"
    assert fdef.parameters == ["FlangeDepth", "FlangeThickness"]


def test_load_type_def_file_features_default_to_optional_fields_unset(tmp_path):
    type_file = tmp_path / "type.json"
    type_file.write_text(json.dumps({
        "id": "circular_straight",
        "label": "Circular Straight",
        "category": "segment",
        "family": ["straight_segment"],
        "generator": {"module": "segments", "function": "build_circular_straight"},
        "construction": {
            "layers": [{"id": "casing", "roles": ["flow_surface"]}],
            "features": [
                {"id": "stiffener", "host_layer": "casing", "generator": "generate_stiffener"},
            ],
        },
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    fdef = type_def.features[0]
    assert fdef.role == ""
    assert fdef.enabled_parameter is None
    assert fdef.visible_parameter is None
    assert fdef.parameters == []


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


# ----------------------------------------------------------------------
# build_geometry() -- construction features
# ----------------------------------------------------------------------

def _register_fake_type(construction=None, features=None, extra_params=None):
    """
    Build a fake-module-backed HVACLibrary + type-def, the same fixture
    shape test_build_geometry_stamps_layer_roles_from_construction_defs()
    already uses, extended with an optional "features" fake module. Neither
    module is a real SMACNA file -- this proves a library-defined feature
    needs zero feature-specific core changes, only its own generator
    function in its own conventional module.
    """
    segments_module = types.ModuleType("fake_hvac_lib_pkg.segments")
    segments_module.build = lambda context: {"layers": {"casing": {"shape": "CASING"}}}
    sys.modules["fake_hvac_lib_pkg.segments"] = segments_module

    lib = HVACLibrary(id="lib", label="Lib", root_path="", generators_package="fake_hvac_lib_pkg")
    type_def = HVACTypeDef(
        id="circular_straight",
        label="Circular Straight",
        category="segment",
        topology="generic",
        family=["straight_segment"],
        generator_module="segments",
        generator_function="build",
        construction=construction or [ConstructionLayerDef(id="casing", roles=["flow_surface"])],
        features=features or [],
    )
    lib.add_type(type_def)
    reg = HVACLibraryRegistry()
    reg.register_library(lib)
    return reg, type_def


def test_build_geometry_invokes_an_enabled_features_own_generator_with_filtered_context():
    calls = []

    def generate_transverse_flange(api, ctx):
        calls.append((api, ctx))
        return "FLANGE_SHAPE"

    features_module = types.ModuleType("fake_hvac_lib_pkg.features")
    features_module.generate_transverse_flange = generate_transverse_flange
    sys.modules["fake_hvac_lib_pkg.features"] = features_module
    try:
        reg, type_def = _register_fake_type(features=[
            ConstructionFeatureDef(
                id="transverse_flange",
                role="transverse_joint",
                host_layer="casing",
                generator="generate_transverse_flange",
                parameters=["FlangeDepth", "FlangeThickness"],
            ),
        ])

        result = reg.build_geometry("lib", type_def, {
            "params": {"FlangeDepth": 25.0, "FlangeThickness": 1.0, "SomethingElse": 999},
        })

        assert len(calls) == 1
        api_arg, ctx = calls[0]
        from freecad.HVAC.library.library_api import HVACLibraryAPI
        assert api_arg is HVACLibraryAPI
        # Only the feature's own declared parameters -- never the type's
        # full property set (e.g. "SomethingElse" must not leak through).
        assert ctx.parameters == {"FlangeDepth": 25.0, "FlangeThickness": 1.0}
        assert ctx.host_layer is result.layers["casing"]

        assert result.features["transverse_flange"].shape == "FLANGE_SHAPE"
        assert result.features["transverse_flange"].role == "transverse_joint"
        assert result.features["transverse_flange"].visible is True
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.features", None)
        sys.modules.pop("fake_hvac_lib_pkg.segments", None)


def test_build_geometry_skips_a_disabled_feature_entirely():
    calls = []

    def generate_transverse_flange(api, ctx):
        calls.append(ctx)
        return "FLANGE_SHAPE"

    features_module = types.ModuleType("fake_hvac_lib_pkg.features")
    features_module.generate_transverse_flange = generate_transverse_flange
    sys.modules["fake_hvac_lib_pkg.features"] = features_module
    try:
        reg, type_def = _register_fake_type(features=[
            ConstructionFeatureDef(
                id="transverse_flange",
                host_layer="casing",
                generator="generate_transverse_flange",
                enabled_parameter="FlangeEnabled",
            ),
        ])

        result = reg.build_geometry("lib", type_def, {"params": {"FlangeEnabled": False}})

        assert calls == []
        assert "transverse_flange" not in result.features
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.features", None)
        sys.modules.pop("fake_hvac_lib_pkg.segments", None)


def test_build_geometry_stamps_visible_false_from_visible_parameter():
    features_module = types.ModuleType("fake_hvac_lib_pkg.features")
    features_module.generate_transverse_flange = lambda api, ctx: "FLANGE_SHAPE"
    sys.modules["fake_hvac_lib_pkg.features"] = features_module
    try:
        reg, type_def = _register_fake_type(features=[
            ConstructionFeatureDef(
                id="transverse_flange",
                host_layer="casing",
                generator="generate_transverse_flange",
                visible_parameter="FlangeVisible",
            ),
        ])

        result = reg.build_geometry("lib", type_def, {"params": {"FlangeVisible": False}})

        # Not visible, but still generated -- present in result.features,
        # unlike a disabled feature.
        assert result.features["transverse_flange"].shape == "FLANGE_SHAPE"
        assert result.features["transverse_flange"].visible is False
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.features", None)
        sys.modules.pop("fake_hvac_lib_pkg.segments", None)


def test_build_geometry_raises_for_a_feature_referencing_an_unbuilt_host_layer():
    features_module = types.ModuleType("fake_hvac_lib_pkg.features")
    features_module.generate_transverse_flange = lambda api, ctx: "FLANGE_SHAPE"
    sys.modules["fake_hvac_lib_pkg.features"] = features_module
    try:
        reg, type_def = _register_fake_type(features=[
            ConstructionFeatureDef(
                id="transverse_flange",
                host_layer="does_not_exist",
                generator="generate_transverse_flange",
            ),
        ])

        try:
            reg.build_geometry("lib", type_def, {"params": {}})
        except ValueError as exc:
            assert "does_not_exist" in str(exc)
        else:
            raise AssertionError("Expected ValueError for a feature with no matching host layer")
    finally:
        sys.modules.pop("fake_hvac_lib_pkg.features", None)
        sys.modules.pop("fake_hvac_lib_pkg.segments", None)


def test_build_geometry_with_no_features_declared_leaves_result_features_empty():
    reg, type_def = _register_fake_type()
    result = reg.build_geometry("lib", type_def, {"params": {}})
    assert result.features == {}


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
