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
