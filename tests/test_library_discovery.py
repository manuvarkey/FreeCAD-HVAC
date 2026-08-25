import os

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.Library import HVACLibraryRegistry

LIBRARIES_ROOT = os.path.join(os.path.dirname(__file__), "..", "freecad", "HVAC", "libraries")


def _load_registry():
    reg = HVACLibraryRegistry()
    reg.set_search_paths([LIBRARIES_ROOT])
    reg.ensure_loaded()
    return reg


def test_all_shipped_libraries_parse_without_error():
    reg = _load_registry()
    lib_ids = set(reg._libraries.keys())
    assert lib_ids == {"builtin_basic", "smacna", "samples"}


def test_smacna_straight_segments_use_partscript_backend():
    reg = _load_registry()
    smacna = reg._libraries["smacna"]
    for type_id in ("circular_straight", "oval_straight", "rectangular_straight"):
        type_def = smacna.get_type(type_id)
        assert type_def is not None, type_id
        assert type_def.geometry.backend == "partscript"
        assert os.path.isfile(os.path.join(smacna.root_path, type_def.geometry.file))


def test_circular_straight_flanges_default_off():
    reg = _load_registry()
    type_def = reg._libraries["smacna"].get_type("circular_straight")
    flags = {p.name: p.default for p in type_def.properties if p.name.startswith("ShowFlange")}
    assert flags == {"ShowFlange1": False, "ShowFlange2": False}


def test_rectangular_straight_flanges_default_on():
    reg = _load_registry()
    type_def = reg._libraries["smacna"].get_type("rectangular_straight")
    flags = {p.name: p.default for p in type_def.properties if p.name.startswith("ShowFlange")}
    assert flags == {"ShowFlange1": True, "ShowFlange2": True}


def test_oval_straight_has_no_flange_properties():
    reg = _load_registry()
    type_def = reg._libraries["smacna"].get_type("oval_straight")
    names = {p.name for p in type_def.properties}
    assert not any(n.startswith("Flange") or n.startswith("ShowFlange") for n in names)


def test_through_elbow_rectangular_uses_partscript_backend():
    reg = _load_registry()
    type_def = reg._libraries["smacna"].get_type("through_elbow_rectangular")
    assert type_def is not None
    assert type_def.profiles == ["Rectangular"]
    assert type_def.geometry.backend == "partscript"
    assert os.path.isfile(os.path.join(reg._libraries["smacna"].root_path, type_def.geometry.file))

    by_name = {p.name: p for p in type_def.properties}
    input_names = {"CenterlineRadius", "Thickness", "FlangeHeight", "FlangeThickness", "ShowFlange1", "ShowFlange2"}
    assert input_names == set(by_name.keys())

    for name in input_names:
        assert by_name[name].editor_mode == 0, name

    assert not any(n.startswith("insulation") for n in by_name)


def test_circular_acoustic_straight_declares_a_three_layer_construction():
    reg = _load_registry()
    smacna = reg._libraries["smacna"]
    type_def = smacna.get_type("circular_acoustic_straight")
    assert type_def is not None
    assert type_def.geometry.backend == "partscript"
    assert os.path.isfile(os.path.join(smacna.root_path, type_def.geometry.file))

    by_id = {ldef.id: ldef for ldef in type_def.construction}
    assert set(by_id.keys()) == {"liner", "absorber", "jacket"}
    assert by_id["liner"].roles == ["flow_surface", "acoustic_liner"]
    assert by_id["absorber"].roles == ["acoustic_absorber"]
    assert by_id["jacket"].roles == ["outer_jacket", "structural_shell"]
    # Every layer's material default is explicit -- never left to fall back
    # on roles[0], since "flow_surface" (liner's own first-listed role)
    # isn't a material-bearing role the way "acoustic_liner" is.
    assert by_id["liner"].default_material_role == "acoustic_liner"
    assert by_id["absorber"].default_material_role == "acoustic_absorber"
    assert by_id["jacket"].default_material_role == "structural_shell"

    # Lower priority than circular_straight (50) -- a real, manually-
    # selectable model type, but never displaces the plain casing+
    # insulation duct as smacna's automatic pick for a Circular segment.
    assert type_def.selection.priority < 50


def test_samples_library_holds_the_fcstd_and_static_diffuser_samples():
    reg = _load_registry()
    samples = reg._libraries["samples"]

    rect = samples.get_type("rectangular_straight")
    assert rect is not None
    assert rect.generator_module == "segments"
    assert rect.generator_function == "build_rectangular_straight_fcstd"
    assert os.path.isfile(os.path.join(samples.root_path, "models", "rectangular_straight.FCStd"))

    diffuser = samples.get_type("end_diffuser_static")
    assert diffuser is not None
    assert diffuser.geometry.backend == "static"
    assert os.path.isfile(os.path.join(samples.root_path, diffuser.geometry.descriptor))
