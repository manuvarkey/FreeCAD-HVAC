# FreeCAD-HVAC

FreeCAD-HVAC is a FreeCAD workbench for creating and managing HVAC duct networks. The current implementation focuses on a basic but usable duct-network workflow built around route geometry, automatic connectivity parsing, and library-driven generation of duct segments and fittings.

## User workflow

The intended workflow is:

1. Create a **Duct Network**
2. Add base routing geometry using sketches or line-based objects
3. Let the parser extract connectivity from that geometry
4. Automatically generate duct segments and junctions/fittings.
5. Modify duct/ fitting parameters directly on the generated segments and junctions/fittings.
6. For editing routing the base geometry can be edited as needed and the generated segments and junctions/fittings are synchronised automatically.

This approach keeps the modeling process parametric and reduces the need to rebuild duct geometry manually after every layout change.

## Screenshots
<img width="1418" height="815" alt="Screenshot from 2026-03-27 03-37-15" src="https://github.com/user-attachments/assets/70b94757-0161-4c5d-b9fd-5b85a57cfde7" />
<img width="1418" height="815" alt="Screenshot from 2026-03-27 03-35-18" src="https://github.com/user-attachments/assets/052a4662-84c8-417a-a198-d021a9b4eba3" />
<img width="1418" height="815" alt="Screenshot from 2026-03-27 03-36-14" src="https://github.com/user-attachments/assets/6fafb9ee-c38f-48df-a6a3-4a32c91a7a1e" />

## Project structure

A simplified view of the architecture is:

- **Network container layer**  
  `DuctNetwork` manages the overall duct model and its internal folders.

- **Topology layer**  
  `DuctNetworkParser` reads base geometry and determines connectivity and classification.

- **Generated object layer**  
  `DuctSegment` and `DuctJunction` manage the derived HVAC elements.

- **Library layer**  
  `HVACLibraryRegistry`, `HVACLibraryService`, and JSON definitions provide reusable segment and junction definitions.

## Main concepts

### 1. DuctNetwork

`DuctNetwork` is the central object used to manage a duct system. It is designed to manage the modeling workflow now, and to support analysis workflows later. A duct network maintains managed folders for:

- **Base geometry**: user-authored routing objects such as sketches and line objects
- **Generated geometry**: automatically created duct segments and junctions/fittings

This separation makes the workflow easier to manage: the user edits the base route, while derived HVAC geometry is regenerated or synchronized from it.

### 2. DuctNetworkParser

`DuctNetworkParser` is responsible for reading the base geometry and building network connectivity information from it. It handles:

- topology generation
- connectivity interpretation
- node and edge handling
- classification logic used for segment and junction generation

In practical terms, this is the component that converts drawing geometry into structured duct-network data.

### 3. DuctSegment and DuctJunction

Generated HVAC elements are managed through:

- **`DuctSegment`**: represents generated duct runs
- **`DuctJunction`**: represents generated fittings, junctions, or similar connection objects

The parser reads the base geometry, extracts connectivity, and the resulting information is synchronized into these generated objects. This keeps the final duct model tied to the original routing input.

### 4. Library system

The project uses a library-driven architecture for duct and fitting generation.

Library parsing and management are handled by:

- **`HVACLibraryRegistry`**
- **`HVACLibraryService`**

These components load and organize the available HVAC definitions used by the model generator.

### 5. JSON-based element definitions

Duct segments and junctions are defined in the library using **JSON files**. Shape generation is handled using shape generation functions specified along with element definition. This keeps the system modular and extensible. New profiles, fitting types, and parameters can be added through library data instead of hard-coding everything in the core logic.

Three shipped libraries: `builtin_basic` (minimal generic types), `smacna`
(SMACNA-based sheet-metal types, the ones meant for day-to-day modeling), and
`samples` (reference-only examples of each geometry backend). For the
directory layout, `library.json`/type-def JSON schema, and the junction
naming convention (`topology_family_sub_classification`), see
[`freecad/HVAC/libraries/README.md`](freecad/HVAC/libraries/README.md). For
the three ways a type can produce geometry -- PartScript, static BREP/STEP,
and the legacy generator function (including its use as an FCStd-template
loader) -- see [`freecad/HVAC/libraries/samples/README.md`](freecad/HVAC/libraries/samples/README.md).

## Design goals


- [x] Duct routing module (may use the same module or reuse components for piping also).
- [ ] Detailing of ducts and fittings for rectangular/ circular/ oval ducts.
- [ ] Add BIM data
- [x] Standard library of commonly used air side HVAC components like Diffusers, grills, registers, dampers, intake and exhaust accessories, VAV units, AHUs etc.
- [x] Pressure drop calculation based on terminal flow rates and static pressure calculation for nodes.
- [x] Automatic sizing module based on constant friction drop, constant velocity, static regain methods.
- [ ] Add additional duct classes (custom profile ducts) and detailing like insulation, duct supports, flanges etc.
- [ ] Add support for defining piping.

### 6. Airflow (pressure drop) calculation

`HVAC_CalculateAirflow` runs `AirflowSolver` over a `DuctNetwork`. Flow
distribution across the network's balanced sub-trees is solved first by
`FlowNetwork.solve_flow_components` (terminal design flow rates + mass
conservation). For each segment, `AirflowSolver` then computes velocity,
Reynolds number, and friction loss (Darcy-Weisbach with the Altshul-Tsal
friction factor from its actual duct size), computes each junction's
fitting/dynamic loss coefficient (pluggable per library type via
`loss_module`/`loss_function`, using SMACNA-based coefficients where
defined, with a generic fallback), and propagates static pressure outward
from each sub-tree's balancing terminal (0 Pa reference). Degree-1 terminals
(diffusers, grilles, intakes, ...) contribute their own component loss too.
Results are written back onto the generated segments/junctions and shown in
a results panel.

### 7. Duct sizing

`HVAC_SizeDucts` runs `DuctSizer` over the same flow distribution/boundary
conditions as `AirflowSolver`, solving for duct size instead of pressure
drop, per the network's `SizingMethod`:

- **Constant velocity** and **constant friction rate** size each segment
  independently from its own flow rate.
- **Static regain** sizes sections in sequence outward from the balancing
  terminal (each section's target depends on its already-solved parent's
  velocity), seeded by the network's target velocity.

A segment can override the network's method locally (a fixed `Velocity`, or
a `RectangularSizingMode`/`TargetAspectRatio` for converting a sized area to
rectangular dimensions); sizing runs as a preview/apply panel so results can
be reviewed before committing to the model.

## Status

Basic duct creation functionality is now reasonably in place. The project already supports the main framework required for:

- defining duct routes
- parsing connectivity
- generating duct segments
- generating junction/fitting objects
- organizing library-based element definitions
- calculating airflow pressure drop across a network (`AirflowSolver`)
- automatic duct sizing by constant velocity, constant friction rate, or static regain (`DuctSizer`)

This provides a solid base for further development, including richer fitting logic, validation tools, and future analysis capabilities.
