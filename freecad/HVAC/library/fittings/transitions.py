# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the HVAC addon.

"""
Transitions and offsets between two collinear ports: angled (straight loft),
radiussed (S-curve) and mitered.
"""

import math

from ._common import _finish, _loft, _positive, _props, _size, _trimmed


def _transition_layout(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("TransitionLength"), max(size, 100.0))
    trim = total / 2.0
    return ports, trim


def measure_transition(context):
    api = context["hvac_api"]
    ports, trim = _transition_layout(context)
    return api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)])


def build_transition(context):
    api = context["hvac_api"]
    ports, trim = _transition_layout(context)
    a, b = _trimmed(api, ports[0], trim), _trimmed(api, ports[1], trim)

    def envelope(offset):
        return _loft(api, [a, b], offset, ruled=True)

    lengths = api.build_trim_rec_from_port_lengths([(ports[0], trim), (ports[1], trim)])
    return _finish(context, envelope, lengths)


def _transition_radiussed_route(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("TransitionLength"), max(size, 100.0))
    radius = _positive(p.get("TransitionRadius"), max(size, 10.0))
    # A bend radius smaller than the duct's own half-width folds the swept
    # surface back on itself on the inside of the bend (same minimum
    # build_elbow enforces on CenterlineRadius).
    radius = max(radius, 0.5 * size)

    # The two arcs have to fit between the ends and the diagonal: each arc
    # takes R tan(turn / 2) on either side of its corner. A radius larger
    # than that is reduced to the largest that fits (a little short of it,
    # so the straight pieces don't shrink to nothing).
    axis = api.offset_transition_axis(ports[0], ports[1], total)
    turn = axis["turn_angle"]
    if turn > 1.0e-6:
        room = min(total / 4.0, (axis["corner1"] - axis["corner0"]).Length / 2.0)
        largest = 0.98 * room / math.tan(turn / 2.0)
        if largest < 0.5 * size:
            raise ValueError(
                "The offset is too steep for a radiussed fitting of this size: make OffsetLength / "
                "TransitionLength longer (the arcs need a radius of at least {:.0f} mm, only {:.0f} mm fits)".format(
                    0.5 * size, largest
                )
            )
        radius = min(radius, largest)

    route = api.make_radiussed_path(ports[0], ports[1], total, radius)
    return ports, route


def measure_transition_radiussed(context):
    api = context["hvac_api"]
    ports, route = _transition_radiussed_route(context)
    return api.build_trim_rec_from_port_lengths(
        [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
    )


def build_transition_radiussed(context):
    api = context["hvac_api"]
    ports, route = _transition_radiussed_route(context)

    def envelope(offset):
        ends = [api.profile_from_port(route["ports"][0], offset), api.profile_from_port(route["ports"][1], offset)]
        return api.sweep(ends, route["path"], solid=True)

    lengths = api.build_trim_rec_from_port_lengths(
        [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
    )
    return _finish(context, envelope, lengths)


def _transition_mitered_layout(context):
    api = context["hvac_api"]
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    p = _props(context)
    size = max(_size(api, ports[0]), _size(api, ports[1]))
    total = _positive(p.get("TransitionLength"), max(size, 100.0))

    # Shared axis geometry with the radiussed transition -- same end
    # points and theoretical sharp turn points, only the corner treatment
    # differs (a flat mitre cut here, an arc there).
    axis = api.offset_transition_axis(ports[0], ports[1], total)
    trim0 = max(0.0, (axis["s0"] - api.port_position(ports[0])).dot(api.port_direction(ports[0])))
    trim1 = max(0.0, (axis["s1"] - api.port_position(ports[1])).dot(api.port_direction(ports[1])))
    return ports, size, total, axis, trim0, trim1


def measure_transition_mitered(context):
    api = context["hvac_api"]
    ports, size, total, axis, trim0, trim1 = _transition_mitered_layout(context)
    return api.build_trim_rec_from_port_lengths([(ports[0], trim0), (ports[1], trim1)])


def build_transition_mitered(context):
    api = context["hvac_api"]
    ports, size, total, axis, trim0, trim1 = _transition_mitered_layout(context)
    d = axis["d"]
    s0, s1 = axis["s0"], axis["s1"]
    corner0, corner1 = axis["corner0"], axis["corner1"]
    diagonal, turn_angle = axis["diagonal"], axis["turn_angle"]

    end_a = api.copy_port(ports[0], position=s0)
    end_b = api.copy_port(ports[1], position=s1)

    def envelope(offset):
        if turn_angle <= 1e-6:
            # No actual lateral offset: an ordinary straight loft, there is
            # no corner to mitre.
            return _loft(api, [end_a, end_b], offset, ruled=True)

        # The mitre plane bisects the straight-run direction and the
        # diagonal, so a straight stub and the diagonal middle piece meet
        # flush -- both turn points share this same bisector as their
        # plane normal, only the plane's own origin differs.
        normal = api.unit(d + diagonal)

        # Extend each port's own profile from its end point toward the
        # transition's centre, then clip it back at its own turn point
        # with the mitre plane.
        reach = total / 2.0 + size
        stub_a = api.extrude(api.profile_from_port(end_a, offset), d * reach, solid=True)
        stub_a = api.clip_plane(stub_a, (corner0, normal), side="negative")
        stub_b = api.extrude(api.profile_from_port(end_b, offset), d * -reach, solid=True)
        stub_b = api.clip_plane(stub_b, (corner1, normal), side="positive")

        # Loft between the stubs' own cut faces at the turn points -- not
        # a fresh, idealised profile wire, which sits on a different plane
        # than the mitre cut and would not line up with it once clipped.
        # Reading the real cut face back off each already-trimmed stub
        # guarantees the middle piece meets them exactly, and needs no
        # further clipping of its own.
        face_a = api.section_face(stub_a, (corner0, normal))
        face_b = api.section_face(stub_b, (corner1, normal))
        middle = api.loft([face_a.OuterWire, face_b.OuterWire], solid=True, ruled=True)
        return api.fuse(stub_a, stub_b, middle)

    lengths = api.build_trim_rec_from_port_lengths([(ports[0], trim0), (ports[1], trim1)])
    return _finish(context, envelope, lengths)
