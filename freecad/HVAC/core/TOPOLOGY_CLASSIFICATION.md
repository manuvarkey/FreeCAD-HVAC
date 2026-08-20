# Node topology and junction-family classification

When the parser encounters a graph node while syncing a `DuctNetwork`, it
classifies that node's geometry into a `topology` and `family` and combines
them into a dotted `family_key` (e.g. `"through.bend_90.3d"`). This key is
matched against each junction type-def's `family` list to decide which
library types are valid candidates for that node — see
[`freecad/HVAC/libraries/README.md`](../libraries/README.md) for the
type-def side of this contract and the `topology_family_sub_classification`
junction naming convention.

This doc exists so a library author can predict what `topology`/`family`
tokens a given duct layout will produce, and therefore write correct
`family` entries in a junction type-def (`classify_node_topology` /
`classify_junction_family` in `freecad/HVAC/core/NetworkParser.py`).

## `topology`: degree -> topology

`topology` is purely a function of how many ports meet at the node (its
degree):

| degree | topology |
|---|---|
| `<= 0` | `isolated` |
| `1` | `end` |
| `2` | `through` |
| `3` | `branch` |
| `4` | `cross` |
| `>= 5` | `multiport` |

This is exactly the vocabulary required at the start of every junction type
id, and must match the type-def's own `topology` field.

## Geometric concepts used below

`family` classification within a topology is driven by three properties of
the node's connected ports, each computed against a fixed tolerance:

- **Collinear pair** — two ports whose directions are ~180° apart (within
  `2.0°`), i.e. they look like one straight through-run.
- **Eccentricity** — the perpendicular distance between two ports' lines.
  Two ports can be collinear (antiparallel directions) but still offset in
  space; eccentricity is what tells a true straight-through connection
  (`eccentricity < 1e-6`) apart from a jog/offset (`eccentricity >= 1e-6`).
- **Coplanar** — true when every pair of connected ports has zero
  eccentricity, i.e. all ports at the node lie in one plane. Any family
  computed on a non-coplanar node gets the `"3d"` qualifier appended.

## `family`: degree -> (family, qualifiers)

Families are generic and profile-independent — no duct-size or
profile-specific logic feeds into this. The only qualifier in use today is
`"3d"`, appended whenever the node's ports are not coplanar.

**Degree 1 (`end`)** — always `terminal`. No geometry to disambiguate a
single port.

**Degree 2 (`through`)**:
- Ports collinear, eccentricity ~0 → `straight`.
- Ports collinear, nonzero eccentricity → `offset` (a jog).
- Ports not collinear, angle ~90° → `bend_90`; otherwise → `bend`.
  `["3d"]` appended if the two ports aren't coplanar.

**Degree 3 (`branch`)**:
- One collinear pair (a trunk run) + one branch port: branch angle to the
  trunk ~90° → `tee`; otherwise → `lateral_tee`. `["3d"]` if not coplanar.
- No collinear pair (all three ports diverge) → `wye`. `["3d"]` if not
  coplanar.
- Any other case (rare/ambiguous geometry) → `generic`.

**Degree 4 (`cross`)**:
- Two independent collinear runs crossing at the node, with the two runs
  roughly orthogonal to each other → `cross`. `["3d"]` if not coplanar.
- One collinear trunk run + two remaining ports that make roughly equal
  angles with the trunk → `double_wye`. `["3d"]` if not coplanar.
- Anything that doesn't cleanly resolve to a two-run cross or a symmetric
  double-wye → `generic`.

**Degree >= 5 (`multiport`)** — always `multiport`. `["3d"]` if not
coplanar. No further family distinction is attempted at this fan-out.

## Matching against type-defs

`topology`, `family`, and any qualifiers are joined into a dotted
`family_key` (e.g. `through.bend_90.3d`, `branch.tee`), which is matched
against a type-def's `family` list as a **prefix** match: a type-def entry
`"through.bend"` matches both `"through.bend"` and `"through.bend.3d"` (any
key starting with `"through.bend."`). This means a type-def can choose to
cover the planar and 3D variants of a family together with one entry, or
list them separately if the two need different geometry/constraints.

A new family string has no effect on its own — it only becomes reachable
once some type-def's `family` list references its dotted key. If a real
duct layout produces a `topology`/`family` combination that doesn't fit any
existing type-def, or a fitting distinction you need doesn't map onto the
degree/collinearity/eccentricity/coplanarity vocabulary above, raise it
rather than working around it in library code — see `CLAUDE.md`'s layering
guidance (core classification logic must stay generic; fitting-specific
behavior belongs in library data).
