# Atomistic Input Formats

All format adapters converge on one canonical MatterVis structure frame before
scene construction. Do not create a format-specific rendering path.

For ASE-backed inputs, canonical `StructureFrame` objects preserve frame
metadata and per-atom arrays. The same data is available on
`frame.bundle.frame_info` and `frame.bundle.atom_arrays`; rendered atoms map to
array rows through their stable `_source_index`. Keep scientific arrays separate
from render atom fields rather than inventing pseudo-elements or screen-space
selection rules.

Discover colorable fields without building a full scene:

~~~bash
mat-vis inspect INPUT --properties --json
~~~

Fields use `array:NAME`, `column:NAME`, or `sidecar:NAME`; a bare name is
accepted only when unique. External properties use a
`mattervis.atom-properties/v1` JSON manifest plus relative, read-only NPY
arrays. Static values have shape `(N, ...)`; trajectory values have shape
`(F, N, ...)` with explicit frame IDs. Atom alignment is by exact `id` or
`label`, or by `row` only when the manifest declares the source SHA-256. Never
convert a large property field to CSV/NPZ or drop identity metadata.

## Static structures

~~~bash
mat-vis render INPUT -o figure.png --backend cpu
~~~

Supported directly:

- CIF through MolCrysKit's public renderer-ready structure records;
- Cube with volumetric data preserved (`matter-vis[cube]`, no Plotly required);
- POSCAR, CONTCAR, and .vasp;
- XYZ and extxyz;
- ASE .traj;
- LAMMPS text dump/lammpstrj and LAMMPS data/configuration files.

Other ASE-readable formats may be auto-detected. For an ambiguous filename, pass
--input-format FORMAT.

Use --frame INDEX for one frame of a multi-frame input. Negative indices are
accepted.

## LAMMPS element identity

LAMMPS numeric atom types are not element symbols. Supply the file's complete
type order when it is not encoded unambiguously:

~~~bash
mat-vis render run.dump --type-map O H -o frame.png
mat-vis render system.data --input-format lammps-data --type-map O H -o frame.png
~~~

The same options apply to mat-vis tui. Never guess a type map from type numbers,
masses, or a model filename when provenance supplies the real order.

## Trajectories

Use .gif or .mp4 output for multiple frames:

~~~bash
mat-vis render trajectory.traj -o trajectory.gif --frame-range 0:100:2 --fps 12
mat-vis render run.dump --type-map O H -o trajectory.mp4 --stride 10 --fps 24
~~~

--frame-range uses Python's half-open START:STOP[:STEP] slice semantics.
--stride subsamples that selection. MatterVis applies one camera and one physical
viewport scale to every selected frame. Property-colored trajectories also use
one exact range over all selected source frames, before repeat/display filtering.
