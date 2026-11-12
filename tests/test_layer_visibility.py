"""Stage 9 item 2: per-network layer and feature visibility rules."""

from types import SimpleNamespace

import conftest  # noqa: F401 -- installs FreeCAD/Materials stubs
from freecad.HVAC.core import _component_appearance as appearance
from freecad.HVAC.library.construction import ALL_LAYER_ROLES


def test_everything_shown_without_a_network():
    shown, features = appearance.network_display_flags(None)
    assert shown == set(ALL_LAYER_ROLES) and features is True


def test_flags_follow_the_network_properties():
    net = SimpleNamespace(ShowLayer_ThermalInsulation=False, ShowFeatures=False)
    shown, features = appearance.network_display_flags(net)
    assert "thermal_insulation" not in shown and "structural_shell" in shown
    assert features is False


def test_layer_with_any_shown_role_is_drawn():
    shown = set(ALL_LAYER_ROLES) - {"flow_surface"}
    assert appearance.layer_shown(["flow_surface", "structural_shell"], shown)
    assert not appearance.layer_shown(["flow_surface"], shown)
    assert appearance.layer_shown([], set())  # a roleless layer is always drawn
