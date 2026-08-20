# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Parameter and context validation for HVAC library items: checks a type-def's
own property values (normalize_property_value/validate_value/resolve_params)
and whether a type-def is even a valid fit for the current network context
(context_violations and friends), a compact JSON-Schema-like rule set rather
than a full validation library.
"""

import math


_QUANTITY_TYPES = {
    "App::PropertyLength",
    "App::PropertyDistance",
    "App::PropertyAngle",
    "App::PropertyFloatConstraint",
    "App::PropertyPressure",
}
_STRING_TYPES = {
    "App::PropertyString",
    "App::PropertyEnumeration",
    "App::PropertyPath",
    "App::PropertyFile",
}


def normalize_property_value(prop_type, value):
    """Convert FreeCAD property values into small, predictable Python values."""
    if value is None:
        return None

    prop_type = str(prop_type or "")

    if prop_type == "App::PropertyBool":
        return bool(value)
    if prop_type.startswith("App::PropertyInteger"):
        return int(value)
    if prop_type == "App::PropertyFloat" or prop_type in _QUANTITY_TYPES:
        return float(value.Value if hasattr(value, "Value") else value)
    if prop_type in _STRING_TYPES:
        return str(value)

    # Keep compound FreeCAD values such as vectors/placements unchanged.
    return value


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_value(type_id, prop_name, value, rules):
    """Validate one normalized property using a compact JSON-Schema-like subset."""
    rules = dict(rules or {})
    prefix = "Type '{}', property '{}'".format(type_id, prop_name)

    if value is None:
        return

    if _numeric(value) and isinstance(value, float) and not math.isfinite(value):
        raise ValueError("{} must be finite".format(prefix))

    if "enum" in rules and value not in list(rules["enum"] or []):
        raise ValueError("{} must be one of {!r}; got {!r}".format(prefix, rules["enum"], value))

    if "minimum" in rules and value < rules["minimum"]:
        raise ValueError("{} must be >= {}; got {}".format(prefix, rules["minimum"], value))
    if "maximum" in rules and value > rules["maximum"]:
        raise ValueError("{} must be <= {}; got {}".format(prefix, rules["maximum"], value))
    if "exclusiveMinimum" in rules and value <= rules["exclusiveMinimum"]:
        raise ValueError("{} must be > {}; got {}".format(prefix, rules["exclusiveMinimum"], value))
    if "exclusiveMaximum" in rules and value >= rules["exclusiveMaximum"]:
        raise ValueError("{} must be < {}; got {}".format(prefix, rules["exclusiveMaximum"], value))

    if "multipleOf" in rules:
        step = float(rules["multipleOf"])
        if step <= 0.0:
            raise ValueError("{} has invalid multipleOf {}".format(prefix, step))
        q = float(value) / step
        if not math.isclose(q, round(q), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("{} must be a multiple of {}; got {}".format(prefix, step, value))

    if "minLength" in rules and len(value) < int(rules["minLength"]):
        raise ValueError("{} length must be >= {}".format(prefix, rules["minLength"]))
    if "maxLength" in rules and len(value) > int(rules["maxLength"]):
        raise ValueError("{} length must be <= {}".format(prefix, rules["maxLength"]))


def resolve_params(type_def, obj=None, supplied=None):
    """
    Resolve type properties from supplied values / FreeCAD object / JSON defaults,
    normalize them, and apply declarative validation.
    """
    supplied = dict(supplied or {})
    params = {}

    for pdef in list(getattr(type_def, "properties", []) or []):
        if pdef.name in supplied:
            raw = supplied[pdef.name]
        elif obj is not None and hasattr(obj, pdef.name):
            raw = getattr(obj, pdef.name)
        else:
            raw = getattr(pdef, "default", None)

        value = normalize_property_value(pdef.prop_type, raw)
        if bool(getattr(pdef, "required", True)) and value is None:
            raise ValueError(
                "Type '{}': required property '{}' has no value".format(type_def.id, pdef.name)
            )

        validate_value(type_def.id, pdef.name, value, getattr(pdef, "validation", {}))
        params[pdef.name] = value

    return params


def context_violations(type_def, context):
    """
    Return a list of human-readable constraint violations for the given
    context against a type descriptor's constraints/topology/profile
    restrictions. An empty list means the context is fully compatible.

    This is the shared compatibility rule set used both by validate_context()
    (raises on the first violation, for geometry execution) and by the
    library registry's matches_type()/select_type() (boolean/ranking use,
    for automatic type selection) -- see freecad/HVAC/library/Library.py.
    """
    violations = []
    constraints = dict(getattr(type_def, "constraints", {}) or {})
    category = str(getattr(type_def, "category", "") or "")
    profiles = set(getattr(type_def, "profiles", []) or [])

    if category == "junction":
        ports = list(context.get("connected_ports", []) or [])
        degree = len(ports)

        # Degree: how many ducts connect here.
        if "degree" in constraints and degree != int(constraints["degree"]):
            violations.append(
                "Type '{}' requires degree {}; got {}".format(
                    type_def.id, constraints["degree"], degree
                )
            )
        if "degree_min" in constraints and degree < int(constraints["degree_min"]):
            violations.append(
                "Type '{}' requires degree >= {}; got {}".format(
                    type_def.id, constraints["degree_min"], degree
                )
            )
        if "degree_max" in constraints and degree > int(constraints["degree_max"]):
            violations.append(
                "Type '{}' requires degree <= {}; got {}".format(
                    type_def.id, constraints["degree_max"], degree
                )
            )

        # Topology: through/branch/cross/multiport/end, as classified by NetworkParser.
        expected_topology = str(getattr(type_def, "topology", "") or "")
        actual_topology = str(context.get("topology", "") or "")
        if (
            expected_topology
            and expected_topology != "generic"
            and actual_topology
            and expected_topology != actual_topology
        ):
            violations.append(
                "Type '{}' requires topology '{}'; got '{}'".format(
                    type_def.id, expected_topology, actual_topology
                )
            )

        # Profile: does every connected port's duct shape (Circular/
        # Rectangular/Oval) match what this type supports? "Generic" is a
        # wildcard used by profile-agnostic placeholder types (e.g. topology
        # marker fittings), which must accept any connected duct profile
        # rather than being restricted to a literal "Generic" port profile.
        if profiles and "Generic" not in profiles:
            for index, port in enumerate(ports):
                profile = str(port.get("profile", "") or "")
                if profile and profile not in profiles:
                    violations.append(
                        "Type '{}' does not support profile '{}' on port {}".format(
                            type_def.id, profile, index
                        )
                    )

    elif category == "segment" and profiles and "Generic" not in profiles:
        profile = str(context.get("profile", "") or "")
        if profile and profile not in profiles:
            violations.append(
                "Type '{}' does not support segment profile '{}'".format(type_def.id, profile)
            )

    return violations


def validate_context(type_def, context):
    """Apply structural constraints declared by the type descriptor."""
    violations = context_violations(type_def, context)
    if violations:
        raise ValueError(violations[0])


def is_context_valid(type_def, context):
    """Boolean form of context_violations(), for registry matching use."""
    return not context_violations(type_def, context)
