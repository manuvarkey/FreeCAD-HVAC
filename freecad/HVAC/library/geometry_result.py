# SPDX-License-Identifier: LGPL-2.1-or-later
"""
The typed result every geometry backend dispatch (PartScript, static
BREP/STEP, legacy generator function) ultimately returns: a GeometryResult
holding one LayerGeometry per construction layer a type declares (see
library/construction.py), plus the non-shape outputs (connection lengths,
trim planes, computed properties) generators have always been able to
report.

Generator/PartScript/static-descriptor authors never construct these
dataclasses themselves -- per CLAUDE.md's "external code uses only
HVACLibraryAPI" rule, they keep returning plain dicts (either the legacy
`{"shape": ...}` form, or the `{"layers": {id: {"shape": ...}}}` form).
normalize() is the one place a raw backend return value is turned into a
real GeometryResult -- called once, from HVACLibraryRegistry.build_geometry()
(which also stamps each layer's `roles` from the type-def's own declared
construction, since normalize() itself has no access to the type-def), so
every caller downstream (DuctSegment/DuctComponent/DuctJunction) always
deals with a real GeometryResult, never a bare dict.
"""

from dataclasses import dataclass, field

from .construction import LayerGeometry, FeatureGeometry  # noqa: F401 -- re-exported for GeometryResult.features consumers

# Top-level raw-dict keys normalize() understands by name; anything else is
# preserved verbatim on GeometryResult.extra rather than silently dropped.
_KNOWN_RAW_KEYS = {
    "shape",
    "layers",
    "connection_lengths",
    "computed_properties",
    "start_trim_plane_json",
    "end_trim_plane_json",
}

# Legacy backends (static BREP/STEP, FCStd-template generators) only ever
# return one shape via {"shape": ...} -- normalize() wraps that as a single
# layer under this id. No roles are implied; the type-def's own
# "construction" block is what stamps roles on afterwards, if declared.
LEGACY_SHAPE_LAYER_ID = "shape"


@dataclass
class GeometryResult:
    """
    The full result of building one type's geometry for one sync/execute
    call. `layers` holds arbitrary library-defined layer ids -> LayerGeometry
    (see library/construction.py) -- no layer id or count is required or
    assumed here; a type may return one layer or many.

    `features` holds arbitrary library-defined feature ids -> FeatureGeometry
    -- unlike `layers`, this is never populated from a raw backend dict
    (normalize() leaves it empty); HVACLibraryRegistry.build_geometry()
    fills it in as a second pass, after the backend's own layers are built
    and normalized, by resolving and invoking each of the type-def's own
    declared construction.features generators (see Library.py). A disabled
    feature (its own enabled_parameter resolves False) has no entry here at
    all, not an entry with a null shape.
    """
    layers: dict = field(default_factory=dict)
    features: dict = field(default_factory=dict)
    connection_lengths: list = field(default_factory=list)
    computed_properties: dict = field(default_factory=dict)
    start_trim_plane_json: "str | None" = None
    end_trim_plane_json: "str | None" = None
    # Any other top-level key a backend returned that normalize() doesn't
    # know about by name -- preserved rather than dropped, for forward
    # compatibility with descriptor/generator outputs this module doesn't
    # yet interpret.
    extra: dict = field(default_factory=dict)


def _coerce_layer(value):
    """One layers[id] entry -> a LayerGeometry, accepting either a real
    LayerGeometry (new-style backend) or a plain {"shape": ...} dict (what
    generator/PartScript authors actually write, per the "only
    HVACLibraryAPI" rule -- they never import this module)."""
    if isinstance(value, LayerGeometry):
        return value
    if isinstance(value, dict):
        return LayerGeometry(shape=value.get("shape"))
    # A bare Part.Shape (or None) is also accepted directly as shorthand.
    return LayerGeometry(shape=value)


def normalize(raw) -> GeometryResult:
    """
    Turn whatever a geometry backend returned into a real GeometryResult.

    Accepts:
      - a GeometryResult already (returned as-is);
      - a dict with a "layers" key (new-style contract);
      - a legacy dict with a "shape" key (wrapped as a single layer,
        id "shape").
    """
    if isinstance(raw, GeometryResult):
        return raw

    if isinstance(raw, dict):
        if "layers" in raw:
            layers = {
                str(layer_id): _coerce_layer(value)
                for layer_id, value in dict(raw.get("layers") or {}).items()
            }
        else:
            layers = {LEGACY_SHAPE_LAYER_ID: LayerGeometry(shape=raw.get("shape"))}

        return GeometryResult(
            layers=layers,
            connection_lengths=list(raw.get("connection_lengths", []) or []),
            computed_properties=dict(raw.get("computed_properties", {}) or {}),
            start_trim_plane_json=raw.get("start_trim_plane_json"),
            end_trim_plane_json=raw.get("end_trim_plane_json"),
            extra={k: v for k, v in raw.items() if k not in _KNOWN_RAW_KEYS},
        )

    raise TypeError(
        "Geometry backend must return a GeometryResult or dict, got {!r}".format(type(raw))
    )
