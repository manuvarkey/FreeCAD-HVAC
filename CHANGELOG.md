# Changelog

## Compatibility policy

The software is alpha. Until a release is tagged, nothing is kept backward
compatible: documents and libraries made with an earlier version may need
to be rebuilt.

What a library may rely on is fixed and checked by tests:

- the names in `HVACLibraryAPI.PUBLIC` and `HVACLossAPI.PUBLIC`;
- the context keys in `freecad/HVAC/library/contexts.py`;
- the type-def format described in `freecad/HVAC/libraries/README.md`
  (`geometry.backend`, results as `{"layers": ...}`).

A change to any of these raises `HVACLibraryAPI.API_VERSION` or
`HVACLossAPI.API_VERSION`. A library declares the versions it was written
for in `library.json` (`"requires_api"`) and is refused by any other.

## Unreleased (roadmap-stage-0)

API versions: geometry 3, loss 1, PartScript 3.

- Libraries: one type-def form (`"geometry": {"backend": ...}`), one result
  form (`{"layers": ...}`, `HVACLibraryAPI.single_layer` for one solid), one
  context key for property values (`properties`).
- Edge keys are stable across save and reload (sketch geometry ids).
- Networks have a `SystemType`; terminals a `TerminalFunction`.
- HVAC equipment joins networks: an App::Part (the modelled unit) made equipment, its port markers inside it.
- Custom fittings: a user-modelled fitting linked at a node, ducts trimmed to its port markers.
- Equipment and custom fittings are set up in one panel (ports by kind, X/Y offset from a face's centre).
- Sizing changes duct size only at straight-through junctions by default (`SizeChangePolicy`
  `AtStraightJunctions`, `AtInlineNodesOnly`, `EverySegment`); pluggable friction models.
- Automatic fitting selection prefers mitered and straight types; bend radii clamp to half the duct plus the wall.
- Hidden construction layers are left out of the displayed shape instead of drawn transparent.
- SMACNA library: the full builtin_basic type set with sheet-metal walls, insulation and flanges as construction features.
- Result tooltips; per-network layer visibility; per-port values table.
