# Camera Contract

Read this only when the default view is unsuitable or panels must be compared.

Default image camera: orthographic, looking from the structure toward lattice
`+c`, with lattice `+b` as screen-up. With `--camera-axis c`, omit
`--camera-up` unless a Cartesian up vector was explicitly requested.

Choose exactly one direction control:

- `--camera-axis a|b|c|a*|b*|c*`
- `--view-direction X Y Z`
- `--camera-position X Y Z`

`--camera-up X Y Z` and `--camera-distance D` are optional. Vectors must be
finite and nonzero; distance must be positive. Reciprocal axes are for lattice
plane normals.

Comparable panels must share direction, up, projection, viewport or physical
scale, aspect ratio, and canvas. Do not independently auto-fit them.

The static TUI `--view c` is Cartesian `+Z`, not exact lattice `c` for an
oblique cell. Record requested and effective direction, up, projection,
distance, and viewport.
