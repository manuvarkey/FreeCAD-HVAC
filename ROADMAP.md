# Roadmap: generic library completeness and API freeze

Staged implementation plan for the work listed in the November 2026 planning
request. The project is in alpha, so breaking changes to type ids, property
names and library JSON are acceptable until Stage 11 (API freeze).

Rules in [`AGENTS.md`](AGENTS.md) apply to every stage: unit tests for
data/dispatch/loss code, none for shape construction; the
`topology_family_sub_classification` naming convention; library code talks
only to `HVACLibraryAPI` / `HVACLossAPI`.

## 0. Progress

| Stage | State | Notes |
|---|---|---|
| 0 | Done | Ovals accept any width/height. One trim naming scheme. Unknown property keys are load errors. Dead per-port trim properties on star fittings now work. |
| 1 | Mostly done | Shared `library/fittings/` package behind the `shared:` module prefix; both libraries are type-defs only (smacna keeps `features.py` and its PartScripts). All shared fittings are hollow and layered, with flanges as data. `api.build_fitting` / `measure_fitting` / `build_layered_fitting` added. `tools/geometry_smoke.py` added. **Open:** split `library_api.py` into modules. |
| 2 | Done | Every table of the reference is digitised in `library/loss_tables/` as a `LossTable` (clamped interpolation; a lookup reports when it was outside the printed range or touched a blank cell): 14-9, 14-10 A to K with Notes 1 to 5, 14-11 A to J, 14-12 A to C, 14-13 A to I, 14-14 A to Z, 14-15 A to I, 14-16 A to J, 14-17 A/B, 14-18 A to L. Each was transcribed from a fresh read of its page and spot-tested; an inventory test fails if a printed fitting is missing. `smacna_loss.py` is now a thin layer over these tables. New `HVACLossAPI` entry points with shared loss functions (`shared:losses`): `entry_loss`, `exit_loss`, `screen_loss`, `damper_loss`, `obstruction_loss`; they return `APPROXIMATION` with a reason when a lookup leaves the printed table. Defects fixed on the way: flow-ratio axis on 14-13 B and 14-14 A, elbow turn-angle factor, Reynolds correction end point, two missing columns of 14-10 D. **Left for the fitting stages (4 to 7), where the fittings that need them are built:** variant arguments on `elbow_loss` / `transition_loss` / `branch_loss` / `wye_loss` (gored and vaned elbows, conical taps, rectangular-main tees, fan transitions), and status flags on those four older methods, which still report `EXACT` even when clamped. Not digitised: the charts (Fig. 14-3 roughness correction, 14-4 flex duct, 14-5 density) and the nominal-size tables 14-2 to 14-5, of which only the equivalent-diameter equation is implemented. Suspected misprints in the source are kept as printed and listed in the module docstrings. |
| 3 | Done except the editing table | `"per_port": true` properties: one value per connected port for any port count, stored as a list plus a hidden edge-key list, matched by key, kept in step on every sync and remapped on reload (`core/_port_properties.py`, `validation.PortValues`). `api.port_param()` reads per-port and plain properties alike; `api.port_roles()` names ports by role for 1 to n ports. Generic branch, cross and multiport types in both libraries now use one per-port `TrimLength` (and smacna a per-port `ShowFlange`) instead of lettered properties, so none is capped at four ports. Checked on a real FreeCAD object, including save and reload. **Open:** the per-port table in the Edit Type task panel; until then the values are edited as a plain list in the property editor, in port order, with no indication of which port is which. |
| 4 | Partly done | Done: **tee/tap attachment (item 1).** A branch whose axis passes clear of the run is face-mounted: a collar on the run face it passes over, then a 90 degree elbow into the branch direction (`taps.face_tap_layout`). All seven tap and tee types share it through an `Attachment` property (`Auto`, `Side`, `Face`) and `ElbowRadius`. `Auto` picks from where the branch is drawn; a branch crossing the run edge is an error that says how far to move it. Top versus bottom is not a choice: it follows from which side of the run the branch is on. Done: **90 degree cross (item 4)**, `cross_tee_straight`. Done: **double wye (item 5)**, `cross_double_wye_radius` (default) and `cross_double_wye_mitered`: a straight trunk with two inclined branches, each leaving through its own bend; valid in FreeCAD for 30, 45 and 60 degree branches on every profile. `cross_generic` now only takes irregular crosses. Added on the way: `App::PropertyEnumeration` type-def properties, with choices from `validation.enum`. Smoke run: 25 face-mounted cases and 4 cross cases valid. **Open:** the classifier value `branch_eccentricity_ratio` was not needed (the fitting measures the offset itself) and is not added; lateral tee on the wye generator (2) and wye types for tee families (3) are **on hold pending a decision**: the wye generators split the main duct's section into two halves, one per leg, so a lateral built that way necks the run down to half its section and back; a lateral normally keeps its full run. If what is wanted is a lateral whose branch joins through a bend instead of a plain stub, the double wye's branch bend already does that and can be reused for a three-port `branch_lateral_tee_radius` / `_mitered`; loss wiring (7): done for face-mounted branches only (`branch_loss` detects them; a round diverging one reads 14-14 E, "90 degree tee with 90 degree elbow", every other case is the plain tee plus the branch elbow, flagged `APPROXIMATION`); the cross and the bullhead case still use the generic losses. Face-mounted lateral tees are not wired (the lateral tee type has no `Attachment`). |
| 5 | Mostly done | Done: **offsets and transitions separated (item 1).** Offsets (`through_offset_angled` / `_mitered` / `_radiussed`, SMACNA offset types 1 to 3) have their own module (`fittings/offsets.py`) and properties (`OffsetLength`, `OffsetRadius`); `offset_loss` reads them. Transitions are now `through_transition_concentric` / `_eccentric` / `_offset`, chosen by the classifier's `alignment`; concentric is the default when alignment is unknown (mixed profiles, no flow direction). The three old `through_transition_angled/mitered/radiussed` types are gone. All three transitions are the same straight loft and the same loss; what differs is only which one a node gets. Labels were not checked against the SMACNA Duct Construction Standards (not in `reference/`). Done: **obstruction (item 3)**, `through_obstruction_generic`, an inline round bar across the duct with `ObstructionDiameter` and `OffsetFromCentre`, loss from 14-18 H. Done: **VAV box (item 4)**: `through_vav_generic` is now a closed rectangular casing (`BoxLength/Width/Height`) with an inlet and an outlet collar (`CollarLength`) in the section of the duct each meets; its loss is still the user's K. Done: **damper types (item 5)**: `DamperType` (`Coefficient`, `Butterfly`, `Butterfly airfoil`, `Gate`, `Parallel blade`, `Opposed blade`) with `BladeAngle`, `GateOpening`, `BladeCount`; `Coefficient` keeps the user's K, the others read 14-18 A to G at the current position. The damper body is still a plain sleeve; blades are not modelled. **Open:** fitting-derived values such as the included angle as read-only properties (2); the eccentric transition does not yet use its own table (14-11 E); a fixed-pressure-drop loss mode for the VAV box; `through_offset_radiussed` rejects a radius that is too large for the offset instead of reducing it. |
| 6 to 11 | Not started | |

Differences from the plan as written below:

- Libraries do not keep thin delegating generator modules. A type-def names
  a shared module directly (`"module": "shared:elbows"`), so there is no
  per-library Python for a standard fitting at all.
- Flanges are handled by the shared finisher (`_common._finish`) from
  `FlangeThickness` / `FlangeHeight` / `ShowFlange*` properties, for every
  shared fitting. Stage 7 item 1 is therefore largely done; what remains
  there is moving flanges from "fused into the casing" to construction
  features.
- Every smacna junction type now declares `connection_lengths` (Stage 7
  item 2 done).

Known geometry issues from the smoke run (194 of 199 builds valid):
`branch_wye_radius` with an Oval profile is hollow with the right volume but
fails OCC's validity check; `branch_tap_mitered_shoe` with a tall Oval was
already invalid before this work; `through_offset_radiussed` rejects the
smoke run's default radius for the test offset (a parameter limit, not a
defect).

## 1. Where things stand

Findings from reading the code that shape the plan:

| Area | Current state | Consequence |
|---|---|---|
| Generic fitting code | `builtin_basic/generators/junctions.py` is 2154 lines. `smacna/generators/junctions.py` re-implements the same helpers (`_props`, `_size`, `_trimmed`, `_loft`, markers, star body). The two `junction_losses.py` files differ only in comments. | Every new fitting would be written twice. Consolidate first (Stage 1). |
| Wall geometry | `builtin_basic` lofts with `solid=True` and has one roleless-thickness layer, so its fittings are solid bodies. Only `smacna` uses `build_layered_geometry(envelope_builder, ...)`. | Generic fittings need the offset/envelope pattern before more are added. |
| Trim measurement | All 19 `smacna` junction type-defs lack `connection_lengths`, so each one is built twice per sync (the `_peekConnectionLengths` fallback). | Fixed for free once smacna reuses the shared builders. |
| Per-port properties | Four naming schemes for the same idea: `TrimRunA`, `RunTrimLengthA`, `TrimLengthA`, `TrimMain`. Multiport has one shared `TrimLength`. No list or per-port property mechanism exists. | Blocks the plenum and any fitting of order 5 or more. Needs one mechanism (Stage 3). |
| Property validation | Three type-defs use a top-level `"min"` key (for example `TapHeight` in `branch_tap_straight.json`). `validation.py` only reads `validation.minimum` / `exclusiveMinimum`, so `"min"` appears to be ignored. | Small fix in Stage 0. |
| Loss tables | `library/smacna_loss.py` covers 14-10 A/C/D/F, 14-11 A/B, 14-12 A, 14-13 A/B and 14-14 A. Its docstring says values were never checked against a primary source. Rectangular branches use the round tables as an approximation. | The reference PDF now allows verification and about 60 more tables (Stage 2). |
| Classifier | `end` is always `terminal`. Tee branch eccentricity only shows up as the `3d` qualifier. `cross` falls to `generic` unless it is a clean two-run cross or symmetric double wye. | Top/bottom tee attachment needs one new derived value. Terminal function stays a manual choice. |
| Sizing | `_size_independently` sizes every segment from its own flow, so sizes step at every tee. | Needs a "sizing section" concept (Stage 8). |
| Oval | `width >= height` is enforced in `analysis/physics.py` (areas, hydraulic diameter, three sizing solvers) and `library_api.make_oval_wire`. | Stage 0. |
| Analysis scope | Tree networks only. Friction is Darcy-Weisbach with Altshul-Tsal, hardcoded. | Custom friction needs a model abstraction (Stage 8). |

`reference/SMACNA ref 1.pdf` is currently untracked. It is copyrighted
material, so keep it out of the repository (add `reference/` to
`.gitignore`) and cite table numbers in code instead.

## 2. Stage overview

| Stage | Title | Covers request items | Size | Depends on |
|---|---|---|---|---|
| 0 | Quick fixes and conventions | G8, part of G13 | S | none |
| 1 | Shared fitting core in `library/` | G1 | L | 0 |
| 2 | Loss table library | 9 (data) | L | none, parallel with 1 |
| 3 | Port roles and per-port properties | G6 (generic ports), enabler for 1, 5, 6 | M | 1 |
| 4 | Branch and cross fittings | 1, 5, 7, G6 | L | 1, 3 |
| 5 | Through fittings | 4, 8, VAV from 3 | M | 1, 2 |
| 6 | Terminals, equipment, plenum | 2, 6, AHU from 3, G10 | L | 1, 2, 3 |
| 7 | SMACNA library rebuild | G2, 9 (wiring) | M | 4, 5, 6 |
| 8 | Analysis: sizing sections, friction models | G4, G11 | M | none, parallel |
| 9 | UI: result tooltips, layer visibility | G5, G9 | S to M | none, parallel |
| 10 | Custom geometry per fitting | G12 | M | 3 |
| 11 | Gap analysis and API freeze | G13 | M | all |

Sizes are relative effort: S is a day or two, M about a week, L more than a
week. Stages 2, 8 and 9 do not touch generator code and can be interleaved
with the others at any time.

## 3. Stages

### Stage 0: quick fixes and conventions

Goal: remove known friction before the larger refactors.

1. **Oval without `width >= height` (G8).** Make `oval_area` and
   `hydraulic_diameter_oval` symmetric (use major and minor internally).
   Let `aspect_ratio` be below 1 in `oval_dims_for_velocity`,
   `oval_dims_for_friction_rate` and `oval_dims_for_static_regain`, and fix
   the solver bounds that assume `lo=height`. In `make_oval_wire`, put the
   flat sides along whichever axis is longer. Update `tests/test_physics.py`
   and `tests/test_analysis_sizing.py`.
2. **Trim property names.** Adopt one scheme everywhere:
   `Trim<Role>` where role is `RunA`, `RunB`, `Branch`, `BranchA`,
   `BranchB`, `Main`, `Inlet`, `Outlet`, `Neck`. Rename the smacna
   `RunTrimLengthA` and `TrimLengthA..D` forms.
3. **Property def hygiene.** Convert the three top-level `"min"` keys to
   `validation.minimum`. Make the loader reject unknown property keys so the
   mistake cannot recur. Document `"Parameters"` as an established property
   group (13 uses) next to `"Dimensions"` and `"Options"`.
4. **Docs.** `ARCHITECTURE.md` points to `CLAUDE.md` for rules that now live
   in `AGENTS.md`. Ignore `reference/`.

Exit: full test suite green; no behaviour change other than oval.

### Stage 1: shared fitting core in `library/` (G1)

Goal: one implementation of every basic fitting, hollow and multilayer by
default, reachable from any library through the API.

1. **New package `freecad/HVAC/library/fittings/`** holding the builders
   now in `builtin_basic/generators/junctions.py`, grouped by family:
   `elbows.py`, `transitions.py`, `offsets.py`, `taps.py`, `tees.py`,
   `wyes.py`, `stars.py`, `terminals.py`, `inline.py`, `markers.py`, plus
   `_common.py` for the shared helpers. Each fitting keeps the existing
   three-part shape: `_layout()` (pure numbers), `measure_*()` (trims
   only), `build_*()` (shape).
2. **Every builder becomes an envelope builder.** Signature
   `envelope(offset) -> Part.Shape`, built from
   `profile_from_port(port, offset)`. `build_*` then calls
   `api.build_layered_geometry(envelope, context["construction_layers"], params)`.
   Zero declared thickness still yields a thin shell, never a solid: give
   `builtin_basic`'s default layer a `Thickness` property so the first
   envelope always has something to cut.
3. **API convenience functions on `HVACLibraryAPI`** so a generator is a
   few lines: `build_elbow`, `build_mitered_elbow`, `build_transition`,
   `build_offset`, `build_tap`, `build_tee`, `build_wye`, `build_star`,
   `build_terminal_body`, `build_inline_body`, `build_marker`, each with a
   matching `measure_*`, plus `add_flanges(layers, ports, spec)` built on
   the existing `make_flange`. They accept the context plus keyword
   overrides and return the standard `{"layers": ..., "connection_lengths": ...}`
   dict.
4. **Split `library_api.py`** (1914 lines) into mixin modules
   (`_api_vector.py`, `_api_ports.py`, `_api_profiles.py`,
   `_api_solids.py`, `_api_paths.py`, `_api_layers.py`, `_api_fittings.py`).
   `HVACLibraryAPI` stays the single public class.
5. **Loss side.** Move the body of `junction_losses.py` to
   `library/fitting_losses.py` as the default dispatch functions.
   Each library's `generators/junction_losses.py` shrinks to re-exports
   plus genuine overrides.
6. **Thin the libraries.** `builtin_basic/generators/junctions.py` and
   `smacna/generators/junctions.py` become one-line delegations. Both keep
   their module and function names so type-def JSON does not change in this
   stage.

Tests: layout and `measure_*` functions are data code, so add
`tests/test_fitting_layouts.py` asserting `measure_*` equals the
`connection_lengths` returned by `build_*` for each family (this is the
"same underlying calculation" rule from the libraries README). No tests for
shapes.

Exit: both libraries render the same fittings as before from shared code;
`builtin_basic` fittings are hollow; no duplicated helper remains.

Risk: `branch_wye_radius` is already fragile in OCC sweeps and booleans.
Offsetting multiplies boolean count. Keep a per-fitting fallback that
builds the outer envelope only and reports through `GeometryError`.

### Stage 2: loss table library (item 9, data half)

Goal: every table in the SMACNA reference digitised, verified, and
reachable through `HVACLossAPI`. Pure Python, fully unit-tested, no FreeCAD.

1. **Infrastructure.** New package `library/loss_tables/` with a small
   `LossTable` class: 1D, 2D and 3D clamped linear interpolation, support
   for blank cells (the dashes in 14-14 A), and a result that reports
   whether the lookup was clamped or hit a blank so the API can downgrade
   `EXACT` to `APPROXIMATION` with a warning. Each table records its
   SMACNA id, reference velocity (upstream, downstream, or a named leg)
   and applicable profiles.
2. **Verify what exists.** Check the ten tables in `smacna_loss.py`
   against the PDF, then move them into the new package and leave
   `smacna_loss.py` as a compatibility shim until Stage 7. Confirm the
   elbow angle factor (14-10 Note 1) and Reynolds corrections (Notes 2, 3)
   are applied.
3. **Digitise in this order**, one module and one test file per group:

| Order | SMACNA table | Content | Used by |
|---|---|---|---|
| a | 14-10 B, E, G, H, I, J, K, L | 3 to 5 piece, converging/diverging mitered, splitter vanes, turning vanes, Z-shaped, different planes, 30 degree round offset, wye-shaped elbow | elbows, offsets |
| b | 14-11 C to J | round-rect transitions (angle formula into 14-11 B), sides straight, transitions at fan | transitions, AHU and fan connections |
| c | 14-12 B, C | conical contraction, slot to round | transitions |
| d | 14-14 B to Z | conical tee, conical wye, rolled tees with elbow, rectangular tees (45 degree entry, damper, extractor), rect main to round branch, rectangular wyes, dovetail | taps, tees, wyes |
| e | 14-13 C to I | converging counterparts | taps, tees, wyes |
| f | 14-15 A to I | entries: duct in wall, bellmouth, conical, intake hood, tapered hood, canopy | end fittings |
| g | 14-16 A to J | exits: exhaust hood, conical, plane and pyramidal diffuser, elbow discharge, flush with wall, fan outlet, abrupt | end fittings |
| h | 14-17 A, B | screen, perforated plate | end fittings (additive), inline |
| i | 14-18 A to L | butterfly, gate, parallel and opposed blade dampers, cylinder obstruction, depressed duct | dampers, obstruction, offsets |
| j | 14-9, Fig. 14-3, Fig. 14-4, Tables 14-2 to 14-5 | straight-through regain coefficients, roughness correction, flex duct compression, equivalent diameters | sizing, friction (Stage 8) |

4. **API entry points** on `HVACLossAPI`, following the existing
   `LossPath` conventions: `entry_loss`, `exit_loss`, `screen_loss`,
   `damper_loss`, `obstruction_loss`, and variant keyword arguments on the
   existing `elbow_loss`, `transition_loss`, `offset_loss`, `branch_loss`
   and `wye_loss` (for example `construction="conical"`, `vanes=2`).
   Rectangular branch tables (14-13 C/D/F/H, 14-14 N to W) are keyed to
   discrete area ratios: use them when the geometry is within a stated
   tolerance and report `EXACT`; otherwise keep the round-table
   approximation and report `APPROXIMATION`.

Tests: one spot value per row and column edge, one interpolated value, one
clamped value per table; extend `tests/test_loss_api.py` for each new entry
point.

Exit: table inventory in the module docstring matches the PDF index; no
table is consumed without a status.

### Stage 3: port roles and per-port properties (G6 "generic ports")

Goal: one way to say "this value belongs to that port" that works for any
order, survives reloads, and replaces ad hoc `A/B/C/D` suffixes.

1. **Port roles API.** `api.port_roles(context)` returns a stable mapping
   of role name to port for the current topology: `run_a`/`run_b`/`branch`
   (tee, lateral), `main`/`branch_a`/`branch_b` (wye), `run_a`/`run_b`/
   `branch_a`/`branch_b` (cross, double wye), `inlet`/`outlet` (through),
   `neck` (end), and `port_1..port_n` ordered by a deterministic geometric
   key for multiport. It supersedes `run_branch_ports`, `_wye_port_roles`
   and `_lean_port`, and is what lets a wye builder accept a tee-shaped
   node (Stage 4).
2. **Per-port property declaration.** A property def gains
   `"per_port": true`. Core expands it to one stored list
   (`App::PropertyFloatList` plus a parallel edge-key list), exactly the
   persistence pattern already used by `CustomLossCoefficientEdgeKeys`,
   including the reload remap in `core/_custom_loss.remap_persisted_edge_keys`
   (generalise that helper into `core/_port_properties.py`). Generators
   read `params["TrimLength"][edge_key]` through
   `api.port_param(context, name, port)`.
3. **Editing UI.** The property editor shows a raw list, which is not
   usable. Add a small per-port table to the existing Edit Type task panel
   (role, connected segment label, value).
4. **Decision recorded here:** fixed-order fittings (tee, wye, cross) keep
   named scalar properties (`TrimRunA`, ...) because they read better in
   the property editor; `per_port` is for variable-order fittings
   (multiport, plenum, AHU).

Tests: schema expansion, remap on reload, role resolution for each
topology (`tests/test_port_properties.py`, `tests/test_library_api_ports.py`).

Open question: "Generic Ports" in the request is read here as this
role/per-port mechanism. If it instead means ports on `Generic`-profile
types, say so and this stage shrinks.

### Stage 4: branch and cross fittings (items 1, 5, 7, G6)

1. **Tee and tap attachment modes (item 1).**
   - Classifier: add one derived value for degree-3 tee and lateral
     nodes, `branch_eccentricity_ratio` = signed offset of the branch axis
     from the run axis divided by the run half-depth in that direction.
     Pure geometry, so it belongs in `NetworkParser.classify_flow`;
     register it in `validation.KNOWN_DERIVED_VALUE_KEYS`.
   - Type-def: add `Attachment` enum (`Auto`, `Side`, `Top`, `Bottom`) to
     `branch_tap_*` and `branch_tee_*`. `Auto` picks `Side` while the
     branch footprint lies inside the run face, `Top` or `Bottom` (by
     sign) once the branch axis clears the run surface.
   - Geometry: top/bottom mode is a collar on the top or bottom face,
     then an elbow (shared elbow builder) turning into the branch
     direction. New properties `CollarHeight`, `ElbowRadius`.
     The partial-overlap band between the two modes is a geometry error
     with a clear message.
   - Loss: 14-14 D to L are exactly "tee with elbow, branch 90 degrees to
     main"; wire them as loss variants selected by `Attachment`.
2. **Lateral tee on the wye generator (item 7).** Rebuild
   `branch_lateral_tee_generic` as a call into the wye mitered/radius
   layout with `run_b` as the straight-through branch. Remove
   `_lateral_tee_layout`.
3. **Wye types accept more families (G6).** With `port_roles`, let
   `branch_wye_mitered` and `branch_wye_radius` list `branch.lateral_tee`
   and `branch.tee` in `family`. For a bullhead tee the common leg is the
   branch, which is already exposed as `qualifiers.common_leg`. Keep
   priorities below the dedicated tee types so automatic selection does
   not change; they become available as manual, sticky choices.
4. **90 degree cross (item 5).** New type for `cross.cross`: straight
   trunk with two opposed taps, built from two tap layouts sharing a
   trunk. Each side takes the same `Attachment` and tap style options.
5. **Double wye (item 5).** New type for `cross.double_wye`: wye layout
   mirrored about the trunk, mitered and radius variants.
6. **`cross_generic`** stays the star-body fallback for `cross.generic`
   only.
7. **Loss.** No 4-port table exists in the reference. Keep
   `manifold_loss` (pairwise decomposition, `APPROXIMATION`). For a
   symmetric double wye splitting from one inlet, 14-14 Y and 14-13 I
   apply directly per branch pair.

Naming needs a decision before implementation (AGENTS.md says raise, do
not improvise). Proposed ids: `cross_tee_straight`,
`cross_double_wye_mitered`, `cross_double_wye_radius`.

Tests: classifier derived value, attachment auto-selection, loss variant
resolution, layout/measure parity.

### Stage 5: through fittings (items 4, 8, VAV)

1. **Separate offset and transition (item 8).** Today the three offset
   type-defs reuse `build_transition*` and even carry a `TransitionLength`
   property. Target structure, following SMACNA duct construction
   nomenclature:

| Type id | SMACNA name | Classifier match | Loss |
|---|---|---|---|
| `through_offset_angled` | Offset type 1 (angled) | `through.offset` | 14-10 K (round), 14-18 I, J |
| `through_offset_mitered` | Offset type 2 (mitered) | `through.offset` | 14-10 I (Z-shape), 14-18 L |
| `through_offset_radiussed` | Offset type 3 (radius, ogee) | `through.offset` | 14-18 K |
| `through_transition_concentric` | Concentric transition | `through.transition`, `alignment = concentric` | 14-11 A, B, C, D; 14-12 A, B |
| `through_transition_eccentric` | Eccentric transition | `through.transition`, `alignment` in `eccentric`, `double_eccentric` | 14-11 E; 14-12 A |
| `through_transition_offset` | Offset transition (size change plus jog) | `through.transition`, `alignment = offset` | transition and offset summed, `APPROXIMATION` |

   Offsets get their own module (`fittings/offsets.py`), their own
   properties (`OffsetLength`, `OffsetAngle`, `Radius`), and stop sharing
   transition code. The existing `through_transition_angled/mitered/radiussed`
   ids are retired; "angled/mitered/radiussed" describes how an offset is
   made, not a transition. Confirm the exact figure names against the
   SMACNA HVAC Duct Construction Standards before finalising labels, since
   that document is not in `reference/`.
2. **Fitting-derived values.** Transition included angle depends on the
   chosen `TransitionLength`, so it is a type-derived value, not a parser
   one. Add `computed_properties` output (`IncludedAngle`, read-only) and
   pass it to the loss call. This is the "separate vocabulary" that
   `TOPOLOGY_CLASSIFICATION.md` already anticipates.
3. **Obstruction (item 4).** `through_obstruction_generic`, inline kind:
   a cylinder crossing the duct. Properties `ObstructionDiameter`,
   `OffsetFromCentre`. Loss from 14-18 H. A flat-plate or rectangular
   obstruction variant can follow using the same body.
4. **VAV unit (item 3).** Replace the scaled-stub `through_vav_generic`
   with a box body: round inlet collar, casing `Length/Width/Height`,
   rectangular outlet, `TrimInlet`, `TrimOutlet`. Loss stays a user K or a
   pressure drop at design flow (add `LossMode`: `Coefficient` or
   `FixedPressureDrop`, which needs a small extension of `LossPath` or a
   converter inside `inline_device_loss`).
5. **Dampers.** Add `DamperType` (butterfly, gate, parallel, opposed) and
   `BladeAngle` to `through_damper_generic`; loss from 14-18 A to G.

### Stage 6: terminals, equipment, plenum (items 2, 3, 6, G10)

1. **Common terminal body.** Extend the terminal builder with a neck
   (collar) of `TrimNeck` length, then a face body. Every `end` type gets
   `TrimNeck` (G10). The neck profile is the connected duct profile; the
   face may be larger.
2. **Entries and exits (item 2).** Geometry is simple; value is in the
   loss tables from Stage 2.

| Type id | Body | Loss |
|---|---|---|
| `end_entry_plain` | duct end, optional wall flange | 14-15 A |
| `end_entry_bellmouth` | rounded lip | 14-15 B, C |
| `end_entry_conical` | cone or pyramid | 14-15 D, E |
| `end_hood_intake` | weather hood | 14-15 F |
| `end_hood_tapered` | tapered capture hood | 14-15 G |
| `end_exit_plain` | abrupt exit | 14-16 J |
| `end_exit_conical` | conical diffuser | 14-16 B |
| `end_exit_pyramidal` | plane or pyramidal diffuser | 14-16 C, D |
| `end_hood_exhaust` | exhaust hood | 14-16 A |

   All take an optional `ScreenFreeArea` that adds 14-17 A per Notes 9 to
   12. `end_intake_exhaust_generic` remains the catch-all.
3. **Diffusers, grilles, registers (item 2).** `end_diffuser_square`
   (ceiling, with `FaceSize`, `NeckSize`), `end_diffuser_round`,
   `end_diffuser_slot`, `end_grille_generic`, `end_register_generic`
   (grille plus damper K). No SMACNA table covers these; loss is
   manufacturer data, so keep `LossCoefficient` and add the same
   `FixedPressureDrop` mode as the VAV.
4. **AHU (item 3).** `end_ahu_generic`: box `Length/Width/Height`,
   connection position offsets on the face, `TrimNeck`. Loss from 14-11 F
   to J and 14-16 G to I, selected by a `FanConnection` enum. An AHU that
   joins supply and return in one object needs a multi-node component,
   which the data model does not have; that is deferred to Stage 11.
5. **Plenum (item 6).** Box with `Length`, `Width`, `Height`,
   `CentreOffsetX/Y/Z` from the node, and per-port `TrimLength` using
   Stage 3. Each port exits through the box face its direction hits; a
   port that misses the box is a geometry error. One shared builder,
   registered per topology to respect the id convention:
   `through_plenum_generic`, `branch_plenum_generic`,
   `cross_plenum_generic`, `multiport_plenum_generic`. Loss: each inlet
   as an abrupt exit into the box (14-16 J) and each outlet as an entry
   (14-15 A), which fits the `LossPath` model as one path per inlet/outlet
   pair and finally gives mixed multi-inlet/multi-outlet nodes a defined
   answer instead of `UNSUPPORTED`.

### Stage 7: SMACNA library rebuild (G2, item 9 wiring)

1. Rewrite `smacna` type-defs on the shared builders with construction
   thickness (offset) and flange specifiers declared as data: extend the
   existing `construction.features` mechanism so `transverse_flange`
   applies to every port of any fitting, not just
   `through_elbow_rectangular`.
2. Add `connection_lengths` to every type (falls out of Stage 1).
3. Add SMACNA-specific variants that only make sense there: vaned and
   splitter elbows (14-10 G, H), 3 to 5 piece round elbows (14-10 B),
   conical tees and wyes (14-14 B, C), 45 degree entry rectangular tee
   with damper or extractor (14-14 N to S).
4. Wire each type's `loss.variants` to the exact table for its profile and
   flow class. Delete the `smacna_loss.py` shim.
5. Produce the coverage matrix (type id by profile by flow class, with
   SMACNA table and `LossStatus`) as a generated section of
   `libraries/README.md`, backed by a test that fails when a type has no
   loss wired.

### Stage 8: analysis (G4, G11)

1. **Size changes only at reducers (G4).**
   - New concept in `analysis/sizing.py`: a **sizing section** is a
     maximal chain of segments that must share one cross-section.
     Segments are merged across the collinear run pair of a branch or
     cross node, and across bends. A section ends at a degree-2 collinear
     node (straight, offset or transition family, the place a reducer can
     exist), at a branch leg, and at terminals.
   - `NodeModel` already carries what is needed (topology, family); add
     the trunk pair indices to it in `_analysis_adapter`.
   - Each section is sized once for its largest flow (its upstream end).
     Constant velocity and friction rate: size for that flow. Static
     regain: regain is evaluated at section boundaries, not at each tee.
   - Network property `SizeChangePolicy`: `AtReducers` (new default) or
     `EverySegment` (current behaviour). Junction property
     `AllowSizeChange` (default true) on through nodes lets the user pin
     a straight coupling.
   - Result reporting: the Size Ducts panel lists where an extra reducer
     node would save material (section flow drops by more than a
     threshold) as advice, never an automatic edit of base geometry.
   - Fits the SMACNA tee tables, most of which assume `Ac = As`.
2. **Custom friction (G11).**
   - `analysis/friction.py`: a `FrictionModel` protocol returning Pa/m
     from (flow, section, roughness, air). Built in: Darcy-Weisbach with
     Altshul-Tsal (current), Colebrook. Custom: `TableFriction`
     (rows of flow or velocity by diameter, log-log interpolation) and
     `ExpressionFriction` (a restricted arithmetic expression in `Q`, `V`,
     `Dh`, `eps`, `rho`, evaluated with a whitelist parser, never `eval`).
   - Declaration: segment type-def `"friction": {"module", "function"}`
     for library-supplied models (flex duct, fabric duct), mirroring
     `loss`. Network property for a project-wide table (CSV path stored
     in the document). `_analysis_adapter` resolves it once into
     `SegmentModel.friction_evaluator`, the same closure pattern as
     `loss_evaluator`.
   - `physics._friction_rate_pa_per_m` and the three sizing solvers take
     the evaluator instead of calling Altshul-Tsal directly.
   - Add Fig. 14-4 flex compression and Fig. 14-5 density corrections as
     optional multipliers.

Tests: both items are pure analysis code, fully unit-tested.

### Stage 9: UI (G5, G9)

1. **Result tooltips (G5).** In `ui/Observer.py`'s `AirflowResultObserver`,
   add an `SoLocation2Event` callback that ray-picks against the existing
   arrow, plane and segment overlay separators and shows a `QToolTip` with
   the object's `Calc*` values: segments (flow, velocity, friction rate,
   pressure drop, static pressure at both ends) and ports (flow, velocity,
   K, loss status and source, static pressure). Throttle to mouse-idle.
   Keep the text builder in `core/_component_results.py` so it is
   unit-testable.
2. **Layer visibility per network (G9).** One `ShowLayer_<Role>` bool per
   `LayerRole` on `DuctNetwork`, plus `ShowFeatures`, all
   `Prop_NoRecompute`. `core/_component_appearance.py` already forces
   non-visible feature faces transparent; extend the same pass to layers
   whose roles are all hidden. A layer with several roles is shown if any
   of its roles is shown. Expose as checkboxes in Network Defaults and as
   a toolbar dropdown.
   Limitation to accept or solve: transparent faces remain pickable. If
   that is not acceptable, build a second display-only compound instead.

### Stage 10: custom geometry per fitting (G12)

Read as: override one placed component's shape without writing a library
type. If the request means something else, revisit.

1. `GeometrySource` enum on `DuctComponent` and `DuctSegment`: `Library`
   or `Custom`, mirroring `LossCoefficientSource`.
2. `Custom` takes a link to a document object or an external
   STEP/BREP/FCStd file, plus a port mapping (which named sub-element or
   placement corresponds to which connected port). Reuse
   `static_shapes.py`'s descriptor transform and port validation.
3. Trims come from the mapped port positions. Construction layers:
   accept one shape per declared layer, or a single shape assigned to the
   first layer.
4. "Save as library type": export the custom component as a static
   descriptor into a user library.

### Stage 11: gap analysis and API freeze (G13)

1. **Schema.** Add `schema_version` to `library.json` and type-defs, and
   `api_version` to `HVACLibraryAPI` and `HVACLossAPI`. Remove the dual
   forms: `generator` versus `geometry`, `loss_module`/`loss_function`
   versus `loss: {}`, `{"shape"}` versus `{"layers"}`. Publish JSON Schema
   files and validate at load.
2. **Stable identity.** `edge_key` regenerates on reload and is patched
   by remap helpers. Decide whether to persist a stable id on base
   geometry before freezing anything that stores keys.
3. **API surface review.** Mark each `HVACLibraryAPI` method public or
   private, with a deprecation path. Freeze the context dict keys.
4. **Data structures to review:** `GeometryResult`, `LossPath` and
   `LossEvaluation`, `NetworkModel` and friends, `HVACTypeMatchRequest`,
   property def schema, construction layer and feature defs.
5. **Gaps already visible** (to confirm and prioritise at this stage):
   - Multi-node equipment (AHU with supply and return, fan section) has no
     representation.
   - Terminal function (supply, return, exhaust, outdoor air) is not a
     network or classifier concept.
   - Looped networks are rejected by the flow solver.
   - No fan model: curves, system effect, operating point.
   - No leakage class, no thermal gain/loss, no acoustics, although
     `Construction` already exposes U-value and acoustic impedance.
   - No flexible duct or curved segment types in shipped libraries.
   - Inline catalogue is thin: silencer, filter, coil, fire damper,
     access door, flex connector are missing.
   - Oval and mixed-profile transitions classify as `transition_form =
     unknown`, so they never get an exact loss.
   - No bill of quantities or schedule output; README lists BIM data and
     detailing as goals.
   - SI units only in analysis.
   - Geometry failures: no systematic regression harness for OCC
     booleans. A headless smoke script that builds every type across a
     size matrix would catch most breakage without unit-testing shapes.

## 4. Decisions needed

| # | Question | Recommendation |
|---|---|---|
| 1 | Ids for the new cross types | `cross_tee_straight`, `cross_double_wye_mitered`, `cross_double_wye_radius` |
| 2 | Retire `through_transition_angled/mitered/radiussed` in favour of concentric/eccentric/offset | Yes, alpha allows it |
| 3 | Meaning of "Generic Ports" | Port roles plus per-port properties (Stage 3) |
| 4 | Meaning of "custom geometry for fittings" | Per-instance override (Stage 10) |
| 5 | Sizing sections: do bends end a section | No, only collinear through nodes do |
| 6 | Default `SizeChangePolicy` | `AtReducers` |
| 7 | Hidden layers: transparent or removed from display | Transparent first |
| 8 | Loss tables as Python literals or JSON data files | Python modules, simpler to test and diff |
| 9 | Plenum registered per topology (four type-defs, one builder) | Yes, keeps the id convention intact |

Answers received (12 November 2026):

- **1 and 2 (table rows above): accepted.** Cross ids are `cross_tee_straight`,
  `cross_double_wye_mitered`, `cross_double_wye_radius`. The three
  `through_transition_angled/mitered/radiussed` types are retired for
  `concentric/eccentric/offset`.
- **3, "Generic Ports":** fittings with an arbitrary number of ports must be
  modellable. Stage 3 stands as written (port roles plus per-port
  properties), with the weight on the variable-order case: `port_1..port_n`
  roles and `per_port` properties are the core, not an add-on. Named
  `TrimPortA..D` properties on generic cross/branch types become `per_port`
  too, so no type is capped at four ports.
- **4, "custom geometry for fittings":** the user selects a FreeCAD geometry
  object and defines custom ports on it to describe a fitting. Stage 10
  stands, with these points fixed: the source is a document object (not
  only a file); ports are defined by the user on that object (position,
  direction, profile and size per port), not inferred; and the port count
  is arbitrary, so it builds on Stage 3's per-port mechanism.

## 5. Suggested order of work

Stage 0, then Stage 1 and Stage 2 in parallel, then 3, 4, 5, 6, 7.
Stages 8 and 9 fit anywhere after Stage 0. Stage 10 after 3. Stage 11
last, with its gap list updated as each stage closes.
