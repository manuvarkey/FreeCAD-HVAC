HVAC_PARTSCRIPT_API = 2


def generate(context):
    api = context["hvac_api"]
    p = dict(context.get("params") or context.get("properties") or {})
    ports = list(api.connected_ports(context))
    if len(ports) != 2:
        raise ValueError(f"Expected 2 connected ports, got {len(ports)}")
    if any(api.port_profile(port) != "Rectangular" for port in ports):
        raise ValueError("through_elbow_rectangular requires rectangular ports")

    t = max(float(p.get("Thickness", 0.8) or 0.0), 0.0)
    ins = max(float(p.get("InsulationThickness", 0.0) or 0.0), 0.0)
    size = max(api.port_width(ports[0]), api.port_height(ports[0]), api.port_width(ports[1]), api.port_height(ports[1]))
    radius = float(p.get("CenterlineRadius", 0.0) or 0.0)
    if radius <= 1.0e-7:
        radius = 0.6 * size
    radius = max(radius, 0.5 * size)
    route = api.make_elbow_path(ports[0], ports[1], radius)

    def boundary(offset):
        profiles = [api.profile_from_port(route["ports"][0], offset), api.profile_from_port(route["ports"][1], offset)]
        return api.sweep(profiles, route["path"], solid=True)

    b0 = api.refine(boundary(0.0))
    b1 = api.refine(boundary(t))
    casing = api.refine(api.cut(b1, b0)) if t > 1.0e-7 else None
    layers = {"casing": {"shape": casing}}
    if ins > 1.0e-7:
        b2 = api.refine(boundary(t + ins))
        layers["insulation"] = {"shape": api.refine(api.cut(b2, b1))}

    return {
        "layers": layers,
        "connection_lengths": api.build_trim_rec_from_port_lengths(
            [(ports[0], route["trim_lengths"][0]), (ports[1], route["trim_lengths"][1])]
        ),
    }
