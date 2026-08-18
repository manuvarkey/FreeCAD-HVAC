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
Renders a parametric OpenSCAD (.scad) file into a Part.Shape -- a shape-
building primitive a generator function can call the same way it already
calls HVACLibraryAPI.make_straight_shape/make_loft/etc., NOT a separate
generator "type" with its own dispatch or port/placement convention like
template_shapes.py's FCStd path. Placement, trim, and connection_lengths
stay the generator function's own responsibility, exactly as for every
other procedural generator.

Shells out to the `openscad` CLI (must be installed and on PATH -- this is
the first subprocess-based code in this addon, so there is no existing
convention here to defer to) with -D var=value overrides, exports STL,
imports it via FreeCAD's Mesh module, and converts to a Part.Shape via
Part.Shape().makeShapeFromMesh(...). This works uniformly for any .scad
file (hull(), minkowski(), extrudes, control flow -- all resolved by the
time OpenSCAD renders), at the cost of faceted/tessellated geometry rather
than true curved BREP -- round shapes will show visible facets. The mesh
needs to be reasonably watertight for makeShapeFromMesh to produce a valid
solid; basic duplicate-facet/point cleanup is applied first, but a badly-
formed .scad model can still fail to convert.

Tessellation quality ($fn) is NOT overridable from here -- set it as a
top-level `$fn = ...;` default inside the .scad file itself. A `-D
$fn=...` override was tried and dropped: under at least one real-world
sandboxed openscad invocation (FreeCAD's Flatpak build), every `-D`/
`--export-format` argument value observed to contain a literal `$` came
back from openscad as "the required argument ... is missing", while plain
`name=value` params (no `$`) were unaffected -- consistent with some
shell-relay layer between this addon's subprocess call and the real
openscad process (e.g. a flatpak-spawn --host hop) expanding `$fn` as an
unset shell variable before openscad ever sees it. Since OpenSCAD's
special variable names can't be renamed to dodge that, the safest fix is
just not passing one via -D at all.
"""

import os
import shutil
import subprocess
import tempfile

import Mesh
import Part


class OpenSCADGeneratorError(Exception):
    """Base for all OpenSCAD-shape-generation failures."""
    pass


class OpenSCADNotFoundError(OpenSCADGeneratorError):
    pass


class OpenSCADCompileError(OpenSCADGeneratorError):
    pass


class OpenSCADShapeError(OpenSCADGeneratorError):
    pass


def _format_define_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[{}]".format(",".join(_format_define_value(x) for x in v))
    return '"{}"'.format(str(v).replace("\\", "\\\\").replace('"', '\\"'))


def _build_command(openscad_bin, scad_path, stl_path, params):
    # Deliberately no --export-format flag: the .stl extension on stl_path
    # alone is enough to select STL export (ascii or binary, whichever the
    # installed openscad defaults to) -- --export-format=/--export-format
    # (both forms) was observed to fail unpredictably ("required argument
    # ... missing", or a bare usage dump with no diagnostic at all) under at
    # least one real-world sandboxed openscad invocation (FreeCAD Flatpak),
    # for reasons that couldn't be reproduced/diagnosed locally. Losing the
    # "force binary STL" preference isn't worth that fragility.
    cmd = [openscad_bin, "-o", stl_path]
    for name, value in (params or {}).items():
        cmd += ["-D", "{}={}".format(name, _format_define_value(value))]
    cmd.append(scad_path)
    return cmd


def build_shape_from_openscad(scad_path, params=None, timeout=60):
    openscad_bin = shutil.which("openscad")
    if openscad_bin is None:
        raise OpenSCADNotFoundError(
            "'openscad' executable not found on PATH; install OpenSCAD to use "
            "OpenSCAD-backed library types."
        )

    fd, stl_path = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        cmd = _build_command(openscad_bin, scad_path, stl_path, params)
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
        except subprocess.TimeoutExpired:
            raise OpenSCADCompileError(
                "openscad timed out ({}s) rendering '{}'".format(timeout, scad_path)
            )
        if result.returncode != 0:
            raise OpenSCADCompileError(
                "openscad failed to render '{}':\n{}".format(scad_path, result.stderr)
            )

        mesh = Mesh.Mesh(stl_path)
        mesh.removeDuplicatedFacets()
        mesh.removeDuplicatedPoints()

        shape = Part.Shape()
        try:
            shape.makeShapeFromMesh(mesh.Topology, 0.05)
        except Exception as e:
            raise OpenSCADShapeError(
                "Failed to convert OpenSCAD mesh from '{}' into a shape: {}".format(scad_path, e)
            )
        if shape.isNull():
            raise OpenSCADShapeError(
                "OpenSCAD render of '{}' produced an empty shape".format(scad_path)
            )

        for shell in shape.Shells:
            if shell.isValid() and shell.isClosed():
                solid = Part.makeSolid(shell)
                if not solid.isNull():
                    return solid
        return shape
    finally:
        try:
            if os.path.isfile(stl_path):
                os.remove(stl_path)
        except Exception:
            pass
