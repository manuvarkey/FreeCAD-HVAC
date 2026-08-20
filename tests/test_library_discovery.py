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


def test_through_elbow_rectangular_uses_partscript_backend_and_reactive_properties():
    reg = _load_registry()
    type_def = reg._libraries["smacna"].get_type("through_elbow_rectangular")
    assert type_def is not None
    assert type_def.profiles == ["Rectangular"]
    assert type_def.geometry.backend == "partscript"
    assert os.path.isfile(os.path.join(reg._libraries["smacna"].root_path, type_def.geometry.file))

    by_name = {p.name: p for p in type_def.properties}
    reactive_names = {"d_h_axis_02", "d_v_axis_02", "angle"}
    input_names = {"r_axis", "thickness", "flange_height", "flange_thickness", "ShowFlange1", "ShowFlange2"}
    assert reactive_names | input_names == set(by_name.keys())

    for name in reactive_names:
        assert by_name[name].editor_mode == 1, name
    for name in input_names:
        assert by_name[name].editor_mode == 0, name

    assert not any(n.startswith("insulation") for n in by_name)


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
