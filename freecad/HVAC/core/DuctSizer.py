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
Whole-network duct sizing: given the same design-flow-rate boundary
conditions and flow distribution as AirflowSolver (see FlowNetwork.py),
compute duct dimensions instead of pressure drop -- solving for size from a
target constant velocity, constant friction rate, or static regain, per the
active DuctNetwork's SizingMethod.

ConstantVelocity and ConstantFrictionRate size each segment independently
(order doesn't matter). StaticRegain does not: each section's target depends
on its already-solved parent section's velocity, so sections are sized in
sequence from the balancing terminal (the source) outward, seeded by the
network's TargetVelocity, reusing FlowComponent.order/parent_edge (the same
rooted-tree walk AirflowSolver uses for pressure propagation).

A segment's own Velocity/RectangularSizingMode/TargetAspectRatio overrides
(see _size_segment) still apply during a StaticRegain solve -- a Velocity-
overridden segment is sized by constant velocity as usual, and its resulting
velocity simply becomes the seed for whatever is downstream of it.

DuctSizer.solve() never mutates any segment object; it only computes and
returns proposed sizes (a preview). DuctSizer.apply(result) is a separate,
explicit step that writes the proposed Diameter/Width/Height onto the real
segment objects -- callers (the Size Ducts command/UI) are expected to show
the preview and let the user confirm before applying.
"""

import math
from dataclasses import dataclass, field

from ..utils import hvaclib
from . import airflow
from .FlowNetwork import solve_flow_components


_RECT_MODE_MAP = {
    "FixedAspectRatio": "aspect_ratio",
    "FixedHeight": "fixed_height",
    "FixedWidth": "fixed_width",
}


@dataclass
class SegmentSizeResult:
    key: str
    obj: object
    profile: str
    flow_lps: float = 0.0
    old_diameter_mm: float = 0.0
    old_width_mm: float = 0.0
    old_height_mm: float = 0.0
    new_diameter_mm: float = 0.0
    new_width_mm: float = 0.0
    new_height_mm: float = 0.0
    velocity_ms: float = 0.0
    friction_rate_pa_per_m: float = 0.0
    regain_balanced: bool = True  # only meaningful for SizingMethod=StaticRegain; see module docstring
    changed: bool = False


@dataclass
class DuctSizingResult:
    segments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _round_up(value_mm, increment_mm):
    """Round up to the nearest multiple of increment_mm (no-op if increment_mm <= 0)."""
    if increment_mm <= 0.0:
        return value_mm
    return math.ceil(value_mm / increment_mm - 1e-9) * increment_mm


class DuctSizer:
    """Computes proposed duct dimensions for a DuctNetwork's segments (preview-only; see apply())."""

    def __init__(self, net_obj):
        self.net_obj = net_obj

    def solve(self):
        net = self.net_obj
        _, _junction_map, segment_map, components, warnings = solve_flow_components(net)

        result = DuctSizingResult(warnings=list(warnings))

        method = str(getattr(net, "SizingMethod", "ConstantVelocity") or "ConstantVelocity")
        mode = _RECT_MODE_MAP.get(
            str(getattr(net, "RectangularSizingMode", "FixedAspectRatio") or "FixedAspectRatio"),
            "aspect_ratio",
        )
        aspect_ratio = float(getattr(net, "TargetAspectRatio", 2.0) or 2.0)
        target_velocity = float(getattr(net, "TargetVelocity", 5.0) or 5.0)
        target_friction_rate = float(getattr(net, "TargetFrictionRate", 1.0) or 1.0)
        regain_factor = float(getattr(net, "StaticRegainFactor", 0.75) or 0.75)
        min_velocity = float(getattr(net, "MinimumVelocity", 2.5) or 2.5)
        # Note: no "or 10.0" fallback here -- unlike the other target
        # properties, 0 is a legitimate, intentional value for this one
        # (disable rounding, keep exact computed sizes), so an explicit 0
        # must not be silently replaced by the default.
        rounding_mm = float(getattr(net, "SizeRoundingIncrement", 10.0))
        viscosity = float(getattr(net, "AirKinematicViscosity", 1.51e-5) or 1.51e-5)
        density = float(getattr(net, "AirDensity", 1.204) or 1.204)
        default_roughness_mm = float(getattr(net, "DefaultRoughness", 0.09) or 0.09)
        default_width_mm = float(getattr(net, "DefaultWidth", 0.0) or 0.0)
        default_height_mm = float(getattr(net, "DefaultHeight", 0.0) or 0.0)

        for comp in components:
            if method == "StaticRegain":
                self._solveComponentStaticRegain(
                    comp, segment_map, result, mode, aspect_ratio, target_velocity, rounding_mm,
                    viscosity, density, default_roughness_mm, default_width_mm, default_height_mm,
                    regain_factor, min_velocity,
                )
                continue

            for edge_ref in comp.comp_edges:
                seg_obj = segment_map[edge_ref.tag]
                flow_lps = comp.edge_flow_lps[edge_ref]
                try:
                    result.segments.append(self._size_segment(
                        seg_obj, flow_lps, method, mode, aspect_ratio,
                        target_velocity, target_friction_rate, rounding_mm,
                        viscosity, density, default_roughness_mm,
                        default_width_mm, default_height_mm,
                    ))
                except ValueError as exc:
                    result.warnings.append("{}: {}".format(seg_obj.Label, exc))

        return result

    def _solveComponentStaticRegain(self, comp, segment_map, result, mode, aspect_ratio,
                                     target_velocity, rounding_mm, viscosity, density,
                                     default_roughness_mm, default_width_mm, default_height_mm,
                                     regain_factor, min_velocity):
        """
        Size every segment in this component in order from the balancing
        terminal (comp.root_node_id) outward, using comp.order (a BFS walk
        that always visits a node after its parent) so each segment's own
        upstream velocity is already resolved by the time it's needed.

        The section(s) leaving the source directly (parent == root) have no
        upstream section to regain from, so -- standard practice -- they are
        sized directly at the network's TargetVelocity (a chosen starting
        velocity) via constant-velocity sizing. Every other section is sized
        by the regain-balance equation against its own parent's already-
        resolved velocity.
        """
        velocity_by_node = {}

        for node_id in comp.order[1:]:
            edge_ref = comp.parent_edge[node_id]
            parent_id = comp.parent_node[node_id]
            seg_obj = segment_map[edge_ref.tag]
            flow_lps = comp.edge_flow_lps[edge_ref]

            if parent_id == comp.root_node_id:
                effective_method = "ConstantVelocity"
                upstream_vp = 0.0  # unused by _size_segment for ConstantVelocity
            else:
                effective_method = "StaticRegain"
                upstream_vp = airflow.velocity_pressure(density, velocity_by_node[parent_id])

            try:
                sres = self._size_segment(
                    seg_obj, flow_lps, effective_method, mode, aspect_ratio,
                    target_velocity, 0.0, rounding_mm,
                    viscosity, density, default_roughness_mm,
                    default_width_mm, default_height_mm,
                    upstream_velocity_pressure_pa=upstream_vp,
                    regain_factor=regain_factor, min_velocity=min_velocity,
                )
                result.segments.append(sres)
                velocity_by_node[node_id] = sres.velocity_ms
                if not sres.regain_balanced:
                    result.warnings.append(
                        "{}: static regain could not be balanced for this section (clamped to the "
                        "minimum/maximum velocity bracket) -- a balancing damper may be needed "
                        "here.".format(seg_obj.Label)
                    )
            except ValueError as exc:
                result.warnings.append("{}: {}".format(seg_obj.Label, exc))
                # Can't resolve this section's own velocity -- seed anything
                # downstream of it with a sensible fallback (the upstream
                # velocity, or the network's TargetVelocity at the source) so
                # the rest of this sub-tree still gets an (less accurate)
                # answer instead of cascading into more failures.
                velocity_by_node[node_id] = velocity_by_node.get(parent_id, target_velocity)

    def apply(self, result):
        """Write every changed proposed size onto its real segment object."""
        changed_count = 0
        for sres in result.segments:
            if not sres.changed:
                continue
            obj = sres.obj
            if sres.profile == "Circular":
                obj.Diameter = sres.new_diameter_mm
            else:
                obj.Width = sres.new_width_mm
                obj.Height = sres.new_height_mm
            changed_count += 1
        return changed_count

    # ------------------------------------------------------------------
    # Per-segment sizing
    # ------------------------------------------------------------------

    def _size_segment(self, seg_obj, flow_lps, method, mode, aspect_ratio,
                       target_velocity, target_friction_rate, rounding_mm,
                       viscosity, density, default_roughness_mm,
                       default_width_mm, default_height_mm,
                       upstream_velocity_pressure_pa=0.0, regain_factor=0.75, min_velocity=2.5):
        profile = str(getattr(seg_obj, "Profile", "") or "")
        section_params = hvaclib.get_segment_section_params(seg_obj)
        old_diameter_mm = float(section_params.get("Diameter", 0.0) or 0.0)
        old_width_mm = float(section_params.get("Width", 0.0) or 0.0)
        old_height_mm = float(section_params.get("Height", 0.0) or 0.0)
        key = getattr(seg_obj, "SegmentKey", "") or seg_obj.Name

        if profile not in ("Circular", "Rectangular", "Oval"):
            raise ValueError("has unsupported or unset Profile '{}'".format(profile))

        base_result = dict(
            key=key, obj=seg_obj, profile=profile, flow_lps=flow_lps,
            old_diameter_mm=old_diameter_mm, old_width_mm=old_width_mm, old_height_mm=old_height_mm,
        )

        if flow_lps <= 1e-9:
            # Nothing to size (e.g. a capped/decorative branch) -- leave dimensions unchanged.
            return SegmentSizeResult(
                new_diameter_mm=old_diameter_mm, new_width_mm=old_width_mm, new_height_mm=old_height_mm,
                changed=False, **base_result
            )

        flow_m3s = airflow.lps_to_m3s(flow_lps)
        roughness_mm = float(getattr(seg_obj, "Roughness", 0.0) or 0.0) or default_roughness_mm
        roughness_m = airflow.mm_to_m(roughness_mm)

        # A segment's own Velocity, if set, overrides both the network's SizingMethod
        # and TargetVelocity for this segment only -- e.g. deliberately running one
        # riser faster/slower than the rest of the system.
        segment_velocity = float(getattr(seg_obj, "Velocity", 0.0) or 0.0)
        if segment_velocity > 0.0:
            method = "ConstantVelocity"
            target_velocity = segment_velocity

        # A segment's own RectangularSizingMode/TargetAspectRatio, if set, override
        # the network's for this segment only -- e.g. one run is height-constrained
        # by a beam or ceiling while the rest of the system uses a fixed aspect ratio.
        seg_mode_raw = str(getattr(seg_obj, "RectangularSizingMode", "UseNetworkDefault") or "UseNetworkDefault")
        if seg_mode_raw != "UseNetworkDefault":
            mode = _RECT_MODE_MAP.get(seg_mode_raw, mode)
        seg_aspect_ratio = float(getattr(seg_obj, "TargetAspectRatio", 0.0) or 0.0)
        if seg_aspect_ratio > 0.0:
            aspect_ratio = seg_aspect_ratio

        fixed_dim_m = None
        if profile in ("Rectangular", "Oval") and mode in ("fixed_height", "fixed_width"):
            if mode == "fixed_height":
                fixed_mm = old_height_mm or default_height_mm
                dim_name = "Height"
            else:
                fixed_mm = old_width_mm or default_width_mm
                dim_name = "Width"
            if fixed_mm <= 0.0:
                raise ValueError(
                    "has no existing/default {} to hold fixed for the current sizing mode".format(dim_name)
                )
            fixed_dim_m = airflow.mm_to_m(fixed_mm)

        length_m = airflow.mm_to_m(float(getattr(seg_obj, "EffectiveLength", 0.0) or 0.0))

        regain_balanced = True  # only ever set False by the StaticRegain branches below

        if profile == "Circular":
            if method == "ConstantVelocity":
                diameter_m = airflow.circular_diameter_for_velocity(flow_m3s, target_velocity)
            elif method == "StaticRegain":
                diameter_m, regain_balanced = airflow.circular_diameter_for_static_regain(
                    flow_m3s, upstream_velocity_pressure_pa, regain_factor, length_m,
                    roughness_m, viscosity, density, min_velocity
                )
            else:
                diameter_m = airflow.circular_diameter_for_friction_rate(
                    flow_m3s, target_friction_rate, roughness_m, viscosity, density
                )
            new_diameter_mm = _round_up(diameter_m * 1000.0, rounding_mm)
            new_width_mm = new_height_mm = 0.0
            area_m2 = airflow.circular_area(airflow.mm_to_m(new_diameter_mm))
            dh_m = airflow.hydraulic_diameter_circular(airflow.mm_to_m(new_diameter_mm))
        else:
            if profile == "Rectangular":
                dims_velocity = airflow.rect_dims_for_velocity
                dims_friction = airflow.rect_dims_for_friction_rate
                dims_regain = airflow.rect_dims_for_static_regain
                area_fn, dh_fn = airflow.rectangular_area, airflow.hydraulic_diameter_rectangular
            else:
                dims_velocity = airflow.oval_dims_for_velocity
                dims_friction = airflow.oval_dims_for_friction_rate
                dims_regain = airflow.oval_dims_for_static_regain
                area_fn, dh_fn = airflow.oval_area, airflow.hydraulic_diameter_oval

            if method == "ConstantVelocity":
                width_m, height_m = dims_velocity(
                    flow_m3s, target_velocity, mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
                )
            elif method == "StaticRegain":
                width_m, height_m, regain_balanced = dims_regain(
                    flow_m3s, upstream_velocity_pressure_pa, regain_factor, length_m,
                    roughness_m, viscosity, density, min_velocity,
                    mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
                )
            else:
                width_m, height_m = dims_friction(
                    flow_m3s, target_friction_rate, roughness_m, viscosity, density,
                    mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
                )

            # Only the solved (free) dimension is rounded; a fixed dimension is
            # kept exactly as-is since it's already an existing/default value.
            if mode == "fixed_height":
                new_height_mm = height_m * 1000.0
                new_width_mm = _round_up(width_m * 1000.0, rounding_mm)
            elif mode == "fixed_width":
                new_width_mm = width_m * 1000.0
                new_height_mm = _round_up(height_m * 1000.0, rounding_mm)
            else:  # aspect_ratio -- both dimensions are solved
                new_height_mm = _round_up(height_m * 1000.0, rounding_mm)
                new_width_mm = _round_up(aspect_ratio * new_height_mm, rounding_mm)

            new_diameter_mm = 0.0
            area_m2 = area_fn(airflow.mm_to_m(new_width_mm), airflow.mm_to_m(new_height_mm))
            dh_m = dh_fn(airflow.mm_to_m(new_width_mm), airflow.mm_to_m(new_height_mm))

        velocity_ms = airflow.velocity_from_flow(flow_m3s, area_m2)
        reynolds = airflow.reynolds_number(velocity_ms, dh_m, viscosity)
        friction_factor = airflow.friction_factor_altshul_tsal(reynolds, roughness_m / dh_m)
        friction_rate_pa_per_m = airflow.darcy_weisbach_pressure_loss(
            friction_factor, 1.0, dh_m, density, velocity_ms
        )

        changed = (
            abs(new_diameter_mm - old_diameter_mm) > 1e-6
            or abs(new_width_mm - old_width_mm) > 1e-6
            or abs(new_height_mm - old_height_mm) > 1e-6
        )

        return SegmentSizeResult(
            new_diameter_mm=new_diameter_mm, new_width_mm=new_width_mm, new_height_mm=new_height_mm,
            velocity_ms=velocity_ms, friction_rate_pa_per_m=friction_rate_pa_per_m,
            regain_balanced=regain_balanced, changed=changed, **base_result
        )
