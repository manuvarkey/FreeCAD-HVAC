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

import math
import FreeCAD
import Part


# --------------------------------------------------------------------------
# Basic helpers
# --------------------------------------------------------------------------

def _section_size_hint(api, port):
    profile = api.port_profile(port)
    if profile == "Circular":
        return max(api.port_diameter(port), 1.0)
    if profile == "Rectangular":
        return max(api.port_width(port), api.port_height(port), 1.0)
    if profile == "Oval":
        return max(api.port_width(port), api.port_height(port), 1.0)
    return 1.0


def _safe_trim(value, fallback_value):
    v = float(value or 0.0)
    if v > 1e-6:
        return v
    return float(fallback_value)


def _inset_port(api, port, thickness):
    """
    Copy of `port` with its cross-section shrunk by 2*thickness (uniformly,
    on every side) -- position/direction/profile are unchanged, only
    section_params shrinks. Used to build the inner-bore wire/wall of a
    hollow sheet-metal fitting alongside its outer-wall counterpart.
    """
    return api.inset_port_section(port, thickness)


def _grown_section_params(api, port, delta):
    """
    Section params for `port`'s profile grown uniformly (on every side) by
    `delta` -- the outer edge of a flange collar around the port's own duct
    section. Inverse of `_inset_port`'s shrink.
    """
    profile = api.port_profile(port)
    params = api.port_section_params(port)
    delta = float(delta)

    if profile == "Circular":
        return dict(params, Diameter=float(params.get("Diameter", 0.0) or 0.0) + 2.0 * delta)
    if profile in ("Rectangular", "Oval"):
        return dict(
            params,
            Width=float(params.get("Width", 0.0) or 0.0) + 2.0 * delta,
            Height=float(params.get("Height", 0.0) or 0.0) + 2.0 * delta,
        )
    raise ValueError("Unsupported profile '{}' for flange".format(profile))


def _make_flange(api, port, inward_direction, flange_thickness, flange_height):
    """
    Flat flange collar at `port`'s own section (position + profile),
    extruded `flange_thickness` along `inward_direction` -- into the
    fitting's own body, overlapping its wall, matching the straight-duct/
    PartScript-elbow flange convention.
    """
    return api.make_flange(port, inward_direction, flange_thickness, flange_height)

# --------------------------------------------------------------------------
# Marker geometry
# --------------------------------------------------------------------------

def _make_sphere(center, diameter):
    radius = float(diameter) / 2.0
    if radius <= 0:
        raise ValueError("Marker diameter must be > 0")

    sphere = Part.makeSphere(radius)
    placement = FreeCAD.Placement(center, FreeCAD.Rotation())
    out = sphere.copy()
    out.transformShape(placement.toMatrix(), True, False)
    return out


def _build_marker(context, default_diameter, trim_factor):
    api = context.get("hvac_api", None)
    
    ports = list(context.get("connected_ports", []) or [])
    if not ports:
        raise ValueError("Marker requires at least one port")
    props = dict(context.get("properties", {}) or {})
    dia = float(props.get("MarkerDiameter", default_diameter) or default_diameter)
    center = api.port_position(ports[0])

    shape = _make_sphere(center, dia)
    trim_len = float(dia) * float(trim_factor)

    return {
        "shape": shape,
        "connection_lengths": api.build_trim_rec_from_context_uniform(context, trim_len),
    }


def build_terminal_marker(context):
    api = context.get("hvac_api", None)
    
    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})
    center = api.port_position(ports[0])

    dia = float(props.get("MarkerDiameter", 200.0) or 200.0)
    if dia <= 0:
        raise ValueError("Marker diameter must be > 0")

    trim_len = 0.0

    port_dir = api.port_direction(ports[0])

    zref = FreeCAD.Vector(0, 0, 1)
    xref = FreeCAD.Vector(1, 0, 0)
    if abs(port_dir.dot(zref)) < 0.95:
        ref = zref
    else:
        ref = xref
        
    v1 = port_dir.cross(ref)
    if v1.Length <= 1e-9:
        ref = FreeCAD.Vector(0, 1, 0)
        v1 = port_dir.cross(ref)
    v1.normalize()

    v2 = port_dir.cross(v1)
    v2.normalize()

    p1_v1 = center - (v1 * (dia / 2.0))
    p2_v1 = center + (v1 * (dia / 2.0))
    line_v1 = Part.makeLine(p1_v1, p2_v1)

    p1_v2 = center - (v2 * (dia / 2.0))
    p2_v2 = center + (v2 * (dia / 2.0))
    line_v2 = Part.makeLine(p1_v2, p2_v2)

    shape = Part.makeCompound([line_v1, line_v2])

    return {
        "shape": shape,
        "connection_lengths": api.build_trim_rec_from_context_uniform(context, trim_len),
    }


def build_diffuser_generic(context):
    """
    Generic terminal air device (diffuser/grille/register): a short solid
    stub extending from the connecting duct's own port profile, standing in
    for the physical device housing. Purely schematic -- not a manufacturer-
    accurate shape -- length is nominal (half the NeckSize, with a 50mm
    floor), not read from any catalog.
    """
    api = context.get("hvac_api", None)

    ports = list(context.get("connected_ports", []) or [])
    if len(ports) != 1:
        raise ValueError("Diffuser/grille/register requires exactly 1 port")
    props = dict(context.get("properties", {}) or {})
    port = ports[0]

    neck_size = float(props.get("NeckSize", 0.0) or 0.0)
    stub_length = max(neck_size * 0.5, 50.0)

    center = api.port_position(port)
    direction = api.port_direction(port)
    end_point = center + direction * stub_length

    shape = api.make_straight_shape(
        start_point=center,
        end_point=end_point,
        profile=api.port_profile(port),
        section_params=api.port_section_params(port),
        profile_x_axis=api.port_profile_x_axis(port),
    )

    return {
        "shape": shape,
        "connection_lengths": api.build_trim_rec_from_context_uniform(context, stub_length),
    }


def build_transition_marker(context):
    return _build_marker(context, default_diameter=240.0, trim_factor=0.30)


def build_elbow_marker(context):
    return _build_marker(context, default_diameter=240.0, trim_factor=0.35)


def build_tee_marker(context):
    return _build_marker(context, default_diameter=260.0, trim_factor=0.40)


def build_wye_marker(context):
    return _build_marker(context, default_diameter=260.0, trim_factor=0.40)


def build_cross_marker(context):
    return _build_marker(context, default_diameter=280.0, trim_factor=0.45)


def build_manifold_marker(context):
    return _build_marker(context, default_diameter=320.0, trim_factor=0.50)


# --------------------------------------------------------------------------
# Generic geometric helpers
# --------------------------------------------------------------------------


def build_through_generic(context):
    """
    Generic through-topology junction: dispatches to build_elbow for a bend
    family or build_transition for a straight/offset family, based on the
    object's own classified Family -- lets one type adapt its shape as the
    duct is dragged and its classification changes, instead of requiring the
    user to manually reassign between through_elbow_generic/through_
    transition_generic.
    """
    family = context.get("family", None)
    if family:
        if family in ["through.bend", "through.bend.3d", "through.bend_90", "through.bend_90.3d"]:
            return build_elbow(context)
        elif family in ["through.straight", "through.offset"]:
            return build_transition(context)

    # Neither bend nor straight/offset -- fall back to an invisible marker.
    # through_generic.json declares a single "casing" construction layer
    # (the same id build_elbow/build_transition already use above), so
    # remap the marker's own legacy {"shape": ...} return onto that id.
    marker = build_terminal_marker(context)
    return {
        "layers": {"casing": {"shape": marker.get("shape")}},
        "connection_lengths": marker.get("connection_lengths", []),
    }


# --------------------------------------------------------------------------
# Elbow
# --------------------------------------------------------------------------


def build_elbow(context):
    api = context.get("hvac_api", None)
    
    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})

    if len(ports) != 2:
        raise ValueError("Elbow requires exactly 2 ports")

    p0 = api.port_position(ports[0])
    p1 = api.port_position(ports[1])
    u0 = api.port_direction(ports[0])
    u1 = api.port_direction(ports[1])

    theta = api.angle_between(u0, u1)
    if theta <= 1e-6:
        raise ValueError("Elbow requires non-collinear directions")
    if abs(theta - math.pi) <= 1e-6:
        raise ValueError("Elbow cannot be built for opposite directions")

    radius = float(props.get("CenterlineRadius", 0.0) or 0.0)
    size_hint =  max(_section_size_hint(api, ports[0]), _section_size_hint(api, ports[1]))
    if radius < size_hint / 2:
        radius = 0.6 * size_hint

    thickness = float(props.get("Thickness", 0.8) or 0.8)

    elbow = api.make_elbow(ports[0], ports[1], radius, thickness)
    path_wire = elbow["path"]
    sweep_port_0, sweep_port_1 = elbow["ports"]
    trim0, trim1 = elbow["trim_lengths"]
    outer_shape = api.make_pipe_shell(
        path_wire, [api.make_section_wire_from_port(sweep_port_0), api.make_section_wire_from_port(sweep_port_1)]
    )

    # Hollow sheet-metal wall: sweep a second, uniformly-inset profile along
    # the *same* centerline arc and cut it from the outer sweep. This is a
    # schematic constant-cross-section-inset approximation (not a true
    # constant-thickness offset surface -- the wall thins slightly through
    # the bend), matching the fidelity already used elsewhere in this module.
    casing_shape = elbow["shape"]
    parts = [casing_shape]

    # Flanges are extruded inward from each tangent plane, into the elbow's
    # own body (overlapping the wall). port_direction() points *away* from
    # the junction, along the connected segment, so "into the elbow" from
    # each tangent plane is -u0 / -u1, not +u0/+u1.
    flange_height = float(props.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(props.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(props.get("ShowFlange1", True))
    show_flange2 = bool(props.get("ShowFlange2", True))

    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(api, sweep_port_0, u0 * -1.0, flange_thickness, flange_height))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(api, sweep_port_1, u1 * -1.0, flange_thickness, flange_height))

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    # Insulation, when enabled, wraps around the outside of the bare casing
    # tube (not the flanges -- same convention as a real insulated elbow):
    # a second sweep along the same centerline arc, with each cross-section
    # grown outward by InsulationThickness, minus the casing's own outer
    # sweep.
    insulation_thickness = float(props.get("InsulationThickness", 0.0) or 0.0)
    insulation_shape = None
    if insulation_thickness > 0.0:
        grown_wire_1 = api.make_section_wire_from_port(
            api.grow_port_section(sweep_port_0, insulation_thickness)
        )
        grown_wire_2 = api.make_section_wire_from_port(
            api.grow_port_section(sweep_port_1, insulation_thickness)
        )
        insulation_outer_shape = api.make_pipe_shell(path_wire, [grown_wire_1, grown_wire_2])
        insulation_shape = insulation_outer_shape.cut(outer_shape)

    return {
        "layers": {
            "casing": {"shape": shape},
            "insulation": {"shape": insulation_shape},
        },
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (ports[0], trim0),
                (ports[1], trim1),
            ]
        ),
    }

# --------------------------------------------------------------------------
# Transition
# --------------------------------------------------------------------------

def _safe_transition_length(length, d1, d2):
    L = float(length or 0.0)
    if L > 1e-6:
        return L
    return max(float(d1), float(d2), 1.0) * 1.5


def build_transition(context):
    api = context.get("hvac_api", None)
    
    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})

    if len(ports) != 2:
        raise ValueError("Transition requires exactly 2 ports")

    u1 = api.port_direction(ports[0])
    u2 = api.port_direction(ports[1])
    theta = api.angle_between(u1, u2)

    if abs(theta - math.pi) > math.radians(10.0):
        raise ValueError("Transition requires near-opposite port directions")

    h1 = _section_size_hint(api, ports[0])
    h2 = _section_size_hint(api, ports[1])
    length = _safe_transition_length(props.get("TransitionLength", 0.0), h1, h2)

    trim1 = 0.5 * length
    trim2 = 0.5 * length

    p1 = api.port_position(ports[0]) + (u1 * (trim1))
    p2 = api.port_position(ports[1]) + (u2 * (trim2))

    port1 = api.copy_port(ports[0], position=p1)
    port2 = api.copy_port(ports[1], position=p2)
    wire1 = api.make_section_wire_from_port(port1)
    wire2 = api.make_section_wire_from_port(port2)

    outer_shape = api.make_loft([wire1, wire2], solid=True, ruled=True)

    # Hollow sheet-metal wall: loft a second, uniformly-inset profile between
    # the same two end planes and cut it from the outer loft. Schematic
    # constant-inset approximation, matching the fidelity used for the elbow
    # (see build_elbow above).
    thickness = float(props.get("Thickness", 0.8) or 0.8)
    inner_port1 = _inset_port(api, port1, thickness)
    inner_port2 = _inset_port(api, port2, thickness)
    inner_wire1 = api.make_section_wire_from_port(inner_port1)
    inner_wire2 = api.make_section_wire_from_port(inner_port2)
    inner_shape = api.make_loft([inner_wire1, inner_wire2], solid=True, ruled=True)

    parts = [outer_shape.cut(inner_shape)]

    # Flanges are extruded inward from each end plane, into the transition's
    # own body (overlapping the wall) -- same convention as build_elbow.
    flange_height = float(props.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(props.get("FlangeThickness", 1.0) or 1.0)
    show_flange1 = bool(props.get("ShowFlange1", True))
    show_flange2 = bool(props.get("ShowFlange2", True))

    if show_flange1 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(api, port1, u1 * -1.0, flange_thickness, flange_height))
    if show_flange2 and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(api, port2, u2 * -1.0, flange_thickness, flange_height))

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    return {
        "layers": {"casing": {"shape": shape}},
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (ports[0], trim1),
                (ports[1], trim2),
            ]
        ),
    }


# --------------------------------------------------------------------------
# Inline devices (damper, VAV box)
# --------------------------------------------------------------------------

def _build_inline_device(context, default_length_factor, min_length):
    """
    Generic 2-port inline device body: a short constant-section stub
    between the two connecting ports, standing in for the physical device
    housing (damper blade, VAV box, ...). Purely schematic -- length is
    nominal, not read from any catalog.
    """
    api = context.get("hvac_api", None)

    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})

    if len(ports) != 2:
        raise ValueError("Inline device requires exactly 2 ports")

    u1 = api.port_direction(ports[0])
    u2 = api.port_direction(ports[1])
    theta = api.angle_between(u1, u2)

    if abs(theta - math.pi) > math.radians(10.0):
        raise ValueError("Inline device requires near-opposite port directions")

    h1 = _section_size_hint(api, ports[0])
    h2 = _section_size_hint(api, ports[1])
    length = _safe_trim(props.get("BodyLength", 0.0), max(h1, h2, 1.0) * default_length_factor)
    length = max(length, min_length)

    trim1 = 0.5 * length
    trim2 = 0.5 * length

    p1 = api.port_position(ports[0]) + (u1 * trim1)
    p2 = api.port_position(ports[1]) + (u2 * trim2)

    port1 = api.copy_port(ports[0], position=p1)
    port2 = api.copy_port(ports[1], position=p2)
    wire1 = api.make_section_wire_from_port(port1)
    wire2 = api.make_section_wire_from_port(port2)

    shape = api.make_loft([wire1, wire2], solid=True, ruled=True)

    return {
        "shape": shape,
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (ports[0], trim1),
                (ports[1], trim2),
            ]
        ),
    }


def build_damper_generic(context):
    return _build_inline_device(context, default_length_factor=0.5, min_length=100.0)


def build_vav_generic(context):
    return _build_inline_device(context, default_length_factor=1.0, min_length=300.0)


# --------------------------------------------------------------------------
# Tee / Wye helpers
# --------------------------------------------------------------------------

def _make_center_merge_port(api, port, center, inset):
    """
    Create a smaller 'inner' port very near the junction center.
    The section is kept identical; only the position is moved.
    """
    u = api.port_direction(port)
    p = center - (u * (float(inset)))
    return api.copy_port(port, position=p)
    
    
def _find_run_pair(context, ports):
    """
    Return indices (i, j, k) where i,j are the near-collinear run pair
    and k is the remaining branch port.

    The run pair is read from the node's own topology analysis
    (`collinear_pairs`, see JunctionAnalysis in NetworkParser.py) rather
    than re-derived from port directions here -- build_tee is only ever
    dispatched for a "tee"/"lateral_tee" family, which the classifier only
    assigns when it already found exactly one such pair.
    """
    if len(ports) != 3:
        raise ValueError("Requires exactly 3 ports")

    analysis = context.get("analysis", {}) or {}
    pairs = analysis.get("collinear_pairs", []) or []
    if len(pairs) != 1:
        raise ValueError("Could not identify run pair")

    i, j = int(pairs[0]["a"]), int(pairs[0]["b"])
    k = [x for x in range(3) if x not in (i, j)][0]
    return i, j, k


def _make_leg_to_center(api, port, center, trim_length, thickness, inner_inset=None):
    """
    Build one hollow leg of a wye/manifold fitting: a solid loft from the
    duct-facing outer port to a near-center inner port (`outer_shape`), plus
    the same loft rebuilt from thickness-inset ports (`void_shape`) -- the
    caller fuses all legs' outer_shapes and void_shapes separately, then
    cuts once, so the wall thickness is consistent everywhere the legs
    overlap near the center (matches build_elbow/build_transition/build_tee).

    Returns (outer_shape, void_shape, outer_port) -- outer_port is handed
    back so the caller can place a flange at the duct-facing end.
    """
    leg = api.make_branch_leg(port, center, trim_length, thickness, inner_inset)
    return leg["outer_shape"], leg["void_shape"], leg["outer_port"]


# --------------------------------------------------------------------------
# Tee
# --------------------------------------------------------------------------

def build_tee(context):
    api = context.get("hvac_api", None)
    
    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})

    if len(ports) != 3:
        raise ValueError("Tee requires exactly 3 ports")

    run_a_idx, run_b_idx, branch_idx = _find_run_pair(context, ports)

    run_a = ports[run_a_idx]
    run_b = ports[run_b_idx]
    branch = ports[branch_idx]

    run_a_hint = _section_size_hint(api, run_a)
    run_b_hint = _section_size_hint(api, run_b)
    run_hint = max(run_a_hint, run_b_hint)
    branch_hint = _section_size_hint(api, branch)

    run_trim_a_sug = _safe_trim(props.get("RunTrimLengthA", 0.0), 0.5 * run_hint)
    run_trim_b_sug = _safe_trim(props.get("RunTrimLengthB", 0.0), 0.5 * run_hint)
    branch_trim_sug = _safe_trim(props.get("BranchTrimLength", 0.0), 0.5 * branch_hint)
    inner_inset = float(props.get("CenterInset", 0.0) or 0.0)
    if inner_inset <= 1e-6:
        inner_inset = max(0.05 * max(run_hint, branch_hint), 1.0)
        
    # Find intersection/ closest point b/w main and branch
    c1a, c2a = api.closest_points_on_lines(api.port_position(run_a), api.port_direction(run_a), 
                                        api.port_position(branch), api.port_direction(branch))
    c1b, c2b = api.closest_points_on_lines(api.port_position(run_b), api.port_direction(run_b), 
                                        api.port_position(branch), api.port_direction(branch))
    center_main = (c1a + c1b) / 2
    center_branch = (c2a + c2b) / 2
    
    # Main branch
    angle = api.angle_between(api.port_direction(run_a), api.port_direction(branch))
    angle_sine = math.sin(angle)
    angle_cosine = math.cos(angle)
    if angle_sine > 0.1 and angle_cosine > 0.1:
        scale_run = angle_cosine / angle_sine
        min_branch_trim = abs(max(run_a_hint, run_b_hint) / 2 / angle_sine) + abs(branch_hint / 2 * angle_sine / angle_cosine)
    else:
        scale_run = 0.0
        min_branch_trim = max(run_a_hint, run_b_hint) / 2
    # adjust trim to account for branch duct size
    if run_a_hint >= run_b_hint:
        pos_a = c1a + api.port_direction(run_a) * (run_trim_a_sug + branch_hint/2 + run_a_hint/2 * scale_run)
        pos_b = c1b + api.port_direction(run_b) * (run_trim_b_sug + branch_hint/2 - run_b_hint/2 * scale_run)
    else:
        pos_a = c1a + api.port_direction(run_a) * (run_trim_a_sug + branch_hint/2 - run_a_hint/2 * scale_run)
        pos_b = c1b + api.port_direction(run_b) * (run_trim_b_sug + branch_hint/2 + run_b_hint/2 * scale_run)
    run_trim_a = (pos_a - api.port_position(run_a)).Length
    run_trim_b = (pos_b - api.port_position(run_b)).Length
    branch_trim = max(min_branch_trim, branch_trim_sug)
    port_a = api.copy_port(run_a, position=pos_a)
    port_b = api.copy_port(run_b, position=pos_b)
    if run_a_hint >= run_b_hint:
        mid_pos = c1a - api.port_direction(port_a) * branch_hint
        port_mid = api.copy_port(port_a, position=mid_pos)
    else:
        mid_pos = c1b - api.port_direction(port_b) * branch_hint
        port_mid = api.copy_port(port_b, position=mid_pos)
    # Branch leg
    pos_branch = center_branch + api.port_direction(branch) * branch_trim
    pos_mid_branch = center_branch
    port_branch = api.copy_port(branch, position=pos_branch)
    port_mid_branch = api.copy_port(branch, position=pos_mid_branch)
    thickness = float(props.get("Thickness", 0.8) or 0.8)
    parts = [api.make_tee(
        [port_a, port_mid, port_b], [port_branch, port_mid_branch], thickness
    )]

    # Flanges at the 3 duct-facing ends, extruded inward into the fitting's
    # own body -- same convention as build_elbow/build_transition.
    flange_height = float(props.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(props.get("FlangeThickness", 1.0) or 1.0)
    show_flange_a = bool(props.get("ShowFlangeA", True))
    show_flange_b = bool(props.get("ShowFlangeB", True))
    show_flange_branch = bool(props.get("ShowFlangeBranch", True))

    if show_flange_a and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(api, port_a, api.port_direction(port_a) * -1.0, flange_thickness, flange_height))
    if show_flange_b and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(_make_flange(api, port_b, api.port_direction(port_b) * -1.0, flange_thickness, flange_height))
    if show_flange_branch and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(
            _make_flange(api, port_branch, api.port_direction(port_branch) * -1.0, flange_thickness, flange_height)
        )

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    return {
        "layers": {"casing": {"shape": shape}},
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (run_a, run_trim_a),
                (run_b, run_trim_b),
                (branch, branch_trim),
            ]
        ),
    }


# --------------------------------------------------------------------------
# Wye
# --------------------------------------------------------------------------

def build_wye(context):
    api = context.get("hvac_api", None)
    
    center = api.center_from_context(context)
    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})

    if len(ports) != 3:
        raise ValueError("Wye requires exactly 3 ports")
        
    port_a = ports[0]
    port_b = ports[1]
    port_c = ports[2]
    
    a_pos = api.port_position(port_a)
    b_pos = api.port_position(port_b)
    c_pos = api.port_position(port_c)
    center = (a_pos + b_pos + c_pos) / 3.0
    
    a_size_hint = _section_size_hint(api, port_a)
    b_size_hint = _section_size_hint(api, port_b)
    c_size_hint = _section_size_hint(api, port_c)
    
    a_trim_sug = _safe_trim(props.get("TrimLengthA", 0.0), 0.5 * a_size_hint)
    b_trim_sug = _safe_trim(props.get("TrimLengthB", 0.0), 0.5 * b_size_hint)
    c_trim_sug = _safe_trim(props.get("TrimLengthC", 0.0), 0.5 * c_size_hint)

    thickness = float(props.get("Thickness", 0.8) or 0.8)
    wye = api.make_wye(
        [port_a, port_b, port_c], center,
        [a_trim_sug, b_trim_sug, c_trim_sug], thickness,
    )
    outer_port_a, outer_port_b, outer_port_c = wye["outer_ports"]
    parts = [wye["shape"]]

    # Flanges at the 3 duct-facing ends, extruded inward into the fitting's
    # own body -- same convention as build_elbow/build_transition.
    flange_height = float(props.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(props.get("FlangeThickness", 1.0) or 1.0)
    show_flange_a = bool(props.get("ShowFlangeA", True))
    show_flange_b = bool(props.get("ShowFlangeB", True))
    show_flange_c = bool(props.get("ShowFlangeC", True))

    if show_flange_a and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(
            _make_flange(api, outer_port_a, api.port_direction(outer_port_a) * -1.0, flange_thickness, flange_height)
        )
    if show_flange_b and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(
            _make_flange(api, outer_port_b, api.port_direction(outer_port_b) * -1.0, flange_thickness, flange_height)
        )
    if show_flange_c and flange_height > 0.0 and flange_thickness > 0.0:
        parts.append(
            _make_flange(api, outer_port_c, api.port_direction(outer_port_c) * -1.0, flange_thickness, flange_height)
        )

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    return {
        "layers": {"casing": {"shape": shape}},
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [
                (port_a, a_trim_sug),
                (port_b, b_trim_sug),
                (port_c, c_trim_sug),
            ]
        ),
    }

# --------------------------------------------------------------------------
# Cross
# --------------------------------------------------------------------------

def build_cross(context):
    return build_manifold(context)


# --------------------------------------------------------------------------
# Manifold
# --------------------------------------------------------------------------

def build_manifold(context):
    """
    Generic multi-port manifold builder.

    Supports any order > 2, i.e. 3-port wye, 4-port cross, higher-order hub/manifold.

    Expected behavior:
    - finds a common center from all connected ports
    - creates one trimmed leg from each port to that center
    - fuses all legs into one fitting
    - returns per-port connection lengths

    Optional per-port trim properties:
        TrimLength1, TrimLength2, ..., TrimLengthN
    and/or
        TrimLengthA, TrimLengthB, ... for the first 26 ports
    and/or a uniform TrimLength fallback.

    Legs are hollow (Thickness) with an optional flange collar at each
    duct-facing end (FlangeHeight/FlangeThickness), individually toggled by
    the same ShowFlange1/.../ShowFlangeA/... convention (falling back to a
    uniform ShowFlange, then True).

    Notes:
    - This is a simple "all legs meet at a center" manifold.
    - It is generic in topology order, but not topology-aware.
      If later you want smarter center selection or smoother branch blending,
      that can be added separately.
    """
    api = context.get("hvac_api", None)
    if api is None:
        raise ValueError("Missing hvac_api in context")

    ports = list(context.get("connected_ports", []) or [])
    props = dict(context.get("properties", {}) or {})

    n_ports = len(ports)
    if n_ports <= 2:
        raise ValueError("Manifold requires more than 2 ports")

    # Gather positions first
    port_positions = [api.port_position(p) for p in ports]

    # Use centroid of all port positions as generic manifold center.
    # This is more stable for arbitrary order than relying on a 3-port-specific pattern.
    center = port_positions[0]
    for p in port_positions[1:]:
        center = center + p
    center = center / float(n_ports)

    thickness = float(props.get("Thickness", 0.8) or 0.8)
    flange_height = float(props.get("FlangeHeight", 25.0) or 25.0)
    flange_thickness = float(props.get("FlangeThickness", 1.0) or 1.0)

    outer_legs = []
    void_legs = []
    flange_parts = []
    trim_records = []

    for idx, port in enumerate(ports):
        size_hint = _section_size_hint(api, port)

        # Support both numeric and alphabetic trim/flange keys
        #   TrimLength1, TrimLength2, ...  /  ShowFlange1, ShowFlange2, ...
        #   TrimLengthA, TrimLengthB, ...  /  ShowFlangeA, ShowFlangeB, ...
        trim_key_num = f"TrimLength{idx + 1}"
        trim_key_alpha = f"TrimLength{chr(ord('A') + idx)}" if idx < 26 else None
        show_flange_key_num = f"ShowFlange{idx + 1}"
        show_flange_key_alpha = f"ShowFlange{chr(ord('A') + idx)}" if idx < 26 else None

        raw_trim = props.get(trim_key_num, None)
        if raw_trim is None and trim_key_alpha is not None:
            raw_trim = props.get(trim_key_alpha, None)
            if raw_trim is None:
                raw_trim = props.get("TrimLength", None)
        if raw_trim is None:
            raw_trim = 0.0

        trim_sug = _safe_trim(raw_trim, 0.5 * size_hint)

        outer_leg, void_leg, outer_port = _make_leg_to_center(api, port, center, trim_sug, thickness)
        outer_legs.append(outer_leg)
        void_legs.append(void_leg)
        trim_records.append((port, trim_sug))

        show_flange = props.get(show_flange_key_num, None)
        if show_flange is None and show_flange_key_alpha is not None:
            show_flange = props.get(show_flange_key_alpha, None)
        if show_flange is None:
            show_flange = props.get("ShowFlange", None)
        if show_flange is None:
            show_flange = True
        if bool(show_flange) and flange_height > 0.0 and flange_thickness > 0.0:
            flange_parts.append(
                _make_flange(api, outer_port, api.port_direction(outer_port) * -1.0, flange_thickness, flange_height)
            )

    # Hollow sheet-metal wall: fuse all legs' outer shapes and, separately,
    # all legs' thickness-inset void shapes, then cut once -- matches the
    # fidelity used for build_elbow/build_transition/build_tee/build_wye.
    outer_shape = api.fuse_shapes(outer_legs)
    void_shape = api.fuse_shapes(void_legs)
    parts = [outer_shape.cut(void_shape)] + flange_parts

    shape = api.fuse_shapes(parts) if len(parts) > 1 else parts[0]

    return {
        "layers": {"casing": {"shape": shape}},
        "connection_lengths": api.build_trim_rec_from_port_lengths(trim_records),
    }
