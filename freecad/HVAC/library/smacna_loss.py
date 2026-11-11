# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Loss coefficients for the fittings HVACLossAPI computes today, as plain
functions of plain floats.

The table data itself lives in `library/loss_tables/` (one LossTable per
printed SMACNA table). This module is the thin layer on top that the loss
API calls: it reads the right table and returns the coefficient already
referenced to the velocity pressure the solver multiplies it by -- the
relevant leg's OWN velocity. Where the source table is referenced to a
different duct, the conversion is applied here
(zeta2 = zeta1 * Pv1 / Pv2 = zeta1 / (V2 / V1)**2, since velocity pressure
goes with V**2 at constant density).

Round-duct branch (tee/wye) tables are also used as an approximation for
rectangular/oval branch fittings. SMACNA's rectangular-specific tables are
keyed to a handful of discrete area-ratio combinations rather than a smooth
function of geometry; they are digitised in loss_tables but not used here
yet.
"""

from .loss_tables import elbows, junctions_converging, junctions_diverging, transitions

# ----------------------------------------------------------------------------
# Elbows (SMACNA Table 14-10)
# ----------------------------------------------------------------------------


def elbow_angle_factor(theta_deg):
    """Multiplier on a 90 deg smooth-radius elbow coefficient for a turn of theta_deg (14-10 Note 1)."""
    return elbows.ANGLE_FACTOR(theta_deg)


def elbow_zeta_round(r_on_d):
    """90 deg smooth-radius round elbow, r_on_d = centerline radius / diameter (14-10 A)."""
    return elbows.SMOOTH_ROUND(r_on_d)


def _elbow_reynolds_correction(r_on_w, reynolds):
    """Reynolds number correction for a smooth-radius rectangular elbow (14-10 Note 3)."""
    return elbows.reynolds_factor_smooth_rect(r_on_w, reynolds).value


def elbow_zeta_rect(h_on_w, r_on_w, reynolds):
    """
    90 deg smooth-radius rectangular elbow without turning vanes (14-10 F),
    with its Reynolds number correction. h_on_w = duct height / width,
    r_on_w = centerline radius / width.
    """
    return elbows.SMOOTH_RECT(r_on_w, h_on_w) * _elbow_reynolds_correction(r_on_w, reynolds)


def elbow_zeta_round_mitered(theta_deg):
    """Mitered round elbow at any turn angle (14-10 C)."""
    return elbows.MITERED_ROUND(theta_deg)


def elbow_zeta_rect_mitered(h_on_w, theta_deg):
    """Mitered rectangular elbow at any turn angle (14-10 D)."""
    return elbows.MITERED_RECT(theta_deg, h_on_w)


# ----------------------------------------------------------------------------
# Transitions (SMACNA Tables 14-11 and 14-12)
# ----------------------------------------------------------------------------


def expansion_zeta_round(theta_deg, area_ratio, reynolds):
    """
    Round conical expansion (14-11 A), referenced to the OUTLET (larger,
    downstream) duct's velocity pressure. area_ratio = larger_area /
    smaller_area (>= 1). The table is referenced to the smaller duct, whose
    velocity is area_ratio times the outlet's.
    """
    zeta_small = transitions.expansion_round(theta_deg, area_ratio, reynolds).value
    return zeta_small * (area_ratio**2)


def expansion_zeta_rect(theta_deg, area_ratio):
    """Rectangular pyramidal expansion (14-11 B), referenced to the OUTLET duct like expansion_zeta_round."""
    zeta_small = transitions.expansion_rect_pyramidal(theta_deg, area_ratio).value
    return zeta_small * (area_ratio**2)


def contraction_zeta(theta_deg, area_ratio):
    """
    Gradual-to-abrupt contraction, round or rectangular (14-12 A). Already
    referenced to the OUTLET (smaller, downstream) duct, as the table is.
    area_ratio = larger_area / smaller_area (>= 1).
    """
    return transitions.contraction(theta_deg, area_ratio).value


# ----------------------------------------------------------------------------
# Branch fittings (SMACNA Tables 14-13 and 14-14)
# ----------------------------------------------------------------------------

# A converging branch up to this angle reads the 45 deg wye table, above it
# the 90 deg tee table (the midpoint between the two printed fittings).
_BRANCH_ANGLE_SPLIT_DEG = 67.5


def _per_own_velocity(zeta_common, v_on_vc):
    """Re-reference a coefficient from the common duct's velocity to a leg's own velocity."""
    return zeta_common / (v_on_vc**2) if v_on_vc > 0 else zeta_common


def converging_branch_zetas(angle_deg, ab_on_ac, vb_on_vc, vs_on_vc):
    """
    Converging (merging) tee/wye. Returns (zeta_branch, zeta_straight), each
    already referenced to that leg's OWN (inlet) velocity pressure.

    angle_deg: branch entry angle from the straight/main duct (0 = collinear).
               <= 67.5 uses the 45 deg wye table (14-13 A), > 67.5 the 90 deg
               tee table (14-13 B).
    ab_on_ac: branch duct area / combined (common, outlet) duct area.
    vb_on_vc, vs_on_vc: branch and straight-leg velocity / combined duct velocity.

    Also used as an approximation for rectangular/oval branches -- see the
    module docstring.
    """
    if angle_deg <= _BRANCH_ANGLE_SPLIT_DEG:
        branch, main = junctions_converging.wye_45_round(ab_on_ac, vb_on_vc, vs_on_vc)
    else:
        # The 90 deg tee table is keyed by the branch FLOW ratio Qb/Qc, unlike
        # the wye table, which is keyed by velocity ratios. Q = V * A, so
        # Qb/Qc = (Vb/Vc) * (Ab/Ac).
        branch, main = junctions_converging.tee_90_round(ab_on_ac, vb_on_vc * ab_on_ac)
    return _per_own_velocity(branch.value, vb_on_vc), _per_own_velocity(main.value, vs_on_vc)


def diverging_branch_zetas(angle_deg, ab_on_ac, vb_on_vc, vs_on_vc):
    """
    Diverging (splitting) tee/wye (14-14 A). Returns (zeta_branch,
    zeta_straight), each already referenced to that leg's OWN (outlet)
    velocity pressure.

    angle_deg: branch angle from the straight/main duct. The source prints
               30, 45, 60 and 90 deg; angles in between are interpolated,
               and anything outside reads the nearest printed angle.
    ab_on_ac: branch duct area / combined (common, inlet) duct area.
    vb_on_vc, vs_on_vc: branch and straight-leg velocity / combined duct velocity.

    Also used as an approximation for rectangular/oval branches -- see the
    module docstring.
    """
    # The branch table is keyed by the branch FLOW ratio Qb/Qc; only the main
    # coefficient is keyed by a velocity ratio. Q = V * A, so
    # Qb/Qc = (Vb/Vc) * (Ab/Ac).
    branch, main = junctions_diverging.round_tee_or_wye(angle_deg, ab_on_ac, vb_on_vc * ab_on_ac, vs_on_vc)
    return _per_own_velocity(branch.value, vb_on_vc), _per_own_velocity(main.value, vs_on_vc)
