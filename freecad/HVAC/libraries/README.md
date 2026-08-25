# HVAC library structure

An HVAC library is any directory under a configured search path (by default
`freecad/HVAC/libraries/`, see `hvaclib.get_default_library_search_paths()`)
containing a `library.json` manifest. `HVACLibraryRegistry.scan_path` auto-
discovers every such directory; there's no separate registration step.

Three libraries ship today:

- **`builtin_basic`** -- minimal generic types, always available.
- **`smacna`** -- SMACNA-based sheet-metal duct/fitting types, the ones
  actually meant for day-to-day modeling.
- **`samples`** -- not for modeling; reference examples of each geometry
  backend. See `samples/README.md`.

## Directory layout

```
<library>/
  library.json                 # manifest (see below)
  __init__.py                  # empty package marker + SPDX header
  generators/
    __init__.py
    <module>.py                # legacy "generator": {module, function} targets
  models/
    <name>.py                  # PartScript files
    <name>.FCStd                # FCStd-template documents
    <name>.json, <name>.step    # static descriptor + source geometry
  types/
    segments/
      <id>.json                # one type-def per file
    junctions/
      <id>.json
```

`generators/`, `models/`, and the two `types/` subfolders are conventional,
not hardcoded -- `library.json`'s `type_roots` is what the loader actually
walks, and `generators_package` is what `import_generator` imports from.
Keep the convention anyway; every shipped library follows it and there's no
reason to diverge.

## `library.json`

```json
{
  "id": "smacna",
  "label": "SMACNA",
  "generators_package": "freecad.HVAC.libraries.smacna.generators",
  "type_roots": ["types/segments", "types/junctions"]
}
```

- `id`: stable identifier, used in `context["library_id"]` and everywhere a
  type is addressed as `(library_id, type_id)`.
- `label`: display name.
- `generators_package`: the Python package `import_generator(library_id, module)`
  imports `<generators_package>.<module>` from, for the legacy `"generator"` backend.
- `type_roots`: directories (relative to the library root) scanned for `*.json` type-defs.

## Type-def JSON (`HVACTypeDef`, see `freecad/HVAC/library/Library.py`)

Common fields, both segments and junctions:

| field | meaning |
|---|---|
| `id` | stable identifier within the library; see naming convention below |
| `label` | display name |
| `category` | `"segment"` or `"junction"` |
| `family` | list of dotted family strings the parser's classifier matches against (segments: e.g. `["straight_segment"]`; junctions: e.g. `["through.bend", "through.bend_90"]` -- see `classify_junction_family`, documented in [`freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md`](../core/TOPOLOGY_CLASSIFICATION.md)) |
| `profiles` | allowed cross-section profiles, e.g. `["Circular"]`, `["Rectangular"]` |
| `constraints` | e.g. `{"degree": 1}` restricting how many ports a junction may have |
| `properties` | list of property defs (below) |
| `construction` | optional list of construction layer defs (below); omit entirely for a type with just one, roleless implicit layer |
| `geometry` | `{"backend": "partscript"\|"static", "file"\|"descriptor": "..."}` |
| `generator` | legacy alternative to `geometry`: `{"module": "...", "function": "..."}` |
| `lengths_module` / `lengths_function` | optional, junctions: computes per-port trim lengths separately from the shape |
| `loss_module` / `loss_function` | optional: fitting-loss coefficient function for the airflow solver |

Junctions additionally carry a `topology` field (see below).

Property def (`HVACPropertyDef`):

```json
{
  "name": "Width",
  "prop_type": "App::PropertyLength",
  "group": "Dimensions",
  "description": "Rectangular duct width",
  "default": 100.0,
  "validation": {"exclusiveMinimum": 0.0}
}
```

`prop_type` is any FreeCAD property type string (`App::PropertyLength`,
`App::PropertyBool`, ...). `group` is the FreeCAD property-editor group; the
established groups are `"Dimensions"` (sizes/thicknesses) and `"Options"`
(booleans/toggles like `ShowFlange1`).

See `samples/README.md` for the three geometry backends (`partscript`,
`static`, and the legacy `generator` function -- including its use as a
FCStd-template loader via `HVACLibraryAPI.shape_from_fcstd`), how to choose
between them, and the `{"shape": ...}`/`{"layers": {...}}` result contract
every backend's return value is normalized into (see
`freecad/HVAC/library/geometry_result.py` and ARCHITECTURE.md's "Component
geometry & materials").

### Construction layers

A type's own `"construction"` block declares how many physical layers its
wall is built from, in build order (e.g. a bare duct wall, or a wall plus
insulation, or a casing plus an acoustic fill plus a perforated liner) and
what each one *means* -- its FreeCAD-standardized semantic role(s), from
`freecad/HVAC/library/construction.py`'s fixed `LayerRole` vocabulary
(`flow_surface`, `structural_shell`, `thermal_insulation`,
`acoustic_absorber`, `acoustic_liner`, `vapor_barrier`, `outer_jacket`,
`fire_protection`). A layer may declare more than one role (e.g. a
single-wall duct's only layer is both `flow_surface` and
`structural_shell`).

```json
"construction": [
  {
    "id": "casing",
    "roles": ["flow_surface", "structural_shell"],
    "thickness_property": "Thickness"
  },
  {
    "id": "insulation",
    "roles": ["thermal_insulation"],
    "default_material_role": "thermal_insulation",
    "thickness_property": "InsulationThickness"
  }
]
```

- `id`: library-chosen, stable within this type-def (e.g. `"casing"`,
  `"liner"`, `"absorber"`) -- pairs a layer's def with the geometry backend's
  own `{"layers": {"<id>": {"shape": ...}}}` return value (see
  `freecad/HVAC/library/geometry_result.py`) and with that layer's own
  `Layer_<id>_Shape`/`Layer_<id>_Material` FreeCAD properties (see
  `core/_construction_schema.py`). Application/core code must never branch
  on this id -- only on `roles`.
- `roles`: the standardized `LayerRole` values this layer plays.
- `default_material_role` (optional): which network-level
  `DefaultMaterial_<Role>` property (see `core/Network.py`) this layer
  falls back to when it has no material of its own yet.
- `default_material_uuid` (optional): an explicit default material,
  overriding `default_material_role`.
- `thickness_property` (optional): the name of one of this type-def's own
  declared `properties` that holds this layer's thickness -- purely
  informational metadata for detailing/mass-calculation consumers; core
  never interprets it (a layer's generated Shape is the source of truth for
  its own volume).

A type-def with no `"construction"` block at all (not yet migrated) behaves
as a single, roleless implicit layer. Geometry backends compose whatever
primitives they need to build each declared layer's own solid --
`HVACLibraryAPI.build_concentric_layers` is the shared primitive for the
common "N concentric shells around a shared set of ports" case (a wall,
optionally wrapped by further layers growing inward/outward from it); see
`freecad/HVAC/library/library_api.py`.

Downstream code (materials, appearance, and airflow/acoustic/thermal/
detailing) queries construction only by role, via
`core/Construction.py`'s `Construction.layers_with_role(role)` /
`flow_surface()` / `structural_layers()` / `thermal_layers()` /
`acoustic_layers()` -- reachable off a segment/component's own
`getConstruction()`. It must never assume a particular layer id exists.

## Type selection (automatic matching)

`freecad/HVAC/core/NetworkParser.py` classifies *what* a network item is
(topology, family, connected-port profiles). It never picks a TypeId. That
job belongs to `HVACLibraryRegistry` (see `freecad/HVAC/library/Library.py`),
which matches a `HVACTypeMatchRequest` (category/topology/family/profile,
derived by `DuctNetwork` sync from the parser's output) against every
type-def a library declares, and picks one. `DuctNetwork` sync then writes
the resulting `LibraryId`/`TypeId` onto the segment/junction object;
`execute()` only ever does an exact `resolve_type(library_id, type_id)`
lookup -- it never classifies or matches anything itself.

A type-def opts into this by declaring what it can represent:

- `family`: the classifier's dotted family keys it supports (see
  `classify_junction_family` /
  [`TOPOLOGY_CLASSIFICATION.md`](../core/TOPOLOGY_CLASSIFICATION.md) for
  junctions; `"straight_segment"`/`"curved_segment"` for segments).
- `profiles`: the cross-section profiles it supports, or the `"Generic"`
  wildcard (see below).
- `selection`: matching metadata, new in this system:

  ```json
  "selection": {
    "kind": "model",
    "priority": 50
  }
  ```

  - `kind`: `"model"` (a real, geometry-producing type), `"placeholder"`
    (an invisible marker/fallback -- see `*_marker.json`), or `"inline"`
    (a user-added-only device -- damper, silencer, flex connector, ...;
    see "Inline components" below). Descriptors without a `selection`
    block default to `kind: "model"`, `priority: 0`, so existing type-defs
    keep loading unchanged.
  - `priority`: tiebreaker used only when more than one type is otherwise
    equally specific for the same request (see "Ranking" below). It plays
    no role once a type is already selected -- see "Sticky selection". Not
    used for `kind: "inline"` types, which are never ranked against one
    another automatically.

### `"Generic"` profile wildcard

`profiles: ["Generic"]` (or an empty `profiles` list, which existing
validation already treats the same way) makes a type profile-independent --
it matches any requested profile, but always ranks below a type that
matches the exact requested profile. This is the same wildcard semantics
`freecad/HVAC/library/validation.py` already used for structural validation;
matching reuses it rather than introducing a second, conflicting wildcard
system.

### Explicit family aliases

A type-def lists every classifier family key it supports explicitly --
matching never walks the family up or down the dotted hierarchy to find a
looser match (that would risk silently selecting the wrong fitting). If a
type should also match the non-coplanar variant of a family, say so:

```json
"family": ["branch.tee", "branch.tee.3d"]
```

(The one exception: a *currently selected* type's family may be a
descendant of the requested family, so a manually-picked, more specific
type stays valid even though the classifier can never literally produce
anything more specific -- see "Sticky selection".)

### How automatic matching works (`select_type`)

For a request (category, topology, family, profile), matching tries, in
order:

1. `kind: "model"`, exact profile match
2. `kind: "model"`, `"Generic"`-profile match
3. `kind: "placeholder"`, exact profile match
4. `kind: "placeholder"`, `"Generic"`-profile match

Within a tier, every candidate is checked against the request's structural
context (topology, degree/constraints, connected-port profiles) using the
same rules as `freecad/HVAC/library/validation.py`'s `context_violations()`
-- a candidate that fails degree/topology/profile constraints is dropped
before ranking, so `matches_type()` and `select_type()` can never disagree
about whether a given type is eligible for a request.

**Ranking** picks the highest `selection.priority` within the first
non-empty, constraint-passing tier. If two or more candidates in that tier
tie at the highest priority, that's a genuine authoring ambiguity: it's
never resolved by JSON file/directory load order. In normal (non-strict)
use, an ambiguous tier is logged as a warning and matching falls through to
the next (broader) tier, eventually a placeholder, rather than silently
guessing; strict/testing callers can pass `strict=True` to get
`status="ambiguous"` back instead.

Priority only decides *which* type wins a fresh automatic selection -- see
"Sticky selection" for why it never disturbs an already-valid choice.

### Sticky selection

Normal network sync (`DuctNetwork.syncSegments`/`syncJunctions`) doesn't
call `select_type()` on every object on every sync. It first asks
`resolve_sticky_type(library_id, current_type_id, request)`:

- if the object's current `TypeId` resolves to a real (`kind: "model"`)
  type in its current `LibraryId`, and that type is still compatible with
  the latest classifier output -> **keep it**, no matter its
  `selection.priority` relative to other candidates.
- otherwise -> run `select_type()`.

This is why a manually-picked, lower-priority fitting (e.g. a long-radius
elbow instead of the library's default short-radius elbow) survives every
subsequent sync as long as it stays geometrically valid, and is only
replaced when the classification genuinely changes underneath it (topology,
family, profile, or a structural constraint like degree/port-profile no
longer holds).

**Placeholders are never sticky.** If the current `TypeId` resolves to
`kind: "placeholder"`, sync always re-runs `select_type()` -- so a marker
automatically upgrades to a real model the moment one becomes eligible
(more specific classification, a newly-available connected-port profile, a
reloaded library, ...).

An explicit **"reset to network defaults"** (`resetObjectsToNetworkDefaults`)
intentionally bypasses stickiness and always calls `select_type()` fresh
against the network's default library, since the user is asking to discard
manual choices, not just repair invalid ones.

### Inline components

A `DuctJunction` that's a simple 2-port `through` node can carry, besides
its automatically-selected **Primary** `DuctComponent`, zero or more
user-added **Inline** components in series (a damper, a silencer, a flex
connector) -- see [`ARCHITECTURE.md`](../../ARCHITECTURE.md) for how a
junction composes its component chain. A type-def marks itself as one of
these by declaring `selection.kind: "inline"`.

Inline types are excluded entirely from `HVACLibrary`'s automatic-matching
indexes (`_rebuild_match_index`), so `select_type()`/`resolve_sticky_type()`
can never choose one as a Primary component, no matter how its `family`/
`profiles` are declared -- they're reachable only through
`HVACLibraryRegistry.list_inline_types(library_id, topology=, profile=)`,
which the "Add Inline Component" UI action uses to populate its type
picker. Unlike Primary selection, there's no family matching involved:
adding an inline component is always a direct, deliberate user choice, not
something the classifier's output should drive.

`freecad/HVAC/libraries/smacna/types/junctions/through_damper_generic.json`
and `through_vav_generic.json` are the built-in examples.

### Adding a new type that's automatically selectable

A third-party/user library needs nothing beyond a descriptor JSON (declaring
`category`/`topology`/`family`/`profiles`/`selection`) and a geometry
implementation -- no change to `Network.py`, `Junction.py`, or any other
core module. Give it:

- the exact classifier `family` key(s) it should match,
- the `profiles` it supports (or `"Generic"`),
- a `selection.priority` higher than any broader/generic type it should be
  preferred over, if it overlaps with one.

Descriptors intentionally reachable only by *manual* selection (e.g. a
degree-1 terminal's specific function -- diffuser vs. AHU connection vs.
louver -- which the classifier cannot infer from geometry alone) don't need
a priority audit: they simply won't appear in any automatic-selection tier
whose family key the classifier can produce, and rely on "Sticky selection"
to stay selected once a user picks one.

## Naming convention

### Segments

`<profile>_<shape>`, e.g. `circular_straight`, `oval_straight`,
`rectangular_straight`, `circular_generic`. `profile` matches (lowercased)
one of the type's `profiles` entries.

### Junctions: `topology_family_sub_classification`

Every junction id **must start with its `topology` value** -- this is the
connectivity-graph role the parser's classifier assigns, and is one of:

- `through` -- degree-2, in-line (straight run, bend, transition, offset, inline device)
- `branch` -- a tee/wye/lateral splitting one run into two
- `cross` -- a 4-way crossing/double-wye
- `multiport` -- more than 4 ports meeting at one node
- `end` -- degree-1 (a terminal: diffuser, grille, louver, fan/AHU connection, ...)

For exactly how `topology` and `family` are derived from node geometry
(the degree/collinearity/eccentricity/coplanarity rules behind
`classify_node_topology` and `classify_junction_family`, and how the two
combine into the dotted `family` key matched against a type-def's `family`
list), see
[`freecad/HVAC/core/TOPOLOGY_CLASSIFICATION.md`](../core/TOPOLOGY_CLASSIFICATION.md).

After the topology, two further tokens narrow it down:

- **`family`** -- the specific fitting family within that topology (`tee`,
  `wye`, `elbow`, `transition`, `damper`, `vav`, `diffuser`, `fan_source`,
  `intake_exhaust`, `terminal`, ...). Omit this token (collapsing to just
  `topology_sub_classification`) when the type is generic across every
  family in its topology rather than one specific fitting -- e.g.
  `through_generic` covers straight/bend/offset alike, vs.
  `through_elbow_generic` which is bend-specific.
- **`sub_classification`** -- the implementation nature of the type:
  - `generic` -- a real, parametric geometry generator, usable for any
    configuration within its family.
  - `marker` -- an invisible placeholder with no real geometry, used purely
    to hold a graph node's connectivity (see `*_marker.json` throughout
    `builtin_basic`/`smacna`).
  - a backend name (e.g. `static`) -- reserved for `samples`, where the
    sub-classification records which geometry backend the type demonstrates
    rather than a real modeling distinction (`end_diffuser_static`).

Worked examples (from `smacna/types/junctions/`):

| id | topology | family | sub_classification |
|---|---|---|---|
| `branch_tee_generic` | branch | tee | generic |
| `branch_wye_generic` | branch | wye | generic |
| `branch_marker` | branch | *(all)* | marker |
| `through_elbow_generic` | through | elbow | generic |
| `through_transition_generic` | through | transition | generic |
| `through_damper_generic` | through | damper | generic |
| `through_vav_generic` | through | vav | generic |
| `through_generic` / `through_marker` | through | *(all)* | generic / marker |
| `cross_generic` / `cross_marker` | cross | *(all)* | generic / marker |
| `multiport_generic` / `multiport_marker` | multiport | *(all)* | generic / marker |
| `end_diffuser_generic` | end | diffuser | generic |
| `end_fan_source_generic` | end | fan_source | generic |
| `end_intake_exhaust_generic` | end | intake_exhaust | generic |
| `end_terminal_marker` | end | terminal | marker |
| `end_diffuser_static` (samples) | end | diffuser | static |

When adding a new junction type, pick the real topology first (it must match
the classifier's vocabulary, not just read naturally), then decide whether
it needs its own `family` token or genuinely applies across the whole
topology.

### Model file naming

A type's model file(s) under `models/` must be named after the type-def
`id`, not invented independently -- `models/<id>.py` for a PartScript,
`models/<id>.FCStd` for an FCStd-template, `models/<id>.json`/`models/<id>.step`
for a static descriptor + its source geometry. This applies regardless of
geometry backend: `smacna/types/segments/circular_straight.json` pairs with
`smacna/models/circular_straight.py`, `smacna/types/segments/rectangular_straight.json`
pairs with `smacna/models/rectangular_straight.FCStd`, and
`samples/types/junctions/end_diffuser_static.json` pairs with
`samples/models/static_diffuser.json`/`.step` (a static descriptor may point
at a differently-named source file, but the descriptor JSON itself should
still follow the type id where practical). When a library reuses one model
file across several type-defs (e.g. one PartScript parametrized by
`profile`), name the file after the shared generic id, not any one caller.
