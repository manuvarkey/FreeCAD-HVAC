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
Works out proposed duct sizes for a whole network. Uses the same flow
distribution as AirflowSolver (see FlowNetwork.py), but instead of computing
pressure drop for an existing size, it computes the size itself -- from a
target constant velocity, constant friction rate, or static regain, chosen
by the active DuctNetwork's SizingMethod.

ConstantVelocity and ConstantFrictionRate size each segment on its own, in
any order. StaticRegain can't: each section's size depends on its parent
section's already-solved velocity, so segments are sized one at a time,
walking the tree from the source outward (see FlowComponent.order/
parent_edge -- the same walk AirflowSolver uses to propagate pressure). The
first section(s) off the source have no upstream velocity to work from, so
they're seeded with the network's TargetVelocity instead.

A segment's own Velocity/RectangularSizingMode/TargetAspectRatio override
(see _size_segment) still applies during a StaticRegain solve -- an
overridden segment is just sized at that fixed velocity, and its result
becomes the seed for whatever comes after it.

Nothing here touches the real segment objects: solve() only computes and
returns proposed sizes (a preview). apply(result) is a separate step that
writes the proposed Diameter/Width/Height onto the real objects -- the Size
Ducts command shows the preview first and lets the user confirm before
calling apply().

StaticRegain also needs to weigh each section's regain against the fitting/
dynamic loss of the junction it takes off from (e.g. a tee's branch loss),
not just its own straight-duct friction -- otherwise it's balancing against
less than the section actually has to overcome. That loss depends on the
very duct sizes being solved for, so it's resolved by iterating: size the
component once (first pass has no fitting-loss estimate yet, same as a
plain regain-only solve), estimate every junction's loss from those
proposed sizes (reusing the same per-type loss_module/loss_function
AirflowSolver.py calls, via a provisional connected_ports context built
from the proposed sizes instead of the segments' live properties), re-size
using that estimate, and repeat until sizes settle -- see
_solveComponentStaticRegain.
"""

import math
from dataclasses import asdict, dataclass, field

from ..utils import hvaclib
from . import airflow
from .AirflowSolver import K_DEFAULT
from .FlowNetwork import solve_flow_components


# Fixed-point iteration cap for StaticRegain's fitting-loss estimate (see
# _solveComponentStaticRegain) -- sizes normally settle in 2-3 passes; this
# is a safety bound, not a tuned expectation.
_STATIC_REGAIN_MAX_ITERATIONS = 5


_RECT_MODE_MAP = {
    "FixedAspectRatio": "aspect_ratio",
    "FixedHeight": "fixed_height",
    "FixedWidth": "fixed_width",
}


@dataclass
class SegmentSizeResult:
    """Proposed new size for one segment, plus its old size for comparison."""
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
    reynolds: float = 0.0
    friction_rate_pa_per_m: float = 0.0
    regain_balanced: bool = True  # only meaningful for SizingMethod=StaticRegain; see module docstring
    changed: bool = False


@dataclass
class DuctSizingResult:
    """Whole-network sizing result: one SegmentSizeResult per segment, plus any warnings."""
    segments: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _round_up(value_mm, increment_mm):
    """Round up to the nearest multiple of increment_mm (no-op if increment_mm <= 0)."""
    if increment_mm <= 0.0:
        return value_mm
    return math.ceil(value_mm / increment_mm - 1e-9) * increment_mm


def _section_params_from_result(sres):
    """A port-style section_params dict (mm) for this segment's proposed size, from its SegmentSizeResult."""
    if sres.profile == "Circular":
        return {"Diameter": sres.new_diameter_mm}
    return {"Width": sres.new_width_mm, "Height": sres.new_height_mm}


def _sizes_converged(prev_sizes, sizes, tolerance_mm):
    """True if every segment's (diameter, width, height) moved by no more than tolerance_mm since the last pass."""
    if set(prev_sizes) != set(sizes):
        return False
    for tag, new_dims in sizes.items():
        old_dims = prev_sizes[tag]
        if any(abs(a - b) > tolerance_mm for a, b in zip(old_dims, new_dims)):
            return False
    return True


class DuctSizer:
    """Computes proposed duct dimensions for a DuctNetwork's segments (preview-only; see apply())."""

    def __init__(self, net_obj):
        self.net_obj = net_obj

    def solve(self):
        net = self.net_obj
        # Same flow-distribution step AirflowSolver uses: which segments carry
        # how much flow, and how they group into independently-solvable trees.
        parser, junction_map, segment_map, components, warnings = solve_flow_components(net)

        result = DuctSizingResult(warnings=list(warnings))

        # Read the network's sizing settings once, up front.
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

        # Each component is an independently-solvable tree. StaticRegain needs
        # its own ordered walk (see _solveComponentStaticRegain); the other
        # two methods just size every segment in the component directly.
        for comp in components:
            if method == "StaticRegain":
                self._solveComponentStaticRegain(
                    parser, comp, segment_map, junction_map, result, mode, aspect_ratio, target_velocity,
                    rounding_mm, viscosity, density, default_roughness_mm, default_width_mm, default_height_mm,
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

    def _solveComponentStaticRegain(self, parser, comp, segment_map, junction_map, result, mode, aspect_ratio,
                                     target_velocity, rounding_mm, viscosity, density,
                                     default_roughness_mm, default_width_mm, default_height_mm,
                                     regain_factor, min_velocity):
        """
        Size every segment in this component, iterating to account for each
        takeoff junction's own fitting/dynamic loss (see the module
        docstring for why). Pass 1 has no fitting-loss estimate yet (0 Pa
        everywhere, i.e. a plain regain-vs-friction solve); each pass after
        that estimates every junction's loss from the previous pass's
        proposed sizes and re-sizes against it. Stops once sizes settle to
        within a rounding step (or _STATIC_REGAIN_MAX_ITERATIONS passes, in
        which case a warning is added and the last pass's sizes are used --
        this is a fixed-point iteration, not a guaranteed-convergent solve).
        """
        size_tolerance_mm = max(rounding_mm, 0.5)
        fitting_loss_by_edge = {}
        seg_results_by_edge = {}
        pass_warnings = []
        prev_sizes = None

        for _ in range(_STATIC_REGAIN_MAX_ITERATIONS):
            seg_results_by_edge, pass_warnings = self._sizeComponentStaticRegainPass(
                comp, segment_map, mode, aspect_ratio, target_velocity, rounding_mm,
                viscosity, density, default_roughness_mm, default_width_mm, default_height_mm,
                regain_factor, min_velocity, fitting_loss_by_edge,
            )
            sizes = {
                tag: (r.new_diameter_mm, r.new_width_mm, r.new_height_mm)
                for tag, r in seg_results_by_edge.items()
            }
            if prev_sizes is not None and _sizes_converged(prev_sizes, sizes, size_tolerance_mm):
                break
            prev_sizes = sizes
            fitting_loss_by_edge = self._junctionFittingLossPa(
                parser, comp, junction_map, seg_results_by_edge, density, viscosity
            )
        else:
            pass_warnings.append(
                "Static regain sizing with junction fitting losses did not settle after {} passes; "
                "sizes shown are from the last pass.".format(_STATIC_REGAIN_MAX_ITERATIONS)
            )

        result.segments.extend(seg_results_by_edge.values())
        result.warnings.extend(pass_warnings)

    def _sizeComponentStaticRegainPass(self, comp, segment_map, mode, aspect_ratio,
                                        target_velocity, rounding_mm, viscosity, density,
                                        default_roughness_mm, default_width_mm, default_height_mm,
                                        regain_factor, min_velocity, fitting_loss_by_edge):
        """
        One static-regain sizing pass, walking outward from the balancing
        terminal (comp.root_node_id). comp.order always visits a node after
        its parent, so by the time a segment is sized, its upstream
        segment's velocity is already known.

        The segment(s) coming straight off the source have no upstream
        section to regain from, so they're just sized at the network's
        TargetVelocity (standard starting point) -- fitting_loss_by_edge
        doesn't apply to them either. Every other segment is sized by the
        regain-balance equation against its own parent's already-solved
        velocity, plus fitting_loss_by_edge's estimate (if any yet) of the
        loss at the junction it takes off from.

        Returns ({edge_tag: SegmentSizeResult}, [warning, ...]).
        """
        velocity_by_node = {}  # each node's resolved velocity, filled in as we go
        seg_results_by_edge = {}
        warnings = []

        for node_id in comp.order[1:]:
            edge_ref = comp.parent_edge[node_id]
            parent_id = comp.parent_node[node_id]
            seg_obj = segment_map[edge_ref.tag]
            flow_lps = comp.edge_flow_lps[edge_ref]

            if parent_id == comp.root_node_id:
                # First segment off the source: no upstream velocity to regain from.
                effective_method = "ConstantVelocity"
                upstream_vp = 0.0  # unused by _size_segment for ConstantVelocity
                fitting_loss_pa = 0.0
            else:
                effective_method = "StaticRegain"
                upstream_vp = airflow.velocity_pressure(density, velocity_by_node[parent_id])
                fitting_loss_pa = fitting_loss_by_edge.get(edge_ref.tag, 0.0)

            try:
                sres = self._size_segment(
                    seg_obj, flow_lps, effective_method, mode, aspect_ratio,
                    target_velocity, 0.0, rounding_mm,
                    viscosity, density, default_roughness_mm,
                    default_width_mm, default_height_mm,
                    upstream_velocity_pressure_pa=upstream_vp,
                    regain_factor=regain_factor, min_velocity=min_velocity,
                    fitting_loss_pa=fitting_loss_pa,
                )
                seg_results_by_edge[edge_ref.tag] = sres
                velocity_by_node[node_id] = sres.velocity_ms
                if not sres.regain_balanced:
                    warnings.append(
                        "{}: static regain could not be balanced for this section (clamped to the "
                        "minimum/maximum velocity bracket) -- a balancing damper may be needed "
                        "here.".format(seg_obj.Label)
                    )
            except ValueError as exc:
                warnings.append("{}: {}".format(seg_obj.Label, exc))
                # Can't resolve this section's own velocity -- seed anything
                # downstream of it with a sensible fallback (the upstream
                # velocity, or the network's TargetVelocity at the source) so
                # the rest of this sub-tree still gets an (less accurate)
                # answer instead of cascading into more failures.
                velocity_by_node[node_id] = velocity_by_node.get(parent_id, target_velocity)

        return seg_results_by_edge, warnings

    def _junctionFittingLossPa(self, parser, comp, junction_map, seg_results_by_edge, density, viscosity):
        """
        Estimate every junction's fitting/dynamic loss (Pa), the same way
        AirflowSolver.py's Phase E does -- calling each junction's own
        loss_module/loss_function (or the generic K_DEFAULT fallback) --
        but from THIS PASS'S PROPOSED sizes (seg_results_by_edge) instead of
        each segment's live FreeCAD property, since the real size hasn't
        been written back yet during sizing.

        Returns {edge_tag: fitting_loss_pa}, restricted to two things:

        - Only real BRANCH junctions (degree >= 3). Classic static regain
          balances pressure between sibling branches off a common trunk; an
          inline degree-2 fitting (an elbow, a transition, ...) has no
          sibling to balance against, so its own loss is left out of the
          regain target, same as it always has been -- only its own
          straight-duct friction counts there.
        - Only a branch junction's own outlet/takeoff legs. A downstream
          terminal device's own loss (e.g. a diffuser's neck loss) is
          excluded too: regain balances pressure between successive duct
          sections, not against a dead-end device with nothing further
          downstream to balance.
        """
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        fitting_loss_by_edge = {}

        def provisional_velocity(edge_key):
            sres = seg_results_by_edge.get(edge_key)
            return sres.velocity_ms if sres is not None else 0.0

        for node_id in comp.comp_nodes:
            if comp.graph.degree[node_id] < 3:
                continue

            junction_obj = junction_map[parser.node_key(node_id)]
            ja = comp.analysis_by_node[node_id]

            library_id = getattr(junction_obj, "LibraryId", "")
            type_id = getattr(junction_obj, "TypeId", "")
            type_def = reg.resolve_type(library_id, type_id) if library_id and type_id else None

            properties = {}
            if type_def is not None:
                for pdef in getattr(type_def, "properties", []) or []:
                    if hasattr(junction_obj, pdef.name):
                        properties[pdef.name] = getattr(junction_obj, pdef.name)
                    else:
                        properties[pdef.name] = getattr(pdef, "default", None)

            connected_ports_ctx = []
            for port in ja.connected_ports:
                port_dict = asdict(port)
                sres = seg_results_by_edge.get(port.edge_key)
                if sres is not None:
                    port_dict["velocity_ms"] = sres.velocity_ms
                    port_dict["reynolds"] = sres.reynolds
                    port_dict["flow_rate_lps"] = sres.flow_lps
                    port_dict["section_params"] = _section_params_from_result(sres)
                connected_ports_ctx.append(port_dict)

            context = {
                "obj": junction_obj,
                "center_point": ja.point,
                "properties": properties,
                "connected_ports": connected_ports_ctx,
                "family": getattr(junction_obj, "Family", ""),
                "type_id": type_id,
                "library_id": library_id,
                "air_density": density,
                "air_kinematic_viscosity": viscosity,
            }

            k_result = reg.call_loss(library_id, type_def, context) if type_def is not None else None

            if isinstance(k_result, dict):
                for edge_key, k in k_result.items():
                    if k is None:
                        continue
                    v = provisional_velocity(edge_key)
                    fitting_loss_by_edge[edge_key] = (
                        fitting_loss_by_edge.get(edge_key, 0.0) + float(k) * airflow.velocity_pressure(density, v)
                    )
                continue

            # A real fitting (degree >= 2) always has some physical loss --
            # K_DEFAULT fills in when the type has no loss formula of its
            # own, same as AirflowSolver.py (a per-junction warning isn't
            # repeated here; AirflowSolver already reports it when the
            # applied sizes are later calculated).
            k_uniform = K_DEFAULT if k_result is None else float(k_result)

            for port in ja.connected_ports:
                if port.flow_into_junction:
                    continue  # inlet port -- fitting loss is attributed at outlet ports only
                v = provisional_velocity(port.edge_key)
                fitting_loss_by_edge[port.edge_key] = (
                    fitting_loss_by_edge.get(port.edge_key, 0.0) + k_uniform * airflow.velocity_pressure(density, v)
                )

        return fitting_loss_by_edge

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
                       upstream_velocity_pressure_pa=0.0, regain_factor=0.75, min_velocity=2.5,
                       fitting_loss_pa=0.0):
        # Step 1: read the segment's current size and profile (used for the
        # "before" side of the result, and as a fallback for fixed dimensions).
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

        # Step 2: apply this segment's own overrides, if any, on top of the
        # network-wide method/mode/aspect-ratio passed in.
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

        # Step 3: for a rectangular/oval duct sized with one dimension held
        # fixed, work out what that fixed dimension actually is (its current
        # value, falling back to the network default).
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

        # Step 4: solve the actual size, using whichever airflow.py formula
        # matches this segment's profile and sizing method.
        if profile == "Circular":
            if method == "ConstantVelocity":
                diameter_m = airflow.circular_diameter_for_velocity(flow_m3s, target_velocity)
            elif method == "StaticRegain":
                diameter_m, regain_balanced = airflow.circular_diameter_for_static_regain(
                    flow_m3s, upstream_velocity_pressure_pa, regain_factor, length_m,
                    roughness_m, viscosity, density, min_velocity, fitting_loss_pa=fitting_loss_pa,
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
                    mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m, fitting_loss_pa=fitting_loss_pa,
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

        # Step 5: report the actual velocity/friction rate at the final
        # (rounded) size -- these can drift slightly from the sizing target
        # once rounding is applied, so recompute them from the real size.
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
            velocity_ms=velocity_ms, reynolds=reynolds, friction_rate_pa_per_m=friction_rate_pa_per_m,
            regain_balanced=regain_balanced, changed=changed, **base_result
        )
