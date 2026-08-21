# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

################################################################################
#                                                                              #
#   Copyright (c) 2026 Francisco Rosa                                          #
#                                                                              #
#   This addon is free software; you can redistribute it and/or modify it      #
#   under the terms of the GNU Lesser General Public License as published      #
#   by the Free Software Foundation; either version 2.1 of the License, or     #
#   (at your option) any later version.                                        #
#                                                                              #
#   This addon is distributed in the hope that it will be useful,              #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                       #
#                                                                              #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with this addon. If not, see https://www.gnu.org/licenses    #
#                                                                              #
################################################################################

"""
Shared dynamic-property (type schema) bookkeeping for FreeCAD objects whose
available properties depend on a selected library type (DuctSegment,
DuctJunction's DuctComponent children). Internal to core/ -- not part of the
stable library_api.py surface for library/generator authors.
"""

from ..utils import hvaclib


def apply_type_schema(obj, library_id, type_id, *, protected_names=()):
    """
    Add/remove/edit-mode-sync dynamic properties on `obj` to match the
    declared property schema of (library_id, type_id). Returns True if
    anything on `obj` changed.

    protected_names: property names that are permanent/core on `obj` (e.g.
    DuctSegment's Diameter/Width/Height) and must never be removed even if
    the newly-selected type doesn't declare them -- only their editor_mode
    is synced to whether the active type declares them.
    """
    reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
    if not library_id or not type_id:
        return False

    type_def = reg.resolve_type(library_id, type_id)
    if type_def is None:
        return False

    changed = False
    protected = set(protected_names)
    active_prop_names = {pdef.name for pdef in (getattr(type_def, "properties", []) or [])}
    new_names = [n for n in active_prop_names if n not in protected]

    # Remove properties left over from a *previously* selected type (e.g.
    # switching TypeId to a different model) that the newly-selected type
    # doesn't declare, so the property editor doesn't accumulate stale
    # fields across model switches. Protected names are never removed.
    old_names = list(getattr(obj, "TypeSchemaPropertyNames", []) or [])
    for name in set(old_names) - set(new_names):
        if name in obj.PropertiesList:
            try:
                obj.removeProperty(name)
                changed = True
            except Exception:
                pass

    for pdef in getattr(type_def, "properties", []) or []:
        prop_added = False

        if pdef.name not in obj.PropertiesList:
            obj.addProperty(pdef.prop_type, pdef.name, pdef.group, pdef.description)
            changed = True
            prop_added = True

        try:
            current = getattr(obj, pdef.name)
        except Exception:
            current = None

        if getattr(pdef, "default", None) is not None:
            should_apply_default = prop_added or current in (None, "")
            if should_apply_default:
                try:
                    setattr(obj, pdef.name, pdef.default)
                    changed = True
                except Exception:
                    pass

        try:
            obj.setEditorMode(pdef.name, int(getattr(pdef, "editor_mode", 0) or 0))
        except Exception:
            pass

    # Protected (permanent) properties are never removed above -- but their
    # visibility still tracks whether the currently-active type declares
    # them, so a property that isn't relevant to this type just hides.
    for name in protected:
        if name in obj.PropertiesList:
            try:
                obj.setEditorMode(name, 0 if name in active_prop_names else 1)
            except Exception:
                pass

    if list(getattr(obj, "TypeSchemaPropertyNames", []) or []) != new_names:
        try:
            obj.TypeSchemaPropertyNames = new_names
            changed = True
        except Exception:
            pass

    return changed
