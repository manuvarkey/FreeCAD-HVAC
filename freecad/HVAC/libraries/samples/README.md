# Samples library

This library is **not** meant to be modeled with directly. It exists purely as a
reference: each type here demonstrates one of the ways a library type-def can
produce geometry (a "geometry backend"), so a library author can see a working,
minimal example of each before writing their own. `smacna` and `builtin_basic`
ship the types actually meant for day-to-day use.

There are three ways to wire geometry to a type-def. All three receive the same
`context` dict (`hvac_api`, `library_id`, `start_point`/`end_point` for segments
or `connected_ports` for junctions, `params`/`properties`, ...) and must return
one of:

- `{"shape": <Part.Shape>}` -- the legacy, single-shape form. Fine for a type
  with only one construction layer (most shipped types); `build_geometry()`
  normalizes it to a single layer, id `"shape"`.
- `{"layers": {"casing": {"shape": <Part.Shape>}, "insulation": {"shape": <Part.Shape or None>}}}`
  -- for a type with more than one construction layer (e.g. a casing plus
  its own insulation solid, as in `smacna/models/circular_straight.py`,
  `smacna/generators/junctions.py:build_elbow`). Layer ids are whatever the
  type-def's own `"construction"` block declares (see
  `freecad/HVAC/libraries/README.md`) -- `build_geometry()` stamps each
  returned layer's semantic roles on from there.

Either form may also include `"connection_lengths"` for junctions that trim
the connected segments, exactly as before. See
`freecad/HVAC/library/geometry_result.py` for the full `GeometryResult`/
`LayerGeometry` contract every backend's return value is normalized into,
`freecad/HVAC/library/Library.py:build_geometry` for the exact dispatch,
and `freecad/HVAC/library/library_api.py` for the full `HVACLibraryAPI`
surface available as `context["hvac_api"]` (including
`build_concentric_layers`, a shared helper for building N concentric
construction-layer solids around a shared set of anchor ports).

## 1. PartScript (`"geometry": {"backend": "partscript", "file": "..."}`)

A plain Python file, loaded and executed directly (`freecad/HVAC/library/partscript_shapes.py`),
with full access to `import Part` / `import FreeCAD` and every `HVACLibraryAPI`
primitive (`make_straight_shape`, `make_section_face`, `fuse_shapes`, boolean
ops, ...). It must set `HVAC_PARTSCRIPT_API = 1` and define
`generate(context) -> {"shape": ...}` (an optional `validate(context)` runs
first if present). This is the preferred backend for new parametric types --
no JSON schema beyond pointing at the file, and the model author has the same
raw `Part` access a generator function has.

Example here: `types/segments/circular_acoustic_straight.json` ->
`models/circular_acoustic_straight.py`. It demonstrates a PartScript that
returns three concentric construction layers: acoustic liner, absorber, and
outer jacket.

## 2. Static BREP/STEP (`"geometry": {"backend": "static", "descriptor": "..."}`)

For a fixed, non-parametric shape (a vendor-supplied STEP file, say) with a
fixed port list. The `descriptor` is a JSON file (`freecad/HVAC/library/static_shapes.py`)
that points at the geometry file plus its placement, ports (position,
direction, profile, section params) and `connection_lengths` -- no Python code
at all.

Example here: `types/junctions/end_diffuser_static.json` ->
`models/static_diffuser.json` (descriptor) -> `models/static_diffuser.step`
(geometry).

## 3. Legacy generator function (`"generator": {"module": "...", "function": "..."}`)

A Python function `func(context) -> {"shape": ..., "connection_lengths": ...}`
in `<library>/generators/<module>.py`. This is the original, fully-general
mechanism -- most procedural types in `builtin_basic`/`smacna`
(`generators/segments.py`, `generators/junctions.py`) use it directly.

It's also how a type loads an entire parametric **FreeCAD document** as its
shape: the generator function itself is a thin wrapper that calls
`HVACLibraryAPI.shape_from_fcstd(fcstd_path, context, params={...},
result_object=...)`, which opens the `.FCStd`, sets its `App::VarSet`
properties, recomputes, and extracts the named result object's shape. This
is heavier than PartScript (a whole FreeCAD document + spreadsheet-style
parametrics vs. a Python file) but useful when the source geometry is more
naturally authored in the FreeCAD GUI than in code.

Example here: `types/segments/rectangular_straight.json` ->
`generators/segments.py:build_rectangular_straight_fcstd` ->
`models/rectangular_straight.FCStd`.

## Choosing a backend

- New parametric type, comfortable writing the geometry in Python: **PartScript**.
- Fixed/imported geometry (vendor part, STEP export), no parametrics needed: **static**.
- Geometry is more naturally authored/edited in the FreeCAD GUI as a `.FCStd`
  document (sketches, VarSet-driven expressions): **legacy generator + `shape_from_fcstd`**.
- Anything needing bespoke Python logic beyond what PartScript's `generate()`
  contract offers (e.g. dispatching to different sub-models based on size, or
  reuse of shared helper code across many types): **legacy generator function**.
