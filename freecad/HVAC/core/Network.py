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
import math
import json
import traceback
from dataclasses import asdict
import FreeCAD
import FreeCADGui as Gui
from pivy import coin
from PySide import QtWidgets, QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from ..utils import hvaclib
from ..utils import materials as hvac_materials
from ..ui import TaskPanel
from ..core.NetworkParser import DuctNetworkParser
from ..core.Segment import DuctSegment
from ..core.Junction import DuctJunction, DuctJunctionVirtual
from ..core.Component import DuctComponent


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

    def dumps(self):
        return None

    def loads(self, state):
        pass

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
            # DuctComponent objects live in this folder's OutList (so
            # collectComponentObjects can find them), but they're claimed
            # by their parent DuctJunction for tree display (see
            # DuctJunctionViewProvider.claimChildren) -- filter them out
            # here or they'd show up twice.
            return [o for o in self.Object.OutList if not hvaclib.isDuctComponent(o)]
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
        self._edge_key_remap = {}
        self._parser = None
        self.setProperties(obj)

    def dumps(self):
        return None

    def loads(self, state):
        # Block sync to prevent premature execution during document restore
        # due to observer scheduling sync
        self._sync_suspended = True

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
        self._edge_key_remap = {}
        self._parser = None
        self.setProperties(obj)
        self.requestSync(initial_sync=True)
        # Resotore sync suspension after document restore
        self._sync_suspended = False
        
    def execute(self, obj):
        """Manual recompute of the network triggers deferred synchronization."""
        if self._sync_in_progress:
            return
        self.requestSync()

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

        if "DefaultInsulationThickness" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyLength",
                "DefaultInsulationThickness",
                "HVAC Types",
                "Default insulation thickness for new segments"
            )

        # Materials::PropertyMaterial, not App::PropertyLinkGlobal -- see
        # the matching comment in Segment.py's setProperties(). Applied
        # onto new segments/components by their own applyOwnerDefaults()
        # (Segment.py/Component.py) when they don't already have a
        # material of their own.
        if "DefaultCasingMaterial" not in obj.PropertiesList:
            obj.addProperty(
                "Materials::PropertyMaterial",
                "DefaultCasingMaterial",
                "HVAC Types",
                "Default casing material for new segments/components",
                16,  # Prop_NoRecompute
            )

        if "DefaultInsulationMaterial" not in obj.PropertiesList:
            obj.addProperty(
                "Materials::PropertyMaterial",
                "DefaultInsulationMaterial",
                "HVAC Types",
                "Default insulation material for new segments/components",
                16,  # Prop_NoRecompute
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

        if not getattr(obj, "DefaultInsulationThickness", 0):
            obj.DefaultInsulationThickness = 25.0

        if not getattr(obj.DefaultCasingMaterial, "Name", ""):
            material = hvac_materials.get_material_by_uuid(hvac_materials.GALVANIZED_STEEL_UUID)
            if material is not None:
                obj.DefaultCasingMaterial = material

        if not getattr(obj.DefaultInsulationMaterial, "Name", ""):
            material = hvac_materials.get_material_by_uuid(hvac_materials.NITRILE_RUBBER_UUID)
            if material is not None:
                obj.DefaultInsulationMaterial = material

        # -------------------------------------------------
        # Air properties used for airflow/pressure-drop calculation
        # -------------------------------------------------

        if "AirDensity" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "AirDensity",
                "HVAC Air Properties",
                "Air density (kg/m3) used for pressure-drop calculation"
            )

        if "AirKinematicViscosity" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "AirKinematicViscosity",
                "HVAC Air Properties",
                "Air kinematic viscosity (m2/s) used for Reynolds number"
            )

        if "DefaultRoughness" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyLength",
                "DefaultRoughness",
                "HVAC Air Properties",
                "Default duct wall absolute roughness used when a segment's own Roughness is 0"
            )

        if not getattr(obj, "AirDensity", 0):
            obj.AirDensity = 1.204  # standard air, 20 degC, sea level

        if not getattr(obj, "AirKinematicViscosity", 0):
            obj.AirKinematicViscosity = 1.51e-5

        if not getattr(obj, "DefaultRoughness", 0):
            obj.DefaultRoughness = 0.09  # mm, galvanized steel

        # -------------------------------------------------
        # Duct sizing (constant velocity / constant friction rate / static regain)
        # -------------------------------------------------

        if "SizingMethod" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyEnumeration",
                "SizingMethod",
                "HVAC Duct Sizing",
                "Method used by the Size Ducts command"
            )
            obj.SizingMethod = ["ConstantVelocity", "ConstantFrictionRate", "StaticRegain"]
            obj.SizingMethod = "ConstantVelocity"

        if "TargetVelocity" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "TargetVelocity",
                "HVAC Duct Sizing",
                "Target duct velocity (m/s) used when SizingMethod is ConstantVelocity, and as the "
                "starting velocity for sections leaving the balancing terminal when SizingMethod is StaticRegain"
            )

        if "TargetFrictionRate" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "TargetFrictionRate",
                "HVAC Duct Sizing",
                "Target friction rate (Pa/m) used when SizingMethod is ConstantFrictionRate"
            )

        if "StaticRegainFactor" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "StaticRegainFactor",
                "HVAC Duct Sizing",
                "Fraction of velocity-pressure regain actually recovered (0-1) when SizingMethod is StaticRegain"
            )

        if "MinimumVelocity" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "MinimumVelocity",
                "HVAC Duct Sizing",
                "Velocity floor (m/s) used when SizingMethod is StaticRegain, since regain sizing alone "
                "can propose impractically large/slow ducts on small or low-velocity branches"
            )

        if "RectangularSizingMode" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyEnumeration",
                "RectangularSizingMode",
                "HVAC Duct Sizing",
                "How the second dimension of a rectangular/oval duct is determined when sizing"
            )
            obj.RectangularSizingMode = ["FixedAspectRatio", "FixedHeight", "FixedWidth"]
            obj.RectangularSizingMode = "FixedAspectRatio"

        if "TargetAspectRatio" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyFloat",
                "TargetAspectRatio",
                "HVAC Duct Sizing",
                "Target Width:Height ratio used when RectangularSizingMode is FixedAspectRatio"
            )

        if "SizeRoundingIncrement" not in obj.PropertiesList:
            obj.addProperty(
                "App::PropertyLength",
                "SizeRoundingIncrement",
                "HVAC Duct Sizing",
                "Computed duct sizes are rounded up to the nearest multiple of this increment"
            )

        if not getattr(obj, "TargetVelocity", 0):
            obj.TargetVelocity = 5.0

        if not getattr(obj, "TargetFrictionRate", 0):
            obj.TargetFrictionRate = 1.0

        if not getattr(obj, "StaticRegainFactor", 0):
            obj.StaticRegainFactor = 0.75

        if not getattr(obj, "MinimumVelocity", 0):
            obj.MinimumVelocity = 2.5

        if not getattr(obj, "TargetAspectRatio", 0):
            obj.TargetAspectRatio = 2.0

        if not getattr(obj, "SizeRoundingIncrement", 0):
            obj.SizeRoundingIncrement = 10.0  # mm

    def getDefaultLibraryId(self):
        net = self.Object
        lib_id = getattr(net, "DefaultLibraryId", "")
        if lib_id:
            return lib_id

        lib = hvaclib.get_active_hvac_library()
        if lib:
            return lib.id
        return ""

    def getDefaultLibrary(self):
        lib_id = self.getDefaultLibraryId()
        if not lib_id:
            return None
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        return reg.get_library(lib_id)

    def getDefaultSegmentProfile(self):
        net = self.Object
        profile = getattr(net, "DefaultSegmentProfile", "")
        if profile:
            return profile

        lib_id = self.getDefaultLibraryId()
        return hvaclib.HVACLibraryService.default_segment_profile_for_library(lib_id)
        
    def getDefaultAttachment(self):
        net = self.Object
        return str(getattr(net, "DefaultAttachment", "Center"))

    def getDefaultOffset(self):
        net = self.Object
        return FreeCAD.Vector(getattr(net, "DefaultOffset", FreeCAD.Vector(0, 0, 0)))

    ## Library defaults management
    
    def defaultSegmentSelection(self, kind='straight'):
        """
        Return network default segment library/profile/type.
        Used only when creating a new segment or resetting one to defaults.
        """
        library_id = self.getDefaultLibraryId()
        profile = self.getDefaultSegmentProfile()
        attachement = self.getDefaultAttachment()
        offset = self.getDefaultOffset()

        valid_profiles = hvaclib.HVACLibraryService.segment_profiles_for_library(library_id)
        if profile not in valid_profiles:
            profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(library_id)

        selection = hvaclib.HVACLibraryService.select_segment_type(
            library_id, profile, curved=(kind != "straight")
        )
        type_id = selection.type_def.id if selection.type_def else ""

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
        default_insulation_thickness=None,
        default_casing_material=None,
        default_insulation_material=None,
    ):
        """
        Apply network-level default type settings.
        (Used as callback for command)
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

        if default_insulation_thickness is not None and abs(float(getattr(network_obj, "DefaultInsulationThickness", 0.0)) - float(default_insulation_thickness)) > 1e-9:
            network_obj.DefaultInsulationThickness = float(default_insulation_thickness)
            changed = True

        if default_casing_material is not None:
            network_obj.DefaultCasingMaterial = default_casing_material
            changed = True

        if default_insulation_material is not None:
            network_obj.DefaultInsulationMaterial = default_insulation_material
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

            # A DuctJunction has no type of its own any more -- retarget to
            # its Primary DuctComponent so "reset to defaults" on a selected
            # junction node does something useful. There's no automatic
            # default for a user-added Inline component (it was a deliberate
            # choice, not something the classifier picked), so those are
            # simply skipped.
            if hvaclib.isDuctJunction(obj):
                obj = obj.Proxy.getPrimaryComponent()
                if obj is None:
                    continue
            elif hvaclib.isDuctComponent(obj) and getattr(obj, "ComponentRole", "") != "Primary":
                continue

            net = DuctNetwork.getOwnerNetwork(obj)
            if net is None:
                continue

            default_lib = net.Proxy.getDefaultLibrary()
            if default_lib is None:
                continue

            proxy = getattr(obj, "Proxy", None)

            if hvaclib.isDuctSegment(obj):
                default_profile = net.Proxy.getDefaultSegmentProfile()
                if not default_profile:
                    default_profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(default_lib.id)

                kind = hvaclib.BaseCurveKind(obj.SourceObjectName, obj.SourceIndex)
                # Explicit reset always runs fresh automatic selection against
                # the network's default library -- it may replace an otherwise
                # sticky manual TypeId, unlike normal sync (see
                # HVACLibraryRegistry.resolve_sticky_type).
                selection = hvaclib.HVACLibraryService.select_segment_type(
                    default_lib.id,
                    default_profile,
                    curved=(kind != "straight"),
                )
                default_type_id = selection.type_def.id if selection.type_def else ""

                if hasattr(obj, "LibraryId") and obj.LibraryId != default_lib.id:
                    obj.LibraryId = default_lib.id
                    changed = True

                if hasattr(obj, "Profile") and obj.Profile != default_profile:
                    obj.Profile = default_profile
                    changed = True

                if hasattr(obj, "TypeId") and obj.TypeId != default_type_id:
                    obj.TypeId = default_type_id
                    changed = True
                
                default_attachment = net.Proxy.getDefaultAttachment()
                if getattr(obj, "Attachment", "Center") != default_attachment:
                    obj.Attachment = default_attachment
                    changed = True
                
                default_offset = net.Proxy.getDefaultOffset()
                if FreeCAD.Vector(getattr(obj, "Offset", FreeCAD.Vector(0, 0, 0))) != default_offset:
                    obj.Offset = default_offset
                    changed = True

            elif hvaclib.isDuctComponent(obj):
                parent = doc.getObject(getattr(obj, "ParentJunctionName", ""))
                if parent is None:
                    continue
                topology = getattr(parent, "Topology", "")
                family_key = getattr(parent, "Family", "")
                try:
                    analysis = json.loads(getattr(parent, "AnalysisJson", "") or "{}")
                except Exception:
                    analysis = {}
                connected_ports = list(analysis.get("connected_ports", []) or [])
                profile = hvaclib.HVACLibraryService.match_profile_from_ports(connected_ports)

                # Explicit reset always runs fresh automatic selection against
                # the network's default library -- see the segment branch above.
                selection = hvaclib.HVACLibraryService.select_junction_type(
                    default_lib.id,
                    topology=topology,
                    family_key=family_key,
                    profile=profile,
                    connected_ports=connected_ports,
                )
                default_type_id = selection.type_def.id if selection.type_def else ""

                if hasattr(obj, "LibraryId") and obj.LibraryId != default_lib.id:
                    obj.LibraryId = default_lib.id
                    changed = True

                if hasattr(obj, "TypeId") and obj.TypeId != default_type_id:
                    obj.TypeId = default_type_id
                    changed = True

            if proxy and hasattr(proxy, "applyTypeSchema"):
                try:
                    changed = proxy.applyTypeSchema() or changed
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

    def addBaseObject(self, obj):
        net = self.Object
        if not net or not obj:
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
            net.Proxy.requestSync()
        net.Document.recompute()
        return True
        
    def addVirtualJunctionObject(self, member_node_keys, member_points):
        obj = self.Object
        doc = obj.Document
        name = doc.getUniqueObjectName("VirtualJunction")
        vj = DuctJunctionVirtual.create(doc, name, owner=obj, member_node_keys=member_node_keys, member_points=member_points)
        obj.Topology.addObject(vj)
        return vj

    def removeBaseObject(self, obj):
        net = self.Object
        if not net or not obj:
            return False
        if not hasattr(net, "Base") or net.Base is None:
            return False
        if net.Document != getattr(obj, "Document", None):
            return False
        if obj not in net.Base.OutList:
            return False
    
        net.Base.removeObject(obj)
        if getattr(net, "Proxy", None):
            net.Proxy.requestSync()
        net.Document.recompute()
        return True

    def removeGeometryObject(self, obj):
        """Remove a derived geometry object from the Geometry folder and document."""
        net = self.Object
        # A junction owns its DuctComponent children -- deleting one must
        # cascade to delete them too, or they'd be left as orphaned objects
        # with a dangling ParentJunctionName.
        if hvaclib.isDuctJunction(obj) and getattr(obj, "Proxy", None):
            for comp in list(obj.Proxy.getComponents()):
                self.removeGeometryObject(comp)
        if (hvaclib.isDuctSegment(obj) or hvaclib.isDuctJunction(obj) or hvaclib.isDuctComponent(obj)) \
                and getattr(obj, "Proxy", None):
            obj.Proxy._allow_delete = True
        if hasattr(net, "Geometry") and net.Geometry and obj in net.Geometry.OutList:
            net.Geometry.removeObject(obj)
        net.Document.removeObject(obj.Name)
        return True

    def collectSegmentObjects(self):
        net = self.Object
        segments = {}
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return segments
        for child in list(geometry.OutList):
            if not hvaclib.isDuctSegment(child):
                continue
            key = getattr(child, "SegmentKey", "")
            if not key and getattr(child, "SourceObjectName", ""):
                key = hvaclib.makeLineKey(child.SourceObjectName, child.SourceIndex)
            if key:
                segments[key] = child
        return segments
        
    def collectJunctionObjects(self):
        net = self.Object
        junctions = {}
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return junctions
        for child in list(geometry.OutList):
            if not hvaclib.isDuctJunction(child):
                continue
            key = getattr(child, "NodeKey", "")
            if key:
                junctions[key] = child
        return junctions

    def collectComponentObjects(self):
        """{parent_junction_name: [component_obj, ...]}, Primary-first then
        Inline grouped by AttachedEdgeKey/PortSequence -- built once per
        sync so syncJunctionComponents doesn't rescan Geometry per node."""
        net = self.Object
        by_parent = {}
        geometry = getattr(net, "Geometry", None)
        if geometry is None:
            return by_parent
        for child in list(geometry.OutList):
            if not hvaclib.isDuctComponent(child):
                continue
            by_parent.setdefault(getattr(child, "ParentJunctionName", ""), []).append(child)
        for lst in by_parent.values():
            lst.sort(key=lambda c: (
                0 if getattr(c, "ComponentRole", "") == "Primary" else 1,
                getattr(c, "AttachedEdgeKey", ""),
                int(getattr(c, "PortSequence", 0)),
                c.Name,
            ))
        return by_parent


    def collectVirtualJunctionObjects(self):
        topology_objs = []
        for child in list(getattr(self.Object.Topology, "Group", []) or []):
            if hvaclib.isDuctJunctionVirtual(child):
                topology_objs.append(child)
        return topology_objs
        
    def getNodeGroups(self, parser):
        """Compile node groups from virtual junction objects and the parser's node ID map."""
        node_groups = []
        
        node_id_by_key = {parser.geometric_node_key(nid): nid for nid in parser.geometric_nodes()}
        virtual_objs = self.collectVirtualJunctionObjects()
        
        for vj in virtual_objs:
            keys = vj.Proxy.getMemberNodeKeys()
            ids = []
    
            for key in keys:
                nid = node_id_by_key.get(key)
                if nid is not None:
                    ids.append(nid)
    
            ids = sorted(set(ids))
            if len(ids) >= 2:
                node_groups.append(ids)
    
        return node_groups
    
    def setActive(self):
        """Set this DuctNetwork as the active container in the 3D view."""
        Gui.ActiveDocument.ActiveView.setActiveObject(DuctNetwork.CONTEXT_KEY, self.Object)

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
        
    def selectAllGeometry(self):
        """Select all generated duct objects under the Geometry folder."""
        net = self.Object
        Gui.Selection.clearSelection()
    
        for child in net.Geometry.OutList:
            if not (hvaclib.isDuctSegment(child) or hvaclib.isDuctJunction(child) or hvaclib.isDuctComponent(child)):
                continue
            try:
                Gui.Selection.addSelection(child)
            except TypeError:
                Gui.Selection.addSelection(child.Document.Name, child.Name)

    def showAllGeometry(self):
        geometry = self.Object.Geometry
        if getattr(geometry, "ViewObject", None):
            try:
                geometry.ViewObject.Visibility = True
            except Exception:
                pass

        for obj in list(geometry.OutList):
            if hvaclib.isDuctSegment(obj) or hvaclib.isDuctJunction(obj) or hvaclib.isDuctComponent(obj):
                self._setGeometryVisibilityDeferred(obj, True)

    def hideAllGeometry(self):
        geometry = self.Object.Geometry

        if getattr(geometry, "ViewObject", None):
            try:
                geometry.ViewObject.Visibility = False
            except Exception:
                pass

        for obj in list(geometry.OutList):
            if hvaclib.isDuctSegment(obj) or hvaclib.isDuctJunction(obj) or hvaclib.isDuctComponent(obj):
                self._setGeometryVisibilityDeferred(obj, False)
                
    def _segmentFromBaseObject(self, seg, base_obj):
        return (
            seg is not None
            and base_obj is not None
            and hvaclib.isDuctSegment(seg)
            and getattr(seg, "SourceObjectName", "") == base_obj.Name
        )
                
    def showAllJunctionGeometry(self):
        # DuctJunction itself has no Shape/visual presence any more -- the
        # thing that actually needs hiding/showing while editing base
        # geometry is each junction's DuctComponent children.
        for obj in list(self.Object.Geometry.OutList):
            if hvaclib.isDuctComponent(obj):
                self._setGeometryVisibilityDeferred(obj, True)

    def hideAllJunctionGeometry(self):
        for obj in list(self.Object.Geometry.OutList):
            if hvaclib.isDuctComponent(obj):
                self._setGeometryVisibilityDeferred(obj, False)
    
    def showGeometryForBaseObject(self, base_obj):
        net = self.Object
        geometry = getattr(net, "Geometry", None)
        if geometry is None or base_obj is None:
            return
        for seg in list(geometry.OutList):
            if self._segmentFromBaseObject(seg, base_obj):
                self._setGeometryVisibilityDeferred(seg, True)
                
    def hideGeometryForBaseObject(self, base_obj):
        net = self.Object
        geometry = getattr(net, "Geometry", None)
        if geometry is None or base_obj is None:
            return
        for seg in list(geometry.OutList):
            if self._segmentFromBaseObject(seg, base_obj):
                self._setGeometryVisibilityDeferred(seg, False)
    
    def setBaseObjectEditing(self, base_obj, editing):
        net = self.Object
        if net is None or base_obj is None:
            return
        if editing:
            self._hidden_source_names.add(base_obj.Name)
            self.hideGeometryForBaseObject(base_obj)
            self.hideAllJunctionGeometry()
        else:
            self._hidden_source_names.discard(base_obj.Name)
            self.showGeometryForBaseObject(base_obj)
            self.showAllJunctionGeometry()
        
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
        return hvaclib.isDuctJunction(obj) or hvaclib.isDuctSegment(obj) or hvaclib.isDuctComponent(obj)
                
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
    
    def syncVirtualJunctions(self, parser, initial_sync=False):
        """Update the MemberNodes property of virtual junction objects
            from the actual node keys from parser."""
        obj = self.Object
        for vj in self.collectVirtualJunctionObjects():
            # Use quantized point keys to look up new node keys from parser
            stored_points = vj.Proxy.getMemberPoints()
            stored_keys = vj.Proxy.getMemberNodeKeys()
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
                owner=obj, 
                member_node_keys=member_keys, 
                member_points=member_points
            )

    def syncSegments(self, parser, initial_sync=False):
        """
        Synchronize derived DuctSegment objects with the base geometry.
    
        Segment LibraryId / Profile / TypeId are object-owned values.
        Network defaults are only used when creating a new segment or repairing
        missing/invalid values.
        """
        net = self.Object
        doc = net.Document
        geometry = getattr(net, "Geometry", None)
        if doc is None or geometry is None:
            return False
    
        default_lib = self.getDefaultLibrary()
        if default_lib is None:
            return False
    
        changed = False
        existing_segments = self.collectSegmentObjects()
        trim_map = self.collectSegmentTrimMap()
        live_objs = set()

        if initial_sync:
            # Reset once per initial-sync pass (this method also runs a
            # second time per pass with initial_sync=False, in Stage 3 --
            # that call must not wipe what Stage 1 just recorded).
            self._edge_key_remap = {}

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
                    # Record old_key -> key so syncJunctionComponents can
                    # carry forward any DuctComponent.AttachedEdgeKey that
                    # was snapshotted under the old (pre-reload) tag --
                    # see that method for why this is needed.
                    self._edge_key_remap[matched_old_key] = key
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
                defaults = self.defaultSegmentSelection(kind=kind)
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
                    self._setGeometryVisibilityDeferred(segment_obj, False)
                else:
                    self._setGeometryVisibilityDeferred(segment_obj, True)
    
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
            library_id = getattr(segment_obj, "LibraryId", "") or self.getDefaultLibraryId()
            profile = getattr(segment_obj, "Profile", "")
            valid_profiles = hvaclib.HVACLibraryService.segment_profiles_for_library(library_id)
            if profile not in valid_profiles:
                profile = hvaclib.HVACLibraryService.default_segment_profile_for_library(library_id)

            kind = hvaclib.BaseCurveKind(edge_ref.obj_name, edge_ref.local_index)
            family = "straight_segment" if kind == "straight" else "curved_segment"

            # Sticky registry-driven selection: retain the current TypeId if
            # it's still a compatible real model, otherwise auto-select.
            current_type_id = getattr(segment_obj, "TypeId", "")
            selection = hvaclib.HVACLibraryService.resolve_segment_type(
                library_id,
                current_type_id,
                profile,
                curved=(kind != "straight"),
            )
            type_id = selection.type_def.id if selection.type_def else ""

            # Update metadata based on updated data
            meta_changed = segment_obj.Proxy.updateMetadata(
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
                family=family,
                type_id=type_id,
                library_id=library_id,
                profile=profile
            )
            changed = changed or meta_changed
    
            # Update property schema based on type ID and library ID
            schema_changed = segment_obj.Proxy.applyTypeSchema()
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
                self.removeGeometryObject(segment_obj)
                changed = True
        
        return changed
        
    def syncJunctions(self, parser, initial_sync=False):
        """
        Synchronize derived DuctJunction objects with parser nodes, and
        (via syncJunctionComponents) each junction's Primary/Inline
        DuctComponent children.

        A DuctJunction itself carries no LibraryId/TypeId any more -- see
        syncJunctionComponents for how the Primary component's type is
        resolved/retained and how the component chain is composed.
        """
        net = self.Object
        doc = net.Document
        geometry = getattr(net, "Geometry", None)
        if doc is None or geometry is None:
            return False

        changed = False
        live_objs = set()

        # Get default library for segment profiles
        default_lib = self.getDefaultLibrary()
        if default_lib is None:
            return False

        # Collect existing junctions, their components, and the segment map
        existing_junctions = self.collectJunctionObjects()
        components_by_parent = self.collectComponentObjects()
        segment_map = self.collectSegmentObjects()

        # Inspect each node from the parser and update junction objects
        for node_id in parser.nodes():
            node_key = parser.node_key(node_id)

            # Get junction analysis
            junction_analysis = parser.build_junction_analysis(node_id, segment_map)
            if not junction_analysis:
                continue
            analysis_dict = asdict(junction_analysis)
            analysis_json = json.dumps(analysis_dict)
            connected_ports = analysis_dict["connected_ports"]
            degree = junction_analysis.degree
            topology = junction_analysis.topology
            family = junction_analysis.family_key
            point = junction_analysis.point
            connected_edge_keys = [p.edge_key for p in junction_analysis.connected_ports]
            match_profile = hvaclib.HVACLibraryService.match_profile_from_ports(connected_ports)

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
                    # components_by_parent is keyed by the junction OBJECT's
                    # own .Name (ParentJunctionName), not by NodeKey -- that
                    # never changes across a resync, so no remapping is
                    # needed here (unlike existing_junctions, which really is
                    # keyed by NodeKey).
            # Else find element based on key
            else:
                junction_obj = existing_junctions.get(node_key)

            is_new_junction = junction_obj is None

            # If junction does not exist, create a new one
            if junction_obj is None:
                junction_obj = DuctJunction.create(
                    doc,
                    "{}_Junc_{}".format(net.Name, node_id),
                    owner=net,
                    node_id=node_id,
                    node_key=node_key,
                    center_point=point,
                    degree=degree,
                    topology=topology
                )
                changed = True

            # Add junction to geometry folder if not already present
            if junction_obj not in geometry.OutList:
                geometry.addObject(junction_obj)
                changed = True

            live_objs.add(junction_obj)

            # Update metadata based on updated data
            meta_changed = junction_obj.Proxy.updateMetadata(
                owner=net,
                node_id=node_id,
                node_key=node_key,
                center_point=point,
                degree=degree,
                topology=topology,
                family=family,
                connected_edge_keys=connected_edge_keys,
                analysis_json=analysis_json,
            )
            changed = changed or meta_changed

            # Create/update this junction's Primary component (sticky type
            # resolution), retain its Inline components, and compose the
            # whole chain's local ports.
            components_changed = self.syncJunctionComponents(
                junction_obj, topology, family, match_profile, connected_ports,
                components_by_parent.get(junction_obj.Name, []), default_lib,
                hide_new=bool(self._hidden_source_names) if is_new_junction else None,
            )
            changed = changed or components_changed

            # Update label for segment object based on source object and edge index
            new_label = DuctJunction.labelFor(family, node_id)
            if junction_obj.Label != new_label:
                junction_obj.Label = new_label
                changed = True

        # Remove old junctions
        for junction_obj in list(existing_junctions.values()):
            if junction_obj not in live_objs:
                self.removeGeometryObject(junction_obj)
                changed = True

        return changed

    def syncJunctionComponents(
        self, junction_obj, topology, family, match_profile, connected_ports,
        existing_components, default_lib, hide_new=None,
    ):
        """
        Create/update a junction's Primary DuctComponent (sticky type
        resolution, same registry policy the junction itself used to run
        directly) and compose the whole component chain's local ports.

        Inline components are never auto-selected/replaced here -- they're
        retained as-is (only their type schema is re-applied, in case a
        library reload changed their declared properties). Each Inline
        component is retained only while its own AttachedEdgeKey is still
        one of this junction's real edges -- if that edge disappears (a
        topology/geometry change resnapped or removed it), that component
        is deleted with a console warning rather than left silently
        orphaned. A junction losing its through/2-port shape no longer
        drops Inline components on its own -- each edge's chain is
        independent of the junction's overall topology.

        Before that check, AttachedEdgeKey is carried forward through
        self._edge_key_remap (built by syncSegments(initial_sync=True),
        which always runs first in a sync pass -- see _runDeferredSync):
        base-geometry Tags regenerate on every document reload, so without
        this an Inline component's AttachedEdgeKey (snapshotted under the
        old tag) would never match the new tag and would be wrongly
        dropped on the very first sync after every file reopen.
        """
        net = self.Object
        doc = net.Document
        geometry = net.Geometry
        changed = False

        # Defensive: a junction should never end up with more than one
        # Primary component, but self-heal if it somehow does (e.g. a
        # document synced under a since-fixed lookup bug) rather than
        # silently treating extras as an Inline-like chain.
        primaries = [c for c in existing_components if getattr(c, "ComponentRole", "") == "Primary"]
        primary = None
        if primaries:
            primaries.sort(key=lambda c: c.Name)
            primary = primaries[0]
            extras = primaries[1:]
            if extras:
                FreeCAD.Console.PrintWarning(
                    "HVAC - Junction '{}' had {} duplicate Primary component(s); removing the extras.\n".format(
                        junction_obj.Label, len(extras)
                    )
                )
                for comp in extras:
                    self.removeGeometryObject(comp)
                changed = True

        real_edge_keys = {p.get("edge_key") for p in connected_ports}
        inline_components = [c for c in existing_components if getattr(c, "ComponentRole", "") == "Inline"]

        # A document reload regenerates every base-geometry Tag, so a real
        # edge_key an Inline component was attached to under the old tag
        # would otherwise never match again -- carry AttachedEdgeKey
        # forward through the same old-tag -> new-tag map syncSegments just
        # built (Stage 1 always runs before this), exactly like SegmentKey
        # itself gets carried forward.
        edge_key_remap = getattr(self, "_edge_key_remap", None) or {}
        for comp in inline_components:
            old_key = getattr(comp, "AttachedEdgeKey", "")
            new_key = edge_key_remap.get(old_key)
            if new_key is not None and new_key != old_key:
                comp.AttachedEdgeKey = new_key
                changed = True

        keep, drop = [], []
        for comp in inline_components:
            (keep if getattr(comp, "AttachedEdgeKey", "") in real_edge_keys else drop).append(comp)

        if drop:
            FreeCAD.Console.PrintWarning(
                "HVAC - Junction '{}' no longer has edge(s) {}; removing {} inline component(s).\n".format(
                    junction_obj.Label,
                    sorted({getattr(c, "AttachedEdgeKey", "") for c in drop}),
                    len(drop),
                )
            )
            for comp in drop:
                self.removeGeometryObject(comp)
            changed = True
        inline_components = keep

        if primary is None:
            primary = DuctComponent.create(
                doc, "{}_Comp0".format(junction_obj.Name),
                parent_junction=junction_obj, role="Primary", attached_edge_key="", port_sequence=0, owner_network=net,
            )
            changed = True
            if hide_new is not None:
                self._setGeometryVisibilityDeferred(primary, not hide_new)

        if primary not in geometry.OutList:
            geometry.addObject(primary)
            changed = True

        # Sticky registry-driven selection: retain the Primary's current
        # TypeId if it's still a compatible real model, otherwise
        # auto-select (falling back to a placeholder if no model matches).
        # This is exactly the policy DuctJunction itself used to run
        # directly before LibraryId/TypeId moved onto the Primary component.
        library_id = getattr(primary, "LibraryId", "") or default_lib.id
        current_type_id = getattr(primary, "TypeId", "")
        selection = hvaclib.HVACLibraryService.resolve_junction_type(
            library_id,
            current_type_id,
            topology=topology,
            family_key=family,
            profile=match_profile,
            connected_ports=connected_ports,
        )
        type_id = selection.type_def.id if selection.type_def else ""

        meta_changed = primary.Proxy.updateMetadata(
            parent_junction=junction_obj, role="Primary", attached_edge_key="", port_sequence=0,
            library_id=library_id, type_id=type_id,
        )
        changed = changed or meta_changed

        schema_changed = primary.Proxy.applyTypeSchema()
        changed = changed or schema_changed

        new_label = DuctComponent.labelFor(
            "Primary", selection.type_def.label if selection.type_def else ""
        )
        if primary.Label != new_label:
            primary.Label = new_label
            changed = True

        # Inline components are retained as-is (never auto-replaced) --
        # just re-apply their schema in case their type's declared
        # properties changed underneath them (e.g. a library reload).
        for comp in inline_components:
            if comp not in geometry.OutList:
                geometry.addObject(comp)
                changed = True
            if comp.Proxy.applyTypeSchema():
                changed = True

        # Build the ordered chain and write each component's local ports.
        junction_obj.Proxy.composeComponents()

        for comp in junction_obj.Proxy.getComponents():
            comp.touch()

        return changed
                            
    def refreshBaseDirectionOverlay(self, parser):
        obj = self.Object
        try:
            if FreeCAD.GuiUp:
                vp = obj.ViewObject.Proxy
                vp.refreshBaseDirectionArrows(parser)
        except Exception:
            FreeCAD.Console.PrintError("Error refreshing base direction overlay.\n")
    
    def requestSync(self, initial_sync=None, force_recompute=False):
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
        QtCore.QTimer.singleShot(0, lambda force_recompute=force_recompute: self._runDeferredSync(force_recompute))
        
    def suspendSync(self):
        self._sync_suspended = True
    
    def resumeSync(self, request_sync=True):
        self._sync_suspended = False
        if request_sync:
            self.requestSync()
            
    def getParser(self, rebuild=False, set_node_groups=True):
        if self._parser is None or rebuild:
            parser = DuctNetworkParser(list(self.Object.Base.OutList))
            if set_node_groups:
                node_groups = self.getNodeGroups(parser)
                parser.set_node_groups(node_groups)
            self._parser = parser
        return self._parser

    def aggregateAllConnectionLengths(self):
        """
        Refresh every live junction's aggregate ConnectionLengthsJson from
        its component chain (see DuctJunction.aggregateConnectionLengths).
        Run after a recompute so each component's own execute() has had a
        chance to write its own ConnectionLengthsJson first.
        """
        for junction_obj in self.collectJunctionObjects().values():
            proxy = getattr(junction_obj, "Proxy", None)
            if proxy is not None and hasattr(proxy, "aggregateConnectionLengths"):
                proxy.aggregateConnectionLengths()


    def _runDeferredSync(self, force_recompute=False):
        obj = self.Object
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
                self.syncVirtualJunctions(parser, initial_sync=True)
                # Rebuild parser after syncing virtual junctions
                parser = self.getParser(rebuild=True)
                
                # Stage 1: Sync segments first to update edge data after document reload
                self.syncSegments(parser, initial_sync=True)
                obj.Document.recompute()
                
                # Stage 2: Sync junctions/components, so that each component's
                # execute() writes its own ConnectionLengthsJson
                self.syncJunctions(parser, initial_sync=True)
                obj.Document.recompute()
                # Aggregate each junction's ConnectionLengthsJson from its
                # now-computed component chain, for Stage 3 to consume.
                self.aggregateAllConnectionLengths()

                # Stage 3: Sync segments which consume the junction trim data
                self.syncSegments(parser, initial_sync=False)
                obj.Document.recompute()
                
            else:  
                # Get parser
                parser = self.getParser(rebuild=True, set_node_groups=False)
                # Update VirtualJunction keys
                self.syncVirtualJunctions(parser, initial_sync=False)
                # Rebuild parser after syncing virtual junctions
                parser = self.getParser(rebuild=True)
                
                # Stage 1: Sync segments first to update edge data
                changed_segments = self.syncSegments(parser, initial_sync=False)
                if changed_segments or force_recompute:
                    obj.Document.recompute()
                    
                # Stage 2: Sync junctions/components, for creating ports; so
                # that each component's execute() writes its own
                # ConnectionLengthsJson
                changed_junctions = self.syncJunctions(parser, initial_sync=False)
                if changed_junctions or force_recompute:
                    obj.Document.recompute()
                # Aggregate each junction's ConnectionLengthsJson from its
                # now-computed component chain, for Stage 3 to consume.
                self.aggregateAllConnectionLengths()

                # Stage 3: Sync segments which consume the junction trim data
                changed_segments = self.syncSegments(parser, initial_sync=False)
                if changed_segments or force_recompute:
                    obj.Document.recompute()
                    
            self._initial_sync = False
            
            # Refresh visual base direction arrows using the same parser
            self.refreshBaseDirectionOverlay(parser)
    
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
            "Velocity": float(getattr(obj, "Velocity", 0.0)),
            "RectangularSizingMode": str(getattr(obj, "RectangularSizingMode", "UseNetworkDefault")),
            "TargetAspectRatio": float(getattr(obj, "TargetAspectRatio", 0.0)),
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
        if "Velocity" in params:
            set_if_needed("Velocity", params["Velocity"])
        if "RectangularSizingMode" in params:
            set_if_needed("RectangularSizingMode", params["RectangularSizingMode"])
        if "TargetAspectRatio" in params:
            set_if_needed("TargetAspectRatio", params["TargetAspectRatio"])

        return changed
    
    @staticmethod
    def applyTypeSelection(objects, library_id="", type_id=""):
        """
        Apply library/type selection to selected segment/junction objects.
        (Used as callback for command)
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

            # A DuctJunction carries no LibraryId/TypeId of its own -- retarget
            # to its Primary DuctComponent, so applying a type selection to a
            # selected junction node acts on "its" fitting.
            if hvaclib.isDuctJunction(obj):
                obj = obj.Proxy.getPrimaryComponent()
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
                    changed = proxy.applyTypeSchema() or changed
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
                    proxy.requestSync(force_recompute=True)
     
    @staticmethod
    def applyAddInlineComponent(junction, edge_key, library_id, type_id):
        """
        Create a new Inline DuctComponent on junction, attached to edge_key,
        and sync. (Used as callback for CommandAddInlineComponent's task
        panel -- TaskPanelAddInlineComponent.)
        """
        from .Component import DuctComponent

        if junction is None or not edge_key or not type_id:
            return

        net = DuctNetwork.getOwnerNetwork(junction)
        if net is None:
            return
        doc = net.Document

        # New Inline components default to the end of THIS EDGE's own chain
        # (PortSequence = that chain's current max + 10), leaving gaps so a
        # user can reorder/insert by editing PortSequence directly in the
        # property editor.
        existing_chain = junction.Proxy.getInlineComponents(edge_key)
        next_port_sequence = max((int(getattr(c, "PortSequence", 0)) for c in existing_chain), default=0) + 10
        # Name uniqueness is decoupled from edge_key/PortSequence -- an
        # edge_key can contain characters invalid in a FreeCAD object Name
        # (e.g. "Sketch001:0").
        name_index = len(junction.Proxy.getComponents())

        component = DuctComponent.create(
            doc, "{}_Comp{}".format(junction.Name, name_index),
            parent_junction=junction, role="Inline",
            attached_edge_key=edge_key, port_sequence=next_port_sequence,
            owner_network=net,
        )
        net.Geometry.addObject(component)
        component.LibraryId = library_id
        component.TypeId = type_id
        component.Proxy.applyTypeSchema()

        proxy = getattr(net, "Proxy", None)
        if proxy:
            proxy.requestSync(force_recompute=True)

    @staticmethod
    def applyPlacementSelection(objects, attachment=None, offset=None, profile_x_axis=None):
        """
        Set placement for selected objects
        (Used as callback for command)
        """
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
                    proxy.requestSync(force_recompute=True)

    @staticmethod
    def applyMaterialSelection(objects, casing_material=None, insulation_material=None):
        """
        Set CasingMaterial/InsulationMaterial on selected duct segment(s)/
        component(s) (used as callback for HVAC_EditMaterial). Unlike
        applyPlacementSelection, this never touches the object or re-syncs
        the owner network -- CasingMaterial/InsulationMaterial are
        Prop_NoRecompute, since picking a material never changes an
        object's own geometry, only its ViewProvider's rendered appearance.
        """
        for obj in objects or []:
            if obj is None:
                continue
            if casing_material is not None and hasattr(obj, "CasingMaterial"):
                obj.CasingMaterial = casing_material
            if insulation_material is not None and hasattr(obj, "InsulationMaterial"):
                obj.InsulationMaterial = insulation_material

    ## Trim map generation from junctions
    
    def collectSegmentTrimMap(self):
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
        net = self.Object
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

        self._baseDirectionRoot = coin.SoSeparator()
        self._baseDirectionRoot.setName("HVAC_BaseDirectionArrows")
        vobj.RootNode.addChild(self._baseDirectionRoot)

        self.ensureDirectionArrowProperties(vobj)

        try:
            vobj.addDisplayMode(self._baseDirectionRoot, "Direction Arrows")
            vobj.DisplayMode = "Direction Arrows"
        except Exception:
            pass

        self.refreshBaseDirectionArrows()

    def dumps(self):
        return None

    def loads(self, state):
        pass

    def getIcon(self):
        return hvaclib.get_icon_path("DuctsIcon.svg")

    def setEdit(self, vobj, mode):
        
        def callback_add_base_object(net, obj):
            net.Proxy.addBaseObject(obj)
        
        def callback_remove_base_object(net, obj):
            net.Proxy.removeBaseObject(obj)
            
        panel = TaskPanel.TaskPanelEditDuctNetwork(vobj.Object,
            callback_add_base_object = callback_add_base_object,
            callback_remove_base_object = callback_remove_base_object
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
        obj.Proxy.selectAllGeometry()
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
        
    def onChanged(self, vobj, prop):
        if prop in ("ShowBaseDirectionArrows", "BaseDirectionArrowSize"):
            self.refreshBaseDirectionArrows()
        
    # Functions for managing base direction arrows
        
    def ensureDirectionArrowProperties(self, vobj):
        try:
            if "ShowBaseDirectionArrows" not in vobj.PropertiesList:
                vobj.addProperty(
                    "App::PropertyBool",
                    "ShowBaseDirectionArrows",
                    "HVAC",
                    "Show direction arrows for base geometry"
                )
                vobj.ShowBaseDirectionArrows = False
        except Exception:
            pass

        try:
            if "BaseDirectionArrowSize" not in vobj.PropertiesList:
                vobj.addProperty(
                    "App::PropertyFloat",
                    "BaseDirectionArrowSize",
                    "HVAC",
                    "Size multiplier for base direction arrows"
                )
                vobj.BaseDirectionArrowSize = 1.0
        except Exception:
            pass
    
    def _buildArrowCoinNode(self, lines, size_scale=1.0):
        """
        Build one Coin3D node containing all direction arrows as 3D cones.
        lines: [(sp, ep, tag, edge_no), ...]
        """
        root = coin.SoSeparator()
        
        # Draw filled faces with one color
        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(1.0, 0.15, 0.0)
        mat.specularColor.setValue(0.4, 0.4, 0.4)
        mat.shininess.setValue(0.6)
        root.addChild(mat)
    
        for sp, ep, _tag, _edge_no in lines:
            p0 = FreeCAD.Vector(*sp) if not hasattr(sp, 'x') else FreeCAD.Vector(sp)
            p1 = FreeCAD.Vector(*ep) if not hasattr(ep, 'x') else FreeCAD.Vector(ep)
        
            direction = p1 - p0
            length = direction.Length
            if length < 1e-9:
                continue
            direction.normalize()
        
            # sizing
            arrow_len   = max(5.0, min(length * 0.25, 80.0)) * max(0.05, float(size_scale))
            arrow_len   = min(arrow_len, length * 0.8)
            head_len    = arrow_len * 0.5
            head_radius = head_len * 0.4
            shaft_len   = arrow_len - head_len
            shaft_radius = head_radius * 0.5
        
            # geometry: chain from tip backwards
            tip         = p0 + direction * (length * 0.6)
            cone_center = tip  - direction * (head_len * 0.5)
            cone_base   = tip  - direction * (head_len)
            shaft_center = cone_base - direction * (shaft_len * 0.5)
        
            # rotation: Coin SoCone/SoCylinder align to +Y, rotate Y → direction
            y_axis    = FreeCAD.Vector(0, 1, 0)
            rot_axis  = y_axis.cross(direction)
            dot       = max(-1.0, min(1.0, y_axis.dot(direction)))
            if rot_axis.Length > 1e-9:
                rot_axis.normalize()
                rot_angle = math.acos(dot)
            else:
                # direction is parallel to Y axis
                if dot > 0:
                    # already +Y, identity — no rotation needed
                    rot_axis  = FreeCAD.Vector(1, 0, 0)
                    rot_angle = 0.0
                else:
                    # exactly -Y, flip 180° around X (or Z, either works)
                    rot_axis  = FreeCAD.Vector(1, 0, 0)
                    rot_angle = math.pi
        
            def make_transform(center, rot_ax, angle):
                xf = coin.SoTransform()
                xf.translation.setValue(center.x, center.y, center.z)
                xf.rotation.setValue(coin.SbVec3f(rot_ax.x, rot_ax.y, rot_ax.z), angle)
                return xf
        
            # cone head
            cone_sep = coin.SoSeparator()
            cone_sep.addChild(make_transform(cone_center, rot_axis, rot_angle))
            cone = coin.SoCone()
            cone.bottomRadius.setValue(head_radius)
            cone.height.setValue(head_len)
            cone_sep.addChild(cone)
            root.addChild(cone_sep)
        
            # cylinder shaft — anchored to cone base, never recomputed independently
            shaft_sep = coin.SoSeparator()
            shaft_sep.addChild(make_transform(shaft_center, rot_axis, rot_angle))
            cyl = coin.SoCylinder()
            cyl.radius.setValue(shaft_radius)
            cyl.height.setValue(shaft_len)
            shaft_sep.addChild(cyl)
            root.addChild(shaft_sep)
    
        return root
        
    def refreshBaseDirectionArrows(self, parser=None):
        """
        Rebuild base direction arrows.

        Uses DuctNetworkParser.all_lines, not separate Draft/Sketch parsing.
        """
        root = getattr(self, "_baseDirectionRoot", None)
        net = self.Object
        
        if root is None or net is None:
            return

        root.removeAllChildren()
        
        if net.ViewObject.ShowBaseDirectionArrows is False:
            return

        try:
            if parser is None:
                parser = DuctNetworkParser()
                parser.compile_lines_from_objects(list(net.Base.OutList))

            lines = list(getattr(parser, "all_lines", []) or [])

            try:
                size_scale = float(net.ViewObject.BaseDirectionArrowSize)
            except Exception:
                size_scale = 1.0

            arrow_node = self._buildArrowCoinNode(lines, size_scale=size_scale)
            root.addChild(arrow_node)

        except Exception as e:
            FreeCAD.Console.PrintError(
                "HVAC - Failed to refresh base direction arrows.\n"
            )
            FreeCAD.Console.PrintError(str(e))


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
    if hvaclib.isDuctNetwork(net):
        net.Proxy.setActive()
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
                net.Proxy.removeGeometryObject(obj)
            doc.removeObject(net.Geometry.Name)
            
        if hasattr(net, "Base") and net.Base:
            for obj in list(net.Base.OutList):
                net.Base.removeObject(obj)
            doc.removeObject(net.Base.Name)
            
        if not remove_internal_only:
            doc.removeObject(net.Name)
    hvaclib.refreshState()
    FreeCAD.Console.PrintMessage("HVAC - Deleted selected {} DuctNetwork(s)".format(len(nets)))
