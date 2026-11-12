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
- `HVACEquipment` joins networks through user-modelled units with port markers.
- Custom fittings: a user-modelled fitting linked at a node, ducts trimmed to its port markers.
- Sizing changes duct size only at reducers (`SizeChangePolicy`); pluggable friction models.
- SMACNA library: the full builtin_basic type set with sheet-metal walls, insulation and flanges as construction features.
- Result tooltips; per-network layer visibility; per-port values table.
