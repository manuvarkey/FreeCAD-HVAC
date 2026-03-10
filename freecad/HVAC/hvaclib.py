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

import os, platform, sys
from dataclasses import dataclass
import FreeCAD
import FreeCADGui as Gui
from PySide import QtGui, QtCore
translate = FreeCAD.Qt.translate
preferences = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/HVAC")

# Enable loading external libraries from the ext_libs directory
path = os.path.dirname(__file__)
vendor_path = os.path.join(path, "ext_libs")
# Add to sys.path if not already there
if vendor_path not in sys.path:
    sys.path.append(vendor_path)

# Load external libraries
import networkx as nx

WORKBENCH_NAME = 'HVAC'
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
    if not FreeCAD.GuiUp:
        return
    
    # Recompute document
    doc = FreeCAD.ActiveDocument
    if doc:
        FreeCAD.ActiveDocument.recompute()
    
    # Refresh TaskWatchers
    def _do_refresh():
        """Refresh HVAC task watchers after commands that change watcher conditions"""
        try:
            wb = Gui.activeWorkbench()
            if wb and hasattr(wb, "refreshWatchers"):
                wb.refreshWatchers()
        except Exception:
            pass
    
    QtCore.QTimer.singleShot(0, _do_refresh)
    

#------------------------------------------------------------------------------
# Object query
#------------------------------------------------------------------------------

def obj_is_sketch(obj):
    # Robust check for Sketcher objects
    try:
        return hasattr(obj, "TypeId") and (
            obj.TypeId.startswith("Sketcher::SketchObject")
            or obj.TypeId.startswith("Sketcher::SketchObjectPython")
        )
    except:
        return None

def obj_is_wire(obj):
    # Draft Wire is usually Part::Feature (or FeaturePython) with Draft properties
    try:
        return (
            obj.TypeId == "Part::FeaturePython"
            and hasattr(obj, "Proxy")
            and hasattr(obj.Proxy, "Type")
            and getattr(obj.Proxy, "Type") == "Wire"
        )
    except:
        return None

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

@dataclass(frozen=True)
class EdgeRef:
    """Stable reference to an edge created from (obj_name, local_line_index)."""
    obj_name: str
    local_index: int

class DuctNetworkParser:

    def __init__(self, objs=None):

        # Input line storage
        self.lines_map = {}   # Obj_Name -> [(sp, ep), ...]
        self.all_lines = []   # [(sp, ep), ...]

        # Graph storage (generated)
        self.tol = 1e-6
        self.node_id_by_key = {}
        self.node_point = {}      # node_id -> representative point
        self.edge_u_v = {}        # edge_ref -> (u, v)
        self.edge_geom = {}       # edge_ref -> (sp, ep)
        self.obj_edges = {}       # obj_name -> [edge_ref,...]

        # Optional networkx graph (recommended)
        self.graph = None

        # Build data structures
        if objs:
            self.compile_lines_from_objects(objs)
        self.build_graph()

    ## Data Parser Methods

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

    ## Graph build utilities

    def _key(self, p):
        """
        Collapse points by tolerance using quantization.
        Points within ~tol map to the same key.
        """
        t = float(self.tol)
        return (
            int(round(p[0] / t)),
            int(round(p[1] / t)),
            int(round(p[2] / t)),
        )

    def _get_node_id(self, p):
        k = self._key(p)
        nid = self.node_id_by_key.get(k)
        if nid is None:
            nid = len(self.node_id_by_key) + 1  # start node ids from 1
            self.node_id_by_key[k] = nid
            self.node_point[nid] = p
        return nid

    def build_graph(self, tol=1e-6):
        """
        Build a graph where:
            - nodes = junction points (collapsed by tol)
            - edges = duct centerlines (your lines)
        """
        self.tol = float(tol)

        # reset generated structures
        self.node_id_by_key.clear()
        self.node_point.clear()
        self.edge_u_v.clear()
        self.edge_geom.clear()
        self.obj_edges.clear()

        G = nx.Graph()

        for obj_name, lines in self.lines_map.items():
            for i, (sp, ep) in enumerate(lines):
                u = self._get_node_id(sp)
                v = self._get_node_id(ep)

                eref = EdgeRef(obj_name=obj_name, local_index=i)
                self.edge_u_v[eref] = (u, v)
                self.edge_geom[eref] = (sp, ep)
                self.obj_edges.setdefault(obj_name, []).append(eref)

                # Similar pattern to referenced build_graph_model(): add_edge with attributes.
                G.add_edge(
                    u, v,
                    key=eref,
                    obj=obj_name,
                    local_index=i,
                    sp=sp, ep=ep,
                )

        self.graph = G
        return G

    ## Convenience queries
    def node_count(self):
        return len(self.node_point)

    def edge_count(self):
        return len(self.edge_u_v)

    def nodes(self):
        return sorted(self.node_point.keys())

    def node_xyz(self, node_id):
        return self.node_point[node_id]

    def edges(self):
        return list(self.edge_u_v.keys())

    def edges_of_obj(self, obj_name):
        return list(self.obj_edges.get(obj_name, []))

    def edge_nodes(self, eref):
        return self.edge_u_v[eref]

    def edge_line(self, eref):
        return self.edge_geom[eref]

    def connected_components(self):
        """Return connected components as lists of node_ids."""
        if self.graph is None:
            raise RuntimeError("Graph not built. Call build_graph() first.")
        return [sorted(list(c)) for c in nx.connected_components(self.graph)]

    def shortest_path_by_points(self, p1, p2):
        """
        Path between two geometric points (snapped by tol).
        Returns node_id path.
        """
        if self.graph is None:
            raise RuntimeError("Graph not built. Call build_graph() first.")

        n1 = self.node_id_by_key[self._key(p1)]
        n2 = self.node_id_by_key[self._key(p2)]
        return nx.shortest_path(self.graph, n1, n2)


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
