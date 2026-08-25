"""
Tests for the GeometryResult/LayerGeometry contract every geometry backend
dispatch (PartScript, static, legacy generator) is normalized into by
HVACLibraryRegistry.build_geometry() -- see library/geometry_result.py.
"""

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library import geometry_result as gr
from freecad.HVAC.library.construction import LayerGeometry


class _Shape:
    """Stand-in for a Part.Shape -- identity is all these tests care about."""


def test_normalize_wraps_legacy_shape_dict_as_a_single_layer():
    shape = _Shape()
    result = gr.normalize({"shape": shape, "connection_lengths": [{"edge_key": "A"}]})

    assert set(result.layers.keys()) == {"shape"}
    assert result.layers["shape"].shape is shape
    assert result.connection_lengths == [{"edge_key": "A"}]


def test_normalize_accepts_layers_dict_with_plain_dict_values():
    casing_shape = _Shape()
    insulation_shape = _Shape()
    result = gr.normalize({
        "layers": {
            "casing": {"shape": casing_shape},
            "insulation": {"shape": insulation_shape},
        },
    })

    assert result.layers["casing"].shape is casing_shape
    assert result.layers["insulation"].shape is insulation_shape


def test_normalize_accepts_layers_dict_with_layer_geometry_values():
    casing_shape = _Shape()
    result = gr.normalize({
        "layers": {
            "casing": LayerGeometry(shape=casing_shape, roles=["structural_shell"]),
        },
    })

    assert result.layers["casing"].shape is casing_shape
    assert result.layers["casing"].roles == ["structural_shell"]


def test_normalize_does_not_invent_layers_beyond_what_the_backend_returned():
    # Only whatever roles/ids a backend actually mentions -- no forced
    # casing/insulation keys any more; arbitrary layer counts are allowed.
    result = gr.normalize({"layers": {"liner": {"shape": _Shape()}}})
    assert set(result.layers.keys()) == {"liner"}


def test_normalize_supports_arbitrary_layer_counts():
    result = gr.normalize({
        "layers": {
            "liner": {"shape": _Shape()},
            "absorber": {"shape": _Shape()},
            "jacket": {"shape": _Shape()},
        },
    })
    assert set(result.layers.keys()) == {"liner", "absorber", "jacket"}


def test_normalize_passthrough_of_a_geometry_result_returns_the_same_object():
    partial = gr.GeometryResult(layers={"casing": LayerGeometry(shape=_Shape())})
    result = gr.normalize(partial)
    assert result is partial


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
