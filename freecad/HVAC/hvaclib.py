# -*- coding: utf-8 -*-
#***************************************************************************
#*                                                                         *
#*   Copyright (c) 2026 Francisco Rosa                                     *
#*                                                                         *
#*   Portions of code based on kbwbe's A2Plus Workbench                    *
#*                                                                         *
#*   This program is free software; you can redistribute it and/or modify  *
#*   it under the terms of the GNU Lesser General Public License (LGPL)    *
#*   as published by the Free Software Foundation; either version 2 of     *
#*   the License, or (at your option) any later version.                   *
#*   for detail see the LICENCE text file.                                 *
#*                                                                         *
#*   This program is distributed in the hope that it will be useful,       *
#*   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
#*   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
#*   GNU Library General Public License for more details.                  *
#*                                                                         *
#*   You should have received a copy of the GNU Library General Public     *
#*   License along with this program; if not, write to the Free Software   *
#*   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
#*   USA                                                                   *
#*                                                                         *
#***************************************************************************

import os, platform
import FreeCAD
import FreeCADGui as Gui
from PySide import QtGui
translate = FreeCAD.Qt.translate
preferences = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/HVAC")

WORKBENCH_STATE = 'DEFAULT'
DUCT_NETWORK_CONTEXT_KEY = "hvac_ductnetwork"

#------------------------------------------------------------------------------
# State management
#------------------------------------------------------------------------------

def activeHVACNetwork():
    doc = Gui.ActiveDocument

    if doc is None or doc.ActiveView is None:
        return None
    active_network = doc.ActiveView.getActiveObject(DUCT_NETWORK_CONTEXT_KEY)

    if active_network:
        return active_network

def allHVACNetworks():
    from freecad.HVAC.DuctNetwork import DuctNetwork
    doc = Gui.ActiveDocument

    hvac_networks = None
    if doc is None:
        return None
    if hasattr(doc.Document, "Objects"):
        hvac_networks = [n for n in doc.Document.Objects if hasattr(n, "Proxy") and isinstance(n.Proxy, DuctNetwork)]

    return hvac_networks

def selectedHVACNetworks():
    from freecad.HVAC.DuctNetwork import DuctNetwork
    objs = Gui.Selection.getSelection()
    if objs:
        filtered = [o for o in objs if hasattr(o, "Proxy") and isinstance(o.Proxy, DuctNetwork)]
        return filtered
    return None

def refreshState():
    QtGui.QApplication.processEvents()

#------------------------------------------------------------------------------
# Object query
#------------------------------------------------------------------------------

def obj_is_sketch(obj):
    # Robust check for Sketcher objects
    return hasattr(obj, "TypeId") and (
        obj.TypeId.startswith("Sketcher::SketchObject")
        or obj.TypeId.startswith("Sketcher::SketchObjectPython")
    )

def obj_is_wire(obj):
    # Draft Wire is usually Part::Feature (or FeaturePython) with Draft properties
    return (
        obj.TypeId == "Part::FeaturePython"
        and hasattr(obj, "Proxy")
        and hasattr(obj.Proxy, "Type")
        and getattr(obj.Proxy, "Type") == "Wire"
    )

def get_obj_name(obj):
    # Get object name from FreeCAD object
    return getattr(obj, "Name", "")

def get_obj_by_name(name, doc=None):
    # Get object by name from FreeCAD document
    if doc is None:
        doc = FreeCAD.ActiveDocument
    obj = doc.getObject(name)
    return obj

#------------------------------------------------------------------------------
# Object data manipulation
#------------------------------------------------------------------------------

def vec_to_xyz(v):
    """Return (x,y,z) tuple from a FreeCAD.Vector-like object."""
    return (float(v.x), float(v.y), float(v.z))


class DuctNetworkParser:

    def __init__(self, objs=None):
        self.lines_map = {}
        self.all_lines = []
        if objs:
            self.compile_lines_from_objects(objs)

    def compile_lines_from_objects(self, objs):
        self.lines_map = {}
        self.all_lines = []
        for obj in objs:
            if obj_is_wire(obj):
                for sp, ep in self.iter_line_segments_from_shape(obj):
                    self.parse_obj(obj, sp, ep)
            elif obj_is_sketch(obj):
                for sp, ep in self.iter_line_segments_from_sketch(obj):
                    self.parse_obj(obj, sp, ep)
        return self.lines_map, self.all_lines

    def parse_obj(self, obj, sp, ep):
        obj_name = getattr(obj, "Name", None)
        if obj_name:
            if obj_name not in self.lines_map:
                self.lines_map[obj_name] = []
            self.lines_map[obj_name].append((sp, ep))
            self.all_lines.append((sp, ep))

    def iter_line_segments_from_sketch(self, sketch_obj, tol=1e-9):
        """
        Yield (start_point, end_point) for all LINE segments in a Sketch.
        Sketch line geo is typically Part.LineSegment.
        """
        for geo in getattr(sketch_obj, "Geometry", []) or []:
            # Accept only straight line segments
            if hasattr(geo, "StartPoint") and hasattr(geo, "EndPoint"):
                # Filter out arcs/circles/etc by type
                # Part.LineSegment usually has TypeId or is instance of Part.LineSegment
                typeid = getattr(geo, "TypeId", "")
                if "Line" in typeid or (typeid == "" and geo.__class__.__name__ in ("LineSegment", "Line")):
                    sp = geo.StartPoint
                    ep = geo.EndPoint
                    # Skip degenerate lines
                    if (sp.sub(ep)).Length > tol:
                        yield (vec_to_xyz(sp), vec_to_xyz(ep))

    def iter_line_segments_from_shape(self, obj, tol=1e-9):
        """
        Yield (start_point, end_point) for all straight edges in obj.Shape.
        Works for Draft Wire (and many Part-based objects) as long as Shape exists.
        """
        shape = getattr(obj, "Shape", None)
        if shape is None:
            return
        for e in getattr(shape, "Edges", []) or []:
            c = getattr(e, "Curve", None)
            if c is None:
                continue
            typeid = getattr(c, "TypeId", "")
            # Straight edges typically have Part::GeomLine / GeomLine
            if "GeomLine" in typeid or c.__class__.__name__ in ("GeomLine",):
                v1 = e.Vertexes[0].Point
                v2 = e.Vertexes[-1].Point
                if (v1.sub(v2)).Length > tol:
                    yield (vec_to_xyz(v1), vec_to_xyz(v2))

#------------------------------------------------------------------------------
# Detect the operating system...
#------------------------------------------------------------------------------

tmp = platform.system()
tmp = tmp.upper()
tmp = tmp.split(' ')

OPERATING_SYSTEM = 'UNKNOWN'
if "WINDOWS" in tmp:
    OPERATING_SYSTEM = "WINDOWS"
elif "LINUX" in tmp:
    OPERATING_SYSTEM = "LINUX"
else:
    OPERATING_SYSTEM = "OTHER"

#------------------------------------------------------------------------------
# Return paths...
#------------------------------------------------------------------------------

def get_module_path():
    """Function returns HVAC module path."""
    s_path = os.path.dirname(os.path.abspath(__file__))
    return s_path

def get_file_path(file_name):
    """Function returns HVAC module path."""
    s_path = os.path.join(get_module_path(), file_name)
    return s_path

def get_language_base_path():
    """Function return path for localization files."""
    s_path = os.path.join(get_module_path(), "translations")
    return s_path

def get_icon_base_path():
    """Function return path for icon files."""
    s_path = os.path.join(get_module_path(), "icons")
    return s_path

def get_icon_path(icon_name: str):
    """Function returns path for icon file."""
    s_path = os.path.join(get_icon_base_path(), icon_name)
    return s_path

#------------------------------------------------------------------------------
# Miscellaneous
#------------------------------------------------------------------------------

def get_version():
    """
    Function return A2Plus version for storing in assembly file
    """

    hvac_path = get_module_path()
    try:
        metadata = FreeCAD.Metadata(os.path.join(hvac_path, 'package.xml'))
        return metadata.Version
    except:
        tx = ' ?? '
        return tx
