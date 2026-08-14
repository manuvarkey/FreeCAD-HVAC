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
Pure-Python engineering helpers for duct airflow and pressure-drop
calculation (Darcy-Weisbach friction loss with the explicit Altshul-Tsal
friction factor approximation).

This module has no dependency on FreeCAD and works exclusively in strict SI
units (metres, cubic metres/second, kilograms/cubic metre, square
metres/second, Pascals) so it can be unit-tested in isolation. Unit
conversions from the addon's user-facing units (mm, L/s) happen only at the
call site in the solver.
"""

import math


# ----------------------------------------------------------------------------
# Unit conversion
# ----------------------------------------------------------------------------

def mm_to_m(value_mm):
    """Convert millimetres to metres."""
    return float(value_mm) / 1000.0


def lps_to_m3s(value_lps):
    """Convert litres/second to cubic metres/second."""
    return float(value_lps) / 1000.0


def m3s_to_lps(value_m3s):
    """Convert cubic metres/second to litres/second."""
    return float(value_m3s) * 1000.0


# ----------------------------------------------------------------------------
# Cross-section area
# ----------------------------------------------------------------------------

def circular_area(diameter_m):
    """Cross-section area of a circular duct."""
    d = float(diameter_m)
    if d <= 0.0:
        raise ValueError("diameter_m must be positive")
    return math.pi * (d ** 2) / 4.0


def rectangular_area(width_m, height_m):
    """Cross-section area of a rectangular duct."""
    w = float(width_m)
    h = float(height_m)
    if w <= 0.0 or h <= 0.0:
        raise ValueError("width_m and height_m must be positive")
    return w * h


def oval_area(width_m, height_m):
    """
    Cross-section area of a flat-oval (stadium/obround) duct.

    width_m  = overall width (major axis, along the straight sides)
    height_m = overall height (minor axis == diameter of the end caps)

    Requires width_m >= height_m > 0, matching
    library_api.HVACLibraryAPI.make_oval_wire's convention.
    """
    w = float(width_m)
    h = float(height_m)
    if w <= 0.0 or h <= 0.0:
        raise ValueError("width_m and height_m must be positive")
    if w < h:
        raise ValueError("oval section requires width_m >= height_m")
    r = h / 2.0
    straight = w - h
    # Rectangle formed by the straight sides + full circle formed by the two end caps
    return straight * h + math.pi * (r ** 2)


# ----------------------------------------------------------------------------
# Hydraulic diameter (Dh = 4*Area / WettedPerimeter)
# ----------------------------------------------------------------------------

def hydraulic_diameter_circular(diameter_m):
    """Hydraulic diameter of a circular duct (== its diameter)."""
    d = float(diameter_m)
    if d <= 0.0:
        raise ValueError("diameter_m must be positive")
    return d


def hydraulic_diameter_rectangular(width_m, height_m):
    """Hydraulic diameter of a rectangular duct: 4A/P = 2wh/(w+h)."""
    w = float(width_m)
    h = float(height_m)
    if w <= 0.0 or h <= 0.0:
        raise ValueError("width_m and height_m must be positive")
    return (2.0 * w * h) / (w + h)


def hydraulic_diameter_oval(width_m, height_m):
    """Hydraulic diameter of a flat-oval (stadium/obround) duct: 4A/P."""
    w = float(width_m)
    h = float(height_m)
    if w <= 0.0 or h <= 0.0:
        raise ValueError("width_m and height_m must be positive")
    if w < h:
        raise ValueError("oval section requires width_m >= height_m")
    straight = w - h
    area = oval_area(w, h)
    perimeter = 2.0 * straight + math.pi * h
    return (4.0 * area) / perimeter


# ----------------------------------------------------------------------------
# Flow / velocity / Reynolds number
# ----------------------------------------------------------------------------

def velocity_from_flow(flow_m3_s, area_m2):
    """Mean duct velocity from volumetric flow rate and cross-section area."""
    a = float(area_m2)
    if a <= 0.0:
        raise ValueError("area_m2 must be positive")
    return float(flow_m3_s) / a


def reynolds_number(velocity_m_s, hydraulic_diameter_m, kinematic_viscosity_m2_s):
    """Reynolds number for duct flow."""
    nu = float(kinematic_viscosity_m2_s)
    if nu <= 0.0:
        raise ValueError("kinematic_viscosity_m2_s must be positive")
    return abs(float(velocity_m_s)) * float(hydraulic_diameter_m) / nu


# ----------------------------------------------------------------------------
# Friction factor and pressure loss
# ----------------------------------------------------------------------------

def friction_factor_altshul_tsal(reynolds, relative_roughness):
    """
    Explicit Darcy (Moody) friction factor approximation (Altshul-Tsal),
    avoiding an iterative Colebrook-White solve.

    relative_roughness = absolute_roughness_m / hydraulic_diameter_m
    """
    re = float(reynolds)
    eps_rel = float(relative_roughness)
    if re <= 0.0:
        raise ValueError("reynolds must be positive")
    if eps_rel < 0.0:
        raise ValueError("relative_roughness must be non-negative")

    f = 0.11 * (eps_rel + 68.0 / re) ** 0.25
    if f < 0.018:
        f = 0.85 * f + 0.0028
    return f


def velocity_pressure(air_density_kg_m3, velocity_m_s):
    """Dynamic (velocity) pressure: rho * V^2 / 2."""
    return float(air_density_kg_m3) * (float(velocity_m_s) ** 2) / 2.0


def darcy_weisbach_pressure_loss(friction_factor, length_m, hydraulic_diameter_m,
                                  air_density_kg_m3, velocity_m_s):
    """Straight-duct friction pressure loss via the Darcy-Weisbach equation."""
    dh = float(hydraulic_diameter_m)
    if dh <= 0.0:
        raise ValueError("hydraulic_diameter_m must be positive")
    length = float(length_m)
    if length < 0.0:
        raise ValueError("length_m must be non-negative")
    return float(friction_factor) * (length / dh) * velocity_pressure(air_density_kg_m3, velocity_m_s)


# ----------------------------------------------------------------------------
# Duct sizing: constant velocity (direct/closed-form)
#
# Solve duct dimensions from a required flow rate and a target velocity.
# Rectangular/oval ducts have two dimensions but sizing only fixes one
# number (area), so a "mode" picks how the second dimension is determined:
#   "aspect_ratio": width/height held at a fixed ratio, both solved from area
#   "fixed_height": height held fixed, width solved from area
#   "fixed_width":  width held fixed, height solved from area
# ----------------------------------------------------------------------------

_OVAL_SHAPE_FACTOR = 1.0 - math.pi / 4.0  # area(w,h) = h^2*(w/h - 1 + pi/4) for an oval


def circular_diameter_for_velocity(flow_m3_s, velocity_m_s):
    """Circular duct diameter that gives exactly the target velocity for the given flow."""
    if velocity_m_s <= 0.0:
        raise ValueError("velocity_m_s must be positive")
    area = float(flow_m3_s) / float(velocity_m_s)
    return 2.0 * math.sqrt(area / math.pi)


def rect_dims_for_velocity(flow_m3_s, velocity_m_s, mode, aspect_ratio=None, fixed_dim_m=None):
    """Rectangular duct (width_m, height_m) that gives exactly the target velocity."""
    if velocity_m_s <= 0.0:
        raise ValueError("velocity_m_s must be positive")
    area = float(flow_m3_s) / float(velocity_m_s)

    if mode == "aspect_ratio":
        if not aspect_ratio or aspect_ratio <= 0.0:
            raise ValueError("aspect_ratio must be positive")
        height = math.sqrt(area / aspect_ratio)
        return aspect_ratio * height, height
    if mode == "fixed_height":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        return area / fixed_dim_m, fixed_dim_m
    if mode == "fixed_width":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        return fixed_dim_m, area / fixed_dim_m
    raise ValueError("Unknown mode: {!r}".format(mode))


def oval_dims_for_velocity(flow_m3_s, velocity_m_s, mode, aspect_ratio=None, fixed_dim_m=None):
    """
    Flat-oval duct (width_m, height_m) that gives exactly the target
    velocity. Unlike a rectangle, oval area = (width-height)*height +
    pi*(height/2)^2 isn't a simple product, so each mode has its own
    closed-form inverse (derived from that area formula; all three exist in
    closed form, no iteration needed):
      aspect_ratio r=width/height (r>=1): area = height^2*(r-1+pi/4)
      fixed height h:  width = h + area/h - pi*h/4
      fixed width w:   height = smaller root of (1-pi/4)*h^2 - w*h + area = 0
    """
    if velocity_m_s <= 0.0:
        raise ValueError("velocity_m_s must be positive")
    area = float(flow_m3_s) / float(velocity_m_s)

    if mode == "aspect_ratio":
        if not aspect_ratio or aspect_ratio < 1.0:
            raise ValueError("aspect_ratio must be >= 1.0 for an oval (width >= height)")
        height = math.sqrt(area / (aspect_ratio - 1.0 + math.pi / 4.0))
        return aspect_ratio * height, height
    if mode == "fixed_height":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        height = fixed_dim_m
        width = height + area / height - math.pi * height / 4.0
        return width, height
    if mode == "fixed_width":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        width = fixed_dim_m
        discriminant = width ** 2 - 4.0 * _OVAL_SHAPE_FACTOR * area
        if discriminant < 0.0:
            raise ValueError("No valid oval height for the given width and flow/velocity")
        height = (width - math.sqrt(discriminant)) / (2.0 * _OVAL_SHAPE_FACTOR)
        return width, height
    raise ValueError("Unknown mode: {!r}".format(mode))


# ----------------------------------------------------------------------------
# Duct sizing: constant friction rate (bisection)
#
# The Darcy-Weisbach friction rate is an implicit function of duct size (size
# affects area, hydraulic diameter, Reynolds number, and relative roughness
# all at once), so unlike the constant-velocity case there is no closed-form
# inverse. Solved instead by bisecting on a single free "scale" parameter --
# friction rate decreases monotonically as duct size increases for a fixed
# flow rate, so the bracket is well-posed.
# ----------------------------------------------------------------------------

def _friction_rate_pa_per_m(area_m2, hydraulic_diameter_m, flow_m3_s,
                             roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3):
    velocity_m_s = velocity_from_flow(flow_m3_s, area_m2)
    reynolds = reynolds_number(velocity_m_s, hydraulic_diameter_m, kinematic_viscosity_m2_s)
    friction_factor = friction_factor_altshul_tsal(reynolds, roughness_m / hydraulic_diameter_m)
    return darcy_weisbach_pressure_loss(friction_factor, 1.0, hydraulic_diameter_m, air_density_kg_m3, velocity_m_s)


def _solve_scale_for_friction_rate(area_and_dh_fn, flow_m3_s, target_rate_pa_per_m,
                                    roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
                                    lo=0.01, hi=5.0, iterations=60):
    """
    Bisect a single scale parameter (e.g. diameter, or one duct dimension)
    so that _friction_rate_pa_per_m(*area_and_dh_fn(scale), ...) equals
    target_rate_pa_per_m. area_and_dh_fn(scale) -> (area_m2, hydraulic_diameter_m).

    Clamps to [lo, hi] if the target can't be reached within that bracket
    (e.g. an unrealistically high/low friction rate target) rather than
    raising -- callers can compare the result's actual resulting rate against
    the target if they need to detect this.
    """
    def rate_at(scale):
        area_m2, dh_m = area_and_dh_fn(scale)
        return _friction_rate_pa_per_m(area_m2, dh_m, flow_m3_s, roughness_m,
                                        kinematic_viscosity_m2_s, air_density_kg_m3)

    if rate_at(lo) <= target_rate_pa_per_m:
        return lo
    if rate_at(hi) > target_rate_pa_per_m:
        return hi

    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        if rate_at(mid) > target_rate_pa_per_m:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def circular_diameter_for_friction_rate(flow_m3_s, target_rate_pa_per_m, roughness_m,
                                         kinematic_viscosity_m2_s, air_density_kg_m3):
    """Circular duct diameter that gives (approximately) the target friction rate (Pa/m)."""
    def area_and_dh(diameter_m):
        return circular_area(diameter_m), hydraulic_diameter_circular(diameter_m)

    return _solve_scale_for_friction_rate(
        area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3
    )


def rect_dims_for_friction_rate(flow_m3_s, target_rate_pa_per_m, roughness_m,
                                 kinematic_viscosity_m2_s, air_density_kg_m3,
                                 mode, aspect_ratio=None, fixed_dim_m=None):
    """Rectangular duct (width_m, height_m) that gives (approximately) the target friction rate."""
    if mode == "aspect_ratio":
        if not aspect_ratio or aspect_ratio <= 0.0:
            raise ValueError("aspect_ratio must be positive")

        def area_and_dh(height_m):
            width_m = aspect_ratio * height_m
            return rectangular_area(width_m, height_m), hydraulic_diameter_rectangular(width_m, height_m)

        height = _solve_scale_for_friction_rate(
            area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3
        )
        return aspect_ratio * height, height

    if mode == "fixed_height":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        height = fixed_dim_m

        def area_and_dh(width_m):
            return rectangular_area(width_m, height), hydraulic_diameter_rectangular(width_m, height)

        width = _solve_scale_for_friction_rate(
            area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3
        )
        return width, height

    if mode == "fixed_width":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        width = fixed_dim_m

        def area_and_dh(height_m):
            return rectangular_area(width, height_m), hydraulic_diameter_rectangular(width, height_m)

        height = _solve_scale_for_friction_rate(
            area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3
        )
        return width, height

    raise ValueError("Unknown mode: {!r}".format(mode))


def oval_dims_for_friction_rate(flow_m3_s, target_rate_pa_per_m, roughness_m,
                                 kinematic_viscosity_m2_s, air_density_kg_m3,
                                 mode, aspect_ratio=None, fixed_dim_m=None):
    """Flat-oval duct (width_m, height_m) that gives (approximately) the target friction rate."""
    if mode == "aspect_ratio":
        if not aspect_ratio or aspect_ratio < 1.0:
            raise ValueError("aspect_ratio must be >= 1.0 for an oval (width >= height)")

        def area_and_dh(height_m):
            width_m = aspect_ratio * height_m
            return oval_area(width_m, height_m), hydraulic_diameter_oval(width_m, height_m)

        height = _solve_scale_for_friction_rate(
            area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3
        )
        return aspect_ratio * height, height

    if mode == "fixed_height":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        height = fixed_dim_m

        def area_and_dh(width_m):
            return oval_area(width_m, height), hydraulic_diameter_oval(width_m, height)

        width = _solve_scale_for_friction_rate(
            area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
            lo=height,  # width must be >= height for a valid oval
        )
        return width, height

    if mode == "fixed_width":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        width = fixed_dim_m

        def area_and_dh(height_m):
            return oval_area(width, height_m), hydraulic_diameter_oval(width, height_m)

        height = _solve_scale_for_friction_rate(
            area_and_dh, flow_m3_s, target_rate_pa_per_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
            hi=width,  # height must be <= width for a valid oval
        )
        return width, height

    raise ValueError("Unknown mode: {!r}".format(mode))


# ----------------------------------------------------------------------------
# Duct sizing: static regain (bisection)
#
# Sizes each duct section so the static-pressure regain from slowing down
# through that section -- regain_factor * (upstream velocity pressure - this
# section's velocity pressure) -- exactly offsets this section's own friction
# loss. Unlike constant velocity/friction rate, this needs the UPSTREAM
# section's already-solved velocity pressure as an input, so sections must be
# sized in order from the source outward (see core.DuctSizer).
#
# Static regain has a well-known failure mode on small/low-velocity branches:
# regain can't offset any reasonable friction loss without an impractically
# large (slow) duct. Every function here takes a min_velocity_m_s floor and
# never proposes anything slower than that -- the search brackets from the
# floor diameter/dimension upward, so if even the floor's own regain already
# exceeds its friction, the floor is returned outright.
# ----------------------------------------------------------------------------

def _regain_minus_friction(area_and_dh_fn, scale, flow_m3_s, upstream_velocity_pressure_pa,
                            regain_factor, length_m, roughness_m, kinematic_viscosity_m2_s,
                            air_density_kg_m3):
    area_m2, dh_m = area_and_dh_fn(scale)
    velocity_m_s = velocity_from_flow(flow_m3_s, area_m2)
    vp = velocity_pressure(air_density_kg_m3, velocity_m_s)
    reynolds = reynolds_number(velocity_m_s, dh_m, kinematic_viscosity_m2_s)
    friction_factor = friction_factor_altshul_tsal(reynolds, roughness_m / dh_m)
    friction = darcy_weisbach_pressure_loss(friction_factor, length_m, dh_m, air_density_kg_m3, velocity_m_s)
    regain = regain_factor * (upstream_velocity_pressure_pa - vp)
    return regain - friction


def _solve_scale_for_static_regain(area_and_dh_fn, flow_m3_s, upstream_velocity_pressure_pa,
                                    regain_factor, length_m, roughness_m, kinematic_viscosity_m2_s,
                                    air_density_kg_m3, lo, hi=5.0, iterations=60):
    """
    Bisect a single scale parameter (e.g. diameter, or one duct dimension) so
    that regain_minus_friction(scale) == 0. That difference increases
    monotonically with scale (a bigger duct means both less friction and,
    since it's slower, more regain), so the bracket is well-posed.

    lo is the scale at the minimum allowed velocity (a floor) -- see the
    module note above. Clamps to hi if the balance can't be reached even at
    the largest bracketed size (e.g. a very long or rough run).
    """
    if lo >= hi:
        return lo

    def balance(scale):
        return _regain_minus_friction(
            area_and_dh_fn, scale, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3
        )

    if balance(lo) >= 0.0:
        return lo
    if balance(hi) < 0.0:
        return hi

    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        if balance(mid) < 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def circular_diameter_for_static_regain(flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
                                         length_m, roughness_m, kinematic_viscosity_m2_s,
                                         air_density_kg_m3, min_velocity_m_s):
    def area_and_dh(diameter_m):
        return circular_area(diameter_m), hydraulic_diameter_circular(diameter_m)

    floor_diameter_m = circular_diameter_for_velocity(flow_m3_s, min_velocity_m_s)
    return _solve_scale_for_static_regain(
        area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
        length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3, lo=floor_diameter_m
    )


def rect_dims_for_static_regain(flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
                                 length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
                                 min_velocity_m_s, mode, aspect_ratio=None, fixed_dim_m=None):
    floor_w, floor_h = rect_dims_for_velocity(
        flow_m3_s, min_velocity_m_s, mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
    )

    if mode == "aspect_ratio":
        if not aspect_ratio or aspect_ratio <= 0.0:
            raise ValueError("aspect_ratio must be positive")

        def area_and_dh(height_m):
            width_m = aspect_ratio * height_m
            return rectangular_area(width_m, height_m), hydraulic_diameter_rectangular(width_m, height_m)

        height = _solve_scale_for_static_regain(
            area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3, lo=floor_h
        )
        return aspect_ratio * height, height

    if mode == "fixed_height":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        height = fixed_dim_m

        def area_and_dh(width_m):
            return rectangular_area(width_m, height), hydraulic_diameter_rectangular(width_m, height)

        width = _solve_scale_for_static_regain(
            area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3, lo=floor_w
        )
        return width, height

    if mode == "fixed_width":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        width = fixed_dim_m

        def area_and_dh(height_m):
            return rectangular_area(width, height_m), hydraulic_diameter_rectangular(width, height_m)

        height = _solve_scale_for_static_regain(
            area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3, lo=floor_h
        )
        return width, height

    raise ValueError("Unknown mode: {!r}".format(mode))


def oval_dims_for_static_regain(flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
                                 length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
                                 min_velocity_m_s, mode, aspect_ratio=None, fixed_dim_m=None):
    floor_w, floor_h = oval_dims_for_velocity(
        flow_m3_s, min_velocity_m_s, mode, aspect_ratio=aspect_ratio, fixed_dim_m=fixed_dim_m
    )

    if mode == "aspect_ratio":
        if not aspect_ratio or aspect_ratio < 1.0:
            raise ValueError("aspect_ratio must be >= 1.0 for an oval (width >= height)")

        def area_and_dh(height_m):
            width_m = aspect_ratio * height_m
            return oval_area(width_m, height_m), hydraulic_diameter_oval(width_m, height_m)

        height = _solve_scale_for_static_regain(
            area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3, lo=floor_h
        )
        return aspect_ratio * height, height

    if mode == "fixed_height":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        height = fixed_dim_m

        def area_and_dh(width_m):
            return oval_area(width_m, height), hydraulic_diameter_oval(width_m, height)

        width = _solve_scale_for_static_regain(
            area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
            lo=max(floor_w, height),  # width must also stay >= height for a valid oval
        )
        return width, height

    if mode == "fixed_width":
        if not fixed_dim_m or fixed_dim_m <= 0.0:
            raise ValueError("fixed_dim_m must be positive")
        width = fixed_dim_m

        def area_and_dh(height_m):
            return oval_area(width, height_m), hydraulic_diameter_oval(width, height_m)

        height = _solve_scale_for_static_regain(
            area_and_dh, flow_m3_s, upstream_velocity_pressure_pa, regain_factor,
            length_m, roughness_m, kinematic_viscosity_m2_s, air_density_kg_m3,
            lo=floor_h, hi=width,  # height must also stay <= width for a valid oval
        )
        return width, height

    raise ValueError("Unknown mode: {!r}".format(mode))
