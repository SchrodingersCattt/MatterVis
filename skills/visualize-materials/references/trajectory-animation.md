# Trajectory and Partial-Highlight Animation

Read this only for MD trajectories, GIF/MP4 exports, frame intervals, or
molecule/fragment highlights. Also read `camera.md`, `molecule-highlight.md` when
applicable, and `verification.md`.

## Animation contract

Record input trajectory and topology, output format, inclusive frame range and
stride, time/stage mapping, camera/projection/viewport/canvas, context layer,
highlighted stable IDs, overlay semantics, and representative QA frames. Render
separate requested intervals explicitly rather than silently concatenating them.

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

## Semantic layers

1. **Context**: cell edges, host lattice, or subordinate wireframe.
2. **Focus**: complete selected molecule/fragment with canonical connectivity.
3. **Highlight**: stable colour, size, opacity, or marker convention.
4. **Overlays**: time, temperature, frame, order parameter, axes, or short label
   in reserved whitespace.

For a full system, keep context subordinate and one representative molecule in
focus. For fragment-only output, select by source IDs or graph membership, never
screen proximity.

## Comparable frames

Fix camera, screen-up, projection, canvas, extent, viewport, object scale,
palette, and overlay positions. Do not auto-fit per frame. If recentering is
needed, define it in the scene contract and apply it consistently.

Use one canonical output stem and leading underscores for raw frames/caches.
Check frame count, duration, dimensions, first/middle/last decoding, identity,
PBC bonds, clipping at maximum displacement, stable overlays, and the animation
as motion. Label any delivered still as first, middle, last, or separately
selected.
