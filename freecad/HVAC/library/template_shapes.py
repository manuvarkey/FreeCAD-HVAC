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
Builds segment/junction shapes from a parametric FreeCAD (.FCStd) template
instead of a Python generator function -- the "generator": {"type":
"template", ...} variant of the type-def JSON schema (see
HVACTypeDef.generator_type in Library.py).

Template authoring convention (all in the template document's global frame):
  - An App::VarSet object (default name "Params") with one property per
    JSON "params" entry (e.g. Width/Height/Thickness -- App::PropertyLength
    etc.); the type's declared property values are written directly onto
    these VarSet properties before recompute. Feature dimensions elsewhere
    in the template are bound to them via expressions (e.g. Params.Width).
    For a 2-port template (a straight duct's length, an inline device's
    inter-port gap), the real distance between the two real ports -- which
    is NOT a declared App::Property, it comes from how the segment was
    routed -- is also available as a param source under the reserved key
    "Length"; map it in JSON the same way ("params": {"Length": "Length"})
    to drive a VarSet property of your own choosing.
  - A result object (default name "Result") whose .Shape (with .Placement
    left at Identity) is the shape to extract.
  - One port-reference object per port, named Port_0..Port_{N-1} (or the
    JSON "ports" override), ordered to match connected_ports order for
    junctions / [start, end] for segments. Only .Placement is read: local
    +Z is the port's outward flow direction, local +X is its cross-section
    reference axis, .Placement.Base is the port's local position.

Placement is a single rigid transform (no scale) anchored on port 0; any
other ports are best-effort validated against a tolerance and only produce
a console warning if they don't line up -- misalignment never blocks shape
generation (an inter-port distance that isn't correctly parametrized in the
template -- e.g. "Length" not wired to both the Result geometry and the
Port_1 marker -- is an authoring problem to fix, not a reason to hide the
shape).

Each call opens the template document fresh (hidden -- not added to the
Gui's tree/3D view), extracts a standalone copy of the Result shape, then
closes it again before returning -- there is no persistent document cache,
so the template is never left open in the session between generator calls.
"""

import math
import os

import FreeCAD

from ..utils import hvaclib
from .library_api import HVACLibraryAPI

class TemplateGeneratorError(Exception):
    """Base for all template-generator failures."""
    pass

class TemplateNotFoundError(TemplateGeneratorError):
    pass

class TemplateSchemaError(TemplateGeneratorError):
    pass

class TemplateRecomputeError(TemplateGeneratorError):
    pass

_DEFAULT_VARSET_NAME = "ParamsVarSet"


def resolve_template_path(lib, type_def):
    raw = type_def.generator_template_file
    if not raw:
        raise TemplateSchemaError(
            "Type '{}': generator.file is required for a template generator".format(type_def.id)
        )
    abs_path = os.path.normpath(os.path.join(lib.root_path, raw))
    if not os.path.isfile(abs_path):
        raise TemplateNotFoundError(
            "Template file not found: '{}' (library root '{}')".format(abs_path, lib.root_path)
        )
    return abs_path


def _split_param_target(target):
    if "." in target:
        varset_name, prop_attr = target.split(".", 1)
        return varset_name, prop_attr
    return _DEFAULT_VARSET_NAME, target


def _apply_params(doc, params_map, properties):
    for prop_name, target in (params_map or {}).items():
        if prop_name not in properties:
            # Tolerated: a template may only use a subset of the type's
            # declared properties.
            continue
        varset_name, prop_attr = _split_param_target(target)
        matches = doc.getObjectsByLabel(varset_name)
        if not matches:
            raise TemplateSchemaError(
                "Template '{}': VarSet '{}' not found".format(doc.Name, varset_name)
            )
        varset = matches[0]
        if not hasattr(varset, prop_attr):
            raise TemplateSchemaError(
                "Template '{}': VarSet '{}' has no property '{}'".format(
                    doc.Name, varset_name, prop_attr
                )
            )
        try:
            setattr(varset, prop_attr, properties[prop_name])
        except Exception as e:
            raise TemplateSchemaError(
                "Template '{}': VarSet '{}' property '{}' rejected value {!r}: {}".format(
                    doc.Name, varset_name, prop_attr, properties[prop_name], e
                )
            )

    doc.recompute()


def _get_result_shape(doc, result_name):
    matches = doc.getObjectsByLabel(result_name)
    if not matches or not hasattr(matches[0], "Shape"):
        raise TemplateSchemaError(
            "Template '{}': result object '{}' not found".format(doc.Name, result_name)
        )
    result_obj = matches[0]
    state = list(getattr(result_obj, "State", None) or [])
    if "Invalid" in state:
        raise TemplateRecomputeError(
            "Template '{}': object '{}' is invalid after recompute".format(doc.Name, result_name)
        )
    shape = result_obj.Shape
    if shape is None or shape.isNull():
        raise TemplateSchemaError(
            "Template '{}': result object '{}' has no shape".format(doc.Name, result_name)
        )
    return shape.copy()


def _port_names(type_def, degree):
    names = list(type_def.generator_template_ports or [])
    if names:
        return names
    return ["Port{}".format(i) for i in range(degree)]


def _real_ports_from_context(context):
    if "connected_ports" in context:
        return list(context["connected_ports"] or [])

    # Segment context: synthesize 2 pseudo-ports from start/end, using the
    # same "outward from the body" sign convention as real junction ports
    # (direction points away from the segment body into the connecting duct).
    sp = HVACLibraryAPI.vec(context["start_point"])
    ep = HVACLibraryAPI.vec(context["end_point"])
    axis = HVACLibraryAPI.unit(ep - sp)
    x_axis = context.get("profile_x_axis")
    return [
        {"position": sp, "direction": axis * -1.0, "profile_x_axis": x_axis},
        {"position": ep, "direction": axis, "profile_x_axis": x_axis},
    ]


def _get_template_ports(doc, port_names):
    objs = []
    for name in port_names:
        matches = doc.getObjectsByLabel(name)
        if not matches:
            raise TemplateSchemaError(
                "Template '{}': port reference object '{}' not found".format(doc.Name, name)
            )
        objs.append(matches[0])
    return objs


def _properties_with_computed_length(context, real_ports):
    # Not a declared App::Property -- the real inter-port distance for a
    # 2-port template (a straight duct's length, an inline device's actual
    # gap) comes from where it was routed, not from a fixed type property.
    # Expose it as a reserved param source key so the JSON "params" map can
    # feed it into the template the same way as any other property, driving
    # both the Result geometry and the Port_1 marker so the rigid-transform
    # alignment in build_shape_from_template actually lines up.
    properties = dict(context.get("properties", {}) or {})
    if len(real_ports) == 2:
        properties.setdefault("Length", (
            HVACLibraryAPI.port_position(real_ports[1]) - HVACLibraryAPI.port_position(real_ports[0])
        ).Length)
    return properties


def build_shape_from_template(lib, type_def, context):
    abs_path = resolve_template_path(lib, type_def)
    # openDocument()/closeDocument() both reassign FreeCAD.ActiveDocument as
    # a side effect (and leave it None once the template is closed, if it
    # was the last/active one) -- other code (e.g. hvaclib.makeLineKey) reads
    # FreeCAD.ActiveDocument assuming it's still the real working document,
    # so it must be restored before returning, not left wherever
    # open/close happened to leave it.
    prev_active = FreeCAD.ActiveDocument
    doc = FreeCAD.openDocument(abs_path, hidden=True)
    try:
        real_ports = _real_ports_from_context(context)
        properties = _properties_with_computed_length(context, real_ports)
        _apply_params(doc, type_def.generator_template_params, properties)
        shape = _get_result_shape(doc, type_def.generator_template_result_object)

        port_names = _port_names(type_def, len(real_ports))
        if len(port_names) != len(real_ports):
            raise TemplateSchemaError(
                "Template '{}': declares {} port(s) but type '{}' needs {}".format(
                    doc.Name, len(port_names), type_def.id, len(real_ports)
                )
            )
        template_ports = _get_template_ports(doc, port_names)

        # Port 0 fully determines the rigid transform (rotation + translation,
        # no scale); every other port is a best-effort validation only.
        real0 = real_ports[0]
        real_frame0, _x, _y, _z = hvaclib.make_profile_frame(
            direction=HVACLibraryAPI.port_direction(real0),
            preferred_x=HVACLibraryAPI.port_profile_x_axis(real0),
            origin=HVACLibraryAPI.port_position(real0),
        )
        transform = real_frame0.multiply(template_ports[0].Placement.inverse())

        tol_mm = float(type_def.generator_template_tol_mm or 0.0)
        tol_deg = float(type_def.generator_template_tol_deg or 0.0)
        for i in range(1, len(real_ports)):
            predicted = transform.multiply(template_ports[i].Placement)
            predicted_dir = predicted.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            real_i = real_ports[i]
            pos_err = (predicted.Base - HVACLibraryAPI.port_position(real_i)).Length
            ang_err_deg = math.degrees(
                HVACLibraryAPI.angle_between(predicted_dir, HVACLibraryAPI.port_direction(real_i))
            )
            if pos_err > tol_mm or ang_err_deg > tol_deg:
                FreeCAD.Console.PrintWarning(
                    "HVAC - Template '{}' (type '{}') port {} misaligned: {:.2f}mm / {:.2f}deg "
                    "(tolerance {}mm / {}deg). Applying best-fit placement anyway; check the "
                    "template's parametrized inter-port geometry.\n".format(
                        doc.Name, type_def.id, i, pos_err, ang_err_deg, tol_mm, tol_deg
                    )
                )

        shape.transformShape(transform.toMatrix(), True, False)
        return {"shape": shape}
    finally:
        try:
            FreeCAD.closeDocument(doc.Name)
        except Exception:
            pass
        if prev_active is not None:
            try:
                FreeCAD.setActiveDocument(prev_active.Name)
            except Exception:
                pass
