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
import freecad.HVAC.DuctNetworkConfigDialog as DuctNetworkConfigDialog
import freecad.HVAC.hvaclib as hvaclib

from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

#=================================================
# A. Main classes
#=================================================

class DuctNetwork:

    """Visualize and configure HVAC duct network in FreeCAD's 3D view."""

    def __init__(self, obj):
        obj.Proxy = self
        self.setProperties(obj)

    def setProperties(self,obj):
        """Gives the object properties to HVAC ducts."""
        pass


class DuctNetworkViewProvider:

    """A View Provider for the HVAC duct network object"""

    def __init__(self, obj):
        obj.Proxy = self

    def getIcon(self):
        return hvaclib.get_icon_path("DuctsIcon.svg")


#=================================================
# A. Command classes
#=================================================


class CommandCreateDuctNetwork:

    """Create HVAC Duct Network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {'Pixmap': hvaclib.get_icon_path("CreateDuctsIcon.svg"),
                'MenuText': QT_TRANSLATE_NOOP('HVAC_CreateDuctNetwork', 'Create HVAC Duct Network'),
                'ToolTip': QT_TRANSLATE_NOOP('HVAC_CreateDuctNetwork', 'Create HVAC Duct Network from Sketch/ Line base Geometries')}

    def IsActive(self):
        if Gui.ActiveDocument:
            return True
        else:
            return False

    def Activated(self):
        create_new_duct_network()


class CommandModifyDuctNetwork:

    """Modify HVAC Duct Network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {'Pixmap': hvaclib.get_icon_path("ModifyDuctsIcon.svg"),
                'MenuText': QT_TRANSLATE_NOOP('HVAC_ModifyDuctNetwork', 'Modify HVAC Duct Network'),
                'ToolTip': QT_TRANSLATE_NOOP('HVAC_ModifyDuctNetwork',  'Modify the selected HVAC Duct Network')}

    def IsActive(self):
        if Gui.ActiveDocument:
            try:
                FreeCAD.ActiveDocument.findObjects(Name = "DuctNetwork")[0].Name
                return True
            except:
                pass
        else:
            return False

    def Activated(self):
        modify_duct_network()


class CommandDeleteDuctNetwork:

    """Delete a selected HVAC Duct Network."""

    def QT_TRANSLATE_NOOP(self, text):
        return text

    def GetResources(self):
        return {'Pixmap': hvaclib.get_icon_path("DeleteDuctsIcon.svg"),
                'MenuText': QT_TRANSLATE_NOOP('DeleteDucts', 'Delete HVAC ducts'),
                'ToolTip': QT_TRANSLATE_NOOP('DeleteDucts', 'Instructions')}

    def IsActive(self):
        if Gui.ActiveDocument:
            try:
                FreeCAD.ActiveDocument.findObjects(Name = "DuctNetwork")[0].Name
                return True
            except:
                pass
        else:
            return False

    def Activated(self):
        delete_duct_network()


#=================================================
# C. General functions
#=================================================


def create_new_duct_network():
    """Create new duct network"""
    # Create new duct netowork and create default folders
    folder = FreeCAD.ActiveDocument.addObject(
             'App::DocumentObjectGroupPython',
             'DuctNetwork')
    DuctNetwork(folder)
    DuctNetworkViewProvider(folder.ViewObject)
    print("New DuctNetwork created...")
    # Open duct network settings
    DuctNetworkConfigDialog.open_duct_network_configuration()
    # Recompute document
    FreeCAD.ActiveDocument.recompute()

def modify_duct_network():
    """Modify the selected HVAC duct network object"""
    pass

def delete_duct_network():
    """Delete the selected HVAC duct network object"""
    pass


#=================================================
# D. Register Commands
#=================================================

if FreeCAD.GuiUp:
    FreeCAD.Gui.addCommand('HVAC_CreateDuctNetwork', CommandCreateDuctNetwork())
    FreeCAD.Gui.addCommand('HVAC_ModifyDuctNetwork', CommandModifyDuctNetwork())
    FreeCAD.Gui.addCommand('HVAC_DeleteDuctNetwork', CommandDeleteDuctNetwork())
