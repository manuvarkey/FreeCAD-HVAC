"""Pure tests for library/custom_fitting.py (ROADMAP 6.3): port matching, rigid fit and trims."""

import math

import numpy as np
import pytest

from freecad.HVAC.library import custom_fitting as cf


def _rot_z(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _tee_ports():
    # A tee in its own frame: run along x (ports at +-300), branch along +y at 250.
    return [
        cf.CustomPort("A", (-300.0, 0.0, 0.0), (-1.0, 0.0, 0.0), profile="Circular", section={"Diameter": 200.0}),
        cf.CustomPort("B", (300.0, 0.0, 0.0), (1.0, 0.0, 0.0), profile="Circular", section={"Diameter": 200.0}),
        cf.CustomPort("C", (0.0, 250.0, 0.0), (0.0, 1.0, 0.0), profile="Circular", section={"Diameter": 150.0}),
    ]


def _ducts_for(rotation, keys=("e1", "e2", "e3"), dirs=None):
    dirs = dirs or [(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    return [cf.DuctEnd(k, tuple(rotation @ np.array(d))) for k, d in zip(keys, dirs)]


def test_tee_fits_a_rotated_node_and_reports_trims():
    rot = _rot_z(30.0)
    node = (1000.0, 500.0, 200.0)
    ducts = _ducts_for(rot, keys=("c", "a", "b"), dirs=[(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    result = cf.fit(_tee_ports(), ducts, node)
    assert result.mapping == {"c": 2, "a": 0, "b": 1}
    assert result.trims == pytest.approx({"c": 250.0, "a": 300.0, "b": 300.0})
    assert np.allclose(result.rotation, rot, atol=1e-9)
    assert np.allclose(result.translation, node, atol=1e-6)
    assert max(result.offset_errors.values()) < 1e-6


def test_off_centre_fitting_is_shifted_onto_the_ducts():
    # Same tee modelled with its frame origin elsewhere: only the translation changes.
    ports = _tee_ports()
    for p in ports:
        p.origin = tuple(np.array(p.origin) + np.array([40.0, -25.0, 10.0]))
    result = cf.fit(ports, _ducts_for(np.eye(3)), (0.0, 0.0, 0.0))
    assert result.trims == pytest.approx({"e1": 300.0, "e2": 300.0, "e3": 250.0})
    assert np.allclose(result.translation, [-40.0, 25.0, -10.0], atol=1e-6)


def test_straight_fitting_roll_follows_the_width_axes():
    ports = [
        cf.CustomPort("in", (-200.0, 0.0, 0.0), (-1.0, 0.0, 0.0), x=(0.0, 1.0, 0.0)),
        cf.CustomPort("out", (200.0, 0.0, 0.0), (1.0, 0.0, 0.0), x=(0.0, 1.0, 0.0)),
    ]
    ducts = [cf.DuctEnd("a", (-1.0, 0.0, 0.0), x=(0.0, 0.0, 1.0)), cf.DuctEnd("b", (1.0, 0.0, 0.0), x=(0.0, 0.0, 1.0))]
    result = cf.fit(ports, ducts, (0.0, 0.0, 0.0))
    assert np.allclose(result.rotation @ np.array([0.0, 1.0, 0.0]), [0.0, 0.0, 1.0], atol=1e-9)
    assert result.trims == pytest.approx({"a": 200.0, "b": 200.0})


def test_straight_fitting_centres_itself_along_the_run():
    # Ports at -100 and +300 along the axis: the free axial position puts the centroid on the node.
    ports = [
        cf.CustomPort("a", (-100.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
        cf.CustomPort("b", (300.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    ]
    ducts = [cf.DuctEnd("a", (-1.0, 0.0, 0.0)), cf.DuctEnd("b", (1.0, 0.0, 0.0))]
    result = cf.fit(ports, ducts, (0.0, 0.0, 0.0))
    assert result.trims == pytest.approx({"a": 200.0, "b": 200.0})


def test_single_port_terminal():
    ports = [cf.CustomPort("neck", (0.0, 0.0, 120.0), (0.0, 0.0, 1.0))]
    result = cf.fit(ports, [cf.DuctEnd("e", (1.0, 0.0, 0.0))], (5.0, 5.0, 5.0))
    assert result.trims == pytest.approx({"e": 0.0}, abs=1e-6)
    assert np.allclose(result.rotation @ np.array([0.0, 0.0, 1.0]), [1.0, 0.0, 0.0])


def test_wrong_angles_are_an_error_naming_the_port():
    ducts = _ducts_for(np.eye(3), dirs=[(-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.5, 0.8660254, 0.0)])
    with pytest.raises(cf.CustomFitError, match="deg away"):
        cf.fit(_tee_ports(), ducts, (0.0, 0.0, 0.0))


def test_port_count_mismatch():
    with pytest.raises(cf.CustomFitError, match="3 port"):
        cf.fit(_tee_ports(), _ducts_for(np.eye(3))[:2], (0.0, 0.0, 0.0))


def test_port_behind_the_node_is_an_error():
    ports = [
        cf.CustomPort("a", (100.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
        cf.CustomPort("b", (0.0, 300.0, 0.0), (0.0, 1.0, 0.0)),
    ]
    ducts = [cf.DuctEnd("a", (-1.0, 0.0, 0.0)), cf.DuctEnd("b", (0.0, 1.0, 0.0))]
    with pytest.raises(cf.CustomFitError, match="behind the node"):
        cf.fit(ports, ducts, (0.0, 0.0, 0.0))


def test_override_pins_a_symmetric_fitting():
    ports = [
        cf.CustomPort("a", (-200.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
        cf.CustomPort("b", (200.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    ]
    ducts = [cf.DuctEnd("x", (-1.0, 0.0, 0.0)), cf.DuctEnd("y", (1.0, 0.0, 0.0))]
    assert cf.fit(ports, ducts, (0, 0, 0)).mapping == {"x": 0, "y": 1}
    flipped = cf.fit(ports, ducts, (0, 0, 0), overrides={"x": 1})
    assert flipped.mapping == {"x": 1, "y": 0}
    with pytest.raises(cf.CustomFitError):
        cf.fit(ports, ducts, (0, 0, 0), overrides={"x": 5})


def test_size_mismatch_is_a_warning():
    ducts = _ducts_for(np.eye(3))
    for d in ducts:
        d.profile, d.section = "Circular", {"Diameter": 200.0}
    result = cf.fit(_tee_ports(), ducts, (0.0, 0.0, 0.0))
    assert len(result.warnings) == 1 and "reducer" in result.warnings[0] and "C" in result.warnings[0]
