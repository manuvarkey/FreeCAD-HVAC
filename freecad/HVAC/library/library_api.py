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

import math
import os
import FreeCAD
import Part

from ..utils import hvaclib
from ..core import airflow
from . import smacna_loss

class HVACLibraryAPI:
    """
    Stable public API for built-in and external HVAC generator libraries.

    External/user-defined libraries should use only this API surface instead of
    importing internal HVAC modules directly.
    """

    API_VERSION = 1
    EPS = 1e-9

    # ------------------------------------------------------------------
    # Basic vector / numeric helpers
    # ------------------------------------------------------------------
    @staticmethod
    def vec(v):
        if hasattr(v, "x"):
            return FreeCAD.Vector(v)
        return FreeCAD.Vector(*v)

    @staticmethod
    def xyz(v):
        vv = HVACLibraryAPI.vec(v)
        return (vv.x, vv.y, vv.z)

    @staticmethod
    def unit(v, eps=None):
        eps = HVACLibraryAPI.EPS if eps is None else float(eps)
        out = HVACLibraryAPI.vec(v)
        if out.Length <= eps:
            raise ValueError("Zero-length vector")
        out.normalize()
        return out

    @staticmethod
    def is_zero(v, eps=None):
        eps = HVACLibraryAPI.EPS if eps is None else float(eps)
        return HVACLibraryAPI.vec(v).Length <= eps

    @staticmethod
    def angle_between(u1, u2):
        a = HVACLibraryAPI.unit(u1)
        b = HVACLibraryAPI.unit(u2)
        dot = max(-1.0, min(1.0, float(a.dot(b))))
        return math.acos(dot)

    @staticmethod
    def average_point(points):
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
        cp = context.get("center_point", None)
        if cp is not None:
            return HVACLibraryAPI.vec(cp)
        ports = HVACLibraryAPI.connected_ports(context)
        if not ports:
            raise ValueError("Junction context requires center_point or connected_ports")
        return HVACLibraryAPI.average_point([p["position"] for p in ports])

    @staticmethod
    def connected_ports(context):
        return list(context.get("connected_ports", []) or [])
        
    @staticmethod
    def port_position(port):
        return HVACLibraryAPI.vec(port["position"])

    @staticmethod
    def port_direction(port):
        return HVACLibraryAPI.unit(port["direction"])

    @staticmethod
    def port_profile(port):
        return str(port.get("profile", "") or "")

    @staticmethod
    def port_section_params(port):
        return dict(port.get("section_params", {}) or {})

    @staticmethod
    def port_profile_x_axis(port):
        v = port.get("profile_x_axis", None)
        if v is None:
            return None
        vv = HVACLibraryAPI.vec(v)
        return None if vv.Length <= HVACLibraryAPI.EPS else vv

    @staticmethod
    def port_diameter(port):
        params = HVACLibraryAPI.port_section_params(port)
        return float(params.get("Diameter", 0.0) or 0.0)

    @staticmethod
    def port_width(port):
        params = HVACLibraryAPI.port_section_params(port)
        return float(params.get("Width", 0.0) or 0.0)

    @staticmethod
    def port_height(port):
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

    # ------------------------------------------------------------------
    # SMACNA/ASHRAE fitting-loss orchestration
    #
    # These methods glue a junction's "connected_ports" context (built by
    # AirflowSolver.py) to the pure-math tables in smacna_loss.py: pulling
    # out the geometry/flow numbers each table needs, picking round vs
    # rectangular/oval formulas, and identifying which port is which leg of
    # the fitting. They are defensive (return None on any missing/invalid
    # data) so a malformed or partially-configured junction degrades to the
    # solver's generic fallback coefficient rather than aborting the whole
    # calculation -- see AirflowSolver.py Phase E / Library.py call_loss.
    # ------------------------------------------------------------------

    @staticmethod
    def elbow_loss(context):
        """
        90 deg elbow fitting loss. Expects exactly 2 connected_ports (one
        inlet, one outlet) and a "CenterlineRadius" entry in properties.
        Returns {outlet_edge_key: K} or None.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 2:
                return None
            outlet = next((p for p in ports if p.get("flow_into_junction") is False), None)
            if outlet is None:
                return None

            radius = float((context.get("properties") or {}).get("CenterlineRadius", 0.0) or 0.0)
            profile = HVACLibraryAPI.port_profile(outlet)

            if profile == "Circular":
                diameter = HVACLibraryAPI.port_diameter(outlet)
                if diameter <= 0.0 or radius <= 0.0:
                    return None
                zeta = smacna_loss.elbow_zeta_round(radius / diameter)
            elif profile in ("Rectangular", "Oval"):
                width = HVACLibraryAPI.port_width(outlet)
                height = HVACLibraryAPI.port_height(outlet)
                if width <= 0.0 or height <= 0.0 or radius <= 0.0:
                    return None
                reynolds = float(outlet.get("reynolds", 0.0) or 0.0)
                if reynolds <= 0.0:
                    return None
                zeta = smacna_loss.elbow_zeta_rect(height / width, radius / width, reynolds)
            else:
                return None

            return {outlet["edge_key"]: zeta}
        except Exception:
            return None

    @staticmethod
    def transition_loss(context):
        """
        Area-change (expansion/contraction) transition fitting loss. Expects
        exactly 2 connected_ports and a "TransitionLength" entry in
        properties. Returns {outlet_edge_key: K} or None.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 2:
                return None
            outlet = next((p for p in ports if p.get("flow_into_junction") is False), None)
            inlet = next((p for p in ports if p.get("flow_into_junction") is True), None)
            if outlet is None or inlet is None:
                return None

            area_out = HVACLibraryAPI.port_area(outlet)
            area_in = HVACLibraryAPI.port_area(inlet)
            if area_out <= 0.0 or area_in <= 0.0:
                return None

            area_ratio = max(area_in, area_out) / min(area_in, area_out)
            if area_ratio <= 1.05:
                # Essentially the same size on both sides (e.g. a lateral
                # offset) -- SMACNA's tables start at an area ratio of 2:1
                # and don't cover this case; treat as negligible loss rather
                # than clamping to the table's (much larger) minimum entry.
                return {outlet["edge_key"]: 0.0}

            length_mm = float((context.get("properties") or {}).get("TransitionLength", 0.0) or 0.0)
            if length_mm > 0.0:
                d_eq_in = 2.0 * math.sqrt(area_in / math.pi)
                d_eq_out = 2.0 * math.sqrt(area_out / math.pi)
                theta_deg = math.degrees(
                    2.0 * math.atan(abs(d_eq_out - d_eq_in) / (2.0 * airflow.mm_to_m(length_mm)))
                )
            else:
                theta_deg = 180.0  # no transition length -> treat as an abrupt change
            theta_deg = max(0.0, min(theta_deg, 180.0))

            profile = HVACLibraryAPI.port_profile(outlet)
            if area_out > area_in:
                # Expanding (diverging): downstream duct is larger.
                if profile == "Circular":
                    reynolds = float(inlet.get("reynolds", 0.0) or 0.0)
                    if reynolds <= 0.0:
                        return None
                    zeta = smacna_loss.expansion_zeta_round(theta_deg, area_ratio, reynolds)
                else:
                    zeta = smacna_loss.expansion_zeta_rect(theta_deg, area_ratio)
            else:
                # Contracting (converging): downstream duct is smaller.
                zeta = smacna_loss.contraction_zeta(theta_deg, area_ratio)

            return {outlet["edge_key"]: zeta}
        except Exception:
            return None

    @staticmethod
    def branch_loss(context):
        """
        Converging (merging) or diverging (splitting) tee/wye fitting loss.
        Expects exactly 3 connected_ports: one "common"/trunk port (the sole
        inlet if diverging, the sole outlet if converging) and two "secondary"
        ports (branch + straight-through), identified by which secondary
        port's direction is closest to anti-parallel with the common port's
        direction (the straight-through continuation of the duct run).

        Returns {branch_edge_key: K_branch, straight_edge_key: K_straight},
        each already referenced to that leg's own velocity, or None.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 3:
                return None

            inlets = [p for p in ports if p.get("flow_into_junction") is True]
            outlets = [p for p in ports if p.get("flow_into_junction") is False]

            if len(inlets) == 1 and len(outlets) == 2:
                diverging = True
                primary, secondaries = inlets[0], outlets
            elif len(inlets) == 2 and len(outlets) == 1:
                diverging = False
                primary, secondaries = outlets[0], inlets
            else:
                return None  # ambiguous/degenerate flow pattern

            primary_dir = HVACLibraryAPI.vec(primary["direction"])
            sec_a, sec_b = secondaries
            dot_a = primary_dir.dot(HVACLibraryAPI.vec(sec_a["direction"]))
            dot_b = primary_dir.dot(HVACLibraryAPI.vec(sec_b["direction"]))
            # Both port directions point away from the junction, so the leg
            # that continues straight through sits opposite the primary leg
            # (most negative dot product); the other secondary is the branch.
            straight, branch = (sec_a, sec_b) if dot_a < dot_b else (sec_b, sec_a)

            v_common = float(primary.get("velocity_ms", 0.0) or 0.0)
            if v_common <= 1e-9:
                return {branch["edge_key"]: 0.0, straight["edge_key"]: 0.0}

            a_common = HVACLibraryAPI.port_area(primary)
            a_branch = HVACLibraryAPI.port_area(branch)
            if a_common <= 0.0 or a_branch <= 0.0:
                return None

            branch_dir = HVACLibraryAPI.vec(branch["direction"])
            straight_dir = HVACLibraryAPI.vec(straight["direction"])
            cos_angle = max(-1.0, min(1.0, branch_dir.dot(straight_dir)))
            angle_deg = 180.0 - math.degrees(math.acos(cos_angle))

            ab_on_ac = a_branch / a_common
            vb_on_vc = float(branch.get("velocity_ms", 0.0) or 0.0) / v_common
            vs_on_vc = float(straight.get("velocity_ms", 0.0) or 0.0) / v_common

            if diverging:
                zeta_branch, zeta_straight = smacna_loss.diverging_branch_zetas(
                    angle_deg, ab_on_ac, vb_on_vc, vs_on_vc
                )
            else:
                zeta_branch, zeta_straight = smacna_loss.converging_branch_zetas(
                    angle_deg, ab_on_ac, vb_on_vc, vs_on_vc
                )

            return {branch["edge_key"]: zeta_branch, straight["edge_key"]: zeta_straight}
        except Exception:
            return None

    @staticmethod
    def manifold_loss(context):
        """
        Cross (4-port) or multiport (5+ port) fitting loss, for the common
        single-trunk case: exactly one port on one flow side (all inlet, or
        all outlet) and the rest ("secondaries") on the other side.

        No dedicated SMACNA/ASHRAE table exists for 4+ port fittings, so this
        decomposes the junction into a sequence of pairwise branch (tee/wye)
        calculations, reusing the exact same diverging_branch_zetas /
        converging_branch_zetas tables as branch_loss: secondaries are
        peeled off one at a time, least-straight (most branch-like) first,
        straightest last -- mirroring how a real header/manifold is
        typically laid out (larger/sharper takeoffs nearer the main
        connection, the straightest path continuing furthest). Each pairwise
        step is referenced against the PRIMARY port's own duct size and
        direction (and, for the branch-angle lookup, the straightest
        secondary's direction) as a stand-in for the intermediate duct
        geometry this addon doesn't actually model between successive
        virtual merges/splits -- an approximation, not a literal per-leg
        geometry readout. With exactly 2 secondaries this reduces to
        exactly the same numbers as branch_loss's 3-port calculation.

        Returns {edge_key: K, ...} covering every secondary port (each
        already referenced to that leg's own velocity), or None for a mixed
        multi-inlet/multi-outlet ("true cross") flow pattern -- which has no
        single trunk to decompose against -- or on any missing/invalid
        geometry.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) < 3:
                return None

            inlets = [p for p in ports if p.get("flow_into_junction") is True]
            outlets = [p for p in ports if p.get("flow_into_junction") is False]

            if len(inlets) == 1:
                diverging = True
                primary, secondaries = inlets[0], outlets
            elif len(outlets) == 1:
                diverging = False
                primary, secondaries = outlets[0], inlets
            else:
                return None  # mixed multi-in/multi-out: no single trunk to decompose

            if len(secondaries) < 2:
                return None

            a_ref = HVACLibraryAPI.port_area(primary)
            v_primary = float(primary.get("velocity_ms", 0.0) or 0.0)
            if a_ref <= 0.0:
                return None
            if v_primary <= 1e-9:
                return {p["edge_key"]: 0.0 for p in secondaries}
            for p in secondaries:
                if HVACLibraryAPI.port_area(p) <= 0.0:
                    return None

            primary_dir = HVACLibraryAPI.vec(primary["direction"])
            # Least-straight (most branch-like) first, straightest (closest
            # continuation of the primary direction) last -- same selection
            # rule as branch_loss, generalized to N-1 secondaries.
            ordered = sorted(
                secondaries,
                key=lambda p: primary_dir.dot(HVACLibraryAPI.vec(p["direction"])),
                reverse=True,
            )
            # Reference direction for every branch-angle lookup: the
            # straightest real secondary (matches branch_loss, which uses
            # the "straight" port's own direction rather than the primary's).
            straight_dir = HVACLibraryAPI.vec(ordered[-1]["direction"])

            zeta_fn = smacna_loss.diverging_branch_zetas if diverging else smacna_loss.converging_branch_zetas
            result = {}
            m = len(ordered)

            def _angle_deg(branch_port):
                branch_dir = HVACLibraryAPI.vec(branch_port["direction"])
                cos_angle = max(-1.0, min(1.0, branch_dir.dot(straight_dir)))
                return 180.0 - math.degrees(math.acos(cos_angle))

            if diverging:
                remaining_flow_lps = float(primary.get("flow_rate_lps", 0.0) or 0.0)
                for i in range(m - 1):
                    branch = ordered[i]
                    is_penultimate = (i == m - 2)
                    branch_flow_lps = float(branch.get("flow_rate_lps", 0.0) or 0.0)
                    after_flow_lps = remaining_flow_lps - branch_flow_lps

                    v_common_step = airflow.velocity_from_flow(
                        airflow.lps_to_m3s(remaining_flow_lps), a_ref
                    )
                    if v_common_step <= 1e-9:
                        result[branch["edge_key"]] = 0.0
                        remaining_flow_lps = after_flow_lps
                        continue

                    if is_penultimate:
                        # What's left after this branch IS the last real
                        # secondary -- use its own real velocity.
                        v_after_step = float(ordered[-1].get("velocity_ms", 0.0) or 0.0)
                    else:
                        v_after_step = airflow.velocity_from_flow(
                            airflow.lps_to_m3s(after_flow_lps), a_ref
                        )

                    ab_on_ac = HVACLibraryAPI.port_area(branch) / a_ref
                    vb_on_vc = float(branch.get("velocity_ms", 0.0) or 0.0) / v_common_step
                    vs_on_vc = v_after_step / v_common_step

                    zeta_branch, zeta_after = zeta_fn(_angle_deg(branch), ab_on_ac, vb_on_vc, vs_on_vc)
                    result[branch["edge_key"]] = zeta_branch
                    if is_penultimate:
                        result[ordered[-1]["edge_key"]] = zeta_after

                    remaining_flow_lps = after_flow_lps
            else:
                # Mirror of the diverging loop: instead of a shrinking
                # "remaining trunk", the reference here is a growing
                # "accumulated so far" stream, seeded with the straightest
                # secondary's own real flow (it is the fixed "main" duct
                # that every other, less-straight secondary merges into,
                # one at a time) and ending at the primary's real flow once
                # the last (second-straightest) secondary has merged in.
                main = ordered[-1]
                v_main = float(main.get("velocity_ms", 0.0) or 0.0)
                accumulated_flow_lps = float(main.get("flow_rate_lps", 0.0) or 0.0)
                for i in range(m - 1):
                    branch = ordered[i]
                    is_last = (i == m - 2)
                    branch_flow_lps = float(branch.get("flow_rate_lps", 0.0) or 0.0)
                    accumulated_after_lps = accumulated_flow_lps + branch_flow_lps

                    if is_last:
                        # This merge produces the fully-combined stream -- use the primary's own real velocity.
                        v_common_step = v_primary
                    else:
                        v_common_step = airflow.velocity_from_flow(
                            airflow.lps_to_m3s(accumulated_after_lps), a_ref
                        )
                    if v_common_step <= 1e-9:
                        result[branch["edge_key"]] = 0.0
                        accumulated_flow_lps = accumulated_after_lps
                        continue

                    if i == 0:
                        # Nothing has merged into main yet -- use its own real velocity.
                        v_straight_side = v_main
                    else:
                        v_straight_side = airflow.velocity_from_flow(
                            airflow.lps_to_m3s(accumulated_flow_lps), a_ref
                        )

                    ab_on_ac = HVACLibraryAPI.port_area(branch) / a_ref
                    vb_on_vc = float(branch.get("velocity_ms", 0.0) or 0.0) / v_common_step
                    vs_on_vc = v_straight_side / v_common_step

                    zeta_branch, zeta_straight = zeta_fn(_angle_deg(branch), ab_on_ac, vb_on_vc, vs_on_vc)
                    result[branch["edge_key"]] = zeta_branch
                    if is_last:
                        result[main["edge_key"]] = zeta_straight

                    accumulated_flow_lps = accumulated_after_lps

            return result
        except Exception:
            return None

    @staticmethod
    def terminal_component_loss(context):
        """
        Generic terminal air device (diffuser/grille/register) loss: a
        dimensionless coefficient K (from properties["LossCoefficient"]),
        referenced to velocity at the device's own neck
        (properties["NeckSize"]) rather than the connecting duct's own
        velocity -- the neck is often a different size than the duct feeding
        it. Converted to a K_effective referenced to the connecting duct's
        velocity (K_effective = K * (V_neck / V_duct)^2, since velocity
        pressure ~ V^2 at constant density) so it composes with the solver's
        existing K * velocity_pressure(duct) convention used for every other
        fitting -- no special-casing needed downstream.

        Expects exactly 1 connected port (a terminal). Returns
        {edge_key: K_effective} or None if NeckSize/LossCoefficient aren't
        set (nothing to compute) or the port geometry is invalid.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 1:
                return None
            port = ports[0]

            properties = context.get("properties") or {}
            neck_size_mm = float(properties.get("NeckSize", 0.0) or 0.0)
            k = float(properties.get("LossCoefficient", 0.0) or 0.0)
            if neck_size_mm <= 0.0 or k <= 0.0:
                return None

            duct_velocity = float(port.get("velocity_ms", 0.0) or 0.0)
            flow_lps = float(port.get("flow_rate_lps", 0.0) or 0.0)
            if flow_lps <= 0.0 or duct_velocity <= 1e-9:
                return {port["edge_key"]: 0.0}

            neck_area_m2 = airflow.circular_area(airflow.mm_to_m(neck_size_mm))
            neck_velocity_ms = airflow.velocity_from_flow(airflow.lps_to_m3s(flow_lps), neck_area_m2)

            k_effective = k * (neck_velocity_ms / duct_velocity) ** 2
            return {port["edge_key"]: k_effective}
        except Exception:
            return None

    @staticmethod
    def inline_device_loss(context):
        """
        Generic inline device (damper, VAV box, ...) loss: a single
        dimensionless coefficient K taken directly from
        properties["LossCoefficient"], applied uniformly by the solver to
        the connecting duct's own velocity pressure. No neck-size
        conversion is needed here (unlike terminal_component_loss) since
        these devices carry the same duct through both ports rather than
        stepping down to a separate neck size.

        Returns a float K, or None if LossCoefficient isn't set (nothing
        to compute -- falls back to the solver's generic default).
        """
        try:
            properties = context.get("properties") or {}
            k = float(properties.get("LossCoefficient", 0.0) or 0.0)
            if k <= 0.0:
                return None
            return k
        except Exception:
            return None

    @staticmethod
    def copy_port(port, position=None, direction=None, profile_x_axis=None):
        out = dict(port)
        if position is not None:
            out["position"] = HVACLibraryAPI.vec(position)
        if direction is not None:
            out["direction"] = HVACLibraryAPI.vec(direction)
        if profile_x_axis is not None:
            out["profile_x_axis"] = HVACLibraryAPI.vec(profile_x_axis)
        return out
    
    @staticmethod
    def build_trim_rec_from_port_lengths(port_lengths):
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
        ports = list(context.get("connected_ports", []) or [])
        return HVACLibraryAPI.build_trim_rec_from_port_lengths([(p, length_value) for p in ports])


    # ------------------------------------------------------------------
    # Frame helpers
    # ------------------------------------------------------------------
    @staticmethod
    def make_profile_frame(direction, preferred_x=None, origin=None):
        return hvaclib.make_profile_frame(direction, preferred_x, origin)

    # ------------------------------------------------------------------
    # Section/profile creation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def make_line_edge(p0, p1):
        v0 = HVACLibraryAPI.vec(p0)
        v1 = HVACLibraryAPI.vec(p1)

        if (v1.sub(v0)).Length <= 1e-9:
            raise ValueError("Cannot create line edge from coincident points")

        return Part.makeLine(v0, v1)

    @staticmethod
    def make_wire_from_edges(edges):
        edge_list = [e for e in (edges or []) if e is not None]

        if not edge_list:
            raise ValueError("make_wire_from_edges requires at least one edge")

        wire = Part.Wire(edge_list)

        if wire.isNull():
            raise ValueError("Failed to create wire from edges")

        return wire
    
    @staticmethod
    def make_rectangular_wire(center, x_axis, y_axis, width, height):
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
        profile = HVACLibraryAPI.port_profile(port)
        center = HVACLibraryAPI.port_position(port)
        direction = HVACLibraryAPI.port_direction(port)
        preferred_x = HVACLibraryAPI.port_profile_x_axis(port)
        section_params = HVACLibraryAPI.port_section_params(port)
        
        return HVACLibraryAPI.make_section_wire(profile, section_params, center, direction, profile_x_axis=preferred_x)
            
    @staticmethod
    def make_section_face(profile, section_params, center, direction, profile_x_axis=None):
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
    def make_straight_shape(start_point, end_point, profile, section_params, profile_x_axis=None):
        p1 = HVACLibraryAPI.vec(start_point)
        p2 = HVACLibraryAPI.vec(end_point)
        direction = p2 - p1
        length = direction.Length
        if length <= HVACLibraryAPI.EPS:
            raise ValueError("Start and end points cannot be identical")

        face = HVACLibraryAPI.make_section_face(
            profile=profile,
            section_params=section_params,
            center=p1,
            direction=direction,
            profile_x_axis=profile_x_axis,
        )
        # _, _, _, z_axis = HVACLibraryAPI.make_profile_frame(direction, profile_x_axis, p1)
        shape = face.extrude(HVACLibraryAPI.unit(direction) * length)

        try:
            return shape.removeSplitter()
        except Exception:
            return shape

    # ------------------------------------------------------------------
    # Sweep helpers
    # ------------------------------------------------------------------
    @staticmethod
    def make_curved_shape(start_point, end_point, profile, section_params, path, profile_x_axis=None, direction = None):
        p1 = HVACLibraryAPI.vec(start_point)
        p2 = HVACLibraryAPI.vec(end_point)
        if direction is None:
            direction = p2 - p1
        direction.normalize()

        if (p2 - p1).Length <= HVACLibraryAPI.EPS:
            raise ValueError("Start and end points cannot be identical")

        section_wire = HVACLibraryAPI.make_section_wire(
            profile=profile,
            section_params=section_params,
            center=p1,
            direction=direction,
            profile_x_axis=profile_x_axis,
        )
        path_wire = Part.Wire([path])
        shape = HVACLibraryAPI.make_pipe_shell(
            spine_wire=path_wire,
            profile_wires=[section_wire],
            make_solid=True,
            is_frenet=False,
        )
        
        try:
            return shape.removeSplitter()
        except Exception:
            return shape
            
    @staticmethod
    def make_pipe_shell(spine_wire, profile_wires, make_solid=True, is_frenet=False):
        shell = Part.BRepOffsetAPI.MakePipeShell(spine_wire)
        for pw in profile_wires:
            shell.add(pw)
        shell.setFrenetMode(bool(is_frenet))
        shell.build()
        if make_solid:
            shell.makeSolid()
        return shell.shape()
        
    @staticmethod
    def make_loft(profile_wires, solid=True, ruled=True):
        return Part.makeLoft(profile_wires, bool(solid), bool(ruled))

    @staticmethod
    def line_wire(p1, p2):
        return Part.Wire([Part.makeLine(HVACLibraryAPI.vec(p1), HVACLibraryAPI.vec(p2))])

    @staticmethod
    def arc_wire(p1, pm, p2):
        edge = Part.Arc(
            HVACLibraryAPI.vec(p1),
            HVACLibraryAPI.vec(pm),
            HVACLibraryAPI.vec(p2),
        ).toShape()
        return Part.Wire([edge])

    @staticmethod
    def fuse_shapes(shapes):
        valid = [s for s in (shapes or []) if s is not None]
        if not valid:
            raise ValueError("No shapes to fuse")
        out = valid[0]
        for shp in valid[1:]:
            out = out.fuse(shp)
        try:
            return out.removeSplitter()
        except Exception:
            return out

    # ------------------------------------------------------------------
    # External geometry sources
    # ------------------------------------------------------------------
    @staticmethod
    def shape_from_openscad(scad_path, params=None, timeout=60):
        from . import openscad_shapes
        return openscad_shapes.build_shape_from_openscad(scad_path, params, timeout)

    @staticmethod
    def shape_from_fcstd(fcstd_path, context, params=None, result_object="Result",
                          port_names=None, tol_mm=0.5, tol_deg=0.5):
        from . import template_shapes
        return template_shapes.build_shape_from_template(
            fcstd_path, context, params, result_object, port_names, tol_mm, tol_deg
        )

    @staticmethod
    def resolve_library_file(context, relative_path):
        lib = hvaclib.HVACLibraryService.get_hvac_library_registry().get_library(context["library_id"])
        return os.path.normpath(os.path.join(lib.root_path, relative_path))
