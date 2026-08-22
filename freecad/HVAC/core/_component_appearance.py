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
Renders CasingShape/InsulationShape with their own native CasingMaterial/
InsulationMaterial (Materials::PropertyMaterial -- see utils/materials.py),
on a single ViewProvider/FeaturePython object, using FreeCAD's own native
per-face appearance mechanism instead of a custom Coin scene graph:

    Step 1: Shape is always Part.makeCompound([casing, insulation]) in that
            fixed order (see core/_geometry_apply.py). CasingShape/
            InsulationShape are the very same two shapes that compound was
            built from, so len(CasingShape.Faces) is the compound's real,
            exact face partition -- not a guessed or hardcoded split.
    Step 2: build one appearance entry per face -- the first
            len(CasingShape.Faces) entries come from CasingMaterial, the
            rest from InsulationMaterial.
    Step 3: assign that per-face list to ViewObject.ShapeAppearance (the
            native per-face App::Material list FreeCAD >= 1.0 renders
            with).

Best-effort throughout: an unassigned/unrecognized material never raises,
it just leaves that component's faces at FreeCAD's own default appearance.
"""

import FreeCAD

from ..utils import materials as hvac_materials

# Property names whose change should trigger a re-render -- passed to
# ViewProvider.updateData(obj, prop) by DuctSegmentViewProvider/
# DuctComponentViewProvider.
TRIGGER_PROPERTIES = ("CasingShape", "InsulationShape", "CasingMaterial", "InsulationMaterial")

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
    Re-render `vobj` (a DuctSegment/DuctComponent ViewObject)'s
    CasingShape/InsulationShape faces from their own native
    CasingMaterial/InsulationMaterial. Safe to call any time; does nothing
    if the object has no faces at all, no ShapeAppearance property (older
    FreeCAD), or neither material resolves to a usable appearance.
    """
    obj = getattr(vobj, "Object", None)
    if obj is None or not hasattr(vobj, "ShapeAppearance"):
        return

    key = id(obj)
    if key in _rendering:
        return
    _rendering.add(key)
    try:
        casing_faces = _face_count(getattr(obj, "CasingShape", None))
        insulation_faces = _face_count(getattr(obj, "InsulationShape", None))
        if casing_faces + insulation_faces == 0:
            return

        casing_appearance = hvac_materials.get_view_appearance(getattr(obj, "CasingMaterial", None))
        insulation_appearance = hvac_materials.get_view_appearance(getattr(obj, "InsulationMaterial", None))
        if casing_appearance is None and insulation_appearance is None:
            return

        # A side with no usable material still needs an entry per its own
        # face (the list length must match the compound's total face
        # count), so it falls back to FreeCAD's own default appearance
        # rather than shifting the other side's entries out of alignment.
        default_appearance = FreeCAD.Material()
        entries = (
            [casing_appearance or default_appearance] * casing_faces
            + [insulation_appearance or default_appearance] * insulation_faces
        )
        try:
            vobj.ShapeAppearance = entries
        except Exception:
            pass
    finally:
        _rendering.discard(key)
