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

"""
Implements DuctComponent: one physical fitting inside a DuctJunction's
component chain (e.g. a reducer, an elbow, an inline damper). A DuctJunction
is a purely logical/network node; each of its DuctComponent children owns
its own library type selection, type-specific properties, and generated
Shape. See DuctJunction.composeComponents() for how a junction assembles and
positions its children's local ports each sync.
"""
import json
import traceback

import FreeCAD, Part
import FreeCADGui as Gui
from PySide import QtWidgets, QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from ..utils import hvaclib
from ..library.library_api import HVACLibraryAPI
from . import _type_schema
from . import _construction_schema
from . import _geometry_apply
from . import _component_appearance
from . import Construction as _construction


class DuctComponent:
    """One physical fitting belonging to a DuctJunction's ordered component chain."""

    TYPE = "DuctComponent"

    def __init__(
        self,
        obj,
        parent_junction=None,
        role="Primary",
        attached_edge_key="",
        port_sequence=0,
        library_id="",
        type_id="",
    ):
        obj.Proxy = self
        self.Object = obj
        self._allow_delete = False
        self._mirroring_design_flow_rate = False
        self.setProperties(obj)
        self.updateMetadata(
            parent_junction=parent_junction,
            role=role,
            attached_edge_key=attached_edge_key,
            port_sequence=port_sequence,
            library_id=library_id,
            type_id=type_id,
        )

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self.Object = obj
        self._allow_delete = False
        self._mirroring_design_flow_rate = False
        self.setProperties(obj)

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def execute(self, obj):
        # The junction composer (DuctJunction.composeComponents) is
        # responsible for working out this component's local inlet/outlet
        # ports and writing them into LocalPortsJson each sync -- execute()
        # only ever reads that, it never resolves its own parent or
        # recomputes placement, so it stays decoupled from FreeCAD's
        # automatic recompute ordering (same reasoning as DuctJunction/
        # DuctSegment not using Placement/Link-based dependencies).
        self._syncDesignFlowRate(obj)

        library_id = getattr(obj, "LibraryId", "")
        type_id = getattr(obj, "TypeId", "")
        if not library_id or not type_id:
            return

        raw_ports = getattr(obj, "LocalPortsJson", "") or "[]"
        try:
            ports = json.loads(raw_ports)
        except Exception:
            return
        # A Primary component standing in for a whole non-through junction
        # (a tee, cross, multiport, or end/terminal device) carries however
        # many real ports that node actually has -- 1, 3, 4, ... -- not
        # always 2 (only a through/2-port chain's own components are always
        # exactly 2-port). The type-def's own degree constraint validates
        # the count; this only guards against a not-yet-composed component.
        if not ports:
            return

        try:
            reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
            type_def = reg.resolve_type(library_id, type_id)
            if type_def is None:
                raise ValueError(
                    "Unknown component type '{}' in library '{}'".format(
                        type_id, library_id
                    )
                )

            params = reg.resolve_params(type_def, obj=obj)
            is_primary = getattr(obj, "ComponentRole", "") == "Primary"

            context = {
                "obj": obj,
                "center_point": HVACLibraryAPI.average_point(
                    [p["position"] for p in ports]
                ),
                "params": params,
                "connected_ports": ports,
                # Full node topology analysis (collinear_pairs,
                # orthogonal_pairs, edge_angles, edge_eccentricities,
                # is_coplanar, ...) -- see JunctionAnalysis in
                # NetworkParser.py. Real structural data about the parent
                # junction, not the component's own, but harmless/correct
                # to pass to every component (e.g. build_tee needs
                # collinear_pairs to identify which two ports form the
                # straight run).
                "analysis": self._parentAnalysis(obj),
                # Family has no meaning of its own on a component -- only a
                # Primary's family-driven dispatch (e.g. through_generic's
                # elbow-vs-transition switch) needs it, so read it straight
                # off the parent junction rather than duplicating it here.
                "family": self._parentAttr(obj, "Family") if is_primary else "",
                # Topology is likewise a junction-level fact for the Primary
                # -- validate_context checks it against the type-def's
                # declared topology (e.g. "branch" for a tee). An Inline
                # component is always a physically two-port device, no
                # matter what the parent junction's real topology is (a
                # damper on a tee's branch leg must still validate as
                # "through", not "branch"), so it always gets "through".
                "topology": self._parentAttr(obj, "Topology", "through") if is_primary else "through",
                "type_id": type_id,
                "library_id": library_id,
            }

            result = reg.build_geometry(library_id, type_def, context)
            _geometry_apply.apply_geometry_result(obj, result)
            lengths = result.connection_lengths

            lengths_json = json.dumps(lengths)
            if getattr(obj, "ConnectionLengthsJson", "") != lengths_json:
                obj.ConnectionLengthsJson = lengths_json

            # Reactive, read-only "as-built" properties a geometry backend
            # may report alongside its shape -- same convention as
            # DuctSegment.execute().
            _geometry_apply.apply_computed_properties(obj, type_def, result)

        except Exception as e:
            FreeCAD.Console.PrintWarning(
                "HVAC - DuctComponent - Execute Error generating component '{}': {}\n".format(obj.Label, e)
            )
            FreeCAD.Console.PrintMessage(traceback.format_exc())

    @staticmethod
    def _parentAttr(obj, name, default=""):
        """Read a property off this component's parent DuctJunction, or `default` if unresolvable."""
        parent_name = getattr(obj, "ParentJunctionName", "")
        doc = getattr(obj, "Document", None)
        if not parent_name or doc is None:
            return default
        parent = doc.getObject(parent_name)
        return getattr(parent, name, default) if parent is not None else default

    def _parentObj(self, obj):
        """This component's parent DuctJunction document object, or None if unresolvable."""
        parent_name = getattr(obj, "ParentJunctionName", "")
        doc = getattr(obj, "Document", None)
        if not parent_name or doc is None:
            return None
        return doc.getObject(parent_name)

    def _syncDesignFlowRate(self, obj):
        """
        Keep DesignFlowRate's editor mode and value in step with the parent
        junction every sync (see the property's own comment in
        setProperties()): editable only on a Primary component whose parent
        is an "end" (terminal) node, hidden everywhere else, and always
        pulled down from the parent's current value here -- any edit made
        directly on this component was already pushed up to the parent by
        onChanged() before this runs, so this is a no-op in that case and
        only actually does something when the parent changed some other way
        (a fresh sync, a document restore, or an edit on the parent itself).
        """
        if "DesignFlowRate" not in obj.PropertiesList:
            return

        is_primary = getattr(obj, "ComponentRole", "") == "Primary"
        parent = self._parentObj(obj)
        topology = getattr(parent, "Topology", "") if (is_primary and parent is not None) else ""
        editable = is_primary and topology == "end"
        try:
            obj.setEditorMode("DesignFlowRate", 0 if editable else 2)
        except Exception:
            pass

        if parent is None or self._mirroring_design_flow_rate:
            return
        parent_value = float(getattr(parent, "DesignFlowRate", 0.0) or 0.0)
        if float(getattr(obj, "DesignFlowRate", 0.0) or 0.0) == parent_value:
            return
        self._mirroring_design_flow_rate = True
        try:
            obj.DesignFlowRate = parent_value
        finally:
            self._mirroring_design_flow_rate = False

    def onChanged(self, obj, prop):
        if prop != "DesignFlowRate" or self._mirroring_design_flow_rate:
            return
        if getattr(obj, "ComponentRole", "") != "Primary":
            return
        parent = self._parentObj(obj)
        if parent is None:
            return
        value = float(getattr(obj, "DesignFlowRate", 0.0) or 0.0)
        if float(getattr(parent, "DesignFlowRate", 0.0) or 0.0) == value:
            return
        self._mirroring_design_flow_rate = True
        try:
            parent.DesignFlowRate = value
        finally:
            self._mirroring_design_flow_rate = False

    @classmethod
    def _parentAnalysis(cls, obj):
        """This component's parent DuctJunction's full topology analysis dict (see NetworkParser.JunctionAnalysis), or {} if unresolvable."""
        raw = cls._parentAttr(obj, "AnalysisJson", "{}")
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}

    def setProperties(self, obj):
        self._addProperty(obj, "App::PropertyString", "OwnerNetworkName", "HVAC", "Owning duct network")
        self._addProperty(obj, "App::PropertyString", "ParentJunctionName", "HVAC", "Owning DuctJunction object name")

        if "ComponentRole" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyEnumeration", "ComponentRole", "HVAC", "Primary fitting or an inline device"
            )
            obj.ComponentRole = ["Primary", "Inline"]

        self._addProperty(obj, "App::PropertyString", "AttachedEdgeKey", "HVAC", "Real edge_key this Inline component's chain is attached to (empty for Primary)")
        self._addProperty(obj, "App::PropertyInteger", "PortSequence", "HVAC", "Order from the Primary outward toward the attached edge (Inline only)")
        # Prop_NoRecompute -- documentation only, never affects geometry.
        self._addProperty(obj, "App::PropertyString", "Number", "HVAC", "Documentation number assigned by Renumber Network (e.g. 'J007-P' or 'J007-01'), blank until first renumbered", attr=16)
        self._addProperty(obj, "App::PropertyString", "LibraryId", "HVAC", "HVAC library id")
        self._addProperty(obj, "App::PropertyString", "TypeId", "HVAC", "Selected fitting type id")
        self._addProperty(obj, "App::PropertyString", "Profile", "HVAC", "Duct profile at this component's primary/outlet side")
        self._addProperty(obj, "App::PropertyStringList", "TypeSchemaPropertyNames", "HVAC", "Internal: property names added by the last-applied type schema (for stale cleanup)")
        self._addProperty(obj, "App::PropertyStringList", "ConstructionLayerIds", "HVAC", "Internal: construction layer ids of the last-applied type (see core/_construction_schema.py)")
        self._addProperty(obj, "App::PropertyStringList", "ConstructionFeatureIds", "HVAC", "Internal: construction feature ids of the last-applied type (see core/_construction_schema.py)")
        self._addProperty(obj, "App::PropertyString", "LocalPortsJson", "HVAC", "Internal: this component's local inlet/outlet port geometry, written by the parent junction's composer")
        self._addProperty(obj, "App::PropertyString", "ConnectionLengthsJson", "HVAC", "This component's own per-port connection (trim) lengths")

        # Per-construction-layer Layer_<id>_Shape/Layer_<id>_Material
        # properties are added/removed by applyTypeSchema() (via
        # core/_construction_schema.py) to match the selected type's own
        # declared construction -- see the matching comment in Segment.py's
        # own setProperties().

        # Two-way proxy for the parent junction's own DesignFlowRate (see
        # Junction.py's DesignFlowRate/onChanged) -- a junction has no Shape
        # and can't be picked in the 3D view, so a terminal's design flow
        # rate needs to be settable from its visible Primary fitting too.
        # Editor mode/value are kept in sync with the parent every sync, from
        # execute()'s _syncDesignFlowRate -- editable only for a Primary
        # component whose parent junction is an "end" (terminal) node,
        # hidden everywhere else since it has no meaning there. Starts
        # hidden (mode 2) here purely as this property's one-time initial
        # default when first added to an object; _syncDesignFlowRate
        # corrects it on the very next sync.
        if "DesignFlowRate" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat", "DesignFlowRate", "Airflow",
                "User-specified design flow rate for this terminal (L/s), mirrored to/from the parent "
                "junction. Leave blank/0 on exactly one terminal per sub-network to solve it as the "
                "balancing terminal."
            )
            try:
                obj.setEditorMode("DesignFlowRate", 2)
            except Exception:
                pass

        self._addProperty(obj, "App::PropertyFloat", "CalcFlowRate", "Airflow", "Computed flow rate through this component (L/s)")
        self._addProperty(obj, "App::PropertyFloat", "CalcVelocity", "Airflow", "Computed reference velocity used for this component's pressure drop (m/s)")
        self._addProperty(obj, "App::PropertyFloat", "CalcLossCoefficient", "Airflow", "Computed loss coefficient (K) from the last calculation")
        self._addProperty(obj, "App::PropertyFloat", "CalcPressureDrop", "Airflow", "Computed pressure drop across this component (Pa)")

        for prop in ("CalcFlowRate", "CalcVelocity", "CalcLossCoefficient", "CalcPressureDrop"):
            try:
                obj.setEditorMode(prop, 1)
            except Exception:
                pass

        if not getattr(obj, "LibraryId", ""):
            lib = hvaclib.HVACLibraryService.get_active_hvac_library()
            if lib:
                obj.LibraryId = lib.id

        if not getattr(obj, "LocalPortsJson", ""):
            obj.LocalPortsJson = "[]"

        if not getattr(obj, "ConnectionLengthsJson", ""):
            obj.ConnectionLengthsJson = "[]"

        for prop in ("ComponentRole", "Profile"):
            try:
                obj.setEditorMode(prop, 1)
            except Exception:
                pass

        # Internal bookkeeping/JSON blobs -- kept (never removed) for the
        # addon's own use, just hidden from the property editor since a
        # user never needs to read or edit them directly. PortSequence is
        # deliberately NOT hidden here -- it's a documented user-facing
        # reordering mechanism for an edge's own Inline chain (see
        # Network.py's applyAddInlineComponent).
        for prop in (
            "OwnerNetworkName",
            "ParentJunctionName",
            "AttachedEdgeKey",
            "TypeSchemaPropertyNames",
            "ConstructionLayerIds",
            "ConstructionFeatureIds",
            "LocalPortsJson",
            "ConnectionLengthsJson",
        ):
            try:
                obj.setEditorMode(prop, 2)
            except Exception:
                pass

    def applyTypeSchema(self):
        obj = self.Object
        library_id = getattr(obj, "LibraryId", "")
        type_id = getattr(obj, "TypeId", "")
        changed = _type_schema.apply_type_schema(obj, library_id, type_id)
        changed = _construction_schema.apply_construction_schema(obj, library_id, type_id) or changed
        _construction_schema.apply_default_layer_materials(obj, library_id, type_id)
        changed = _construction_schema.apply_construction_features_schema(obj, library_id, type_id) or changed
        return changed

    def getConstruction(self):
        """This component's own construction layers, queryable by semantic role -- see core/Construction.py."""
        return _construction.construction_for(self.Object)

    def updateMetadata(
        self,
        parent_junction=None,
        role=None,
        attached_edge_key=None,
        port_sequence=None,
        library_id="",
        type_id="",
        profile="",
    ):
        obj = self.Object
        changed = False

        if parent_junction is not None and getattr(obj, "ParentJunctionName", "") != parent_junction.Name:
            obj.ParentJunctionName = parent_junction.Name
            changed = True

        owner = hvaclib.getOwnerNetwork(parent_junction) if parent_junction is not None else None
        if owner is not None and getattr(obj, "OwnerNetworkName", "") != owner.Name:
            obj.OwnerNetworkName = owner.Name
            changed = True

        if role and getattr(obj, "ComponentRole", "") != str(role):
            obj.ComponentRole = str(role)
            changed = True

        if attached_edge_key is not None and getattr(obj, "AttachedEdgeKey", "") != str(attached_edge_key):
            obj.AttachedEdgeKey = str(attached_edge_key)
            changed = True

        if port_sequence is not None and getattr(obj, "PortSequence", None) != int(port_sequence):
            obj.PortSequence = int(port_sequence)
            changed = True

        if library_id and getattr(obj, "LibraryId", "") != str(library_id):
            obj.LibraryId = str(library_id)
            changed = True

        if type_id and getattr(obj, "TypeId", "") != str(type_id):
            obj.TypeId = str(type_id)
            changed = True

        if profile and getattr(obj, "Profile", "") != str(profile):
            obj.Profile = str(profile)
            changed = True

        return changed

    @classmethod
    def create(cls, doc, name, parent_junction, role, attached_edge_key="", port_sequence=0, owner_network=None):
        component = doc.addObject("Part::FeaturePython", name)
        cls(
            component,
            parent_junction=parent_junction,
            role=role,
            attached_edge_key=attached_edge_key,
            port_sequence=port_sequence,
        )
        DuctComponentViewProvider(component.ViewObject)
        return component

    @staticmethod
    def labelFor(role, type_label):
        label = str(type_label) if type_label else "Component"
        return "{} [{}]".format(label, role)

    @staticmethod
    def _addProperty(obj, prop_type, prop_name, group, description, attr=0):
        if prop_name not in obj.PropertiesList:
            obj.addProperty(prop_type, prop_name, group, description, attr)


class DuctComponentViewProvider:
    """View provider for derived duct component objects."""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object
        self.ViewObject = vobj

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def updateData(self, obj, prop):
        # Re-render every construction layer's own shape from its own
        # linked material whenever a layer's shape or material link
        # changes -- see core/_component_appearance.py.
        if _component_appearance.is_trigger_property(obj, prop):
            vobj = getattr(self, "ViewObject", None)
            if vobj is not None:
                _component_appearance.apply_component_appearance(vobj)

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
            "HVAC - Internal component '{}' cannot be deleted directly.\n".format(obj.Label)
        )
        return False

    def canDropObjects(self):
        return False

    def canDragObjects(self):
        return False
