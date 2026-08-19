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
| `family` | list of dotted family strings the parser's classifier matches against (segments: e.g. `["straight_segment"]`; junctions: e.g. `["through.bend", "through.bend_90"]` -- see `classify_junction_family`) |
| `profiles` | allowed cross-section profiles, e.g. `["Circular"]`, `["Rectangular"]` |
| `constraints` | e.g. `{"degree": 1}` restricting how many ports a junction may have |
| `properties` | list of property defs (below) |
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
FCStd-template loader via `HVACLibraryAPI.shape_from_fcstd`) and how to
choose between them.

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
