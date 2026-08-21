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

"""This module implements HVAC duct description classes."""
import json
import traceback

import FreeCAD, Part
import FreeCADGui as Gui
from PySide import QtWidgets, QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from ..utils import hvaclib
from ..library.library_api import HVACLibraryAPI


def _json_safe_port(port):
    """
    Return a copy of a port dict safe to json.dumps: any FreeCAD.Vector
    field (as produced by HVACLibraryAPI.copy_port) is converted back to a
    plain (x, y, z) tuple. Fields that already came straight out of
    AnalysisJson (parsed JSON) are plain lists/tuples already and pass
    through unchanged.
    """
    out = dict(port)
    for key in ("position", "direction", "profile_x_axis", "user_offset", "flow_direction"):
        value = out.get(key)
        if value is not None and hasattr(value, "x"):
            out[key] = HVACLibraryAPI.xyz(value)
    return out


class DuctJunction:
    """
    Derived per-node network junction: a purely logical/connectivity
    container. It holds no library type or geometry of its own -- each
    physical fitting it represents is a separate DuctComponent child (see
    core/Component.py), found via getComponents()/getPrimaryComponent().

    A junction's only "compute" job is composing its component chain: for
    the common case (a single Primary, no Inline components) each
    component just gets the junction's real connected ports unchanged --
    identical to how a single fitting worked before this class was split.
    For a simple through/2-port node carrying one or more Inline
    components too, composeComponents() works out each component's local
    inlet/outlet ports in inlet->outlet order and writes them to that
    component's LocalPortsJson (see below).
    """

    TYPE = "DuctJunction"

    def __init__(
        self,
        obj,
        owner=None,
        node_id=0,
        node_key="",
        center_point=None,
        degree=0,
        topology=""
    ):
        obj.Proxy = self
        self.Object = obj
        self._allow_delete = False
        self.setProperties(obj)
        self.updateMetadata(
            owner=owner,
            node_id=node_id,
            node_key=node_key,
            center_point=center_point,
            degree=degree,
            topology=topology
        )

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self.Object = obj
        self._allow_delete = False
        self.setProperties(obj)

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def execute(self, obj):
        # A DuctJunction has no geometry of its own -- every physical
        # fitting is generated independently by its DuctComponent children
        # (see Component.py's own execute()). Nothing to do here.
        pass

    def setProperties(self, obj):
        self._addProperty(obj, "App::PropertyString", "OwnerNetworkName", "HVAC", "Owning duct network")
        self._addProperty(obj, "App::PropertyInteger", "NodeId", "HVAC", "Parser node id")
        self._addProperty(obj, "App::PropertyString", "NodeKey", "HVAC", "Persistent snapped node key")
        self._addProperty(obj, "App::PropertyVector", "CenterPoint", "HVAC", "Junction center point")
        self._addProperty(obj, "App::PropertyInteger", "Degree", "HVAC", "Node degree")
        self._addProperty(obj, "App::PropertyString", "Topology", "HVAC", "Junction topology")
        self._addProperty(obj, "App::PropertyString", "Family", "HVAC", "Classified fitting family")

        self._addProperty(obj, "App::PropertyStringList", "ConnectedEdgeKeys", "HVAC", "Connected segment keys")
        self._addProperty(obj, "App::PropertyString", "ConnectionLengthsJson", "HVAC", "Aggregate per-edge connection lengths (from the outermost component on each side)")
        self._addProperty(obj, "App::PropertyString", "AnalysisJson", "HVAC", "Serialized topology analysis")

        self._addProperty(obj, "App::PropertyFloat", "DesignFlowRate", "Airflow", "User-specified design flow rate for this terminal (L/s). Leave blank/0 on exactly one terminal per sub-network to solve it as the balancing terminal.")
        self._addProperty(obj, "App::PropertyFloat", "CalcTotalFlowRate", "Airflow", "Computed total flow through this junction (L/s)")
        self._addProperty(obj, "App::PropertyFloat", "CalcStaticPressure", "Airflow", "Computed relative static pressure (Pa), referenced to 0 Pa at this sub-network's balancing terminal")
        self._addProperty(obj, "App::PropertyBool", "IsFlowSource", "Airflow", "True if flow physically leaves the system at this terminal (a supply/source opening)")
        self._addProperty(obj, "App::PropertyString", "CalcLossWarning", "Airflow", "Non-fatal warning from the last calculation (e.g. fallback loss coefficient used)")

        for prop in ("CalcTotalFlowRate", "CalcStaticPressure", "IsFlowSource", "CalcLossWarning"):
            try:
                obj.setEditorMode(prop, 1)
            except Exception:
                pass

        if not getattr(obj, "ConnectionLengthsJson", ""):
            obj.ConnectionLengthsJson = "[]"

        if not getattr(obj, "AnalysisJson", ""):
            obj.AnalysisJson = "{}"

        for prop in (
            "OwnerNetworkName",
            "NodeId",
            "NodeKey",
            "CenterPoint",
            "Degree",
            "Topology",
            "Family",
            "ConnectedEdgeKeys",
            "ConnectionLengthsJson",
            "AnalysisJson",
        ):
            try:
                obj.setEditorMode(prop, 1)
            except Exception:
                pass

    def updateMetadata(
        self,
        owner=None,
        node_id=0,
        node_key="",
        center_point=None,
        degree=0,
        topology="",
        family="",
        connected_edge_keys=None,
        analysis_json="{}",
    ):
        obj = self.Object
        changed = False

        if owner and getattr(obj, "OwnerNetworkName", "") != owner.Name:
            obj.OwnerNetworkName = owner.Name
            changed = True

        if getattr(obj, "NodeId", None) != int(node_id):
            obj.NodeId = int(node_id)
            changed = True

        if getattr(obj, "NodeKey", "") != str(node_key):
            obj.NodeKey = str(node_key)
            changed = True

        if center_point is not None:
            center_vec = FreeCAD.Vector(*center_point)
            if obj.CenterPoint != center_vec:
                obj.CenterPoint = center_vec
                changed = True

        if getattr(obj, "Degree", None) != int(degree):
            obj.Degree = int(degree)
            changed = True

        if getattr(obj, "Topology", "") != str(topology):
            obj.Topology = str(topology)
            changed = True

        try:
            obj.setEditorMode("DesignFlowRate", 0 if getattr(obj, "Topology", "") == "end" else 1)
        except Exception:
            pass

        if family and getattr(obj, "Family", "") != str(family):
            obj.Family = str(family)
            changed = True

        if connected_edge_keys is not None:
            edge_keys = [str(k) for k in connected_edge_keys]
            if list(getattr(obj, "ConnectedEdgeKeys", []) or []) != edge_keys:
                obj.ConnectedEdgeKeys = edge_keys
                changed = True

        if analysis_json is not None and getattr(obj, "AnalysisJson", "") != str(analysis_json):
            obj.AnalysisJson = str(analysis_json)
            changed = True

        return changed

    # ------------------------------------------------------------------
    # Component chain: lookup, composition, trim aggregation
    # ------------------------------------------------------------------

    def getComponents(self):
        """This junction's DuctComponent children, Sequence-ascending."""
        obj = self.Object
        net = hvaclib.getOwnerNetwork(obj)
        geometry = getattr(net, "Geometry", None) if net is not None else None
        if geometry is None:
            return []
        out = [
            c for c in geometry.OutList
            if hvaclib.isDuctComponent(c) and getattr(c, "ParentJunctionName", "") == obj.Name
        ]
        out.sort(key=lambda c: int(getattr(c, "Sequence", 0)))
        return out

    def getPrimaryComponent(self):
        for c in self.getComponents():
            if getattr(c, "ComponentRole", "") == "Primary":
                return c
        return None

    def composeComponents(self):
        """
        Work out every child component's local inlet/outlet ports and write
        them to each component's LocalPortsJson, in Sequence order. Called
        once per sync (Network.syncJunctionComponents), before the
        recompute that runs each component's own execute().

        Everything stays in absolute world coordinates -- DuctJunction/
        DuctSegment never use Placement, and this keeps that convention
        rather than introducing a local frame.

        Single-component case (the common one: a plain fitting, or any
        junction that isn't a simple through/2-port node): each component
        just gets the junction's real connected_ports unchanged -- exactly
        the context a single fitting always received.

        Multi-component case (through/2-port node, 2+ components): builds
        an ordered chain from the real inlet port through to the real
        outlet port. Every existing 2-port geometry backend (elbow,
        transition, damper, VAV) treats its two given ports as coincident
        at one shared anchor point and independently pushes each one
        outward, in that port's own direction, by a trim length that
        depends only on the component's own properties/profile -- never on
        where that anchor happens to sit in space. That position-
        independence is what makes this composition possible: each
        component's own (left trim, right trim) can be read once from a
        "peek" geometry call (position doesn't matter yet), then the real
        anchor for every component is derived from a single running sum of
        (this component's own outward push + the next component's own
        outward push) along the appropriate side's direction -- see
        _peekComponentTrims. The real anchor for the very first component
        is exactly the real inlet port's own position, so the upstream
        segment's trim comes out identical to today's single-fitting
        behavior; every other anchor is purely derived, so adding,
        removing, or reordering Inline components never moves the
        junction's own CenterPoint or the real inlet segment's trim --
        only how far the chain reaches toward the real outlet, i.e. that
        segment's own trim.
        """
        obj = self.Object
        try:
            analysis = json.loads(getattr(obj, "AnalysisJson", "") or "{}")
        except Exception:
            analysis = {}
        ports = list(analysis.get("connected_ports", []) or [])
        components = self.getComponents()

        eligible = getattr(obj, "Topology", "") == "through" and len(ports) == 2 and len(components) > 1
        if not eligible:
            primary = self.getPrimaryComponent()
            if primary is not None:
                primary.LocalPortsJson = json.dumps(ports)
                primary.Profile = hvaclib.HVACLibraryService.match_profile_from_ports(ports)
            return

        port_a, port_b = ports[0], ports[1]
        if port_a.get("flow_into_junction") is False and port_b.get("flow_into_junction") is True:
            port_a, port_b = port_b, port_a
        # port_a: real inlet-facing port. port_b: real outlet-facing port.

        primary_index = next(
            (i for i, c in enumerate(components) if getattr(c, "ComponentRole", "") == "Primary"),
            0,
        )

        pos_a = HVACLibraryAPI.vec(port_a["position"])
        dir_a = HVACLibraryAPI.unit(port_a["direction"])
        dir_b = HVACLibraryAPI.unit(port_b["direction"])
        step_upstream = dir_a * -1.0   # advancing from port_a, deeper into the junction
        step_downstream = dir_b        # advancing from the junction, out toward port_b

        node_key = getattr(obj, "NodeKey", "")

        # Pass 1: work out each component's own local left/right port
        # templates (direction/profile/edge_key -- all position-
        # independent) and peek its own (trim_left, trim_right).
        left_tpls = []
        right_tpls = []
        trims_left = []
        trims_right = []
        for i, comp_obj in enumerate(components):
            is_first = (i == 0)
            is_last = (i == len(components) - 1)

            if is_first:
                left_tpl = dict(port_a)
            else:
                left_carrier = port_a if i <= primary_index else port_b
                left_dir = dir_a if i <= primary_index else (dir_b * -1.0)
                left_tpl = HVACLibraryAPI.copy_port(
                    left_carrier, position=pos_a, direction=left_dir,
                    edge_key="{}#seam{}".format(node_key, i - 1), segment_end="end",
                )
                # A synthetic seam's LEFT side is always this component's
                # own local inlet, regardless of which real port's
                # profile/section it happens to carry -- loss functions key
                # off flow_into_junction, not edge_key, to find "the inlet".
                left_tpl["flow_into_junction"] = True
                left_tpl["flow_role"] = "inlet"

            if is_last:
                right_tpl = dict(port_b)
            else:
                right_carrier = port_b if i >= primary_index else port_a
                right_dir = dir_b if i >= primary_index else (dir_a * -1.0)
                right_tpl = HVACLibraryAPI.copy_port(
                    right_carrier, position=pos_a, direction=right_dir,
                    edge_key="{}#seam{}".format(node_key, i), segment_end="start",
                )
                right_tpl["flow_into_junction"] = False
                right_tpl["flow_role"] = "outlet"

            left_tpls.append(left_tpl)
            right_tpls.append(right_tpl)
            tl, tr = self._peekComponentTrims(comp_obj, left_tpl, right_tpl, analysis)
            trims_left.append(tl)
            trims_right.append(tr)

        # Pass 2: derive each component's real shared anchor from a running
        # sum -- the first component is anchored exactly at the real inlet
        # port, every other anchor follows from the previous component's
        # own outward push plus this component's own outward push, along
        # whichever side's direction the transition happens on.
        anchors = [pos_a]
        for i in range(len(components) - 1):
            step = step_upstream if i < primary_index else step_downstream
            anchors.append(anchors[i] + step * (trims_right[i] + trims_left[i + 1]))

        # Pass 3: write each component's final local ports at its real anchor.
        for i, comp_obj in enumerate(components):
            local_ports = [
                HVACLibraryAPI.copy_port(left_tpls[i], position=anchors[i]),
                HVACLibraryAPI.copy_port(right_tpls[i], position=anchors[i]),
            ]
            comp_obj.LocalPortsJson = json.dumps([_json_safe_port(p) for p in local_ports])
            comp_obj.Profile = str(local_ports[1].get("profile", "") or "")

        # Pass 4: write the aggregate external trim contract directly. The
        # upstream real segment's trim is simply the first component's own
        # left push (its anchor is exactly pos_a, so nothing accumulates
        # ahead of it). The downstream real segment's trim is NOT just the
        # last component's own local push -- that alone only accounts for
        # its own body and silently ignores everything upstream of it in
        # the chain. It has to be the distance from the real port_b
        # position to where the chain's actual final face ends up, found
        # directly from the last component's own real anchor + push
        # (this stays correct even with a bent Primary, since it's a plain
        # position difference, not a re-derivation through intermediate
        # anchors).
        last_right_face = anchors[-1] + dir_b * trims_right[-1]
        trim_b = max(0.0, (last_right_face - HVACLibraryAPI.vec(port_b["position"])).dot(dir_b))
        trim_a = max(0.0, trims_left[0])

        aggregate = [
            {"edge_key": port_a.get("edge_key"), "segment_end": port_a.get("segment_end"), "length": trim_a},
            {"edge_key": port_b.get("edge_key"), "segment_end": port_b.get("segment_end"), "length": trim_b},
        ]
        aggregate_json = json.dumps(aggregate)
        if getattr(obj, "ConnectionLengthsJson", "") != aggregate_json:
            obj.ConnectionLengthsJson = aggregate_json

    def _peekComponentTrims(self, comp_obj, left_tpl, right_tpl, analysis):
        """
        Ask this component's own geometry backend how far it pushes out
        past each of its two given (coincident) ports, purely to learn its
        own (trim_left, trim_right) -- the shape itself is discarded here;
        execute() (run right after composeComponents, via touch() +
        recompute()) builds the real Shape with the exact same call using
        the final anchor positions. Calling build_geometry twice per
        component per sync is a deliberate, bounded cost -- see
        composeComponents()'s docstring; it keeps execute() as the single
        source of truth for Shape rather than caching a result across the
        sync/recompute boundary.

        Returns (0.0, 0.0) if the component has no type selected yet, or
        its geometry backend fails/reports nothing for a given side.
        """
        library_id = getattr(comp_obj, "LibraryId", "")
        type_id = getattr(comp_obj, "TypeId", "")
        if not library_id or not type_id:
            return 0.0, 0.0

        try:
            reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
            type_def = reg.resolve_type(library_id, type_id)
            if type_def is None:
                return 0.0, 0.0
            params = reg.resolve_params(type_def, obj=comp_obj)
            local_ports = [left_tpl, right_tpl]
            context = {
                "obj": comp_obj,
                "center_point": HVACLibraryAPI.average_point([p["position"] for p in local_ports]),
                "params": params,
                "connected_ports": local_ports,
                # Real topology analysis of this junction (unaffected by
                # chain composition) -- harmless/correct to pass through
                # even though no current through-topology generator reads
                # it, for consistency with DuctComponent.execute()'s own
                # context (see there for why this matters, e.g. build_tee).
                "analysis": analysis,
                # DuctComponent has no Family of its own -- only a Primary's
                # family-driven dispatch (e.g. through_generic) needs it,
                # and this junction (self.Object) IS the parent, so read it
                # directly rather than looking anything up.
                "family": getattr(self.Object, "Family", "") if getattr(comp_obj, "ComponentRole", "") == "Primary" else "",
                "topology": "through",
                "type_id": type_id,
                "library_id": library_id,
            }
            result = reg.build_geometry(library_id, type_def, context)
        except Exception:
            return 0.0, 0.0

        lengths = result.get("connection_lengths", []) or []

        def _find(port):
            for item in lengths:
                if item.get("edge_key") == port.get("edge_key") and item.get("segment_end") == port.get("segment_end"):
                    try:
                        return max(0.0, float(item.get("length", 0.0) or 0.0))
                    except Exception:
                        return 0.0
            return 0.0

        return _find(left_tpl), _find(right_tpl)

    def aggregateConnectionLengths(self):
        """
        Build the external trim contract (spec: DuctJunction.
        ConnectionLengthsJson is the only network-facing trimming contact)
        for the single-component case: a plain fitting, or any node that
        isn't a through/2-port chain (branch/cross/multiport/end). The
        single component's own reported connection lengths already are the
        correct, real-anchor-relative trims -- no aggregation math needed,
        just a passthrough filtered to this junction's real
        ConnectedEdgeKeys.

        A multi-component through/2-port chain's aggregate trim is instead
        computed and written directly by composeComponents() (its Pass 4)
        -- that needs the exact cumulative anchor geometry, which isn't
        recoverable from the outermost component's own post-execute
        ConnectionLengthsJson alone (that value is relative to its own
        local anchor, not the true distance from the real boundary port),
        so this method leaves it alone here.
        """
        obj = self.Object
        components = self.getComponents()
        if not components:
            return

        if getattr(obj, "Topology", "") == "through" and len(components) > 1:
            return

        first, last = components[0], components[-1]
        try:
            first_lengths = json.loads(getattr(first, "ConnectionLengthsJson", "") or "[]")
        except Exception:
            first_lengths = []
        try:
            last_lengths = json.loads(getattr(last, "ConnectionLengthsJson", "") or "[]")
        except Exception:
            last_lengths = []

        real_edge_keys = set(getattr(obj, "ConnectedEdgeKeys", []) or [])
        combined = list(first_lengths) + (list(last_lengths) if last is not first else [])
        out = [item for item in combined if isinstance(item, dict) and item.get("edge_key") in real_edge_keys]

        lengths_json = json.dumps(out)
        if getattr(obj, "ConnectionLengthsJson", "") != lengths_json:
            obj.ConnectionLengthsJson = lengths_json

    @classmethod
    def create(cls, doc, name, owner, node_id, node_key, center_point, degree, topology):
        junction = doc.addObject("App::FeaturePython", name)
        cls(
            junction,
            owner=owner,
            node_id=node_id,
            node_key=node_key,
            center_point=center_point,
            degree=degree,
            topology=topology
        )
        DuctJunctionViewProvider(junction.ViewObject)
        return junction

    @staticmethod
    def labelFor(family, node_id):
        family_label = str(family).capitalize() if family else "Junction"
        return "{} [{}]".format(family_label, int(node_id))

    @staticmethod
    def _addProperty(obj, prop_type, prop_name, group, description):
        if prop_name not in obj.PropertiesList:
            obj.addProperty(prop_type, prop_name, group, description)


class DuctJunctionViewProvider:
    """View provider for derived duct junction objects."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def getIcon(self):
        return hvaclib.get_icon_path("DuctsIcon.svg")

    def onDelete(self, vobj, subelements):
        obj = vobj.Object
        owner = hvaclib.getOwnerNetwork(obj)
        if getattr(obj.Proxy, "_allow_delete", False):
            return True
        if owner and getattr(owner.Proxy, "_allow_internal_delete", False):
            return True
        FreeCAD.Console.PrintWarning(
            "HVAC - Internal junction '{}' cannot be deleted directly.\n".format(obj.Label)
        )
        return False

    def claimChildren(self):
        try:
            return self.Object.Proxy.getComponents()
        except Exception:
            return []

    def canDropObjects(self):
        return False

    def canDragObjects(self):
        return False


class DuctJunctionVirtual:
    """User-authored logical junction definition used to group parser nodes."""

    TYPE = "DuctJunctionVirtual"

    def __init__(self, obj, owner=None, member_node_keys=None, member_points=None):
        obj.Proxy = self
        self.Object = obj
        self.setProperties(obj)
        self.updateMetadata(
            owner=owner,
            member_node_keys=member_node_keys or [],
            member_points=member_points or [] )

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self.Object = obj
        self.setProperties(obj)

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def execute(self, obj):
        # No generated geometry yet. Keep empty.
        pass

    def setProperties(self, obj):
        self._addProperty(obj, "App::PropertyString", "OwnerNetworkName", "HVAC", "Owning duct network")
        self._addProperty(obj, "App::PropertyStringList", "MemberNodeKeys", "HVAC", "Array of member node keys")
        self._addProperty(obj, "App::PropertyVectorList", "MemberPoints", "HVAC", "Array of junction points")

        # Read-only internal metadata
        for prop in ("OwnerNetworkName", "MemberNodeKeys", "MemberPoints"):
            try:
                obj.setEditorMode(prop, 1)
            except Exception:
                pass

        if not getattr(obj, "MemberNodeKeys", []):
            obj.MemberNodeKeys = []

        if not getattr(obj, "MemberPoints", []):
            obj.MemberPoints = []


    def updateMetadata(self, owner=None, member_node_keys=[], member_points=[]):
        obj = self.Object
        changed = False

        def compare_vector_lists(list1, list2, tol=1e-6):
            if len(list1) != len(list2):
                return False
            for v1, v2 in zip(list1, list2):
                if (v1 - v2).Length > tol:
                    return False
            return True

        owner_name = owner.Name if owner else getattr(obj, "OwnerNetworkName", "")
        if getattr(obj, "OwnerNetworkName", "") != owner_name:
            obj.OwnerNetworkName = owner_name
            changed = True

        if getattr(obj, "MemberNodeKeys", []) != member_node_keys:
            obj.MemberNodeKeys = member_node_keys
            changed = True

        member_points_vecs = [FreeCAD.Vector(t) for t in member_points]
        if compare_vector_lists(getattr(obj, "MemberPoints", []), member_points_vecs) is False:
            obj.MemberPoints = member_points_vecs
            changed = True

        # Friendly label
        try:
            keys = list(member_node_keys or [])
            if keys:
                obj.Label = "Virtual Junction ({})".format(len(keys))
        except Exception:
            pass

        return changed

    @classmethod
    def create(cls, doc, name, owner, member_node_keys, member_points):
        vj = doc.addObject("App::FeaturePython", name)
        cls(vj, owner=owner, member_node_keys=member_node_keys, member_points=member_points)
        DuctJunctionVirtualViewProvider(vj.ViewObject)
        return vj

    def getMemberNodeKeys(self):
        return getattr(self.Object, "MemberNodeKeys", [])

    def getMemberPoints(self):
        points = getattr(self.Object, "MemberPoints", "")
        return [tuple(x) for x in points]

    @staticmethod
    def _addProperty(obj, prop_type, prop_name, group, description):
        if prop_name not in obj.PropertiesList:
            obj.addProperty(prop_type, prop_name, group, description)


class DuctJunctionVirtualViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def getIcon(self):
        return hvaclib.get_icon_path("Junction.svg")

    def onDelete(self, vobj, subelements):
        # User must be able to delete these directly from the tree.
        return True
