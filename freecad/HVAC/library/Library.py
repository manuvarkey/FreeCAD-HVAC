# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

################################################################################
#                                                                              #
#   Copyright (c) 2026 Francisco Rosa                                          #
#                                                                              #
#   This addon is free software; you can redistribute it and/or modify it      #
#   under the terms of the GNU Lesser General Public License as published      #
#   by the Free Software Foundation; either version 2.1 of the License, or     #
#   (at your option) any later version.                                        #
#                                                                              #
#   This addon is distributed in the hope that it will be useful,              #
#   but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.                       #
#                                                                              #
#   See the GNU Lesser General Public License for more details.                #
#                                                                              #
#   You should have received a copy of the GNU Lesser General Public           #
#   License along with this addon. If not, see https://www.gnu.org/licenses    #
#                                                                              #
################################################################################

# SPDX-License-Identifier: LGPL-2.1-or-later

"""
The library layer: loads type-defs (JSON files describing a segment/junction
type) from disk, decides which type-def best matches a request from
NetworkParser/DuctNetwork sync, and dispatches to the right geometry backend
to build a type's actual shape.

Three main pieces:
  - HVACTypeDef and friends: the in-memory shape of one loaded type-def.
  - HVACLibrary: one loaded library's types, plus the matching/selection
    logic that picks the best type for a given topology/family/profile.
  - HVACLibraryRegistry: owns every loaded HVACLibrary, and is the single
    entry point core code calls into (loading, selection, geometry/loss
    dispatch) -- see freecad/HVAC/utils/hvaclib.py's HVACLibraryService for
    how the rest of the addon reaches this registry.
"""

import importlib
import json
import os
from dataclasses import dataclass, field

import FreeCAD

from .library_api import HVACLibraryAPI
from . import validation
from . import geometry_result
from .construction import ConstructionLayerDef, ConstructionFeatureDef, FeatureContext


@dataclass
class HVACPropertyDef:
    """One user-facing property a type-def declares (name, FreeCAD property type, default, ...)."""
    name: str
    prop_type: str
    group: str = "HVAC"
    description: str = ""
    default: object = None
    editor_mode: int = 0
    required: bool = True
    validation: dict = field(default_factory=dict)


@dataclass
class HVACGeometryDef:
    """Which geometry backend a type-def uses and where to find it (PartScript file / static descriptor)."""
    backend: str = ""
    file: str = ""
    descriptor: str = ""


# Selection kinds a type descriptor may declare under "selection.kind".
SELECTION_KIND_MODEL = "model"
SELECTION_KIND_PLACEHOLDER = "placeholder"
SELECTION_KIND_INLINE = "inline"
VALID_SELECTION_KINDS = {SELECTION_KIND_MODEL, SELECTION_KIND_PLACEHOLDER, SELECTION_KIND_INLINE}


@dataclass
class HVACSelectionDef:
    """
    Registry-driven selection metadata for a type descriptor.

    kind:
        "model"       -- a real, selectable geometry-producing type. Eligible
                          to become a DuctJunction's Primary DuctComponent
                          via automatic (topology, family, profile) matching.
        "placeholder" -- an invisible/marker fallback (never sticky; always
                          re-evaluated on sync so it can upgrade to a model).
        "inline"      -- a user-added-only device (damper, silencer, flex
                          connector, ...). Never automatically selected as a
                          Primary component -- excluded from select_type()'s
                          indexes entirely; only reachable via
                          HVACLibrary.list_inline_types() for the "Add
                          Inline Component" UI action.
    priority:
        Tiebreaker used only when choosing among multiple candidates that are
        otherwise equally specific (same tier: exact-profile model,
        Generic-profile model, or placeholder). Higher wins. Never used to
        replace an already-selected compatible type -- see
        HVACLibraryRegistry.resolve_sticky_type().
    """
    kind: str = SELECTION_KIND_MODEL
    priority: int = 0


@dataclass
class HVACTypeDef:
    """One loaded type-def -- the in-memory form of a library type's JSON file."""
    id: str
    label: str
    category: str
    topology: str
    family: list[str]
    profiles: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    properties: list[HVACPropertyDef] = field(default_factory=list)
    geometry: HVACGeometryDef = field(default_factory=HVACGeometryDef)
    generator_module: str = ""
    generator_function: str = ""
    lengths_module: str = ""
    lengths_function: str = ""
    loss_module: str = ""
    loss_function: str = ""
    selection: HVACSelectionDef = field(default_factory=HVACSelectionDef)
    # Construction layers this type declares (see library/construction.py
    # and the "construction" JSON block below) -- empty for a type that
    # hasn't been migrated to the multilayer model, which build_geometry()
    # treats as a single, roleless implicit layer (geometry_result.normalize()'s
    # legacy {"shape": ...} wrapping). Parsed from the JSON "construction"
    # block's own "layers" array.
    construction: list[ConstructionLayerDef] = field(default_factory=list)
    # Construction features this type declares (localized attachments --
    # flanges, stiffeners, seams, ... -- see library/construction.py).
    # Parsed from the JSON "construction" block's own "features" array.
    features: list[ConstructionFeatureDef] = field(default_factory=list)


@dataclass(frozen=True)
class HVACMatchKey:
    """Index key for HVACLibrary's per-library match indexes."""
    category: str
    topology: str
    family: str
    profile: str


@dataclass(frozen=True)
class HVACTypeMatchRequest:
    """
    What NetworkParser/DuctNetwork sync is asking the registry to match.

    family is the classifier's literal family key (junctions: e.g.
    "branch.tee.3d", from JunctionAnalysis.family_key; segments:
    "straight_segment"/"curved_segment"). context carries whatever
    freecad/HVAC/library/validation.py's context_violations() needs for the
    second-stage constraint check (junctions: "connected_ports"/"topology";
    segments: "profile") -- topology/profile are auto-filled from this
    request's own fields if the caller didn't already put them in context.
    """
    category: str
    topology: str
    family: str
    profile: str
    context: dict = field(default_factory=dict)


@dataclass
class HVACTypeSelection:
    """Result of HVACLibrary/HVACLibraryRegistry.select_type()."""
    library_id: str
    type_def: "HVACTypeDef | None"
    status: str  # "exact" | "generic_profile" | "placeholder" | "retained" | "ambiguous" | "not_found"
    reason: str = ""
    candidates: tuple = ()


def _family_key_related(a, b):
    """
    True if classifier family key `a` and descriptor-declared family entry
    `b` refer to the same or an ancestor/descendant point in the dotted
    family hierarchy (e.g. "branch.tee" and "branch.tee.3d", or
    "end.terminal" and "end.terminal.diffuser").

    This is intentionally NOT used for automatic-selection index lookups
    (those require an exact literal family_key match per type-def author's
    explicit family list -- see HVACLibrary._rebuild_match_index). It's used
    for the second-stage compatibility check (matches_type()/select_type()'s
    per-candidate validation), so that e.g. a manually-selected
    end_diffuser_generic (family=["end.terminal.diffuser", ...]) stays sticky
    even though the classifier can never literally produce anything more
    specific than "end.terminal" for a degree-1 node.
    """
    if a == b:
        return True
    if b.startswith(a + "."):
        return True
    if a.startswith(b + "."):
        return True
    return False


@dataclass
class HVACLibrary:
    """
    One loaded library: its types, plus the indexes and matching logic used
    to pick the best type for a given topology/family/profile request.
    """
    id: str
    label: str
    root_path: str
    generators_package: str = ""
    default_construction: list[ConstructionLayerDef] = field(default_factory=list)
    types_by_id: dict = field(default_factory=dict)
    _index_dirty: bool = field(default=True, repr=False, compare=False)
    _model_match_index: dict = field(default_factory=dict, repr=False, compare=False)
    _placeholder_match_index: dict = field(default_factory=dict, repr=False, compare=False)

    def add_type(self, type_def: HVACTypeDef):
        self.types_by_id[type_def.id] = type_def
        self._index_dirty = True

    def get_type(self, type_id: str):
        return self.types_by_id.get(type_id)

    def _family_match(self, parent, child):
        return child == parent or child.startswith(parent + ".")

    def list_types(self, category=None, topology=None, family=None, profile=None, include_placeholders=True):
        out = []
        for t in self.types_by_id.values():
            if category and t.category != category:
                continue
            if topology and t.topology != topology:
                continue
            if family and not any(self._family_match(family, candidate) for candidate in t.family):
                continue
            if profile and t.profiles and profile not in t.profiles:
                continue
            if not include_placeholders and getattr(t.selection, "kind", SELECTION_KIND_MODEL) == SELECTION_KIND_PLACEHOLDER:
                continue
            out.append(t)
        return out

    def list_profiles(self, category=None, family=None):
        profiles = set()
        for t in self.types_by_id.values():
            if category and t.category != category:
                continue
            if family and not any(self._family_match(family, candidate) for candidate in t.family):
                continue
            for p in (t.profiles or []):
                profiles.add(p)
        return sorted(profiles)

    def default_profile(self, category=None, family=None):
        profiles = self.list_profiles(category=category, family=family)
        return profiles[0] if profiles else ""

    def list_inline_types(self, topology=None, profile=None):
        """
        All selection.kind=="inline" junction types (dampers, silencers,
        flex connectors, ...) compatible with an optional topology/profile
        filter -- for the "Add Inline Component" UI action, not automatic
        Primary-fitting matching (see _rebuild_match_index). No family
        filter: unlike Primary selection, adding an inline component is
        always a direct user choice, so there's no classifier family key to
        match against.
        """
        out = []
        for t in self.types_by_id.values():
            if getattr(t.selection, "kind", SELECTION_KIND_MODEL) != SELECTION_KIND_INLINE:
                continue
            if t.category != "junction":
                continue
            if topology and t.topology not in (topology, "generic"):
                continue
            if profile and t.profiles and "Generic" not in t.profiles and profile not in t.profiles:
                continue
            out.append(t)
        return out

    # ------------------------------------------------------------------
    # Match indexes -- rebuilt lazily whenever a type is added/reloaded.
    # ------------------------------------------------------------------

    def _rebuild_match_index(self):
        """
        Build two lookup tables (real "model" types, and "placeholder"
        fallback types), keyed by (category, topology, family, profile) so
        select_type() can jump straight to the candidates for a request
        instead of scanning every type. A type with no declared profiles is
        indexed under "Generic" (matches any profile).

        "inline"-kind types (see HVACSelectionDef) are deliberately left out
        of both indexes -- they must never be reachable through automatic
        Primary-fitting selection. They're looked up separately, by a plain
        scan, in list_inline_types() below.
        """
        self._model_match_index = {}
        self._placeholder_match_index = {}

        for t in self.types_by_id.values():
            kind = getattr(t.selection, "kind", SELECTION_KIND_MODEL)
            if kind == SELECTION_KIND_INLINE:
                continue
            target = (
                self._model_match_index
                if kind == SELECTION_KIND_MODEL
                else self._placeholder_match_index
            )
            index_profiles = list(t.profiles) if t.profiles else ["Generic"]
            for fam in t.family:
                for prof in index_profiles:
                    key = HVACMatchKey(category=t.category, topology=t.topology, family=fam, profile=prof)
                    target.setdefault(key, []).append(t)

        self._index_dirty = False

    def _ensure_match_index(self):
        if self._index_dirty:
            self._rebuild_match_index()

    @property
    def model_match_index(self):
        self._ensure_match_index()
        return self._model_match_index

    @property
    def placeholder_match_index(self):
        self._ensure_match_index()
        return self._placeholder_match_index

    def reindex(self):
        """Force an immediate rebuild of the match indexes."""
        self._rebuild_match_index()

    # ------------------------------------------------------------------
    # Automatic / compatibility matching
    # ------------------------------------------------------------------

    def _index_candidates(self, index, request: HVACTypeMatchRequest):
        """Return (exact_profile_candidates, generic_profile_candidates)."""
        exact_key = HVACMatchKey(request.category, request.topology, request.family, request.profile)
        exact = list(index.get(exact_key, []))

        generic = []
        if request.profile != "Generic":
            generic_key = HVACMatchKey(request.category, request.topology, request.family, "Generic")
            generic = list(index.get(generic_key, []))

        return exact, generic

    def _compatible(self, type_def: HVACTypeDef, request: HVACTypeMatchRequest):
        if type_def.category != request.category:
            return False
        if not any(_family_key_related(request.family, candidate) for candidate in type_def.family):
            return False

        ctx = dict(request.context or {})
        ctx.setdefault("topology", request.topology)
        ctx.setdefault("profile", request.profile)
        return validation.is_context_valid(type_def, ctx)

    @staticmethod
    def _rank_tier(candidates):
        """
        Return (winner_or_None, is_ambiguous, candidate_ids) for one
        specificity tier, using selection.priority as the tiebreaker.
        Never uses dict/list insertion order to break a tie.
        """
        if not candidates:
            return None, False, ()

        max_priority = max(getattr(t.selection, "priority", 0) for t in candidates)
        top = [t for t in candidates if getattr(t.selection, "priority", 0) == max_priority]
        ids = tuple(t.id for t in candidates)

        if len(top) == 1:
            return top[0], False, ids
        return None, True, tuple(sorted(t.id for t in top))

    def select_type(self, request: HVACTypeMatchRequest, strict=False) -> HVACTypeSelection:
        """
        Choose the best compatible type for `request` in this library.

        Tiers, in priority order (see freecad/HVAC/libraries/README.md):
            1. model,       exact profile match
            2. model,       Generic-profile (wildcard) match
            3. placeholder, exact profile match
            4. placeholder, Generic-profile match

        Within a tier, selection.priority breaks ties; a genuine tie (equal
        highest priority, more than one candidate) is ambiguous -- in
        strict mode this is reported as status="ambiguous"; otherwise a
        console warning is logged and matching falls through to the next
        tier (eventually a placeholder) rather than silently picking one.
        """
        self._ensure_match_index()

        model_exact, model_generic = self._index_candidates(self.model_match_index, request)
        ph_exact, ph_generic = self._index_candidates(self.placeholder_match_index, request)

        tiers = (
            ([t for t in model_exact if self._compatible(t, request)], "exact"),
            ([t for t in model_generic if self._compatible(t, request)], "generic_profile"),
            ([t for t in ph_exact if self._compatible(t, request)], "placeholder"),
            ([t for t in ph_generic if self._compatible(t, request)], "placeholder"),
        )

        for candidates, status in tiers:
            winner, ambiguous, ids = self._rank_tier(candidates)
            if winner is not None:
                return HVACTypeSelection(
                    library_id=self.id, type_def=winner, status=status, reason="", candidates=ids
                )
            if ambiguous:
                reason = (
                    "Ambiguous type selection in library '{}' for category={!r} topology={!r} "
                    "family={!r} profile={!r}: candidates {} tie at the same priority".format(
                        self.id, request.category, request.topology, request.family, request.profile, ids
                    )
                )
                if strict:
                    return HVACTypeSelection(
                        library_id=self.id, type_def=None, status="ambiguous", reason=reason, candidates=ids
                    )
                FreeCAD.Console.PrintWarning("HVAC - " + reason + "\n")
                # Fall through to the next (broader) tier rather than
                # silently picking one of the tied candidates.

        return HVACTypeSelection(
            library_id=self.id,
            type_def=None,
            status="not_found",
            reason=(
                "No compatible model or placeholder type found in library '{}' for "
                "category={!r} topology={!r} family={!r} profile={!r}".format(
                    self.id, request.category, request.topology, request.family, request.profile
                )
            ),
        )

    def matches_type(self, type_def, request: HVACTypeMatchRequest) -> bool:
        """Does this already-selected type remain valid for `request`?"""
        if type_def is None:
            return False
        return self._compatible(type_def, request)


class HVACLibraryRegistry:
    """
    Owns every loaded HVACLibrary and is the single entry point the rest of
    the addon calls into: loading libraries from disk, type selection, and
    dispatching to a type's geometry/loss backend.
    """

    def __init__(self):
        self._libraries = {}
        self._active_library_id = None
        self._loaded = False
        self._search_paths = []

    def clear(self):
        self._libraries = {}
        self._active_library_id = None
        self._loaded = False

    def register_library(self, library: HVACLibrary):
        self._libraries[library.id] = library
        if self._active_library_id is None:
            self._active_library_id = library.id

    def get_library(self, library_id: str):
        return self._libraries.get(library_id)

    def list_libraries(self):
        return list(self._libraries.values())

    def set_active_library(self, library_id: str):
        if library_id in self._libraries:
            self._active_library_id = library_id
            return True
        return False

    def get_active_library(self):
        if self._active_library_id is None:
            return None
        return self._libraries.get(self._active_library_id)

    def resolve_type(self, library_id: str, type_id: str):
        """Exact TypeId lookup. Never fuzzy -- geometry execution relies on this."""
        lib = self.get_library(library_id)
        if lib is None:
            return None
        return lib.get_type(type_id)

    def select_type(self, library_id: str, request: HVACTypeMatchRequest, strict=False) -> HVACTypeSelection:
        """Automatic selection: choose the best compatible type in `library_id`."""
        lib = self.get_library(library_id)
        if lib is None:
            return HVACTypeSelection(
                library_id=library_id,
                type_def=None,
                status="not_found",
                reason="Unknown HVAC library '{}'".format(library_id),
            )
        return lib.select_type(request, strict=strict)

    def matches_type(self, library_id: str, type_id: str, request: HVACTypeMatchRequest) -> bool:
        """Compatibility check: does `type_id` in `library_id` remain valid for `request`?"""
        lib = self.get_library(library_id)
        if lib is None:
            return False
        return lib.matches_type(lib.get_type(type_id), request)

    def list_inline_types(self, library_id: str, topology=None, profile=None):
        """See HVACLibrary.list_inline_types."""
        lib = self.get_library(library_id)
        if lib is None:
            return []
        return lib.list_inline_types(topology=topology, profile=profile)

    def resolve_sticky_type(
        self, library_id: str, current_type_id: str, request: HVACTypeMatchRequest, strict=False
    ) -> HVACTypeSelection:
        """
        Normal (non-reset) synchronization policy:

            current TypeId exists, resolves in library_id, is a real model
            (not a placeholder), and remains compatible with `request`?
                -> retain it (status="retained")
            otherwise
                -> automatic selection (select_type)

        Placeholders are deliberately never retained here -- they're
        re-evaluated every sync so they can upgrade to a real model.
        """
        lib = self.get_library(library_id)
        if lib is not None and current_type_id:
            current_type = lib.get_type(current_type_id)
            if current_type is not None:
                kind = getattr(current_type.selection, "kind", SELECTION_KIND_MODEL)
                # Defense-in-depth: inline types are never assigned as a
                # Primary component's current_type_id in practice (the UI
                # never offers them there), but treat them the same as
                # placeholders here anyway rather than relying solely on
                # that invariant.
                if kind not in (SELECTION_KIND_PLACEHOLDER, SELECTION_KIND_INLINE) and lib.matches_type(current_type, request):
                    return HVACTypeSelection(
                        library_id=library_id,
                        type_def=current_type,
                        status="retained",
                        reason="Current type remains compatible",
                        candidates=(current_type_id,),
                    )

        return self.select_type(library_id, request, strict=strict)

    def import_generator(self, library_id: str, module_name: str):
        lib = self.get_library(library_id)
        if lib is None:
            raise ValueError("Unknown HVAC library '{}'".format(library_id))
        if not lib.generators_package:
            raise ValueError("HVAC library '{}' has no generators_package".format(library_id))
        full_module = "{}.{}".format(lib.generators_package, module_name)
        return importlib.import_module(full_module)

    def resolve_library_file(self, library_id: str, relative_path: str):
        lib = self.get_library(library_id)
        if lib is None:
            raise ValueError("Unknown HVAC library '{}'".format(library_id))
        root = os.path.realpath(lib.root_path)
        path = os.path.realpath(os.path.join(root, relative_path))
        if os.path.commonpath([root, path]) != root:
            raise ValueError("Library path escapes library root: '{}'".format(relative_path))
        return path

    def resolve_params(self, type_def: HVACTypeDef, obj=None, supplied=None):
        return validation.resolve_params(type_def, obj=obj, supplied=supplied)

    def _prepare_geometry_context(self, type_def: HVACTypeDef, context: dict):
        prepared = dict(context or {})
        prepared["hvac_api"] = HVACLibraryAPI
        prepared["hvac_api_version"] = HVACLibraryAPI.API_VERSION

        params = prepared.get("params")
        if params is None:
            params = self.resolve_params(
                type_def,
                obj=prepared.get("obj"),
                supplied=prepared.get("properties"),
            )
        prepared["params"] = dict(params or {})

        # Backward compatibility for existing generators/loss helpers.
        prepared["properties"] = prepared["params"]
        validation.validate_context(type_def, prepared)
        return prepared

    def build_geometry(self, library_id: str, type_def: HVACTypeDef, context: dict):
        """
        Build a type's geometry by dispatching to whichever of the three
        supported geometry backends it declares: "partscript" (a Python
        script under the library), "static" (a pre-built BREP/STEP file
        plus a placement descriptor), or -- if neither is set -- the legacy
        generator_module/generator_function pair. Whatever the backend
        returns (a legacy {"shape": ...} dict or a new-style
        {"layers": {...}} dict) is normalized here, once, into a real
        GeometryResult -- every caller downstream (DuctSegment/DuctComponent/
        DuctJunction) only ever sees a GeometryResult, never a raw dict.

        Each returned layer's `roles` is then stamped on from the type-def's
        own declared construction (matched by layer id) -- this is the one
        place a raw geometry dict and the type-def's construction defs are
        both in scope together, so it's where role vocabulary gets attached.
        """
        context = self._prepare_geometry_context(type_def, context)
        geometry = getattr(type_def, "geometry", None)
        backend = str(getattr(geometry, "backend", "") or "").lower()

        if backend == "partscript":
            from . import partscript_shapes
            script_path = self.resolve_library_file(library_id, geometry.file)
            raw = partscript_shapes.execute_partscript(script_path, context)
        elif backend == "static":
            from . import static_shapes
            descriptor_path = self.resolve_library_file(library_id, geometry.descriptor)
            raw = static_shapes.build_static_geometry(descriptor_path, context)
        elif backend:
            raise ValueError(
                "Type '{}' uses unsupported geometry backend '{}'".format(type_def.id, backend)
            )
        elif type_def.generator_module and type_def.generator_function:
            # Legacy generator backend.
            module = self.import_generator(library_id, type_def.generator_module)
            func = getattr(module, type_def.generator_function)
            raw = func(context)
        else:
            raise ValueError("Type '{}' has no geometry definition".format(type_def.id))

        result = geometry_result.normalize(raw)

        layer_defs_by_id = {ldef.id: ldef for ldef in getattr(type_def, "construction", []) or []}
        for layer_id, layer_geometry in result.layers.items():
            layer_def = layer_defs_by_id.get(layer_id)
            if layer_def is not None:
                layer_geometry.roles = list(layer_def.roles)

        self._build_features(library_id, type_def, context, result)

        return result

    def _build_features(self, library_id: str, type_def: HVACTypeDef, context: dict, result):
        """
        Second pass, after the backend's own layers are built and
        normalized: resolve and invoke each of the type-def's own declared
        construction.features generators, storing each enabled one's
        returned Part.Shape into result.features. A feature's own
        enabled_parameter/visible_parameter/parameters only ever reference
        property names already resolved into context["params"] by
        _prepare_geometry_context() above -- no second parameter
        resolution, per the "existing parameter system, unchanged" rule.

        Unlike geometry backends (partscript/static/legacy generator, one
        per type, declared via type_def.geometry/generator_module), feature
        generators always live in one fixed, conventional module --
        <library's generators_package>.features -- resolved the same way
        generator_module/loss_module already are (import_generator()), so a
        PartScript-backed type (which has no generator_module of its own)
        still has a well-defined place for its own feature generators.
        """
        feature_defs = getattr(type_def, "features", None) or []
        if not feature_defs:
            return

        params = dict(context.get("params", {}) or {})
        features_module = None

        for feature_def in feature_defs:
            enabled = True
            if feature_def.enabled_parameter:
                enabled = bool(params.get(feature_def.enabled_parameter, True))
            if not enabled:
                continue

            host_layer_geometry = result.layers.get(feature_def.host_layer)
            if host_layer_geometry is None:
                raise ValueError(
                    "Feature '{}' on type '{}' references host_layer '{}', which this "
                    "type's geometry backend never returned".format(
                        feature_def.id, type_def.id, feature_def.host_layer
                    )
                )

            visible = True
            if feature_def.visible_parameter:
                visible = bool(params.get(feature_def.visible_parameter, True))

            feature_params = {name: params.get(name) for name in feature_def.parameters}

            ctx = FeatureContext(
                parameters=feature_params,
                host_layer=host_layer_geometry,
                context=context,
            )

            if features_module is None:
                features_module = self.import_generator(library_id, "features")
            generator_func = getattr(features_module, feature_def.generator)

            shape = generator_func(HVACLibraryAPI, ctx)

            result.features[feature_def.id] = geometry_result.FeatureGeometry(
                shape=shape,
                role=feature_def.role,
                visible=visible,
            )

    def call_generator(self, library_id: str, type_def: HVACTypeDef, context: dict):
        return self.build_geometry(library_id, type_def, context)

    def call_loss(self, library_id: str, type_def: HVACTypeDef, context: dict):
        """
        Call the type's optional fitting-loss function. Returns one of:
          - dict {edge_key: K}: per-port dimensionless loss coefficients, each
            already referenced to that port's own velocity. Required for
            junctions where different legs have physically distinct
            coefficients (e.g. a converging/merging tee, where each inlet leg
            has its own loss).
          - float: a single dimensionless loss coefficient applied uniformly
            to every outlet port (simple contract, fine for junctions with
            one meaningfully distinct downstream condition).
          - None: the type has no loss function wired up (caller should apply
            a fallback coefficient).
        """
        if not type_def.loss_module or not type_def.loss_function:
            return None
        module = self.import_generator(library_id, type_def.loss_module)
        func = getattr(module, type_def.loss_function, None)
        if func is None:
            return None
        context["hvac_api"] = HVACLibraryAPI
        context["hvac_api_version"] = HVACLibraryAPI.API_VERSION
        return func(context)

    def set_search_paths(self, paths):
        self._search_paths = [p for p in (paths or []) if p]

    def add_search_path(self, path):
        if path and path not in self._search_paths:
            self._search_paths.append(path)

    def ensure_loaded(self):
        """Load libraries once, on first use -- a no-op on every call after the first."""
        if self._loaded:
            return

        self.scan_paths()

        if not self._libraries:
            FreeCAD.Console.PrintError(
                "HVAC - No HVAC libraries found in configured search paths.\n"
            )

        self._loaded = True

    def scan_paths(self):
        """Forget every previously-loaded library and scan all search paths again from scratch."""
        self._libraries = {}
        self._active_library_id = None

        for root in self._search_paths:
            self.scan_path(root)

    def scan_path(self, root_path):
        """Treat every subfolder of root_path as one candidate library and try to load it."""
        if not root_path or not os.path.isdir(root_path):
            return

        for entry in sorted(os.listdir(root_path)):
            lib_dir = os.path.join(root_path, entry)
            if not os.path.isdir(lib_dir):
                continue
            try:
                lib = self.load_library_from_folder(lib_dir)
                if lib is not None:
                    self.register_library(lib)
            except Exception as e:
                # One bad library shouldn't block the others from loading.
                FreeCAD.Console.PrintWarning(
                    "HVAC - Failed to load library from '{}': {}\n".format(lib_dir, e)
                )

    def reload(self):
        self._loaded = False
        self.ensure_loaded()

    def load_library_from_folder(self, lib_dir):
        """Load one library from a folder: its library.json manifest, then every type-def under type_roots."""
        manifest_path = os.path.join(lib_dir, "library.json")
        if not os.path.isfile(manifest_path):
            return None

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        lib_id = manifest["id"]
        label = manifest.get("label", lib_id)
        generators_package = manifest.get("generators_package", "")
        type_roots = manifest.get("type_roots", ["types"])
        default_construction = self._parse_construction_layers(
            (manifest.get("default_construction", {}) or {}).get("layers", [])
        )

        library = HVACLibrary(
            id=lib_id,
            label=label,
            root_path=lib_dir,
            generators_package=generators_package,
            default_construction=default_construction,
        )

        for rel_root in type_roots:
            abs_root = os.path.join(lib_dir, rel_root)
            self._load_type_defs_from_tree(abs_root, library)

        return library

    def _load_type_defs_from_tree(self, root_dir, library):
        """Recursively load every *.json type-def file under root_dir into library."""
        if not os.path.isdir(root_dir):
            return

        for dirpath, _, filenames in os.walk(root_dir):
            for fn in filenames:
                if not fn.lower().endswith(".json"):
                    continue
                fpath = os.path.join(dirpath, fn)
                type_def = self._load_type_def_file(fpath)
                if not type_def.construction:
                    type_def.construction = [
                        ConstructionLayerDef(
                            id=layer.id,
                            roles=list(layer.roles),
                            default_material_role=layer.default_material_role,
                            default_material_uuid=layer.default_material_uuid,
                            thickness_property=layer.thickness_property,
                        )
                        for layer in library.default_construction
                    ]
                library.add_type(type_def)

    @staticmethod
    def _parse_construction_layers(layers_raw):
        return [
            ConstructionLayerDef(
                id=layer_raw["id"],
                roles=list(layer_raw.get("roles", []) or []),
                default_material_role=layer_raw.get("default_material_role"),
                default_material_uuid=layer_raw.get("default_material_uuid"),
                thickness_property=layer_raw.get("thickness_property"),
            )
            for layer_raw in (layers_raw or [])
        ]

    def _load_type_def_file(self, filepath):
        """Parse one type-def JSON file into an HVACTypeDef -- a plain field-by-field mapping."""
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)

        props = []
        for p in raw.get("properties", []) or []:
            props.append(
                HVACPropertyDef(
                    name=p["name"],
                    prop_type=p["prop_type"],
                    group=p.get("group", "HVAC"),
                    description=p.get("description", ""),
                    default=p.get("default", None),
                    editor_mode=int(p.get("editor_mode", 0)),
                    required=bool(p.get("required", True)),
                    validation=dict(p.get("validation", {}) or {}),
                )
            )

        gen = raw.get("generator", {}) or {}
        lengths = raw.get("connection_lengths", {}) or {}
        loss = raw.get("loss", {}) or {}
        geometry_raw = raw.get("geometry", {}) or {}
        geometry = HVACGeometryDef(
            backend=geometry_raw.get("backend", ""),
            file=geometry_raw.get("file", ""),
            descriptor=geometry_raw.get("descriptor", ""),
        )

        family = raw["family"]
        if isinstance(family, str):
            family = [family]

        selection_raw = raw.get("selection", {}) or {}
        selection_kind = str(selection_raw.get("kind", SELECTION_KIND_MODEL) or SELECTION_KIND_MODEL)
        if selection_kind not in VALID_SELECTION_KINDS:
            raise ValueError(
                "Type '{}' in '{}' has invalid selection.kind '{}'; expected one of {}".format(
                    raw.get("id", "?"), filepath, selection_kind, sorted(VALID_SELECTION_KINDS)
                )
            )
        selection = HVACSelectionDef(
            kind=selection_kind,
            priority=int(selection_raw.get("priority", 0) or 0),
        )

        # "construction" is an object with its own "layers"/"features"
        # arrays (see freecad/HVAC/libraries/README.md's "Construction
        # layers"/"Construction features" sections) -- both optional, a
        # type-def with no "construction" block at all keeps behaving as a
        # single, roleless implicit layer with no features.
        construction_raw = raw.get("construction", {}) or {}

        construction = self._parse_construction_layers(construction_raw.get("layers", []))

        features = []
        for feature_raw in construction_raw.get("features", []) or []:
            features.append(
                ConstructionFeatureDef(
                    id=feature_raw["id"],
                    role=feature_raw.get("role", ""),
                    host_layer=feature_raw.get("host_layer", ""),
                    generator=feature_raw.get("generator", ""),
                    enabled_parameter=feature_raw.get("enabled_parameter"),
                    visible_parameter=feature_raw.get("visible_parameter"),
                    parameters=list(feature_raw.get("parameters", []) or []),
                )
            )

        return HVACTypeDef(
            id=raw["id"],
            label=raw.get("label", raw["id"]),
            category=raw["category"],
            topology=raw.get("topology", "generic"),
            family=family,
            profiles=list(raw.get("profiles", []) or []),
            constraints=dict(raw.get("constraints", {}) or {}),
            properties=props,
            geometry=geometry,
            generator_module=gen.get("module", ""),
            generator_function=gen.get("function", ""),
            lengths_module=lengths.get("module", ""),
            lengths_function=lengths.get("function", ""),
            loss_module=loss.get("module", ""),
            loss_function=loss.get("function", ""),
            selection=selection,
            construction=construction,
            features=features,
        )
