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
Works out proposed duct sizes for a whole NetworkModel -- a pure port of
core/DuctSizer.py's algorithm. Uses the same flow distribution as
pressure.py (see flow.py), but instead of computing pressure drop for an
existing size, it computes the size itself -- from a target constant
velocity, constant friction rate, or local static regain.

ConstantVelocitySizer/ConstantFrictionRateSizer size each segment on its
own, in any order. LocalStaticRegainSizer can't: each section's size
depends on its parent section's already-solved velocity, so segments are
sized one at a time, walking the tree from the source outward (see
FlowComponent.order/parent_edge). The first section(s) off the source have
no upstream velocity to work from, so they're seeded with the settings'
target velocity instead.

A segment's own velocity/rectangular-mode/aspect-ratio override (see
SegmentModel) still applies during a LocalStaticRegain solve -- an
overridden segment is just sized at that fixed velocity, and its result
becomes the seed for whatever comes after it.

Nothing here mutates the NetworkModel: size() only computes and returns
proposed sizes (a preview) -- writing them back onto real FreeCAD segments
is core/DuctSizer.py's job (apply()).

LocalStaticRegainSizer also needs to weigh each section's regain against
the fitting/dynamic loss of the node it takes off from (e.g. a tee's branch
loss), not just its own straight-duct friction -- otherwise it's balancing
against less than the section actually has to overcome. That loss depends
on the very duct sizes being solved for, so it's resolved by iterating:
size the component once (first pass has no fitting-loss estimate yet, same
as a plain regain-only solve), estimate every node's loss from those
proposed sizes (reusing each node's own loss_evaluator, called against a
provisional {edge_key: velocity} built from the proposed sizes), re-size
using that estimate, and repeat until sizes settle.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List

from . import physics
from .model import NetworkModel, SectionModel, SizingSettings
from .pressure import K_DEFAULT

# Fixed-point iteration cap for LocalStaticRegainSizer's fitting-loss
# estimate -- sizes normally settle in 2-3 passes; this is a safety bound,
# not a tuned expectation.
_STATIC_REGAIN_MAX_ITERATIONS = 5


@dataclass
class SegmentSizeResult:
    """Proposed new size for one segment, plus its old size for comparison."""
    edge_key: str
    profile: str
    flow_lps: float = 0.0
    old_section: SectionModel = field(default_factory=SectionModel)
    new_section: SectionModel = field(default_factory=SectionModel)
    velocity_ms: float = 0.0
    reynolds: float = 0.0
    friction_rate_pa_per_m: float = 0.0
    regain_balanced: bool = True  # only meaningful for StaticRegain-family methods
    changed: bool = False


@dataclass
class DuctSizingResult:
    """Whole-network sizing result: one SegmentSizeResult per segment, plus any warnings."""
    segments: Dict[str, SegmentSizeResult] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _round_up(value_mm, increment_mm):
    """Round up to the nearest multiple of increment_mm (no-op if increment_mm <= 0)."""
    if increment_mm <= 0.0:
        return value_mm
    return math.ceil(value_mm / increment_mm - 1e-9) * increment_mm


def _sizes_converged(prev_sizes, sizes, tolerance_mm):
    """True if every segment's (diameter, width, height) moved by no more than tolerance_mm since the last pass."""
    if set(prev_sizes) != set(sizes):
        return False
    for edge_key, new_dims in sizes.items():
        old_dims = prev_sizes[edge_key]
        if any(abs(a - b) > tolerance_mm for a, b in zip(old_dims, new_dims)):
            return False
    return True


def _size_dims(sres: SegmentSizeResult):
    return (sres.new_section.diameter_mm, sres.new_section.width_mm, sres.new_section.height_mm)


# ----------------------------------------------------------------------------
# Per-segment sizing -- shared by all three methods
# ----------------------------------------------------------------------------

def _size_one_segment(seg, flow_lps, settings: SizingSettings, air, method,
                       upstream_velocity_pressure_pa=0.0, fitting_loss_pa=0.0) -> SegmentSizeResult:
    profile = seg.section.profile
    old_section = seg.section
    if profile not in ("Circular", "Rectangular", "Oval"):
        raise ValueError("has unsupported or unset Profile '{}'".format(profile))

    base = dict(edge_key=seg.edge_key, profile=profile, flow_lps=flow_lps, old_section=old_section)

    if flow_lps <= 1e-9:
        # Nothing to size (e.g. a capped/decorative branch) -- leave dimensions unchanged.
        return SegmentSizeResult(new_section=old_section, changed=False, **base)

    flow_m3s = physics.lps_to_m3s(flow_lps)
    roughness_m = physics.mm_to_m(seg.roughness_mm)

    # This segment's own overrides, if any, on top of the network-wide
    # settings passed in.
    mode = settings.rectangular_mode
    aspect_ratio = settings.aspect_ratio
    target_velocity = settings.target_velocity_ms
    if seg.velocity_override_ms > 0.0:
        method = "ConstantVelocity"
        target_velocity = seg.velocity_override_ms
    if seg.rectangular_mode_override:
        mode = seg.rectangular_mode_override
    if seg.aspect_ratio_override > 0.0:
        aspect_ratio = seg.aspect_ratio_override

    # For a rectangular/oval duct sized with one dimension held fixed, work
    # out what that fixed dimension actually is (its current value, falling
    # back to the network default).
    fixed_dim_m = None
    if profile in ("Rectangular", "Oval") and mode in ("fixed_height", "fixed_width"):
        if mode == "fixed_height":
            fixed_mm = old_section.height_mm or settings.default_height_mm
            dim_name = "Height"
        else:
            fixed_mm = old_section.width_mm or settings.default_width_mm
            dim_name = "Width"
        if fixed_mm <= 0.0:
            raise ValueError("has no existing/default {} to hold fixed for the current sizing mode".format(dim_name))
        fixed_dim_m = physics.mm_to_m(fixed_mm)

    length_m = physics.mm_to_m(seg.length_mm)
    regain_balanced = True  # only ever set False by the StaticRegain branches below

    # Solve the actual size, using whichever physics.py formula matches
    # this segment's profile and sizing method.
    if profile == "Circular":
        if method == "ConstantVelocity":
            diameter_m = physics.circular_diameter_for_velocity(flow_m3s, target_velocity)
        elif method == "StaticRegain":
            diameter_m, regain_balanced = physics.circular_diameter_for_static_regain(
                flow_m3s, upstream_velocity_pressure_pa, settings.regain_factor, length_m,
                roughness_m, air.kinematic_viscosity_m2_s, air.density_kg_m3, settings.min_velocity_ms,
                fitting_loss_pa=fitting_loss_pa,
            )
        else:
            diameter_m = physics.circular_diameter_for_friction_rate(
                flow_m3s, settings.target_friction_rate_pa_per_m, roughness_m,
                air.kinematic_viscosity_m2_s, air.density_kg_m3,
            )
        new_diameter_mm = _round_up(diameter_m * 1000.0, settings.rounding_mm)
        new_section = SectionModel(profile="Circular", diameter_mm=new_diameter_mm)
        area_m2 = physics.circular_area(physics.mm_to_m(new_diameter_mm))
        dh_m = physics.hydraulic_diameter_circular(physics.mm_to_m(new_diameter_mm))
    else:
        if profile == "Rectangular":
            dims_velocity = physics.rect_dims_for_velocity
            dims_friction = physics.rect_dims_for_friction_rate
            dims_regain = physics.rect_dims_for_static_regain
            area_fn, dh_fn = physics.rectangular_area, physics.hydraulic_diameter_rectangular
        else:
            dims_velocity = physics.oval_dims_for_velocity
            dims_friction = physics.oval_dims_for_friction_rate
            dims_regain = physics.oval_dims_for_static_regain
            area_fn, dh_fn = physics.oval_area, physics.hydraulic_diameter_oval

        if method == "ConstantVelocity":
            width_m, height_m = dims_velocity(
                flow_m3s, target_velocity, mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
            )
        elif method == "StaticRegain":
            width_m, height_m, regain_balanced = dims_regain(
                flow_m3s, upstream_velocity_pressure_pa, settings.regain_factor, length_m,
                roughness_m, air.kinematic_viscosity_m2_s, air.density_kg_m3, settings.min_velocity_ms,
                mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m, fitting_loss_pa=fitting_loss_pa,
            )
        else:
            width_m, height_m = dims_friction(
                flow_m3s, settings.target_friction_rate_pa_per_m, roughness_m,
                air.kinematic_viscosity_m2_s, air.density_kg_m3,
                mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m,
            )

        # Only the solved (free) dimension is rounded; a fixed dimension is
        # kept exactly as-is since it's already an existing/default value.
        if mode == "fixed_height":
            new_height_mm = height_m * 1000.0
            new_width_mm = _round_up(width_m * 1000.0, settings.rounding_mm)
        elif mode == "fixed_width":
            new_width_mm = width_m * 1000.0
            new_height_mm = _round_up(height_m * 1000.0, settings.rounding_mm)
        else:  # aspect_ratio -- both dimensions are solved
            new_height_mm = _round_up(height_m * 1000.0, settings.rounding_mm)
            new_width_mm = _round_up(aspect_ratio * new_height_mm, settings.rounding_mm)

        new_section = SectionModel(profile=profile, width_mm=new_width_mm, height_mm=new_height_mm)
        area_m2 = area_fn(physics.mm_to_m(new_width_mm), physics.mm_to_m(new_height_mm))
        dh_m = dh_fn(physics.mm_to_m(new_width_mm), physics.mm_to_m(new_height_mm))

    # Report the actual velocity/friction rate at the final (rounded) size
    # -- these can drift slightly from the sizing target once rounding is
    # applied, so recompute them from the real size.
    velocity_ms = physics.velocity_from_flow(flow_m3s, area_m2)
    reynolds = physics.reynolds_number(velocity_ms, dh_m, air.kinematic_viscosity_m2_s)
    friction_factor = physics.friction_factor_altshul_tsal(reynolds, roughness_m / dh_m)
    friction_rate_pa_per_m = physics.darcy_weisbach_pressure_loss(
        friction_factor, 1.0, dh_m, air.density_kg_m3, velocity_ms
    )

    changed = (
        abs(new_section.diameter_mm - old_section.diameter_mm) > 1e-6
        or abs(new_section.width_mm - old_section.width_mm) > 1e-6
        or abs(new_section.height_mm - old_section.height_mm) > 1e-6
    )

    return SegmentSizeResult(
        new_section=new_section, velocity_ms=velocity_ms, reynolds=reynolds,
        friction_rate_pa_per_m=friction_rate_pa_per_m, regain_balanced=regain_balanced,
        changed=changed, **base
    )


# ----------------------------------------------------------------------------
# ConstantVelocity / ConstantFrictionRate -- each segment sized independently
# ----------------------------------------------------------------------------

def _size_independently(network: NetworkModel, components, settings: SizingSettings, method) -> DuctSizingResult:
    result = DuctSizingResult()
    for comp in components:
        for edge_key in comp.edge_keys:
            seg = network.segments[edge_key]
            flow_lps = comp.edge_flow_lps[edge_key]
            try:
                result.segments[edge_key] = _size_one_segment(seg, flow_lps, settings, network.air, method)
            except ValueError as exc:
                result.warnings.append("{}: {}".format(edge_key, exc))
    return result


class ConstantVelocitySizer:
    def size(self, network: NetworkModel, components, settings: SizingSettings) -> DuctSizingResult:
        return _size_independently(network, components, settings, "ConstantVelocity")


class ConstantFrictionRateSizer:
    def size(self, network: NetworkModel, components, settings: SizingSettings) -> DuctSizingResult:
        return _size_independently(network, components, settings, "ConstantFrictionRate")


# ----------------------------------------------------------------------------
# LocalStaticRegainSizer -- today's "StaticRegain" algorithm, unchanged in meaning
# ----------------------------------------------------------------------------

class LocalStaticRegainSizer:
    """
    Parent-to-child sizing by velocity-pressure regain: each section's size
    is picked so its own regain cancels its own straight-duct friction plus
    (for a branch takeoff) the fitting/dynamic loss of the node it takes
    off from -- see the module docstring.
    """

    def size(self, network: NetworkModel, components, settings: SizingSettings) -> DuctSizingResult:
        result = DuctSizingResult()
        for comp in components:
            self._solve_component(network, comp, settings, result)
        return result

    def _solve_component(self, network, comp, settings, result):
        size_tolerance_mm = max(settings.rounding_mm, 0.5)
        fitting_loss_by_edge = {}
        seg_results = {}
        pass_warnings = []
        prev_sizes = None

        for _ in range(_STATIC_REGAIN_MAX_ITERATIONS):
            seg_results, pass_warnings = self._size_pass(network, comp, settings, fitting_loss_by_edge)
            sizes = {edge_key: _size_dims(r) for edge_key, r in seg_results.items()}
            if prev_sizes is not None and _sizes_converged(prev_sizes, sizes, size_tolerance_mm):
                break
            prev_sizes = sizes
            fitting_loss_by_edge = self._junction_fitting_loss_pa(network, comp, seg_results)
        else:
            pass_warnings.append(
                "Static regain sizing with junction fitting losses did not settle after {} passes; "
                "sizes shown are from the last pass.".format(_STATIC_REGAIN_MAX_ITERATIONS)
            )

        result.segments.update(seg_results)
        result.warnings.extend(pass_warnings)

    def _size_pass(self, network, comp, settings, fitting_loss_by_edge):
        """
        One static-regain sizing pass, walking outward from the balancing
        terminal (comp.root_node_id). comp.order always visits a node after
        its parent, so by the time a segment is sized, its upstream
        segment's velocity is already known. The segment(s) coming straight
        off the source have no upstream section to regain from, so they're
        just sized at the settings' target velocity.
        """
        velocity_by_node = {}
        seg_results = {}
        warnings = []

        for node_id in comp.order[1:]:
            edge_key = comp.parent_edge[node_id]
            parent_id = comp.parent_node[node_id]
            seg = network.segments[edge_key]
            flow_lps = comp.edge_flow_lps[edge_key]

            if parent_id == comp.root_node_id:
                method = "ConstantVelocity"
                upstream_vp = 0.0  # unused by _size_one_segment for ConstantVelocity
                fitting_loss_pa = 0.0
            else:
                method = "StaticRegain"
                upstream_vp = physics.velocity_pressure(network.air.density_kg_m3, velocity_by_node[parent_id])
                fitting_loss_pa = fitting_loss_by_edge.get(edge_key, 0.0)

            try:
                sres = _size_one_segment(
                    seg, flow_lps, settings, network.air, method,
                    upstream_velocity_pressure_pa=upstream_vp, fitting_loss_pa=fitting_loss_pa,
                )
                seg_results[edge_key] = sres
                velocity_by_node[node_id] = sres.velocity_ms
                if not sres.regain_balanced:
                    warnings.append(
                        "{}: static regain could not be balanced for this section (clamped to the "
                        "minimum/maximum velocity bracket) -- a balancing damper may be needed "
                        "here.".format(edge_key)
                    )
            except ValueError as exc:
                warnings.append("{}: {}".format(edge_key, exc))
                # Can't resolve this section's own velocity -- seed anything
                # downstream of it with a sensible fallback so the rest of
                # this sub-tree still gets an (less accurate) answer instead
                # of cascading into more failures.
                velocity_by_node[node_id] = velocity_by_node.get(parent_id, settings.target_velocity_ms)

        return seg_results, warnings

    def _junction_fitting_loss_pa(self, network, comp, seg_results):
        """
        Estimate every node's fitting/dynamic loss (Pa), the same way
        pressure.py's Phase E does -- calling each node's own
        loss_evaluator (or the generic K_DEFAULT fallback) -- but from THIS
        PASS'S PROPOSED sizes (seg_results) instead of each segment's
        already-solved velocity, since the real size hasn't been decided
        yet during sizing.

        Returns {edge_key: fitting_loss_pa}, restricted to two things:

        - Only real BRANCH nodes (degree >= 3). Classic static regain
          balances pressure between sibling branches off a common trunk; an
          inline degree-2 fitting (an elbow, a transition, ...) has no
          sibling to balance against, so its own loss is left out of the
          regain target -- only its own straight-duct friction counts there.
        - Only a branch node's own outlet/takeoff legs. A downstream
          terminal device's own loss is excluded too: regain balances
          pressure between successive duct sections, not against a dead-end
          device with nothing further downstream to balance.
        """
        fitting_loss_by_edge = {}

        def provisional_velocity(edge_key):
            sres = seg_results.get(edge_key)
            return sres.velocity_ms if sres is not None else 0.0

        for node_id in comp.node_ids:
            node = network.nodes[node_id]
            if len(node.ports) < 3:
                continue

            primary = node.primary_component
            port_velocities = {}
            for port in node.ports:
                sres = seg_results.get(port.edge_key)
                port_velocities[port.edge_key] = {
                    "velocity_ms": provisional_velocity(port.edge_key),
                    "flow_lps": sres.flow_lps if sres is not None else 0.0,
                    "reynolds": sres.reynolds if sres is not None else 0.0,
                }
            k_result = (
                primary.loss_evaluator(port_velocities)
                if primary is not None and primary.loss_evaluator is not None else None
            )

            if isinstance(k_result, dict):
                for edge_key, k in k_result.items():
                    if k is None:
                        continue
                    v = provisional_velocity(edge_key)
                    fitting_loss_by_edge[edge_key] = (
                        fitting_loss_by_edge.get(edge_key, 0.0) + float(k) * physics.velocity_pressure(network.air.density_kg_m3, v)
                    )
                continue

            # A real fitting (degree >= 2) always has some physical loss --
            # K_DEFAULT fills in when the type has no loss formula of its
            # own (a per-node warning isn't repeated here; pressure.py
            # already reports it when the applied sizes are later calculated).
            k_uniform = K_DEFAULT if k_result is None else float(k_result)

            for port in node.ports:
                if port.flow_into_node:
                    continue  # inlet port -- fitting loss is attributed at outlet ports only
                v = provisional_velocity(port.edge_key)
                fitting_loss_by_edge[port.edge_key] = (
                    fitting_loss_by_edge.get(port.edge_key, 0.0) + k_uniform * physics.velocity_pressure(network.air.density_kg_m3, v)
                )

        return fitting_loss_by_edge
