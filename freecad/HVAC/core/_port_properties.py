# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Per-port properties on a FreeCAD object: a type-def property marked
`"per_port": true` holds one value for each connected port, so a fitting
can have any number of ports (a plenum, a manifold, an AHU).

On the object such a property is two lists: the values themselves, and a
hidden companion (`<Name>EdgeKeys`) naming the port each value belongs to.
Matching is always by edge_key, never by position. This module keeps the
two lists in step with the object's current ports and carries the keys
across a document reload, when every edge_key is regenerated.
"""

from ..library import validation


def per_port_property_defs(type_def):
    """The per-port property defs a type declares."""
    return [pdef for pdef in (getattr(type_def, "properties", []) or []) if getattr(pdef, "per_port", False)]


def sync_port_property_lists(obj, type_def, edge_keys):
    """
    Make every per-port property of `obj` hold exactly one value per current
    port, in port order. A port that already had a value keeps it; a new
    port gets the type-def default; a port that is gone loses its value.
    Returns True if anything changed.
    """
    edge_keys = [key for key in edge_keys if key]
    changed = False
    for pdef in per_port_property_defs(type_def):
        keys_name = validation.per_port_keys_name(pdef.name)
        if pdef.name not in obj.PropertiesList or keys_name not in obj.PropertiesList:
            continue
        values = list(getattr(obj, pdef.name, None) or [])
        keys = list(getattr(obj, keys_name, None) or [])
        if keys == edge_keys and len(values) == len(edge_keys):
            continue
        # Lists that disagree in length can't be matched up; start again from the default.
        stored = dict(zip(keys, values)) if len(keys) == len(values) else {}
        default = validation.normalize_property_value(pdef.prop_type, getattr(pdef, "default", None))
        setattr(obj, pdef.name, [stored.get(key, default) for key in edge_keys])
        setattr(obj, keys_name, list(edge_keys))
        changed = True
    return changed


def remap_port_property_keys(obj, type_def, edge_key_remap):
    """
    Translate the stored edge keys of every per-port property through
    `edge_key_remap` (old key -> new key). Needed once after a document
    reload, when base geometry gets new tags and with them new edge keys;
    without it every stored value would look like it belongs to a port
    that no longer exists. Returns True if anything changed.
    """
    if not edge_key_remap:
        return False
    changed = False
    for pdef in per_port_property_defs(type_def):
        keys_name = validation.per_port_keys_name(pdef.name)
        if keys_name not in obj.PropertiesList:
            continue
        keys = list(getattr(obj, keys_name, None) or [])
        new_keys = [edge_key_remap.get(key, key) for key in keys]
        if new_keys != keys:
            setattr(obj, keys_name, new_keys)
            changed = True
    return changed
