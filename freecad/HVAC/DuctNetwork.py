# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Solar addon.

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

import FreeCAD
import FreeCADGui as Gui
from PySide import QtWidgets, QtCore
import freecad.HVAC.hvaclib as hvaclib

from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate


#=================================================
# A. Helper classes
#=================================================


class NewSketchObserver:
    """New sketch creation observer"""
    
    def __init__(self, network_obj):
        self.network_obj = network_obj
        self.doc = network_obj.Document
        self.created_sketch = None

    def slotCreatedObject(self, obj):
        # Called when a new object is created in the document
        if self.created_sketch is not None:
            return
        if obj and obj.Document == self.doc and obj.TypeId == "Sketcher::SketchObject":
            self.created_sketch = obj
            # Delay grouping slightly so built-in command finishes first
            QtCore.QTimer.singleShot(0, self.finalize)

    def finalize(self):
        try:
            if self.created_sketch:
                DuctNetwork.addBaseObject(self.network_obj, self.created_sketch)
        finally:
            # Always remove observer after one use
            FreeCAD.removeDocumentObserver(self)


class NewDraftLineObserver:
    """Observe Draft line creation and add all created lines to the network
    after the Draft tool is closed.
    """

    def __init__(self, network_obj):
        self.network_obj = network_obj
        self.doc = network_obj.Document
        self.created_objects = []
        self._finished = False
        self._seen_dialog = False

        self._timer = QtCore.QTimer()
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.check_finished)
        self._timer.start()

    def slotCreatedObject(self, obj):
        """Called whenever a new object is created in the document."""
        if self._finished:
            return
        if not obj or obj.Document != self.doc:
            return
        if obj not in self.created_objects:
            self.created_objects.append(obj)

    def check_finished(self):
        """Detect when the Draft command has been exited."""
        if self._finished:
            return
        # Draft Line normally opens a task panel/dialog while active.
        if Gui.Control.activeDialog():
            self._seen_dialog = True
            return
        # Finalize only after the dialog has appeared once and then closed.
        if self._seen_dialog:
            QtCore.QTimer.singleShot(0, self.finalize)
            return True

    def finalize(self):
        if self._finished:
            return
        self._finished = True
        self._timer.stop()

        try:
            for obj in self.created_objects:
                if hvaclib.obj_is_wire(obj):
                    DuctNetwork.addBaseObject(self.network_obj, obj)
        finally:
            # Switch back workbench to HVAC
            Gui.activateWorkbench(hvaclib.WORKBENCH_NAME)
            FreeCAD.removeDocumentObserver(self)


#=================================================
# B. Main classes
#=================================================


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

    def execute(self, obj):
        """Required so the object can clear its touched state on recompute."""
        pass
        
    @staticmethod
    def getOwner(obj):
        if self.OwnerNetworkName and obj.Document:
            return obj.Document.getObject(owner_name)

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
        #owner = getattr(obj, "OwnerNetwork", None)
        owner = obj.getOwner(obj)
        # Allow deletion only when the owner network itself is being deleted
        if owner and getattr(owner.Proxy, "_allow_internal_delete", False):
            return True
        FreeCAD.Console.PrintWarning(
            "HVAC - Internal folder '{}' cannot be deleted directly.\n".format(obj.Label)
        )
        return False

    def canDropObjects(self):
        return False

    def canDragObjects(self):
        return False
        
        
class DuctNetwork:
    """Visualize and configure HVAC duct network in FreeCAD's 3D view."""

    CONTEXT_KEY = hvaclib.DUCT_NETWORK_CONTEXT_KEY
    FOLDER_BASE_NAME = "Base"
    FOLDER_GEOMETRY_NAME = "Geometry"

    def __init__(self, obj):
        obj.Proxy = self
        self._allow_internal_delete = False
        self.setProperties(obj)

    def setProperties(self, obj):
        """Gives the object properties to HVAC ducts."""
        doc = obj.Document
        # Create the sub-folders
        obj.addProperty("App::PropertyLink", "Base", "HVAC", "Base (internal)")
        folder_base = DuctManagedFolder.create(
            doc,
            f"{obj.Name}_{self.FOLDER_BASE_NAME}",
            owner=obj,
            role=self.FOLDER_BASE_NAME,
        )
        folder_base.Label = self.FOLDER_BASE_NAME
        obj.Base = folder_base
        
        obj.addProperty("App::PropertyLink", "Geometry", "HVAC", "Geometry (internal)")
        folder_geometry = DuctManagedFolder.create(
            doc,
            f"{obj.Name}_{self.FOLDER_GEOMETRY_NAME}",
            owner=obj,
            role=self.FOLDER_GEOMETRY_NAME,
        )
        folder_geometry.Label = self.FOLDER_GEOMETRY_NAME
        obj.Geometry = folder_geometry

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
        after the sketch is created, move it under obj.Geometry.
        """
        if not obj or not DuctNetwork.isDuctNetwork(obj):
            return
        if FreeCAD.ActiveDocument is None or Gui.ActiveDocument is None:
            return
            
        # Make this network active in the 3D view context
        DuctNetwork.setActive(obj)
        # Install observer before running the command
        obs = NewSketchObserver(obj)
        FreeCAD.addDocumentObserver(obs)
        # Launch the built-in sketch creation command
        Gui.runCommand("Sketcher_NewSketch")
        
    @staticmethod
    def createDraftLineInteractive(obj):
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
        obs = NewDraftLineObserver(obj)
        FreeCAD.addDocumentObserver(obs)
        # Launch the built-in Draft line creation command
        Gui.activateWorkbench("DraftWorkbench")
        Gui.runCommand("Draft_Line")
            
    @staticmethod
    def addBaseObject(net, obj):
        """
        Add a valid base object to the network Base folder.

        Allowed objects:
        - Sketch objects
        - Draft wire/line style objects detected by hvaclib.obj_is_wire()

        Returns:
            bool: True if object was added, False otherwise.
        """
        # Basic validity
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
        # Allow only supported object types
        if not (hvaclib.obj_is_sketch(obj) or hvaclib.obj_is_wire(obj)):
            return False
        # Already in Base folder
        if obj in net.Base.OutList:
            return False

        net.Base.addObject(obj)
        net.Document.recompute()
        return True
        
        
    @staticmethod
    def removeBaseObject(net, obj):
        """
        Remove an object from the network Base folder.

        Returns:
            bool: True if object was removed, False otherwise.
        """
        # Basic validity
        if not net or not obj:
            return False
        if not DuctNetwork.isDuctNetwork(net):
            return False
        if not hasattr(net, "Base") or net.Base is None:
            return False
        if net.Document != getattr(obj, "Document", None):
            return False
        # Object is not inside Base
        if obj not in net.Base.OutList:
            return False

        net.Base.removeObject(obj)
        net.Document.recompute()
        return True

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
    def isDuctNetwork(obj):
        """Test whether obj is a DuctNetwork FeaturePython object."""
        return bool(obj) and hasattr(obj, "Proxy") and isinstance(obj.Proxy, DuctNetwork)

    def execute(self, obj):
        # Parse geomtery
        parser = hvaclib.DuctNetworkParser(obj.Base.OutList)
        # Update DuctNetworkData Class
        print(parser.edge_u_v.values())
        #TODO


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
        panel = TaskPanelEditDuctNetwork(vobj.Object)
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
        obj = vobj.Object
        # Allow deletion only when the owner network itself is being deleted
        if obj and getattr(obj.Proxy, "_allow_internal_delete", False):
            return True
        FreeCAD.Console.PrintWarning(
            "HVAC - Network '{}' cannot be deleted directly.\n".format(obj.Label)
        )
        return False


#=================================================
# C. Command classes
#=================================================


class CommandCreateDuctNetwork:
    """Create HVAC Duct Network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {'Pixmap': hvaclib.get_icon_path("CreateDuctsIcon.svg"),
                'MenuText': QT_TRANSLATE_NOOP('HVAC_CreateDuctNetwork', 'Create Network'),
                'ToolTip': QT_TRANSLATE_NOOP('HVAC_CreateDuctNetwork', 'Create HVAC Duct Network from Sketch/ Line base Geometries')}

    def IsActive(self):
        if Gui.ActiveDocument:
            return True
        else:
            return False

    def Activated(self):
        create_new_duct_network()


class CommandActivateDuctNetwork:
    """Activate HVAC Duct Network."""

    def __init__(self):
        self.task_panel = None

    def GetResources(self):
        return {
            'Pixmap': hvaclib.get_icon_path("ActivateDuctsIcon.svg"),
            "MenuText": QT_TRANSLATE_NOOP("HVAC_ActivateDuctNetwork", "Activate Network"),
            "ToolTip": QT_TRANSLATE_NOOP("HVAC_ActivateDuctNetwork", "Sets a HVAC duct network as the active one for editing."),
            "CmdType": "ForEdit",
        }

    def IsActive(self):
        if Gui.ActiveDocument is None:
            return False

        # Command is active only if there is at least one HVAC network in the document to activate
        if hvaclib.allHVACNetworks():
            return True

        return False

    def Activated(self):
        hvac_networks = hvaclib.allHVACNetworks()
        selected_hvac_networks = hvaclib.selectedHVACNetworks()

        if len(hvac_networks) == 1:
            # If there's only one, activate it directly without showing a dialog
            activate_duct_network(hvac_networks[0], set_edit=False)
        elif selected_hvac_networks:
            # Select first selected
            activate_duct_network(selected_hvac_networks[0], set_edit=False)
        elif len(hvac_networks) > 1:
            # If there are multiple, show a task panel to let the user choose
            self.task_panel = TaskPanelActivate(hvac_networks)
            Gui.Control.showDialog(self.task_panel)


class CommandModifyDuctNetwork:
    """Modify HVAC Duct Network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {'Pixmap': hvaclib.get_icon_path("ModifyDuctsIcon.svg"),
                'MenuText': QT_TRANSLATE_NOOP('HVAC_ModifyDuctNetwork', 'Modify Network'),
                'ToolTip': QT_TRANSLATE_NOOP('HVAC_ModifyDuctNetwork',  'Modify the selected HVAC Duct Network')}

    def IsActive(self):
        if Gui.ActiveDocument:
            selected_hvac_networks = hvaclib.selectedHVACNetworks()
            if selected_hvac_networks:
                return True
        else:
            return False

    def Activated(self):
        selected_hvac_networks = hvaclib.selectedHVACNetworks()
        if selected_hvac_networks:
            modify_duct_network(selected_hvac_networks[0])


class CommandDeleteDuctNetwork:
    """Delete a selected HVAC Duct Network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {'Pixmap': hvaclib.get_icon_path("DeleteDuctsIcon.svg"),
                'MenuText': QT_TRANSLATE_NOOP('HVAC_DeleteDuctNetwork', 'Delete Network'),
                'ToolTip': QT_TRANSLATE_NOOP('HVAC_DeleteDuctNetwork', 'Delete the selected HVAC Duct Network')}

    def IsActive(self):
        if Gui.ActiveDocument:
            selected_hvac_networks = hvaclib.selectedHVACNetworks()
            if selected_hvac_networks:
                return True
        else:
            return False

    def Activated(self):
        selected_hvac_networks = hvaclib.selectedHVACNetworks()
        if selected_hvac_networks:
            delete_duct_networks(selected_hvac_networks)
            
    
class CommandCreateSketch:
    """interactively adds a sketch to the currently active network"""
    
    def QT_TRANSLATE_NOOP(self, text):
        return text
    
    def GetResources(self):
        return {
            "Pixmap": "Sketcher_NewSketch",
            "MenuText": QT_TRANSLATE_NOOP('HVAC_CreateSketch', 'New Sketch'),
            "ToolTip": QT_TRANSLATE_NOOP('HVAC_CreateSketch', 'Create a new sketch inside the active duct network')
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and DuctNetwork.getActive() is not None

    def Activated(self):
        net = DuctNetwork.getActive()
        if net and DuctNetwork.isDuctNetwork(net):
            DuctNetwork.createSketchInteractive(net)
            
            
class CommandCreateLine:
    """Interactively adds Draft line objects to the currently active network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {
            "Pixmap": "Draft_Line",
            "MenuText": QT_TRANSLATE_NOOP("HVAC_CreateLine", "New Line"),
            "ToolTip": QT_TRANSLATE_NOOP(
                "HVAC_CreateLine",
                "Create line objects inside the active duct network"
            ),
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None and DuctNetwork.getActive() is not None

    def Activated(self):
        net = DuctNetwork.getActive()
        if net and DuctNetwork.isDuctNetwork(net):
            DuctNetwork.createDraftLineInteractive(net)


#=================================================
# D. Task Panel classes
#=================================================


class TaskPanelActivate:
    """A basic TaskPanel to select an HVAC netowrk to activate."""

    def __init__(self, hvac_networks):
        self.hvac_networks = hvac_networks
        self.hvac_networks_dict = {}
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_ActivateDuctNetwork", "Activate Network"))

        layout = QtWidgets.QVBoxLayout(self.form)
        label = QtWidgets.QLabel(translate("HVAC_ActivateDuctNetwork", "Select Network :"))
        self.combo = QtWidgets.QComboBox()

        for net in self.hvac_networks:
            # Store the user-friendly Label for display, and the internal Name for activation
            self.combo.addItem(net.Label, net.Name)
            self.hvac_networks_dict[net.Name] = net

        layout.addWidget(label)
        layout.addWidget(self.combo)

    def accept(self):
        """Called when the user clicks OK."""
        selected_name = self.combo.currentData()
        if selected_name:
            QtCore.QTimer.singleShot(0, lambda: activate_duct_network(self.hvac_networks_dict[selected_name], set_edit=False))
        return True

    def reject(self):
        """Called when the user clicks Cancel or closes the panel."""
        return True


class TaskPanelEditDuctNetwork:
    """A basic TaskPanel to edit an HVAC network."""

    def __init__(self, hvac_network):
        self.hvac_network = hvac_network
        self.hvac_networks_dict = {}
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_EditDuctNetwork", "Edit Network"))

        layout = QtWidgets.QVBoxLayout(self.form)
        # Label for instructions
        label = QtWidgets.QLabel(translate("HVAC_EditDuctNetwork", "Base Objects in Network (Sketch/ Draft Line):"))
        layout.addWidget(label)
        # List view to display selected objects
        self.list_view = QtWidgets.QListWidget()
        self.list_view.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)  # Enable multiple selection
        layout.addWidget(self.list_view)

        # Populate existing objects under Base
        if self.hvac_network.Base:
            for obj in self.hvac_network.Base.OutList:
                if self.valid_obj(obj):
                    self.list_view.addItem(obj.Label)

        # Button to enable selection of objects
        self.select_button = QtWidgets.QPushButton(translate("HVAC_EditDuctNetwork", "Add Selected"))
        self.select_button.clicked.connect(self.select_objects)
        layout.addWidget(self.select_button)

        # Button to remove selected objects from the list view
        self.remove_button = QtWidgets.QPushButton(translate("HVAC_EditDuctNetwork", "Remove Selected"))
        self.remove_button.clicked.connect(self.remove_selected_objects)
        layout.addWidget(self.remove_button)

    ## Helper methods

    def valid_obj(self, obj):
        """Return True if the object is valid for selection."""
        return hvaclib.obj_is_sketch(obj) or hvaclib.obj_is_wire(obj)

    def get_valid_selection(self):
        """Return a list of valid objects for selection."""
        selected_objects = Gui.Selection.getSelection()
        return [obj for obj in selected_objects if self.valid_obj(obj)]

    ## Core methods

    def select_objects(self):
        """Enable selection of objects and add them to the list view."""
        valid_objects = self.get_valid_selection()
        existing_labels = [self.list_view.item(i).text() for i in range(self.list_view.count())]
        for obj in valid_objects:
            if obj.Label not in existing_labels:
                self.list_view.addItem(obj.Label)

    def remove_selected_objects(self):
        """Remove selected objects from the list view."""
        # Remove based on selected items in QListWidget
        selected_items = self.list_view.selectedItems()
        for item in selected_items:
            self.list_view.takeItem(self.list_view.row(item))
        # Remove based on selection in 3D view
        doc = self.hvac_network.Document
        selected_objects = Gui.Selection.getSelection()
        for obj in selected_objects:
            if obj in self.hvac_network.Base.OutList:
                for i in range(self.list_view.count()):
                    if self.list_view.item(i).text() == obj.Label:
                        self.list_view.takeItem(i)
                        break

    def accept(self):
        """Called when the user clicks OK."""
        selected_items = [self.list_view.item(i).text() for i in range(self.list_view.count())]
        doc = self.hvac_network.Document

        # Add selected items to Base folder
        for item_label in selected_items:
            for obj in doc.Objects:
                if obj.Label == item_label and obj not in self.hvac_network.Base.OutList:
                    DuctNetwork.addBaseObject(self.hvac_network, obj)
                    break

        # Remove unselected items from Base folder
        existing_labels = [self.list_view.item(i).text() for i in range(self.list_view.count())]
        for obj in self.hvac_network.Base.OutList:
            if self.valid_obj(obj) and obj.Label not in existing_labels:
                DuctNetwork.removeBaseObject(self.hvac_network, obj)

        return True

    def reject(self):
        """Called when the user clicks Cancel or closes the panel."""
        return True


#=================================================
# E. General functions
#=================================================


def create_new_duct_network(name="DuctNetwork", set_active=True):
    """Create new duct network"""
    # Create new duct netowork and create default folders
    net = DuctNetwork.createObject(name)
    print("HVAC - New DuctNetwork created")
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
    print("HVAC - Edit DuctNetwork completed")

def delete_duct_networks(nets):
    """Delete the selected HVAC duct network object"""
    doc = FreeCAD.ActiveDocument
    for net in nets:
        if net.Document != doc:
            continue
        if hasattr(net, "Proxy") and net.Proxy:
            net.Proxy._allow_internal_delete = True
        if hasattr(net, "Base") and net.Base:
            doc.removeObject(net.Base.Name)
        if hasattr(net, "Geometry") and net.Geometry:
            doc.removeObject(net.Geometry.Name)
        doc.removeObject(net.Name)
    hvaclib.refreshState()
    print("HVAC - Deleted selected {} DuctNetwork(s)".format(len(nets)))


#=================================================
# F. Register Commands
#=================================================

if FreeCAD.GuiUp:
    FreeCAD.Gui.addCommand('HVAC_CreateDuctNetwork', CommandCreateDuctNetwork())
    FreeCAD.Gui.addCommand('HVAC_ModifyDuctNetwork', CommandModifyDuctNetwork())
    FreeCAD.Gui.addCommand('HVAC_DeleteDuctNetwork', CommandDeleteDuctNetwork())
    FreeCAD.Gui.addCommand('HVAC_ActivateDuctNetwork', CommandActivateDuctNetwork())
    FreeCAD.Gui.addCommand("HVAC_CreateSketch", CommandCreateSketch())
    FreeCAD.Gui.addCommand("HVAC_CreateLine", CommandCreateLine())
