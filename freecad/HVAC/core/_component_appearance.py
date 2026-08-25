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
Renders every construction layer's own Layer_<id>_Shape with its own native
Layer_<id>_Material (Materials::PropertyMaterial -- see utils/materials.py
and core/_construction_schema.py), on a single ViewProvider/FeaturePython
object, using FreeCAD's own native per-face appearance mechanism instead of
a custom Coin scene graph:

    Step 1: Shape is always Part.makeCompound([layer shapes in
            obj.ConstructionLayerIds order]) (see core/_geometry_apply.py).
            Each Layer_<id>_Shape is one of the very shapes that compound
            was built from, so len(Layer_<id>_Shape.Faces) is the
            compound's real, exact per-layer face partition -- not a
            guessed or hardcoded split.
    Step 2: build one appearance entry per face, walking layers in that
            same declared order -- each layer's own faces get its own
            Layer_<id>_Material's appearance.
    Step 3: assign that per-face list to ViewObject.ShapeAppearance (the
            native per-face App::Material list FreeCAD >= 1.0 renders
            with).

Best-effort throughout: an unassigned/unrecognized material never raises,
it just leaves that layer's faces at FreeCAD's own default appearance.
"""

import FreeCAD

from ..utils import materials as hvac_materials
from . import _construction_schema


def _trigger_property_names(obj):
    """
    Property names whose change should trigger a re-render for this
    object's own current construction -- passed to
    ViewProvider.updateData(obj, prop) by DuctSegmentViewProvider/
    DuctComponentViewProvider. Computed per-object (not a fixed tuple)
    since which Layer_<id>_* properties exist depends on the object's
    currently-selected type.
    """
    names = []
    for layer_id in getattr(obj, "ConstructionLayerIds", []) or []:
        names.append(_construction_schema.shape_property_name(layer_id))
        names.append(_construction_schema.material_property_name(layer_id))
    return names


def is_trigger_property(obj, prop):
    """True if a changed property named `prop` on `obj` should trigger a re-render."""
    return prop in _trigger_property_names(obj)


# Guards against a real FreeCAD re-entrancy quirk: querying a
# Materials::PropertyMaterial value's own appearance (e.g.
# hasAppearanceProperty()) can synchronously re-fire updateData() for that
# same property *before* the original call returns, which would otherwise
# recurse until the interpreter's stack limit crashes it. Keyed by the App
# object's id() -- one re-render per object may be genuinely in flight at
# any time, but never nested inside itself.
_rendering = set()


def _face_count(shape):
    if shape is None:
        return 0
    try:
        if shape.isNull():
            return 0
        return len(shape.Faces)
    except Exception:
        return 0


def apply_component_appearance(vobj):
    """
    Re-render `vobj` (a DuctSegment/DuctComponent ViewObject)'s construction
    layer faces from their own native Layer_<id>_Material. Safe to call any
    time; does nothing if the object has no faces at all, no
    ShapeAppearance property (older FreeCAD), or no layer resolves to a
    usable appearance.
    """
    obj = getattr(vobj, "Object", None)
    if obj is None or not hasattr(vobj, "ShapeAppearance"):
        return

    key = id(obj)
    if key in _rendering:
        return
    _rendering.add(key)
    try:
        layer_ids = list(getattr(obj, "ConstructionLayerIds", []) or [])
        layer_faces = []
        layer_appearances = []
        for layer_id in layer_ids:
            layer_faces.append(_face_count(getattr(obj, _construction_schema.shape_property_name(layer_id), None)))
            layer_appearances.append(
                hvac_materials.get_view_appearance(getattr(obj, _construction_schema.material_property_name(layer_id), None))
            )

        if sum(layer_faces) == 0:
            return
        if all(appearance is None for appearance in layer_appearances):
            return

        # A layer with no usable material still needs an entry per its own
        # face (the list length must match the compound's total face
        # count), so it falls back to FreeCAD's own default appearance
        # rather than shifting the other layers' entries out of alignment.
        default_appearance = FreeCAD.Material()
        entries = []
        for face_count, appearance in zip(layer_faces, layer_appearances):
            entries.extend([appearance or default_appearance] * face_count)

        try:
            vobj.ShapeAppearance = entries
        except Exception:
            pass
    finally:
        _rendering.discard(key)
