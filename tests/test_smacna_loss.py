import pytest

from freecad.HVAC.library import smacna_loss as sl


# ----------------------------------------------------------------------------
# Interpolation helpers
# ----------------------------------------------------------------------------

def test_interp1d_exact_grid_point():
    assert sl._interp1d(1.0, [0.5, 1.0, 1.5], [10.0, 20.0, 30.0]) == pytest.approx(20.0)


def test_interp1d_midpoint():
    assert sl._interp1d(1.25, [1.0, 1.5], [20.0, 30.0]) == pytest.approx(25.0)


def test_interp1d_clamps_outside_range():
    assert sl._interp1d(-5.0, [0.0, 1.0], [1.0, 2.0]) == pytest.approx(1.0)
    assert sl._interp1d(50.0, [0.0, 1.0], [1.0, 2.0]) == pytest.approx(2.0)


def test_interp2d_exact_grid_point():
    table = [[1.0, 2.0], [3.0, 4.0]]  # table[iy][ix]
    got = sl._interp2d(x=10.0, y=200.0, xs=[10.0, 20.0], ys=[100.0, 200.0], table=table)
    assert got == pytest.approx(3.0)


def test_interp2d_handles_descending_y_axis():
    # Same table, but ys given descending -- must match table row order.
    table = [[3.0, 4.0], [1.0, 2.0]]  # row0 -> y=200, row1 -> y=100
    got = sl._interp2d(x=10.0, y=200.0, xs=[10.0, 20.0], ys=[200.0, 100.0], table=table)
    assert got == pytest.approx(3.0)


# ----------------------------------------------------------------------------
# Elbows
# ----------------------------------------------------------------------------

def test_elbow_zeta_round_exact_grid_points():
    assert sl.elbow_zeta_round(0.5) == pytest.approx(0.71)
    assert sl.elbow_zeta_round(1.0) == pytest.approx(0.22)
    assert sl.elbow_zeta_round(2.5) == pytest.approx(0.12)


def test_elbow_zeta_round_interpolates_and_clamps():
    assert sl.elbow_zeta_round(0.625) == pytest.approx((0.71 + 0.33) / 2.0)
    assert sl.elbow_zeta_round(0.1) == pytest.approx(0.71)   # clamped below range
    assert sl.elbow_zeta_round(10.0) == pytest.approx(0.12)  # clamped above range


def test_elbow_zeta_rect_exact_grid_point_high_reynolds():
    # H/W=1.0, R/W=1.0 -> table row R/W=1.0 is [0.27,0.25,0.23,0.21,...], col H/W=1.0 -> 0.21
    # High Re (>=2e5) -> correction factor is 1.0 regardless of R/W.
    zeta = sl.elbow_zeta_rect(h_on_w=1.0, r_on_w=1.0, reynolds=1e6)
    assert zeta == pytest.approx(0.21)


def test_elbow_zeta_rect_reynolds_correction_reduces_at_low_reynolds():
    zeta_low_re = sl.elbow_zeta_rect(h_on_w=1.0, r_on_w=1.0, reynolds=1e4)
    zeta_high_re = sl.elbow_zeta_rect(h_on_w=1.0, r_on_w=1.0, reynolds=1e6)
    assert zeta_low_re > zeta_high_re  # correction factor > 1 at low Re


# ----------------------------------------------------------------------------
# Transitions
# ----------------------------------------------------------------------------

def test_expansion_zeta_round_exact_grid_point_mid_reynolds():
    # theta=90 (index 5), area_ratio=4 (index 1) -> mid-Re table value 0.59, referenced to small duct.
    zeta_small_equivalent = 0.59
    expected_large = zeta_small_equivalent * (4.0 ** 2)
    got = sl.expansion_zeta_round(theta_deg=90.0, area_ratio=4.0, reynolds=3e5)
    assert got == pytest.approx(expected_large)


def test_expansion_zeta_round_picks_reynolds_bucket():
    low = sl.expansion_zeta_round(theta_deg=90.0, area_ratio=4.0, reynolds=1e5)
    mid = sl.expansion_zeta_round(theta_deg=90.0, area_ratio=4.0, reynolds=3e5)
    high = sl.expansion_zeta_round(theta_deg=90.0, area_ratio=4.0, reynolds=7e5)
    assert low != mid or mid != high  # different Re buckets give different tables


def test_expansion_zeta_rect_exact_grid_point():
    # theta=90 (index5), area_ratio=4(index1) -> 0.63, referenced to small duct.
    expected_large = 0.63 * (4.0 ** 2)
    got = sl.expansion_zeta_rect(theta_deg=90.0, area_ratio=4.0)
    assert got == pytest.approx(expected_large)


def test_contraction_zeta_exact_grid_point():
    # theta=90 (index7), area_ratio=4 (index1) -> 0.17, referenced directly to small/outlet duct.
    got = sl.contraction_zeta(theta_deg=90.0, area_ratio=4.0)
    assert got == pytest.approx(0.17)


# ----------------------------------------------------------------------------
# Branch fittings
# ----------------------------------------------------------------------------

def test_diverging_branch_tee90_exact_grid_point():
    # ab_on_ac=0.5 (index3), vb_on_vc=0.5(index4) -> raw zeta_b=1.40
    zeta_branch, _ = sl.diverging_branch_zetas(
        angle_deg=90.0, ab_on_ac=0.5, vb_on_vc=0.5, vs_on_vc=0.4
    )
    assert zeta_branch == pytest.approx(1.40 / (0.5 ** 2))


def test_diverging_branch_tee90_straight_leg_exact_grid_point():
    # vs_on_vc=0.4 (index4 of [0,0.1,...,1.0]) -> raw zeta_c=0.13
    _, zeta_straight = sl.diverging_branch_zetas(
        angle_deg=90.0, ab_on_ac=0.5, vb_on_vc=0.5, vs_on_vc=0.4
    )
    assert zeta_straight == pytest.approx(0.13 / (0.4 ** 2))


def test_diverging_branch_selects_wye_vs_tee_table():
    wye_branch, _ = sl.diverging_branch_zetas(angle_deg=45.0, ab_on_ac=0.5, vb_on_vc=0.5, vs_on_vc=0.4)
    tee_branch, _ = sl.diverging_branch_zetas(angle_deg=90.0, ab_on_ac=0.5, vb_on_vc=0.5, vs_on_vc=0.4)
    assert wye_branch != tee_branch


def test_converging_branch_tee90_exact_grid_point():
    # Ab_on_Ac=0.4 (index3 of [0.1,0.2,0.3,0.4,0.6,0.8,1.0]), Vb_on_Vc=0.4 (index3) -> raw zeta_b=0.94
    zeta_branch, _ = sl.converging_branch_zetas(
        angle_deg=90.0, ab_on_ac=0.4, vb_on_vc=0.4, vs_on_vc=0.6
    )
    assert zeta_branch == pytest.approx(0.94 / (0.4 ** 2))


def test_converging_branch_wye_vs_tee_differ():
    wye_branch, _ = sl.converging_branch_zetas(angle_deg=45.0, ab_on_ac=0.4, vb_on_vc=0.7, vs_on_vc=0.6)
    tee_branch, _ = sl.converging_branch_zetas(angle_deg=90.0, ab_on_ac=0.4, vb_on_vc=0.7, vs_on_vc=0.6)
    assert wye_branch != tee_branch
