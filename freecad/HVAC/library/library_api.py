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

"""Stable public helpers and geometry primitives for HVAC libraries.

Library generators and PartScripts receive this class as
``context["hvac_api"]``. The API is grouped from low-level, non-shape
helpers through direct Part geometry operations to HVAC-specific convenience
recipes. Library code must use this surface instead of importing FreeCAD,
Part, or internal HVAC modules directly.
"""

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

import FreeCAD
import Part

from ..analysis import physics as airflow

_EPS = 1.0e-7


@dataclass(frozen=True)
class HVACProfile:
    """Immutable section description paired with its generated closed wire.

    ``local_points`` is populated only for polygon profiles so analytical
    offsets can be rebuilt in the same local two-dimensional frame.
    """

    profile: str
    params: Mapping[str, Any]
    center: Any
    direction: Any
    profile_x_axis: Any
    wire: Any = field(repr=False)
    local_points: tuple[tuple[float, float], ...] = ()


class HVACLibraryAPI:
    """Stable geometry and context API exposed to HVAC library code."""

    API_VERSION = 2
    EPS = 1.0e-9

    # ------------------------------------------------------------------
    # Basic helpers: vectors, contexts, ports, and trim records
    # ------------------------------------------------------------------

    @staticmethod
    def vec(v):
        """Return *v* as a FreeCAD vector."""
        if hasattr(v, "x"):
            return FreeCAD.Vector(v)
        return FreeCAD.Vector(*v)

    @staticmethod
    def xyz(v):
        """Return a vector-like value as an ``(x, y, z)`` tuple."""
        vv = HVACLibraryAPI.vec(v)
        return (vv.x, vv.y, vv.z)

    @staticmethod
    def unit(v, eps=None):
        """Return a normalized copy of *v*, rejecting a zero-length vector."""
        eps = HVACLibraryAPI.EPS if eps is None else float(eps)
        out = HVACLibraryAPI.vec(v)
        if out.Length <= eps:
            raise ValueError("Zero-length vector")
        out.normalize()
        return out

    @staticmethod
    def is_zero(v, eps=None):
        """Return whether *v* is no longer than the requested tolerance."""
        eps = HVACLibraryAPI.EPS if eps is None else float(eps)
        return HVACLibraryAPI.vec(v).Length <= eps

    @staticmethod
    def angle_between(u1, u2):
        """Return the smaller angle between two vectors in radians."""
        a = HVACLibraryAPI.unit(u1)
        b = HVACLibraryAPI.unit(u2)
        dot = max(-1.0, min(1.0, float(a.dot(b))))
        return math.acos(dot)

    @staticmethod
    def rotate_vector(v, axis, angle):
        """Rotate vector *v* by *angle* radians around a unit *axis* (Rodrigues' formula)."""
        v = HVACLibraryAPI.vec(v)
        axis = HVACLibraryAPI.unit(axis)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        return v * cos_a + axis.cross(v) * sin_a + axis * (axis.dot(v) * (1.0 - cos_a))

    @staticmethod
    def average_point(points):
        """Return the arithmetic mean of points, or the origin for an empty input."""
        pts = list(points or [])
        if not pts:
            return FreeCAD.Vector(0, 0, 0)
        s = FreeCAD.Vector(0, 0, 0)
        for p in pts:
            s = s + HVACLibraryAPI.vec(p)
        return s * (1.0 / float(len(pts)))

    @staticmethod
    def distance_between_lines(origin_i, dir_i, origin_j, dir_j):
        """
        Compute the perpendicular distance between two lines in 3D.
        Each line is defined by a FreeCAD.Vector origin and direction.
        Handles parallel/coincident lines as a special case.
        """    
        u_i = FreeCAD.Vector(dir_i).normalize()
        u_j = FreeCAD.Vector(dir_j).normalize()
        w0  = FreeCAD.Vector(origin_i) - FreeCAD.Vector(origin_j)
    
        cross = u_i.cross(u_j)
        denom = cross.Length
    
        if denom < 1e-10:
            # Lines are parallel — distance is |w0 × u_i|
            return w0.cross(u_i).Length
    
        # Skew lines — |(w0 · (d_i × d_j))| / |d_i × d_j|
        return abs(w0.dot(cross)) / denom

    @staticmethod
    def closest_points_on_lines(p0, d0, p1, d1):
        """
        Return closest points c0 on L0 and c1 on L1 for:
    
            L0(t) = p0 + t d0
            L1(s) = p1 + s d1
    
        Returns (None, None) for nearly parallel lines.
        """
        p0 = FreeCAD.Vector(p0)
        p1 = FreeCAD.Vector(p1)
        d0 = FreeCAD.Vector(d0)
        d1 = FreeCAD.Vector(d1)
    
        if d0.Length <= HVACLibraryAPI.EPS or d1.Length <= HVACLibraryAPI.EPS:
            raise ValueError("Line direction too small")
    
        d0.normalize()
        d1.normalize()
    
        w0 = p0 - p1
        a = d0.dot(d0)
        b = d0.dot(d1)
        c = d1.dot(d1)
        d = d0.dot(w0)
        e = d1.dot(w0)
    
        denom = a * c - b * b
        if abs(denom) <= HVACLibraryAPI.EPS:
            return None, None
    
        t = (b * e - c * d) / denom
        s = (a * e - b * d) / denom
    
        c0 = p0 + d0 * t
        c1 = p1 + d1 * s
        return c0, c1

    @staticmethod
    def virtual_corner_for_lines(p0, u0, p1, u1):
        """
        Compute the virtual corner from the two offset segment centerlines.
        Lines starting from p0 with direction u0 and from p1 with direction u1.
        """
        d0 = FreeCAD.Vector(u0)
        d1 = FreeCAD.Vector(u1)
        d0.normalize()
        d1.normalize()
    
        # Lines traced back toward the junction
        c0, c1 = HVACLibraryAPI.closest_points_on_lines(
            FreeCAD.Vector(p0), d0,
            FreeCAD.Vector(p1), d1
        )
        if c0 is None or c1 is None:
            raise ValueError("Failed to compute virtual corner")
    
        # For clean coplanar cases c0 ~= c1; midpoint is robust
        corner = (c0 + c1) * 0.5
    
        # Sanity check
        if (c0 - c1).Length > HVACLibraryAPI.EPS:
            FreeCAD.Console.PrintWarning(
                "HVAC: elbow centerlines do not intersect exactly; using midpoint of closest points\n"
            )
    
        return corner

    @staticmethod
    def arc_center_from_points_tangents_radius(p0, p1, u0, u1, radius):
        """
        Compute the center of a circular arc joining p0 -> p1 with given radius,
        using tangent directions u0 at p0 and u1 at p1.
    
        Parameters
        ----------
        p0, p1 : FreeCAD.Vector
            Arc end points.
        u0, u1 : FreeCAD.Vector
            Tangent directions at p0 and p1.
        radius : float
            Arc radius.
    
        Returns
        -------
        FreeCAD.Vector
            Arc center.
    
        Notes
        -----
        - The bend plane is derived from u0 x u1.
        - The chosen center is the one whose radius vectors are most
          perpendicular to the supplied tangents.
        """
    
        p0 = FreeCAD.Vector(p0)
        p1 = FreeCAD.Vector(p1)
        if radius <= 0:
            raise ValueError("Radius must be positive")
        # Normalize tangent directions
        u0 = FreeCAD.Vector(u0)
        u1 = FreeCAD.Vector(u1)
        if u0.Length <= 1e-12 or u1.Length <= 1e-12:
            raise ValueError("Tangent direction too small")
        u0.normalize()
        u1.normalize()
    
        # Chord between endpoints
        chord = p1 - p0
        d = chord.Length
        if d <= 1e-12:
            raise ValueError("Arc endpoints are coincident")
    
        # A circle of radius r can span the chord only if d <= 2r
        if d > 2.0 * float(radius) + 1e-9:
            raise ValueError("Radius too small for given endpoints")
    
        # Midpoint of the chord
        mid = (p0 + p1) * 0.5
    
        # Bend plane normal from the two tangents
        plane_n = u0.cross(u1)
        if plane_n.Length <= 1e-12:
            raise ValueError("Elbow requires non-collinear tangent directions")
        plane_n.normalize()
    
        # Unit chord direction
        chord_dir = FreeCAD.Vector(chord)
        chord_dir.normalize()
    
        # Direction from chord midpoint toward candidate centers,
        # constrained to remain in the bend plane
        perp = plane_n.cross(chord_dir)
        if perp.Length <= 1e-12:
            perp = chord_dir.cross(plane_n)
        if perp.Length <= 1e-12:
            raise ValueError("Failed to compute elbow center direction")
        perp.normalize()
    
        # Distance from chord midpoint to the circle center
        h_sq = float(radius) ** 2 - (d * 0.5) ** 2
        if h_sq < -1e-9:
            raise ValueError("Invalid geometry for arc center")
        h = math.sqrt(max(h_sq, 0.0))
    
        # Two possible centers
        c1 = mid + perp * h
        c2 = mid - perp * h
    
        def score(c):
            """
            Smaller score is better.
            For a valid circle tangent to the arc, the radius vector at each end
            should be perpendicular to the tangent there.
            """
            v0 = p0 - c
            v1 = p1 - c
            return abs(v0.dot(u0)) + abs(v1.dot(u1))
    
        return c1 if score(c1) <= score(c2) else c2


    # ------------------------------------------------------------------
    # Context / port helpers
    # ------------------------------------------------------------------

    @staticmethod
    def center_from_context(context):
        """The junction's center point, or (if not given) the average of its connected ports' positions."""
        cp = context.get("center_point", None)
        if cp is not None:
            return HVACLibraryAPI.vec(cp)
        ports = HVACLibraryAPI.connected_ports(context)
        if not ports:
            raise ValueError("Junction context requires center_point or connected_ports")
        return HVACLibraryAPI.average_point([p["position"] for p in ports])

    @staticmethod
    def connected_ports(context):
        """Return a copy of the context's connected-port list."""
        return list(context.get("connected_ports", []) or [])

    @staticmethod
    def collinear_port_index_pairs(context):
        """Index pairs (into ``connected_ports(context)``) the network
        classifier already found collinear (~180 deg apart -- see
        ``NetworkParser._collinear_pairs``), read straight off the
        junction's own ``context["analysis"]["collinear_pairs"]`` payload
        (``NetworkParser.JunctionAnalysis``, propagated via
        ``Component._parentAnalysis``/``AnalysisJson``).

        A "which two of this node's ports form the straight run" generator
        (a radiused/mitered tee's trunk, a tap's main run, ...) should use
        this rather than re-deriving collinearity itself, so it agrees
        exactly with the classifier's own family_key decision (same
        tolerance, same data) instead of risking a second, possibly
        divergent judgment call. Empty if no analysis payload is present
        (e.g. a synthetic/unit-test context) -- callers need their own
        geometric fallback for that case.
        """
        analysis = context.get("analysis") or {}
        pairs = analysis.get("collinear_pairs") or []
        result = []
        for pair in pairs:
            try:
                a, b = int(pair["a"]), int(pair["b"])
            except (KeyError, TypeError, ValueError):
                continue
            result.append((a, b))
        return result

    @staticmethod
    def port_position(port):
        """Return a port's position as a FreeCAD vector."""
        return HVACLibraryAPI.vec(port["position"])

    @staticmethod
    def port_direction(port):
        """Return a port's outward direction as a unit vector."""
        return HVACLibraryAPI.unit(port["direction"])

    @staticmethod
    def port_profile(port):
        """Return the normalized profile name stored on a port."""
        return str(port.get("profile", "") or "")

    @staticmethod
    def port_section_params(port):
        """Return a copy of a port's section-parameter mapping."""
        return dict(port.get("section_params", {}) or {})

    @staticmethod
    def port_profile_x_axis(port):
        """Return a port's profile x-axis, or ``None`` when it is unavailable."""
        v = port.get("profile_x_axis", None)
        if v is None:
            return None
        vv = HVACLibraryAPI.vec(v)
        return None if vv.Length <= HVACLibraryAPI.EPS else vv

    @staticmethod
    def port_diameter(port):
        """Return a circular port's diameter in millimetres, defaulting to zero."""
        params = HVACLibraryAPI.port_section_params(port)
        return float(params.get("Diameter", 0.0) or 0.0)

    @staticmethod
    def port_width(port):
        """Return a rectangular or oval port's width in millimetres."""
        params = HVACLibraryAPI.port_section_params(port)
        return float(params.get("Width", 0.0) or 0.0)

    @staticmethod
    def port_height(port):
        """Return a rectangular or oval port's height in millimetres."""
        params = HVACLibraryAPI.port_section_params(port)
        return float(params.get("Height", 0.0) or 0.0)

    @staticmethod
    def port_area(port):
        """Cross-section area (m^2) of a port, from its profile/section_params (in mm)."""
        profile = HVACLibraryAPI.port_profile(port)
        if profile == "Circular":
            d = HVACLibraryAPI.port_diameter(port)
            return airflow.circular_area(airflow.mm_to_m(d)) if d > 0.0 else 0.0
        if profile in ("Rectangular", "Oval"):
            w = HVACLibraryAPI.port_width(port)
            h = HVACLibraryAPI.port_height(port)
            if w <= 0.0 or h <= 0.0:
                return 0.0
            if profile == "Rectangular":
                return airflow.rectangular_area(airflow.mm_to_m(w), airflow.mm_to_m(h))
            return airflow.oval_area(airflow.mm_to_m(w), airflow.mm_to_m(h))
        return 0.0

    @staticmethod
    def copy_port(port, position=None, direction=None, profile_x_axis=None, edge_key=None, segment_end=None):
        """
        Copy a port dict, optionally overriding position/direction/
        profile_x_axis/edge_key/segment_end (everything else unchanged).

        edge_key/segment_end are useful when synthesizing a port that
        doesn't correspond to the original port's own connection any more
        (e.g. an internal seam between two chained junction components).
        """
        out = dict(port)
        if position is not None:
            out["position"] = HVACLibraryAPI.vec(position)
        if direction is not None:
            out["direction"] = HVACLibraryAPI.vec(direction)
        if profile_x_axis is not None:
            out["profile_x_axis"] = HVACLibraryAPI.vec(profile_x_axis)
        if edge_key is not None:
            out["edge_key"] = str(edge_key)
        if segment_end is not None:
            out["segment_end"] = str(segment_end)
        return out

    @staticmethod
    def grow_port_section(port, delta):
        """
        Copy of `port` with its cross-section grown by `delta` (uniformly,
        on every side) -- position/direction/profile are unchanged, only
        section_params grows. Shared helper for building an insulation (or
        any other wrap-around) shape's own outer profile from a casing
        port's profile, without duplicating this per fitting generator.
        Inverse of shrinking a port's section by a wall thickness (see
        e.g. smacna/generators/junctions.py's own private _inset_port,
        which this does not replace -- that one shrinks to build a hollow
        casing wall, this one grows to wrap something around the outside).
        """
        profile = HVACLibraryAPI.port_profile(port)
        params = HVACLibraryAPI.port_section_params(port)
        delta = float(delta)

        if profile == "Circular":
            diameter = float(params.get("Diameter", 0.0) or 0.0) + 2.0 * delta
            if diameter <= 0.0:
                raise ValueError("Grown Diameter must be positive")
            new_params = dict(params, Diameter=diameter)
        elif profile in ("Rectangular", "Oval"):
            width = float(params.get("Width", 0.0) or 0.0) + 2.0 * delta
            height = float(params.get("Height", 0.0) or 0.0) + 2.0 * delta
            if width <= 0.0 or height <= 0.0:
                raise ValueError("Grown Width/Height must be positive")
            new_params = dict(params, Width=width, Height=height)
        else:
            raise ValueError("Unsupported profile '{}' for grow_port_section".format(profile))

        out = HVACLibraryAPI.copy_port(port)
        out["section_params"] = new_params
        return out

    @staticmethod
    def build_trim_rec_from_port_lengths(port_lengths):
        """
        Build the connection_lengths a generator returns from a list of
        (port, trim_length) pairs -- how far each connected segment is
        trimmed back from its own endpoint to make room for this fitting.
        Ports with no real edge_key/segment_end (e.g. a synthetic port) are
        silently skipped.
        """
        out = []
        for port, length in port_lengths:
            edge_key = str(port.get("edge_key", "") or "")
            seg_end = str(port.get("segment_end", "") or "")
            if not edge_key or seg_end not in ("start", "end"):
                continue
            out.append(
                {
                    "edge_key": edge_key,
                    "segment_end": seg_end,
                    "length": float(length),
                }
            )
        return out

    def build_trim_rec_from_context_uniform(context, length_value):
        """Same as build_trim_rec_from_port_lengths, but trims every connected port by the same length_value."""
        ports = list(context.get("connected_ports", []) or [])
        return HVACLibraryAPI.build_trim_rec_from_port_lengths([(p, length_value) for p in ports])


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
        
        return Part.Wire([
            Part.makeLine(p1, p2),
            Part.makeLine(p2, p3),
            Part.makeLine(p3, p4),
            Part.makeLine(p4, p1),
        ])

    @staticmethod
    def make_circular_wire(center, normal, diameter):
        """A circular section wire, centered on `center`, lying in the plane perpendicular to `normal`."""
        c = HVACLibraryAPI.vec(center)
        n = HVACLibraryAPI.unit(normal)
        r = float(diameter) * 0.5
        circle = Part.Circle(c, n, r)
        return Part.Wire([Part.Edge(circle)])

    @staticmethod
    def make_oval_wire(center, x_axis, y_axis, width, height):
        """
        Flat-oval / obround section.
        Major axis along x_axis, minor axis along y_axis.
    
        width  = total overall width
        height = total overall height
    
        Requires width >= height > 0.
        """
        c = HVACLibraryAPI.vec(center)
        x = HVACLibraryAPI.unit(x_axis)
        y = HVACLibraryAPI.unit(y_axis)
    
        width = float(width or 0.0)
        height = float(height or 0.0)
    
        if width <= 0.0 or height <= 0.0:
            raise ValueError("Oval section requires positive Width and Height")
    
        if width < height:
            raise ValueError("Oval section currently requires Width >= Height")
    
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
        _, x_axis, y_axis, _ = HVACLibraryAPI.make_profile_frame(
            direction, profile_x_axis, center
        )
        
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
    
    # ------------------------------------------------------------------
    # Straight solids
    # ------------------------------------------------------------------

    @staticmethod
    def arc_wire(p1, pm, p2):
        """A single-edge wire, a circular arc through p1 -> pm -> p2."""
        edge = Part.Arc(
            HVACLibraryAPI.vec(p1),
            HVACLibraryAPI.vec(pm),
            HVACLibraryAPI.vec(p2),
        ).toShape()
        return Part.Wire([edge])

    @classmethod
    def make_profile(
        cls,
        profile_type,
        params,
        frame=None,
        *,
        center=None,
        direction=None,
        profile_x_axis=None,
    ):
        """Build a closed section wire in a local placement frame.

        ``profile_type`` accepts rectangular, circular, oval, rounded-
        rectangle, and polygon aliases. ``frame`` may be a mapping with
        origin/normal/x-axis entries or a three-item sequence; explicit frame
        keyword arguments fill any values omitted by a mapping.

        Returns an :class:`HVACProfile`. Invalid or collapsed dimensions raise
        ``ValueError``.
        """
        profile = cls._norm_profile_name(profile_type)
        params = dict(params or {})

        if frame is not None:
            if isinstance(frame, Mapping):
                center = frame.get("origin", frame.get("center", center))
                direction = frame.get("normal", frame.get("direction", direction))
                profile_x_axis = frame.get("x_axis", frame.get("profile_x_axis", profile_x_axis))
            elif isinstance(frame, (tuple, list)) and len(frame) >= 3:
                center, direction, profile_x_axis = frame[:3]

        center = cls.vec(center or (0.0, 0.0, 0.0))
        direction = cls.unit(direction or (0.0, 0.0, 1.0))
        _, x_axis, y_axis, _ = cls.make_profile_frame(direction, profile_x_axis, center)

        if profile in {"Rectangular", "Circular", "Oval"}:
            wire = cls.make_section_wire(profile, params, center, direction, x_axis)
            return HVACProfile(profile, params, center, direction, x_axis, wire)

        if profile == "RoundedRectangle":
            w = float(params.get("Width", params.get("width", 0.0)) or 0.0)
            h = float(params.get("Height", params.get("height", 0.0)) or 0.0)
            r = float(params.get("Radius", params.get("radius", 0.0)) or 0.0)
            if w <= 0 or h <= 0:
                raise ValueError("Rounded rectangle Width and Height must be positive")
            if r < 0 or r > min(w, h) / 2.0 + _EPS:
                raise ValueError("Rounded rectangle Radius must be between 0 and min(Width, Height)/2")
            if r <= _EPS:
                normalized = {"Width": w, "Height": h}
                wire = cls.make_section_wire("Rectangular", normalized, center, direction, x_axis)
                return HVACProfile("RoundedRectangle", {"Width": w, "Height": h, "Radius": 0.0}, center, direction, x_axis, wire)
            wire = cls._rounded_rectangle_wire(center, x_axis, y_axis, w, h, r)
            return HVACProfile("RoundedRectangle", {"Width": w, "Height": h, "Radius": r}, center, direction, x_axis, wire)

        points = params.get("Points", params.get("points"))
        if not points or len(points) < 3:
            raise ValueError("Polygon requires at least three local 2D Points")
        local = tuple((float(p[0]), float(p[1])) for p in points)
        world = [center + x_axis * x + y_axis * y for x, y in local]
        wire = Part.makePolygon(world + [world[0]])
        return HVACProfile("Polygon", {"Points": local}, center, direction, x_axis, wire, local)

    @classmethod
    def profile_from_port(cls, port, offset=0.0):
        """Build an ``HVACProfile`` from a library port.

        A positive ``offset`` grows the port section uniformly before the
        profile is built; a negative value shrinks it.
        """
        working = cls.grow_port_section(port, float(offset)) if abs(float(offset)) > _EPS else port
        return cls.make_profile(
            cls.port_profile(working),
            cls.port_section_params(working),
            center=cls.port_position(working),
            direction=cls.port_direction(working),
            profile_x_axis=cls.port_profile_x_axis(working),
        )

    @classmethod
    def offset_profile(cls, profile, distance):
        """Return a new profile offset uniformly in its local section plane.

        Positive distances grow the clear-air boundary and negative distances
        shrink it. The input must be an ``HVACProfile`` returned by this API.
        """
        if not isinstance(profile, HVACProfile):
            raise TypeError("offset_profile() requires a profile returned by make_profile()")
        d = float(distance)
        p = dict(profile.params)
        kind = profile.profile

        if kind == "Circular":
            diameter = float(p.get("Diameter", p.get("diameter", 0.0))) + 2.0 * d
            if diameter <= _EPS:
                raise ValueError("Profile offset collapses circular section")
            p = {"Diameter": diameter}
        elif kind in {"Rectangular", "Oval"}:
            width = float(p.get("Width", p.get("width", 0.0))) + 2.0 * d
            height = float(p.get("Height", p.get("height", 0.0))) + 2.0 * d
            if width <= _EPS or height <= _EPS:
                raise ValueError("Profile offset collapses section")
            p = {"Width": width, "Height": height}
        elif kind == "RoundedRectangle":
            width = float(p["Width"]) + 2.0 * d
            height = float(p["Height"]) + 2.0 * d
            radius = float(p["Radius"]) + d
            if width <= _EPS or height <= _EPS or radius < -_EPS:
                raise ValueError("Profile offset collapses rounded rectangle")
            p = {"Width": width, "Height": height, "Radius": max(0.0, radius)}
        elif kind == "Polygon":
            p = {"Points": cls._offset_polygon(profile.local_points, d)}
        else:
            raise ValueError(f"Unsupported profile offset: {kind}")

        return cls.make_profile(
            kind,
            p,
            center=profile.center,
            direction=profile.direction,
            profile_x_axis=profile.profile_x_axis,
        )

    @staticmethod
    def _offset_polygon(points, distance):
        """Offset a simple polygon by intersecting adjacent shifted edges."""
        pts = [(float(x), float(y)) for x, y in points]
        n = len(pts)
        area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
        if abs(area2) <= _EPS:
            raise ValueError("Polygon profile has zero area")
        # For CCW polygon, interior lies left of edges, so outward is right.
        sign = 1.0 if area2 > 0.0 else -1.0
        shifted = []
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length <= _EPS:
                raise ValueError("Polygon contains a zero-length edge")
            nx, ny = sign * dy / length, -sign * dx / length
            shifted.append(((x0 + nx * distance, y0 + ny * distance), (dx, dy)))

        result = []
        for i in range(n):
            p1, d1 = shifted[(i - 1) % n]
            p2, d2 = shifted[i]
            den = d1[0] * d2[1] - d1[1] * d2[0]
            if abs(den) <= _EPS:
                # Parallel adjacent edges: use the second shifted vertex.
                result.append(p2)
                continue
            qx, qy = p2[0] - p1[0], p2[1] - p1[1]
            t = (qx * d2[1] - qy * d2[0]) / den
            result.append((p1[0] + t * d1[0], p1[1] + t * d1[1]))
        return tuple(result)

    @staticmethod
    def _rounded_rectangle_wire(center, x_axis, y_axis, width, height, radius):
        """Build a rounded-rectangle wire, including capsule/circle limits."""
        hw, hh, r = width / 2.0, height / 2.0, radius
        s2 = math.sqrt(0.5)

        def v(x, y):
            """Map local profile coordinates into the world-space frame."""
            return center + x_axis * x + y_axis * y

        edges = []

        def add_line(start, end):
            """Append a non-degenerate straight side to the boundary."""
            # Maximum corner radii legitimately collapse one or both pairs of
            # straight sides. OCC rejects a zero-length Part edge.
            if (end - start).Length > _EPS:
                edges.append(Part.makeLine(start, end))

        add_line(v(-hw + r, hh), v(hw - r, hh))
        edges.append(Part.Arc(v(hw - r, hh), v(hw - r + r * s2, hh - r + r * s2), v(hw, hh - r)).toShape())
        add_line(v(hw, hh - r), v(hw, -hh + r))
        edges.append(Part.Arc(v(hw, -hh + r), v(hw - r + r * s2, -hh + r - r * s2), v(hw - r, -hh)).toShape())
        add_line(v(hw - r, -hh), v(-hw + r, -hh))
        edges.append(Part.Arc(v(-hw + r, -hh), v(-hw + r - r * s2, -hh + r - r * s2), v(-hw, -hh + r)).toShape())
        add_line(v(-hw, -hh + r), v(-hw, hh - r))
        edges.append(Part.Arc(v(-hw, hh - r), v(-hw + r - r * s2, hh - r + r * s2), v(-hw + r, hh)).toShape())
        return Part.Wire(edges)

    # ------------------------------------------------------------------
    # Surface / solid construction
    # ------------------------------------------------------------------

    @staticmethod
    def _wire(profile_or_wire):
        """Return a profile's wire and normalize a single edge to a wire."""
        shape = profile_or_wire.wire if isinstance(profile_or_wire, HVACProfile) else profile_or_wire
        if getattr(shape, "ShapeType", "") == "Edge":
            return Part.Wire([shape])
        return shape

    @classmethod
    def extrude(cls, profile, vector, *, solid=False):
        """Extrude a profile or wire along a nonzero vector.

        With ``solid=True`` the wire is first converted to a face, producing a
        solid extrusion. Otherwise the wire itself is extruded into a shell.
        """
        wire = cls._wire(profile)
        vec = cls.vec(vector)
        if vec.Length <= _EPS:
            raise ValueError("Extrusion vector must be non-zero")
        return Part.Face(wire).extrude(vec) if solid else wire.extrude(vec)

    @classmethod
    def sweep(cls, profiles, path, *, solid=False, frenet=False, transition=0):
        """Sweep one or more section profiles along a path wire.

        ``frenet`` and ``transition`` are forwarded to OCC's pipe-shell
        builder. A failed, null, or non-solid result (when ``solid=True``)
        raises ``RuntimeError`` with a stable library-facing message.
        """
        if not isinstance(profiles, (list, tuple)):
            profiles = [profiles]
        spine = cls._wire(path)
        wires = [cls._wire(profile) for profile in profiles]
        try:
            shape = spine.makePipeShell(
                wires,
                bool(solid),
                bool(frenet),
                int(transition),
            )
        except Exception as exc:
            raise RuntimeError(f"Sweep failed: {exc}") from exc
        if shape.isNull():
            raise RuntimeError("Sweep returned a null shape")
        if solid and not shape.Solids:
            raise RuntimeError("Sweep failed to create a solid")
        return shape

    @classmethod
    def loft(cls, profiles, *, solid=False, ruled=True, closed=False):
        """Loft through at least two profiles and return the resulting shape."""
        wires = [cls._wire(p) for p in profiles]
        if len(wires) < 2:
            raise ValueError("loft() requires at least two profiles")
        return Part.makeLoft(wires, bool(solid), bool(ruled), bool(closed))

    @classmethod
    def revolve(cls, profile, axis, angle=360.0, *, solid=False):
        """Revolve a profile around ``(origin, direction)`` by degrees.

        ``axis`` may also be a mapping with ``origin`` and ``direction`` (or
        ``axis``) entries. ``solid=True`` revolves the profile's filled face.
        """
        if isinstance(axis, Mapping):
            origin = cls.vec(axis.get("origin", (0, 0, 0)))
            direction = cls.unit(axis.get("direction", axis.get("axis", (0, 0, 1))))
        else:
            origin, direction = axis
            origin, direction = cls.vec(origin), cls.unit(direction)
        base = Part.Face(cls._wire(profile)) if solid else cls._wire(profile)
        return base.revolve(origin, direction, float(angle))

    @staticmethod
    def transform(shape, transform):
        """Return a transformed copy using a FreeCAD placement or matrix."""
        out = shape.copy()
        if isinstance(transform, FreeCAD.Placement):
            out.Placement = transform.multiply(out.Placement)
            return out
        if isinstance(transform, FreeCAD.Matrix):
            out.transformShape(transform, True)
            return out
        if hasattr(transform, "toMatrix"):
            out.transformShape(transform.toMatrix(), True)
            return out
        raise TypeError("transform must be a FreeCAD.Placement or FreeCAD.Matrix")

    # ------------------------------------------------------------------
    # Trim / booleans
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_plane(plane):
        """Normalize a plane argument -- an ``(origin, normal)`` pair or a mapping -- to vectors."""
        if isinstance(plane, Mapping):
            origin = HVACLibraryAPI.vec(plane.get("origin", plane.get("point", (0, 0, 0))))
            normal = HVACLibraryAPI.unit(plane.get("normal", (0, 0, 1)))
        else:
            origin, normal = plane
            origin, normal = HVACLibraryAPI.vec(origin), HVACLibraryAPI.unit(normal)
        return origin, normal

    @classmethod
    def _bounded_plane_face(cls, shape, origin, normal):
        """A finite planar face, generously sized to cover ``shape``'s bounding box.

        Shared by ``clip_plane`` (which extrudes this into a half-space
        solid to trim ``shape``) and ``section_face`` (which intersects it
        directly to read off an exact cross-section). Also returns
        ``extent``/``signed_distance`` so ``clip_plane`` can size its
        extrusion depth without redoing the bounding-box math.
        """
        bb = shape.BoundBox
        extent = max(
            bb.DiagonalLength if hasattr(bb, "DiagonalLength") else 0.0,
            bb.XLength,
            bb.YLength,
            bb.ZLength,
            1.0,
        )
        shape_center = cls.vec(
            (
                (bb.XMin + bb.XMax) / 2.0,
                (bb.YMin + bb.YMax) / 2.0,
                (bb.ZMin + bb.ZMax) / 2.0,
            )
        )
        signed_distance = (shape_center - origin).dot(normal)
        plane_center = shape_center - normal * signed_distance
        size = extent * 4.0
        _, x_axis, y_axis, _ = cls.make_profile_frame(normal, None, origin)
        corner = plane_center - x_axis * (size / 2.0) - y_axis * (size / 2.0)
        face = Part.makePlane(size, size, corner, normal, x_axis)
        return face, extent, signed_distance

    @classmethod
    def clip_plane(cls, shape, plane, side="positive"):
        """Keep one half of ``shape`` relative to an infinite plane.

        ``plane`` is ``(origin, normal)`` or a mapping. ``side`` accepts
        positive/front/normal or negative/back/opposite aliases.
        """
        origin, normal = cls._resolve_plane(plane)

        side_key = str(side).strip().lower()
        positive_aliases = {"positive", "+", "front", "normal"}
        negative_aliases = {"negative", "-", "back", "opposite"}
        if side_key not in positive_aliases | negative_aliases:
            raise ValueError(f"Unsupported clip-plane side: {side}")

        plane_face, extent, signed_distance = cls._bounded_plane_face(shape, origin, normal)
        depth = abs(signed_distance) + extent * 2.0
        positive = side_key in positive_aliases
        # FreeCAD's Python Part module does not expose OCC's half-space
        # builder consistently. Extruding a plane larger than the target's
        # bounding box creates an equivalent bounded clipping solid.
        half_space = plane_face.extrude(normal * (depth if positive else -depth))
        result = shape.common(half_space)
        if result.isNull() or not result.Solids:
            # A shape that never actually reaches the requested side of the
            # plane produces an empty (but "valid") result -- fail here,
            # with the plane/side that caused it, instead of leaving an
            # empty shape to crash confusingly deep inside whatever uses
            # it next (e.g. an invalid-bounding-box error far from here).
            raise ValueError(
                f"clip_plane produced an empty shape: nothing of the input "
                f"shape lies on the {side!r} side of the given plane"
            )
        return result

    @classmethod
    def section_face(cls, shape, plane):
        """Return the exact planar cross-section where ``shape`` meets a plane.

        ``plane`` is ``(origin, normal)`` or a mapping, same as
        ``clip_plane``. This is the ready-made face a neighbouring piece
        should be built against after ``clip_plane`` trims a shape back to
        a cut line (e.g. a mitre plane) -- reading the real cut face back
        off the trimmed shape avoids any mismatch against an independently
        built, idealised profile that might not land exactly on the plane.

        Reads the face directly off ``shape.Faces`` rather than
        intersecting with a fresh plane face: a plane coincident with (part
        of) the shape's own boundary -- exactly the case right after
        ``clip_plane`` cut it there -- is a tangential/degenerate boolean
        for OCC, which is unreliable (it can silently return empty). The
        cut face is already a real face of ``shape``; this just finds it.
        """
        origin, normal = cls._resolve_plane(plane)
        bb = shape.BoundBox
        tol = max(bb.DiagonalLength if hasattr(bb, "DiagonalLength") else 0.0, 1.0) * 1e-6

        matches = []
        for face in shape.Faces:
            surface = face.Surface
            if not isinstance(surface, Part.Plane):
                continue
            face_normal = cls.unit(surface.Axis)
            if abs(abs(face_normal.dot(normal)) - 1.0) > 1e-6:
                continue
            if abs((face.CenterOfMass - origin).dot(normal)) > tol:
                continue
            matches.append(face)

        if not matches:
            raise RuntimeError("No face of the shape lies on the requested plane")
        result = matches[0]
        for extra in matches[1:]:
            result = result.fuse(extra)
        return result

    @classmethod
    def trim(cls, shape, boundary, keep="inside"):
        """Trim ``shape`` with a plane or another Part shape.

        Shape boundaries support intersection (``inside``) and difference
        (``outside``). Plane mappings use their own optional ``side`` value
        and default to the positive half-space.
        """
        if isinstance(boundary, Mapping) and ("normal" in boundary or "plane" in boundary):
            plane = boundary.get("plane", boundary)
            return cls.clip_plane(shape, plane, boundary.get("side", "positive"))
        mode = str(keep).lower()
        if mode in {"inside", "common", "intersection"}:
            return cls.common(shape, boundary)
        if mode in {"outside", "cut", "difference"}:
            return cls.cut(shape, boundary)
        raise ValueError(f"Unsupported trim keep mode: {keep}")

    @staticmethod
    def _flatten_shapes(shapes):
        """Flatten nested lists/tuples and discard null or missing shapes."""
        result = []
        for shape in shapes:
            if shape is None:
                continue
            if isinstance(shape, (list, tuple)):
                result.extend(HVACLibraryAPI._flatten_shapes(shape))
            elif not shape.isNull():
                result.append(shape)
        return result

    @classmethod
    def fuse(cls, *shapes, refine=False):
        """Fuse nested shape arguments, optionally removing splitters."""
        items = cls._flatten_shapes(shapes)
        if not items:
            return Part.Shape()
        result = items[0]
        for item in items[1:]:
            result = result.fuse(item)
        return cls.refine(result) if refine else result

    @classmethod
    def cut(cls, shape, *tools, refine=False):
        """Subtract each non-null tool from ``shape`` in argument order."""
        result = shape
        for tool in cls._flatten_shapes(tools):
            result = result.cut(tool)
        return cls.refine(result) if refine else result

    @classmethod
    def common(cls, *shapes, refine=False):
        """Intersect nested shape arguments, optionally removing splitters."""
        items = cls._flatten_shapes(shapes)
        if not items:
            return Part.Shape()
        result = items[0]
        for item in items[1:]:
            result = result.common(item)
        return cls.refine(result) if refine else result

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    @classmethod
    def boundary(cls, shape):
        """Return groups of edges used by only one face.

        Closed solids normally return an empty list. Open shells return the
        free edges grouped into connectable edge sequences by Part.
        """
        edge_use = {}
        for face in shape.Faces:
            for edge in face.Edges:
                key = edge.hashCode(1000003)
                edge_use[key] = (edge_use.get(key, [edge, 0])[0], edge_use.get(key, [edge, 0])[1] + 1)
        free = [edge for edge, count in edge_use.values() if count == 1]
        if not free:
            return []
        try:
            return Part.sortEdges(free)
        except Exception:
            return [Part.Wire(free)]

    @classmethod
    def sew(cls, shapes, tolerance=1.0e-6, require_closed=False):
        """Sew faces from one or more shapes using the requested tolerance.

        Returns OCC's sewed result. ``require_closed=True`` raises when that
        result is not closed; inputs containing no faces are rejected.
        """
        faces = []
        for shape in cls._flatten_shapes(shapes):
            if getattr(shape, "ShapeType", "") == "Face":
                faces.append(shape)
            else:
                faces.extend(shape.Faces)
        if not faces:
            raise ValueError("sew() received no faces")

        tol = float(tolerance)
        if tol <= 0.0:
            raise ValueError("sew() tolerance must be positive")
        result = Part.makeCompound(faces)
        result.fixTolerance(tol)
        sewed = result.sewShape()
        # FreeCAD currently mutates the TopoShape and returns None, while
        # accepting a returned shape keeps this compatible with other builds.
        if sewed is not None:
            result = sewed
        if result is None or result.isNull():
            raise RuntimeError("Sewing returned a null shape")
        if require_closed and not result.isClosed():
            raise ValueError("Sewn shell is not closed")
        return result

    @classmethod
    def bridge_boundaries(cls, boundary_a, boundary_b):
        """Create a face or ruled shell between two boundary wires.

        Coplanar nested loops produce a planar rim. Other loop pairs fall
        back to a ruled loft.
        """
        wa, wb = cls._wire(boundary_a), cls._wire(boundary_b)
        # Preferred case: coplanar nested closed wires => planar annular/rim face.
        try:
            fa, fb = Part.Face(wa), Part.Face(wb)
            if fa.Area >= fb.Area:
                rim = fa.cut(fb)
            else:
                rim = fb.cut(fa)
            if not rim.isNull() and rim.Area > _EPS:
                return rim
        except Exception:
            pass
        # General fallback for corresponding non-coplanar loops.
        return Part.makeLoft([wa, wb], False, True, False)

    @staticmethod
    def reverse(shape):
        """Return a copy with reversed topology orientation."""
        out = shape.copy()
        out.reverse()
        return out

    @classmethod
    def solidify(cls, shell):
        """Convert a closed shell to a validated solid.

        Existing solids pass through unchanged. Null, open, or invalid inputs
        raise ``ValueError``.
        """
        if shell.isNull():
            raise ValueError("Cannot solidify a null shell")
        if getattr(shell, "ShapeType", "") == "Solid":
            result = shell
        else:
            if not shell.isClosed():
                raise ValueError("Cannot solidify an open shell")
            result = Part.Solid(shell)
        cls.validate(result, raise_on_error=True, require_solid=True)
        return result

    # ------------------------------------------------------------------
    # Finish / diagnostics / tiny common primitives
    # ------------------------------------------------------------------

    @staticmethod
    def refine(shape):
        """Remove boolean splitters when OCC can do so safely."""
        if shape is None or shape.isNull():
            return shape
        try:
            return shape.removeSplitter()
        except Exception:
            return shape

    @staticmethod
    def validate(shape, *, raise_on_error=False, require_solid=False):
        """Return a stable diagnostic dictionary for a Part shape.

        Set ``require_solid`` to treat shapes without solids as invalid and
        ``raise_on_error`` to raise ``ValueError`` instead of only reporting
        accumulated messages.
        """
        errors = []
        null = shape is None or shape.isNull()
        valid = False if null else bool(shape.isValid())
        if null:
            errors.append("null shape")
        elif not valid:
            errors.append("Part shape is invalid")
        solid_count = 0 if null else len(shape.Solids)
        if require_solid and solid_count < 1:
            errors.append("shape contains no solid")
        closed = False if null else (shape.isClosed() if hasattr(shape, "isClosed") else None)
        result = {
            "valid": not errors,
            "shape_type": None if null else shape.ShapeType,
            "null": null,
            "closed": closed,
            "solid_count": solid_count,
            "volume": 0.0 if null else float(getattr(shape, "Volume", 0.0) or 0.0),
            "errors": errors,
        }
        if raise_on_error and errors:
            raise ValueError("; ".join(errors))
        return result

    @classmethod
    def make_sphere(cls, center, diameter):
        """Create a sphere from its center and positive diameter."""
        d = float(diameter)
        if d <= _EPS:
            raise ValueError("Sphere diameter must be positive")
        return Part.makeSphere(d / 2.0, cls.vec(center))

    @classmethod
    def make_line(cls, start, end):
        """Create a line edge between two distinct points."""
        start_point = cls.vec(start)
        end_point = cls.vec(end)
        if (end_point - start_point).Length <= _EPS:
            raise ValueError("Line endpoints must be distinct")
        return Part.makeLine(start_point, end_point)

    @classmethod
    def compound(cls, shapes):
        """Create a compound from nested non-null shape sequences."""
        return Part.makeCompound(cls._flatten_shapes(shapes))

    # ------------------------------------------------------------------
    # Convenience functions: common HVAC recipes and external geometry
    # ------------------------------------------------------------------

    @staticmethod
    def make_flange(port, inward_direction, thickness, height):
        """Build a flat annular flange collar around any supported port profile."""
        thickness = float(thickness)
        height = float(height)
        if thickness <= 0.0 or height <= 0.0:
            raise ValueError("Flange thickness and height must be > 0")
        outer_port = HVACLibraryAPI.grow_port_section(port, height)
        outer_face = HVACLibraryAPI.make_section_face_from_port(
            HVACLibraryAPI.copy_port(outer_port, direction=inward_direction)
        )
        inner_face = HVACLibraryAPI.make_section_face_from_port(
            HVACLibraryAPI.copy_port(port, direction=inward_direction)
        )
        extrusion = HVACLibraryAPI.unit(inward_direction) * thickness
        return outer_face.extrude(extrusion).cut(inner_face.extrude(extrusion))

    @staticmethod
    def make_elbow_path(port0, port1, radius):
        """Create the tangent arc and trimmed ports for a two-port elbow.

        Returns a dict containing ``path``, tangent ``ports``, and
        ``trim_lengths``. Callers can build matching offset profiles at those
        ports and pass them to :meth:`sweep`.
        """
        p0 = HVACLibraryAPI.port_position(port0)
        p1 = HVACLibraryAPI.port_position(port1)
        u0 = HVACLibraryAPI.port_direction(port0)
        u1 = HVACLibraryAPI.port_direction(port1)
        theta = HVACLibraryAPI.angle_between(u0, u1)
        if theta <= 1e-6 or abs(theta - math.pi) <= 1e-6:
            raise ValueError("Elbow requires non-collinear, non-opposite directions")
        radius = float(radius)
        if radius <= 0.0:
            raise ValueError("Elbow radius must be > 0")
        trim = radius / math.tan(theta / 2.0)
        c0, c1 = HVACLibraryAPI.closest_points_on_lines(p0, u0 * -1.0, p1, u1 * -1.0)
        s0 = c0 + u0 * trim
        s1 = c1 + u1 * trim
        center = HVACLibraryAPI.arc_center_from_points_tangents_radius(s0, s1, u0, u1, radius)
        bisector = u0 + u1
        if bisector.Length <= 1e-12:
            raise ValueError("Elbow bisector is undefined")
        bisector.normalize()
        midpoint = center - bisector * radius
        return {
            "path": HVACLibraryAPI.arc_wire(s0, midpoint, s1),
            "ports": [
                HVACLibraryAPI.copy_port(port0, position=s0),
                HVACLibraryAPI.copy_port(port1, position=s1),
            ],
            "trim_lengths": [
                max(0.0, (s0 - p0).dot(u0)),
                max(0.0, (s1 - p1).dot(u1)),
            ],
            "center": center,
        }

    @staticmethod
    def offset_transition_axis(port0, port1, length):
        """Shared axis geometry for a two-port lateral-offset transition.

        Both ports face outward in opposite, parallel directions but sit on
        offset (non-coincident) axes -- the generic "through.offset" case.
        This works out the end points and theoretical sharp turn points
        that every offset-transition builder needs, so the radiussed
        (arc-filleted) and mitered (flat-cut) builders agree on exactly the
        same axis and only differ in how the corner itself is finished.

        Step 1: find the travel direction and the two generated end points,
        ``length`` apart, that preserve the ports' lateral (transverse)
        offset.
        Step 2: find the theoretical sharp turn points -- a quarter of the
        body length in from each end point -- and the direction/angle of
        the diagonal segment connecting them.

        Returns a dict with keys ``p0``/``p1``/``u0``/``u1`` (the original
        port positions/outward directions), ``d`` (unit travel direction,
        port0 -> port1), ``s0``/``s1`` (generated end points), ``corner0``/
        ``corner1`` (turn points), ``diagonal`` (unit corner0 -> corner1
        direction), and ``turn_angle`` (radians, the deflection between the
        end run and the diagonal -- zero for a plain straight run with no
        lateral offset).
        """
        p0 = HVACLibraryAPI.port_position(port0)
        p1 = HVACLibraryAPI.port_position(port1)
        u0 = HVACLibraryAPI.port_direction(port0)
        u1 = HVACLibraryAPI.port_direction(port1)
        theta = HVACLibraryAPI.angle_between(u0, u1)
        if abs(theta - math.pi) > 1e-6:
            raise ValueError(
                "Offset transition requires opposite parallel port directions"
            )
        length = float(length)
        if length <= 0.0:
            raise ValueError("Offset transition length must be > 0")
        eps = HVACLibraryAPI.EPS
        # Step 1: travel direction through the fitting from port0 toward
        # port1, and the two generated end points.  Port directions
        # themselves are outward.
        d = -u0
        d.normalize()
        # Establish corresponding points c0/c1 on the two parallel axes.
        #
        # For parallel lines there is no unique closest-point pair.  Choose
        # points lying at the same axial station, centred between p0 and p1.
        #
        # c1 - c0 is therefore purely transverse to d.
        delta = p1 - p0
        axial_separation = delta.dot(d)
        c0 = p0 + d * (axial_separation / 2.0)
        c1 = p1 - d * (axial_separation / 2.0)
        transverse = c1 - c0
        # Numerical sanity check.
        if abs(transverse.dot(d)) > 1e-6:
            raise ValueError("Failed to establish parallel offset axes")
        # Generated fitting ports.  They are separated by ``length`` in the
        # axial direction while preserving the transverse offset.
        s0 = c0 - d * (length / 2.0)
        s1 = c1 + d * (length / 2.0)
        # Step 2: theoretical sharp corners.
        corner0 = s0 + d * (length / 4.0)
        corner1 = s1 - d * (length / 4.0)
        diagonal_vec = corner1 - corner0
        diagonal_length = diagonal_vec.Length
        if diagonal_length <= eps:
            raise ValueError("Offset transition diagonal is undefined")
        diagonal = HVACLibraryAPI.unit(diagonal_vec)
        # Deflection angle between end straight and diagonal.
        turn_angle = HVACLibraryAPI.angle_between(d, diagonal)
        return {
            "p0": p0, "p1": p1, "u0": u0, "u1": u1,
            "d": d, "s0": s0, "s1": s1,
            "corner0": corner0, "corner1": corner1,
            "diagonal": diagonal, "turn_angle": turn_angle,
        }

    @staticmethod
    def make_radiussed_path(port0, port1, length, radius):
        """Create a radiussed offset path between two parallel ports.

        The path consists of:

            straight -> circular arc -> diagonal -> circular arc -> straight

        The fitting has an axial length of ``length``.  The theoretical sharp
        turn points are located at 1/4 of the fitting length from each generated
        port.  ``radius`` specifies the radius of both circular bends.

        The input port directions are outward and must therefore be opposite.

        Returns a dict containing ``path``, trimmed ``ports``,
        ``trim_lengths``, and ``turn_points``.
        """
        length = float(length)
        radius = float(radius)
        if radius <= 0.0:
            raise ValueError("Radiussed offset radius must be > 0")
        axis = HVACLibraryAPI.offset_transition_axis(port0, port1, length)
        p0, p1, u0, u1 = axis["p0"], axis["p1"], axis["u0"], axis["u1"]
        d, s0, s1 = axis["d"], axis["s0"], axis["s1"]
        corner0, corner1 = axis["corner0"], axis["corner1"]
        diagonal, turn_angle = axis["diagonal"], axis["turn_angle"]
        eps = HVACLibraryAPI.EPS
        diagonal_length = (corner1 - corner0).Length
        # ------------------------------------------------------------------
        # No actual offset: return a straight path.
        # ------------------------------------------------------------------
        if turn_angle <= 1e-6:
            return {
                "path": Part.Wire([Part.makeLine(s0, s1)]),
                "ports": [
                    HVACLibraryAPI.copy_port(port0, position=s0),
                    HVACLibraryAPI.copy_port(port1, position=s1),
                ],
                "trim_lengths": [
                    max(0.0, (s0 - p0).dot(u0)),
                    max(0.0, (s1 - p1).dot(u1)),
                ],
                "turn_points": [corner0, corner1],
            }
        if turn_angle >= math.pi - 1e-6:
            raise ValueError("Invalid radiussed offset geometry")
        # ------------------------------------------------------------------
        # Circular fillets.
        #
        # Distance from theoretical corner to tangent point:
        #
        #     T = R tan(theta / 2)
        # ------------------------------------------------------------------
        tangent_length = radius * math.tan(turn_angle / 2.0)
        end_straight_available = length / 4.0
        if tangent_length >= end_straight_available - eps:
            raise ValueError(
                "Radius is too large for the available end straight length"
            )
        if 2.0 * tangent_length >= diagonal_length - eps:
            raise ValueError(
                "Radius is too large for the available diagonal length"
            )
        # First bend:
        #
        # s0 ---- a0 )---- a1 ------ diagonal
        #
        a0 = corner0 - d * tangent_length
        a1 = corner0 + diagonal * tangent_length
        # Second bend:
        #
        # diagonal ------ b0 ----( b1 ---- s1
        #
        b0 = corner1 - diagonal * tangent_length
        b1 = corner1 + d * tangent_length
        # ------------------------------------------------------------------
        # Circular arc helper.
        # ------------------------------------------------------------------
        def make_arc(start, end, tangent_start, tangent_end):
            center = HVACLibraryAPI.arc_center_from_points_tangents_radius(
                start,
                end,
                tangent_start,
                tangent_end,
                radius,
            )
            r0 = start - center
            r1 = end - center
            # For bends below 180 degrees, r0 + r1 points toward the
            # midpoint of the minor circular arc.
            rm = r0 + r1
            if rm.Length <= eps:
                raise ValueError("Unable to determine arc midpoint")
            rm.normalize()
            midpoint = center + rm * radius
            return HVACLibraryAPI.arc_wire(
                start,
                midpoint,
                end,
            )
    
        arc0 = make_arc(a0, a1, d, diagonal)
        arc1 = make_arc(b0, b1, diagonal, d)
        # ------------------------------------------------------------------
        # Assemble continuous path.
        # ------------------------------------------------------------------
        edges = []
        if (a0 - s0).Length > eps:
            edges.append(Part.makeLine(s0, a0))
        edges.extend(arc0.Edges)
    
        if (b0 - a1).Length > eps:
            edges.append(Part.makeLine(a1, b0))
        edges.extend(arc1.Edges)
    
        if (s1 - b1).Length > eps:
            edges.append(Part.makeLine(b1, s1))
    
        return {
            "path": Part.Wire(edges),
            "ports": [
                HVACLibraryAPI.copy_port(
                    port0,
                    position=s0,
                ),
                HVACLibraryAPI.copy_port(
                    port1,
                    position=s1,
                ),
            ],
            "trim_lengths": [
                max(0.0, (s0 - p0).dot(u0)),
                max(0.0, (s1 - p1).dot(u1)),
            ],
            "turn_points": [
                corner0,
                corner1,
            ],
        }
        
    @classmethod
    def build_envelopes(cls, envelope_builder, construction_layers, params):
        """
        Build the nested outer envelopes of a multilayer wall, from the
        clear-air boundary outward.

        `envelope_builder(offset)` builds the fitting's *whole* outer
        boundary shape uniformly offset outward by `offset` from the
        clear-air boundary (an offset of 0 is the air envelope itself) --
        a generator builds this closure once, from its own profiles/path/
        mitre-plane logic (typically via `profile_from_port(..., offset)`),
        and this method is the only thing that ever calls it, so every
        layer reuses exactly the same axes/stations/trim planes.

        `construction_layers` is the type-def's own declared construction
        layers, in order (`context["construction_layers"]`, a list of
        library/construction.py's ConstructionLayerDef). Each layer's
        thickness is read from `params[layer.thickness_property]`; a layer
        with no `thickness_property` contributes zero thickness (e.g. a
        purely descriptive layer with no wall thickness of its own).

        Returns `[air, envelope_1, envelope_2, ..., envelope_N]` -- one
        more shape than there are layers. A zero-thickness layer's
        envelope is the same object as the envelope before it (no
        redundant rebuild), which is how `envelopes_to_layers` recognizes
        it below.
        """
        envelopes = [cls.refine(envelope_builder(0.0))]
        cumulative = 0.0
        for layer_def in construction_layers:
            thickness_property = getattr(layer_def, "thickness_property", None)
            thickness = float(params.get(thickness_property, 0.0) or 0.0) if thickness_property else 0.0
            if thickness > _EPS:
                cumulative += thickness
                envelopes.append(cls.refine(envelope_builder(cumulative)))
            else:
                envelopes.append(envelopes[-1])
        return envelopes

    @classmethod
    def envelopes_to_layers(cls, envelopes):
        """
        Cut `HVACLibraryAPI.build_envelopes()`'s nested envelopes into each
        layer's own physical solid: Layer 1 = Envelope 1 - Air,
        Layer 2 = Envelope 2 - Envelope 1, ..., innermost layer first.

        A layer whose envelope is the same object as the one before it (a
        zero-thickness layer, see `build_envelopes`) is reported as `None`
        without running a boolean at all.
        """
        layers = []
        for inner, outer in zip(envelopes, envelopes[1:]):
            layers.append(None if outer is inner else cls.refine(cls.cut(outer, inner)))
        return layers

    @classmethod
    def build_layered_geometry(cls, envelope_builder, construction_layers, params):
        """
        Convenience wrapper: `build_envelopes()` + `envelopes_to_layers()`,
        packaged as the `{"layers": {id: {"shape": ...}}}` dict a geometry
        backend returns (see library/geometry_result.py's `normalize()`).

        This is the one call most multilayer fitting generators need: build
        `envelope_builder(offset)` once from the fitting's own geometry
        context, then hand it here together with `context["construction_layers"]`
        and the resolved `params` -- no per-fitting casing/insulation-specific
        layer helpers required.
        """
        envelopes = cls.build_envelopes(envelope_builder, construction_layers, params)
        layer_shapes = cls.envelopes_to_layers(envelopes)
        return {
            "layers": {
                layer_def.id: {"shape": shape}
                for layer_def, shape in zip(construction_layers, layer_shapes)
            }
        }

    @staticmethod
    def shape_from_fcstd(fcstd_path, context, params=None, result_object="Result",
                          port_names=None, tol_mm=0.5, tol_deg=0.5):
        """Build a shape from an FCStd template file (a parametric sketch-driven model) -- see template_shapes.py."""
        from . import template_shapes
        return template_shapes.build_shape_from_template(
            fcstd_path, context, params, result_object, port_names, tol_mm, tol_deg
        )

    @staticmethod
    def resolve_library_file(context, relative_path):
        """Resolve a path relative to the current type-def's own library folder (e.g. a data file it ships with)."""
        # Lazy import keeps the public API independent from the registry's
        # own import path while this module is being initialized.
        from ..utils import hvaclib

        registry = hvaclib.HVACLibraryService.get_hvac_library_registry()
        return registry.resolve_library_file(context["library_id"], relative_path)
