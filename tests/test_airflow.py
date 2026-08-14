import math

import pytest

from freecad.HVAC.core import airflow


# ----------------------------------------------------------------------------
# Unit conversion
# ----------------------------------------------------------------------------

def test_mm_to_m():
    assert airflow.mm_to_m(1000.0) == pytest.approx(1.0)


def test_lps_to_m3s_roundtrip():
    assert airflow.lps_to_m3s(1000.0) == pytest.approx(1.0)
    assert airflow.m3s_to_lps(airflow.lps_to_m3s(250.0)) == pytest.approx(250.0)


# ----------------------------------------------------------------------------
# Area
# ----------------------------------------------------------------------------

def test_circular_area():
    # d=1m -> A = pi/4
    assert airflow.circular_area(1.0) == pytest.approx(math.pi / 4.0)


def test_rectangular_area():
    assert airflow.rectangular_area(0.4, 0.2) == pytest.approx(0.08)


def test_oval_area_degenerates_to_circle_when_square():
    # width == height -> pure circle of that diameter
    got = airflow.oval_area(0.3, 0.3)
    expected = airflow.circular_area(0.3)
    assert got == pytest.approx(expected)


def test_oval_area_rectangle_plus_circle():
    # width=0.5, height=0.2 -> straight rectangle (0.3 x 0.2) + full circle r=0.1
    got = airflow.oval_area(0.5, 0.2)
    expected = 0.3 * 0.2 + math.pi * (0.1 ** 2)
    assert got == pytest.approx(expected)


def test_oval_area_rejects_width_less_than_height():
    with pytest.raises(ValueError):
        airflow.oval_area(0.2, 0.5)


# ----------------------------------------------------------------------------
# Hydraulic diameter
# ----------------------------------------------------------------------------

def test_hydraulic_diameter_circular_equals_diameter():
    assert airflow.hydraulic_diameter_circular(0.25) == pytest.approx(0.25)


def test_hydraulic_diameter_rectangular_square_duct_equals_side():
    # For a square duct, Dh == side length
    assert airflow.hydraulic_diameter_rectangular(0.3, 0.3) == pytest.approx(0.3)


def test_hydraulic_diameter_rectangular_known_value():
    # w=0.4, h=0.2 -> Dh = 2*0.4*0.2/(0.4+0.2) = 0.16/0.6
    assert airflow.hydraulic_diameter_rectangular(0.4, 0.2) == pytest.approx(0.16 / 0.6)


def test_hydraulic_diameter_oval_degenerates_to_circle_when_square():
    got = airflow.hydraulic_diameter_oval(0.3, 0.3)
    expected = airflow.hydraulic_diameter_circular(0.3)
    assert got == pytest.approx(expected)


# ----------------------------------------------------------------------------
# Velocity / Reynolds number
# ----------------------------------------------------------------------------

def test_velocity_from_flow():
    # 1 m3/s through 1 m2 -> 1 m/s
    assert airflow.velocity_from_flow(1.0, 1.0) == pytest.approx(1.0)
    assert airflow.velocity_from_flow(2.0, 0.5) == pytest.approx(4.0)


def test_reynolds_number_known_value():
    # Re = V*Dh/nu
    re = airflow.reynolds_number(velocity_m_s=10.0, hydraulic_diameter_m=0.2,
                                  kinematic_viscosity_m2_s=1.51e-5)
    assert re == pytest.approx(10.0 * 0.2 / 1.51e-5)


def test_reynolds_number_uses_absolute_velocity():
    re = airflow.reynolds_number(velocity_m_s=-10.0, hydraulic_diameter_m=0.2,
                                  kinematic_viscosity_m2_s=1.51e-5)
    assert re > 0.0


# ----------------------------------------------------------------------------
# Friction factor
# ----------------------------------------------------------------------------

def test_friction_factor_altshul_tsal_typical_duct_case():
    # Typical HVAC duct: Re ~ 1.3e5, smooth-ish galvanized steel relative roughness ~ 0.0005
    f = airflow.friction_factor_altshul_tsal(reynolds=1.3e5, relative_roughness=0.0005)
    # Should land in the normal Moody-chart range for this Re/roughness combination
    assert 0.017 < f < 0.03


def test_friction_factor_altshul_tsal_low_f_branch_applies_correction():
    # Very smooth duct, very high Re -> raw formula would go below 0.018,
    # triggering the low-f correction branch (f = 0.85*f + 0.0028)
    raw = 0.11 * (0.0 + 68.0 / 1e7) ** 0.25
    assert raw < 0.018
    f = airflow.friction_factor_altshul_tsal(reynolds=1e7, relative_roughness=0.0)
    assert f == pytest.approx(0.85 * raw + 0.0028)


def test_friction_factor_altshul_tsal_increases_with_roughness():
    f_smooth = airflow.friction_factor_altshul_tsal(reynolds=1e5, relative_roughness=0.0001)
    f_rough = airflow.friction_factor_altshul_tsal(reynolds=1e5, relative_roughness=0.01)
    assert f_rough > f_smooth


def test_friction_factor_rejects_nonpositive_reynolds():
    with pytest.raises(ValueError):
        airflow.friction_factor_altshul_tsal(reynolds=0.0, relative_roughness=0.001)


# ----------------------------------------------------------------------------
# Pressure loss
# ----------------------------------------------------------------------------

def test_velocity_pressure_known_value():
    # rho=1.204, V=10 -> Pv = 1.204*100/2 = 60.2
    assert airflow.velocity_pressure(1.204, 10.0) == pytest.approx(60.2)


def test_darcy_weisbach_pressure_loss_known_value():
    # f=0.02, L=10m, Dh=0.2m, rho=1.2, V=5m/s
    # dP = f*(L/Dh)*(rho*V^2/2) = 0.02*(10/0.2)*(1.2*25/2) = 0.02*50*15 = 15
    dp = airflow.darcy_weisbach_pressure_loss(friction_factor=0.02, length_m=10.0,
                                               hydraulic_diameter_m=0.2,
                                               air_density_kg_m3=1.2, velocity_m_s=5.0)
    assert dp == pytest.approx(15.0)


def test_darcy_weisbach_pressure_loss_zero_length_is_zero():
    dp = airflow.darcy_weisbach_pressure_loss(friction_factor=0.02, length_m=0.0,
                                               hydraulic_diameter_m=0.2,
                                               air_density_kg_m3=1.2, velocity_m_s=5.0)
    assert dp == pytest.approx(0.0)


# ----------------------------------------------------------------------------
# Duct sizing: constant velocity
# ----------------------------------------------------------------------------

def test_circular_diameter_for_velocity_round_trip():
    flow, v = 0.5, 6.0
    d = airflow.circular_diameter_for_velocity(flow, v)
    got_v = airflow.velocity_from_flow(flow, airflow.circular_area(d))
    assert got_v == pytest.approx(v)


def test_circular_diameter_for_velocity_rejects_nonpositive_velocity():
    with pytest.raises(ValueError):
        airflow.circular_diameter_for_velocity(0.5, 0.0)


@pytest.mark.parametrize("mode,kwargs", [
    ("aspect_ratio", {"aspect_ratio": 2.0}),
    ("fixed_height", {"fixed_dim_m": 0.3}),
    ("fixed_width", {"fixed_dim_m": 0.5}),
])
def test_rect_dims_for_velocity_round_trip(mode, kwargs):
    flow, v = 0.5, 6.0
    w, h = airflow.rect_dims_for_velocity(flow, v, mode, **kwargs)
    got_v = airflow.velocity_from_flow(flow, airflow.rectangular_area(w, h))
    assert got_v == pytest.approx(v)


def test_rect_dims_for_velocity_aspect_ratio_is_honored():
    w, h = airflow.rect_dims_for_velocity(0.5, 6.0, "aspect_ratio", aspect_ratio=2.5)
    assert w / h == pytest.approx(2.5)


def test_rect_dims_for_velocity_fixed_height_keeps_height():
    w, h = airflow.rect_dims_for_velocity(0.5, 6.0, "fixed_height", fixed_dim_m=0.3)
    assert h == pytest.approx(0.3)


def test_rect_dims_for_velocity_fixed_width_keeps_width():
    w, h = airflow.rect_dims_for_velocity(0.5, 6.0, "fixed_width", fixed_dim_m=0.5)
    assert w == pytest.approx(0.5)


def test_rect_dims_for_velocity_unknown_mode_raises():
    with pytest.raises(ValueError):
        airflow.rect_dims_for_velocity(0.5, 6.0, "bogus")


@pytest.mark.parametrize("mode,kwargs", [
    ("aspect_ratio", {"aspect_ratio": 2.0}),
    ("fixed_height", {"fixed_dim_m": 0.25}),
    ("fixed_width", {"fixed_dim_m": 0.6}),
])
def test_oval_dims_for_velocity_round_trip(mode, kwargs):
    flow, v = 0.4, 6.0
    w, h = airflow.oval_dims_for_velocity(flow, v, mode, **kwargs)
    assert w >= h > 0.0
    got_v = airflow.velocity_from_flow(flow, airflow.oval_area(w, h))
    assert got_v == pytest.approx(v)


def test_oval_dims_for_velocity_aspect_ratio_is_honored():
    w, h = airflow.oval_dims_for_velocity(0.4, 6.0, "aspect_ratio", aspect_ratio=1.8)
    assert w / h == pytest.approx(1.8)


def test_oval_dims_for_velocity_aspect_ratio_one_matches_circle():
    # At aspect_ratio=1 an oval degenerates to a circle.
    flow, v = 0.4, 6.0
    w, h = airflow.oval_dims_for_velocity(flow, v, "aspect_ratio", aspect_ratio=1.0)
    d = airflow.circular_diameter_for_velocity(flow, v)
    assert w == pytest.approx(d)
    assert h == pytest.approx(d)


def test_oval_dims_for_velocity_aspect_ratio_below_one_rejected():
    with pytest.raises(ValueError):
        airflow.oval_dims_for_velocity(0.4, 6.0, "aspect_ratio", aspect_ratio=0.5)


def test_oval_dims_for_velocity_fixed_height_keeps_height():
    w, h = airflow.oval_dims_for_velocity(0.4, 6.0, "fixed_height", fixed_dim_m=0.25)
    assert h == pytest.approx(0.25)


def test_oval_dims_for_velocity_fixed_width_keeps_width():
    w, h = airflow.oval_dims_for_velocity(0.4, 6.0, "fixed_width", fixed_dim_m=0.6)
    assert w == pytest.approx(0.6)


# ----------------------------------------------------------------------------
# Duct sizing: constant friction rate
# ----------------------------------------------------------------------------

ROUGHNESS_M = airflow.mm_to_m(0.09)
VISCOSITY = 1.51e-5
DENSITY = 1.204


def _actual_friction_rate(flow_m3_s, area_m2, dh_m):
    v = airflow.velocity_from_flow(flow_m3_s, area_m2)
    re = airflow.reynolds_number(v, dh_m, VISCOSITY)
    f = airflow.friction_factor_altshul_tsal(re, ROUGHNESS_M / dh_m)
    return airflow.darcy_weisbach_pressure_loss(f, 1.0, dh_m, DENSITY, v)


def test_circular_diameter_for_friction_rate_round_trip():
    flow, target = 0.5, 1.0
    d = airflow.circular_diameter_for_friction_rate(flow, target, ROUGHNESS_M, VISCOSITY, DENSITY)
    got_rate = _actual_friction_rate(flow, airflow.circular_area(d), airflow.hydraulic_diameter_circular(d))
    assert got_rate == pytest.approx(target, rel=1e-3)


@pytest.mark.parametrize("mode,kwargs", [
    ("aspect_ratio", {"aspect_ratio": 2.0}),
    ("fixed_height", {"fixed_dim_m": 0.3}),
    ("fixed_width", {"fixed_dim_m": 0.5}),
])
def test_rect_dims_for_friction_rate_round_trip(mode, kwargs):
    flow, target = 0.5, 1.0
    w, h = airflow.rect_dims_for_friction_rate(flow, target, ROUGHNESS_M, VISCOSITY, DENSITY, mode, **kwargs)
    got_rate = _actual_friction_rate(flow, airflow.rectangular_area(w, h), airflow.hydraulic_diameter_rectangular(w, h))
    assert got_rate == pytest.approx(target, rel=1e-3)


@pytest.mark.parametrize("mode,kwargs", [
    ("aspect_ratio", {"aspect_ratio": 2.0}),
    ("fixed_height", {"fixed_dim_m": 0.25}),
    ("fixed_width", {"fixed_dim_m": 0.6}),
])
def test_oval_dims_for_friction_rate_round_trip(mode, kwargs):
    flow, target = 0.4, 1.0
    w, h = airflow.oval_dims_for_friction_rate(flow, target, ROUGHNESS_M, VISCOSITY, DENSITY, mode, **kwargs)
    assert w >= h > 0.0
    got_rate = _actual_friction_rate(flow, airflow.oval_area(w, h), airflow.hydraulic_diameter_oval(w, h))
    assert got_rate == pytest.approx(target, rel=1e-3)


def test_friction_rate_sizing_monotonic_with_target():
    # A looser (smaller) target friction rate should always require a bigger duct.
    flow = 0.5
    d_tight = airflow.circular_diameter_for_friction_rate(flow, 2.0, ROUGHNESS_M, VISCOSITY, DENSITY)
    d_loose = airflow.circular_diameter_for_friction_rate(flow, 0.5, ROUGHNESS_M, VISCOSITY, DENSITY)
    assert d_loose > d_tight


def test_friction_rate_sizing_clamps_to_bracket_when_unreachable():
    # An absurdly tight target (unreachable within the bracket) clamps to the
    # largest bracketed diameter rather than raising or looping forever.
    d = airflow.circular_diameter_for_friction_rate(0.5, 1e-6, ROUGHNESS_M, VISCOSITY, DENSITY)
    assert d == pytest.approx(5.0)
