import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Mesh/PySide stubs

from freecad.HVAC.library import openscad_shapes
from freecad.HVAC.library.openscad_shapes import (
    OpenSCADCompileError,
    OpenSCADNotFoundError,
    _build_command,
    _format_define_value,
    build_shape_from_openscad,
)


# ----------------------------------------------------------------------------
# _format_define_value
# ----------------------------------------------------------------------------

def test_format_define_value_bool():
    assert _format_define_value(True) == "true"
    assert _format_define_value(False) == "false"


def test_format_define_value_number():
    assert _format_define_value(150) == "150"
    assert _format_define_value(150.5) == "150.5"


def test_format_define_value_string_is_quoted_and_escaped():
    assert _format_define_value("hello") == '"hello"'
    assert _format_define_value('he said "hi"') == '"he said \\"hi\\""'


def test_format_define_value_vector():
    assert _format_define_value([1, 2, 3]) == "[1,2,3]"
    assert _format_define_value((1.0, 2.0)) == "[1.0,2.0]"


# ----------------------------------------------------------------------------
# _build_command
# ----------------------------------------------------------------------------

def test_build_command_includes_params():
    cmd = _build_command("openscad", "model.scad", "out.stl", {"width": 100.0, "length": 200.0})

    assert cmd[0] == "openscad"
    assert cmd[1:3] == ["-o", "out.stl"]
    assert "--export-format" not in cmd
    assert cmd[-1] == "model.scad"
    assert "-D" in cmd and "width=100.0" in cmd
    assert "length=200.0" in cmd


def test_build_command_never_emits_a_dollar_sign_argument():
    # $fn overrides were dropped entirely -- a literal '$' in any -D value
    # was observed to get mangled by a shell-relay layer in at least one
    # real-world sandboxed openscad invocation (see openscad_shapes.py's
    # module docstring). No argument this function builds should ever
    # contain '$'.
    cmd = _build_command("openscad", "model.scad", "out.stl", {"width": 100.0})
    assert not any("$" in a for a in cmd if isinstance(a, str))


# ----------------------------------------------------------------------------
# build_shape_from_openscad error paths
# ----------------------------------------------------------------------------

def test_build_shape_from_openscad_raises_when_binary_missing(monkeypatch):
    monkeypatch.setattr(openscad_shapes.shutil, "which", lambda name: None)

    try:
        build_shape_from_openscad("model.scad")
        assert False, "expected OpenSCADNotFoundError"
    except OpenSCADNotFoundError:
        pass


def test_build_shape_from_openscad_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(openscad_shapes.shutil, "which", lambda name: "/usr/bin/openscad")

    class _Result:
        returncode = 1
        stderr = "ERROR: Parse error in file, line 3"

    monkeypatch.setattr(openscad_shapes.subprocess, "run", lambda *a, **k: _Result())

    try:
        build_shape_from_openscad(str(tmp_path / "model.scad"))
        assert False, "expected OpenSCADCompileError"
    except OpenSCADCompileError as e:
        assert "Parse error" in str(e)
