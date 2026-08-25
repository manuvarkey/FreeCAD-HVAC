"""
Structural checks on the .FCMat cards this addon ships under
freecad/HVAC/Resources/Materials/ -- verified to actually parse and load
correctly against a real FreeCAD 1.1 install during development (see
ARCHITECTURE.md's "Component geometry & materials" section); these tests
run without a real FreeCAD/Materials module, so they check the on-disk YAML
structure directly (regex-based -- this addon has no YAML dependency
otherwise, and .FCMat parsing itself is entirely FreeCAD's own job, not
something this addon should reimplement).
"""

import os
import re

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/Materials/PySide stubs

from freecad.HVAC.utils import hvaclib

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

# The standard FreeCAD model UUIDs every card must reuse (see
# utils/materials.py's module docstring) -- confirmed against a real
# FreeCAD 1.1 install's Resources/Models/*.yml.
_FATHER_MODEL_UUID = "9cdda8b6-b606-4778-8f13-3934d8668e67"
_DENSITY_MODEL_UUID = "454661e5-265b-4320-8e6f-fcf6223ac3af"
_THERMAL_MODEL_UUID = "9959d007-a970-4ea7-bae4-3eb1b8b883c7"
_BASIC_RENDERING_MODEL_UUID = "f006c7e4-35b7-43d5-bbf9-c5d572309e6e"

_METAL_CARDS = [
    "Metal/Galvanized-Steel.FCMat",
    "Metal/Aluminium.FCMat",
    "Metal/Stainless-Steel.FCMat",
    "Metal/Perforated-Galvanized-Steel.FCMat",
]
_INSULATION_CARDS = [
    "Insulation/Glass-Wool.FCMat",
    "Insulation/Rock-Wool.FCMat",
    "Insulation/Nitrile-Rubber.FCMat",
    "Insulation/Polyurethane-Foam.FCMat",
    "Insulation/Expanded-Polystyrene.FCMat",
    "Insulation/Nitrile-Rubber-Open-Cell.FCMat",
]
_EXPECTED_CARDS = _METAL_CARDS + _INSULATION_CARDS


def _materials_root():
    return hvaclib.get_materials_base_path()


def _read(relative_path):
    with open(os.path.join(_materials_root(), relative_path), "r", encoding="utf-8") as handle:
        return handle.read()


def _field(text, name):
    """Pull a simple 'Name: "value"' or 'Name: value' scalar out of the card text."""
    match = re.search(r'^\s*{}:\s*"?([^"\n]+?)"?\s*$'.format(re.escape(name)), text, re.MULTILINE)
    return match.group(1) if match else None


def test_all_expected_cards_exist():
    for relative_path in _EXPECTED_CARDS:
        assert os.path.isfile(os.path.join(_materials_root(), relative_path)), relative_path


def test_every_card_has_a_unique_valid_uuid():
    uuids = []
    for relative_path in _EXPECTED_CARDS:
        text = _read(relative_path)
        uuid = _field(text, "UUID")
        assert uuid and _UUID_RE.fullmatch(uuid), "{}: bad UUID {!r}".format(relative_path, uuid)
        uuids.append(uuid.lower())
    assert len(uuids) == len(set(uuids)), "duplicate UUIDs across shipped cards"


def test_every_card_reuses_standard_models_not_a_custom_schema():
    for relative_path in _EXPECTED_CARDS:
        text = _read(relative_path)
        assert _FATHER_MODEL_UUID in text, relative_path
        assert _DENSITY_MODEL_UUID in text, relative_path
        assert _THERMAL_MODEL_UUID in text, relative_path


def test_every_card_declares_density_and_thermal_properties_with_units():
    for relative_path in _EXPECTED_CARDS:
        text = _read(relative_path)
        assert re.search(r"Density:\s*\"[\d.]+\s*kg/m\^3\"", text), relative_path
        assert re.search(r"ThermalConductivity:\s*\"[\d.]+\s*W/m/K\"", text), relative_path
        assert re.search(r"SpecificHeat:\s*\"[\d.]+\s*J/kg/K\"", text), relative_path


def test_every_card_declares_a_basic_rendering_appearance():
    for relative_path in _EXPECTED_CARDS:
        text = _read(relative_path)
        assert _BASIC_RENDERING_MODEL_UUID in text, relative_path
        assert re.search(r"DiffuseColor:\s*\"\([^)]+\)\"", text), relative_path


def test_metal_cards_are_filed_under_metal_and_insulation_cards_under_insulation():
    for relative_path in _METAL_CARDS:
        assert 'Father: "Metal"' in _read(relative_path), relative_path

    for relative_path in _INSULATION_CARDS:
        assert 'Father: "Insulation"' in _read(relative_path), relative_path


def test_insulation_cards_are_60_percent_transparent_so_ducts_show_through():
    for relative_path in _INSULATION_CARDS:
        assert re.search(r'Transparency:\s*"0\.6"', _read(relative_path)), relative_path

    for relative_path in _METAL_CARDS:
        assert re.search(r'Transparency:\s*"0\.0"', _read(relative_path)), relative_path


def test_no_hvac_specific_material_schema_file_shipped_alongside_cards():
    # This addon must not invent its own material JSON/schema -- every
    # card is a plain, self-contained .FCMat FreeCAD already understands.
    for dirpath, _dirs, filenames in os.walk(_materials_root()):
        for filename in filenames:
            assert filename.endswith(".FCMat"), os.path.join(dirpath, filename)
