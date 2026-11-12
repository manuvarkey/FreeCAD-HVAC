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

"""HVACLibraryAPI, part: frames and section / profile creation. The class itself is in library_api.py."""

import math

import FreeCAD
import Part


# Bound to the real class by library_api.py once HVACLibraryAPI exists; the
# methods below call each other through it, as they always have.
HVACLibraryAPI = None


class _ProfilesMixin:
    # ------------------------------------------------------------------
    # Basic geometry: frames, profiles, solids, booleans, and topology
    # ------------------------------------------------------------------

    @staticmethod
    def _norm_profile_name(name):
        """Return the canonical API name for a supported profile alias."""
        key = str(name or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
        aliases = {
            "rectangle": "Rectangular",
            "rectangular": "Rectangular",
            "circle": "Circular",
            "circular": "Circular",
            "oval": "Oval",
            "flatoval": "Oval",
            "roundedrectangle": "RoundedRectangle",
            "roundrect": "RoundedRectangle",
            "polygon": "Polygon",
        }
        if key not in aliases:
            raise ValueError(f"Unsupported profile type: {name!r}")
        return aliases[key]

    @staticmethod
    def make_profile_frame(direction, preferred_x=None, origin=None):
        """Build a right-handed section frame whose z-axis follows *direction*."""
        z_axis = HVACLibraryAPI.unit(direction)
        x_axis = None

        if preferred_x is not None:
            candidate = HVACLibraryAPI.vec(preferred_x)
            if candidate.Length > HVACLibraryAPI.EPS:
                candidate = candidate - z_axis * candidate.dot(z_axis)
                if candidate.Length > HVACLibraryAPI.EPS:
                    candidate.normalize()
                    x_axis = candidate

        if x_axis is None:
            reference = FreeCAD.Vector(0, 0, 1)
            if abs(z_axis.dot(reference)) > 0.99:
                reference = FreeCAD.Vector(1, 0, 0)
            x_axis = HVACLibraryAPI.unit(reference.cross(z_axis))

        y_axis = HVACLibraryAPI.unit(z_axis.cross(x_axis))
        x_axis = HVACLibraryAPI.unit(y_axis.cross(z_axis))

        matrix = FreeCAD.Matrix()
        matrix.A11, matrix.A12, matrix.A13 = x_axis.x, y_axis.x, z_axis.x
        matrix.A21, matrix.A22, matrix.A23 = x_axis.y, y_axis.y, z_axis.y
        matrix.A31, matrix.A32, matrix.A33 = x_axis.z, y_axis.z, z_axis.z
        placement = FreeCAD.Placement(matrix)
        if origin is not None:
            placement.Base = HVACLibraryAPI.vec(origin)
        return placement, x_axis, y_axis, z_axis

    # ------------------------------------------------------------------
    # Section/profile creation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def make_rectangular_wire(center, x_axis, y_axis, width, height):
        """A rectangular section wire, centered on `center` and aligned to x_axis/y_axis."""
        c = HVACLibraryAPI.vec(center)
        x = HVACLibraryAPI.unit(x_axis) * (float(width) * 0.5)
        y = HVACLibraryAPI.unit(y_axis) * (float(height) * 0.5)

        p1 = c - x - y
        p2 = c + x - y
        p3 = c + x + y
        p4 = c - x + y

        return Part.Wire([Part.makeLine(p1, p2), Part.makeLine(p2, p3), Part.makeLine(p3, p4), Part.makeLine(p4, p1)])

    @staticmethod
    def make_circular_wire(center, normal, diameter):
        """A circular section wire, centered on `center`, lying in the plane perpendicular to `normal`."""
        c = HVACLibraryAPI.vec(center)
        n = HVACLibraryAPI.unit(normal)
        r = float(diameter) * 0.5
        circle = Part.Circle(c, n, r)
        return Part.Wire([Part.Edge(circle)])

    @staticmethod
    def make_split_circular_wire(center, x_axis, y_axis, diameter, width, height):
        """
        A circle drawn as four arcs, split where the lines from its centre to
        the corners of a width x height rectangle (on the same axes) cross it.

        A plain circle is one edge with its seam wherever OCC puts it, so a
        loft from it to a rectangle twists. This one has a corner for each
        corner of the rectangle, in the same order as make_rectangular_wire,
        so the two loft edge for edge.
        """
        c = HVACLibraryAPI.vec(center)
        x = HVACLibraryAPI.unit(x_axis)
        y = HVACLibraryAPI.unit(y_axis)
        r = float(diameter) * 0.5
        diagonal = math.hypot(float(width), float(height))
        if r <= 0.0 or diagonal <= 0.0:
            raise ValueError("Split circle needs a positive diameter and rectangle")
        ux, uy = float(width) / diagonal, float(height) / diagonal

        def point(a, b):
            return c + x * (a * r) + y * (b * r)

        corners = [(-ux, -uy), (ux, -uy), (ux, uy), (-ux, uy)]
        middles = [(0.0, -1.0), (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)]
        edges = [
            Part.Arc(point(*corners[i]), point(*middles[i]), point(*corners[(i + 1) % 4])).toShape() for i in range(4)
        ]
        return Part.Wire(edges)

    @staticmethod
    def make_oval_wire(center, x_axis, y_axis, width, height):
        """
        Flat-oval / obround section.

        width  = total overall extent along x_axis
        height = total overall extent along y_axis

        Either one may be the longer side: the straight sides run along
        whichever axis is longer, the rounded caps close the shorter one.
        """
        c = HVACLibraryAPI.vec(center)
        x = HVACLibraryAPI.unit(x_axis)
        y = HVACLibraryAPI.unit(y_axis)

        width = float(width or 0.0)
        height = float(height or 0.0)

        if width <= 0.0 or height <= 0.0:
            raise ValueError("Oval section requires positive Width and Height")

        # A tall oval is a wide one turned 90 degrees: swap the frame so the
        # code below can always build with the major axis along x. Negating
        # the new y keeps the same normal (x cross y), so the wire's winding
        # direction doesn't change.
        if width < height:
            x, y = y, x * -1.0
            width, height = height, width

        r = 0.5 * height
        straight = width - height

        # Degenerates to a circle when width == height
        if straight <= HVACLibraryAPI.EPS:
            return HVACLibraryAPI.make_circular_wire(c, x.cross(y), height)

        half_straight = 0.5 * straight

        left_center = c - x * half_straight
        right_center = c + x * half_straight

        p_lt = left_center + y * r
        p_lb = left_center - y * r
        p_rt = right_center + y * r
        p_rb = right_center - y * r

        # top and bottom straight edges
        e_top = Part.makeLine(p_lt, p_rt)
        e_bottom = Part.makeLine(p_rb, p_lb)

        normal = x.cross(y)
        if normal.Length <= HVACLibraryAPI.EPS:
            raise ValueError("Invalid oval frame")
        normal.normalize()

        # Left semicircle: top -> bottom
        left_arc = Part.Arc(p_lt, left_center - x * r, p_lb).toShape()

        # Right semicircle: bottom -> top
        right_arc = Part.Arc(p_rb, right_center + x * r, p_rt).toShape()

        return Part.Wire([e_top, right_arc, e_bottom, left_arc])

    @staticmethod
    def make_section_wire(profile, section_params, center, direction, profile_x_axis=None):
        """Build a section wire (circular/rectangular/oval, by `profile`) at `center`, facing `direction`."""
        profile = str(profile or "")
        params = dict(section_params or {})
        center = HVACLibraryAPI.vec(center)
        direction = HVACLibraryAPI.unit(direction)
        _, x_axis, y_axis, _ = HVACLibraryAPI.make_profile_frame(direction, profile_x_axis, center)

        if profile == "Circular":
            diameter = float(params.get("Diameter", 0.0) or 0.0)
            if diameter <= 0.0:
                raise ValueError("Circular section requires a positive Diameter")
            return HVACLibraryAPI.make_circular_wire(center, direction, diameter)

        if profile == "Rectangular":
            width = float(params.get("Width", 0.0) or 0.0)
            height = float(params.get("Height", 0.0) or 0.0)
            if width <= 0.0 or height <= 0.0:
                raise ValueError("Rectangular section requires positive Width and Height")
            return HVACLibraryAPI.make_rectangular_wire(center, x_axis, y_axis, width, height)

        if profile == "Oval":
            width = float(params.get("Width", 0.0) or 0.0)
            height = float(params.get("Height", 0.0) or 0.0)
            return HVACLibraryAPI.make_oval_wire(center, x_axis, y_axis, width, height)

        raise ValueError("Unsupported profile '{}'".format(profile))

    @staticmethod
    def make_section_wire_from_port(port):
        """make_section_wire, reading profile/center/direction/section_params straight from a port dict."""
        profile = HVACLibraryAPI.port_profile(port)
        center = HVACLibraryAPI.port_position(port)
        direction = HVACLibraryAPI.port_direction(port)
        preferred_x = HVACLibraryAPI.port_profile_x_axis(port)
        section_params = HVACLibraryAPI.port_section_params(port)

        return HVACLibraryAPI.make_section_wire(profile, section_params, center, direction, profile_x_axis=preferred_x)

    @staticmethod
    def make_section_face(profile, section_params, center, direction, profile_x_axis=None):
        """Same as make_section_wire, but returns a flat face instead of just the wire outline."""
        wire = HVACLibraryAPI.make_section_wire(
            profile=profile,
            section_params=section_params,
            center=center,
            direction=direction,
            profile_x_axis=profile_x_axis,
        )
        return Part.Face(wire)

    @staticmethod
    def make_section_face_from_port(port):
        """make_section_face, reading profile/center/direction/section_params straight from a port dict."""
        profile = HVACLibraryAPI.port_profile(port)
        center = HVACLibraryAPI.port_position(port)
        direction = HVACLibraryAPI.port_direction(port)
        preferred_x = HVACLibraryAPI.port_profile_x_axis(port)
        section_params = HVACLibraryAPI.port_section_params(port)

        return HVACLibraryAPI.make_section_face(profile, section_params, center, direction, profile_x_axis=preferred_x)
