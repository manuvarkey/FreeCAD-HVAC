"""
Tests for the registry-driven library type-matching engine: descriptor
selection metadata (HVACSelectionDef), per-library match indexes, automatic
selection (select_type), compatibility checks (matches_type), and the sticky
current-selection policy (resolve_sticky_type) -- see
freecad/HVAC/library/Library.py and freecad/HVAC/libraries/README.md.
"""
import json
import os

import conftest  # noqa: F401 -- installs FreeCAD/FreeCADGui/Part/PySide stubs

from freecad.HVAC.library.Library import (
    HVACLibrary,
    HVACLibraryRegistry,
    HVACSelectionDef,
    HVACTypeDef,
    HVACTypeMatchRequest,
)
from freecad.HVAC.utils import hvaclib

LIBRARIES_ROOT = os.path.join(os.path.dirname(__file__), "..", "freecad", "HVAC", "libraries")


def _type_def(id_, category, family, topology="generic", profiles=None, constraints=None, kind="model", priority=0):
    return HVACTypeDef(
        id=id_,
        label=id_,
        category=category,
        topology=topology,
        family=family,
        profiles=list(profiles or []),
        constraints=dict(constraints or {}),
        selection=HVACSelectionDef(kind=kind, priority=priority),
    )


def _library_with(*type_defs):
    lib = HVACLibrary(id="lib", label="Lib", root_path="", generators_package="")
    for t in type_defs:
        lib.add_type(t)
    return lib


def _ports(n, profile="Circular"):
    return [{"profile": profile} for _ in range(n)]


def _junction_request(topology, family, profile, ports):
    return HVACTypeMatchRequest(
        category="junction",
        topology=topology,
        family=family,
        profile=profile,
        context={"connected_ports": ports, "topology": topology},
    )


def _segment_request(family, profile):
    return HVACTypeMatchRequest(
        category="segment", topology="generic", family=family, profile=profile, context={"profile": profile}
    )


# ----------------------------------------------------------------------
# Descriptor loading
# ----------------------------------------------------------------------

def test_selection_defaults_when_missing_from_json(tmp_path):
    type_file = tmp_path / "through_generic.json"
    type_file.write_text(json.dumps({
        "id": "through_generic",
        "label": "Through Generic",
        "category": "junction",
        "topology": "through",
        "family": ["through.straight"],
        "profiles": ["Generic"],
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.selection.kind == "model"
    assert type_def.selection.priority == 0


def test_selection_explicit_model_metadata_loads(tmp_path):
    type_file = tmp_path / "branch_tee_generic.json"
    type_file.write_text(json.dumps({
        "id": "branch_tee_generic",
        "label": "Tee",
        "category": "junction",
        "topology": "branch",
        "family": ["branch.tee"],
        "profiles": ["Circular"],
        "selection": {"kind": "model", "priority": 50},
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.selection.kind == "model"
    assert type_def.selection.priority == 50


def test_selection_explicit_placeholder_metadata_loads(tmp_path):
    type_file = tmp_path / "branch_marker.json"
    type_file.write_text(json.dumps({
        "id": "branch_marker",
        "label": "Marker",
        "category": "junction",
        "topology": "branch",
        "family": ["branch.tee"],
        "profiles": ["Generic"],
        "selection": {"kind": "placeholder", "priority": 0},
    }))

    reg = HVACLibraryRegistry()
    type_def = reg._load_type_def_file(str(type_file))

    assert type_def.selection.kind == "placeholder"


def test_invalid_selection_kind_raises(tmp_path):
    type_file = tmp_path / "bad.json"
    type_file.write_text(json.dumps({
        "id": "bad",
        "label": "Bad",
        "category": "junction",
        "topology": "branch",
        "family": ["branch.tee"],
        "selection": {"kind": "not_a_real_kind"},
    }))

    reg = HVACLibraryRegistry()
    try:
        reg._load_type_def_file(str(type_file))
    except ValueError as exc:
        assert "not_a_real_kind" in str(exc)
    else:
        raise AssertionError("Expected invalid selection.kind to raise")


# ----------------------------------------------------------------------
# Indexing
# ----------------------------------------------------------------------

def test_model_match_index_expands_multiple_families_and_profiles():
    t = _type_def(
        "branch_tee_generic", "junction",
        family=["branch.tee", "branch.tee.3d"],
        topology="branch",
        profiles=["Circular", "Rectangular"],
    )
    lib = _library_with(t)

    index = lib.model_match_index
    for fam in ("branch.tee", "branch.tee.3d"):
        for prof in ("Circular", "Rectangular"):
            key = [k for k in index if k.family == fam and k.profile == prof]
            assert len(key) == 1
            assert index[key[0]] == [t]


def test_generic_profile_indexed_as_wildcard():
    t = _type_def("through_marker", "junction", family=["through.bend"], topology="through", profiles=["Generic"], kind="placeholder")
    lib = _library_with(t)

    index = lib.placeholder_match_index
    matches = [k for k in index if k.family == "through.bend" and k.profile == "Generic"]
    assert len(matches) == 1


def test_empty_profiles_list_indexed_as_generic_wildcard_too():
    # Existing repo semantics: profiles=[] is profile-independent, same as
    # ["Generic"] -- see validation.py / libraries/README.md.
    t = _type_def("multiport_generic", "junction", family=["multiport.multiport"], topology="multiport", profiles=[])
    lib = _library_with(t)

    index = lib.model_match_index
    matches = [k for k in index if k.family == "multiport.multiport" and k.profile == "Generic"]
    assert len(matches) == 1


def test_indexes_isolated_per_library():
    t1 = _type_def("a", "segment", family=["straight_segment"], profiles=["Circular"])
    t2 = _type_def("b", "segment", family=["straight_segment"], profiles=["Circular"])
    lib1 = _library_with(t1)
    lib2 = _library_with(t2)

    ids1 = {t.id for cands in lib1.model_match_index.values() for t in cands}
    ids2 = {t.id for cands in lib2.model_match_index.values() for t in cands}
    assert ids1 == {"a"}
    assert ids2 == {"b"}


def test_reindex_after_add_type_reflects_new_type_not_stale():
    lib = _library_with()
    lib._ensure_match_index()  # force an initial (empty) index build
    assert lib.model_match_index == {}

    t = _type_def("new_type", "segment", family=["straight_segment"], profiles=["Circular"])
    lib.add_type(t)

    ids = {tt.id for cands in lib.model_match_index.values() for tt in cands}
    assert ids == {"new_type"}


# ----------------------------------------------------------------------
# Automatic selection (select_type)
# ----------------------------------------------------------------------

def test_select_type_exact_family_and_profile_match():
    t = _type_def("circular_straight", "segment", family=["straight_segment"], profiles=["Circular"])
    lib = _library_with(t)

    selection = lib.select_type(_segment_request("straight_segment", "Circular"))
    assert selection.status == "exact"
    assert selection.type_def.id == "circular_straight"


def test_select_type_generic_profile_fallback():
    t = _type_def("through_generic", "junction", family=["through.bend"], topology="through", profiles=["Generic"])
    lib = _library_with(t)

    selection = lib.select_type(_junction_request("through", "through.bend", "Circular", _ports(2, "Circular")))
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "through_generic"


def test_select_type_prefers_exact_profile_over_generic():
    generic = _type_def("through_generic", "junction", family=["through.bend"], topology="through", profiles=["Generic"], priority=999)
    exact = _type_def("through_elbow_generic", "junction", family=["through.bend"], topology="through", profiles=["Circular"], priority=0)
    lib = _library_with(generic, exact)

    selection = lib.select_type(_junction_request("through", "through.bend", "Circular", _ports(2, "Circular")))
    assert selection.status == "exact"
    assert selection.type_def.id == "through_elbow_generic"


def test_select_type_prefers_model_over_placeholder():
    marker = _type_def("through_marker", "junction", family=["through.bend"], topology="through", profiles=["Generic"], kind="placeholder", priority=999)
    model = _type_def("through_elbow_generic", "junction", family=["through.bend"], topology="through", profiles=["Circular"], priority=0)
    lib = _library_with(marker, model)

    selection = lib.select_type(_junction_request("through", "through.bend", "Circular", _ports(2, "Circular")))
    assert selection.type_def.id == "through_elbow_generic"
    assert selection.status == "exact"


def test_select_type_priority_resolves_overlapping_candidates():
    broad = _type_def("branch_generic", "junction", family=["branch.tee", "branch.wye"], topology="branch", profiles=["Circular"], priority=10)
    specific = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    lib = _library_with(broad, specific)

    selection = lib.select_type(_junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular")))
    assert selection.type_def.id == "branch_tee_generic"


def test_select_type_tied_candidates_are_reported_ambiguous_not_insertion_order():
    a = _type_def("b_type", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    b = _type_def("a_type", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    lib = _library_with(a, b)

    selection = lib.select_type(_junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular")), strict=True)
    assert selection.status == "ambiguous"
    assert set(selection.candidates) == {"a_type", "b_type"}


def test_select_type_ambiguous_falls_through_to_placeholder_when_not_strict():
    a = _type_def("b_type", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    b = _type_def("a_type", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    marker = _type_def("branch_marker", "junction", family=["branch.tee"], topology="branch", profiles=["Generic"], kind="placeholder")
    lib = _library_with(a, b, marker)

    selection = lib.select_type(_junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular")), strict=False)
    assert selection.status == "placeholder"
    assert selection.type_def.id == "branch_marker"


def test_select_type_no_model_falls_back_to_placeholder():
    marker = _type_def("through_marker", "junction", family=["through.bend"], topology="through", profiles=["Generic"], kind="placeholder")
    lib = _library_with(marker)

    selection = lib.select_type(_junction_request("through", "through.bend", "Circular", _ports(2, "Circular")))
    assert selection.status == "placeholder"
    assert selection.type_def.id == "through_marker"


def test_select_type_unsupported_family_returns_not_found_with_no_placeholder():
    t = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"])
    lib = _library_with(t)

    selection = lib.select_type(_junction_request("cross", "cross.cross", "Circular", _ports(4, "Circular")))
    assert selection.status == "not_found"
    assert selection.type_def is None


# ----------------------------------------------------------------------
# Constraints
# ----------------------------------------------------------------------

def test_select_type_rejects_degree_mismatch():
    t = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], constraints={"degree": 3})
    lib = _library_with(t)

    # Only 2 connected ports -- degree constraint requires 3.
    selection = lib.select_type(_junction_request("branch", "branch.tee", "Circular", _ports(2, "Circular")))
    assert selection.type_def is None


def test_select_type_rejects_topology_mismatch():
    t = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], constraints={"degree": 3})
    lib = _library_with(t)

    selection = lib.select_type(_junction_request("through", "branch.tee", "Circular", _ports(3, "Circular")))
    assert selection.type_def is None


def test_select_type_rejects_connected_port_profile_mismatch():
    t = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], constraints={"degree": 3})
    lib = _library_with(t)

    ports = _ports(2, "Circular") + [{"profile": "Rectangular"}]
    selection = lib.select_type(_junction_request("branch", "branch.tee", "Mixed", ports))
    assert selection.type_def is None


def test_matches_type_and_select_type_agree_on_compatibility():
    t = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], constraints={"degree": 3})
    lib = _library_with(t)

    compatible_request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    incompatible_request = _junction_request("through", "through.bend", "Circular", _ports(2, "Circular"))

    selection = lib.select_type(compatible_request)
    assert selection.type_def is t
    assert lib.matches_type(t, compatible_request) is True
    assert lib.matches_type(t, incompatible_request) is False


# ----------------------------------------------------------------------
# Sticky current-selection policy (resolve_sticky_type)
# ----------------------------------------------------------------------

def _registry_with(*type_defs):
    lib = _library_with(*type_defs)
    reg = HVACLibraryRegistry()
    reg.register_library(lib)
    return reg


def test_resolve_sticky_type_retains_compatible_current_model():
    preferred = _type_def("tee_smacna", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    current = _type_def("tee_long_radius", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=10)
    reg = _registry_with(preferred, current)

    request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    selection = reg.resolve_sticky_type("lib", "tee_long_radius", request)

    assert selection.status == "retained"
    assert selection.type_def.id == "tee_long_radius"


def test_resolve_sticky_type_reselects_on_topology_conflict():
    tee = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    elbow = _type_def("through_elbow_generic", "junction", family=["through.bend_90"], topology="through", profiles=["Circular"], priority=50)
    reg = _registry_with(tee, elbow)

    request = _junction_request("through", "through.bend_90", "Circular", _ports(2, "Circular"))
    selection = reg.resolve_sticky_type("lib", "branch_tee_generic", request)

    assert selection.status != "retained"
    assert selection.type_def.id == "through_elbow_generic"


def test_resolve_sticky_type_reselects_on_family_conflict():
    tee = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    wye = _type_def("branch_wye_generic", "junction", family=["branch.wye"], topology="branch", profiles=["Circular"], priority=10)
    reg = _registry_with(tee, wye)

    request = _junction_request("branch", "branch.wye", "Circular", _ports(3, "Circular"))
    selection = reg.resolve_sticky_type("lib", "branch_tee_generic", request)

    assert selection.status != "retained"
    assert selection.type_def.id == "branch_wye_generic"


def test_resolve_sticky_type_reselects_on_profile_conflict():
    circ = _type_def("circular_straight", "segment", family=["straight_segment"], profiles=["Circular"])
    rect = _type_def("rectangular_straight", "segment", family=["straight_segment"], profiles=["Rectangular"])
    reg = _registry_with(circ, rect)

    request = _segment_request("straight_segment", "Rectangular")
    selection = reg.resolve_sticky_type("lib", "circular_straight", request)

    assert selection.status != "retained"
    assert selection.type_def.id == "rectangular_straight"


def test_resolve_sticky_type_reselects_on_constraint_conflict():
    tee = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], constraints={"degree": 3})
    marker = _type_def("branch_marker", "junction", family=["branch.tee"], topology="branch", profiles=["Generic"], kind="placeholder")
    reg = _registry_with(tee, marker)

    # Degree drops to 2 (no longer a real 3-port tee) -- current tee type
    # violates its own degree constraint now.
    request = _junction_request("branch", "branch.tee", "Circular", _ports(2, "Circular"))
    selection = reg.resolve_sticky_type("lib", "branch_tee_generic", request)

    assert selection.status != "retained"


def test_resolve_sticky_type_reevaluates_current_placeholder():
    marker = _type_def("branch_marker", "junction", family=["branch.tee"], topology="branch", profiles=["Generic"], kind="placeholder")
    model = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    reg = _registry_with(marker, model)

    request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    selection = reg.resolve_sticky_type("lib", "branch_marker", request)

    # A currently-selected placeholder must never be treated as sticky --
    # it's re-evaluated and upgrades to the real model.
    assert selection.status != "retained"
    assert selection.type_def.id == "branch_tee_generic"


def test_resolve_sticky_type_no_current_type_runs_automatic_selection():
    model = _type_def("branch_tee_generic", "junction", family=["branch.tee"], topology="branch", profiles=["Circular"], priority=50)
    reg = _registry_with(model)

    request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    selection = reg.resolve_sticky_type("lib", "", request)

    assert selection.status == "exact"
    assert selection.type_def.id == "branch_tee_generic"


# ----------------------------------------------------------------------
# Segments: straight vs curved family
# ----------------------------------------------------------------------

def test_select_type_segment_straight_family():
    straight = _type_def("circular_straight", "segment", family=["straight_segment"], profiles=["Circular"])
    curved = _type_def("circular_generic", "segment", family=["curved_segment"], profiles=["Circular"])
    lib = _library_with(straight, curved)

    selection = lib.select_type(_segment_request("straight_segment", "Circular"))
    assert selection.type_def.id == "circular_straight"


def test_select_type_segment_curved_family():
    straight = _type_def("circular_straight", "segment", family=["straight_segment"], profiles=["Circular"])
    curved = _type_def("circular_generic", "segment", family=["curved_segment"], profiles=["Circular"])
    lib = _library_with(straight, curved)

    selection = lib.select_type(_segment_request("curved_segment", "Circular"))
    assert selection.type_def.id == "circular_generic"


def test_resolve_sticky_type_segment_manual_selection_remains_sticky():
    # Two compatible circular-straight models; user manually picked the
    # lower-priority one -- it must not be replaced by the higher-priority
    # default on normal sync.
    default_model = _type_def("circular_straight_smacna", "segment", family=["straight_segment"], profiles=["Circular"], priority=50)
    manual_model = _type_def("circular_straight_custom", "segment", family=["straight_segment"], profiles=["Circular"], priority=10)
    reg = _registry_with(default_model, manual_model)

    request = _segment_request("straight_segment", "Circular")
    selection = reg.resolve_sticky_type("lib", "circular_straight_custom", request)

    assert selection.status == "retained"
    assert selection.type_def.id == "circular_straight_custom"


# ----------------------------------------------------------------------
# hvaclib.HVACLibraryService.match_profile_from_ports
# ----------------------------------------------------------------------

def test_match_profile_from_ports_homogeneous_circular():
    ports = _ports(3, "Circular")
    assert hvaclib.HVACLibraryService.match_profile_from_ports(ports) == "Circular"


def test_match_profile_from_ports_mixed():
    ports = _ports(2, "Circular") + [{"profile": "Rectangular"}]
    assert hvaclib.HVACLibraryService.match_profile_from_ports(ports) == "Mixed"


def test_match_profile_from_ports_no_known_profiles():
    ports = [{"profile": ""}, {}]
    assert hvaclib.HVACLibraryService.match_profile_from_ports(ports) == ""


def test_match_profile_from_ports_empty_list():
    assert hvaclib.HVACLibraryService.match_profile_from_ports([]) == ""


# ----------------------------------------------------------------------
# Bundled library audit: no unresolved top-ranked ambiguity, and the two
# real overlaps identified during descriptor priority auditing resolve to
# the intended winner (see freecad/HVAC/libraries/README.md).
# ----------------------------------------------------------------------

def _load_bundled_registry():
    reg = HVACLibraryRegistry()
    reg.set_search_paths([LIBRARIES_ROOT])
    reg.ensure_loaded()
    return reg


def test_bundled_libraries_have_no_unresolved_priority_ties():
    reg = _load_bundled_registry()
    for lib in reg.list_libraries():
        for index in (lib.model_match_index, lib.placeholder_match_index):
            for key, candidates in index.items():
                if len(candidates) < 2:
                    continue
                max_priority = max(c.selection.priority for c in candidates)
                top = [c for c in candidates if c.selection.priority == max_priority]
                assert len(top) == 1, (
                    "Ambiguous automatic selection in library '{}' for {}: {} tie at priority {}".format(
                        lib.id, key, sorted(c.id for c in top), max_priority
                    )
                )


def test_bundled_builtin_basic_branch_tee_prefers_specific_tee_model():
    reg = _load_bundled_registry()
    request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    selection = reg.select_type("builtin_basic", request, strict=True)
    assert selection.type_def.id == "branch_tee_generic"


def test_bundled_builtin_basic_branch_wye_falls_back_to_broad_model():
    reg = _load_bundled_registry()
    request = _junction_request("branch", "branch.wye", "Circular", _ports(3, "Circular"))
    selection = reg.select_type("builtin_basic", request, strict=True)
    assert selection.type_def.id == "branch_generic"


def test_bundled_smacna_branch_tee_prefers_specific_tee_model():
    reg = _load_bundled_registry()
    request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.type_def.id == "branch_tee_generic"


def test_bundled_smacna_rectangular_elbow_prefers_dedicated_rectangular_model():
    reg = _load_bundled_registry()
    request = _junction_request("through", "through.bend_90", "Rectangular", _ports(2, "Rectangular"))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.type_def.id == "through_elbow_rectangular"


def test_bundled_smacna_circular_elbow_uses_generic_elbow_model():
    # through_elbow_rectangular only advertises the Rectangular profile, so
    # a Circular request must still resolve to the generic elbow model, not
    # be blocked by the rectangular-specific type's higher priority.
    reg = _load_bundled_registry()
    request = _junction_request("through", "through.bend_90", "Circular", _ports(2, "Circular"))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.type_def.id == "through_elbow_generic"


def test_bundled_end_topology_always_falls_back_to_terminal_marker():
    # Degree-1 nodes only ever classify as family_key "end.terminal" -- the
    # specific end_*_generic fittings (diffuser/fan/louver) are unreachable
    # by automatic selection and exist purely for manual/sticky selection.
    reg = _load_bundled_registry()
    request = _junction_request("end", "end.terminal", "Circular", _ports(1, "Circular"))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.status == "placeholder"
    assert selection.type_def.id == "end_terminal_marker"


def test_bundled_manual_end_diffuser_selection_stays_sticky():
    # Even though automatic selection can never choose end_diffuser_generic
    # (see test above), a manual selection of it must remain sticky across
    # normal sync, since its family is a specialization of "end.terminal".
    reg = _load_bundled_registry()
    request = _junction_request("end", "end.terminal", "Circular", _ports(1, "Circular"))
    selection = reg.resolve_sticky_type("smacna", "end_diffuser_generic", request)
    assert selection.status == "retained"
    assert selection.type_def.id == "end_diffuser_generic"


# ----------------------------------------------------------------------
# Mixed-profile branch/cross/multiport fall back to the broad Generic-
# profile model (not the invisible marker) -- branch_generic (builtin_basic)
# / branch_wye_generic (smacna) / cross_generic / multiport_generic all
# advertise "Generic" alongside their concrete profiles, mirroring how
# through_generic already covers "through". This matters because a
# placeholder selection here would silently drop to AirflowSolver's generic
# K_DEFAULT fallback instead of a real, type-specific loss coefficient.
# ----------------------------------------------------------------------

def _mixed_ports(n, extra_profile="Rectangular"):
    return _ports(n - 1, "Circular") + [{"profile": extra_profile}]


def test_bundled_builtin_basic_mixed_profile_branch_uses_generic_model_not_marker():
    reg = _load_bundled_registry()
    request = _junction_request("branch", "branch.tee", "Mixed", _mixed_ports(3))
    selection = reg.select_type("builtin_basic", request, strict=True)
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "branch_generic"


def test_bundled_builtin_basic_mixed_profile_cross_uses_generic_model_not_marker():
    reg = _load_bundled_registry()
    request = _junction_request("cross", "cross.cross", "Mixed", _mixed_ports(4))
    selection = reg.select_type("builtin_basic", request, strict=True)
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "cross_generic"


def test_bundled_builtin_basic_mixed_profile_multiport_uses_generic_model_not_marker():
    reg = _load_bundled_registry()
    request = _junction_request("multiport", "multiport.multiport", "Mixed", _mixed_ports(6))
    selection = reg.select_type("builtin_basic", request, strict=True)
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "multiport_generic"


def test_bundled_smacna_mixed_profile_branch_uses_generic_model_not_marker():
    reg = _load_bundled_registry()
    request = _junction_request("branch", "branch.wye", "Mixed", _mixed_ports(3))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "branch_wye_generic"


def test_bundled_smacna_mixed_profile_cross_uses_generic_model_not_marker():
    reg = _load_bundled_registry()
    request = _junction_request("cross", "cross.cross", "Mixed", _mixed_ports(4))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "cross_generic"


def test_bundled_smacna_mixed_profile_multiport_uses_generic_model_not_marker():
    reg = _load_bundled_registry()
    request = _junction_request("multiport", "multiport.multiport", "Mixed", _mixed_ports(6))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.status == "generic_profile"
    assert selection.type_def.id == "multiport_generic"


def test_bundled_mixed_profile_fix_does_not_disturb_exact_profile_ranking():
    # The specific tee model must still win over the now-Generic-profile-
    # capable broad model for a real, single-profile Circular tee.
    reg = _load_bundled_registry()
    request = _junction_request("branch", "branch.tee", "Circular", _ports(3, "Circular"))
    for lib_id, expected in (("builtin_basic", "branch_tee_generic"), ("smacna", "branch_tee_generic")):
        selection = reg.select_type(lib_id, request, strict=True)
        assert selection.status == "exact"
        assert selection.type_def.id == expected


# ----------------------------------------------------------------------
# selection.kind == "inline": dampers/silencers/flex connectors -- never
# reachable through automatic (topology, family, profile) matching, only
# through HVACLibrary.list_inline_types() for the "Add Inline Component"
# UI action. See freecad/HVAC/library/Library.py and libraries/README.md.
# ----------------------------------------------------------------------

def test_selection_kind_inline_excluded_from_match_indexes():
    model = _type_def("m1", "junction", ["through.straight.damper"], topology="through",
                       profiles=["Circular"], kind="model")
    inline = _type_def("i1", "junction", ["through.straight.damper"], topology="through",
                        profiles=["Circular"], kind="inline")
    lib = _library_with(model, inline)
    lib.reindex()

    all_indexed = set(lib.model_match_index.keys()) | set(lib.placeholder_match_index.keys())
    indexed_ids = {t.id for candidates in lib.model_match_index.values() for t in candidates}
    indexed_ids |= {t.id for candidates in lib.placeholder_match_index.values() for t in candidates}
    assert "i1" in {t.id for t in [model, inline]}  # sanity: both types exist
    assert "i1" not in indexed_ids
    assert "m1" in indexed_ids
    assert all_indexed  # the model's key is present


def test_select_type_never_returns_an_inline_type():
    model = _type_def("m1", "junction", ["through.straight.damper"], topology="through",
                       profiles=["Circular"], kind="model", priority=0)
    inline = _type_def("i1", "junction", ["through.straight.damper"], topology="through",
                        profiles=["Circular"], kind="inline", priority=1000)
    lib = _library_with(model, inline)
    request = _junction_request("through", "through.straight.damper", "Circular", _ports(2))

    selection = lib.select_type(request, strict=True)
    assert selection.type_def is not None
    assert selection.type_def.id == "m1"


def test_resolve_sticky_type_treats_inline_current_type_like_placeholder():
    inline = _type_def("i1", "junction", ["through.straight"], topology="through",
                        profiles=["Circular"], kind="inline")
    fallback = _type_def("m1", "junction", ["through.straight"], topology="through",
                          profiles=["Circular"], kind="model")
    lib = _library_with(inline, fallback)
    reg = HVACLibraryRegistry()
    reg.register_library(lib)

    request = _junction_request("through", "through.straight", "Circular", _ports(2))
    # An inline type should never end up as a Primary's current_type_id in
    # practice, but resolve_sticky_type must not treat it as sticky if it
    # somehow does -- same as a placeholder, always re-evaluated.
    selection = reg.resolve_sticky_type("lib", "i1", request)
    assert selection.status != "retained"
    assert selection.type_def.id == "m1"


def test_list_inline_types_filters_by_topology_and_profile():
    damper = _type_def("through_damper", "junction", ["through.straight.damper"], topology="through",
                        profiles=["Circular", "Rectangular"], kind="inline")
    vav = _type_def("through_vav", "junction", ["through.straight.vav"], topology="through",
                     profiles=["Oval"], kind="inline")
    model = _type_def("m1", "junction", ["through.straight"], topology="through",
                       profiles=["Circular"], kind="model")
    lib = _library_with(damper, vav, model)

    all_inline = lib.list_inline_types()
    assert {t.id for t in all_inline} == {"through_damper", "through_vav"}

    circular_only = lib.list_inline_types(topology="through", profile="Circular")
    assert {t.id for t in circular_only} == {"through_damper"}

    wrong_topology = lib.list_inline_types(topology="branch", profile="Circular")
    assert wrong_topology == []


def test_bundled_damper_and_vav_are_reclassified_inline():
    reg = _load_bundled_registry()
    for lib_id in ("smacna", "builtin_basic"):
        lib = reg.get_library(lib_id)
        damper = lib.get_type("through_damper_generic")
        vav = lib.get_type("through_vav_generic")
        assert damper.selection.kind == "inline"
        assert vav.selection.kind == "inline"

        inline_types = {t.id for t in lib.list_inline_types(topology="through")}
        assert {"through_damper_generic", "through_vav_generic"} <= inline_types

        # Never reachable through automatic matching, even if their own
        # declared family were somehow requested.
        request = _junction_request("through", "through.straight.damper", "Circular", _ports(2))
        selection = reg.select_type(lib_id, request, strict=True)
        assert selection.type_def is None or selection.type_def.id != "through_damper_generic"


def test_bundled_reclassifying_dampers_to_inline_does_not_disturb_other_selection():
    # The through/2-port automatic-selection outcome for an ordinary
    # straight/bend run must be unaffected by the damper/VAV reclassification
    # (they were never reachable by the classifier's own family keys anyway
    # -- see through_damper_generic.json/through_vav_generic.json).
    reg = _load_bundled_registry()
    request = _junction_request("through", "through.straight", "Circular", _ports(2))
    selection = reg.select_type("smacna", request, strict=True)
    assert selection.type_def is not None
    assert selection.type_def.id == "through_transition_generic"
