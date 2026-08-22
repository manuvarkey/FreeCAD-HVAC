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
#                                      `                                        #
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
import math
import FreeCAD
import FreeCADGui as Gui
from pivy import coin
from PySide import QtWidgets, QtCore, QtGui
from PySide.QtCore import QT_TRANSLATE_NOOP
translate = FreeCAD.Qt.translate

from ..utils import hvaclib
from ..ui import Toolbar


class SketchObserver:
    """New sketch creation/ modification observer"""

    def __init__(self, network_obj, callback, edit_mode=False):
        self.network_obj = network_obj
        self.callback = callback
        self.edit_mode = edit_mode
        self.doc = network_obj.Document
        self.tracked_sketch = None
        self.finished = False
        self._seen_dialog = False
        self._arrow_root = None  # For showing base direction arrows
        
        # Suspend sync to prevent transient sync requests while sketching
        if self.network_obj and hasattr(self.network_obj, "Proxy") and self.network_obj.Proxy:
            self.network_obj.Proxy.suspendSync()
            
        self._timer = QtCore.QTimer()
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.check_finished)
        self._timer.start()

        # Create temporary toolbar
        self._temp_toolbar = Toolbar.FloatingBaseEditToolbar
        Toolbar.create_toolbar(self._temp_toolbar)

    def slotCreatedObject(self, obj):
        # Called when a new object is created in the document
        if self.finished or self.tracked_sketch is not None or self.edit_mode:
            return
        if obj and obj.Document == self.doc and hvaclib.isSketch(obj):
            self.tracked_sketch = obj
            self._attach_arrows()
            Toolbar.show_toolbar(self._temp_toolbar)
            
    def set_modified_sketch(self, sketch):
        """Set the modified sketch and attach arrows if in edit mode."""
        if self.edit_mode:
            self.tracked_sketch = sketch
            self._attach_arrows()
            Toolbar.show_toolbar(self._temp_toolbar)
            
    def _attach_arrows(self):
        """Inject arrow separator into the sketch's Coin scene."""
        try:
            vp = self.network_obj.ViewObject
            if vp is None:
                return
            self._arrow_root = coin.SoSeparator()
            vp.RootNode.addChild(self._arrow_root)
        except Exception:
            FreeCAD.Console.PrintError("Unable to attach arrows to sketch")
            pass
            
    def _detach_arrows(self):
        """Remove arrow separator from the sketch's Coin scene."""
        if self._arrow_root is None:
            return
        try:
            vp = self.network_obj.ViewObject
            if vp is not None:
                vp.RootNode.removeChild(self._arrow_root)
        except Exception:
            pass
        self._arrow_root = None
        
    def _sync_arrows(self):
        if self._arrow_root is None:
            return
        self._arrow_root.removeAllChildren()
        lines = [
            (geo.StartPoint, geo.EndPoint)
            for geo in self.tracked_sketch.Geometry
            if hasattr(geo, 'StartPoint') and hasattr(geo, 'EndPoint')
        ]
        if lines:
            self._arrow_root.addChild(buildArrowCoinNodes(lines))
        
    def slotChangedObject(self, obj, prop):
        """Rebuild arrows whenever sketch geometry changes while editing."""
        if self.finished or self.tracked_sketch is None:
            return
        if obj != self.tracked_sketch or prop != "Geometry":
            return
        self._sync_arrows()

    def check_finished(self):
        """Detect when the sketch edition has been exited."""
        if self.finished:
            return
        # Sketcher normally opens a task panel/dialog while active.
        if Gui.Control.activeDialog():
            self._seen_dialog = True
            return
        # Finalize only after the dialog has appeared once and then closed.
        if self._seen_dialog:
            self._timer.stop()
            QtCore.QTimer.singleShot(0, self.finalize)
            return True

    def finalize(self):
        if self.finished:
            return
        self.finished = True
        
        if self.tracked_sketch is not None:
            self._detach_arrows()
            Toolbar.hide_toolbar(self._temp_toolbar)
        
        try:
            self.callback(self.network_obj, self.tracked_sketch)
        finally:
            FreeCAD.removeDocumentObserver(self)
            # Resume sync after sketching is done and request sync
            if self.network_obj and hasattr(self.network_obj, "Proxy") and self.network_obj.Proxy:
                self.network_obj.Proxy.resumeSync(request_sync=True)


class DraftLineObserver:
    """Observe Draft line creation/ modification and add all created lines to the network
    after the Draft tool is closed.
    """

    def __init__(self, network_obj, callback, edit_mode=False):
        self.network_obj = network_obj
        self.callback = callback
        self.edit_mode = edit_mode
        self.doc = network_obj.Document
        self.tracked_objects = []
        self.finished = False
        self._seen_dialog = False
        self._arrow_root = None  # For showing base direction arrows
        
        # Suspend sync to prevent transient sync requests while creating lines
        if self.network_obj and hasattr(self.network_obj, "Proxy") and self.network_obj.Proxy:
            self.network_obj.Proxy.suspendSync()

        self._timer = QtCore.QTimer()
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.check_finished)
        self._timer.start()

    def slotCreatedObject(self, obj):
        """Called whenever a new object is created in the document."""
        if self.finished or self.edit_mode:
            return
        if not obj or obj.Document != self.doc:
            return
        if obj not in self.tracked_objects:
            self.tracked_objects.append(obj)
            
    def set_modified_line(self, line):
        """Set the modified line and attach arrows if in edit mode."""
        if self.edit_mode:
            self.tracked_objects.append(line)
            self._attach_arrows()
            
    def _attach_arrows(self):
        """Inject arrow separator into the sketch's Coin scene."""
        try:
            vp = self.network_obj.ViewObject
            if vp is None:
                return
            self._arrow_root = coin.SoSeparator()
            vp.RootNode.addChild(self._arrow_root)
        except Exception:
            FreeCAD.Console.PrintError("Unable to attach arrows to sketch")
            pass
            
    def _detach_arrows(self):
        """Remove arrow separator from the sketch's Coin scene."""
        if self._arrow_root is None:
            return
        try:
            vp = self.network_obj.ViewObject
            if vp is not None:
                vp.RootNode.removeChild(self._arrow_root)
        except Exception:
            pass
        self._arrow_root = None
        
    def _sync_arrows(self):
        if self._arrow_root is None:
            return
        self._arrow_root.removeAllChildren()
        lines = []
        for obj in self.tracked_objects:
            lines.extend(iter_line_segments_from_shape(obj))
        if lines:
            self._arrow_root.addChild(buildArrowCoinNodes(lines))
        
    def slotChangedObject(self, obj, prop):
        """Rebuild arrows whenever line geometry changes while editing."""
        if self.finished or self.tracked_objects is None:
            return
        if obj not in self.tracked_objects:
            return
        self._sync_arrows()

    def check_finished(self):
        """Detect when the Draft command has been exited."""
        if self.finished:
            return
        # Draft Line normally opens a task panel/dialog while active.
        if Gui.Control.activeDialog():
            self._seen_dialog = True
            return
        # Finalize only after the dialog has appeared once and then closed.
        if self._seen_dialog:
            self._timer.stop()
            QtCore.QTimer.singleShot(0, self.finalize)
            return True

    def finalize(self):
        if self.finished:
            return
        self.finished = True
        
        if self.tracked_objects is not None:
            self._detach_arrows()
        
        try:
            self.callback(self.network_obj, self.tracked_objects)
        finally:
            # Always remove observer after one use
            FreeCAD.removeDocumentObserver(self)
            # Resume sync after creation is done and request sync
            if self.network_obj and hasattr(self.network_obj, "Proxy") and self.network_obj.Proxy:
                self.network_obj.Proxy.resumeSync(request_sync=True)
            # Switch back workbench to HVAC
            Gui.activateWorkbench(hvaclib.WORKBENCH_NAME)
                

class DuctDirectionEditSession:
    """
    Temporary edit mode for reversing base-object directions.

    Behaviour:
    - enable base direction arrows
    - hide generated geometry
    - suspend network sync
    - set reverse-direction cursor
    - selecting a base edge/object runs HVAC_ReverseGeometryDirection
    - on exit, restore visibility, arrows, sync, and cursor
    """

    def __init__(self, network_obj):
        self.network_obj = network_obj
        self.doc = getattr(network_obj, "Document", None)
        self.active = False
        self._busy = False
        self._old_show_arrows = None
        self._cursor_set = False

    def start(self):
        if self.active:
            return

        if self.network_obj is None or self.doc is None:
            return

        self.active = True

        # Make this network active.
        try:
            self.network_obj.Proxy.setActive()
        except Exception:
            pass

        # Suspend sync while directions are being edited.
        try:
            self.network_obj.Proxy.suspendSync()
        except Exception:
            pass

        # Hide generated duct geometry.
        try:
            self.network_obj.Proxy.hideAllGeometry()
        except Exception:
            pass

        # Enable base direction arrows.
        try:
            vobj = self.network_obj.ViewObject
            self._old_show_arrows = bool(vobj.ShowBaseDirectionArrows)
            vobj.ShowBaseDirectionArrows = True
            self.network_obj.ViewObject.Proxy.refreshBaseDirectionArrows()
        except Exception:
            pass

        # Change cursor to reverse command icon.
        self._setCursor()

        # Observe selection.
        Gui.Selection.addObserver(self)

        FreeCAD.Console.PrintMessage(
            "HVAC - Direction edit mode started. Select base edges/objects to reverse.\n"
        )

    def stop(self, request_sync=True):
        if not self.active:
            return

        self.active = False

        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass

        self._restoreCursor()

        # Disable or restore direction arrows.
        try:
            vobj = self.network_obj.ViewObject
            if self._old_show_arrows is not None:
                vobj.ShowBaseDirectionArrows = self._old_show_arrows
            else:
                vobj.ShowBaseDirectionArrows = False
            self.network_obj.ViewObject.Proxy.refreshBaseDirectionArrows()
        except Exception:
            pass

        # Show generated geometry again.
        try:
            self.network_obj.Proxy.showAllGeometry()
        except Exception:
            pass

        # Resume sync and request network update.
        try:
            self.network_obj.Proxy.resumeSync(request_sync=request_sync)
        except Exception:
            pass

        try:
            FreeCAD.ActiveDocument.recompute()
        except Exception:
            pass

        FreeCAD.Console.PrintMessage("HVAC - Direction edit mode closed.\n")

    # -------------------------------------------------
    # Selection observer API
    # -------------------------------------------------

    def addSelection(self, doc_name, obj_name, sub_name, point):
        if not self.active or self._busy:
            return

        doc = FreeCAD.getDocument(doc_name)
        if doc is None or doc != self.doc:
            return

        obj = doc.getObject(obj_name)
        if obj is None:
            return

        # Only act on base objects belonging to this network.
        if obj not in list(getattr(self.network_obj.Base, "OutList", []) or []):
            return

        if not (hvaclib.isSketch(obj) or hvaclib.isWire(obj)):
            return

        # For sketches, only reverse selected edges.
        # Ignore full sketch selection without EdgeN subelement.
        if hvaclib.isSketch(obj) and not str(sub_name or "").startswith("Edge"):
            return

        self._busy = True
        try:
            Gui.runCommand("HVAC_ReverseGeometryDirection")

            # Rebuild only direction arrows, not generated geometry.
            try:
                parser = self.network_obj.Proxy.getParser(
                    rebuild=True,
                    set_node_groups=False,
                )
                self.network_obj.ViewObject.Proxy.refreshBaseDirectionArrows(parser)
            except Exception:
                self.network_obj.ViewObject.Proxy.refreshBaseDirectionArrows()

            # Clear selection so the same edge can be clicked again.
            QtCore.QTimer.singleShot(0, Gui.Selection.clearSelection)

        finally:
            self._busy = False

    # -------------------------------------------------
    # Cursor handling
    # -------------------------------------------------

    def _setCursor(self):
        try:
            icon_path = hvaclib.get_icon_path("ReverseDirection.svg")
            pixmap = QtGui.QPixmap(icon_path)
            if not pixmap.isNull():
                cursor = QtGui.QCursor(pixmap.scaled(24, 24))
                QtWidgets.QApplication.setOverrideCursor(cursor)
                self._cursor_set = True
        except Exception:
            self._cursor_set = False

    def _restoreCursor(self):
        try:
            QtWidgets.QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self._cursor_set = False


class DuctNetworkChangeObserver:
    """
    Observe changes in base objects and resync owning duct networks.

    This observer monitors property changes in Sketches or Draft Wires that define
    the paths for HVAC duct networks. When a base geometry object is modified,
    the observer schedules a synchronization task to update the derived
    3D geometry of the corresponding DuctNetwork.
    """

    def __init__(self) -> None:
        self._scheduled: set[str] = set()
        self._undo_redo_in_progress: bool = False
        self._sync_in_progress: bool = False
        self.edit_observer = None
        
        self._edit_timer = QtCore.QTimer()
        self._edit_timer.setInterval(hvaclib.OBSERVER_TIMER_POLL_INTERVAL)
        self._edit_timer.timeout.connect(self._checkEditedBaseObject)
        self._edit_timer.start()
        
        self._edited_net = None
        self._edited_base_obj = None

    def slotChangedObject(self, obj: object, prop: str) -> None:
        """
        Callback triggered when an object property is changed.

        Checks if the modified object is used as a base for any HVAC duct network
        and schedules a sync if geometry-relevant properties were changed.

        Args:
            obj: The document object that was changed.
            prop: The name of the property that was modified.
        """
        if self._undo_redo_in_progress or self._sync_in_progress:
            return
            
        if obj is None or self._edited_net is None:
            return
        doc = getattr(obj, "Document", None)
        if doc is None:
            return

        # Ignore internal managed objects to avoid circular updates
        if hvaclib.isDuctNetwork(obj) or hvaclib.isDuctSegment(obj) or hvaclib.isDuctManagedFolder(obj):
            return

        # React only to properties relevant to geometry updates
        if hvaclib.isSketch(obj):
            relevant_props = ("Geometry", "Shape", "Placement")
        elif hvaclib.isWire(obj):
            relevant_props = ("Points", "Shape", "Placement")
        else:
            return

        if prop not in relevant_props:
            return

        for net in hvaclib.allHVACNetworks(doc):
            # If the modified object is part of the network's base geometry
            if obj in net.Base.OutList:
                if net.Name in self._scheduled:
                    continue

                self._scheduled.add(net.Name)
                # Schedule sync via a single-shot timer to ensure it runs after 
                # the current calculation cycle has finished.
                QtCore.QTimer.singleShot(0, lambda n=net: self._doSync(n))

    def slotUndoDocument(self, doc):
        self._undo_redo_in_progress = True
        QtCore.QTimer.singleShot(0, lambda d=doc: self._resyncAllNetworks(d))

    def slotRedoDocument(self, doc):
        self._undo_redo_in_progress = True
        QtCore.QTimer.singleShot(0, lambda d=doc: self._resyncAllNetworks(d))

    # Sync watcher
    
    def _doSync(self, net):
        if net is None:
            return

        self._scheduled.discard(net.Name)

        if getattr(net, "Document", None) is None:
            return
        if not hvaclib.isDuctNetwork(net):
            return
        
        proxy = getattr(net, "Proxy", None)
        if proxy is None:
            return
                    
        self._sync_in_progress = True
        try:
            proxy.requestSync()
        finally:
            self._sync_in_progress = False

    def _resyncAllNetworks(self, doc):
        try:
            if doc is None:
                return

            self._scheduled.clear()
            self._sync_in_progress = True

            for obj in doc.Objects:
                if hvaclib.isDuctNetwork(obj):
                    proxy = getattr(obj, "Proxy", None)
                    if proxy:
                        proxy.requestSync(initial_sync=True)
        finally:
            self._sync_in_progress = False
            self._undo_redo_in_progress = False
       
    # Visibility watcher
        
    def _finishEditedBaseObject(self):
        """
        Finalize the tracking state when a base geometry object exits edit mode.

        Resets internal references and notifies the parent network's proxy to
        restore normal segment visibility and perform a final synchronization.
        """
        net = self._edited_net
        obj = self._edited_base_obj
        self._edited_net = None
        self._edited_base_obj = None
        
        if net is None or obj is None:
            return

        proxy = getattr(net, "Proxy", None)
        if proxy:
            # Patch: turn off snapper for wire objects
            if hvaclib.isWire(obj):
                try:
                    if hasattr(Gui, "Snapper") and Gui.Snapper:
                        try:
                            Gui.Snapper.off()
                        except TypeError:
                            Gui.Snapper.off(False)
                        except Exception:
                            pass
                        try:
                            Gui.Snapper.hide()
                        except Exception:
                            pass
                except Exception:
                    pass
            
            proxy.setBaseObjectEditing(obj, False)            
            # Resume sync after editing is done and request sync
            if net and hasattr(net, "Proxy") and net.Proxy:
                net.Proxy.resumeSync(request_sync=True)
        
        # Reset sketch/ line observer
        self.edit_observer = None

    def _checkEditedBaseObject(self):
        """
        Monitor the active document to detect when base objects enter or exit edit mode.

        This method is called periodically via a timer to identify if a Sketch 
        or Draft Wire managed by an HVAC network is currently being edited. 
        It toggles the visibility of derived 3D geometry through the network 
        proxy to facilitate editing.
        """        
        if not FreeCAD.GuiUp or Gui.ActiveDocument is None:
            return

        # Query the current edited object
        in_edit = Gui.ActiveDocument.getInEdit()
        obj = getattr(in_edit, "Object", None) if in_edit else None

        # Check if the object type is relevant
        if not ( hvaclib.isSketch(obj) or hvaclib.isWire(obj) ):
            if self._edited_base_obj is not None:
                self._finishEditedBaseObject()
            return

        # Find the owning network
        net = hvaclib.getOwnerNetwork(obj)
        if net is None:
            if self._edited_base_obj is not None:
                self._finishEditedBaseObject()
            return

        # If the same object is still being edited
        if self._edited_net is net and self._edited_base_obj is obj:
            return

        # If editing switched to a different object
        if self._edited_base_obj is not None:
            self._finishEditedBaseObject()

        # Record the new editing state
        self._edited_net = net
        self._edited_base_obj = obj

        # Hide the geometry belonging to that base object
        proxy = getattr(net, "Proxy", None)
        if proxy:
            proxy.setBaseObjectEditing(obj, True)
            
        # Suspend sync to prevent transient sync requests while editing
        if net and hasattr(net, "Proxy") and net.Proxy:
            net.Proxy.suspendSync()
            
        # Setup and manage observers
        if not self.edit_observer:
            def callback(obj, sketch):
                pass
                
            if hvaclib.isSketch(obj):
                self.edit_observer = SketchObserver(self._edited_net, callback, edit_mode=True)
                self.edit_observer.set_modified_sketch(obj)
            elif hvaclib.isWire(obj):
                self.edit_observer = DraftLineObserver(self._edited_net, callback, edit_mode=True)
                self.edit_observer.set_modified_line(obj)
                
            FreeCAD.addDocumentObserver(self.edit_observer)
            self.edit_observer._sync_arrows()
            FreeCAD.ActiveDocument.recompute()


class TerminalFlowRateObserver:
    """
    Session-scoped 3D overlay, active while a Calculate Airflow / Size
    Ducts task panel is open: draws one colored flow-direction arrow plus
    one colored port plane at every terminal ("end" topology) junction in
    the network -- green if that terminal's DesignFlowRate is set
    (non-zero), red otherwise -- and lets a user click an arrow to set
    that terminal's DesignFlowRate from a dialog. Purely visual/
    interactive -- like buildArrowCoinNodes'/buildPortHighlightCoinNode's
    own overlays, this never touches the document or undo stack except
    for the one property write a user explicitly makes through the
    dialog.

    Both the arrow's length and its separation from the terminal scale
    with that terminal's own duct dimension (the larger of width/height,
    or diameter) -- length equal to that dimension, separation 20% of it
    -- rather than a fixed size, so it reads sensibly against ducts of any
    size. The port plane reuses buildPortHighlightCoinNode's own "2x port
    dimension, translated to the port's real (post-fitting) position"
    convention, exactly like TaskPanelEditInlineComponents' own port
    highlight.

    Clicking is handled with a plain Coin3D SoEventCallback + its own
    built-in pick, checked against each arrow's own SoSeparator -- these
    arrows are never real FreeCAD document objects, so FreeCAD's normal
    Gui.Selection mechanism can't see them on its own. Only arrows are
    pickable -- the port planes are decorative only.
    """

    FALLBACK_DIMENSION_MM = 100.0
    SEPARATION_FRACTION = 0.2
    COLOR_SET = (0.0, 0.7, 0.0)
    COLOR_UNSET = (0.85, 0.0, 0.0)
    TRANSPARENCY = 0.5

    def __init__(self, network_obj):
        self.network_obj = network_obj
        self._root = None
        self._event_callback_node = None
        self._arrows = []  # [(SoSeparator, junction_obj), ...] -- pickable
        self._planes = []  # [SoSeparator, ...] -- decorative only

    def start(self):
        vobj = getattr(self.network_obj, "ViewObject", None)
        if vobj is None:
            return
        self._root = coin.SoSeparator()
        self._event_callback_node = coin.SoEventCallback()
        self._event_callback_node.addEventCallback(
            coin.SoMouseButtonEvent.getClassTypeId(), self._onMouseClick
        )
        self._root.addChild(self._event_callback_node)
        vobj.RootNode.addChild(self._root)
        self.refresh()

    def stop(self):
        if self._root is not None:
            try:
                vobj = getattr(self.network_obj, "ViewObject", None)
                if vobj is not None:
                    vobj.RootNode.removeChild(self._root)
            except Exception:
                pass
        self._root = None
        self._event_callback_node = None
        self._arrows = []
        self._planes = []

    def refresh(self):
        """Rebuild every terminal's arrow/plane from the junctions' current DesignFlowRate/geometry."""
        if self._root is None:
            return
        # Child 0 is the event callback node -- keep it, drop only the arrows/planes.
        while self._root.getNumChildren() > 1:
            self._root.removeChild(self._root.getNumChildren() - 1)
        self._arrows = []
        self._planes = []

        for junction in self._terminalJunctions():
            self._buildOverlayForJunction(junction)

    def _terminalJunctions(self):
        geometry = getattr(self.network_obj, "Geometry", None)
        if geometry is None:
            return []
        return [
            obj for obj in geometry.OutList
            if hvaclib.isDuctJunction(obj) and getattr(obj, "Topology", "") == "end"
        ]

    def _buildOverlayForJunction(self, junction):
        center = getattr(junction, "CenterPoint", None)
        if center is None:
            return
        try:
            analysis = json.loads(getattr(junction, "AnalysisJson", "") or "{}")
        except Exception:
            analysis = {}
        ports = analysis.get("connected_ports") or []
        if not ports:
            return

        port = ports[0]
        direction = port.get("direction")
        flow_into_junction = port.get("flow_into_junction")
        if not direction or flow_into_junction is None:
            return

        width, height = hvaclib.get_section_extents(port.get("section_params", {}) or {})
        dimension = max(width, height) if (width > 0.0 and height > 0.0) else self.FALLBACK_DIMENSION_MM
        separation = dimension * self.SEPARATION_FRACTION

        design_flow_rate = float(getattr(junction, "DesignFlowRate", 0.0) or 0.0)
        color = self.COLOR_SET if design_flow_rate != 0.0 else self.COLOR_UNSET

        arrow_node = buildTerminalFlowArrowCoinNode(
            center, direction, bool(flow_into_junction), separation, dimension, color, self.TRANSPARENCY,
        )
        if arrow_node is not None:
            self._root.addChild(arrow_node)
            self._arrows.append((arrow_node, junction))

        plane_port = hvaclib.translated_port_position(junction, port)
        plane_node = buildPortHighlightCoinNode(plane_port, color=color, transparency=self.TRANSPARENCY)
        if plane_node is not None:
            self._root.addChild(plane_node)
            self._planes.append(plane_node)

    def _onMouseClick(self, user_data, event_callback):
        event = event_callback.getEvent()
        if not (
            isinstance(event, coin.SoMouseButtonEvent)
            and event.getButton() == coin.SoMouseButtonEvent.BUTTON1
        ):
            return

        picked = event_callback.getPickedPoint()
        if picked is None:
            return

        junction = self._junctionForPickedPath(picked.getPath())
        if junction is None:
            return

        # Consume both the press AND the release for this click -- opening
        # a modal dialog on DOWN means the matching BUTTON1 UP event
        # arrives as a separate callback invocation afterwards; leaving
        # that one unhandled let FreeCAD's own selection logic treat it as
        # a stray click and pop its Object/Face/Edge/Other picker menu.
        event_callback.setHandled()

        if event.getState() != coin.SoButtonEvent.DOWN:
            return
        self._openFlowRateDialog(junction)

    def _junctionForPickedPath(self, path):
        for arrow_node, junction in self._arrows:
            if path.containsNode(arrow_node):
                return junction
        return None

    def _openFlowRateDialog(self, junction):
        current = float(getattr(junction, "DesignFlowRate", 0.0) or 0.0)
        value, ok = QtWidgets.QInputDialog.getDouble(
            Gui.getMainWindow(),
            translate("HVAC", "Design Flow Rate"),
            translate("HVAC", "Design flow rate for '{}' (L/s):").format(junction.Label),
            current, 0.0, 1.0e6, 2,
        )
        if not ok:
            return
        if float(getattr(junction, "DesignFlowRate", 0.0) or 0.0) != value:
            junction.DesignFlowRate = value
            doc = getattr(junction, "Document", None)
            if doc is not None:
                doc.recompute()
        self.refresh()


def buildArrowCoinNodes(lines, size_scale=1.0):
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

    for sp, ep in lines:
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

def buildPortHighlightCoinNode(port, color=(0.2, 0.6, 1.0), transparency=0.5):
    """
    Build one Coin node: a semi-transparent quad marking a junction port's
    connection plane, centered on the port and sized to ~2x its own
    section extents. Used by TaskPanelEditInlineComponents so a user can
    see which physical port "Attach to edge" currently refers to, before
    committing to it -- and by TerminalFlowRateObserver for its own
    terminal-port plane, colored to match that terminal's flow arrow.

    port: a connected_ports-style dict (position, direction, profile_x_axis,
    section_params -- see NetworkParser.JunctionPort). `position` is taken
    as-is here -- the caller is responsible for translating it to the
    actual physical connection point (see TaskPanelEditInlineComponents.
    _highlightCurrentPort / hvaclib.translated_port_position()), since a
    raw connected_ports position is only the pre-fitting shared anchor
    point, not where a duct wall / existing inline device chain actually
    ends.
    """
    position = port.get("position") or (0.0, 0.0, 0.0)
    direction = port.get("direction") or (0.0, 0.0, 1.0)
    preferred_x = port.get("profile_x_axis")
    section_params = port.get("section_params", {}) or {}

    width, height = hvaclib.get_section_extents(section_params)
    if width <= 0.0 or height <= 0.0:
        # Section params missing/degenerate -- fall back to a fixed size
        # rather than drawing a zero-area (invisible) highlight.
        width = height = 100.0
    half_w = width  # half of 2x the full width
    half_h = height

    origin = FreeCAD.Vector(*position)
    _, x_dir, y_dir, _ = hvaclib.make_profile_frame(direction, preferred_x, origin)

    p1 = origin - x_dir * half_w - y_dir * half_h
    p2 = origin + x_dir * half_w - y_dir * half_h
    p3 = origin + x_dir * half_w + y_dir * half_h
    p4 = origin - x_dir * half_w + y_dir * half_h

    root = coin.SoSeparator()

    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*color)
    mat.transparency.setValue(transparency)
    root.addChild(mat)

    coords = coin.SoCoordinate3()
    coords.point.setValues(0, 4, [
        (p1.x, p1.y, p1.z),
        (p2.x, p2.y, p2.z),
        (p3.x, p3.y, p3.z),
        (p4.x, p4.y, p4.z),
    ])
    root.addChild(coords)

    face = coin.SoFaceSet()
    face.numVertices.setValue(4)
    root.addChild(face)

    return root

def buildTerminalFlowArrowCoinNode(
    center, direction, flow_into_junction, offset_mm, length_mm, color, transparency=0.0,
):
    """
    Build one Coin node: a colored cone-and-shaft arrow marking a terminal
    junction's design flow rate, offset into the open space beyond the
    terminal (not overlapping the duct itself) -- see
    TerminalFlowRateObserver.

    center: the junction's own CenterPoint.
    direction: the terminal's single real port's own direction (points
    away from the junction, into the duct network -- see
    NetworkParser.JunctionPort) -- the arrow is drawn on the opposite side,
    in the open space the terminal faces.
    flow_into_junction: True if flow enters the junction from the duct
    (a supply outlet -- air continues on outward past the terminal, into
    the room), False if flow leaves the junction into the duct (a return/
    extract inlet -- air is drawn in from the room). Standard HVAC
    drafting convention: a supply arrow points away from the duct (tail
    near, head far); a return arrow points toward the duct (head near,
    tail far).
    """
    away_dir = FreeCAD.Vector(direction) * -1.0
    if away_dir.Length <= 1e-9:
        return None
    away_dir.normalize()

    near_point = FreeCAD.Vector(center) + away_dir * offset_mm
    far_point = near_point + away_dir * length_mm

    head_len = length_mm * 0.5
    head_radius = head_len * 0.35
    shaft_radius = head_radius * 0.5

    if flow_into_junction:
        # Supply: arrow points away from the duct -- head at the far end.
        tip, tail, head_dir = far_point, near_point, away_dir
    else:
        # Return/extract: arrow points toward the duct -- head at the near end.
        tip, tail, head_dir = near_point, far_point, away_dir * -1.0

    cone_base_point = tip - head_dir * head_len
    cone_center = tip - head_dir * (head_len * 0.5)
    shaft_len = (cone_base_point - tail).Length
    shaft_center = tail + head_dir * (shaft_len * 0.5)

    # Coin SoCone/SoCylinder align to +Y by default -- rotate Y to head_dir.
    y_axis = FreeCAD.Vector(0, 1, 0)
    rot_axis = y_axis.cross(head_dir)
    dot = max(-1.0, min(1.0, y_axis.dot(head_dir)))
    if rot_axis.Length > 1e-9:
        rot_axis.normalize()
        rot_angle = math.acos(dot)
    elif dot > 0:
        rot_axis, rot_angle = FreeCAD.Vector(1, 0, 0), 0.0
    else:
        rot_axis, rot_angle = FreeCAD.Vector(1, 0, 0), math.pi

    def make_transform(point, rot_ax, angle):
        xf = coin.SoTransform()
        xf.translation.setValue(point.x, point.y, point.z)
        xf.rotation.setValue(coin.SbVec3f(rot_ax.x, rot_ax.y, rot_ax.z), angle)
        return xf

    root = coin.SoSeparator()

    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(*color)
    mat.transparency.setValue(transparency)
    root.addChild(mat)

    cone_sep = coin.SoSeparator()
    cone_sep.addChild(make_transform(cone_center, rot_axis, rot_angle))
    cone = coin.SoCone()
    cone.bottomRadius.setValue(head_radius)
    cone.height.setValue(head_len)
    cone_sep.addChild(cone)
    root.addChild(cone_sep)

    shaft_sep = coin.SoSeparator()
    shaft_sep.addChild(make_transform(shaft_center, rot_axis, rot_angle))
    cyl = coin.SoCylinder()
    cyl.radius.setValue(shaft_radius)
    cyl.height.setValue(shaft_len)
    shaft_sep.addChild(cyl)
    root.addChild(shaft_sep)

    return root

def iter_line_segments_from_shape(obj, tol=1e-9):
    """
    Yield per-edge path records for supported shape edges.

    Output tuple:
        (
            start_xyz,
            end_xyz,
            tag,
            path_json,
            start_dir_xyz,
            end_dir_xyz,
        )
    """
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return

    for slno, edge in enumerate(getattr(shape, "Edges", []) or []):
        curve = getattr(edge, "Curve", None)
        kind = hvaclib.GeomType(curve)
        if curve is None or kind == "Unknown":
            continue

        v1 = edge.Vertexes[0].Point
        v2 = edge.Vertexes[-1].Point
        if (v1.sub(v2)).Length <= tol:
            continue

        tag = hvaclib.makeLineKey(obj.Name, slno)

        yield (
            hvaclib.vec_to_xyz(v1),
            hvaclib.vec_to_xyz(v2)
        )
