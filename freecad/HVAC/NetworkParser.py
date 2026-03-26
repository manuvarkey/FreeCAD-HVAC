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

from dataclasses import dataclass
import math

import FreeCAD

from . import hvaclib
from .hvaclib import (
    isWire,
    isSketch,
    vec_to_xyz,
    nx
)


@dataclass(frozen=True)
class EdgeRef:
    """Stable reference to an edge created from (obj_name, local_line_index)."""
    obj_name: str
    local_index: int
    tag: str


@dataclass
class JunctionPort:
    """
    Generic junction port descriptor.

    edge_key      : stable segment key, e.g. "Sketch001:0"
    segment_end   : "start" or "end" relative to the connected segment
    direction     : unit vector pointing away from the junction along the segment
    profile       : segment profile string, e.g. "Circular", "Rectangular"
    section_params: generic profile-dependent section data
    """
    edge_key: str
    segment_end: str
    position: tuple
    direction: tuple
    profile: str
    section_params: dict
    attachment: str
    user_offset: tuple
    profile_x_axis: tuple | None = None


class DuctNetworkParser:

    def __init__(self, objs=None):

        # Input line storage
        self.lines_map = {}   # Obj_Name -> [(sp, ep, tag), ...]
        self.all_lines = []   # [(sp, ep, tag), ...]

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

    def _key(self, p):
        """
        Collapse points by tolerance using quantization.
        Points within ~tol map to the same key.
        """
        return hvaclib.vec_quant(p)

    def _get_node_id(self, p):
        k = self._key(p)
        nid = self.node_id_by_key.get(k)
        if nid is None:
            nid = len(self.node_id_by_key) + 1  # start node ids from 1
            self.node_id_by_key[k] = nid
            self.node_point[nid] = p
        return nid

    def _segment_end_for_node(self, edge_ref, node_id):
        """
        Return 'start' if node_id is the start node of edge_ref,
        'end' if it is the end node.
        """
        start_node, end_node = self.edge_nodes(edge_ref)
        if node_id == start_node:
            return "start"
        if node_id == end_node:
            return "end"
        return ""

    def _parse_obj(self, obj, sp, ep, tag):
        obj_name = getattr(obj, "Name", None)
        if obj_name:
            if obj_name not in self.lines_map:
                self.lines_map[obj_name] = []
            self.lines_map[obj_name].append((sp, ep, tag))
            self.all_lines.append((sp, ep, tag))

    def _iter_line_segments_from_sketch(self, sketch_obj, tol=1e-9):
        """
        Yield (start_point, end_point, tag) for all non-construction LINE segments in a Sketch.
        """
        from .DuctNetwork import DuctSegment

        geos = getattr(sketch_obj, "Geometry", []) or []
        for slno, geo in enumerate(geos):
            # Skip construction geometry
            try:
                if sketch_obj.getConstruction(slno):
                    continue
            except Exception:
                pass
            # Accept only straight line segments
            if hasattr(geo, "StartPoint") and hasattr(geo, "EndPoint"):
                typeid = getattr(geo, "TypeId", "")
                if "Line" in typeid or (typeid == "" and geo.__class__.__name__ in ("LineSegment", "Line")):
                    sp = geo.StartPoint
                    ep = geo.EndPoint
                    # Skip degenerate lines
                    if (sp.sub(ep)).Length > tol:
                        tag = hvaclib.makeLineKey(sketch_obj.Name, slno)
                        yield (vec_to_xyz(sp), vec_to_xyz(ep), tag)

    def _iter_line_segments_from_shape(self, obj, tol=1e-9):
        """
        Yield (start_point, end_point) for all straight edges in obj.Shape.
        Works for Draft Wire (and many Part-based objects) as long as Shape exists.
        """
        from .DuctNetwork import DuctSegment

        shape = getattr(obj, "Shape", None)
        if shape is None:
            return
        for slno, e in enumerate(getattr(shape, "Edges", []) or []):
            c = getattr(e, "Curve", None)
            if c is None:
                continue
            typeid = getattr(c, "TypeId", "")
            # Straight edges typically have Part::GeomLine / GeomLine
            if "GeomLine" in typeid or c.__class__.__name__ in ("GeomLine",):
                v1 = e.Vertexes[0].Point
                v2 = e.Vertexes[-1].Point
                if (v1.sub(v2)).Length > tol:
                    tag = hvaclib.makeLineKey(obj.Name, slno)
                    yield (vec_to_xyz(v1), vec_to_xyz(v2), tag)

    ## Graph build utilities
    
    def compile_lines_from_objects(self, objs):
        self.lines_map = {}
        self.all_lines = []
        for obj in objs:
            if isWire(obj):
                for sp, ep, tag in self._iter_line_segments_from_shape(obj):
                    self._parse_obj(obj, sp, ep, tag)
            elif isSketch(obj):
                for sp, ep, tag in self._iter_line_segments_from_sketch(obj):
                    self._parse_obj(obj, sp, ep, tag)
        return self.lines_map, self.all_lines

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
            for i, (sp, ep, tag) in enumerate(lines):
                u = self._get_node_id(sp)
                v = self._get_node_id(ep)

                eref = EdgeRef(obj_name=obj_name, local_index=i, tag=tag)
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

    ## Junction/ port building

    def build_junction_ports(self, node_id, edge_refs, segment_map=None):
        """
        Build generic port descriptors for a junction node.

        segment_map:
            dict { segment_key : DuctSegment object }
        """
        ports = []
        segment_map = segment_map or {}

        node_point = FreeCAD.Vector(*self.node_xyz(node_id))

        for edge_ref in edge_refs:
            edge_key = edge_ref.tag
            segment_end = self._segment_end_for_node(edge_ref, node_id)
            if segment_end not in ("start", "end"):
                continue

            sp, ep = self.edge_line(edge_ref)
            sp_vec = FreeCAD.Vector(*sp)
            ep_vec = FreeCAD.Vector(*ep)

            # Direction points away from the junction along the connected segment
            if segment_end == "start":
                other = ep_vec
            else:
                other = sp_vec

            direction_port_ref = other - node_point
            direction_seg_ref = ep_vec - sp_vec
            if direction_port_ref.Length <= 1e-9:
                continue
            direction_port_ref.normalize()

            seg_obj = segment_map.get(edge_key)

            if seg_obj:
                section_params = hvaclib.get_segment_section_params(seg_obj)
                profile = getattr(seg_obj, "Profile", "")
                attachment = getattr(seg_obj, "Attachment", "Center")
                user_offset = getattr(seg_obj, "Offset", FreeCAD.Vector(0,0,0))
                profile_x = getattr(seg_obj, "ProfileXAxis", FreeCAD.Vector(0, 0, 0))
            else:
                section_params = {}
                profile = ""
                attachment = "Center"
                user_offset = FreeCAD.Vector(0,0,0)
                profile_x = FreeCAD.Vector(0,0,0)

            base_point = FreeCAD.Vector(node_point)  # parser node position

            final_pos = hvaclib.compute_port_position(
                base_point,
                direction_seg_ref,  # Use segment reference for computation of port position
                section_params,
                attachment,
                user_offset,
                profile_x
            )

            ports.append(JunctionPort(
                edge_key = edge_key,
                segment_end = segment_end,
                position = vec_to_xyz(final_pos),
                direction = vec_to_xyz(direction_port_ref),  # Use port reference convention
                profile = profile,
                section_params = section_params,
                attachment = attachment,
                user_offset = vec_to_xyz(user_offset),
                profile_x_axis = vec_to_xyz(profile_x) if profile_x.Length > 1e-12 else None
            ))

        return ports

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

    def node_key(self, node_id):
        """Return persistent snapped-key for a node."""
        edge_refs = self.node_edges(node_id)

        if len(edge_refs) == 0:
            return self._key(self.node_point[node_id])

        elif len(edge_refs) == 1:
            u, v = self.edge_u_v[edge_refs[0]]
            if node_id == u:
                label = 's'
            elif node_id == v:
                label = 'e'
            else:
                return self._key(self.node_point[node_id])
            return edge_refs[0].tag + "_" + label

        else:
            return "_".join([ref.tag for ref in edge_refs])

    def node_degree(self, node_id):
        if self.graph is None:
            raise RuntimeError("Graph not built. Call build_graph() first.")
        return int(self.graph.degree[node_id])

    def node_edges(self, node_id):
        """
        Return EdgeRef objects incident to a node.
        """
        refs = []
        for eref, (u, v) in self.edge_u_v.items():
            if u == node_id or v == node_id:
                refs.append(eref)
        return refs

    def node_kind(self, node_id):
        d = self.node_degree(node_id)
        if d <= 0:
            return "isolated"
        if d == 1:
            return "terminal"
        if d == 2:
            return "transition"
        if d == 3:
            return "tee"
        if d == 4:
            return "cross"
        return "manifold"

    def node_vectors(self, node_id):
        p = FreeCAD.Vector(*self.node_xyz(node_id))
        out = []
        for eref in self.node_edges(node_id):
            sp, ep = self.edge_line(eref)
            v1 = FreeCAD.Vector(*sp)
            v2 = FreeCAD.Vector(*ep)
            other = v2 if (v1.sub(p)).Length <= self.tol else v1
            d = other.sub(p)
            if d.Length > self.tol:
                d.normalize()
                out.append((eref, d))
        return out

    def _safe_angle_deg(self, a, b):
        dot = max(-1.0, min(1.0, float(a.dot(b))))
        return math.degrees(math.acos(dot))

    def node_collinear_pairs(self, node_id, ang_tol_deg=2.0):
        vecs = self.node_vectors(node_id)
        pairs = []
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                ai = vecs[i][1]
                bj = vecs[j][1]
                ang = self._safe_angle_deg(ai, bj)
                if abs(ang - 180.0) <= ang_tol_deg:
                    pairs.append((vecs[i][0], vecs[j][0], ang))
        return pairs

    def node_analysis(self, node_id, ang_tol_deg=2.0, ortho_tol_deg=10.0):
        degree = self.node_degree(node_id)
        edge_refs = self.node_edges(node_id)
        vecs = self.node_vectors(node_id)
        collinear_pairs = self.node_collinear_pairs(node_id, ang_tol_deg=ang_tol_deg)

        orthogonal_pairs = []
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                ang = self._safe_angle_deg(vecs[i][1], vecs[j][1])
                if abs(ang - 90.0) <= ortho_tol_deg:
                    orthogonal_pairs.append((vecs[i][0], vecs[j][0], ang))

        return {
            "node_id": int(node_id),
            "node_key": self.node_key(node_id),
            "point": self.node_xyz(node_id),
            "degree": degree,
            "edge_refs": edge_refs,
            "collinear_pairs": collinear_pairs,
            "orthogonal_pairs": orthogonal_pairs,
        }
