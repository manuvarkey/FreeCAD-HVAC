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
target constant velocity or constant friction rate, per the active
DuctNetwork's SizingMethod.

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
        rounding_mm = float(getattr(net, "SizeRoundingIncrement", 10.0) or 10.0)
        viscosity = float(getattr(net, "AirKinematicViscosity", 1.51e-5) or 1.51e-5)
        density = float(getattr(net, "AirDensity", 1.204) or 1.204)
        default_roughness_mm = float(getattr(net, "DefaultRoughness", 0.09) or 0.09)
        default_width_mm = float(getattr(net, "DefaultWidth", 0.0) or 0.0)
        default_height_mm = float(getattr(net, "DefaultHeight", 0.0) or 0.0)

        for comp in components:
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
                       default_width_mm, default_height_mm):
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

        if profile == "Circular":
            if method == "ConstantVelocity":
                diameter_m = airflow.circular_diameter_for_velocity(flow_m3s, target_velocity)
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
                dims_velocity, dims_friction = airflow.rect_dims_for_velocity, airflow.rect_dims_for_friction_rate
                area_fn, dh_fn = airflow.rectangular_area, airflow.hydraulic_diameter_rectangular
            else:
                dims_velocity, dims_friction = airflow.oval_dims_for_velocity, airflow.oval_dims_for_friction_rate
                area_fn, dh_fn = airflow.oval_area, airflow.hydraulic_diameter_oval

            if method == "ConstantVelocity":
                width_m, height_m = dims_velocity(
                    flow_m3s, target_velocity, mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
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
            changed=changed, **base_result
        )
