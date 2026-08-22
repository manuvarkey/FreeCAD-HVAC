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

import FreeCAD
import FreeCADGui as Gui
import Materials
import MatGui  # registers MatGui::MaterialTreeWidget with Gui.UiLoader -- see MaterialPickerDialog
from PySide import QtWidgets, QtCore
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from ..utils import hvaclib
from ..utils import materials as hvac_materials


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


class TaskPanelAddInlineComponent:
    """
    Task panel for adding an Inline device (damper, silencer, ...) to a
    junction: pick which real edge to attach it to and which type, in one
    step (rather than two sequential pop-up dialogs).
    """

    def __init__(self, junction, apply_callback=None):
        self.junction = junction
        self.apply_callback = apply_callback
        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_AddInlineComponent", "Add Inline Component"))

        layout = QtWidgets.QVBoxLayout(self.form)
        layout.addWidget(QtWidgets.QLabel(
            translate("HVAC_AddInlineComponent", "Junction: {}").format(junction.Label)
        ))

        try:
            analysis = json.loads(getattr(junction, "AnalysisJson", "") or "{}")
        except Exception:
            analysis = {}
        self.ports = list(analysis.get("connected_ports", []) or [])

        layout.addWidget(QtWidgets.QLabel(translate("HVAC_AddInlineComponent", "Attach to edge:")))
        self.edge_combo = QtWidgets.QComboBox()
        for port in self.ports:
            self.edge_combo.addItem(self._portLabel(port))
        layout.addWidget(self.edge_combo)
        # Degree 1: nothing to choose, but still shown for clarity.
        self.edge_combo.setEnabled(len(self.ports) > 1)

        layout.addWidget(QtWidgets.QLabel(translate("HVAC_AddInlineComponent", "Type:")))
        self.type_combo = QtWidgets.QComboBox()
        layout.addWidget(self.type_combo)

        self._refreshTypes()
        self.edge_combo.currentIndexChanged.connect(self._refreshTypes)

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
        role = translate("HVAC_AddInlineComponent", "inlet") if port.get("flow_into_junction") else translate("HVAC_AddInlineComponent", "outlet")
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

    def accept(self):
        port = self._currentPort()
        type_id = self.type_combo.currentData()
        if port is None or not type_id:
            return True

        edge_key = port.get("edge_key", "")
        library_id = getattr(self, "_library_id", "")
        if edge_key and self.apply_callback:
            QtCore.QTimer.singleShot(
                0,
                lambda: self.apply_callback(self.junction, edge_key, library_id, type_id),
            )
        return True

    def reject(self):
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


class TaskPanelAirflowResults:
    """Read-only report panel showing the results of an airflow/pressure-drop calculation."""

    SEGMENT_HEADERS = ["Segment", "Flow (L/s)", "Velocity (m/s)", "Friction (Pa)",
                       "Fitting (Pa)", "Total Loss (Pa)", "Static Pressure (Pa)"]
    JUNCTION_HEADERS = ["Junction", "Total Flow (L/s)", "Static Pressure (Pa)", "Source", "Warning"]

    def __init__(self, network_obj, result):
        self.network_obj = network_obj
        self.result = result

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_CalculateAirflow", "Airflow Calculation Results"))
        layout = QtWidgets.QVBoxLayout(self.form)

        if result.warnings:
            warn_box = QtWidgets.QGroupBox(translate("HVAC_CalculateAirflow", "Warnings"))
            warn_layout = QtWidgets.QVBoxLayout(warn_box)
            warn_label = QtWidgets.QLabel("\n".join(result.warnings))
            warn_label.setWordWrap(True)
            warn_layout.addWidget(warn_label)
            layout.addWidget(warn_box)

        if not result.components:
            empty_label = QtWidgets.QLabel(
                translate("HVAC_CalculateAirflow", "No sub-network could be solved. See warnings above.")
            )
            empty_label.setWordWrap(True)
            layout.addWidget(empty_label)
        elif len(result.components) == 1:
            layout.addWidget(self._buildComponentWidget(result.components[0]))
        else:
            tabs = QtWidgets.QTabWidget()
            for i, comp in enumerate(result.components):
                tabs.addTab(self._buildComponentWidget(comp), "{} {}".format(
                    translate("HVAC_CalculateAirflow", "Sub-network"), i + 1
                ))
            layout.addWidget(tabs)

        close_button = QtWidgets.QPushButton(translate("HVAC_CalculateAirflow", "Close"))
        close_button.clicked.connect(lambda: Gui.Control.closeDialog())
        layout.addWidget(close_button)

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
        layout.addWidget(QtWidgets.QLabel(translate("HVAC_CalculateAirflow", "Junctions")))
        layout.addWidget(junc_table)

        return widget

    def accept(self):
        return True

    def reject(self):
        return True


class TaskPanelDuctSizingResults:
    """Preview panel for a duct-sizing pass. OK applies the proposed sizes; Cancel discards them."""

    HEADERS = ["Segment", "Profile", "Current Size", "Proposed Size", "Velocity (m/s)",
               "Friction Rate (Pa/m)", "Balanced"]

    def __init__(self, network_obj, sizer, result):
        self.network_obj = network_obj
        self.sizer = sizer
        self.result = result

        self.form = QtWidgets.QWidget()
        self.form.setWindowTitle(translate("HVAC_SizeDucts", "Duct Sizing Results"))
        layout = QtWidgets.QVBoxLayout(self.form)

        if result.warnings:
            warn_box = QtWidgets.QGroupBox(translate("HVAC_SizeDucts", "Warnings"))
            warn_layout = QtWidgets.QVBoxLayout(warn_box)
            warn_label = QtWidgets.QLabel("\n".join(result.warnings))
            warn_label.setWordWrap(True)
            warn_layout.addWidget(warn_label)
            layout.addWidget(warn_box)

        info = QtWidgets.QLabel(
            translate(
                "HVAC_SizeDucts",
                "Review the proposed duct sizes below, then click OK to apply them to the "
                "segments, or Cancel to discard."
            )
        )
        info.setWordWrap(True)
        layout.addWidget(info)

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
        layout.addWidget(table)

    def _formatSize(self, profile, diameter_mm, width_mm, height_mm):
        if profile == "Circular":
            return "{:.0f} mm dia".format(diameter_mm)
        return "{:.0f} x {:.0f} mm".format(width_mm, height_mm)

    def accept(self):
        changed_count = self.sizer.apply(self.result)
        if changed_count and getattr(self.network_obj, "Document", None):
            self.network_obj.Document.recompute()
        return True

    def reject(self):
        return True
