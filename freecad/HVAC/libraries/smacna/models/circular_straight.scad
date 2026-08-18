// Circular straight duct segment -- parametric test model for
// freecad/HVAC/library/openscad_shapes.py (HVACLibraryAPI.shape_from_openscad),
// wired up by build_circular_straight_openscad in
// freecad/HVAC/libraries/smacna/generators/segments.py.
//
// Canonical local frame (must match the Python generator's placement
// transform -- see build_circular_straight_openscad):
//   - Local origin = port 0 (start port)
//   - Local +Z axis = duct axis, pointing toward port 1 (end port)
//   - The tube is drawn along +Z from z=0 to z=length
//
// Parameters below are the customizer defaults; the generator overrides
// diameter/thickness/length via `openscad -D name=value` on every render.
// $fn is NOT overridden from Python (see openscad_shapes.py's module
// docstring for why) -- edit the default below directly to change facet
// quality.

diameter = 100;   // outer diameter, mm
thickness = 0.8;  // sheet metal wall thickness, mm
length = 500;     // duct length (port0 to port1 distance), mm

$fn = 64;

module circular_duct(diameter, thickness, length) {
    outer_r = diameter / 2;
    inner_r = max(outer_r - thickness, 0.1);

    difference() {
        cylinder(h = length, r = outer_r, center = false);
        translate([0, 0, -1])
            cylinder(h = length + 2, r = inner_r, center = false);
    }
}

circular_duct(diameter, thickness, length);
