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
and core/_construction_schema.py), plus every construction feature's own
Feature_<id>_Shape, on a single ViewProvider/FeaturePython object, using
FreeCAD's own native per-face appearance mechanism instead of a custom Coin
scene graph:

    Step 1: Shape is always Part.makeCompound([layer shapes in
            obj.ConstructionLayerIds order, then feature shapes in
            obj.ConstructionFeatureIds order]) (see core/_geometry_apply.py).
            Each Layer_<id>_Shape/Feature_<id>_Shape is one of the very
            shapes that compound was built from, so
            len(Layer_<id>_Shape.Faces)/len(Feature_<id>_Shape.Faces) is
            the compound's real, exact per-layer/per-feature face
            partition -- not a guessed or hardcoded split.
    Step 2: build one appearance entry per face, walking layers then
            features in those same declared orders -- each layer's own
            faces get its own Layer_<id>_Material's appearance. A feature
            has no material of its own (see core/_construction_schema.py);
            it visually inherits its host layer's own appearance when
            "visible" (per its own visible_parameter, if declared),
            otherwise a fully-transparent override -- the mechanism behind
            "hide a feature without regenerating geometry": the feature's
            visible_parameter is marked Prop_NoRecompute (see
            core/_construction_schema.py), so changing it re-fires this
            module's updateData() trigger without ever touching Shape.
    Step 3: assign that per-face list to ViewObject.ShapeAppearance (the
            native per-face App::Material list FreeCAD >= 1.0 renders
            with).

Best-effort throughout: an unassigned/unrecognized material never raises,
it just leaves that layer's faces at FreeCAD's own default appearance.
"""

import FreeCAD

from ..utils import hvaclib
from ..utils import materials as hvac_materials
from . import _construction_schema


def _resolve_type_def(obj):
    """The resolved HVACTypeDef for obj's current LibraryId/TypeId, or None."""
    library_id = getattr(obj, "LibraryId", "")
    type_id = getattr(obj, "TypeId", "")
    if not library_id or not type_id:
        return None
    reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
    return reg.resolve_type(library_id, type_id)


def _feature_defs_by_id(obj):
    type_def = _resolve_type_def(obj)
    return {fdef.id: fdef for fdef in getattr(type_def, "features", None) or []}


def _trigger_property_names(obj):
    """
    Property names whose change should trigger a re-render for this
    object's own current construction -- passed to
    ViewProvider.updateData(obj, prop) by DuctSegmentViewProvider/
    DuctComponentViewProvider. Computed per-object (not a fixed tuple)
    since which Layer_<id>_*/Feature_<id>_* properties exist, and which
    ordinary property each feature's own visible_parameter names, depends
    on the object's currently-selected type.
    """
    names = []
    for layer_id in getattr(obj, "ConstructionLayerIds", []) or []:
        names.append(_construction_schema.shape_property_name(layer_id))
        names.append(_construction_schema.material_property_name(layer_id))

    feature_defs_by_id = _feature_defs_by_id(obj)
    for feature_id in getattr(obj, "ConstructionFeatureIds", []) or []:
        names.append(_construction_schema.feature_shape_property_name(feature_id))
        feature_def = feature_defs_by_id.get(feature_id)
        if feature_def is not None and feature_def.visible_parameter:
            names.append(feature_def.visible_parameter)

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


_FULLY_TRANSPARENT_TRANSPARENCY = 1.0


def _fully_transparent_appearance():
    """A FreeCAD.Material() forced fully transparent -- the "hidden" override for a non-visible feature's own faces."""
    appearance = FreeCAD.Material()
    try:
        appearance.Transparency = _FULLY_TRANSPARENT_TRANSPARENCY
    except Exception:
        pass
    return appearance


def apply_component_appearance(vobj):
    """
    Re-render `vobj` (a DuctSegment/DuctComponent ViewObject)'s construction
    layer faces from their own native Layer_<id>_Material, and every
    construction feature's own faces from its host layer's appearance (or
    fully transparent, if the feature currently resolves not-visible). Safe
    to call any time; does nothing if the object has no faces at all, no
    ShapeAppearance property (older FreeCAD), or nothing resolves to a
    usable appearance/override.
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
        layer_appearances_by_id = {}
        for layer_id in layer_ids:
            face_count = _face_count(getattr(obj, _construction_schema.shape_property_name(layer_id), None))
            appearance = hvac_materials.get_view_appearance(getattr(obj, _construction_schema.material_property_name(layer_id), None))
            layer_faces.append(face_count)
            layer_appearances_by_id[layer_id] = appearance

        feature_defs_by_id = _feature_defs_by_id(obj)
        feature_ids = list(getattr(obj, "ConstructionFeatureIds", []) or [])
        feature_faces = []
        feature_appearances = []
        for feature_id in feature_ids:
            face_count = _face_count(getattr(obj, _construction_schema.feature_shape_property_name(feature_id), None))
            feature_faces.append(face_count)

            feature_def = feature_defs_by_id.get(feature_id)
            visible = True
            if feature_def is not None and feature_def.visible_parameter:
                visible = bool(getattr(obj, feature_def.visible_parameter, True))

            if visible:
                appearance = layer_appearances_by_id.get(feature_def.host_layer) if feature_def is not None else None
            else:
                appearance = _fully_transparent_appearance()
            feature_appearances.append(appearance)

        if sum(layer_faces) + sum(feature_faces) == 0:
            return
        if all(a is None for a in layer_appearances_by_id.values()) and all(a is None for a in feature_appearances):
            return

        # A layer/feature with no usable appearance still needs an entry
        # per its own face (the list length must match the compound's
        # total face count), so it falls back to FreeCAD's own default
        # appearance rather than shifting later entries out of alignment.
        default_appearance = FreeCAD.Material()
        entries = []
        for layer_id, face_count in zip(layer_ids, layer_faces):
            entries.extend([layer_appearances_by_id[layer_id] or default_appearance] * face_count)
        for face_count, appearance in zip(feature_faces, feature_appearances):
            entries.extend([appearance or default_appearance] * face_count)

        try:
            vobj.ShapeAppearance = entries
        except Exception:
            pass
    finally:
        _rendering.discard(key)
