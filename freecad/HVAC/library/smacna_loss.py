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
Digitized SMACNA / ASHRAE Fundamentals (Ch. 21, "Duct Design") duct-fitting
local-loss-coefficient (zeta) tables, with simple clamped linear/bilinear
interpolation instead of numpy/scipy so this module has no dependency beyond
the standard library.

Table values were cross-checked against the open-source reference
implementation at https://github.com/TomLXXVI/python-hvac
(hvac/fluid_flow/fittings/duct/smacna.py), which digitizes the same ASHRAE
Duct Fitting Database tables (fitting codes A7x/A8x/A9x/A10x/A11x). They have
not been re-verified against a purchased primary SMACNA/ASHRAE source
document -- spot-check against a primary reference before relying on this for
critical fan/AHU sizing decisions.

Every function here is a pure function of plain floats (no FreeCAD, no unit
objects) and returns a dimensionless loss coefficient already referenced to
the OUTLET (downstream, for diverging fittings) or the relevant leg's own
velocity pressure, ready to multiply directly by that leg's velocity_pressure
-- see core.airflow.velocity_pressure. Where the source table is naturally
referenced to a different duct than the one we need, the conversion
(zeta2 = zeta1 * Pv1/Pv2 = zeta1 / (V1/V2)**2, since Pv ~ V**2 at constant
density) is applied internally.

Round-duct branch (tee/wye) tables are also used as an approximation for
rectangular/oval branch fittings: SMACNA's rectangular-specific tables (e.g.
A10D, A10H, A11N) are keyed to a handful of exact, discrete manufactured
area-ratio combinations rather than a smooth function of geometry, and don't
generalize to the continuously-parametric ducts this addon models. Velocity-
and area-ratio-driven branch losses transfer reasonably well across duct
shapes, which is why this approximation is commonly used in practice when a
shape-specific table isn't available -- but it is an approximation, not a
literal SMACNA rectangular-fitting lookup.
"""


# ----------------------------------------------------------------------------
# Interpolation helpers
# ----------------------------------------------------------------------------

def _interp1d(x, xs, ys):
    """Linear interpolation, clamped to the table's range. Handles xs given in either order."""
    pairs = sorted(zip(xs, ys))
    xs_sorted = [p[0] for p in pairs]
    ys_sorted = [p[1] for p in pairs]

    if x <= xs_sorted[0]:
        return ys_sorted[0]
    if x >= xs_sorted[-1]:
        return ys_sorted[-1]

    for i in range(len(xs_sorted) - 1):
        x0, x1 = xs_sorted[i], xs_sorted[i + 1]
        if x0 <= x <= x1:
            y0, y1 = ys_sorted[i], ys_sorted[i + 1]
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)

    return ys_sorted[-1]


def _interp2d(x, y, xs, ys, table):
    """
    Bilinear interpolation, clamped to the table's range on both axes.

    table[iy][ix] is the value at (xs[ix], ys[iy]). xs/ys may be given in
    either ascending or descending order.
    """
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    ys_sorted = [ys[i] for i in order]

    if y <= ys_sorted[0]:
        return _interp1d(x, xs, table[order[0]])
    if y >= ys_sorted[-1]:
        return _interp1d(x, xs, table[order[-1]])

    for k in range(len(ys_sorted) - 1):
        y0, y1 = ys_sorted[k], ys_sorted[k + 1]
        if y0 <= y <= y1:
            z0 = _interp1d(x, xs, table[order[k]])
            z1 = _interp1d(x, xs, table[order[k + 1]])
            if y1 == y0:
                return z0
            t = (y - y0) / (y1 - y0)
            return z0 + t * (z1 - z0)

    return _interp1d(x, xs, table[order[-1]])


# ----------------------------------------------------------------------------
# Elbows (SMACNA A7A / A7F)
# ----------------------------------------------------------------------------

_ELBOW_ROUND_R_ON_D = [0.5, 0.75, 1.0, 1.5, 2.0, 2.5]
_ELBOW_ROUND_ZETA = [0.71, 0.33, 0.22, 0.15, 0.13, 0.12]


def elbow_zeta_round(r_on_d):
    """90 deg smooth-radius round elbow. r_on_d = centerline radius / diameter. SMACNA A7A."""
    return _interp1d(r_on_d, _ELBOW_ROUND_R_ON_D, _ELBOW_ROUND_ZETA)


_ELBOW_RECT_H_ON_W = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0]
_ELBOW_RECT_R_ON_W = [0.5, 0.75, 1.0, 1.5, 2.0]
_ELBOW_RECT_ZETA = [
    [1.5, 1.4, 1.3, 1.2, 1.1, 1.0, 1.0, 1.1, 1.1, 1.2, 1.2],
    [0.57, 0.52, 0.48, 0.44, 0.40, 0.39, 0.39, 0.40, 0.42, 0.43, 0.44],
    [0.27, 0.25, 0.23, 0.21, 0.19, 0.18, 0.18, 0.19, 0.20, 0.27, 0.21],
    [0.22, 0.20, 0.19, 0.17, 0.15, 0.14, 0.14, 0.15, 0.16, 0.17, 0.17],
    [0.20, 0.18, 0.16, 0.15, 0.14, 0.13, 0.13, 0.14, 0.14, 0.15, 0.15],
]

_RE_ELBOW_CORR_RE = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 14.0]  # in units of 1e4
_RE_ELBOW_CORR_K_LOW = [1.40, 1.26, 1.19, 1.14, 1.09, 1.06, 1.04, 1.0]   # R/W <= 0.5
_RE_ELBOW_CORR_K_HIGH = [2.0, 1.77, 1.64, 1.56, 1.46, 1.38, 1.30, 1.15]  # R/W >= 0.75


def _elbow_reynolds_correction(r_on_w, reynolds):
    re_scaled = reynolds / 1e4
    if re_scaled >= 20.0:
        return 1.0
    k_low = _interp1d(re_scaled, _RE_ELBOW_CORR_RE, _RE_ELBOW_CORR_K_LOW)
    if r_on_w <= 0.5:
        return k_low
    k_high = _interp1d(re_scaled, _RE_ELBOW_CORR_RE, _RE_ELBOW_CORR_K_HIGH)
    if r_on_w >= 0.75:
        return k_high
    t = (r_on_w - 0.5) / 0.25
    return k_low + t * (k_high - k_low)


def elbow_zeta_rect(h_on_w, r_on_w, reynolds):
    """
    90 deg smooth-radius rectangular elbow without turning vanes.
    h_on_w = duct height / width, r_on_w = centerline radius / width.
    SMACNA A7F, with the Reynolds-number correction factor from the same table set.
    """
    zeta = _interp2d(h_on_w, r_on_w, _ELBOW_RECT_H_ON_W, _ELBOW_RECT_R_ON_W, _ELBOW_RECT_ZETA)
    return zeta * _elbow_reynolds_correction(r_on_w, reynolds)


# ----------------------------------------------------------------------------
# Transitions (SMACNA A8A / A8B / A9A)
# ----------------------------------------------------------------------------

_EXP_ROUND_THETA = [16.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0]
_EXP_ROUND_AREA_RATIO = [2.0, 4.0, 6.0, 10.0, 16.0]
_EXP_ROUND_ZETA_RE_LOW = [
    [0.14, 0.19, 0.32, 0.33, 0.33, 0.32, 0.31, 0.30],
    [0.23, 0.30, 0.46, 0.61, 0.68, 0.64, 0.63, 0.62],
    [0.27, 0.33, 0.48, 0.66, 0.77, 0.74, 0.73, 0.72],
    [0.29, 0.38, 0.59, 0.76, 0.80, 0.83, 0.84, 0.83],
    [0.31, 0.38, 0.60, 0.84, 0.88, 0.88, 0.88, 0.88],
]
_EXP_ROUND_ZETA_RE_MID = [
    [0.07, 0.12, 0.23, 0.28, 0.27, 0.27, 0.27, 0.26],
    [0.15, 0.18, 0.36, 0.55, 0.59, 0.59, 0.58, 0.57],
    [0.19, 0.28, 0.44, 0.90, 0.70, 0.71, 0.71, 0.69],
    [0.20, 0.24, 0.43, 0.76, 0.80, 0.81, 0.81, 0.81],
    [0.21, 0.28, 0.52, 0.76, 0.87, 0.87, 0.87, 0.87],
]
_EXP_ROUND_ZETA_RE_HIGH = [
    [0.05, 0.07, 0.12, 0.27, 0.27, 0.27, 0.27, 0.27],
    [0.17, 0.24, 0.38, 0.51, 0.56, 0.58, 0.58, 0.57],
    [0.16, 0.29, 0.46, 0.60, 0.69, 0.71, 0.70, 0.70],
    [0.21, 0.33, 0.52, 0.60, 0.76, 0.83, 0.84, 0.83],
    [0.21, 0.34, 0.56, 0.72, 0.79, 0.85, 0.87, 0.89],
]


def expansion_zeta_round(theta_deg, area_ratio, reynolds):
    """
    Gradual round conical expansion (diverging transition), referenced to
    the OUTLET (larger, downstream) duct's velocity pressure.
    area_ratio = larger_area / smaller_area (>= 1). SMACNA A8A.
    """
    if reynolds < 2e5:
        table = _EXP_ROUND_ZETA_RE_LOW
    elif reynolds < 6e5:
        table = _EXP_ROUND_ZETA_RE_MID
    else:
        table = _EXP_ROUND_ZETA_RE_HIGH
    zeta_small = _interp2d(theta_deg, area_ratio, _EXP_ROUND_THETA, _EXP_ROUND_AREA_RATIO, table)
    return zeta_small * (area_ratio ** 2)


_EXP_RECT_THETA = [16.0, 20.0, 30.0, 45.0, 60.0, 90.0, 120.0, 180.0]
_EXP_RECT_AREA_RATIO = [2.0, 4.0, 6.0, 10.0]
_EXP_RECT_ZETA = [
    [0.18, 0.22, 0.25, 0.29, 0.31, 0.32, 0.33, 0.30],
    [0.36, 0.43, 0.50, 0.56, 0.61, 0.63, 0.63, 0.63],
    [0.42, 0.47, 0.58, 0.68, 0.72, 0.76, 0.76, 0.75],
    [0.42, 0.49, 0.59, 0.70, 0.80, 0.87, 0.85, 0.86],
]


def expansion_zeta_rect(theta_deg, area_ratio):
    """
    Pyramidal rectangular expansion (diverging transition), referenced to
    the OUTLET (larger, downstream) duct's velocity pressure.
    area_ratio = larger_area / smaller_area (>= 1). SMACNA A8B.
    """
    zeta_small = _interp2d(theta_deg, area_ratio, _EXP_RECT_THETA, _EXP_RECT_AREA_RATIO, _EXP_RECT_ZETA)
    return zeta_small * (area_ratio ** 2)


_CONTRACTION_THETA = [10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 60.0, 90.0, 120.0, 150.0, 180.0]
_CONTRACTION_AREA_RATIO = [2.0, 4.0, 6.0, 10.0]
_CONTRACTION_ZETA = [
    [0.05, 0.05, 0.05, 0.05, 0.05, 0.06, 0.06, 0.12, 0.18, 0.24, 0.26],
    [0.05, 0.04, 0.04, 0.04, 0.04, 0.07, 0.07, 0.17, 0.27, 0.35, 0.41],
    [0.05, 0.04, 0.04, 0.04, 0.04, 0.07, 0.07, 0.18, 0.28, 0.36, 0.42],
    [0.05, 0.05, 0.05, 0.05, 0.05, 0.08, 0.08, 0.19, 0.29, 0.37, 0.43],
]


def contraction_zeta(theta_deg, area_ratio):
    """
    Gradual-to-abrupt contraction (converging transition), round or
    rectangular, referenced directly to the OUTLET (smaller, downstream)
    duct's velocity pressure (no conversion needed -- the table is already
    referenced to the small/downstream duct). area_ratio = larger_area /
    smaller_area (>= 1). SMACNA A9A (explicitly covers round and rectangular).
    """
    return _interp2d(theta_deg, area_ratio, _CONTRACTION_THETA, _CONTRACTION_AREA_RATIO, _CONTRACTION_ZETA)


# ----------------------------------------------------------------------------
# Branch fittings -- converging (merging), SMACNA A10A (45 deg wye) / A10B (90 deg tee)
# ----------------------------------------------------------------------------

_CONV_WYE45_AB_ON_AC = [0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0]
_CONV_WYE45_VB_ON_VC = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.5, 2.0, 2.5, 3.0]
_CONV_WYE45_ZETA_B = [
    [-0.56, -0.44, -0.35, -0.28, -0.15, -0.04, 0.05],
    [-0.48, -0.37, -0.28, -0.21, -0.09, 0.02, 0.11],
    [-0.38, -0.27, -0.19, -0.12, 0.0, 0.10, 0.18],
    [-0.26, -0.16, -0.08, -0.01, 0.10, 0.20, 0.28],
    [-0.21, -0.02, 0.05, 0.12, 0.23, 0.32, 0.40],
    [0.04, 0.13, 0.21, 0.27, 0.37, 0.46, 0.53],
    [0.22, 0.31, 0.38, 0.44, 0.53, 0.62, 0.69],
    [1.4, 1.5, 1.5, 1.6, 1.7, 1.7, 1.8],
    [3.1, 3.2, 3.2, 3.2, 3.3, 3.3, 3.3],
    [5.3, 5.3, 5.3, 5.4, 5.4, 5.4, 5.4],
    [8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0],
]
_CONV_WYE45_VS_ON_VC = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
_CONV_WYE45_ZETA_C = [
    [-8.6, -4.1, -2.5, -1.7, -0.97, -0.58, -0.34],
    [-6.7, -3.1, -1.9, -1.3, -0.67, -0.36, -0.18],
    [-5.0, -2.2, -1.3, -0.88, -0.42, -0.19, -0.05],
    [-3.5, -1.5, -0.88, -0.55, -0.21, -0.05, 0.05],
    [-2.3, -0.95, -0.51, -0.28, -0.06, 0.06, 0.13],
    [-1.3, -0.50, -0.22, -0.09, 0.05, 0.12, 0.17],
    [-0.63, -0.18, -0.03, 0.04, 0.12, 0.16, 0.18],
    [-0.18, 0.01, 0.07, 0.10, 0.13, 0.15, 0.17],
    [0.03, 0.07, 0.08, 0.09, 0.10, 0.11, 0.13],
    [-0.01, 0.0, 0.0, 0.10, 0.02, 0.04, 0.05],
]

_CONV_TEE90_AB_ON_AC = [0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0]
_CONV_TEE90_VB_ON_VC = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
_CONV_TEE90_ZETA_B = [
    [0.40, -0.37, -0.51, -0.46, -0.50, -0.51, -0.52],
    [3.8, 0.72, 0.17, -0.02, -0.14, -0.18, -0.24],
    [9.2, 2.3, 1.0, 0.44, 0.21, 0.11, -0.08],
    [16.0, 4.3, 2.1, 0.94, 0.54, 0.40, 0.32],
    [26.0, 6.8, 3.2, 1.1, 0.66, 0.49, 0.42],
    [37.0, 9.7, 4.7, 1.6, 0.92, 0.69, 0.57],
    [43.0, 13.0, 6.3, 2.1, 1.2, 0.88, 0.72],
    [65.0, 17.0, 7.9, 2.7, 1.5, 1.1, 0.86],
    [82.0, 21.0, 9.7, 3.4, 1.8, 1.2, 0.99],
    [101.0, 26.0, 12.0, 4.0, 2.1, 1.4, 1.1],
]
_CONV_TEE90_ZETA_C = [0.16, 0.27, 0.38, 0.46, 0.53, 0.57, 0.59, 0.60, 0.59, 0.55]

_BRANCH_ANGLE_SPLIT_DEG = 67.5  # midpoint between the 45 deg wye and 90 deg tee tables


def converging_branch_zetas(angle_deg, ab_on_ac, vb_on_vc, vs_on_vc):
    """
    Converging (merging) tee/wye. Returns (zeta_branch, zeta_straight), each
    already referenced to that leg's OWN (inlet) velocity pressure.

    angle_deg: branch entry angle from the straight/main duct (0 = collinear).
               <= 67.5 uses the 45 deg wye table (A10A), > 67.5 the 90 deg
               tee table (A10B).
    ab_on_ac: branch duct area / combined (common, outlet) duct area.
    vb_on_vc, vs_on_vc: branch and straight-leg velocity / combined duct velocity.

    Also used as an approximation for rectangular/oval branches -- see the
    module docstring.
    """
    if angle_deg <= _BRANCH_ANGLE_SPLIT_DEG:
        zeta_b_raw = _interp2d(ab_on_ac, vb_on_vc, _CONV_WYE45_AB_ON_AC, _CONV_WYE45_VB_ON_VC, _CONV_WYE45_ZETA_B)
        zeta_c_raw = _interp2d(ab_on_ac, vs_on_vc, _CONV_WYE45_AB_ON_AC, _CONV_WYE45_VS_ON_VC, _CONV_WYE45_ZETA_C)
    else:
        zeta_b_raw = _interp2d(ab_on_ac, vb_on_vc, _CONV_TEE90_AB_ON_AC, _CONV_TEE90_VB_ON_VC, _CONV_TEE90_ZETA_B)
        zeta_c_raw = _interp1d(vb_on_vc, _CONV_TEE90_VB_ON_VC, _CONV_TEE90_ZETA_C)

    zeta_branch = zeta_b_raw / (vb_on_vc ** 2) if vb_on_vc > 0 else zeta_b_raw
    zeta_straight = zeta_c_raw / (vs_on_vc ** 2) if vs_on_vc > 0 else zeta_c_raw
    return zeta_branch, zeta_straight


# ----------------------------------------------------------------------------
# Branch fittings -- diverging (splitting), SMACNA A11A (30/45/60/90 deg; only
# the 45/90 deg variants are implemented, matching this addon's wye/tee families)
# ----------------------------------------------------------------------------

_DIV_AB_ON_AC = [0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
_DIV_VB_ON_VC = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
_DIV_ZETA_B_WYE45 = [
    [0.78, 0.62, 0.49, 0.40, 0.34, 0.31, 0.32, 0.35, 0.40],
    [0.77, 0.59, 0.47, 0.38, 0.34, 0.32, 0.35, 0.41, 0.50],
    [0.74, 0.56, 0.44, 0.37, 0.35, 0.36, 0.43, 0.54, 0.68],
    [0.71, 0.52, 0.41, 0.38, 0.40, 0.45, 0.59, 0.78, 1.00],
    [0.66, 0.47, 0.40, 0.43, 0.54, 0.69, 0.95, 1.30, 1.70],
    [0.66, 0.48, 0.52, 0.73, 1.20, 1.80, 2.70, 2.70, 2.70],
    [0.56, 0.56, 1.00, 1.80, 1.80, 1.80, 1.80, 1.80, 1.80],
    [0.60, 2.10, 2.10, 2.10, 2.10, 2.10, 2.10, 2.10, 2.10],
]
_DIV_ZETA_B_TEE90 = [
    [0.95, 0.92, 0.92, 0.93, 0.94, 0.95, 1.10, 1.20, 1.40],
    [0.95, 0.94, 0.95, 0.98, 1.00, 1.10, 1.20, 1.40, 1.60],
    [0.96, 0.97, 1.00, 1.10, 1.10, 1.20, 1.40, 1.70, 2.00],
    [0.97, 1.00, 1.10, 1.20, 1.40, 1.50, 1.80, 2.10, 2.50],
    [0.99, 1.10, 1.30, 1.50, 1.70, 2.00, 2.40, 2.40, 2.40],
    [1.10, 1.40, 1.80, 2.30, 2.30, 2.30, 2.30, 2.30, 2.30],
    [1.30, 1.90, 2.90, 2.90, 2.90, 2.90, 2.90, 2.90, 2.90],
    [2.10, 2.10, 2.10, 2.10, 2.10, 2.10, 2.10, 2.10, 2.10],
]
_DIV_VS_ON_VC = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]
_DIV_ZETA_C = [0.35, 0.28, 0.22, 0.17, 0.13, 0.09, 0.06, 0.02, 0.00]


def diverging_branch_zetas(angle_deg, ab_on_ac, vb_on_vc, vs_on_vc):
    """
    Diverging (splitting) tee/wye. Returns (zeta_branch, zeta_straight), each
    already referenced to that leg's OWN (outlet) velocity pressure.

    angle_deg: branch angle from the straight/main duct.
               <= 67.5 uses the 45 deg wye table, > 67.5 the 90 deg tee table.
    ab_on_ac: branch duct area / combined (common, inlet) duct area.
    vb_on_vc, vs_on_vc: branch and straight-leg velocity / combined duct velocity.

    Also used as an approximation for rectangular/oval branches -- see the
    module docstring.
    """
    table = _DIV_ZETA_B_WYE45 if angle_deg <= _BRANCH_ANGLE_SPLIT_DEG else _DIV_ZETA_B_TEE90
    zeta_b_raw = _interp2d(vb_on_vc, ab_on_ac, _DIV_VB_ON_VC, _DIV_AB_ON_AC, table)
    zeta_c_raw = _interp1d(vs_on_vc, _DIV_VS_ON_VC, _DIV_ZETA_C)

    zeta_branch = zeta_b_raw / (vb_on_vc ** 2) if vb_on_vc > 0 else zeta_b_raw
    zeta_straight = zeta_c_raw / (vs_on_vc ** 2) if vs_on_vc > 0 else zeta_c_raw
    return zeta_branch, zeta_straight
