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
Standardized construction-layer vocabulary.

A duct/fitting's wall can be built from any number of physical layers (a
bare sheet-metal wall, a wall plus insulation wrap, a wall plus an acoustic
fill plus a perforated liner, ...). Library authors invent their own layer
*ids* ("casing", "liner", "absorber", ...) and decide how many layers a type
has and how they're geometrically built -- that recipe lives entirely in
library JSON/generator code (see Library.py's "construction" type-def block
and library_api.py's build_concentric_layers()).

What core owns instead is the semantic *role* vocabulary below: the fixed,
standardized set of physical roles a layer can play. Downstream code
(materials, appearance, and later airflow/acoustic/thermal/detailing) must
only ever branch on a layer's roles, never on its library-chosen id -- that's
what keeps core/application code generic across arbitrary library-defined
constructions.

This module is pure data -- no FreeCAD import, same "library/core-neutral"
convention as geometry_result.py -- so it can be imported from both
library/ (type-def loading, geometry backends) and core/ (the construction
query facade) without pulling in FreeCAD.
"""

from dataclasses import dataclass, field

# A layer may carry more than one role at once -- e.g. a single-wall duct's
# only layer is both the flow surface and the structural shell.
ROLE_FLOW_SURFACE = "flow_surface"
ROLE_STRUCTURAL_SHELL = "structural_shell"
ROLE_THERMAL_INSULATION = "thermal_insulation"
ROLE_ACOUSTIC_ABSORBER = "acoustic_absorber"
ROLE_ACOUSTIC_LINER = "acoustic_liner"
ROLE_VAPOR_BARRIER = "vapor_barrier"
ROLE_OUTER_JACKET = "outer_jacket"
ROLE_FIRE_PROTECTION = "fire_protection"

ALL_LAYER_ROLES = (
    ROLE_FLOW_SURFACE,
    ROLE_STRUCTURAL_SHELL,
    ROLE_THERMAL_INSULATION,
    ROLE_ACOUSTIC_ABSORBER,
    ROLE_ACOUSTIC_LINER,
    ROLE_VAPOR_BARRIER,
    ROLE_OUTER_JACKET,
    ROLE_FIRE_PROTECTION,
)


def role_property_suffix(role):
    """
    PascalCase form of a ROLE_* string, e.g. "structural_shell" ->
    "StructuralShell" -- the single place that maps a semantic role onto
    the network-level DefaultMaterial_<Role> FreeCAD property name (see
    core/Network.py and core/_construction_schema.py), so the two always
    agree on the mapping.
    """
    return "".join(part.capitalize() for part in str(role).split("_"))


@dataclass
class ConstructionLayerDef:
    """
    One library-declared construction layer, parsed from a type-def's
    "construction" JSON block (see Library.py._load_type_def_file).

    id: library-chosen, stable within that type-def (e.g. "casing",
        "liner", "absorber") -- never interpreted by core/application code,
        only used to pair a layer's def with its generated geometry and its
        own Layer_<id>_Shape/Layer_<id>_Material FreeCAD properties.
    roles: standardized ROLE_* strings this layer plays (see module docstring).
    default_material_role: which network-level default-material role (a
        ROLE_* value) to fall back to when this layer has no material of
        its own yet -- see core/Segment.py's applyOwnerDefaults().
    default_material_uuid: an explicit default material, taking priority
        over default_material_role when set.
    thickness_property: name of the type-def property holding this layer's
        thickness, if any -- purely informational metadata for detailing/
        mass-calculation consumers; core never interprets it itself (the
        layer's actual generated Shape is the source of truth for volume).
    """
    id: str
    roles: list = field(default_factory=list)
    default_material_role: "str | None" = None
    default_material_uuid: "str | None" = None
    thickness_property: "str | None" = None


@dataclass
class LayerGeometry:
    """
    One physical layer's generated geometry for one build_geometry() call.

    `roles` is stamped on by HVACLibraryRegistry.build_geometry() from the
    type-def's matching ConstructionLayerDef (see Library.py), so a
    GeometryResult stays self-describing without needing the type-def
    around afterwards.
    """
    shape: object = None  # Part.Shape, or None if this layer has no geometry this call
    roles: list = field(default_factory=list)
