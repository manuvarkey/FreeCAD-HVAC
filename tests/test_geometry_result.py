"""
Tests for the GeometryResult/ComponentGeometry contract every geometry
backend dispatch (PartScript, static, legacy generator) is normalized into
by HVACLibraryRegistry.build_geometry() -- see library/geometry_result.py.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library import geometry_result as gr


class _Shape:
    """Stand-in for a Part.Shape -- identity is all these tests care about."""


def test_normalize_wraps_legacy_shape_dict_as_casing_with_null_insulation():
    casing_shape = _Shape()
    result = gr.normalize({"shape": casing_shape, "connection_lengths": [{"edge_key": "A"}]})

    assert result.casing.shape is casing_shape
    assert result.casing.material_role == "casing"
    assert result.insulation.shape is None
    assert result.insulation.material_role == "insulation"
    assert result.connection_lengths == [{"edge_key": "A"}]


def test_normalize_accepts_components_dict_with_plain_dict_values():
    casing_shape = _Shape()
    insulation_shape = _Shape()
    result = gr.normalize({
        "components": {
            "casing": {"shape": casing_shape},
            "insulation": {"shape": insulation_shape, "material_role": "insulation"},
        },
    })

    assert result.casing.shape is casing_shape
    assert result.insulation.shape is insulation_shape


def test_normalize_accepts_components_dict_with_component_geometry_values():
    casing_shape = _Shape()
    result = gr.normalize({
        "components": {
            "casing": gr.ComponentGeometry(shape=casing_shape, material_role="casing"),
        },
    })

    assert result.casing.shape is casing_shape
    # "insulation" wasn't mentioned at all -- normalize() must still fill it in.
    assert result.insulation is not None
    assert result.insulation.shape is None


def test_normalize_always_yields_both_casing_and_insulation_keys():
    # Even a components dict that only mentions a third, unrelated role.
    result = gr.normalize({"components": {"flange": {"shape": _Shape()}}})
    assert set(result.components.keys()) >= {"casing", "insulation", "flange"}
    assert result.casing.shape is None
    assert result.insulation.shape is None


def test_normalize_passthrough_of_a_geometry_result_fills_missing_roles():
    partial = gr.GeometryResult(components={"casing": gr.ComponentGeometry(shape=_Shape(), material_role="casing")})
    result = gr.normalize(partial)
    assert result is partial  # same object, just filled in
    assert result.insulation.shape is None


def test_normalize_preserves_trim_planes_and_computed_properties():
    result = gr.normalize({
        "shape": _Shape(),
        "start_trim_plane_json": '{"position": [0, 0, 0]}',
        "end_trim_plane_json": None,
        "computed_properties": {"angle": 42.0},
    })
    assert result.start_trim_plane_json == '{"position": [0, 0, 0]}'
    assert result.end_trim_plane_json is None
    assert result.computed_properties == {"angle": 42.0}


def test_normalize_preserves_unknown_keys_in_extra():
    result = gr.normalize({"shape": _Shape(), "some_future_output": "value"})
    assert result.extra == {"some_future_output": "value"}


def test_normalize_rejects_unsupported_raw_type():
    try:
        gr.normalize(42)
    except TypeError:
        pass
    else:
        raise AssertionError("Expected TypeError for a non-dict/GeometryResult raw value")
