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
import FreeCAD
import FreeCADGui as Gui
from PySide import QtWidgets, QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from . import hvaclib
from . import Observer
from . import TaskPanel
from .NetworkParser import DuctNetworkParser
from .Segment import DuctSegment
from .Junction import DuctJunction, DuctJunctionVirtual


class DuctManagedFolder:
    """Internal managed folder used by DuctNetwork."""

    def __init__(self, obj, owner=None, role=""):
        obj.Proxy = self
        if "OwnerNetworkName" not in obj.PropertiesList:
            # Store link as string to avoid cyclic dependency issue
            obj.addProperty("App::PropertyString", "OwnerNetworkName", "HVAC", "Owning duct network")
        if "FolderRole" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "FolderRole", "HVAC", "Internal folder role")
        obj.OwnerNetworkName = owner.Name if owner else ""
        obj.FolderRole = role

    def onDocumentRestored(self, obj):
        obj.Proxy = self

    def execute(self, obj):
        """Required so the object can clear its touched state on recompute."""
        pass

    @staticmethod
    def getOwner(obj):
        return DuctNetwork.getOwnerNetwork(obj)

    @staticmethod
    def create(doc, name, owner, role):
        folder = doc.addObject("App::DocumentObjectGroupPython", name)
        DuctManagedFolder(folder, owner=owner, role=role)
        DuctManagedFolderViewProvider(folder.ViewObject)
        return folder


class DuctManagedFolderViewProvider:
    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop("Object", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def getIcon(self):
        return hvaclib.get_icon_path("Folder.svg")  # optional

    def onDelete(self, vobj, subelements):
        obj = vobj.Object
        owner = DuctNetwork.getOwnerNetwork(obj)
        # Allow deletion only when the owner network itself is being deleted
        if owner and getattr(owner.Proxy, "_allow_internal_delete", False):
            return True
        FreeCAD.Console.PrintWarning(
            "HVAC - Internal folder '{}' cannot be deleted directly.\n".format(obj.Label)
        )
        return False

    def claimChildren(self):
        try:
            return list(self.Object.OutList)
        except Exception:
            return []

    def canDropObjects(self):
        return False

    def canDragObjects(self):
        return False


class DuctNetwork:
    """Visualize and configure HVAC duct network in FreeCAD's 3D view."""

    CONTEXT_KEY = hvaclib.DUCT_NETWORK_CONTEXT_KEY
    FOLDER_BASE_NAME = "Base"
    FOLDER_GEOMETRY_NAME = "Geometry"
    FOLDER_TOPOLOGY_NAME = "Topology"

    def __init__(self, obj):
        obj.Proxy = self
        self.Object = obj
        self._runtime_param_cache = {}
        self._allow_internal_delete = False
        self._initial_sync = True
        self._sync_in_progress = False
        self._sync_scheduled = False
        self._sync_suspended = False
        self._hidden_source_names = set()
        self._parser = None
        self.setProperties(obj)
        
    def __getstate__(self):
        state = self.__dict__.copy()
        state.pop("Object", None)
        state.pop("_hidden_source_names", None)
        state.pop("_parser", None)
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self.Object = obj
        self._runtime_param_cache = {}
        self._allow_internal_delete = False
        self._initial_sync = True
        self._sync_in_progress = False
        self._sync_scheduled = False
        self._sync_suspended = False
        self._hidden_source_names = set()
        self._parser = None
        self.setProperties(obj)
        self.requestSync(obj, initial_sync=True)
        
    def execute(self, obj):
        """Manual recompute of the network triggers deferred synchronization."""
        if self._sync_in_progress:
            return
        self.requestSync(obj)

    def setProperties(self, obj):
        """Gives the object properties to HVAC ducts."""
        doc = obj.Document

        # Base folder
        if "Base" not in obj.PropertiesList:
            obj.addProperty("App::PropertyLink", "Base", "HVAC", "Base (internal)")
        if getattr(obj, "Base", None) is None and doc is not None:
            folder_base = DuctManagedFolder.create(
                doc,
                f"{obj.Name}_{self.FOLDER_BASE_NAME}",
                owner=obj,
                role=self.FOLDER_BASE_NAME,
            )
            folder_base.Label = self.FOLDER_BASE_NAME
            obj.Base = folder_base
        elif obj.Base:
            if getattr(obj.Base, "OwnerNetworkName", "") != obj.Name:
                obj.Base.OwnerNetworkName = obj.Name
            if getattr(obj.Base, "FolderRole", "") != self.FOLDER_BASE_NAME:
                obj.Base.FolderRole = self.FOLDER_BASE_NAME

        # Geometry folder
        if "Geometry" not in obj.PropertiesList:
            obj.addProperty("App::PropertyLink", "Geometry", "HVAC", "Geometry (internal)")
        if getattr(obj, "Geometry", None) is None and doc is not None:
            folder_geometry = DuctManagedFolder.create(
                doc,
                f"{obj.Name}_{self.FOLDER_GEOMETRY_NAME}",
                owner=obj,
                role=self.FOLDER_GEOMETRY_NAME,
            )
            folder_geometry.Label = self.FOLDER_GEOMETRY_NAME
            obj.Geometry = folder_geometry
        elif obj.Geometry:
            if getattr(obj.Geometry, "OwnerNetworkName", "") != obj.Name:
                obj.Geometry.OwnerNetworkName = obj.Name
            if getattr(obj.Geometry, "FolderRole", "") != self.FOLDER_GEOMETRY_NAME:
                obj.Geometry.FolderRole = self.FOLDER_GEOMETRY_NAME
                
        # Topology folder
        if "Topology" not in obj.PropertiesList:
            obj.addProperty("App::PropertyLink", "Topology", "HVAC", "Topology (internal)")
        if getattr(obj, "Topology", None) is None and doc is not None:
            folder_topology = DuctManagedFolder.create(
                doc, 
                f"{obj.Name}_{self.FOLDER_TOPOLOGY_NAME}",
                owner=obj, 
                role=self.FOLDER_TOPOLOGY_NAME
            )
            folder_topology.Label = self.FOLDER_TOPOLOGY_NAME
            obj.Topology = folder_topology
        elif obj.Topology:
            if getattr(obj.Topology, "OwnerNetworkName", "") != obj.Name:
                obj.Topology.OwnerNetworkName = obj.Name
            if getattr(obj.Topology, "FolderRole", "") != self.FOLDER_TOPOLOGY_NAME:
                obj.Topology.FolderRole = self.FOLDER_TOPOLOGY_NAME
                
        # -------------------------------------------------
        # Library/type defaults for this network
        # -------------------------------------------------
        
        if "DefaultLibraryId" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyString",
                "DefaultLibraryId",
                "HVAC Types",
                "Default HVAC library for derived geometry"
            )
        
        if "DefaultSegmentProfile" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyString",
                "DefaultSegmentProfile",
                "HVAC Types",
                "Default segment profile from selected library"
            )
        
        if "DefaultAttachment" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyEnumeration",
                "DefaultAttachment",
                "HVAC Types",
                "Default section attachment for new segments"
            )
            obj.DefaultAttachment = list(hvaclib.ATTACH_MAP.keys())
            obj.DefaultAttachment = "Center"
        
        if "DefaultOffset" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyVector",
                "DefaultOffset",
                "HVAC Types",
                "Default section offset for new segments"
            )
        
        if "DefaultDiameter" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyLength",
                "DefaultDiameter",
                "HVAC Types",
                "Default circular duct diameter"
            )
        
        if "DefaultWidth" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyLength",
                "DefaultWidth",
                "HVAC Types",
                "Default rectangular duct width"
            )
        
        if "DefaultHeight" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyLength",
                "DefaultHeight",
                "HVAC Types",
                "Default rectangular duct height"
            )
        
        if not getattr(obj, "DefaultLibraryId", ""):
            lib = hvaclib.HVACLibraryService.get_active_hvac_library()
            if lib:
                obj.DefaultLibraryId = lib.id
        
        if not getattr(obj, "DefaultSegmentProfile", ""):
            obj.DefaultSegmentProfile = hvaclib.HVACLibraryService.default_segment_profile_for_library(
                getattr(obj, "DefaultLibraryId", "")
            )
        
        try:
            if obj.DefaultOffset != FreeCAD.Vector(0, 0, 0):
                pass
        except Exception:
            obj.DefaultOffset = FreeCAD.Vector(0, 0, 0)
        
        if not getattr(obj, "DefaultDiameter", 0):
            obj.DefaultDiameter = 100.0
        
        if not getattr(obj, "DefaultWidth", 0):
            obj.DefaultWidth = 100.0
        
        if not getattr(obj, "DefaultHeight", 0):
            obj.DefaultHeight = 100.0
    
    @staticmethod
    def getDefaultLibraryId(net):
        lib_id = getattr(net, "DefaultLibraryId", "")
        if lib_id:
            return lib_id

        lib = hvaclib.get_active_hvac_library()
        if lib:
            return lib.id
        return ""

    @staticmethod
    def getDefaultLibrary(net):
        lib_id = DuctNetwork.getDefaultLibraryId(net)
        if not lib_id:
            return None
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        return reg.get_library(lib_id)

    @staticmethod
    def getDefaultSegmentProfile(net):
        profile = getattr(net, "DefaultSegmentProfile", "")
        if profile:
            return profile

        lib_id = DuctNetwork.getDefaultLibraryId(net)
        return hvaclib.HVACLibraryService.default_segment_profile_for_library(lib_id)
        
    @staticmethod
    def getDefaultAttachment(net):
        return str(getattr(net, "DefaultAttachment", "Center"))

    @staticmethod
    def getDefaultOffset(net):
        return FreeCAD.Vector(getattr(net, "DefaultOffset", FreeCAD.Vector(0, 0, 0)))

    ## Library defaults management
    
    @staticmethod
    def defaultSegmentSelection(net, kind='straight'):
        """
        Return network default segment library/profile/type.
        Used only when creating a new segment or resetting one to defaults.
        """
        library_id = DuctNetwork.getDefaultLibraryId(net)
        profile = DuctNetwork.getDefaultSegmentProfile(net)
        attachement = DuctNetwork.getDefaultAttachment(net)
        offset = DuctNetwork.getDefaultOffset(net)

        valid_profiles = hvaclib.HVACLibraryService.segment_profiles_for_library(library_id)
        if profile not in valid_profiles:
            profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(library_id)

        if kind == "straight":
            type_id = hvaclib.HVACLibraryService.default_segment_type_id(library_id, profile, curved=False)
        else:
            type_id = hvaclib.HVACLibraryService.default_segment_type_id(library_id, profile, curved=True)
        
        return {
            "library_id": library_id,
            "profile": profile,
            "type_id": type_id,
            "attachment": attachement,
            "offset": offset,
        }

    @staticmethod
    def applyNetworkTypeDefaults(
        network_obj, 
        library_id=None,
        segment_profile=None,
        default_attachment=None,
        default_offset=None,
        default_diameter=None,
        default_width=None,
        default_height=None,
    ):
        """
        Apply network-level default type settings.
        """
        if network_obj is None:
            return

        changed = False

        if library_id is not None and getattr(network_obj, "DefaultLibraryId", "") != str(library_id):
            network_obj.DefaultLibraryId = str(library_id)
            changed = True
    
        if segment_profile is not None and getattr(network_obj, "DefaultSegmentProfile", "") != str(segment_profile):
            network_obj.DefaultSegmentProfile = str(segment_profile)
            changed = True
    
        if default_attachment is not None and str(getattr(network_obj, "DefaultAttachment", "Center")) != str(default_attachment):
            network_obj.DefaultAttachment = str(default_attachment)
            changed = True
    
        if default_offset is not None and FreeCAD.Vector(getattr(network_obj, "DefaultOffset", FreeCAD.Vector(0, 0, 0))) != FreeCAD.Vector(default_offset):
            network_obj.DefaultOffset = FreeCAD.Vector(default_offset)
            changed = True
    
        if default_diameter is not None and abs(float(getattr(network_obj, "DefaultDiameter", 100.0)) - float(default_diameter)) > 1e-9:
            network_obj.DefaultDiameter = float(default_diameter)
            changed = True
    
        if default_width is not None and abs(float(getattr(network_obj, "DefaultWidth", 100.0)) - float(default_width)) > 1e-9:
            network_obj.DefaultWidth = float(default_width)
            changed = True
    
        if default_height is not None and abs(float(getattr(network_obj, "DefaultHeight", 100.0)) - float(default_height)) > 1e-9:
            network_obj.DefaultHeight = float(default_height)
            changed = True
    
        if changed:
            network_obj.touch()
            if network_obj.Document:
                network_obj.Document.recompute()
    
        return changed

    @staticmethod
    def resetObjectsToNetworkDefaults(objects):
        """
        Reset selected segment/junction objects to their owner network defaults.
        """
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return

        changed = False

        for obj in objects or []:
            if obj is None:
                continue

            net = DuctNetwork.getOwnerNetwork(obj)
            if net is None:
                continue

            default_lib = DuctNetwork.getDefaultLibrary(net)
            if default_lib is None:
                continue

            proxy = getattr(obj, "Proxy", None)

            if DuctSegment.isDuctSegment(obj):
                default_profile = DuctNetwork.getDefaultSegmentProfile(net)
                if not default_profile:
                    default_profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(default_lib.id)

                kind = hvaclib.BaseCurveKind(obj.SourceObjectName, obj.SourceIndex)
                if kind == "straight":
                    default_type_id = hvaclib.HVACLibraryService.default_segment_type_id(
                        default_lib.id,
                        default_profile,
                        curved=False,
                    )
                else:
                    default_type_id = hvaclib.HVACLibraryService.default_segment_type_id(
                        default_lib.id,
                        default_profile,
                        curved=True,
                    )

                if hasattr(obj, "LibraryId") and obj.LibraryId != default_lib.id:
                    obj.LibraryId = default_lib.id
                    changed = True

                if hasattr(obj, "Profile") and obj.Profile != default_profile:
                    obj.Profile = default_profile
                    changed = True

                if hasattr(obj, "TypeId") and obj.TypeId != default_type_id:
                    obj.TypeId = default_type_id
                    changed = True

                analysis_json = json.dumps(
                    {
                        "profile": default_profile,
                        "default_type_id": default_type_id,
                        "start_node": int(getattr(obj, "StartNode", 0)),
                        "end_node": int(getattr(obj, "EndNode", 0)),
                    }
                )
                if hasattr(obj, "AnalysisJson") and obj.AnalysisJson != analysis_json:
                    obj.AnalysisJson = analysis_json
                    changed = True
                
                default_attachment = DuctNetwork.getDefaultAttachment(net)
                if getattr(obj, "Attachment", "Center") != default_attachment:
                    obj.Attachment = default_attachment
                    changed = True
                
                default_offset = DuctNetwork.getDefaultOffset(net)
                if FreeCAD.Vector(getattr(obj, "Offset", FreeCAD.Vector(0, 0, 0))) != default_offset:
                    obj.Offset = default_offset
                    changed = True

            elif DuctJunction.isDuctJunction(obj):
                family = getattr(obj, "Family", "")
                default_type_id = hvaclib.HVACLibraryService.default_junction_type_id(family)

                if hasattr(obj, "LibraryId") and obj.LibraryId != default_lib.id:
                    obj.LibraryId = default_lib.id
                    changed = True

                if hasattr(obj, "TypeId") and obj.TypeId != default_type_id:
                    obj.TypeId = default_type_id
                    changed = True

            if proxy and hasattr(proxy, "applyTypeSchema"):
                try:
                    changed = proxy.applyTypeSchema(obj) or changed
                except Exception:
                    pass

            try:
                obj.touch()
            except Exception:
                pass

        if changed:
            doc.recompute()
    
    ## Object Management
    
    @staticmethod
    def createObject(name):
        net = FreeCAD.ActiveDocument.addObject('App::DocumentObjectGroupPython', name)
        DuctNetwork(net)
        DuctNetworkViewProvider(net.ViewObject)
        return net

    @staticmethod
    def createSketchInteractive(obj):
        """
        Open the standard FreeCAD sketch creation panel and,
        after the sketch is created, move it under obj.Base.
        """
        if not obj or not DuctNetwork.isDuctNetwork(obj):
            return
        if FreeCAD.ActiveDocument is None or Gui.ActiveDocument is None:
            return

        # Make this network active in the 3D view context
        DuctNetwork.setActive(obj)
        
        # Install observer before running the command
        def callback(obj, sketch):
            if sketch:
                DuctNetwork.addBaseObject(obj, sketch)
                DuctNetwork.showAllJunctionGeometry(obj)
                
        obs = Observer.NewSketchObserver(obj, callback)
        FreeCAD.addDocumentObserver(obs)
        
        # Launch the built-in sketch creation command
        DuctNetwork.hideAllJunctionGeometry(obj)
        Gui.runCommand("Sketcher_NewSketch")

    @staticmethod
    def createDraftLineInteractive(obj, linetype='Line'):
        """
        Open the standard Draft Line command and, after the user exits the tool,
        move all newly created Draft line objects under obj.Base.
        """
        if not obj or not DuctNetwork.isDuctNetwork(obj):
            return
        if FreeCAD.ActiveDocument is None or Gui.ActiveDocument is None:
            return

        # Make this network active in the 3D view context
        DuctNetwork.setActive(obj)
        
        # Install observer before running the command
        def callback(net, objs):
            for obj in objs:
                if hvaclib.isWire(obj):
                    DuctNetwork.addBaseObject(net, obj)
            DuctNetwork.showAllJunctionGeometry(net)
                
        obs = Observer.NewDraftLineObserver(obj, callback)
        FreeCAD.addDocumentObserver(obs)
        
        # Launch the built-in Draft Line/ BSpline creation command
        DuctNetwork.hideAllJunctionGeometry(obj)
        Gui.activateWorkbench("DraftWorkbench")
        if linetype=='Line':
            Gui.runCommand("Draft_Line")
        elif linetype=='BSpline':
            Gui.runCommand("Draft_BSpline")

    @staticmethod
    def addBaseObject(net, obj):
        if not net or not obj:
            return False
        if not DuctNetwork.isDuctNetwork(net):
            return False
        if not hasattr(net, "Base") or net.Base is None:
            return False
        if not hasattr(obj, "Document") or obj.Document is None:
            return False
        if net.Document != obj.Document:
            return False
        if not (hvaclib.isSketch(obj) or hvaclib.isWire(obj)):
            return False
        if obj in net.Base.OutList:
            return False
        
        net.Base.addObject(obj)
        if getattr(net, "Proxy", None):
            net.Proxy.requestSync(net)
        net.Document.recompute()
        return True
        
    @staticmethod
    def addVirtualJunctionObject(obj, member_node_keys, member_points):
        doc = obj.Document
        name = doc.getUniqueObjectName("VirtualJunction")
        vj = DuctJunctionVirtual.create(doc, name, owner=obj, member_node_keys=member_node_keys, member_points=member_points)
        obj.Topology.addObject(vj)
        return vj

    @staticmethod
    def removeBaseObject(net, obj):
        if not net or not obj:
            return False
        if not DuctNetwork.isDuctNetwork(net):
            return False
        if not hasattr(net, "Base") or net.Base is None:
            return False
        if net.Document != getattr(obj, "Document", None):
            return False
        if obj not in net.Base.OutList:
            return False
    
        net.Base.removeObject(obj)
        if getattr(net, "Proxy", None):
            net.Proxy.requestSync(net)
        net.Document.recompute()
        return True

    @staticmethod
    def removeGeometryObject(net, obj):
        """Remove a derived geometry object from the Geometry folder and document."""
        if not net or not obj or not DuctNetwork.isDuctNetwork(net):
            return False
        if getattr(obj, "Document", None) != net.Document:
            return False
        if (DuctSegment.isDuctSegment(obj) or DuctJunction.isDuctJunction(obj)) and getattr(obj, "Proxy", None):
            obj.Proxy._allow_delete = True
        if hasattr(net, "Geometry") and net.Geometry and obj in net.Geometry.OutList:
            net.Geometry.removeObject(obj)
        net.Document.removeObject(obj.Name)
        return True

    @staticmethod
    def collectSegmentObjects(net):
        segments = {}
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return segments
        for child in list(geometry.OutList):
            if not DuctSegment.isDuctSegment(child):
                continue
            key = getattr(child, "SegmentKey", "")
            if not key and getattr(child, "SourceObjectName", ""):
                key = hvaclib.makeLineKey(child.SourceObjectName, child.SourceIndex)
            if key:
                segments[key] = child
        return segments
        
    @staticmethod
    def collectJunctionObjects(net):
        junctions = {}
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return junctions
        for child in list(geometry.OutList):
            if not DuctJunction.isDuctJunction(child):
                continue
            key = getattr(child, "NodeKey", "")
            if key:
                junctions[key] = child
        return junctions
        
    @staticmethod
    def collectVirtualJunctionObjects(obj):
        topology_objs = []
        topology = getattr(obj, "Topology", None)
        if topology:
            for child in list(getattr(topology, "Group", []) or []):
                if DuctJunctionVirtual.isDuctJunctionVirtual(child):
                    topology_objs.append(child)
        return topology_objs
        
    @staticmethod
    def getNodeGroups(obj, parser):
        """Compile node groups from virtual junction objects and the parser's node ID map."""
        node_groups = []
        
        node_id_by_key = {parser.geometric_node_key(nid): nid for nid in parser.geometric_nodes()}
        virtual_objs = DuctNetwork.collectVirtualJunctionObjects(obj)
        
        for vj in virtual_objs:
            keys = DuctJunctionVirtual.getMemberNodeKeys(vj)
            ids = []
    
            for key in keys:
                nid = node_id_by_key.get(key)
                if nid is not None:
                    ids.append(nid)
    
            ids = sorted(set(ids))
            if len(ids) >= 2:
                node_groups.append(ids)
    
        return node_groups
    
    @staticmethod
    def setActive(obj):
        """Set this DuctNetwork as the active container in the 3D view."""
        Gui.ActiveDocument.ActiveView.setActiveObject(DuctNetwork.CONTEXT_KEY, obj)

    @staticmethod
    def getActive(doc=None):
        """Get the active DuctNetwork container from the 3D view."""
        if not FreeCAD.GuiUp:
            return None
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None or Gui.ActiveDocument is None:
            return None
        return Gui.ActiveDocument.ActiveView.getActiveObject(DuctNetwork.CONTEXT_KEY)
        
    @staticmethod
    def _setGeometryVisibilityDeferred(obj, visible):
        if not FreeCAD.GuiUp:
            return

        def apply():
            try:
                if obj is None or getattr(obj, "Document", None) is None:
                    return
                vobj = getattr(obj, "ViewObject", None)
                if vobj is None:
                    return
                vobj.Visibility = bool(visible)
            except Exception:
                pass

        QtCore.QTimer.singleShot(0, apply)

    def showAllGeometry(self, net):
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return

        if getattr(geometry, "ViewObject", None):
            try:
                geometry.ViewObject.Visibility = True
            except Exception:
                pass

        for obj in list(geometry.OutList):
            if DuctSegment.isDuctSegment(obj) or DuctJunction.isDuctJunction(obj):
                DuctNetwork._setGeometryVisibilityDeferred(obj, True)
                
    def _segmentFromBaseObject(self, seg, base_obj):
        return (
            seg is not None
            and base_obj is not None
            and DuctSegment.isDuctSegment(seg)
            and getattr(seg, "SourceObjectName", "") == base_obj.Name
        )
    
    def hideGeometryForBaseObject(self, net, base_obj):
        geometry = getattr(net, "Geometry", None)
        if geometry is None or base_obj is None:
            return
        for seg in list(geometry.OutList):
            if self._segmentFromBaseObject(seg, base_obj):
                DuctNetwork._setGeometryVisibilityDeferred(seg, False)
                
    @staticmethod
    def hideAllJunctionGeometry(net):
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return

        for obj in list(geometry.OutList):
            if DuctNetwork._isJunctionObject(obj):
                DuctNetwork._setGeometryVisibilityDeferred(obj, False)

    @staticmethod
    def showAllJunctionGeometry(net):
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return

        for obj in list(geometry.OutList):
            if DuctNetwork._isJunctionObject(obj):
                DuctNetwork._setGeometryVisibilityDeferred(obj, True)
    
    def showGeometryForBaseObject(self, net, base_obj):
        geometry = getattr(net, "Geometry", None)
        if geometry is None or base_obj is None:
            return
        for seg in list(geometry.OutList):
            if self._segmentFromBaseObject(seg, base_obj):
                DuctNetwork._setGeometryVisibilityDeferred(seg, True)
    
    def setBaseObjectEditing(self, net, base_obj, editing):
        if net is None or base_obj is None:
            return
        if editing:
            self._hidden_source_names.add(base_obj.Name)
            self.hideGeometryForBaseObject(net, base_obj)
            self.hideAllJunctionGeometry(net)
        else:
            self._hidden_source_names.discard(base_obj.Name)
            self.showGeometryForBaseObject(net, base_obj)
            self.showAllJunctionGeometry(net)

    @staticmethod
    def isDuctNetwork(obj):
        """Test whether obj is a DuctNetwork FeaturePython object."""
        return bool(obj) and hasattr(obj, "Proxy") and isinstance(obj.Proxy, DuctNetwork)
        
    @staticmethod
    def isBaseObject(obj):
        if obj is None:
            return False
        if not (hvaclib.isSketch(obj) or hvaclib.isWire(obj)):
            return False
            
        for net in hvaclib.allHVACNetworks(obj.Document):
            base = getattr(net, "Base", None)
            if base and obj in base.OutList:
                return True
        return False
        
    @staticmethod
    def isGeometryObject(obj):
        return (
            bool(obj)
            and hasattr(obj, "Proxy")
            and (
                isinstance(obj.Proxy, DuctSegment)
                or isinstance(obj.Proxy, DuctJunction)
            )
        )
        
    @staticmethod
    def _isJunctionObject(obj):
        try:
            return DuctJunction.isDuctJunction(obj)
        except Exception:
            return False
        
    @staticmethod
    def getOwnerNetwork(obj):
        """Return the owning duct network document object for an internal object."""
        if DuctNetwork.isGeometryObject(obj):
            owner_name = getattr(obj, "OwnerNetworkName", "")
            doc = getattr(obj, "Document", None)
            if owner_name and doc:
                return doc.getObject(owner_name)
            return None
        elif DuctNetwork.isBaseObject(obj):
            for net in hvaclib.allHVACNetworks(obj.Document):
                base = getattr(net, "Base", None)
                if base and obj in base.OutList:
                    return net
        return None
        
    @staticmethod
    def getOwnerBaseObject(obj):
        """Return the base object for a given geometry object."""
        if DuctNetwork.isGeometryObject(obj):
            owner_name = getattr(obj, "SourceObjectName", "")
            doc = getattr(obj, "Document", None)
            if owner_name and doc:
                return doc.getObject(owner_name)
        return None
    
    # Functions for syncing object data with the network parser
    
    @staticmethod
    def syncVirtualJunctions(obj, parser, initial_sync=False):
        """Update the MemberNodes property of virtual junction objects
            from the actual node keys from parser."""
        for vj in DuctNetwork.collectVirtualJunctionObjects(obj):
            # Use quantized point keys to look up new node keys from parser
            stored_points = DuctJunctionVirtual.getMemberPoints(vj)
            stored_keys = DuctJunctionVirtual.getMemberNodeKeys(vj)
            # Get quantised nodemap from parser
            geo_nodekey_map = {parser.geometric_node_key(id): point for (id, point) in parser.geometric_node_point_map().items()}
            # Find nodekeys from nodemap
            member_keys = []
            member_points = []
                        
            # If initial_sync, use stored points to udate modified keys
            if initial_sync:
                for key, point in geo_nodekey_map.items():
                    if hvaclib.vec_in_list(point, stored_points) and not hvaclib.vec_in_list(point, member_points):
                        member_keys.append(key)
                        member_points.append(point)
            # Else use stored keys to find updated points
            else:
                for key, point in geo_nodekey_map.items():
                    if key in stored_keys and key not in member_keys:
                        member_keys.append(key)
                        member_points.append(point)
            
            # Update the MemberNodeKeys property with the new node keys
            vj.Proxy.updateMetadata(
                vj, 
                owner=obj, 
                member_node_keys=member_keys, 
                member_points=member_points
            )

    def syncSegments(self, net, parser, initial_sync=False):
        """
        Synchronize derived DuctSegment objects with the base geometry.
    
        Segment LibraryId / Profile / TypeId are object-owned values.
        Network defaults are only used when creating a new segment or repairing
        missing/invalid values.
        """
        doc = net.Document
        geometry = getattr(net, "Geometry", None)
        if doc is None or geometry is None:
            return False
    
        default_lib = self.getDefaultLibrary(net)
        if default_lib is None:
            return False
    
        changed = False
        existing_segments = self.collectSegmentObjects(net)
        trim_map = self.collectSegmentTrimMap(net)
        live_objs = set()
    
        for edge_ref in parser.edges():
            key = edge_ref.tag
    
            source_obj = doc.getObject(edge_ref.obj_name)
            if source_obj is None:
                continue
    
            # If initial sync, the tags are regenerated hence find element based on SourceObjectName and SourceIndex
            # Also update the existing segment's key in the dictionary with the modified key (Object.Tag)
            if initial_sync:
                segment_obj = None
                matched_old_key = None
                for old_key, seg in existing_segments.items():
                    if seg.SourceObjectName == source_obj.Name and seg.SourceIndex == edge_ref.local_index:
                        segment_obj = seg
                        matched_old_key = old_key
                        break
    
                if matched_old_key is not None and matched_old_key != key:
                    existing_segments.pop(matched_old_key, None)
                    existing_segments[key] = segment_obj
            # Else find element based on key
            else:
                segment_obj = existing_segments.get(key)
    
            # If segment does not exist, create a new one
            if segment_obj is None:
                segment_obj = DuctSegment.create(
                    doc,
                    "{}_Seg_{}_{}".format(net.Name, source_obj.Name, edge_ref.local_index),
                    owner=net,
                    key=key,
                    source_obj=source_obj,
                    source_index=edge_ref.local_index,
                )
                
                # Get and set default segment properties from default library
                kind = hvaclib.BaseCurveKind(edge_ref.obj_name, edge_ref.local_index)
                defaults = self.defaultSegmentSelection(net, kind=kind)
                if hasattr(segment_obj, "LibraryId"):
                    segment_obj.LibraryId = defaults["library_id"]
                if hasattr(segment_obj, "Profile"):
                    segment_obj.Profile = defaults["profile"]
                if hasattr(segment_obj, "TypeId"):
                    segment_obj.TypeId = defaults["type_id"]
                if hasattr(segment_obj, "Attachment"):
                    segment_obj.Attachment = defaults["attachment"]
                if hasattr(segment_obj, "Offset"):
                    segment_obj.Offset = defaults["offset"]
                
                changed = True
    
                # If source base object is marked to be hidden, hide the created segment geometry
                if source_obj.Name in self._hidden_source_names:
                    DuctNetwork._setGeometryVisibilityDeferred(segment_obj, False)
                else:
                    DuctNetwork._setGeometryVisibilityDeferred(segment_obj, True)
    
            # Add the segment object to the geometry folder if not already present
            if segment_obj not in geometry.OutList:
                geometry.addObject(segment_obj)
                changed = True
            
            live_objs.add(segment_obj)
            
            # Compute start and end points based on start/end nodes
            start_node, end_node = parser.edge_nodes(edge_ref)
            raw_start_point, raw_end_point = parser.edge_line(edge_ref)
            raw_sp_vec = FreeCAD.Vector(*raw_start_point)
            raw_ep_vec = FreeCAD.Vector(*raw_end_point)
            seg_dir = raw_ep_vec.sub(raw_sp_vec)
            if seg_dir.Length <= 1e-9:
                continue
            seg_dir.normalize()
            start_point = self.resolveSegmentEndpoint(raw_sp_vec, seg_dir, segment_obj)
            end_point = self.resolveSegmentEndpoint(raw_ep_vec, seg_dir, segment_obj)
            
            # Get trim start/end from the trim map, if available
            trim_entry = trim_map.get(key, {})
            trim_start, trim_end = self.resolveSegmentEndTrims(trim_entry)
            
            # Get library ID, profile and type_id for segment, defaulting to active library if not set
            library_id = getattr(segment_obj, "LibraryId", "") or self.getDefaultLibraryId(net)
            profile = getattr(segment_obj, "Profile", "")
            valid_profiles = hvaclib.HVACLibraryService.segment_profiles_for_library(library_id)
            if profile not in valid_profiles:
                profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(library_id)
            type_id = getattr(segment_obj, "TypeId", "")
            if not type_id:
                kind = hvaclib.BaseCurveKind(edge_ref.obj_name, edge_ref.local_index)
                if kind == "straight":
                    type_id = hvaclib.HVACLibraryService.default_segment_type_id(library_id, profile, curved=False)
                else:
                    type_id = hvaclib.HVACLibraryService.default_segment_type_id(library_id, profile, curved=True)
            
            # Update metadata based on updated data
            meta_changed = segment_obj.Proxy.updateMetadata(
                segment_obj,
                owner=net,
                key=key,
                source_obj=source_obj,
                source_index=edge_ref.local_index,
                start_node=start_node,
                end_node=end_node,
                start_point=hvaclib.vec_to_xyz(start_point),
                end_point=hvaclib.vec_to_xyz(end_point),
                trim_start=trim_start,
                trim_end=trim_end,
                family="straight_segment",
                type_id=type_id,
                library_id=library_id,
                profile=profile,
                analysis_json=json.dumps(
                    {
                        "profile": profile,
                        "start_node": int(start_node),
                        "end_node": int(end_node),
                    }
                ),
            )
            changed = changed or meta_changed
    
            # Update property schema based on type ID and library ID
            schema_changed = segment_obj.Proxy.applyTypeSchema(segment_obj)
            changed = changed or schema_changed
    
            # If parameters were cached, restore them
            cached_params = self._runtime_param_cache.pop(key, None)
            if cached_params:
                restored = self._restoreSegmentUserParams(segment_obj, cached_params)
                changed = changed or restored
    
            # Update label for segment object based on source object and edge index
            new_label = DuctSegment.labelFor(source_obj, edge_ref.local_index)
            if segment_obj.Label != new_label:
                segment_obj.Label = new_label
                changed = True
    
        # Remove old segments
        for segment_obj in list(existing_segments.values()):
            if segment_obj not in live_objs:
                seg_key = getattr(segment_obj, "SegmentKey", "")
                # Cache segment parameters for later restoration during undo
                if seg_key:
                    self._runtime_param_cache[seg_key] = self._segmentUserParams(segment_obj)
                self.removeGeometryObject(net, segment_obj)
                changed = True
        
        return changed
        
    def syncJunctions(self, net, parser, initial_sync=False):
        """
        Synchronize derived DuctJunction objects with parser nodes.
    
        Existing junction LibraryId / TypeId are preserved.
        New junctions are initialized from network defaults.
        """
        doc = net.Document
        geometry = getattr(net, "Geometry", None)
        if doc is None or geometry is None:
            return False
    
        default_lib = self.getDefaultLibrary(net)
        if default_lib is None:
            return False
    
        changed = False
        live_objs = set()
        existing_junctions = self.collectJunctionObjects(net)
        segment_map = self.collectSegmentObjects(net)
        
        for node_id in parser.nodes():
            # Get node analysis
            analysis = parser.node_analysis(node_id)
            degree = int(analysis.get("degree", 0))
            if degree <= 0:
                continue
    
            # Run classification for identifying junction family
            family = hvaclib.HVACLibraryService.classify_junction_family(analysis)
            point = analysis["point"]
            node_key = analysis["node_key"]
    
            connected_edge_keys = [
                edge_ref.tag
                for edge_ref in analysis["edge_refs"]
            ]
    
            port_objs = parser.build_junction_ports(
                node_id,
                analysis["edge_refs"],
                segment_map=segment_map,
            )
            connected_ports = [
                {
                    "edge_key": p.edge_key,
                    "segment_end": p.segment_end,
                    "position": p.position,
                    "direction": p.direction,
                    "profile": p.profile,
                    "section_params": p.section_params,
                    "attachment": p.attachment,
                    "user_offset": p.user_offset,
                    "profile_x_axis": p.profile_x_axis
                }
                for p in port_objs
            ]
            # Build analysis JSON for the junction
            analysis_json = json.dumps(
                {
                    "degree": degree,
                    "family": family,
                    "connected_ports": connected_ports,
                    "collinear_pairs": [
                        [
                            a.tag,
                            b.tag,
                            float(ang),
                        ]
                        for a, b, ang in analysis.get("collinear_pairs", [])
                    ],
                    "orthogonal_pairs": [
                        [
                            a.tag,
                            b.tag,
                            float(ang),
                        ]
                        for a, b, ang in analysis.get("orthogonal_pairs", [])
                    ],
                }
            )
    
            # If initial sync, the tags are regenerated hence find element based on position
            # Also update the existing junction's key in the dictionary with the modified key
            if initial_sync:
                junction_obj = None
                matched_old_key = None
                for old_key, junc in existing_junctions.items():
                    if hvaclib.vec_quant(junc.CenterPoint) == hvaclib.vec_quant(point):
                        junction_obj = junc
                        matched_old_key = old_key
                        break
    
                if matched_old_key is not None and matched_old_key != node_key:
                    existing_junctions.pop(matched_old_key, None)
                    existing_junctions[node_key] = junction_obj
            # Else find element based on key
            else:
                junction_obj = existing_junctions.get(node_key)
    
            # If junction does not exist, create a new one
            if junction_obj is None:
                junction_obj = DuctJunction.create(
                    doc,
                    "{}_Junc_{}".format(net.Name, node_id),
                    owner=net,
                    node_id=node_id,
                    node_key=node_key,
                    node_kind=family,
                    center_point=point,
                    degree=degree,
                )
                # Get and set default segment properties from default library
                default_lib_id = default_lib.id
                default_type_id = hvaclib.HVACLibraryService.default_junction_type_id(family)
                if hasattr(junction_obj, "LibraryId"):
                    junction_obj.LibraryId = default_lib_id
                if hasattr(junction_obj, "TypeId"):
                    junction_obj.TypeId = default_type_id
    
                changed = True
                
                # If source object is marked as hidden, hide junction geometry
                if self._hidden_source_names:
                    self._setGeometryVisibilityDeferred(junction_obj, False)
                else:
                    self._setGeometryVisibilityDeferred(junction_obj, True)
    
            # Add junction to geometry folder if not already present
            if junction_obj not in geometry.OutList:
                geometry.addObject(junction_obj)
                changed = True
    
            live_objs.add(junction_obj)
    
            # Get library and type IDs, setting defaults if not present
            library_id = getattr(junction_obj, "LibraryId", "") or default_lib.id
            type_id = getattr(junction_obj, "TypeId", "")
            if not type_id:
                type_id = hvaclib.HVACLibraryService.default_junction_type_id(family)
            
            # Update metadata based on updated data
            meta_changed = junction_obj.Proxy.updateMetadata(
                junction_obj,
                owner=net,
                node_id=node_id,
                node_key=node_key,
                node_kind=family,
                center_point=point,
                degree=degree,
                family=family,
                type_id=type_id,
                library_id=library_id,
                connected_edge_keys=connected_edge_keys,
                analysis_json=analysis_json,
            )
            changed = changed or meta_changed
            
            # Update property schema based on type ID and library ID
            schema_changed = junction_obj.Proxy.applyTypeSchema(junction_obj)
            changed = changed or schema_changed
    
            # Update label for segment object based on source object and edge index
            new_label = DuctJunction.labelFor(family, node_id)
            if junction_obj.Label != new_label:
                junction_obj.Label = new_label
                changed = True
    
        # Remove old junctions
        for junction_obj in list(existing_junctions.values()):
            if junction_obj not in live_objs:
                self.removeGeometryObject(net, junction_obj)
                changed = True
        
        return changed
                            
    def requestSync(self, obj, initial_sync=None, force_recompute=False):
        if initial_sync is not None:
            self._initial_sync = bool(initial_sync)            
        
        if self._sync_suspended:
                return
        
        if self._sync_scheduled:
            return
        
        self._sync_scheduled = True
        if initial_sync:
            FreeCAD.Console.PrintMessage("HVAC - Sync requested (Initial sync).\n")
        else:
            FreeCAD.Console.PrintMessage("HVAC - Sync requested.\n")
        QtCore.QTimer.singleShot(0, lambda o=obj: self._runDeferredSync(o, force_recompute))
        
    def suspendSync(self):
        self._sync_suspended = True
    
    def resumeSync(self, obj, request_sync=True):
        self._sync_suspended = False
        if request_sync == True:
            self.requestSync(obj)
            
    def getParser(self, rebuild=False, set_node_groups=True):
        if self._parser is None or rebuild:
            parser = DuctNetworkParser(list(self.Object.Base.OutList))
            if set_node_groups:
                node_groups = DuctNetwork.getNodeGroups(self.Object, parser)
                parser.set_node_groups(node_groups)
            self._parser = parser
        return self._parser
    
    def _runDeferredSync(self, obj, force_recompute=False):
        self._sync_scheduled = False
    
        if obj is None or obj.Document is None:
            return
        if self._sync_in_progress:
            return
        
        self._sync_in_progress = True
        try:
            
            if self._initial_sync:  
                # Do not run junction update on initial sync since edge tags will not be updated in segments
                # Doing so will clear all junctions since edges could not be found
                
                # Get parser for syncing virtual junctions
                parser = self.getParser(rebuild=True, set_node_groups=False)
                # Update VirtualJunction keys
                self.syncVirtualJunctions(obj, parser, initial_sync=True)
                # Rebuild parser after syncing virtual junctions
                parser = self.getParser(rebuild=True)
                
                # Stage 1: Sync segments first to update edge data after document reload
                self.syncSegments(obj, parser, initial_sync=True)
                obj.Document.recompute()
                
                # Stage 2: Sync junctions, so that their execute() writes ConnectionLengthsJson
                self.syncJunctions(obj, parser, initial_sync=True)
                obj.Document.recompute()
                
                # Stage 3: Sync segments which consume the junction trim data
                self.syncSegments(obj, parser, initial_sync=False)
                obj.Document.recompute()
                
            else:  
                # Get parser
                parser = self.getParser(rebuild=True, set_node_groups=False)
                # Update VirtualJunction keys
                self.syncVirtualJunctions(obj, parser, initial_sync=False)
                # Rebuild parser after syncing virtual junctions
                parser = self.getParser(rebuild=True)
                
                # Stage 1: Sync segments first to update edge data
                changed_segments = self.syncSegments(obj, parser, initial_sync=False)
                if changed_segments or force_recompute:
                    obj.Document.recompute()
                    
                # Stage 2: Sync junctions, for creating ports; so that their execute() writes ConnectionLengthsJson
                changed_junctions = self.syncJunctions(obj, parser, initial_sync=False)
                if changed_junctions or force_recompute:
                    obj.Document.recompute()
    
                # Stage 3: Sync segments which consume the junction trim data
                changed_segments = self.syncSegments(obj, parser, initial_sync=False)
                if changed_segments or force_recompute:
                    obj.Document.recompute()
                    
            self._initial_sync = False
    
        except Exception as err:
            FreeCAD.Console.PrintError(traceback.format_exc())
            FreeCAD.Console.PrintError(
                "HVAC - Failed to update network '{}': {}\n".format(obj.Label, err)
            )
        finally:
            self._sync_in_progress = False
        
    @staticmethod
    def _segmentUserParams(obj):
        return {
            "LibraryId": str(getattr(obj, "LibraryId", "")),
            "Profile": str(getattr(obj, "Profile", "")),
            "TypeId": str(getattr(obj, "TypeId", "")),
            "Attachment": str(getattr(obj, "Attachment", "Center")),
            "Offset": hvaclib.vec_to_xyz(getattr(obj, "Offset", (0, 0, 0))),
            "ProfileXAxis": hvaclib.vec_to_xyz(getattr(obj, "ProfileXAxis", (0, 0, 0))),
            "Diameter": float(getattr(obj, "Diameter", 0.0)),
            "Width": float(getattr(obj, "Width", 0.0)),
            "Height": float(getattr(obj, "Height", 0.0)),
            "InsulationThickness": float(getattr(obj, "InsulationThickness", 0.0)),
            "Roughness": float(getattr(obj, "Roughness", 0.0)),
            "FlowRate": float(getattr(obj, "FlowRate", 0.0)),
            "Velocity": float(getattr(obj, "Velocity", 0.0)),
        }
    
    @staticmethod
    def _restoreSegmentUserParams(obj, params):
        if not isinstance(params, dict):
            return False
    
        changed = False
    
        def set_if_needed(prop, value):
            nonlocal changed
            try:
                if getattr(obj, prop) != value:
                    setattr(obj, prop, value)
                    changed = True
            except Exception:
                pass
    
        if "LibraryId" in params:
            set_if_needed("LibraryId", params["LibraryId"])
        if "Profile" in params:
            set_if_needed("Profile", params["Profile"])
        if "TypeId" in params:
            set_if_needed("TypeId", params["TypeId"])
        if "Attachment" in params:
            set_if_needed("Attachment", params["Attachment"])
        if "Offset" in params:
            set_if_needed("Offset", params["Offset"])
        if "ProfileXAxis" in params:
            set_if_needed("ProfileXAxis", params["ProfileXAxis"])
        if "Diameter" in params:
            set_if_needed("Diameter", params["Diameter"])
        if "Width" in params:
            set_if_needed("Width", params["Width"])
        if "Height" in params:
            set_if_needed("Height", params["Height"])
        if "InsulationThickness" in params:
            set_if_needed("InsulationThickness", params["InsulationThickness"])
        if "Roughness" in params:
            set_if_needed("Roughness", params["Roughness"])
        if "FlowRate" in params:
            set_if_needed("FlowRate", params["FlowRate"])
        if "Velocity" in params:
            set_if_needed("Velocity", params["Velocity"])
    
        return changed
    
    @staticmethod
    def applyTypeSelection(objects, library_id="", type_id=""):
        """
        Apply library/type selection to selected segment/junction objects.
        """
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return
        
        nets_to_sync = set()
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        changed = False

        for obj in objects or []:
            if obj is None:
                continue
            
            net = DuctNetwork.getOwnerNetwork(obj)
            if net is not None:
                nets_to_sync.add(net)
                    
            if hasattr(obj, "LibraryId") and library_id:
                if obj.LibraryId != library_id:
                    obj.LibraryId = library_id
                    changed = True

            if type_id and hasattr(obj, "TypeId"):
                if obj.TypeId != type_id:
                    obj.TypeId = type_id
                    changed = True

            if hvaclib.isDuctSegment(obj):
                valid_profiles = hvaclib.HVACLibraryService.segment_profiles_for_library(obj.LibraryId)
                current_profile = getattr(obj, "Profile", "")
                if current_profile not in valid_profiles:
                    new_profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(obj.LibraryId)
                    if new_profile and obj.Profile != new_profile:
                        obj.Profile = new_profile
                        changed = True

                if type_id:
                    tdef = reg.resolve_type(obj.LibraryId, type_id)
                    if tdef and getattr(tdef, "profiles", None):
                        new_profile = tdef.profiles[0]
                        if obj.Profile != new_profile:
                            obj.Profile = new_profile
                            changed = True

            proxy = getattr(obj, "Proxy", None)
            if proxy and hasattr(proxy, "applyTypeSchema"):
                try:
                    changed = proxy.applyTypeSchema(obj) or changed
                except Exception:
                    pass
            
            # Touch the modified obects for recomputation
            try:
                obj.touch()
            except Exception:
                pass

        if changed:
            # Sync all affected networks
            for net in nets_to_sync:
                proxy = getattr(net, "Proxy", None)
                if proxy:
                    proxy.requestSync(net, force_recompute=True)
     
    @staticmethod
    def applyPlacementSelection(objects, attachment=None, offset=None, profile_x_axis=None):
        doc = FreeCAD.ActiveDocument
        if doc is None:
            return
    
        nets_to_sync = set()
        changed = False
        for obj in objects or []:
            if obj is None or not hvaclib.isDuctSegment(obj):
                continue
    
            net = DuctNetwork.getOwnerNetwork(obj)
            if net is not None:
                nets_to_sync.add(net)
    
            if attachment is not None and getattr(obj, "Attachment", "") != attachment:
                obj.Attachment = attachment
                changed = True
            if offset is not None and getattr(obj, "Offset", FreeCAD.Vector(0,0,0)) != offset:
                obj.Offset = offset
                changed = True
            if profile_x_axis is not None and getattr(obj, "ProfileXAxis", FreeCAD.Vector(0,0,0)) != profile_x_axis:
                obj.ProfileXAxis = profile_x_axis
                changed = True
    
            try:
                obj.touch()
            except Exception:
                pass
        if changed:
            for net in nets_to_sync:
                proxy = getattr(net, "Proxy", None)
                if proxy:
                    proxy.requestSync(net, force_recompute=True)       
    
    ## Trim map generation from junctions
    
    @staticmethod
    def collectSegmentTrimMap(net):
        """
        Collect trim contributions from all junctions.
    
        Returns:
            {
                "edge_key": {
                    "start": max_length_at_start,
                    "end": max_length_at_end,
                },
                ...
            }
        """
        trim_map = {}
    
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return trim_map
    
        for obj in list(geometry.OutList):
            if not hvaclib.isDuctJunction(obj):
                continue
    
            raw = getattr(obj, "ConnectionLengthsJson", "") or "[]"
            try:
                items = json.loads(raw)
            except Exception:
                continue
    
            if not isinstance(items, list):
                continue
    
            for item in items:
                if not isinstance(item, dict):
                    continue
    
                edge_key = str(item.get("edge_key", "") or "")
                seg_end = str(item.get("segment_end", "") or "")
                if not edge_key or seg_end not in ("start", "end"):
                    continue
    
                try:
                    length = float(item.get("length", 0.0) or 0.0)
                except Exception:
                    length = 0.0
    
                if length < 0:
                    length = 0.0
    
                trim_map.setdefault(edge_key, {"start": 0.0, "end": 0.0})
                trim_map[edge_key][seg_end] = max(trim_map[edge_key][seg_end], length)
    
        return trim_map
        
    @staticmethod
    def resolveSegmentEndpoint(base_point, direction, seg_obj):
        return hvaclib.compute_port_position(
            base_point,
            direction,
            hvaclib.get_segment_section_params(seg_obj),
            getattr(seg_obj, "Attachment", "Center"),
            getattr(seg_obj, "Offset", FreeCAD.Vector(0,0,0)),
            getattr(seg_obj, "ProfileXAxis", FreeCAD.Vector(0, 0, 0))
        )
    
    @staticmethod
    def resolveSegmentEndTrims(trim_entry):
        """
        Resolve explicit end-mapped trim contribution for a segment.
        """
        if not trim_entry:
            return 0.0, 0.0
    
        ts = max(0.0, float(trim_entry.get("start", 0.0) or 0.0))
        te = max(0.0, float(trim_entry.get("end", 0.0) or 0.0))
        return ts, te
        

class DuctNetworkViewProvider:
    """A View Provider for the HVAC duct network object"""

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def __getstate__(self):
        # Create a copy of the state and remove the non-serializable object
        state = self.__dict__.copy()
        if 'Object' in state:
            del state['Object']
        return state

    def __setstate__(self, state):
        # Restore the state
        self.__dict__.update(state)
        # self.Object will be restored later in attach()

    def getIcon(self):
        return hvaclib.get_icon_path("DuctsIcon.svg")

    def setEdit(self, vobj, mode):
        panel = TaskPanel.TaskPanelEditDuctNetwork(vobj.Object,
            callback_add_base_object = DuctNetwork.addBaseObject,
            callback_remove_base_object = DuctNetwork.removeBaseObject
        )
        Gui.Control.showDialog(panel)
        return True

    def unsetEdit(self, vobj, mode):
        Gui.Control.closeDialog()
        return True

    def doubleClicked(self, vobj):
        obj = vobj.Object
        # Make it the active network
        activate_duct_network(obj, set_edit=False)
        return True

    def claimChildren(self):
        obj = self.Object
        kids = []
        try:
            if obj.Base: kids.append(obj.Base)
            if obj.Geometry: kids.append(obj.Geometry)
            if obj.Topology: kids.append(obj.Topology)
        except Exception:
            pass
        return kids

    def canDropObjects(self):
        # Returning False prevents users from dragging items into this group via the Tree View
        return False

    def canDragObjects(self):
        # Prevents users from dragging the managed folders OUT of the group
        return False
        
    def onDelete(self, vobj, subelements):
        net = vobj.Object
        delete_duct_networks([net], remove_internal_only=True)
        return True


#=================================================
# General functions
#=================================================


def create_new_duct_network(name="DuctNetwork", set_active=True):
    """Create new duct network"""
    # Create new duct netowork and create default folders
    net = DuctNetwork.createObject(name)
    FreeCAD.Console.PrintMessage("HVAC - New DuctNetwork created")
    if set_active:
        # Set as active network and enable edit mode
        activate_duct_network(net, set_edit=False)

def activate_duct_network(net, set_edit=False):
    DuctNetwork.setActive(net)
    # Set network to edit mode
    if set_edit:
        Gui.ActiveDocument.setEdit(net.Name)
    else:
      pass
    hvaclib.refreshState()

def modify_duct_network(net):
    """Modify the selected HVAC duct network object"""
    # Set as active network and enable edit mode
    activate_duct_network(net, set_edit=True)
    FreeCAD.Console.PrintMessage("HVAC - Edit DuctNetwork completed")

def delete_duct_networks(nets, remove_internal_only=False):
    """Delete the selected HVAC duct network object"""
    doc = FreeCAD.ActiveDocument
    for net in nets:
        if net.Document != doc:
            continue
            
        if hasattr(net, "Proxy") and net.Proxy:
            net.Proxy._allow_internal_delete = True
            
        if hasattr(net, "Geometry") and net.Geometry:
            for obj in list(net.Geometry.OutList):
                DuctNetwork.removeGeometryObject(net, obj)
            doc.removeObject(net.Geometry.Name)
            
        if hasattr(net, "Base") and net.Base:
            for obj in list(net.Base.OutList):
                net.Base.removeObject(obj)
            doc.removeObject(net.Base.Name)
            
        if not remove_internal_only:
            doc.removeObject(net.Name)
    hvaclib.refreshState()
    FreeCAD.Console.PrintMessage("HVAC - Deleted selected {} DuctNetwork(s)".format(len(nets)))
