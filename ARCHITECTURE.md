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
| `library/Library.py` | `HVACTypeDef`/`HVACLibrary`/`HVACLibraryRegistry`: type-def loading, per-library match indexes, `select_type`/`matches_type`/`resolve_sticky_type`, geometry-backend dispatch (`build_geometry`). |
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
6. FreeCAD recompute calls `DuctSegment.execute()` / `DuctComponent.execute()`, which do an **exact** `resolve_type(LibraryId, TypeId)` lookup and call `HVACLibraryRegistry.build_geometry()` to produce the `Part.Shape`. `DuctJunction.aggregateConnectionLengths()` then rolls each component's own trim into the junction's `ConnectionLengthsJson` — the one external trim contract `syncSegments`'s next pass consumes to shorten the two real connected segments.
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
list_inline_types()` (the "Add Inline Component" UI action).

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
