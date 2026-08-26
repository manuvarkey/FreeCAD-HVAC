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
| `core/FlowNetwork.py` | Thin FreeCAD adapter: builds a `NetworkModel` from a `DuctNetwork` (via `core/_analysis_adapter.py`) and calls `analysis/flow.py`'s pure solve — see "Analysis layer" below for the actual algorithm. Shared by AirflowSolver and DuctSizer. |
| `core/AirflowSolver.py` | Thin FreeCAD adapter over `analysis/pressure.py`'s pure `PressureSolver`: builds the network model, solves it, writes the results back onto segment/junction/component `Calc*` properties, and returns the same `SegmentResult`/`JunctionResult`/`ComponentResult`/`AirflowSolveResult` shape it always has. |
| `core/DuctSizer.py` | Thin FreeCAD adapter over `analysis/sizing.py`/`analysis/balancing.py`: picks the sizing strategy for the network's `SizingMethod`, maps the pure result back onto `SegmentSizeResult`/`DuctSizingResult`. Never mutates objects itself — `solve()` returns a preview; `apply()` is a separate, explicit write step. |
| `core/_analysis_adapter.py` | The one place a real `DuctNetwork` gets read into `analysis/model.py` dataclasses: `build_network_model()`, and `build_loss_evaluator()` (resolves a component's library type once and returns a plain callable closure — the pure layer never resolves a type itself). |
| `core/Numbering.py` | `renumber_network()`: presentation-only D.../J.../J...-P/J...-NN documentation numbering, walked outward from a deterministic source terminal over the parser's analysis graph. Only ever runs when `HVAC_RenumberNetwork` is invoked — normal sync never renumbers. |
| `library/Library.py` | `HVACTypeDef`/`HVACLibrary`/`HVACLibraryRegistry`: type-def loading, per-library match indexes, `select_type`/`matches_type`/`resolve_sticky_type`, geometry-backend dispatch (`build_geometry`, normalized to a `GeometryResult` -- see `library/geometry_result.py`). |
| `library/validation.py` | Declarative property validation (`resolve_params`) and structural constraint checking (`context_violations`) — the same rules back both geometry execution and type matching. |
| `library/library_api.py` | `HVACLibraryAPI` — the geometry interface generator/PartScript authors receive as `context["hvac_api"]`: basic context/port helpers, basic geometry, then HVAC convenience functions. |
| `library/loss_api.py` | `HVACLossAPI` — fitting-loss orchestration exposed to library loss modules as `context["loss_api"]`; it converts library context into inputs for the pure loss tables. |
| `utils/hvaclib.py` | `HVACLibraryService`: thin FreeCAD-facing facade over the registry (search paths, active library, segment/junction type resolution), plus misc FreeCAD object/geometry helpers used throughout `core/`. |

## Analysis layer

`freecad/HVAC/analysis/` is a pure-Python engineering domain package: **nothing
in it may import FreeCAD, `Part`, `pivy`, `core/`, `library/`, or
`utils/hvaclib`** — only `math`, `dataclasses`, `typing`, and `networkx`
(vendored under `HVAC/ext_libs`, loaded the same way `utils/hvaclib.py` does
but without depending on that module). This is what lets
`tests/test_physics.py`/`tests/test_analysis_*.py` run with no FreeCAD stub
of any kind, unlike every other test in the suite.

```
DuctNetwork (FreeCAD)
     |
core/_analysis_adapter.py    reads the document into pure dataclasses,
     |                       resolves library loss formulas into plain callables
     v
analysis/model.py            NetworkModel / NodeModel / SegmentModel / ComponentModel /
                              PortModel / AirState / SizingSettings -- stable string
                              ids (edge_key / node_id) relate everything back to a
                              real FreeCAD object, never a direct reference
     |
analysis/physics.py          pure formulas (areas, velocity, Reynolds, friction,
     |                       sizing-for-velocity/friction-rate/static-regain)
     v
analysis/flow.py             tree/loop check + mass-conservation flow solve
     |
     +-- analysis/pressure.py   velocity/friction/fitting loss + static pressure
     |        |
     |        v
     |   analysis/paths.py      per-terminal FlowPathResult + CriticalPathResult
     |                          (pressure_deficit_pa relative to the critical path)
     |
     +-- analysis/sizing.py     ConstantVelocitySizer / ConstantFrictionRateSizer /
              |                 LocalStaticRegainSizer (today's "StaticRegain" algorithm,
              |                 unchanged in meaning)
              v
         analysis/balancing.py  PressureBalanceCoordinator: iterates a base sizer +
                                 pressure/path re-solves, shrinking the most-upstream
                                 segment unique to whichever terminal's path has the
                                 largest pressure_deficit_pa, until every path is
                                 within tolerance or a segment can't shrink further --
                                 any deficit left over becomes an explicit
                                 BalancingRequirement (junction_id/branch_port/
                                 pressure_deficit_pa/required_k), with no damper
                                 FreeCAD object required to exist
     |
     v
core/AirflowSolver.py / core/DuctSizer.py   map pure results back onto Calc*
                                             properties / SegmentSizeResult.obj
```

A component's own fitting-loss formula is never resolved by `analysis/`
itself: `core/_analysis_adapter.build_loss_evaluator()` resolves the library
type once and returns a plain `Callable[[port_velocities], K]` closure
(`ComponentModel.loss_evaluator`) that `analysis/pressure.py`/`sizing.py`
call with nothing but plain floats — the same 3-shape return contract
(`dict`/`float`/`None`) `HVACLibraryRegistry.call_loss` has always had. Each
component's own loss is still converted to Pa **using its own reference
velocity before being summed** onto a segment — `SegmentModel` keeps a
node's own Primary contribution (`junction_loss_pa`) and an edge's own
Inline-chain contribution (`component_loss_pa`) as two separate fields
(rather than one combined "fitting loss") specifically so `analysis/paths.py`
can report duct/junction/component/terminal loss separately along a path.

`DuctNetwork.SizingMethod` has a fourth option, `PressureBalancedStaticRegain`
(`ui/TaskPanel.py`'s Size Ducts panel), which routes through
`PressureBalanceCoordinator` instead of `LocalStaticRegainSizer` directly —
any `BalancingRequirement` the coordinator can't resolve by sizing alone is
surfaced both as a plain warning in the existing Size Ducts warnings box and
as structured data on the result (`DuctSizingResult.balancing_requirements`)
for anything that wants to act on it later (e.g. flagging where a real
damper should go). The coordinator is deliberately written against a
`base_sizer` it doesn't otherwise know about, so a future Equal-Friction or
Constant-Velocity sizing method could reuse the same balancing layer without
changes to `analysis/balancing.py` itself.

### Airflow & sizing flow

`HVAC_CalculateAirflow` and `HVAC_SizeDucts` share the same first step, then diverge:

```
FlowNetwork.solve_flow_components(net_obj)
   adapter: builds a NetworkModel, then analysis/flow.py solves it --
   one FlowComponent per connected sub-network (must be a tree);
   flow magnitude per segment, solved leaf terminals -> balancing terminal
        |
        +----------------------------+-----------------------------+
        v                                                          v
   AirflowSolver                                              DuctSizer
   adapter over analysis/pressure.py's PressureSolver:         adapter over analysis/sizing.py / balancing.py:
   velocity / Reynolds / friction loss, junction/component     picks ConstantVelocitySizer / ConstantFrictionRateSizer /
   fitting loss (via each node's own loss_evaluator             LocalStaticRegainSizer / PressureBalanceCoordinator per
   closure), static pressure propagated outward from the       the network's SizingMethod -- static regain (plain or
   balancing terminal (0 Pa) -- plus per-terminal               pressure-balanced) walks outward from the balancing
   FlowPathResult/CriticalPathResult (analysis/paths.py)        terminal since each section's target depends on its
                                                                 already-solved parent
```

See "Analysis layer" above for what actually happens inside `analysis/flow.py`/`pressure.py`/`sizing.py`/`paths.py`/`balancing.py` -- this section is only about which FreeCAD command triggers which adapter.

`DuctSizer.solve()` only returns a preview; it never writes to segments —
`apply()` is a separate, explicit step so the UI can show the preview and
let the user confirm first.

## End-to-end flow (normal edit → recompute)

1. User edits base routing geometry (a sketch or line-based object) inside a `DuctNetwork`.
2. `DuctNetwork.requestSync()` is scheduled (debounced via a `QTimer`) and calls `_runDeferredSync()`.
3. `DuctNetworkParser` rebuilds its graph from the base geometry and classifies every node/edge (topology, `family_key`, connected-port profiles).
4. `DuctNetwork.syncSegments` builds a match request from that classification and asks the library registry to resolve a type (see next section) — then writes the result onto the segment (`LibraryId`, `TypeId`, `Profile`, etc.) via `updateMetadata()`, a **pure metadata writer** with no selection logic of its own, and `applyTypeSchema()` adds/removes the FreeCAD properties the selected type declares.
5. `DuctNetwork.syncJunctions` writes each junction's own metadata (`NodeKey`, `CenterPoint`, `Degree`, `Topology`, `Family`, ...), then `syncJunctionComponents` creates/updates that junction's **Primary** `DuctComponent` (same sticky type-resolution policy a junction used to run directly), leaves any **Inline** components untouched (never auto-replaced), and calls `DuctJunction.composeComponents()` to write every component's local inlet/outlet ports for this sync — see "Junction component composition" below.
6. FreeCAD recompute calls `DuctSegment.execute()` / `DuctComponent.execute()`, which do an **exact** `resolve_type(LibraryId, TypeId)` lookup and call `HVACLibraryRegistry.build_geometry()` to produce a `GeometryResult` (see "Component geometry & materials" below), applied onto each of the object's own construction layers' `Layer_<id>_Shape`/aggregate `Shape` by the shared `core/_geometry_apply.apply_geometry_result()` helper. `DuctJunction.aggregateConnectionLengths()` then rolls each component's own trim into the junction's `ConnectionLengthsJson` — the one external trim contract `syncSegments`'s next pass consumes to shorten the two real connected segments.
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
element can be made of any number of physical **construction layers** -- a
bare sheet-metal wall, a wall plus insulation, or a casing plus an acoustic
fill plus a perforated liner. How many layers a type has, what each is
called, and how they're built is entirely **library-defined data** (a
type-def's own `"construction"` block -- see
`freecad/HVAC/libraries/README.md`'s "Construction layers" section); core
only owns the standardized semantic **role** vocabulary
(`library/construction.py`'s `LayerRole`: `flow_surface`,
`structural_shell`, `thermal_insulation`, `acoustic_absorber`,
`acoustic_liner`, `vapor_barrier`, `outer_jacket`, `fire_protection`) and
the generic machinery that composes/queries whatever layers a type
declares. Downstream code must only ever branch on a layer's roles, never
on its library-chosen id.

```
library/construction.py     LayerRole vocabulary + ConstructionLayerDef
                             (a type-def's own declared layer: id, roles,
                             default_material_role/uuid, thickness_property)
                             + LayerGeometry (one layer's built shape+roles)
        |
library/geometry_result.py  GeometryResult.layers: dict[layer_id, LayerGeometry]
        |
library/Library.py          HVACTypeDef.construction: list[ConstructionLayerDef],
                             parsed from the type-def's "construction" JSON;
                             build_geometry() stamps each returned layer's
                             roles from there after normalize()
        |
library/library_api.py      HVACLibraryAPI profiles, offsets, sweeps, lofts,
                             and boolean operations -- the shared primitives
                             used by backends to construct each declared
                             layer without importing Part directly
        |
core/_construction_schema.py   apply_construction_schema() -- adds/removes
                                each layer's own Layer_<id>_Shape/
                                Layer_<id>_Material FreeCAD properties to
                                match the selected type, mirroring
                                core/_type_schema.py's dynamic-property
                                pattern; writes the declared order onto
                                obj.ConstructionLayerIds
        |
core/_geometry_apply.py     writes each declared layer's own Layer_<id>_Shape
                             from GeometryResult.layers, then derives the
                             object's own Shape as Part.makeCompound() of
                             every non-null layer shape, in
                             ConstructionLayerIds order
        |
core/Construction.py        the semantic query API: Construction(obj)
                             .layers_with_role(role) / .flow_surface() /
                             .structural_layers() / .thermal_layers() /
                             .acoustic_layers() / .hydraulic_roughness() /
                             .acoustic_impedance() / .overall_u_value() --
                             reachable off a segment/component's own
                             getConstruction()
```

`HVACLibraryRegistry.build_geometry()` normalizes whatever a geometry
backend returned into a `GeometryResult`:

```
GeometryResult
   layers: dict[str, LayerGeometry]  -- arbitrary library-defined layer ids;
               no required keys or count. Each LayerGeometry carries its own
               .shape (None if that layer has no geometry this call) and
               .roles (stamped on by build_geometry() from the type-def's
               own "construction" block -- empty for a not-yet-migrated
               type with no construction block at all).
   connection_lengths / computed_properties / start_trim_plane_json / ...
               -- the same non-shape outputs generators have always returned
```

Generator/PartScript/static-descriptor authors never import
`GeometryResult`/`LayerGeometry` directly. Geometry code uses
`HVACLibraryAPI`, while loss modules use `HVACLossAPI`; both keep returning
plain values instead of internal library classes. Geometry backends return either the
legacy `{"shape": ...}` form (a type with just one, roleless implicit
layer, id `"shape"`), or `{"layers": {"<id>": {"shape": ...}, ...}}` for a
type with more than one declared layer. `normalize()` accepts both.

`core/_geometry_apply.apply_geometry_result()` is the one place
`DuctSegment.execute()`/`DuctComponent.execute()` share: it writes each of
`obj.ConstructionLayerIds`' own `Layer_<id>_Shape`
(`Part::PropertyPartShape`, read-only in the property editor) from
`result.layers`, then derives the object's own `Shape` as
`Part.makeCompound()` of whichever layers actually have a shape, in that
same declared order. `Shape` is only ever this derived aggregate --
nothing downstream recovers a layer's meaning by inspecting its
faces/solids; that's what `core/Construction.py`'s role queries are for.

**FreeCAD-HVAC uses FreeCAD's native `Materials::PropertyMaterial` and
`.FCMat` database. HVAC supplies only domain-specific material cards;
FreeCAD's Material subsystem owns material storage, selection, physical
properties and appearance.** Each construction layer's own
`Layer_<id>_Material` (`Materials::PropertyMaterial`) holds a native
FreeCAD material value directly -- not a link to a per-object document
object -- so the same database material (built-in, this addon's own, from
another addon, or user-defined) can be assigned to any number of duct
objects without duplication, exactly like assigning a material anywhere
else in FreeCAD. There are no HVAC-specific color/transparency properties:
appearance always comes from the assigned material's own
`AppearanceModels`. Every `Layer_<id>_Material` is added with
`Prop_NoRecompute` -- picking a material never changes the object's own
geometry, only its ViewProvider's rendered appearance, so it shouldn't
force a recompute.

`freecad/HVAC/Resources/Materials/` ships HVAC-domain `.FCMat` cards for
metal casing, rigid nonmetallic casing, flexible duct, and insulation.
They reuse FreeCAD's standard `Father`, `Density`, `Thermal`, and
`BasicRendering` models. Every card also uses the addon's small native
`Hydraulic` material model, declared under
`Resources/Models/`, so effective absolute roughness is a native,
unit-aware material property. Every insulation card's
`BasicRendering.Transparency` is `0.6` so an inner layer stays visible
through an outer wrap while modeling; metal casing cards are opaque
(`0.0`).
`utils/materials.register_material_resources()` (called once from
`init_gui.py`) registers that folder with FreeCAD's Material subsystem the
same way FreeCAD's own Supplemental-Materials addon does (a `ModuleDir` key
and a `ModuleModelDir` key under
`.../Mod/Material/Resources/Modules/FreeCAD-HVAC`), so these cards and the
Hydraulic model show up in the normal material browser/editor next to every
other material FreeCAD knows about -- there is no separate HVAC material
dropdown.
The read-only helpers in `utils/materials.py` are the only way core/ code
reads a `Materials::PropertyMaterial` value: physical-value helpers serve
roughness and future quantity calculations (volume x density -> mass, from a
layer's own `Layer_<id>_Shape` + `Layer_<id>_Material`), while the
appearance helper builds the plain `FreeCAD.Material()` struct
`ViewObject.ShapeAppearance` actually consumes from the native material's
own appearance. That conversion is one-way and read-only; nothing is
written back onto the material or cached onto the HVAC object itself.
Construction parameters
like a layer's own thickness stay separate, plain type properties -- never
part of material identity, so the same Glass Wool material works at any
thickness (a `ConstructionLayerDef.thickness_property`, if declared, names
which one -- purely informational metadata, since a layer's generated
Shape is always the source of truth for its own volume).

Airflow friction follows the same role-based construction contract.
`core/_analysis_adapter.py` asks each segment's own `Construction` for the
`flow_surface()` layer and uses its material's effective
`HydraulicRoughness`; the network's `DefaultRoughness` is used only when
that construction has no usable value. Primary and inline components are
resolved independently and receive their own construction and roughness in
the library loss context. Components have no fictitious straight length,
so their roughness only affects a loss formula that explicitly uses it.
Shipped metal cards declare the native property directly; cards without
that property use the network fallback rather than a name- or UUID-based
implicit material value.

`core/_component_appearance.py` renders every layer's own material: since
`Shape` is always the compound built in `ConstructionLayerIds` order,
`len(Layer_<id>_Shape.Faces)` for each layer in that same order tells the
ViewProvider exactly where each layer's own faces fall in that compound
(an exact count derived from the very shapes the compound was built from,
never a hardcoded/guessed split), so it can assign a per-face
`ViewObject.ShapeAppearance` array built from each layer's own converted
material appearance -- no custom Coin scene graph needed. It guards
against a real FreeCAD re-entrancy quirk (querying a material's own
appearance can synchronously re-fire `updateData()` for that same property
before the original call returns, which would otherwise recurse until the
interpreter's stack limit crashes it) -- see the module's own `_rendering`
guard and its comment before touching that function.

FreeCAD's generic property editor has no interactive picker for
`Materials::PropertyMaterial` on an arbitrary object (confirmed: no shipped
FreeCAD workbench relies on inline editing for it either -- CAM's own
"Assign Material" feature builds its own dialog the same way). Materials are
assigned via one command, `HVAC_EditMaterial` (`ui/Command.py`), which opens
`ui/TaskPanel.py:TaskPanelEditMaterial` -- one panel with a
`MaterialPickerRow` per construction layer id present on the selection
(the union across a mixed selection of different types), so every layer is
edited together rather than through a separate command per layer. Each
row's "Browse..." button opens a `MaterialPickerDialog` built from
FreeCAD's own `MatGui::MaterialTreeWidget` -- the same native browser
widget the Material workbench and CAM use. A row only reports a material
back to `Network.applyMaterialSelection()` if the user actually picked one
(`MaterialPickerRow.touched`) -- leaving a row alone (e.g. only changing
one layer across a selection with mixed materials on another layer) never
clobbers the other layers' properties with whatever the first selected
object happened to show. `MatGui` (the Gui module that implements the tree
widget) is imported once, at `ui/TaskPanel.py` module scope, since it
isn't loaded automatically just by activating the HVAC workbench.

`DuctNetwork` carries the same picker (embedded in
`TaskPanelNetworkTypeDefaults`, the "Network Defaults" command) for one
`DefaultMaterial_<Role>` property per standardized `LayerRole` (not per
library-defined layer id -- roles are the fixed, small vocabulary core
owns). Fresh networks seed flow surfaces and structural shells with
Galvanised Steel, thermal insulation with closed-cell Nitrile Rubber,
acoustic absorbers with open-cell Nitrile Rubber, acoustic liners with
Galvanized Steel - Perforated, and vapor barriers/outer jackets with
Aluminium. Fire protection remains project-specific and unset.
`core/_construction_schema.apply_default_layer_materials()` (called from
`DuctSegment.applyTypeSchema()`/`DuctComponent.applyTypeSchema()` every
time a type's construction schema is (re)established, not just once at
object creation, since which layers exist depends on a type that isn't
known yet when a segment/component is first created) fills in each layer's
material whenever it doesn't already have one: the layer's own
`ConstructionLayerDef.default_material_uuid`, else the network's
`DefaultMaterial_<Role>` for the layer's own `default_material_role` (or
its first declared role, if the layer doesn't specify one explicitly) --
never overwrites a manual choice or a value restored from an existing
document. `DuctNetwork.resetObjectsToNetworkDefaults()` (the "Reset to
Defaults" command) is the opposite convention, via
`core/_construction_schema.reset_layer_materials_to_network_defaults()` --
like it already does for `LibraryId`/`TypeId`, an explicit reset always
re-applies the network's *current* default materials, discarding whatever
material each layer already had.

`DuctJunction` itself stays geometry-free: it never gets
`ConstructionLayerIds`/`Layer_<id>_Shape`/materials of its own, only its
`DuctComponent` children do.

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
- **Public geometry API for generator/PartScript authors** → `freecad/HVAC/library/library_api.py` (`HVACLibraryAPI`, supplied as `context["hvac_api"]`).
- **Public fitting-loss API for loss modules** → `freecad/HVAC/library/loss_api.py` (`HVACLossAPI`, supplied as `context["loss_api"]`).
