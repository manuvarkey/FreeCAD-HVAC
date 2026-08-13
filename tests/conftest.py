"""
Stub out the FreeCAD/FreeCADGui/Part/PySide modules that freecad.HVAC.core.*
imports at module scope, so those modules can be imported and unit-tested
without a real FreeCAD installation.

Only freecad.HVAC.core.airflow (tested in test_airflow.py) has no FreeCAD
dependency at all; everything else in this addon does, so any test that needs
freecad.HVAC.core.AirflowSolver relies on this stubbing.
"""

import sys
from unittest.mock import MagicMock

for _name in ("FreeCAD", "FreeCADGui", "Part", "PySide", "PySide.QtWidgets", "PySide.QtCore"):
    if _name not in sys.modules:
        sys.modules[_name] = MagicMock(name=_name)
sys.modules["FreeCAD"].Qt.translate = lambda *a, **k: (a[1] if len(a) > 1 else "")
