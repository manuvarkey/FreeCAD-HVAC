"""SMACNA construction-feature generators.

All section dimensions supplied by the library are clear-air dimensions.  A
feature attached to the sheet-metal casing therefore grows the port by the
casing thickness before creating its geometry.
"""

_EPS = 1.0e-7


def _full_parameters(ctx):
    full = dict(ctx.context.get("params") or ctx.context.get("properties") or {})
    full.update(dict(ctx.parameters or {}))
    return full


def _flange_mount_ports(api, ctx):
    """
    The two ports to mount a transverse flange feature on, in port order,
    already positioned at the fitting's real end faces.

    Resolution order:
      1. context["flange_ports"] -- set by a generator whose flange-mount
         points differ from its raw connected ports (e.g. an elbow's
         tangent points after trimming for the bend radius -- see
         models/through_elbow_rectangular.py).
      2. context["connected_ports"] -- an untrimmed two-port junction.
      3. Synthesized from context["start_point"]/["end_point"] plus this
         type's own resolved properties -- a straight segment, which has
         no port objects of its own at all.
    """
    context = ctx.context

    flange_ports = context.get("flange_ports")
    if flange_ports:
        return list(flange_ports)[:2]

    connected_ports = api.connected_ports(context)
    if len(connected_ports) == 2:
        return connected_ports

    p = _full_parameters(ctx)
    start = api.vec(context["start_point"])
    end = api.vec(context["end_point"])
    axis = api.unit(end - start)
    port0 = {
        "position": start,
        "direction": axis * -1.0,
        "profile": context.get("profile", ""),
        "section_params": p,
        "profile_x_axis": context.get("profile_x_axis"),
    }
    port1 = api.copy_port(port0, position=end, direction=axis)
    return [port0, port1]


def generate_transverse_flange(api, ctx):
    p = _full_parameters(ctx)
    thickness = max(float(p.get("Thickness", 0.0) or 0.0), 0.0)
    flange_t = max(float(p.get("FlangeThickness", 0.0) or 0.0), 0.0)
    flange_h = max(float(p.get("FlangeHeight", 0.0) or 0.0), 0.0)
    if flange_t <= _EPS or flange_h <= _EPS:
        return None

    ports = _flange_mount_ports(api, ctx)
    if len(ports) != 2:
        return None

    shapes = []
    for i, port in enumerate(ports):
        if not bool(p.get("ShowFlange{}".format(i + 1), True)):
            continue
        casing_port = api.grow_port_section(port, thickness)
        inward = api.port_direction(port) * -1.0
        shapes.append(api.make_flange(casing_port, inward, flange_t, flange_h))

    if not shapes:
        return None
    return api.refine(api.fuse(*shapes))
