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

"""Stable fitting-loss calculations exposed to HVAC libraries.

Loss modules receive this class as ``context["loss_api"]``. It translates
library context data into the pure SMACNA/ASHRAE table inputs and returns
coefficients using the registry's established dict/float/None contract.
"""

import math

from ..analysis import physics as airflow
from .library_api import HVACLibraryAPI
from . import smacna_loss


class HVACLossAPI:
    """Library-facing fitting-loss orchestration API."""

    API_VERSION = 1

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

