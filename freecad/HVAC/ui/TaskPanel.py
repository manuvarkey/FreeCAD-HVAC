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

import json
import traceback

import FreeCAD
import FreeCADGui as Gui
import Materials
import MatGui  # registers MatGui::MaterialTreeWidget with Gui.UiLoader -- see MaterialPickerDialog
from PySide import QtWidgets, QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from ..utils import hvaclib
from ..utils import materials as hvac_materials
from ..ui.Observer import buildPortHighlightCoinNode, TerminalFlowRateObserver, AirflowResultObserver


class TaskPanelActivate:
    """A basic TaskPanel to select an HVAC netowrk to activate."""

    def __init__(self, hvac_networks, activate_callback=None):
        self.hvac_networks = hvac_networks
        self.activate_callback = activate_callback
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
            QtCore.QTimer.singleShot(0, lambda: self.activate_callback(self.hvac_networks_dict[selected_name], set_edit=False))
        return True

    def reject(self):
        """Called when the user clicks Cancel or closes the panel."""
        return True


class TaskPanelEditDuctNetwork:
    """A basic TaskPanel to edit an HVAC network."""

    def __init__(self, hvac_network, callback_add_base_object, callback_remove_base_object):
        self.hvac_network = hvac_network
        self.callback_add_base_object = callback_add_base_object
        self.callback_remove_base_object = callback_remove_base_object
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
        return hvaclib.isSketch(obj) or hvaclib.isWire(obj)

    def get_valid_selection(self, include_derived=True):
        """Return a list of valid objects for selection."""
        from ..core.Network import DuctNetwork
        selected_objects = Gui.Selection.getSelection()
        derived_objects = [obj for obj in selected_objects if hvaclib.isDuctSegment(obj)]
        
        valid_obs = {obj for obj in selected_objects if self.valid_obj(obj)}
        
        if include_derived:
            valid_obs_derived = set()
            for obj in derived_objects:
                base_obj = DuctNetwork.getOwnerBaseObject(obj)
                base_net = DuctNetwork.getOwnerNetwork(base_obj)
                if base_obj and base_net and base_net == self.hvac_network:
                    valid_obs_derived.add(base_obj)
            return list(valid_obs | valid_obs_derived)
        else:
            return list(valid_obs)

    ## Core methods

    def select_objects(self):
        """Enable selection of objects and add them to the list view."""
        valid_objects = self.get_valid_selection(include_derived=False)
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
        selected_objects = self.get_valid_selection(include_derived=True)
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
                    self.callback_add_base_object(self.hvac_network, obj)
                    break

        # Remove unselected items from Base folder
        existing_labels = [self.list_view.item(i).text() for i in range(self.list_view.count())]
        for obj in self.hvac_network.Base.OutList:
            if self.valid_obj(obj) and obj.Label not in existing_labels:
                self.callback_remove_base_object(self.hvac_network, obj)

        return True

    def reject(self):
        """Called when the user clicks Cancel or closes the panel."""
        return True


class TaskPanelDirectionEditMode:
    """Side panel shown while duct direction edit mode is active."""

    def __init__(self, network_obj, session):
        self.network_obj = network_obj
        self.session = session

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(
            translate("HVAC_DirectionEditMode", "Edit Duct Directions")
        )

        layout = QtWidgets.QVBoxLayout(self.form)

        title = QtWidgets.QLabel(
            translate(
                "HVAC_DirectionEditMode",
                "Direction edit mode is active.",
            )
        )
        title.setWordWrap(True)
        layout.addWidget(title)

        help_text = QtWidgets.QLabel(
            translate(
                "HVAC_DirectionEditMode",
                "Select a base Sketch edge or Draft route object to reverse its direction."
            )
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)

        close_button = QtWidgets.QPushButton(
            translate("HVAC_DirectionEditMode", "Close Direction Edit Mode")
        )
        close_button.clicked.connect(self._close)
        layout.addWidget(close_button)

        layout.addStretch(1)

    def _close(self):
        self.session.stop(request_sync=True)
        Gui.Control.closeDialog()

    def accept(self):
        self.session.stop(request_sync=True)
        return True

    def reject(self):
        self.session.stop(request_sync=True)
        return True


class MaterialPickerDialog(QtWidgets.QDialog):
    """
    Modal "pick any FreeCAD material" dialog, built from FreeCAD's own
    native Material browser widget (MatGui::MaterialTreeWidget) -- the same
    tree used by the Material workbench/editor and by other addons (e.g.
    CAM's own "Assign Material" dialog in Path/Main/Gui/Job.py). No HVAC-
    specific material list: every material known to FreeCAD (built-in, this
    addon's own, other addons', user-defined) shows up here unfiltered.
    """

    def __init__(self, title, parent=None):
        super(MaterialPickerDialog, self).__init__(parent)
        self.uuid = None

        self.setWindowTitle(title)

        self.materialTree = Gui.UiLoader().createWidget("MatGui::MaterialTreeWidget")
        self.materialTreeWidget = MatGui.MaterialTreeWidget(self.materialTree)

        self.okButton = QtWidgets.QPushButton(translate("HVAC", "OK"))
        self.cancelButton = QtWidgets.QPushButton(translate("HVAC", "Cancel"))
        self.okButton.clicked.connect(self.accept)
        self.cancelButton.clicked.connect(self.reject)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.materialTree)

        button_layout = QtWidgets.QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self.okButton)
        button_layout.addWidget(self.cancelButton)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.materialTree.onMaterial.connect(self._onMaterial)

    def _onMaterial(self, uuid):
        self.uuid = uuid


class MaterialPickerRow(QtWidgets.QWidget):
    """
    One "<current material name> [Browse...]" row, reused by
    TaskPanelEditMaterial (per-object casing/insulation) and
    TaskPanelNetworkTypeDefaults (network-level defaults) -- both just need
    "show what's currently set, let the user replace it via
    MaterialPickerDialog." `touched` is False until the user actually picks
    something, so a caller can tell "left alone" apart from "re-picked the
    same material" and skip re-applying anything unnecessarily.
    """

    def __init__(self, dialog_title, initial_label, parent=None):
        super(MaterialPickerRow, self).__init__(parent)
        self.material = None
        self.touched = False
        self._dialog_title = dialog_title

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.name_label = QtWidgets.QLabel(initial_label)
        self.browse_button = QtWidgets.QPushButton(translate("HVAC", "Browse..."))
        self.browse_button.clicked.connect(self._browse)
        layout.addWidget(self.name_label, 1)
        layout.addWidget(self.browse_button)

    def _browse(self):
        dialog = MaterialPickerDialog(self._dialog_title, parent=self)
        if dialog.exec_() == QtWidgets.QDialog.Accepted and dialog.uuid:
            material = hvac_materials.get_material_by_uuid(dialog.uuid)
            if material is not None:
                self.material = material
                self.touched = True
                self.name_label.setText(material.Name)


def _common_material_label(objects, prop_name):
    """
    "(none)" if no object has this material set, the shared material's own
    Name if every object agrees, else "(multiple)" -- same convention
    TaskPanelTypeEditor uses for a mixed-selection combo box.
    """
    names = set()
    for obj in objects:
        material = getattr(obj, prop_name, None)
        names.add(material.Name if material is not None and getattr(material, "Name", "") else "")
    if len(names) == 1:
        name = names.pop()
        return name if name else translate("HVAC_EditMaterial", "(none)")
    return translate("HVAC_EditMaterial", "(multiple)")


class TaskPanelEditMaterial:
    """
    Task panel to assign native FreeCAD casing/insulation materials to
    selected duct segment(s)/component(s) -- one panel covering both
    properties (see CommandEditMaterial), instead of two separate commands.
    """

    def __init__(self, objects, apply_callback=None):
        self.objects = [o for o in (objects or []) if o is not None]
        self.apply_callback = apply_callback

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_EditMaterial", "Edit Material"))

        layout = QtWidgets.QVBoxLayout(self.form)
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditMaterial", "Selected objects: {}").format(len(self.objects))
        ))

        layout.addWidget(QtWidgets.QLabel(translate("HVAC_EditMaterial", "Casing material:")))
        self.casing_row = MaterialPickerRow(
            translate("HVAC_EditMaterial", "Select Casing Material"),
            _common_material_label(self.objects, "CasingMaterial"),
        )
        layout.addWidget(self.casing_row)

        layout.addWidget(QtWidgets.QLabel(translate("HVAC_EditMaterial", "Insulation material:")))
        self.insulation_row = MaterialPickerRow(
            translate("HVAC_EditMaterial", "Select Insulation Material"),
            _common_material_label(self.objects, "InsulationMaterial"),
        )
        layout.addWidget(self.insulation_row)

    def accept(self):
        casing_material = self.casing_row.material if self.casing_row.touched else None
        insulation_material = self.insulation_row.material if self.insulation_row.touched else None

        if (casing_material is not None or insulation_material is not None) and self.apply_callback:
            QtCore.QTimer.singleShot(
                0,
                lambda: self.apply_callback(
                    self.objects,
                    casing_material=casing_material,
                    insulation_material=insulation_material,
                )
            )
        return True

    def reject(self):
        return True


class TaskPanelNetworkTypeDefaults:
    """Task panel to edit network-level type defaults."""

    def __init__(self, network_obj, apply_callback=None):
        self.network_obj = network_obj
        self.apply_callback = apply_callback

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_NetworkTypeDefaults", "HVAC Type Defaults"))

        layout = QtWidgets.QVBoxLayout(self.form)

        title = QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Network: {}").format(network_obj.Label)
        )
        layout.addWidget(title)

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default library:")
        ))
        self.library_combo = QtWidgets.QComboBox()
        layout.addWidget(self.library_combo)

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default segment profile:")
        ))
        self.profile_combo = QtWidgets.QComboBox()
        layout.addWidget(self.profile_combo)
        
        layout.addWidget(self._makeSeparator())
        
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default dimensions:")
        ))
        
        layout.addLayout(self._buildDimensionEditors())

        # note = QtWidgets.QLabel(
        #     translate(
        #         "HVAC_NetworkTypeDefaults",
        #         "Junction types are auto selected based on parser/classifier output unless manually overridden."
        #     )
        # )
        # note.setWordWrap(True)
        # layout.addWidget(note)

        layout.addWidget(self._makeSeparator())

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default attachment:")
        ))
        layout.addLayout(self._buildAttachmentGrid())

        layout.addWidget(self._makeSeparator())

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default offset:")
        ))
        layout.addLayout(self._buildOffsetEditors())

        layout.addWidget(self._makeSeparator())

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default casing material:")
        ))
        self.casing_material_row = MaterialPickerRow(
            translate("HVAC_NetworkTypeDefaults", "Select Casing Material"),
            _common_material_label([network_obj], "DefaultCasingMaterial"),
        )
        layout.addWidget(self.casing_material_row)

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_NetworkTypeDefaults", "Default insulation material:")
        ))
        self.insulation_material_row = MaterialPickerRow(
            translate("HVAC_NetworkTypeDefaults", "Select Insulation Material"),
            _common_material_label([network_obj], "DefaultInsulationMaterial"),
        )
        layout.addWidget(self.insulation_material_row)

        self._populateLibraries()
        self._loadFromNetwork()

        self.library_combo.currentIndexChanged.connect(self._refreshProfiles)

    def _makeSeparator(self):
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line
        
    def _buildDimensionEditors(self):
        row = QtWidgets.QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
    
        self.default_diameter = QtWidgets.QDoubleSpinBox()
        self.default_height = QtWidgets.QDoubleSpinBox()
        self.default_width = QtWidgets.QDoubleSpinBox()
        self.default_insulation_thickness = QtWidgets.QDoubleSpinBox()

        for w in (self.default_diameter, self.default_width, self.default_height, self.default_insulation_thickness):
            w.setDecimals(3)
            w.setRange(0.0, 1e9)
            w.setSingleStep(10.0)
            w.setSuffix(" mm")

        row.addWidget(QtWidgets.QLabel("Diameter"), 0, 0)
        row.addWidget(self.default_diameter, 0, 1)

        row.addWidget(QtWidgets.QLabel("Height"), 1, 0)
        row.addWidget(self.default_height, 1, 1)

        row.addWidget(QtWidgets.QLabel("Width"), 2, 0)
        row.addWidget(self.default_width, 2, 1)

        row.addWidget(QtWidgets.QLabel("Insulation thickness"), 3, 0)
        row.addWidget(self.default_insulation_thickness, 3, 1)

        return row
    
    def _buildAttachmentGrid(self):
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(1)
        grid.setVerticalSpacing(1)
    
        self.attachment_group = QtWidgets.QButtonGroup(self.form)
        self.attachment_group.setExclusive(True)
        self._attachment_buttons = {}
    
        items = [
            ("TopLeft",       "↖", 0, 0),
            ("TopCenter",     "↑", 0, 1),
            ("TopRight",      "↗", 0, 2),
            ("CenterLeft",    "←", 1, 0),
            ("Center",        "•", 1, 1),
            ("CenterRight",   "→", 1, 2),
            ("BottomLeft",    "↙", 2, 0),
            ("BottomCenter",  "↓", 2, 1),
            ("BottomRight",   "↘", 2, 2),
        ]
    
        for key, text, r, c in items:
            btn = QtWidgets.QToolButton()
            btn.setText(text)
            btn.setCheckable(True)
            btn.setToolTip(key)
            btn.setFixedSize(28, 24)
            self.attachment_group.addButton(btn)
            self._attachment_buttons[key] = btn
            grid.addWidget(btn, r, c)
    
        return grid
        
    def _buildOffsetEditors(self):
        row = QtWidgets.QGridLayout()

        self.offset_x = QtWidgets.QDoubleSpinBox()
        self.offset_y = QtWidgets.QDoubleSpinBox()
        self.offset_z = QtWidgets.QDoubleSpinBox()

        for w in (self.offset_x, self.offset_y, self.offset_z):
            w.setDecimals(3)
            w.setRange(-1e6, 1e6)
            w.setSingleStep(10.0)

        row.addWidget(QtWidgets.QLabel("X"), 0, 0)
        row.addWidget(self.offset_x, 0, 1)
        row.addWidget(QtWidgets.QLabel("Y"), 1, 0)
        row.addWidget(self.offset_y, 1, 1)
        row.addWidget(QtWidgets.QLabel("Z"), 2, 0)
        row.addWidget(self.offset_z, 2, 1)
    
        return row
    
    def _populateLibraries(self):
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        self.library_combo.clear()
        for lib in reg.list_libraries():
            self.library_combo.addItem(lib.label, lib.id)

    def _refreshProfiles(self):
        library_id = self.library_combo.currentData()
        current_profile = self.profile_combo.currentData()

        self.profile_combo.clear()
        if not library_id:
            return

        profiles = hvaclib.HVACLibraryService.segment_profiles_for_library(library_id)
        for profile in profiles:
            self.profile_combo.addItem(profile, profile)

        if current_profile:
            idx = self.profile_combo.findData(current_profile)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
            elif profiles:
                self.profile_combo.setCurrentIndex(0)
        elif profiles:
            self.profile_combo.setCurrentIndex(0)

    def _loadFromNetwork(self):
        lib_id = getattr(self.network_obj, "DefaultLibraryId", "")
        if lib_id:
            idx = self.library_combo.findData(lib_id)
            if idx >= 0:
                self.library_combo.setCurrentIndex(idx)
                
        self._refreshProfiles()

        profile = getattr(self.network_obj, "DefaultSegmentProfile", "")
        if profile:
            idx = self.profile_combo.findData(profile)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
                
        self.default_diameter.setValue(float(getattr(self.network_obj, "DefaultDiameter", 100.0)))
        self.default_width.setValue(float(getattr(self.network_obj, "DefaultWidth", 100.0)))
        self.default_height.setValue(float(getattr(self.network_obj, "DefaultHeight", 100.0)))
        self.default_insulation_thickness.setValue(float(getattr(self.network_obj, "DefaultInsulationThickness", 0.0)))
                
        attachment = str(getattr(self.network_obj, "DefaultAttachment", "Center"))
        if attachment in self._attachment_buttons:
            self._attachment_buttons[attachment].setChecked(True)
        elif "Center" in self._attachment_buttons:
            self._attachment_buttons["Center"].setChecked(True)
        
        offset = FreeCAD.Vector(getattr(self.network_obj, "DefaultOffset", FreeCAD.Vector(0, 0, 0)))
        self.offset_x.setValue(offset.x)
        self.offset_y.setValue(offset.y)
        self.offset_z.setValue(offset.z)
        
    def _selectedAttachment(self):
        for key, btn in self._attachment_buttons.items():
            if btn.isChecked():
                return key
        return "Center"
    
    def _currentOffset(self):
        return FreeCAD.Vector(
            self.offset_x.value(),
            self.offset_y.value(),
            self.offset_z.value(),
        )

    def accept(self):
        if self.apply_callback:
            self.apply_callback(
                self.network_obj,
                library_id=self.library_combo.currentData(),
                segment_profile=self.profile_combo.currentData(),
                default_attachment=self._selectedAttachment(),
                default_offset=self._currentOffset(),
                default_diameter=self.default_diameter.value(),
                default_width=self.default_width.value(),
                default_height=self.default_height.value(),
                default_insulation_thickness=self.default_insulation_thickness.value(),
                default_casing_material=(
                    self.casing_material_row.material if self.casing_material_row.touched else None
                ),
                default_insulation_material=(
                    self.insulation_material_row.material if self.insulation_material_row.touched else None
                ),
            )
        return True

    def reject(self):
        return True


class TaskPanelTypeEditor:
    """Task panel to edit library/type selection for selected HVAC geometry objects."""

    def __init__(self, objects, apply_callback=None):
        self.objects = [o for o in (objects or []) if o is not None]
        self.apply_callback = apply_callback
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_EditType", "Edit HVAC Type"))

        layout = QtWidgets.QVBoxLayout(self.form)

        info_text = translate(
            "HVAC_EditType",
            "Selected objects: {}"
        ).format(len(self.objects))
        self.info_label = QtWidgets.QLabel(info_text)
        layout.addWidget(self.info_label)

        self.object_names = QtWidgets.QListWidget()
        self.object_names.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        for obj in self.objects:
            self.object_names.addItem("{} ({})".format(obj.Label, obj.Name))
        layout.addWidget(self.object_names)

        # Library
        layout.addWidget(QtWidgets.QLabel(translate("HVAC_EditType", "Library:")))
        self.library_combo = QtWidgets.QComboBox()
        layout.addWidget(self.library_combo)

        # Type
        layout.addWidget(QtWidgets.QLabel(translate("HVAC_EditType", "Type:")))
        self.type_combo = QtWidgets.QComboBox()
        layout.addWidget(self.type_combo)

        self._populateLibraries()
        self._loadFromSelection()

        self.library_combo.currentIndexChanged.connect(self._refreshTypes)

    def _populateLibraries(self):
        self.library_combo.clear()
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        for lib in reg.list_libraries():
            self.library_combo.addItem(lib.label, lib.id)

    def _commonLibraryId(self):
        vals = {getattr(o, "LibraryId", "") for o in self.objects}
        return vals.pop() if len(vals) == 1 else ""

    def _commonTypeId(self):
        vals = {getattr(o, "TypeId", "") for o in self.objects}
        return vals.pop() if len(vals) == 1 else ""

    def _loadFromSelection(self):
        library_id = self._commonLibraryId()

        if library_id:
            idx = self.library_combo.findData(library_id)
            if idx >= 0:
                self.library_combo.setCurrentIndex(idx)

        self._refreshTypes()

        type_id = self._commonTypeId()
        if type_id:
            idx = self.type_combo.findData(type_id)
            if idx >= 0:
                self.type_combo.setCurrentIndex(idx)

    def _refreshTypes(self):
        self.type_combo.clear()
        if not self.objects:
            return

        library_id = self.library_combo.currentData()
        if not library_id:
            return

        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        lib = reg.get_library(library_id)
        if lib is None:
            return

        ref = self.objects[0]

        if hvaclib.isDuctSegment(ref):
            # For segments, keep it simple:
            # show all segment types from the selected library.
            type_defs = lib.list_types(category="segment")

        else:
            # For a junction's Primary/Inline DuctComponent, Family is a
            # junction-level classifier concept (not the component's own --
            # see Component.py), so resolve it off the parent junction via
            # ParentJunctionName. Profile is the component's own
            # composer-derived local profile.
            family = getattr(ref, "Family", "")
            if not family and hvaclib.isDuctComponent(ref):
                parent_name = getattr(ref, "ParentJunctionName", "")
                parent = ref.Document.getObject(parent_name) if parent_name else None
                family = getattr(parent, "Family", "") if parent is not None else ""
            profile = getattr(ref, "Profile", "")
            type_defs = lib.list_types(
                category="junction",
                family=family if family else None,
                profile=profile if profile else None,
            )

        for tdef in type_defs:
            self.type_combo.addItem(tdef.label, tdef.id)

    def accept(self):
        library_id = self.library_combo.currentData()
        type_id = self.type_combo.currentData()

        if self.apply_callback:
            QtCore.QTimer.singleShot(
                0,
                lambda: self.apply_callback(
                    self.objects,
                    library_id=library_id,
                    type_id=type_id,
                )
            )
        return True

    def reject(self):
        return True


class TaskPanelEditInlineComponents:
    """
    Task panel for managing a junction's Inline devices (dampers,
    silencers, ...) in one place, replacing the previous separate Add/
    Remove Inline Component commands: a list of what's already attached
    (selecting an entry highlights it in the 3D view), a Delete button for
    it, and an "Add inline component" section (pick which real edge to
    attach a new one to and which type -- the old Add Inline Component
    panel's own content) that highlights the currently-picked port with a
    semi-transparent plane so a user can see which physical connection
    "Attach to edge" refers to.

    Add/remove act immediately through add_callback/remove_callback rather
    than being deferred to accept() -- the list has to reflect each change
    right away so the panel can stay open across several edits.
    """

    def __init__(self, junction, add_callback=None, remove_callback=None):
        self.junction = junction
        self.add_callback = add_callback
        self.remove_callback = remove_callback
        self._highlight_root = None
        self._components = []

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_EditInlineComponents", "Edit Inline Components"))

        layout = QtWidgets.QVBoxLayout(self.form)
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditInlineComponents", "Junction: {}").format(junction.Label)
        ))

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditInlineComponents", "Existing inline components:")
        ))
        self.component_list = QtWidgets.QListWidget()
        self.component_list.currentRowChanged.connect(self._onSelectComponent)
        layout.addWidget(self.component_list)

        self.delete_button = QtWidgets.QPushButton(
            translate("HVAC_EditInlineComponents", "Delete Selected")
        )
        self.delete_button.clicked.connect(self._deleteSelected)
        layout.addWidget(self.delete_button)

        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(separator)

        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditInlineComponents", "Add inline component:")
        ))

        try:
            analysis = json.loads(getattr(junction, "AnalysisJson", "") or "{}")
        except Exception:
            analysis = {}
        self.ports = list(analysis.get("connected_ports", []) or [])

        layout.addWidget(QtWidgets.QLabel(translate("HVAC_EditInlineComponents", "Attach to edge:")))
        self.edge_combo = QtWidgets.QComboBox()
        for port in self.ports:
            self.edge_combo.addItem(self._portLabel(port))
        layout.addWidget(self.edge_combo)
        # Degree 1: nothing to choose, but still shown for clarity.
        self.edge_combo.setEnabled(len(self.ports) > 1)

        layout.addWidget(QtWidgets.QLabel(translate("HVAC_EditInlineComponents", "Type:")))
        self.type_combo = QtWidgets.QComboBox()
        layout.addWidget(self.type_combo)

        self.add_button = QtWidgets.QPushButton(translate("HVAC_EditInlineComponents", "Add"))
        self.add_button.clicked.connect(self._addComponent)
        layout.addWidget(self.add_button)

        self._refreshTypes()
        self.edge_combo.currentIndexChanged.connect(self._refreshTypes)
        self.edge_combo.currentIndexChanged.connect(self._highlightCurrentPort)

        self._refreshComponentList()
        self._highlightCurrentPort()

    # ------------------------------------------------------------------
    # Existing components list
    # ------------------------------------------------------------------

    def _refreshComponentList(self):
        self.component_list.blockSignals(True)
        self.component_list.clear()
        self._components = self.junction.Proxy.getInlineComponents()
        for component in self._components:
            self.component_list.addItem(self._componentLabel(component))
        self.component_list.blockSignals(False)
        Gui.Selection.clearSelection()

    @classmethod
    def _componentLabel(cls, component):
        edge_key = getattr(component, "AttachedEdgeKey", "")
        return "{} -- {}".format(edge_key, cls._typeLabel(component))

    @staticmethod
    def _typeLabel(component):
        library_id = getattr(component, "LibraryId", "")
        type_id = getattr(component, "TypeId", "")
        reg = hvaclib.HVACLibraryService.get_hvac_library_registry()
        lib = reg.get_library(library_id) if library_id else None
        tdef = lib.get_type(type_id) if lib is not None else None
        return tdef.label if tdef is not None else (type_id or translate("HVAC_EditInlineComponents", "(no type)"))

    def _onSelectComponent(self, row):
        Gui.Selection.clearSelection()
        if row is None or row < 0 or row >= len(self._components):
            return
        component = self._components[row]
        Gui.Selection.addSelection(component.Document.Name, component.Name)

    def _deleteSelected(self):
        row = self.component_list.currentRow()
        if row is None or row < 0 or row >= len(self._components):
            return
        component = self._components[row]
        if self.remove_callback:
            self.remove_callback(component)
        self._refreshComponentList()

    # ------------------------------------------------------------------
    # Add section
    # ------------------------------------------------------------------

    @staticmethod
    def _portLabel(port):
        """Size (inlet/outlet) -- direction vector -- e.g. '300 x 250 mm
        (outlet) -- (1.00, 0.00, 0.00)'. Leads with what a user actually
        recognizes a duct run by, not the internal edge_key."""
        section_params = port.get("section_params", {}) or {}
        if port.get("profile") == "Circular":
            size = "{:.0f} mm dia".format(section_params.get("Diameter", 0.0) or 0.0)
        else:
            size = "{:.0f} x {:.0f} mm".format(
                section_params.get("Width", 0.0) or 0.0, section_params.get("Height", 0.0) or 0.0,
            )
        role = translate("HVAC_EditInlineComponents", "inlet") if port.get("flow_into_junction") else translate("HVAC_EditInlineComponents", "outlet")
        direction = port.get("direction") or (0.0, 0.0, 0.0)
        direction = [0.0 if abs(x) < 0.05 else x for x in direction]  # round small values to 0.0 to avoid noise
        return "{} ({}) -- ({:.1f}, {:.1f}, {:.1f})".format(size, role, direction[0], direction[1], direction[2])

    def _currentPort(self):
        idx = self.edge_combo.currentIndex()
        if idx < 0 or idx >= len(self.ports):
            return None
        return self.ports[idx]

    def _refreshTypes(self):
        self.type_combo.clear()
        junction = self.junction
        primary = junction.Proxy.getPrimaryComponent()
        net = hvaclib.getOwnerNetwork(junction)
        library_id = getattr(primary, "LibraryId", "") or (net.Proxy.getDefaultLibraryId() if net is not None else "")
        self._library_id = library_id

        port = self._currentPort()
        profile = port.get("profile", "") if port else ""
        # Always "through": an Inline device is always a physically
        # two-port device evaluated against its own attached leg, no
        # matter what the parent junction's real topology is.
        types = hvaclib.HVACLibraryService.list_inline_types(library_id, topology="through", profile=profile)
        self._types = types
        for tdef in types:
            self.type_combo.addItem(tdef.label, tdef.id)

    def _translatedPort(self, port):
        """
        Highlighting a port's raw AnalysisJson position would always land
        on the junction's shared pre-fitting anchor, not the physical duct
        wall / existing inline device chain a new component would actually
        attach to -- see hvaclib.translated_port_position().
        """
        return hvaclib.translated_port_position(self.junction, port)

    def _highlightCurrentPort(self):
        """Show a semi-transparent plane over the port picked in "Attach to
        edge", sized ~2x its own duct section and centered on where a new
        device would actually attach, so a user can see which physical
        connection it refers to before adding one there."""
        self._clearHighlight()
        port = self._currentPort()
        if port is None:
            return
        net = hvaclib.getOwnerNetwork(self.junction)
        vobj = getattr(net, "ViewObject", None) if net is not None else None
        if vobj is None:
            return
        try:
            self._highlight_root = buildPortHighlightCoinNode(self._translatedPort(port))
            vobj.RootNode.addChild(self._highlight_root)
        except Exception:
            self._highlight_root = None

    def _clearHighlight(self):
        if self._highlight_root is None:
            return
        try:
            net = hvaclib.getOwnerNetwork(self.junction)
            vobj = getattr(net, "ViewObject", None) if net is not None else None
            if vobj is not None:
                vobj.RootNode.removeChild(self._highlight_root)
        except Exception:
            pass
        self._highlight_root = None

    def _addComponent(self):
        port = self._currentPort()
        type_id = self.type_combo.currentData()
        if port is None or not type_id:
            return

        edge_key = port.get("edge_key", "")
        library_id = getattr(self, "_library_id", "")
        if edge_key and self.add_callback:
            self.add_callback(self.junction, edge_key, library_id, type_id)
        self._refreshComponentList()

    def accept(self):
        self._clearHighlight()
        return True

    def reject(self):
        self._clearHighlight()
        return True


class TaskPanelSegmentPlacementEditor:
    """Live editor for attachment, offset and profile X axis."""

    def __init__(self, objects, apply_callback=None):
        self.objects = [o for o in (objects or []) if o is not None]
        self.apply_callback = apply_callback
        self._loading = False
        self._attachment_buttons = {}
        self._axis_buttons = {}

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(
            translate("HVAC_EditPlacement", "Edit Segment Placement")
        )

        layout = QtWidgets.QVBoxLayout(self.form)

        info_text = translate(
            "HVAC_EditPlacement",
            "Selected objects: {}"
        ).format(len(self.objects))
        self.info_label = QtWidgets.QLabel(info_text)
        layout.addWidget(self.info_label)

        self.object_names = QtWidgets.QListWidget()
        self.object_names.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        for obj in self.objects:
            self.object_names.addItem("{} ({})".format(obj.Label, obj.Name))
        if len(self.objects) > 1:
            layout.addWidget(self.object_names)
            
        layout.addWidget(self._makeSeparator())

        # Attachment
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditPlacement", "Attachment:")
        ))
        layout.addLayout(self._buildAttachmentGrid())
        
        layout.addWidget(self._makeSeparator())

        # Offset
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditPlacement", "Offset:")
        ))
        layout.addLayout(self._buildOffsetEditors())
        
        layout.addWidget(self._makeSeparator())
        
        # Profile X axis
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_EditPlacement", "Profile X axis:")
        ))
        layout.addLayout(self._buildAxisButtons())

        self._loading = True
        try:
            self._loadFromSelection()
        finally:
            self._loading = False

    def _makeSeparator(self):
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        line.setLineWidth(1)
        line.setMidLineWidth(0)
        return line
    
    def _buildAttachmentGrid(self):
        grid = QtWidgets.QGridLayout()
        
        self.attachment_group = QtWidgets.QButtonGroup(self.form)
        self.attachment_group.setExclusive(True)

        items = [
            ("TopLeft",       "↖", 0, 0),
            ("TopCenter",     "↑", 0, 1),
            ("TopRight",      "↗", 0, 2),
            ("CenterLeft",    "←", 1, 0),
            ("Center",        "•", 1, 1),
            ("CenterRight",   "→", 1, 2),
            ("BottomLeft",    "↙", 2, 0),
            ("BottomCenter",  "↓", 2, 1),
            ("BottomRight",   "↘", 2, 2),
        ]

        for key, text, r, c in items:
            btn = QtWidgets.QToolButton()
            btn.setText(text)
            btn.setCheckable(True)
            btn.setToolTip(key)
            btn.setMinimumSize(40, 32)
            self.attachment_group.addButton(btn)
            self._attachment_buttons[key] = btn
            grid.addWidget(btn, r, c)

        self.attachment_group.buttonClicked.connect(lambda _btn: self._applyNow())
        return grid

    def _buildOffsetEditors(self):
        row = QtWidgets.QGridLayout()

        self.offset_x = QtWidgets.QDoubleSpinBox()
        self.offset_y = QtWidgets.QDoubleSpinBox()
        self.offset_z = QtWidgets.QDoubleSpinBox()

        for w in (self.offset_x, self.offset_y, self.offset_z):
            w.setDecimals(3)
            w.setRange(-1e6, 1e6)
            w.setSingleStep(10.0)
            w.editingFinished.connect(self._applyNow)

        row.addWidget(QtWidgets.QLabel("X"), 0, 0)
        row.addWidget(self.offset_x, 0, 1)
        row.addWidget(QtWidgets.QLabel("Y"), 1, 0)
        row.addWidget(self.offset_y, 1, 1)
        row.addWidget(QtWidgets.QLabel("Z"), 2, 0)
        row.addWidget(self.offset_z, 2, 1)

        return row

    def _buildAxisButtons(self):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)
        
        self.axis_group = QtWidgets.QButtonGroup(self.form)
        self.axis_group.setExclusive(True)

        axes = [
            ("Auto", FreeCAD.Vector(0, 0, 0)),
            ("X",    FreeCAD.Vector(1, 0, 0)),
            ("Y",    FreeCAD.Vector(0, 1, 0)),
            ("Z",    FreeCAD.Vector(0, 0, 1)),
        ]

        for label, vec in axes:
            btn = QtWidgets.QToolButton()
            btn.setText(label)
            btn.setCheckable(True)
            btn._axis_vec = FreeCAD.Vector(vec)
            self.axis_group.addButton(btn)
            self._axis_buttons[label] = btn
            row.addWidget(btn)

        self.axis_group.buttonClicked.connect(lambda _btn: self._applyNow())
        return row

    def _commonValue(self, getter):
        vals = []
        for obj in self.objects:
            try:
                vals.append(getter(obj))
            except Exception:
                vals.append(None)

        if not vals:
            return None

        first = vals[0]
        for v in vals[1:]:
            if v != first:
                return None
        return first

    def _loadFromSelection(self):
        attachment = self._commonValue(
            lambda o: str(getattr(o, "Attachment", "Center"))
        )
        if attachment in self._attachment_buttons:
            self._attachment_buttons[attachment].setChecked(True)
        elif "Center" in self._attachment_buttons:
            self._attachment_buttons["Center"].setChecked(True)

        offset = self._commonValue(
            lambda o: FreeCAD.Vector(getattr(o, "Offset", FreeCAD.Vector(0, 0, 0)))
        )
        if offset is not None:
            self.offset_x.setValue(offset.x)
            self.offset_y.setValue(offset.y)
            self.offset_z.setValue(offset.z)

        axis = self._commonValue(
            lambda o: FreeCAD.Vector(getattr(o, "ProfileXAxis", FreeCAD.Vector(0, 0, 0)))
        )

        if axis is None or axis == FreeCAD.Vector(0, 0, 0):
            self._axis_buttons["Auto"].setChecked(True)
        elif axis == FreeCAD.Vector(1, 0, 0):
            self._axis_buttons["X"].setChecked(True)
        elif axis == FreeCAD.Vector(0, 1, 0):
            self._axis_buttons["Y"].setChecked(True)
        elif axis == FreeCAD.Vector(0, 0, 1):
            self._axis_buttons["Z"].setChecked(True)
        else:
            self._axis_buttons["Auto"].setChecked(True)

    def _selectedAttachment(self):
        for key, btn in self._attachment_buttons.items():
            if btn.isChecked():
                return key
        return "Center"

    def _selectedProfileXAxis(self):
        for btn in self.axis_group.buttons():
            if btn.isChecked():
                return FreeCAD.Vector(btn._axis_vec)
        return FreeCAD.Vector(0, 0, 0)

    def _currentOffset(self):
        return FreeCAD.Vector(
            self.offset_x.value(),
            self.offset_y.value(),
            self.offset_z.value(),
        )

    def _applyNow(self):
        if self._loading or not self.apply_callback:
            return

        self.apply_callback(
            self.objects,
            attachment=self._selectedAttachment(),
            offset=self._currentOffset(),
            profile_x_axis=self._selectedProfileXAxis(),
        )

    def accept(self):
        # Live-apply panel; nothing extra on OK.
        return True

    def reject(self):
        return True


def _spreadsheet_cell(col, row):
    """0-based (col, row) -> a Spreadsheet::Sheet cell address, e.g. (0, 0) -> "A1", (27, 1) -> "AB2"."""
    letters = ""
    n = col
    while True:
        letters = chr(ord('A') + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            break
    return "{}{}".format(letters, row + 1)


def _resolveSelectableObjects(obj):
    """
    A DuctJunction has no Shape of its own and can't be highlighted in the
    3D view -- retarget to every one of its DuctComponent children: the
    Primary fitting AND any Inline devices (dampers, etc.) chained onto
    its own edges, so a junction's whole fitting geometry gets selected,
    not just its Primary.
    """
    if obj is None:
        return []
    if hvaclib.isDuctJunction(obj):
        proxy = getattr(obj, "Proxy", None)
        components = list(proxy.getComponents()) if proxy is not None else []
        return components if components else [obj]
    return [obj]


class _ResultsSelectionSync:
    """
    Two-way sync between one or more results tables' row selection and
    Gui.Selection, for the lifetime of a TaskPanelAirflowResults/
    TaskPanelSizeDucts panel: selecting a row selects that row's object in
    the 3D view, AND selecting a matching object in the 3D view (or tree)
    selects its row back -- across every table registered via addTable()
    (e.g. Calculate Airflow's segment AND junction tables at once).

    `_updating` guards against the two directions bouncing off each other
    forever: a table-driven Gui.Selection change would otherwise trigger
    this same class's own Gui.Selection observer, which would try to
    re-apply the very same table selection it just came from.
    """

    def __init__(self):
        self._tables = []  # [(QTableWidget, [obj, obj, ...]), ...]
        self._updating = False
        Gui.Selection.addObserver(self)

    def addTable(self, table, objs):
        """
        Row-based (not per-cell) selection, so clicking any cell picks the
        whole row's object -- `objs[row]` is that row's own object.
        """
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._tables.append((table, objs))
        table.itemSelectionChanged.connect(lambda t=table, o=objs: self._onTableSelectionChanged(t, o))

    def clearTables(self):
        """Drop every registered table -- call before rebuilding results (e.g. a re-run), since
        the old table widgets are about to be deleted and must not be referenced afterwards."""
        self._tables = []

    def stop(self):
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        self._tables = []

    def _onTableSelectionChanged(self, table, objs):
        if self._updating:
            return
        self._updating = True
        try:
            Gui.Selection.clearSelection()
            rows = {index.row() for index in table.selectedIndexes()}
            for row in rows:
                if 0 <= row < len(objs):
                    for target in _resolveSelectableObjects(objs[row]):
                        try:
                            Gui.Selection.addSelection(target.Document.Name, target.Name)
                        except Exception:
                            pass
        finally:
            self._updating = False

    def _syncTablesFromSelection(self):
        if self._updating:
            return
        self._updating = True
        try:
            selected = {
                (sel.Object.Document.Name, sel.Object.Name)
                for sel in Gui.Selection.getSelectionEx()
                if sel.Object is not None
            }
            for table, objs in self._tables:
                table.blockSignals(True)
                try:
                    table.clearSelection()
                    for row, obj in enumerate(objs):
                        targets = _resolveSelectableObjects(obj)
                        if any((t.Document.Name, t.Name) in selected for t in targets):
                            table.selectRow(row)
                finally:
                    table.blockSignals(False)
        finally:
            self._updating = False

    # ------------------------------------------------------------------
    # Gui.Selection observer protocol -- react to any selection change by
    # just re-reading the current selection fresh, rather than tracking
    # each method's own specific (doc, obj, sub, ...) args individually.
    # ------------------------------------------------------------------

    def addSelection(self, doc, obj, sub, pnt):
        self._syncTablesFromSelection()

    def removeSelection(self, doc, obj, sub):
        self._syncTablesFromSelection()

    def setSelection(self, doc):
        self._syncTablesFromSelection()

    def clearSelection(self, doc):
        self._syncTablesFromSelection()


class TaskPanelAirflowResults:
    """Read-only report panel showing the results of an airflow/pressure-drop calculation."""

    SEGMENT_HEADERS = ["Segment", "Flow (L/s)", "Velocity (m/s)", "Friction (Pa)",
                       "Fitting (Pa)", "Total Loss (Pa)", "Static Pressure (Pa)"]
    JUNCTION_HEADERS = ["Junction", "Total Flow (L/s)", "Static Pressure (Pa)", "Source", "Warning"]

    def __init__(self, network_obj, result):
        self.network_obj = network_obj

        # Terminal design-flow-rate arrow overlay, active for this panel's
        # whole lifetime -- see Observer.py:TerminalFlowRateObserver.
        self.flow_arrow_observer = TerminalFlowRateObserver(network_obj)
        self.flow_arrow_observer.start()

        # Two-way row <-> 3D-selection sync across every segment/junction
        # table this panel builds (one pair per sub-network tab).
        self.selection_sync = _ResultsSelectionSync()

        # Colored plane/overlay result visualization, active for this
        # panel's whole lifetime -- see Observer.py:AirflowResultObserver.
        # Off by default (both Enable checkboxes start unchecked).
        self.airflow_result_observer = AirflowResultObserver(
            network_obj, result, terminal_observer=self.flow_arrow_observer,
        )
        self.airflow_result_observer.start()

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_CalculateAirflow", "Airflow Calculation Results"))
        self.layout = QtWidgets.QVBoxLayout(self.form)

        self.layout.addWidget(self._buildVisualizationControls())

        self.results_container = QtWidgets.QWidget()
        self.results_layout = QtWidgets.QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.results_container)

        self.run_button = QtWidgets.QPushButton(
            translate("HVAC_CalculateAirflow", "Run Revised Calculation")
        )
        self.run_button.clicked.connect(self._runRevisedCalculation)
        self.layout.addWidget(self.run_button)

        self.export_button = QtWidgets.QPushButton(translate("HVAC_CalculateAirflow", "Export to Spreadsheet"))
        self.export_button.clicked.connect(self._exportToSpreadsheets)
        self.layout.addWidget(self.export_button)

        self._showResults(result)

    def _buildVisualizationControls(self):
        """
        "Enable" checkbox + "Color by" dropdown for each of the two result
        overlays AirflowResultObserver can draw -- see its own docstring.
        Text values on the overlays are always shown regardless of which
        dropdown entry is picked; only the overlay's own color follows it.
        """
        group = QtWidgets.QGroupBox(translate("HVAC_CalculateAirflow", "Result Visualization"))
        grid = QtWidgets.QGridLayout(group)

        self.junction_viz_checkbox = QtWidgets.QCheckBox(translate("HVAC_CalculateAirflow", "Junction Ports"))
        grid.addWidget(self.junction_viz_checkbox, 0, 0)
        grid.addWidget(QtWidgets.QLabel(translate("HVAC_CalculateAirflow", "Color by:")), 0, 1)
        self.junction_color_combo = QtWidgets.QComboBox()
        self.junction_color_combo.addItem(translate("HVAC_CalculateAirflow", "Flow Rate"), "flow_rate")
        self.junction_color_combo.addItem(translate("HVAC_CalculateAirflow", "Static Pressure"), "static_pressure")
        grid.addWidget(self.junction_color_combo, 0, 2)

        self.segment_viz_checkbox = QtWidgets.QCheckBox(translate("HVAC_CalculateAirflow", "Segments"))
        grid.addWidget(self.segment_viz_checkbox, 1, 0)
        grid.addWidget(QtWidgets.QLabel(translate("HVAC_CalculateAirflow", "Color by:")), 1, 1)
        self.segment_color_combo = QtWidgets.QComboBox()
        self.segment_color_combo.addItem(translate("HVAC_CalculateAirflow", "Velocity"), "velocity")
        self.segment_color_combo.addItem(translate("HVAC_CalculateAirflow", "Friction Drop"), "friction_drop")
        self.segment_color_combo.addItem(translate("HVAC_CalculateAirflow", "Pressure Loss"), "pressure_loss")
        grid.addWidget(self.segment_color_combo, 1, 2)

        self.junction_viz_checkbox.toggled.connect(self.airflow_result_observer.setJunctionEnabled)
        self.junction_color_combo.currentIndexChanged.connect(
            lambda: self.airflow_result_observer.setJunctionColorBy(self.junction_color_combo.currentData())
        )
        self.segment_viz_checkbox.toggled.connect(self.airflow_result_observer.setSegmentEnabled)
        self.segment_color_combo.currentIndexChanged.connect(
            lambda: self.airflow_result_observer.setSegmentColorBy(self.segment_color_combo.currentData())
        )

        return group

    def _clearResults(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _showResults(self, result):
        self.result = result
        self._clearResults()
        self.selection_sync.clearTables()
        self.airflow_result_observer.setResult(result)

        if result.warnings:
            warn_box = QtWidgets.QGroupBox(translate("HVAC_CalculateAirflow", "Warnings"))
            warn_layout = QtWidgets.QVBoxLayout(warn_box)
            warn_label = QtWidgets.QLabel("\n".join(result.warnings))
            warn_label.setWordWrap(True)
            warn_layout.addWidget(warn_label)
            self.results_layout.addWidget(warn_box)

        if not result.components:
            empty_label = QtWidgets.QLabel(
                translate("HVAC_CalculateAirflow", "No sub-network could be solved. See warnings above.")
            )
            empty_label.setWordWrap(True)
            self.results_layout.addWidget(empty_label)
        elif len(result.components) == 1:
            self.results_layout.addWidget(self._buildComponentWidget(result.components[0]))
        else:
            tabs = QtWidgets.QTabWidget()
            for i, comp in enumerate(result.components):
                tabs.addTab(self._buildComponentWidget(comp), "{} {}".format(
                    translate("HVAC_CalculateAirflow", "Sub-network"), i + 1
                ))
            self.results_layout.addWidget(tabs)

        self.export_button.setEnabled(bool(result.components))

    def _runRevisedCalculation(self):
        from ..core.AirflowSolver import AirflowSolver

        try:
            result = AirflowSolver(self.network_obj).solve()
        except Exception as e:
            FreeCAD.Console.PrintWarning(
                "HVAC - CalculateAirflow - Error solving network '{}': {}\n".format(self.network_obj.Label, e)
            )
            FreeCAD.Console.PrintMessage(traceback.format_exc())
            return

        doc = getattr(self.network_obj, "Document", None)
        if doc is not None:
            doc.recompute()

        self._showResults(result)

    def _buildComponentWidget(self, comp):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        summary = QtWidgets.QLabel(
            translate(
                "HVAC_CalculateAirflow",
                "Balancing terminal: {ref}\n"
                "Critical terminal: {crit}\n"
                "Required fan/AHU total pressure: {pa:.1f} Pa"
            ).format(
                ref=comp.reference_terminal_key,
                crit=comp.critical_terminal_key,
                pa=comp.critical_pressure_pa,
            )
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        seg_table = QtWidgets.QTableWidget(len(comp.segments), len(self.SEGMENT_HEADERS))
        seg_table.setHorizontalHeaderLabels(self.SEGMENT_HEADERS)
        seg_table.verticalHeader().setVisible(False)
        seg_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, seg in enumerate(comp.segments):
            values = [
                seg.obj.Label,
                "{:.2f}".format(seg.flow_lps),
                "{:.2f}".format(seg.velocity_ms),
                "{:.2f}".format(seg.friction_loss_pa),
                "{:.2f}".format(seg.fitting_loss_pa),
                "{:.2f}".format(seg.total_loss_pa),
                "{:.1f}".format(seg.cumulative_pressure_pa),
            ]
            for col, value in enumerate(values):
                seg_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        seg_table.resizeColumnsToContents()
        self.selection_sync.addTable(seg_table, [seg.obj for seg in comp.segments])
        layout.addWidget(QtWidgets.QLabel(translate("HVAC_CalculateAirflow", "Segments")))
        layout.addWidget(seg_table)

        junc_table = QtWidgets.QTableWidget(len(comp.junctions), len(self.JUNCTION_HEADERS))
        junc_table.setHorizontalHeaderLabels(self.JUNCTION_HEADERS)
        junc_table.verticalHeader().setVisible(False)
        junc_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, junc in enumerate(comp.junctions):
            values = [
                junc.obj.Label,
                "{:.2f}".format(junc.total_flow_lps),
                "{:.1f}".format(junc.static_pressure_pa),
                translate("HVAC_CalculateAirflow", "Yes") if junc.is_source else "",
                junc.warning,
            ]
            for col, value in enumerate(values):
                junc_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        junc_table.resizeColumnsToContents()
        self.selection_sync.addTable(junc_table, [junc.obj for junc in comp.junctions])
        layout.addWidget(QtWidgets.QLabel(translate("HVAC_CalculateAirflow", "Junctions")))
        layout.addWidget(junc_table)

        return widget

    def _exportToSpreadsheets(self):
        """
        Write every sub-network's segment/junction results into two
        Spreadsheet::Sheet document objects (one row per segment/junction,
        same columns as the on-screen tables) -- lets a user take the
        numbers into a report without retyping them. A "Sub-network"
        column is added only when there's more than one component, mirroring
        when the on-screen view itself splits into per-sub-network tabs.
        """
        doc = getattr(self.network_obj, "Document", None)
        if doc is None:
            return

        multi = len(self.result.components) > 1
        segment_headers = list(self.SEGMENT_HEADERS)
        junction_headers = list(self.JUNCTION_HEADERS)
        if multi:
            segment_headers = [translate("HVAC_CalculateAirflow", "Sub-network")] + segment_headers
            junction_headers = [translate("HVAC_CalculateAirflow", "Sub-network")] + junction_headers

        segment_rows = []
        junction_rows = []
        for i, comp in enumerate(self.result.components):
            sub_network_label = "{} {}".format(translate("HVAC_CalculateAirflow", "Sub-network"), i + 1)

            for seg in comp.segments:
                row = [
                    seg.obj.Label,
                    "{:.2f}".format(seg.flow_lps),
                    "{:.2f}".format(seg.velocity_ms),
                    "{:.2f}".format(seg.friction_loss_pa),
                    "{:.2f}".format(seg.fitting_loss_pa),
                    "{:.2f}".format(seg.total_loss_pa),
                    "{:.1f}".format(seg.cumulative_pressure_pa),
                ]
                segment_rows.append([sub_network_label] + row if multi else row)

            for junc in comp.junctions:
                row = [
                    junc.obj.Label,
                    "{:.2f}".format(junc.total_flow_lps),
                    "{:.1f}".format(junc.static_pressure_pa),
                    translate("HVAC_CalculateAirflow", "Yes") if junc.is_source else "",
                    junc.warning,
                ]
                junction_rows.append([sub_network_label] + row if multi else row)

        seg_sheet = self._writeSheet(
            doc, "AirflowSegments",
            translate("HVAC_CalculateAirflow", "Airflow - Segments"),
            segment_headers, segment_rows,
        )
        junc_sheet = self._writeSheet(
            doc, "AirflowJunctions",
            translate("HVAC_CalculateAirflow", "Airflow - Junctions"),
            junction_headers, junction_rows,
        )
        doc.recompute()

        FreeCAD.Console.PrintMessage(
            "HVAC - Exported airflow results to '{}' and '{}'.\n".format(seg_sheet.Label, junc_sheet.Label)
        )
        # Export closes the dialog directly (not via accept()/reject()), so
        # every overlay/sync helper must be torn down here too.
        self.flow_arrow_observer.stop()
        self.selection_sync.stop()
        self.airflow_result_observer.stop()
        Gui.Control.closeDialog()

    @staticmethod
    def _writeSheet(doc, base_name, label, headers, rows):
        sheet = doc.addObject("Spreadsheet::Sheet", doc.getUniqueObjectName(base_name))
        sheet.Label = label
        for col, header in enumerate(headers):
            sheet.set(_spreadsheet_cell(col, 0), str(header))
        for row_index, row in enumerate(rows, start=1):
            for col, value in enumerate(row):
                sheet.set(_spreadsheet_cell(col, row_index), str(value))
        return sheet

    def accept(self):
        self.flow_arrow_observer.stop()
        self.selection_sync.stop()
        self.airflow_result_observer.stop()
        return True

    def reject(self):
        self.flow_arrow_observer.stop()
        self.selection_sync.stop()
        self.airflow_result_observer.stop()
        return True


class TaskPanelSizeDucts:
    """
    Combined duct-sizing task panel: an editable "Sizing Parameters"
    section (the network's own SizingMethod/TargetVelocity/etc -- the same
    "HVAC Duct Sizing" group properties otherwise only reachable through
    the raw property editor) with a "Run Duct Sizing" button, followed by
    the results/warnings section built fresh each time that button is
    clicked. OK applies the last computed proposed sizes; Cancel discards
    them (parameter edits already written to the network by a Run are
    kept either way, same as editing them directly in the property editor
    would be).
    """

    HEADERS = ["Segment", "Profile", "Current Size", "Proposed Size", "Velocity (m/s)",
               "Friction Rate (Pa/m)", "Balanced"]

    SIZING_METHODS = [
        ("ConstantVelocity", "Constant Velocity"),
        ("ConstantFrictionRate", "Constant Friction Rate"),
        ("StaticRegain", "Static Regain"),
        ("PressureBalancedStaticRegain", "Pressure-Balanced Static Regain"),
    ]
    RECTANGULAR_SIZING_MODES = [
        ("FixedAspectRatio", "Fixed Aspect Ratio"),
        ("FixedHeight", "Fixed Height"),
        ("FixedWidth", "Fixed Width"),
    ]

    def __init__(self, network_obj):
        self.network_obj = network_obj
        self.sizer = None
        self.result = None

        # Terminal design-flow-rate arrow overlay, active for this panel's
        # whole lifetime -- see Observer.py:TerminalFlowRateObserver.
        self.flow_arrow_observer = TerminalFlowRateObserver(network_obj)
        self.flow_arrow_observer.start()

        # Two-way row <-> 3D-selection sync for the results table.
        self.selection_sync = _ResultsSelectionSync()

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_SizeDucts", "Size Ducts"))
        self.layout = QtWidgets.QVBoxLayout(self.form)

        self.layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_SizeDucts", "Network: {}").format(network_obj.Label)
        ))

        self.layout.addWidget(self._buildParametersGroup())

        self.run_button = QtWidgets.QPushButton(translate("HVAC_SizeDucts", "Run Duct Sizing"))
        self.run_button.clicked.connect(self._runSizing)
        self.layout.addWidget(self.run_button)

        # Populated by _showResults() each time Run is clicked -- empty
        # until the first run.
        self.results_container = QtWidgets.QWidget()
        self.results_layout = QtWidgets.QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.results_container)

        self._loadFromNetwork()
        self.sizing_method_combo.currentIndexChanged.connect(self._refreshParameterVisibility)
        self.rectangular_sizing_mode_combo.currentIndexChanged.connect(self._refreshParameterVisibility)
        self._refreshParameterVisibility()

    def _buildParametersGroup(self):
        group = QtWidgets.QGroupBox(translate("HVAC_SizeDucts", "Sizing Parameters"))
        grid = QtWidgets.QGridLayout(group)
        row = 0

        grid.addWidget(QtWidgets.QLabel(translate("HVAC_SizeDucts", "Sizing method:")), row, 0)
        self.sizing_method_combo = QtWidgets.QComboBox()
        for value, label in self.SIZING_METHODS:
            self.sizing_method_combo.addItem(translate("HVAC_SizeDucts", label), value)
        grid.addWidget(self.sizing_method_combo, row, 1)
        row += 1

        self.target_velocity_label = QtWidgets.QLabel(translate("HVAC_SizeDucts", "Target velocity:"))
        grid.addWidget(self.target_velocity_label, row, 0)
        self.target_velocity = QtWidgets.QDoubleSpinBox()
        self.target_velocity.setDecimals(2)
        self.target_velocity.setRange(0.0, 1e6)
        self.target_velocity.setSingleStep(0.5)
        self.target_velocity.setSuffix(" m/s")
        grid.addWidget(self.target_velocity, row, 1)
        row += 1

        self.target_friction_rate_label = QtWidgets.QLabel(translate("HVAC_SizeDucts", "Target friction rate:"))
        grid.addWidget(self.target_friction_rate_label, row, 0)
        self.target_friction_rate = QtWidgets.QDoubleSpinBox()
        self.target_friction_rate.setDecimals(3)
        self.target_friction_rate.setRange(0.0, 1e6)
        self.target_friction_rate.setSingleStep(0.1)
        self.target_friction_rate.setSuffix(" Pa/m")
        grid.addWidget(self.target_friction_rate, row, 1)
        row += 1

        self.static_regain_factor_label = QtWidgets.QLabel(translate("HVAC_SizeDucts", "Static regain factor:"))
        grid.addWidget(self.static_regain_factor_label, row, 0)
        self.static_regain_factor = QtWidgets.QDoubleSpinBox()
        self.static_regain_factor.setDecimals(2)
        self.static_regain_factor.setRange(0.0, 1.0)
        self.static_regain_factor.setSingleStep(0.05)
        grid.addWidget(self.static_regain_factor, row, 1)
        row += 1

        self.minimum_velocity_label = QtWidgets.QLabel(translate("HVAC_SizeDucts", "Minimum velocity:"))
        grid.addWidget(self.minimum_velocity_label, row, 0)
        self.minimum_velocity = QtWidgets.QDoubleSpinBox()
        self.minimum_velocity.setDecimals(2)
        self.minimum_velocity.setRange(0.0, 1e6)
        self.minimum_velocity.setSingleStep(0.5)
        self.minimum_velocity.setSuffix(" m/s")
        grid.addWidget(self.minimum_velocity, row, 1)
        row += 1

        grid.addWidget(QtWidgets.QLabel(translate("HVAC_SizeDucts", "Rectangular sizing mode:")), row, 0)
        self.rectangular_sizing_mode_combo = QtWidgets.QComboBox()
        for value, label in self.RECTANGULAR_SIZING_MODES:
            self.rectangular_sizing_mode_combo.addItem(translate("HVAC_SizeDucts", label), value)
        grid.addWidget(self.rectangular_sizing_mode_combo, row, 1)
        row += 1

        self.target_aspect_ratio_label = QtWidgets.QLabel(translate("HVAC_SizeDucts", "Target aspect ratio:"))
        grid.addWidget(self.target_aspect_ratio_label, row, 0)
        self.target_aspect_ratio = QtWidgets.QDoubleSpinBox()
        self.target_aspect_ratio.setDecimals(2)
        self.target_aspect_ratio.setRange(0.1, 100.0)
        self.target_aspect_ratio.setSingleStep(0.1)
        grid.addWidget(self.target_aspect_ratio, row, 1)
        row += 1

        grid.addWidget(QtWidgets.QLabel(translate("HVAC_SizeDucts", "Size rounding increment:")), row, 0)
        self.size_rounding_increment = QtWidgets.QDoubleSpinBox()
        self.size_rounding_increment.setDecimals(1)
        self.size_rounding_increment.setRange(0.1, 1e6)
        self.size_rounding_increment.setSingleStep(5.0)
        self.size_rounding_increment.setSuffix(" mm")
        grid.addWidget(self.size_rounding_increment, row, 1)

        return group

    def _loadFromNetwork(self):
        net = self.network_obj

        method = str(getattr(net, "SizingMethod", "ConstantVelocity") or "ConstantVelocity")
        idx = self.sizing_method_combo.findData(method)
        self.sizing_method_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.target_velocity.setValue(float(getattr(net, "TargetVelocity", 5.0) or 5.0))
        self.target_friction_rate.setValue(float(getattr(net, "TargetFrictionRate", 1.0) or 1.0))
        self.static_regain_factor.setValue(float(getattr(net, "StaticRegainFactor", 0.75) or 0.75))
        self.minimum_velocity.setValue(float(getattr(net, "MinimumVelocity", 2.5) or 2.5))

        mode = str(getattr(net, "RectangularSizingMode", "FixedAspectRatio") or "FixedAspectRatio")
        idx = self.rectangular_sizing_mode_combo.findData(mode)
        self.rectangular_sizing_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.target_aspect_ratio.setValue(float(getattr(net, "TargetAspectRatio", 2.0) or 2.0))
        self.size_rounding_increment.setValue(float(getattr(net, "SizeRoundingIncrement", 10.0) or 10.0))

    def _refreshParameterVisibility(self):
        """Only show the parameters relevant to the currently-picked SizingMethod/RectangularSizingMode."""
        method = self.sizing_method_combo.currentData()
        regain_family = ("StaticRegain", "PressureBalancedStaticRegain")
        for widget in (self.target_velocity_label, self.target_velocity):
            widget.setVisible(method == "ConstantVelocity" or method in regain_family)
        for widget in (self.target_friction_rate_label, self.target_friction_rate):
            widget.setVisible(method == "ConstantFrictionRate")
        for widget in (self.static_regain_factor_label, self.static_regain_factor):
            widget.setVisible(method in regain_family)
        for widget in (self.minimum_velocity_label, self.minimum_velocity):
            widget.setVisible(method in regain_family)

        show_aspect_ratio = self.rectangular_sizing_mode_combo.currentData() == "FixedAspectRatio"
        for widget in (self.target_aspect_ratio_label, self.target_aspect_ratio):
            widget.setVisible(show_aspect_ratio)

    def _applyParametersToNetwork(self):
        net = self.network_obj
        net.SizingMethod = self.sizing_method_combo.currentData()
        net.TargetVelocity = self.target_velocity.value()
        net.TargetFrictionRate = self.target_friction_rate.value()
        net.StaticRegainFactor = self.static_regain_factor.value()
        net.MinimumVelocity = self.minimum_velocity.value()
        net.RectangularSizingMode = self.rectangular_sizing_mode_combo.currentData()
        net.TargetAspectRatio = self.target_aspect_ratio.value()
        net.SizeRoundingIncrement = self.size_rounding_increment.value()

    def _runSizing(self):
        from ..core.DuctSizer import DuctSizer

        self._applyParametersToNetwork()

        sizer = DuctSizer(self.network_obj)
        try:
            result = sizer.solve()
        except Exception as e:
            FreeCAD.Console.PrintWarning(
                "HVAC - SizeDucts - Error sizing network '{}': {}\n".format(self.network_obj.Label, e)
            )
            FreeCAD.Console.PrintMessage(traceback.format_exc())
            return

        self.sizer = sizer
        self.result = result
        self._showResults(result)

    def _clearResults(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _showResults(self, result):
        self._clearResults()
        self.selection_sync.clearTables()

        if result.warnings:
            warn_box = QtWidgets.QGroupBox(translate("HVAC_SizeDucts", "Warnings"))
            warn_layout = QtWidgets.QVBoxLayout(warn_box)
            warn_label = QtWidgets.QLabel("\n".join(result.warnings))
            warn_label.setWordWrap(True)
            warn_layout.addWidget(warn_label)
            self.results_layout.addWidget(warn_box)

        info = QtWidgets.QLabel(
            translate(
                "HVAC_SizeDucts",
                "Review the proposed duct sizes below, then click OK to apply them to the "
                "segments, or Cancel to discard."
            )
        )
        info.setWordWrap(True)
        self.results_layout.addWidget(info)

        table = QtWidgets.QTableWidget(len(result.segments), len(self.HEADERS))
        table.setHorizontalHeaderLabels(self.HEADERS)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row, sres in enumerate(result.segments):
            current = self._formatSize(sres.profile, sres.old_diameter_mm, sres.old_width_mm, sres.old_height_mm)
            proposed = self._formatSize(sres.profile, sres.new_diameter_mm, sres.new_width_mm, sres.new_height_mm)
            if not sres.changed:
                proposed = translate("HVAC_SizeDucts", "(unchanged)")
            values = [
                sres.obj.Label,
                sres.profile,
                current,
                proposed,
                "{:.2f}".format(sres.velocity_ms),
                "{:.3f}".format(sres.friction_rate_pa_per_m),
                "" if sres.regain_balanced else translate("HVAC_SizeDucts", "No (see warnings)"),
            ]
            for col, value in enumerate(values):
                table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        table.resizeColumnsToContents()
        self.selection_sync.addTable(table, [sres.obj for sres in result.segments])
        self.results_layout.addWidget(table)

    def _formatSize(self, profile, diameter_mm, width_mm, height_mm):
        if profile == "Circular":
            return "{:.0f} mm dia".format(diameter_mm)
        return "{:.0f} x {:.0f} mm".format(width_mm, height_mm)

    def accept(self):
        self.flow_arrow_observer.stop()
        self.selection_sync.stop()
        if self.sizer is not None and self.result is not None:
            changed_count = self.sizer.apply(self.result)
            if changed_count and getattr(self.network_obj, "Document", None):
                self.network_obj.Document.recompute()
        return True

    def reject(self):
        self.flow_arrow_observer.stop()
        self.selection_sync.stop()
        return True
