# Camera Contract

Read this only when the default view is unsuitable or panels must be compared.

Periodic default: orthographic, normal to the largest lattice face. MatterVis
compares the areas of `ab`, `ac`, and `bc`, chooses the corresponding
reciprocal normal `c*`, `b*`, or `a*`, and prefers `c*` for equal-area ties.
Use `--camera-axis` only when the evidence needs a different crystallographic
direction. Nonperiodic inputs use automatic
atom-content fitting without a crystallographic axis or cell; set an explicit
Cartesian direction only when it clarifies the requested feature.

Choose exactly one direction control:

- `--camera-axis a|b|c|a*|b*|c*`
- `--view-direction X Y Z`
- `--camera-position X Y Z`

`--camera-up X Y Z` and `--camera-distance D` are optional. Vectors must be
finite and nonzero; distance must be positive. Reciprocal axes are for lattice
plane normals.

Comparable panels must share direction, up, projection, viewport or physical
scale, aspect ratio, and canvas. Do not independently auto-fit them.

For interactive HTML, confirm that the corner a/b/c compass changes orientation
with a camera drag. A compass frozen at its initial projection is invalid.

The static TUI `--view c` is Cartesian `+Z`, not exact lattice `c` for an
oblique cell. Record requested and effective direction, up, projection,
distance, and viewport.
