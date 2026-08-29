# Trajectory and Partial-Highlight Animation

Read this only for MD trajectories, GIF/MP4 exports, frame intervals, or
molecule/fragment highlights. Also read `camera.md`, `molecule-highlight.md` when
applicable, and `verification.md`.

## Animation contract

Read input-formats.md first. Record the trajectory, topology or type map,
output format, half-open frame slice and stride, progress/observable/stage
mapping, camera/projection/viewport/canvas, context layer,
highlighted stable IDs, overlay semantics, and representative QA frames. Render
separate requested intervals explicitly rather than silently concatenating them.


## CLI

Animation encoding is optional. Preflight it before rendering:

~~~bash
mat-vis capabilities --require animation --json
mat-vis render trajectory.traj -o trajectory.gif --backend cpu --check --json
~~~

~~~bash
mat-vis render trajectory.traj -o trajectory.gif --backend cpu \
  --frame-range 0:100:2 --fps 12

mat-vis render run.dump --type-map O H -o trajectory.mp4 --backend cpu \
  --stride 10 --fps 24

mat-vis render neb.extxyz -o neb.gif --backend cpu --fps 1.5 \
  --frame-field 'lambda=metadata:lambda,role=progress' \
  --frame-field 'angle=metadata:rotation_deg,role=observable,unit=deg' \
  --frame-label 'lambda={lambda:.2f}  rotation={angle:.1f} deg'
~~~

A generic frame annotation consists of repeatable `--frame-field` values and
one `--frame-label` template. Field sources are `index`, `metadata:KEY`,
`linear:START:STEP`, or `table:PATH:COLUMN`. Roles are `progress`,
`observable`, or `stage`. Do not infer a scientific observable from geometry
unless its reference, stable IDs, PBC treatment, and units are explicitly
defined; otherwise require it as frame metadata or a provenance-bearing table.
The physical-time flags remain a compatible shortcut and cannot be mixed with a
generic annotation.

Choose `--fps` from the motion semantics, not the input format. For discrete
rotations, NEB images, or reaction-coordinate paths, start around 1-2 fps;
1.5 fps is a useful first render. Smooth MD overviews can start around 8-15 fps.
Review the animation at delivery size and lower `--fps` when intermediate states
cannot be inspected comfortably. To slow the same scientific sequence, lower
`--fps`; do not drop frames or increase stride unless changing temporal or path
sampling is intended.

Use --frame INDEX only for a static output. --frame-range follows Python's
half-open START:STOP[:STEP] semantics; --stride is applied afterward.

## Stable identity and PBC

Build molecule and bond identity from topology/source data, preferably
MolCrysKit at a reference frame. Carry stable global atom or molecule IDs through
the trajectory; do not select a fragment by a coordinate box.

For each periodic frame:

1. use the stable molecule graph and global indices;
2. unwrap each molecule relative to a reference atom with the instantaneous cell
   and minimum-image convention;
3. draw stored intramolecular bonds;
4. preserve original cell and step metadata.

Do not discard required cross-boundary instances or create screen-spanning bonds.

An array row is not automatically a stable scientific identity. Record the
source ID field and verify uniqueness and cross-frame stability before using a
per-atom array for selection or overlays. MatterVis preserves ASE-backed frame
metadata and atom arrays on both `StructureFrame` and its canonical bundle;
display atoms map to those arrays through `_source_index`. After subsetting,
retain an explicit source-ID array because `_source_index` is subset-local.

When a selected object depends on neighbours, include the full topology closure
(bond partners, coordination shell, polyhedron ligands, or molecule members).
Never crop an atom by coordinates and then claim that its chemical environment
is complete.

## Semantic layers

1. **Context**: cell edges, host lattice, or subordinate wireframe.
2. **Focus**: complete selected molecule/fragment with canonical connectivity.
3. **Highlight**: stable colour, size, opacity, or marker convention.
4. **Overlays**: time, temperature, frame, order parameter, axes, or short label
   in reserved whitespace.

For a full system, keep context subordinate and one representative molecule in
focus. For fragment-only output, select by source IDs or graph membership, never
screen proximity.

For semantic animations, keep the rendering boundary auditable:

1. **MatterVis base**: atoms, bonds, cell, polyhedra, camera, and shared viewport;
2. **world-space overlays**: vectors or geometry with declared units and one
  fixed scale across all frames and panels;
3. **paper-space overlays**: time, stage, temperature, observable, and legend;
4. **composition/encoding**: cache base and composed frames separately.

State what every observable is and is not. A geometric displacement, proxy, or
order parameter must not be relabelled as polarization, charge, or another
physical quantity without the required conversion and provenance. Independent
temperatures, replicas, or intervals may be compared at matched times, but must
not be edited to look like one continuous simulated trajectory.

## Streaming and backend contract

"Streaming" means bounded memory through parsing, canonicalisation, rendering,
and encoding. Do not accumulate all canonical scenes, decoded PNGs, or a stacked
`F×H×W` array. Temporary frame spooling is acceptable for reverse-order slices;
the live memory target remains one rendered frame plus encoder state.

One animation uses one explicit effective backend. CPU rendering plus
`[animation]` is the default browser-free path. If an explicitly requested
Plotly export cannot run, stop rather than replacing individual frames. Record
the backend and representation; neither may change between frames.

## Comparable frames

Fix camera, screen-up, projection, canvas, extent, viewport, object scale,
palette, and overlay positions. Do not auto-fit per frame. If recentering is
needed, define it in the scene contract and apply it consistently.

For per-atom property colors, fix one exact finite range across all selected
source frames and atoms. Never normalize each frame separately. Inspect the
property-extrema frames in addition to first/middle/last. On large LAMMPS
trajectories, keep the selected field as an embedded column or mmap NPY
sidecar; do not create CSV/NPZ intermediates, stack `F×N`, or sample atoms.

Use one canonical output stem and leading underscores for raw frames/caches.
Check frame count, duration, dimensions, first/middle/last decoding, identity,
PBC bonds, clipping at maximum displacement, stable overlays, and the animation
as motion. Label any delivered still as first, middle, last, or separately
selected.

Also inspect frames at extrema of cell size, displacement, camera projection,
or any overlay likely to clip; first/middle/last alone may miss the worst case.
