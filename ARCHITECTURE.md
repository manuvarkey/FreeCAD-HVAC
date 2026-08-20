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
DuctSegment / DuctJunction  generated objects: hold LibraryId/TypeId + geometry
     |
Library layer              JSON type-defs + HVACLibraryRegistry: what geometry to build
```

Each layer only talks to the one below it. Don't hardcode fitting-specific
logic upstream of where it belongs (e.g. no fitting knowledge in the
parser) — see `CLAUDE.md` §3 "Respect the layering".

| Layer | File(s) | Job |
|---|---|---|
| Container | `freecad/HVAC/core/Network.py` (`DuctNetwork`) | Owns the network's Base/Geometry/Topology folders, network-wide defaults, and the sync loop that keeps everything below in step with the base geometry. |
| Classifier | `freecad/HVAC/core/NetworkParser.py` (`DuctNetworkParser`) | Reads base sketches/wires, builds a connectivity graph, and classifies each node's `topology`/`family_key` and each edge's port profile. Never picks a type. See [`core/TOPOLOGY_CLASSIFICATION.md`](freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md). |
| Generated objects | `freecad/HVAC/core/Segment.py`, `freecad/HVAC/core/Junction.py` | `DuctSegment`/`DuctJunction`: FreeCAD document objects that store `LibraryId`/`TypeId` and other metadata, and `execute()` by resolving that TypeId and asking the library layer to build geometry. Never classify or select a type themselves. |
| Library | `freecad/HVAC/library/Library.py`, `freecad/HVAC/library/validation.py`, `freecad/HVAC/utils/hvaclib.py` (`HVACLibraryService`), `freecad/HVAC/libraries/**/*.json` | Loads type-defs from JSON, decides *which* type-def matches a request, and dispatches to the geometry backend (PartScript / static BREP / legacy generator). See [`freecad/HVAC/libraries/README.md`](freecad/HVAC/libraries/README.md). |

## Core modules

One-line concept per module — read the module's own docstring for detail,
it's kept accurate and up to date on purpose.

| Module | Concept |
|---|---|
| `core/Network.py` | `DuctNetwork` container + the debounced sync loop (`requestSync`/`_runDeferredSync`) that keeps `DuctSegment`/`DuctJunction` objects in step with base geometry. |
| `core/NetworkParser.py` | `DuctNetworkParser`: builds a geometric graph from actual snapped endpoints, then an analysis graph on top (grouped "supernodes" for user-defined virtual junctions) that connectivity/degree/classification actually run on. |
| `core/Segment.py` / `core/Junction.py` | `DuctSegment`/`DuctJunction`: FreeCAD document objects. `updateMetadata()` is a pure metadata writer (no selection logic); `execute()` does an exact type lookup and builds geometry. See "Type selection subsystem" below. |
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
4. For each segment/junction, `DuctNetwork.sync{Segments,Junctions}` builds a match request from that classification and asks the library registry to resolve a type (see next section) — then writes the result onto the object (`LibraryId`, `TypeId`, `Profile`/`Family`, etc.) via `updateMetadata()`, which is a **pure metadata writer** with no selection logic of its own.
5. `applyTypeSchema()` adds/removes the FreeCAD properties the selected type declares.
6. FreeCAD recompute calls `DuctSegment.execute()` / `DuctJunction.execute()`, which do an **exact** `resolve_type(LibraryId, TypeId)` lookup and call `HVACLibraryRegistry.build_geometry()` to produce the `Part.Shape`.
7. Optionally, `HVAC_CalculateAirflow` (`AirflowSolver`) and `HVAC_SizeDucts` (`DuctSizer`) run over the resulting network for pressure-drop and sizing results — see "Airflow & sizing flow" above.

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
LibraryId / TypeId written onto the object
        v
execute() -> resolve_type() (exact lookup, no matching) -> build_geometry()
```

Key rules (all enforced in `freecad/HVAC/library/Library.py`):
- A type-def opts in via `family` (classifier keys it supports), `profiles`
  (`"Generic"` = wildcard), and `selection: {kind, priority}`.
- `kind: "model"` beats `kind: "placeholder"`; exact profile beats
  `"Generic"` profile; `priority` only breaks ties inside the same tier.
- A manually-picked, still-valid `model` type is **sticky** — it survives
  resync even if a higher-priority alternative exists. A `placeholder` is
  **never** sticky, so it can auto-upgrade once a real model qualifies.
- Adding a new fitting = a new JSON type-def + geometry backend. No new
  `if/elif` in `Network.py`/`Junction.py`/`hvaclib.py`.

## Where to look for more

- **How to write code here** (test policy, naming convention, layering
  rules, SPDX header) → [`CLAUDE.md`](CLAUDE.md).
- **User-facing feature overview, design goals, status** → [`README.md`](README.md).
- **Library/type-def JSON schema, naming convention, type-selection
  details** → [`freecad/HVAC/libraries/README.md`](freecad/HVAC/libraries/README.md).
- **How the parser classifies topology/family** → [`freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md`](freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md).
- **Choosing a geometry backend (PartScript / static / generator)** → [`freecad/HVAC/libraries/samples/README.md`](freecad/HVAC/libraries/samples/README.md).
- **Public API for generator/PartScript authors** → `freecad/HVAC/library/library_api.py` (`HVACLibraryAPI`) — external/library code should only use this, not internal HVAC modules directly.
