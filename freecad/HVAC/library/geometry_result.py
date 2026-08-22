# SPDX-License-Identifier: LGPL-2.1-or-later
"""
The typed result every geometry backend dispatch (PartScript, static
BREP/STEP, legacy generator function) ultimately returns:  a GeometryResult
holding one ComponentGeometry per physical piece of the fitting (at least
"casing" and "insulation", always both present), plus the non-shape outputs
(connection lengths, trim planes, computed properties) generators have
always been able to report.

Generator/PartScript/static-descriptor authors never construct these
dataclasses themselves -- per CLAUDE.md's "external code uses only
HVACLibraryAPI" rule, they keep returning plain dicts (either the legacy
`{"shape": ...}` form, or the new `{"components": {name: {"shape": ...}}}`
form). normalize() is the one place a raw backend return value is turned
into a real GeometryResult -- called once, from
HVACLibraryRegistry.build_geometry(), so every caller downstream
(DuctSegment/DuctComponent/DuctJunction) always deals with a real
GeometryResult, never a bare dict.
"""

from dataclasses import dataclass, field

# Every GeometryResult is guaranteed to carry at least these two component
# roles (see normalize()), even if a backend never mentions them -- the
# missing one just gets a null shape.
_REQUIRED_COMPONENT_ROLES = ("casing", "insulation")

# Top-level raw-dict keys normalize() understands by name; anything else is
# preserved verbatim on GeometryResult.extra rather than silently dropped.
_KNOWN_RAW_KEYS = {
    "shape",
    "components",
    "connection_lengths",
    "computed_properties",
    "start_trim_plane_json",
    "end_trim_plane_json",
}


@dataclass
class ComponentGeometry:
    """One physical piece of a fitting/segment (its shape, and which material role it plays)."""
    shape: object = None  # Part.Shape, or None if this component has no geometry (e.g. insulation disabled)
    material_role: str = ""


@dataclass
class GeometryResult:
    """
    The full result of building one type's geometry for one sync/execute
    call. `components` always has "casing" and "insulation" keys (see
    normalize()); other roles (lining, flange, ...) may also be present --
    see CLAUDE.md's extensibility note -- but only casing/insulation have
    first-class FreeCAD properties today.
    """
    components: dict = field(default_factory=dict)
    connection_lengths: list = field(default_factory=list)
    computed_properties: dict = field(default_factory=dict)
    start_trim_plane_json: "str | None" = None
    end_trim_plane_json: "str | None" = None
    # Any other top-level key a backend returned that normalize() doesn't
    # know about by name -- preserved rather than dropped, for forward
    # compatibility with descriptor/generator outputs this module doesn't
    # yet interpret.
    extra: dict = field(default_factory=dict)

    @property
    def casing(self):
        return self.components.get("casing")

    @property
    def insulation(self):
        return self.components.get("insulation")


def _coerce_component(name, value):
    """One components[name] entry -> a ComponentGeometry, accepting either a
    real ComponentGeometry (new-style backend) or a plain {"shape":...,
    "material_role":...} dict (what generator/PartScript authors actually
    write, per the "only HVACLibraryAPI" rule -- they never import this
    module)."""
    if isinstance(value, ComponentGeometry):
        return value
    if isinstance(value, dict):
        return ComponentGeometry(
            shape=value.get("shape"),
            material_role=str(value.get("material_role", "") or name),
        )
    # A bare Part.Shape (or None) is also accepted directly as shorthand.
    return ComponentGeometry(shape=value, material_role=name)


def normalize(raw) -> GeometryResult:
    """
    Turn whatever a geometry backend returned into a real GeometryResult
    with both "casing" and "insulation" component keys always present.

    Accepts:
      - a GeometryResult already (missing required roles get filled in);
      - a dict with a "components" key (new-style contract);
      - a legacy dict with a "shape" key (wrapped as the casing component,
        insulation defaults to no shape).
    """
    if isinstance(raw, GeometryResult):
        result = raw
    elif isinstance(raw, dict):
        if "components" in raw:
            components = {
                str(name): _coerce_component(str(name), value)
                for name, value in dict(raw.get("components") or {}).items()
            }
        else:
            components = {"casing": ComponentGeometry(shape=raw.get("shape"), material_role="casing")}

        result = GeometryResult(
            components=components,
            connection_lengths=list(raw.get("connection_lengths", []) or []),
            computed_properties=dict(raw.get("computed_properties", {}) or {}),
            start_trim_plane_json=raw.get("start_trim_plane_json"),
            end_trim_plane_json=raw.get("end_trim_plane_json"),
            extra={k: v for k, v in raw.items() if k not in _KNOWN_RAW_KEYS},
        )
    else:
        raise TypeError(
            "Geometry backend must return a GeometryResult or dict, got {!r}".format(type(raw))
        )

    # Guarantee both required roles exist, even if the backend never
    # mentioned one of them (e.g. every legacy single-shape generator has no
    # concept of insulation at all).
    for role in _REQUIRED_COMPONENT_ROLES:
        if role not in result.components:
            result.components[role] = ComponentGeometry(shape=None, material_role=role)

    return result
