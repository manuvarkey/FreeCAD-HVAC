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
an analysis.loss.LossEvaluation -- never a bare dict/float/None (see that
module's own docstring for the LossPath/LossEvaluation/LossStatus shapes).
Generator code never imports analysis.loss directly; it compares against
the class-level LossStatus aliases below instead (e.g.
``result.status == context["loss_api"].UNSUPPORTED``).
"""

import math

from ..analysis import physics as airflow
from ..analysis.loss import LossEvaluation, LossPath, LossStatus
from .library_api import HVACLibraryAPI
from . import smacna_loss


class HVACLossAPI:
    """Library-facing fitting-loss orchestration API."""

    API_VERSION = 1

    # Aliases so generator code (which only ever sees context["loss_api"],
    # never imports analysis.loss directly) can compare a result's status
    # without a new import path.
    EXACT = LossStatus.EXACT
    APPROXIMATION = LossStatus.APPROXIMATION
    CUSTOM = LossStatus.CUSTOM
    FALLBACK = LossStatus.FALLBACK
    UNSUPPORTED = LossStatus.UNSUPPORTED

    # ------------------------------------------------------------------
    # LossEvaluation/LossPath builders -- shared by every method below.
    # ------------------------------------------------------------------

    @staticmethod
    def _leg_path(port, other_edge_key, k, source=None):
        """
        LossPath for `port` (has its own edge_key/flow_into_junction)
        against `other_edge_key` (the fitting's other side -- another
        port's edge_key, or None for a 1-port terminal's open-atmosphere
        side). `k` is referenced to `port`'s own velocity -- the
        convention every table in this module already uses.
        """
        edge_key = port["edge_key"]
        if port.get("flow_into_junction"):
            from_edge, to_edge = edge_key, other_edge_key
        else:
            from_edge, to_edge = other_edge_key, edge_key
        return LossPath(from_edge, to_edge, edge_key, float(k), source=source)

    @staticmethod
    def _exact(paths, warning=None):
        return LossEvaluation(paths=list(paths), status=LossStatus.EXACT, warning=warning)

    @staticmethod
    def _approximation(paths, warning=None):
        return LossEvaluation(paths=list(paths), status=LossStatus.APPROXIMATION, warning=warning)

    @staticmethod
    def _unsupported(warning=None):
        return LossEvaluation(paths=[], status=LossStatus.UNSUPPORTED, warning=warning)

    @staticmethod
    def elbow_loss(context):
        """
        90 deg elbow fitting loss. Expects exactly 2 connected_ports (one
        inlet, one outlet) and a "CenterlineRadius" entry in properties.
        Returns one EXACT LossPath referenced to the outlet's own
        velocity, or UNSUPPORTED if the topology/geometry doesn't support
        the calculation.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 2:
                return HVACLossAPI._unsupported(
                    "expected exactly 2 connected ports, found {}".format(len(ports))
                )
            outlet = next((p for p in ports if p.get("flow_into_junction") is False), None)
            if outlet is None:
                return HVACLossAPI._unsupported("could not resolve outlet direction")
            inlet = next((p for p in ports if p is not outlet), None)

            radius = float((context.get("properties") or {}).get("CenterlineRadius", 0.0) or 0.0)
            profile = HVACLibraryAPI.port_profile(outlet)

            if profile == "Circular":
                diameter = HVACLibraryAPI.port_diameter(outlet)
                if diameter <= 0.0 or radius <= 0.0:
                    return HVACLossAPI._unsupported("invalid diameter/CenterlineRadius")
                zeta = smacna_loss.elbow_zeta_round(radius / diameter)
            elif profile in ("Rectangular", "Oval"):
                width = HVACLibraryAPI.port_width(outlet)
                height = HVACLibraryAPI.port_height(outlet)
                if width <= 0.0 or height <= 0.0 or radius <= 0.0:
                    return HVACLossAPI._unsupported("invalid width/height/CenterlineRadius")
                reynolds = float(outlet.get("reynolds", 0.0) or 0.0)
                if reynolds <= 0.0:
                    return HVACLossAPI._unsupported("no Reynolds number available (zero flow?)")
                zeta = smacna_loss.elbow_zeta_rect(height / width, radius / width, reynolds)
            else:
                return HVACLossAPI._unsupported("unsupported profile '{}'".format(profile))

            return HVACLossAPI._exact(
                [HVACLossAPI._leg_path(outlet, inlet["edge_key"], zeta, source="smacna_elbow")]
            )
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing elbow loss: {}".format(exc))

    @staticmethod
    def transition_loss(context):
        """
        Area-change (expansion/contraction) transition fitting loss. Expects
        exactly 2 connected_ports and a "TransitionLength" entry in
        properties. Returns one EXACT LossPath referenced to the outlet's
        own velocity, or UNSUPPORTED if the topology/geometry doesn't
        support the calculation.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 2:
                return HVACLossAPI._unsupported(
                    "expected exactly 2 connected ports, found {}".format(len(ports))
                )
            outlet = next((p for p in ports if p.get("flow_into_junction") is False), None)
            inlet = next((p for p in ports if p.get("flow_into_junction") is True), None)
            if outlet is None or inlet is None:
                return HVACLossAPI._unsupported("could not resolve inlet/outlet direction")

            area_out = HVACLibraryAPI.port_area(outlet)
            area_in = HVACLibraryAPI.port_area(inlet)
            if area_out <= 0.0 or area_in <= 0.0:
                return HVACLossAPI._unsupported("invalid inlet/outlet area")

            area_ratio = max(area_in, area_out) / min(area_in, area_out)
            if area_ratio <= 1.05:
                # Essentially the same size on both sides (e.g. a lateral
                # offset) -- SMACNA's tables start at an area ratio of 2:1
                # and don't cover this case; treat as negligible loss rather
                # than clamping to the table's (much larger) minimum entry.
                return HVACLossAPI._exact(
                    [HVACLossAPI._leg_path(outlet, inlet["edge_key"], 0.0, source="smacna_transition")]
                )

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
            if profile not in ("Circular", "Rectangular"):
                # Oval/Generic/custom profile -- unlike branch (tee/wye)
                # fittings, no SMACNA table or documented approximation
                # covers this shape for a straight-axis area change
                # (expansion_zeta_rect is a literal rectangular table, A8B;
                # never silently reused here). A library-specific
                # loss.variant can still supply its own formula for this
                # case (see validation.resolve_loss_variant).
                return HVACLossAPI._unsupported(
                    "no transition table for profile '{}'".format(profile)
                )

            if area_out > area_in:
                # Expanding (diverging): downstream duct is larger.
                if profile == "Circular":
                    reynolds = float(inlet.get("reynolds", 0.0) or 0.0)
                    if reynolds <= 0.0:
                        return HVACLossAPI._unsupported("no Reynolds number available (zero flow?)")
                    zeta = smacna_loss.expansion_zeta_round(theta_deg, area_ratio, reynolds)
                else:
                    zeta = smacna_loss.expansion_zeta_rect(theta_deg, area_ratio)
            else:
                # Contracting (converging): downstream duct is smaller.
                # SMACNA A9A explicitly covers both round and rectangular
                # with the one table.
                zeta = smacna_loss.contraction_zeta(theta_deg, area_ratio)

            return HVACLossAPI._exact(
                [HVACLossAPI._leg_path(outlet, inlet["edge_key"], zeta, source="smacna_transition")]
            )
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing transition loss: {}".format(exc))

    @staticmethod
    def _leg_angle_deg(leg_dir, reference_dir):
        """
        Angle (degrees) a leg makes against a reference leg, given both as
        outward-pointing direction vectors (each pointing away from the
        junction along its own duct, e.g. HVACLibraryAPI.vec(port["direction"])).
        Two outward-pointing vectors that are anti-parallel represent duct
        legs that are physically in line with each other (0 deg apart, not
        180), so this is 180 minus the raw angle between the vectors.
        Shared by every branch-angle lookup in this module (branch_loss,
        _no_straight_leg_branch_zetas, manifold_loss) instead of
        reimplementing the same clamp/acos/180-minus pattern in each.
        """
        cos_angle = max(-1.0, min(1.0, leg_dir.dot(reference_dir)))
        return 180.0 - math.degrees(math.acos(cos_angle))

    @staticmethod
    def _split_single_common_port(ports):
        """
        Split `ports` into (primary, secondaries, diverging) where exactly
        one port sits on one flow side (the sole inlet if diverging, the
        sole outlet if converging) and the rest sit on the other side --
        the single-trunk shape shared by branch_loss, branch_loss_bullhead,
        wye_loss, and manifold_loss. Returns None when that's not the flow
        pattern (e.g. a mixed multi-inlet/multi-outlet "true cross" has no
        single trunk to decompose against).
        """
        inlets = [p for p in ports if p.get("flow_into_junction") is True]
        outlets = [p for p in ports if p.get("flow_into_junction") is False]

        if len(inlets) == 1 and outlets:
            return inlets[0], outlets, True
        if len(outlets) == 1 and inlets:
            return outlets[0], inlets, False
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

        Returns two EXACT LossPaths (branch, straight), each referenced to
        that leg's own velocity, or UNSUPPORTED for an ambiguous/degenerate
        flow pattern or invalid geometry.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 3:
                return HVACLossAPI._unsupported(
                    "expected exactly 3 connected ports, found {}".format(len(ports))
                )

            split = HVACLossAPI._split_single_common_port(ports)
            if split is None:
                return HVACLossAPI._unsupported("ambiguous/degenerate flow pattern (no single common port)")
            primary, secondaries, diverging = split

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
                return HVACLossAPI._exact([
                    HVACLossAPI._leg_path(branch, primary["edge_key"], 0.0, source="smacna_branch"),
                    HVACLossAPI._leg_path(straight, primary["edge_key"], 0.0, source="smacna_branch"),
                ])

            a_common = HVACLibraryAPI.port_area(primary)
            a_branch = HVACLibraryAPI.port_area(branch)
            if a_common <= 0.0 or a_branch <= 0.0:
                return HVACLossAPI._unsupported("invalid common/branch area")

            branch_dir = HVACLibraryAPI.vec(branch["direction"])
            straight_dir = HVACLibraryAPI.vec(straight["direction"])
            angle_deg = HVACLossAPI._leg_angle_deg(branch_dir, straight_dir)

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

            source = "smacna_branch_diverging" if diverging else "smacna_branch_converging"
            return HVACLossAPI._exact([
                HVACLossAPI._leg_path(branch, primary["edge_key"], zeta_branch, source=source),
                HVACLossAPI._leg_path(straight, primary["edge_key"], zeta_straight, source=source),
            ])
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing branch loss: {}".format(exc))

    @staticmethod
    def _no_straight_leg_branch_zetas(common, legs, diverging):
        """
        Shared math for a 3-port branch fitting where no leg can be
        singled out as "the straight-through continuation" of the common
        leg -- used by both a bullhead tee/wye (branch_loss_bullhead) and
        a true Wye (wye_loss). These are geometrically different fittings
        (a bullhead is a Tee whose common flow sits on the branch leg; a
        true Wye has no run/branch split at all -- see NetworkParser's
        qualifiers["common_leg"], never assigned to a Wye), but they share
        the exact same "every non-common leg is independently a branch
        relative to the common leg" shape, so the underlying calculation
        lives here once rather than duplicated.

        No SMACNA table treats this shape as its own entry (neither leg
        gets the "straight-through continuation" the ordinary branch
        table's branch/straight split assumes), so each leg is evaluated
        independently against the common leg with the same diverging/
        converging branch-zeta tables an ordinary tee's own branch leg
        uses, reusing each leg's own velocity ratio for both table
        arguments -- a reasonable estimate, not a validated table value.

        Returns [LossPath, ...] (one per leg, each already referenced to
        that leg's own velocity and to `common`'s own edge_key), or None
        if the geometry is invalid (caller turns that into UNSUPPORTED).
        """
        v_common = float(common.get("velocity_ms", 0.0) or 0.0)
        if v_common <= 1e-9:
            return [HVACLossAPI._leg_path(leg, common["edge_key"], 0.0) for leg in legs]

        a_common = HVACLibraryAPI.port_area(common)
        if a_common <= 0.0:
            return None

        common_dir = HVACLibraryAPI.vec(common["direction"])
        zeta_fn = smacna_loss.diverging_branch_zetas if diverging else smacna_loss.converging_branch_zetas

        paths = []
        for leg in legs:
            a_leg = HVACLibraryAPI.port_area(leg)
            if a_leg <= 0.0:
                return None

            leg_dir = HVACLibraryAPI.vec(leg["direction"])
            angle_deg = HVACLossAPI._leg_angle_deg(leg_dir, common_dir)

            a_on_ac = a_leg / a_common
            v_on_vc = float(leg.get("velocity_ms", 0.0) or 0.0) / v_common
            # No independent "straight-through" leg exists here, so the
            # reference velocity ratio the table otherwise expects for it
            # is undefined -- reuse this leg's own ratio for both table
            # arguments.
            zeta_leg, _ = zeta_fn(angle_deg, a_on_ac, v_on_vc, v_on_vc)
            paths.append(HVACLossAPI._leg_path(leg, common["edge_key"], zeta_leg))

        return paths

    @staticmethod
    def branch_loss_bullhead(context):
        """
        Bullhead tee/wye: the single common-flow port sits on the
        geometric *branch* leg instead of the run (see NetworkParser's
        qualifiers["common_leg"] == "branch" -- library JSON loss.variants
        route here for exactly that case, distinct from the ordinary
        common_leg == "run" case branch_loss() above already handles
        correctly). Expects exactly 3 connected_ports.

        This is a generic APPROXIMATION (see _no_straight_leg_branch_zetas),
        not a validated SMACNA table entry for a bullhead arrangement --
        never presented to a caller as an EXACT table result.

        Returns one LossPath per run leg, each already referenced to that
        leg's own velocity, or UNSUPPORTED for an ambiguous/degenerate flow
        pattern or invalid geometry.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 3:
                return HVACLossAPI._unsupported(
                    "expected exactly 3 connected ports, found {}".format(len(ports))
                )

            split = HVACLossAPI._split_single_common_port(ports)
            if split is None:
                return HVACLossAPI._unsupported("ambiguous/degenerate flow pattern (no single common port)")
            common, run_legs, diverging = split

            paths = HVACLossAPI._no_straight_leg_branch_zetas(common, run_legs, diverging)
            if paths is None:
                return HVACLossAPI._unsupported("invalid port geometry (zero/invalid duct area)")
            return HVACLossAPI._approximation(paths)
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing bullhead branch loss: {}".format(exc))

    @staticmethod
    def wye_loss(context):
        """
        True Wye (branch.wye) fitting loss: three ports where no pair is a
        Tee-style collinear run -- NetworkParser never assigns a
        qualifiers["common_leg"] to a Wye, since there's no run/branch
        split to name (see TOPOLOGY_CLASSIFICATION.md). This is therefore
        a distinct API entry point from branch_loss()/branch_loss_bullhead()
        rather than routing a Wye through either of those Tee-oriented
        methods -- it never guesses a "straight-through" secondary leg,
        since a true Wye has none by definition.

        Internally this reuses the same "no independent straight leg"
        calculation a bullhead tee/wye needs (see
        _no_straight_leg_branch_zetas): each non-common leg is evaluated
        independently against the common leg with the diverging/converging
        branch-zeta tables. This is a reasonable engineering APPROXIMATION,
        not a dedicated SMACNA Wye table result (SMACNA's own branch tables
        are keyed by one branch's angle against a straight run, not by two
        symmetric legs) -- never presented as an EXACT table value.

        Expects exactly 3 connected_ports. Returns one LossPath per leg,
        each already referenced to that leg's own velocity, or UNSUPPORTED
        for an ambiguous/degenerate flow pattern or invalid geometry.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 3:
                return HVACLossAPI._unsupported(
                    "expected exactly 3 connected ports, found {}".format(len(ports))
                )

            split = HVACLossAPI._split_single_common_port(ports)
            if split is None:
                return HVACLossAPI._unsupported("ambiguous/degenerate flow pattern (no single common port)")
            common, legs, diverging = split

            paths = HVACLossAPI._no_straight_leg_branch_zetas(common, legs, diverging)
            if paths is None:
                return HVACLossAPI._unsupported("invalid port geometry (zero/invalid duct area)")
            return HVACLossAPI._approximation(paths)
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing wye loss: {}".format(exc))

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
        virtual merges/splits -- an APPROXIMATION, not a literal per-leg
        geometry readout. With exactly 2 secondaries this reduces to
        exactly the same numbers as branch_loss's 3-port calculation.

        Returns one LossPath per secondary port (each already referenced to
        that leg's own velocity and to the primary port's own edge_key), or
        UNSUPPORTED for a mixed multi-inlet/multi-outlet ("true cross") flow
        pattern -- which has no single trunk to decompose against -- or on
        any missing/invalid geometry.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) < 3:
                return HVACLossAPI._unsupported(
                    "expected at least 3 connected ports, found {}".format(len(ports))
                )

            split = HVACLossAPI._split_single_common_port(ports)
            if split is None:
                return HVACLossAPI._unsupported(
                    "mixed multi-inlet/multi-outlet flow arrangement has no single trunk to decompose"
                )
            primary, secondaries, diverging = split

            if len(secondaries) < 2:
                return HVACLossAPI._unsupported("expected at least 2 secondary ports")

            a_ref = HVACLibraryAPI.port_area(primary)
            v_primary = float(primary.get("velocity_ms", 0.0) or 0.0)
            if a_ref <= 0.0:
                return HVACLossAPI._unsupported("invalid primary port area")
            port_by_edge = {p["edge_key"]: p for p in secondaries}
            if v_primary <= 1e-9:
                paths = [
                    HVACLossAPI._leg_path(p, primary["edge_key"], 0.0, source="smacna_manifold")
                    for p in secondaries
                ]
                return HVACLossAPI._approximation(paths)
            for p in secondaries:
                if HVACLibraryAPI.port_area(p) <= 0.0:
                    return HVACLossAPI._unsupported("invalid secondary port area")

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
                return HVACLossAPI._leg_angle_deg(branch_dir, straight_dir)

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

            paths = [
                HVACLossAPI._leg_path(port_by_edge[edge_key], primary["edge_key"], k, source="smacna_manifold")
                for edge_key, k in result.items()
            ]
            return HVACLossAPI._approximation(paths)
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing manifold loss: {}".format(exc))

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

        Expects exactly 1 connected port (a terminal). Returns one EXACT
        LossPath (the port's open-atmosphere side is None), or UNSUPPORTED
        if NeckSize/LossCoefficient aren't set (nothing to compute) or the
        port geometry is invalid.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 1:
                return HVACLossAPI._unsupported(
                    "expected exactly 1 connected port, found {}".format(len(ports))
                )
            port = ports[0]

            properties = context.get("properties") or {}
            neck_size_mm = float(properties.get("NeckSize", 0.0) or 0.0)
            k = float(properties.get("LossCoefficient", 0.0) or 0.0)
            if neck_size_mm <= 0.0 or k <= 0.0:
                return HVACLossAPI._unsupported("NeckSize/LossCoefficient not set")

            duct_velocity = float(port.get("velocity_ms", 0.0) or 0.0)
            flow_lps = float(port.get("flow_rate_lps", 0.0) or 0.0)
            if flow_lps <= 0.0 or duct_velocity <= 1e-9:
                return HVACLossAPI._exact([HVACLossAPI._leg_path(port, None, 0.0, source="terminal_device")])

            neck_area_m2 = airflow.circular_area(airflow.mm_to_m(neck_size_mm))
            neck_velocity_ms = airflow.velocity_from_flow(airflow.lps_to_m3s(flow_lps), neck_area_m2)

            k_effective = k * (neck_velocity_ms / duct_velocity) ** 2
            return HVACLossAPI._exact(
                [HVACLossAPI._leg_path(port, None, k_effective, source="terminal_device")]
            )
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing terminal device loss: {}".format(exc))

    @staticmethod
    def inline_device_loss(context):
        """
        Generic inline device (damper, VAV box, ...) loss: a single
        dimensionless coefficient K taken directly from
        properties["LossCoefficient"], applied to the connecting duct's own
        velocity pressure at the device's outlet. No neck-size conversion is
        needed here (unlike terminal_component_loss) since these devices
        carry the same duct through both ports rather than stepping down to
        a separate neck size.

        Expects exactly 2 connected_ports (one inlet, one outlet). Returns
        one EXACT LossPath referenced to the outlet's own velocity
        (matching the inline-chain convention analysis/pressure.py already
        uses), or UNSUPPORTED if LossCoefficient isn't set (nothing to
        compute) or the topology can't be resolved.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
            if len(ports) != 2:
                return HVACLossAPI._unsupported(
                    "expected exactly 2 connected ports, found {}".format(len(ports))
                )
            outlet = next((p for p in ports if p.get("flow_into_junction") is False), None)
            inlet = next((p for p in ports if p is not outlet), None)
            if outlet is None:
                return HVACLossAPI._unsupported("could not resolve outlet direction")

            properties = context.get("properties") or {}
            k = float(properties.get("LossCoefficient", 0.0) or 0.0)
            if k <= 0.0:
                return HVACLossAPI._unsupported("LossCoefficient not set")

            return HVACLossAPI._exact(
                [HVACLossAPI._leg_path(outlet, inlet["edge_key"], k, source="inline_device")]
            )
        except Exception as exc:
            return HVACLossAPI._unsupported("unexpected error computing inline device loss: {}".format(exc))

    @staticmethod
    def uniform_fallback_loss(context, k, warning=None):
        """
        Explicit generic-K policy for a fitting shape with no dedicated
        formula (e.g. a true multi-in/multi-out cross): applies `k`
        uniformly to every outlet port, same as the old "return a bare
        float" convention used to mean -- but now an explicit FALLBACK
        result instead of a silently-applied default.
        """
        try:
            ports = HVACLibraryAPI.connected_ports(context)
        except Exception:
            ports = []
        outlets = [p for p in ports if p.get("flow_into_junction") is False]
        paths = [
            HVACLossAPI._leg_path(p, None, k, source="type-declared fallback") for p in outlets
        ]
        return LossEvaluation(paths=paths, status=LossStatus.FALLBACK, warning=warning)
