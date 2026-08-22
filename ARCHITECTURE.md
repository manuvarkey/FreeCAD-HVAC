# Architecture / process flow

Short, practical map of how this addon actually runs — for developers and
for Claude picking up work in a fresh session. Rules for *how* to write code
here live in [`CLAUDE.md`](CLAUDE.md); this document is about how the
pieces fit together and execute. For the user-facing feature overview see
[`README.md`](README.md).

## The four layers

```
DuctNetwork              container: owns Base/Geometry/Topology folders, drives sync
     |
DuctNetworkParser         classifies: connectivity, topology, family, port profiles
     |
DuctSegment / DuctJunction + DuctComponent   generated objects: hold LibraryId/TypeId + geometry
     |
Library layer              JSON type-defs + HVACLibraryRegistry: what geometry to build
```

Each layer only talks to the one below it. Don't hardcode fitting-specific
logic upstream of where it belongs (e.g. no fitting knowledge in the
parser) — see `CLAUDE.md` §3 "Respect the layering".

A `DuctJunction` is a purely logical/connectivity node — it holds no
`LibraryId`/`TypeId`/`Shape` of its own. Each physical fitting at that node
is a separate `DuctComponent` child object: exactly one **Primary**
component (the automatically/manually selected main fitting), plus zero or
more user-added **Inline** components in series (a damper, a silencer, ...)
for a simple through/2-port node. See "Junction component composition"
below.

| Layer | File(s) | Job |
|---|---|---|
| Container | `freecad/HVAC/core/Network.py` (`DuctNetwork`) | Owns the network's Base/Geometry/Topology folders, network-wide defaults, and the sync loop that keeps everything below in step with the base geometry. |
| Classifier | `freecad/HVAC/core/NetworkParser.py` (`DuctNetworkParser`) | Reads base sketches/wires, builds a connectivity graph, and classifies each node's `topology`/`family_key` and each edge's port profile. Never picks a type, never knows about components. See [`core/TOPOLOGY_CLASSIFICATION.md`](freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md). |
| Generated objects | `freecad/HVAC/core/Segment.py`, `freecad/HVAC/core/Junction.py`, `freecad/HVAC/core/Component.py` | `DuctSegment`/`DuctComponent`: FreeCAD document objects that store `LibraryId`/`TypeId` and other metadata, and `execute()` by resolving that TypeId and asking the library layer to build geometry. `DuctJunction` itself holds no type/geometry — it composes its `DuctComponent` children's local ports each sync and aggregates their trim contributions. None of these classify or select a type themselves. |
| Library | `freecad/HVAC/library/Library.py`, `freecad/HVAC/library/validation.py`, `freecad/HVAC/utils/hvaclib.py` (`HVACLibraryService`), `freecad/HVAC/libraries/**/*.json` | Loads type-defs from JSON, decides *which* type-def matches a request, and dispatches to the geometry backend (PartScript / static BREP / legacy generator). See [`freecad/HVAC/libraries/README.md`](freecad/HVAC/libraries/README.md). |

## Core modules

One-line concept per module — read the module's own docstring for detail,
it's kept accurate and up to date on purpose.

| Module | Concept |
|---|---|
| `core/Network.py` | `DuctNetwork` container + the debounced sync loop (`requestSync`/`_runDeferredSync`) that keeps `DuctSegment`/`DuctJunction` objects in step with base geometry. |
| `core/NetworkParser.py` | `DuctNetworkParser`: builds a geometric graph from actual snapped endpoints, then an analysis graph on top (grouped "supernodes" for user-defined virtual junctions) that connectivity/degree/classification actually run on. |
| `core/Segment.py` / `core/Component.py` | `DuctSegment`/`DuctComponent`: FreeCAD document objects. `updateMetadata()` is a pure metadata writer (no selection logic); `execute()` does an exact type lookup and builds geometry. Both use the shared `core/_type_schema.apply_type_schema()` helper for their dynamic (type-declared) properties. See "Type selection subsystem" below. |
| `core/Junction.py` | `DuctJunction`: a logical node with no type/geometry of its own. `getComponents()`/`getPrimaryComponent()` find its `DuctComponent` children (via `ParentJunctionName`, in `Sequence` order); `composeComponents()` works out each child's local inlet/outlet ports for the current sync (a single-component junction gets the real connected ports unchanged); `aggregateConnectionLengths()` rolls each component's own trim into the external trim contract (`ConnectionLengthsJson`). See "Junction component composition" below. |
| `core/FlowNetwork.py` | Whole-network flow-distribution solve shared by AirflowSolver and DuctSizer. Assumes each connected sub-network is a tree with exactly one "balancing terminal" (no design flow given — e.g. the AHU/fan) and every other terminal carrying a user design flow rate; solves flow magnitude per segment by mass conservation from the leaves inward. Rejects loops with a clear error instead of guessing. |
| `core/AirflowSolver.py` | Given FlowNetwork's flow distribution and each segment's actual size: velocity, Reynolds number, and friction loss (Darcy-Weisbach + Altshul-Tsal friction factor) per segment; each junction's fitting/dynamic loss (pluggable per library type via `loss_module`/`loss_function`, generic fallback); static pressure propagated outward from the balancing terminal (0 Pa reference). |
| `core/DuctSizer.py` | Same flow distribution/boundary conditions as AirflowSolver, but solves for duct dimensions instead of pressure drop, per the network's `SizingMethod` (constant velocity / constant friction rate / static regain). Never mutates objects itself — `solve()` returns a preview (`DuctSizingResult`); `apply()` is a separate, explicit write step. |
| `core/airflow.py` | Pure-Python engineering formulas (areas, velocity, Reynolds number, friction factor, unit conversions). No FreeCAD dependency, strict SI units, unit-tested in isolation — see `CLAUDE.md`'s unit test policy. |
| `library/Library.py` | `HVACTypeDef`/`HVACLibrary`/`HVACLibraryRegistry`: type-def loading, per-library match indexes, `select_type`/`matches_type`/`resolve_sticky_type`, geometry-backend dispatch (`build_geometry`, normalized to a `GeometryResult` -- see `library/geometry_result.py`). |
| `library/validation.py` | Declarative property validation (`resolve_params`) and structural constraint checking (`context_violations`) — the same rules back both geometry execution and type matching. |
| `library/library_api.py` | `HVACLibraryAPI` — the only interface generator/PartScript authors should use: ports, geometry primitives, loss-orchestration helpers. |
| `utils/hvaclib.py` | `HVACLibraryService`: thin FreeCAD-facing facade over the registry (search paths, active library, segment/junction type resolution), plus misc FreeCAD object/geometry helpers used throughout `core/`. |

### Airflow & sizing flow

`HVAC_CalculateAirflow` and `HVAC_SizeDucts` share the same first step, then diverge:

```
FlowNetwork.solve_flow_components(net_obj)
   one FlowComponent per connected sub-network (must be a tree);
   flow magnitude per segment, solved leaf terminals -> balancing terminal
        |
        +----------------------------+-----------------------------+
        v                                                          v
   AirflowSolver                                              DuctSizer
   velocity / Reynolds / friction loss (airflow.py, from       velocity / friction-rate / static-regain sizing,
   each segment's existing size) + junction fitting loss       per the network's SizingMethod (constant-velocity
   (library loss_module, per type) + static pressure           and constant-friction-rate size each segment
   propagated outward from the balancing terminal (0 Pa)       independently; static regain walks outward from
                                                                 the balancing terminal since each section's
                                                                 target depends on its already-solved parent)
```

`DuctSizer.solve()` only returns a preview; it never writes to segments —
`apply()` is a separate, explicit step so the UI can show the preview and
let the user confirm first.

## End-to-end flow (normal edit → recompute)

1. User edits base routing geometry (a sketch or line-based object) inside a `DuctNetwork`.
2. `DuctNetwork.requestSync()` is scheduled (debounced via a `QTimer`) and calls `_runDeferredSync()`.
3. `DuctNetworkParser` rebuilds its graph from the base geometry and classifies every node/edge (topology, `family_key`, connected-port profiles).
4. `DuctNetwork.syncSegments` builds a match request from that classification and asks the library registry to resolve a type (see next section) — then writes the result onto the segment (`LibraryId`, `TypeId`, `Profile`, etc.) via `updateMetadata()`, a **pure metadata writer** with no selection logic of its own, and `applyTypeSchema()` adds/removes the FreeCAD properties the selected type declares.
5. `DuctNetwork.syncJunctions` writes each junction's own metadata (`NodeKey`, `CenterPoint`, `Degree`, `Topology`, `Family`, ...), then `syncJunctionComponents` creates/updates that junction's **Primary** `DuctComponent` (same sticky type-resolution policy a junction used to run directly), leaves any **Inline** components untouched (never auto-replaced), and calls `DuctJunction.composeComponents()` to write every component's local inlet/outlet ports for this sync — see "Junction component composition" below.
6. FreeCAD recompute calls `DuctSegment.execute()` / `DuctComponent.execute()`, which do an **exact** `resolve_type(LibraryId, TypeId)` lookup and call `HVACLibraryRegistry.build_geometry()` to produce a `GeometryResult` (see "Component geometry & materials" below), applied onto the object's `CasingShape`/`InsulationShape`/aggregate `Shape` by the shared `core/_geometry_apply.apply_geometry_result()` helper. `DuctJunction.aggregateConnectionLengths()` then rolls each component's own trim into the junction's `ConnectionLengthsJson` — the one external trim contract `syncSegments`'s next pass consumes to shorten the two real connected segments.
7. Optionally, `HVAC_CalculateAirflow` (`AirflowSolver`) and `HVAC_SizeDucts` (`DuctSizer`) run over the resulting network for pressure-drop and sizing results — see "Airflow & sizing flow" above.

## Junction component composition

For the common case — a junction with just its Primary component — this is
a no-op: the component simply gets the junction's real connected ports
unchanged, identical to how a single fitting worked before `DuctComponent`
existed. It's only interesting for a simple **through/2-port** node
carrying one or more **Inline** components in series with the Primary
(spec-scoped to this topology only — branch/cross/multiport/end nodes
always have exactly one component):

```
composeComponents()
   real inlet port (port_a) -- real outlet port (port_b)
        |
   Pass 1: for each component (Sequence order), work out its own local
           left/right port templates (direction/profile/edge_key -- all
           position-independent) and "peek" its own (trim_left, trim_right)
           by calling build_geometry once with a placeholder position
           (every 2-port geometry backend treats its two given ports as
           coincident and pushes each outward by a trim that depends only
           on its own properties/profile, never on where that shared point
           sits in space)
        |
   Pass 2: derive each component's real shared anchor point from a running
           sum of (previous component's own outward push + this
           component's own outward push) -- the first component's anchor
           is exactly port_a's own real position, so the upstream
           segment's trim is unaffected by how many components exist
        |
   Pass 3: write each component's final LocalPortsJson at its real anchor
```

Each component's `execute()` (run via `touch()` + the next recompute) then
calls `build_geometry` a second time with those final positions to build
the real `Shape` — `build_geometry` runs twice per component per sync by
design, so `execute()` stays the single source of truth for `Shape` rather
than caching a result across the sync/recompute boundary.

`AirflowSolver`'s Phase E mirrors this: for a through/2-port chain, each
component's own loss is evaluated against its own local ports/velocity and
converted to Pa immediately (never summing raw K values across components
that don't share a reference velocity), then the Pa contributions are
summed once onto the one real segment leaving the junction. Per-component
results (`CalcFlowRate`/`CalcVelocity`/`CalcLossCoefficient`/
`CalcPressureDrop`) are stored on each `DuctComponent`. Everything else
(branch/cross/multiport/end nodes, `DuctSizer`'s static-regain branch-loss
pass) is untouched — those always have exactly one component, so behave
identically to before this split.

`DuctJunction` has no `Shape` and so can't be picked in the 3D view — its
own `DesignFlowRate` (the terminal solve target `FlowNetwork`/`AirflowSolver`
read directly off the junction) is therefore also mirrored onto its Primary
`DuctComponent`'s own `DesignFlowRate` property, via a two-way `onChanged`
hook on both `Junction.py` and `Component.py` (each guarded by an
`_mirroring_design_flow_rate` flag so the two handlers don't bounce an edit
back and forth). The mirror is editable only on a Primary component whose
parent is an `"end"` (terminal) node — hidden (editor mode 2) everywhere
else — and is also pulled down from the parent every sync
(`DuctComponent.execute()`'s `_syncDesignFlowRate`), so a document reopen or
a topology change always leaves it consistent, not just a live edit.

## Component geometry & materials

Every `DuctSegment`/`DuctComponent` is one physical HVAC element, but that
element can be made of more than one solid -- a sheet-metal casing, and
optionally an insulation wrap. `HVACLibraryRegistry.build_geometry()`
normalizes whatever a geometry backend returned (see
`library/geometry_result.py`) into a `GeometryResult`:

```
GeometryResult
   components: {"casing": ComponentGeometry, "insulation": ComponentGeometry, ...}
               -- always has "casing" and "insulation" keys; a component's
               .shape is None when that piece has no geometry (e.g.
               insulation disabled for that type)
   connection_lengths / computed_properties / start_trim_plane_json / ...
               -- the same non-shape outputs generators have always returned
```

Generator/PartScript/static-descriptor authors never import
`GeometryResult`/`ComponentGeometry` directly -- per the "external code uses
only `HVACLibraryAPI`" rule, they keep returning plain dicts: either the
legacy `{"shape": ...}` form (every shipped type that doesn't model
insulation), or `{"components": {"casing": {"shape": ...}, "insulation":
{"shape": ...}}}` for a type that does. `normalize()` accepts both.

`core/_geometry_apply.apply_geometry_result()` is the one place
`DuctSegment.execute()`/`DuctComponent.execute()` share: it writes
`CasingShape`/`InsulationShape` (`Part::PropertyPartShape`, read-only in the
property editor) from `result.components`, then derives the object's own
`Shape` as `Part.makeCompound()` of whichever of those two actually have a
shape, in that fixed casing-then-insulation order. `Shape` is only ever this
derived aggregate -- nothing downstream recovers casing/insulation meaning
by inspecting its faces/solids.

**FreeCAD-HVAC uses FreeCAD's native `Materials::PropertyMaterial` and
`.FCMat` database. HVAC supplies only domain-specific material cards;
FreeCAD's Material subsystem owns material storage, selection, physical
properties and appearance.** `CasingMaterial`/`InsulationMaterial`
(`Materials::PropertyMaterial`) hold a native FreeCAD material value
directly -- not a link to a per-object document object -- so the same
database material (built-in, this addon's own, from another addon, or
user-defined) can be assigned to any number of duct objects without
duplication, exactly like assigning a material anywhere else in FreeCAD.
There are no HVAC-specific color/transparency properties: appearance always
comes from the assigned material's own `AppearanceModels`. Both properties
are added with `Prop_NoRecompute` -- picking a material never changes the
object's own geometry, only its ViewProvider's rendered appearance, so it
shouldn't force a recompute.

`freecad/HVAC/Resources/Materials/` ships a handful of HVAC-domain `.FCMat`
cards -- casing metals (galvanized steel, aluminium, stainless steel) and
insulation (glass wool, rock wool, nitrile rubber, polyurethane foam,
expanded polystyrene) -- built entirely from FreeCAD's own standard models
(`Father`, `Density`, `Thermal`, `BasicRendering`) -- no HVAC-specific
material schema. Every insulation card's `BasicRendering.Transparency` is
`0.6` so a duct's base casing stays visible through its insulation wrap
while modeling; metal casing cards are opaque (`0.0`).
`utils/materials.register_material_resources()` (called once from
`init_gui.py`) registers that folder with FreeCAD's Material subsystem the
same way FreeCAD's own Supplemental-Materials addon does (a `ModuleDir` key
under `.../Mod/Material/Resources/Modules/FreeCAD-HVAC`), so these cards
show up in the normal material browser/editor next to every other material
FreeCAD knows about -- there is no separate HVAC material dropdown.
`utils/materials.get_physical_value()`/`get_view_appearance()` are the only
two ways core/ code reads a `Materials::PropertyMaterial` value: the first
for a future quantity calculation (volume x density -> mass, from
`CasingShape`/`InsulationShape` + `CasingMaterial`/`InsulationMaterial`),
the second to build the plain `FreeCAD.Material()` struct
`ViewObject.ShapeAppearance` actually consumes, from the native material's
own appearance -- a one-way, read-only conversion; nothing is written back
onto the material, and nothing is cached onto the HVAC object itself.
Construction parameters like `InsulationThickness` stay separate, plain
type properties -- never part of material identity, so the same Glass Wool
material works at any thickness.

`core/_component_appearance.py` renders the two materials: since `Shape` is
always the compound built in fixed casing-then-insulation order,
`len(CasingShape.Faces)` tells the ViewProvider exactly where the casing's
own faces end in that compound (an exact count derived from the very two
shapes the compound was built from, never a hardcoded/guessed split), so it
can assign a per-face `ViewObject.ShapeAppearance` array built from each
material's own converted appearance -- no custom Coin scene graph needed.
It guards against a real FreeCAD re-entrancy quirk (querying a material's
own appearance can synchronously re-fire `updateData()` for that same
property before the original call returns, which would otherwise recurse
until the interpreter's stack limit crashes it) -- see the module's own
`_rendering` guard and its comment before touching that function.

FreeCAD's generic property editor has no interactive picker for
`Materials::PropertyMaterial` on an arbitrary object (confirmed: no shipped
FreeCAD workbench relies on inline editing for it either -- CAM's own
"Assign Material" feature builds its own dialog the same way). Materials are
assigned via one command, `HVAC_EditMaterial` (`ui/Command.py`), which opens
`ui/TaskPanel.py:TaskPanelEditMaterial` -- a single panel with a
`MaterialPickerRow` for each of Casing/Insulation, so both properties are
edited together rather than through two separate commands. Each row's
"Browse..." button opens a `MaterialPickerDialog` built from FreeCAD's own
`MatGui::MaterialTreeWidget` -- the same native browser widget the Material
workbench and CAM use. A row only reports a material back to
`Network.applyMaterialSelection()` if the user actually picked one
(`MaterialPickerRow.touched`) -- leaving a row alone (e.g. only changing
Insulation across a selection with mixed Casing materials) never clobbers
the other property with whatever the first selected object happened to
show. `MatGui` (the Gui module that implements the tree widget) is imported
once, at `ui/TaskPanel.py` module scope, since it isn't loaded automatically
just by activating the HVAC workbench.

`DuctNetwork` carries the same picker (embedded in
`TaskPanelNetworkTypeDefaults`, the "Network Defaults" command) for
`DefaultCasingMaterial`/`DefaultInsulationMaterial` -- defaulted to this
addon's own Galvanized Steel/Nitrile Rubber cards
(`utils/materials.GALVANIZED_STEEL_UUID`/`NITRILE_RUBBER_UUID`) the first
time a network is created -- plus a `DefaultInsulationThickness` alongside
the existing `DefaultDiameter`/`DefaultWidth`/`DefaultHeight`.
`DuctSegment.applyOwnerDefaults()`/`DuctComponent.applyOwnerDefaults()` copy
these onto a newly-created segment/component whenever it doesn't already
have its own value (never overwrites a manual choice or a value restored
from an existing document) -- so every new duct object is fully materialed
out of the box without the user having to visit `HVAC_EditMaterial` for it.
`DuctNetwork.resetObjectsToNetworkDefaults()` (the "Reset to Defaults"
command) is the opposite convention -- like it already does for
`LibraryId`/`TypeId`, an explicit reset always re-applies the network's
*current* default materials, discarding whatever `CasingMaterial`/
`InsulationMaterial` the object already had.

`DuctJunction` itself stays geometry-free: it never gets `CasingShape`/
`InsulationShape`/materials of its own, only its `DuctComponent` children do.

`components` is intentionally an open dict, not hardcoded to exactly two
entries -- a future role (`lining`, `coating`, `flange`, `access_panel`, ...)
can appear in it without changing the generator API, though only `casing`
and `insulation` have first-class FreeCAD properties today.

## Type selection subsystem

This is the part most likely to need touching when adding a new fitting
type or debugging "why did it pick that geometry." Full detail:
[`freecad/HVAC/libraries/README.md`](freecad/HVAC/libraries/README.md) §"Type
selection (automatic matching)".

```
classifier output (topology, family_key, profile)
        |
HVACTypeMatchRequest
        |
HVACLibraryRegistry.resolve_sticky_type(library_id, current_type_id, request)
        |-- current TypeId still a valid real model? -> keep it (sticky)
        |-- else -> select_type(): best model, else best placeholder
        v
LibraryId / TypeId written onto the object (a segment, or a junction's
                                             Primary DuctComponent)
        v
execute() -> resolve_type() (exact lookup, no matching) -> build_geometry()
```

For a junction this whole flow runs against its **Primary** `DuctComponent`
only (`DuctNetwork.syncJunctionComponents`) — a junction itself is never a
`resolve_sticky_type`/`select_type` target any more. **Inline** components
are never reachable through this automatic flow at all: their type-defs
declare `selection: {kind: "inline"}`, which excludes them from both the
model and placeholder match indexes entirely (`HVACLibrary.
_rebuild_match_index`), so they're only reachable via `HVACLibrary.
list_inline_types()` (the "Edit Inline Components" UI action's Add section).

Key rules (all enforced in `freecad/HVAC/library/Library.py`):
- A type-def opts in via `family` (classifier keys it supports), `profiles`
  (`"Generic"` = wildcard), and `selection: {kind, priority}`.
- `kind: "model"` beats `kind: "placeholder"`; exact profile beats
  `"Generic"` profile; `priority` only breaks ties inside the same tier.
  `kind: "inline"` never participates in automatic matching at all.
- A manually-picked, still-valid `model` type is **sticky** — it survives
  resync even if a higher-priority alternative exists. A `placeholder` is
  **never** sticky, so it can auto-upgrade once a real model qualifies.
- Adding a new fitting = a new JSON type-def + geometry backend. No new
  `if/elif` in `Network.py`/`Junction.py`/`Component.py`/`hvaclib.py`.

## Where to look for more

- **How to write code here** (test policy, naming convention, layering
  rules, SPDX header) → [`CLAUDE.md`](CLAUDE.md).
- **User-facing feature overview, design goals, status** → [`README.md`](README.md).
- **Library/type-def JSON schema, naming convention, type-selection
  details** → [`freecad/HVAC/libraries/README.md`](freecad/HVAC/libraries/README.md).
- **How the parser classifies topology/family** → [`freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md`](freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md).
- **Choosing a geometry backend (PartScript / static / generator)** → [`freecad/HVAC/libraries/samples/README.md`](freecad/HVAC/libraries/samples/README.md).
- **Public API for generator/PartScript authors** → `freecad/HVAC/library/library_api.py` (`HVACLibraryAPI`) — external/library code should only use this, not internal HVAC modules directly.
