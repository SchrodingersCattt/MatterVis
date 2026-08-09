# Trajectory and Partial-Highlight Animation

Use this path for molecular-dynamics trajectories, GIF/MP4 exports, or a
request to show only selected time segments or selected molecular fragments.

## Define the animation contract

Before rendering, record:

```text
Input trajectory and topology source:
Output stem and format:
Frame start/stop and stride:
Time mapping and stage labels:
Camera, projection, viewport, and canvas size:
Background/context layer:
Highlighted molecule, fragment, or atom IDs:
Overlay labels and color semantics:
Representative frames for QA:
```

Use an inclusive frame window and an explicit stride. Preserve the mapping from
rendered frame to source frame, simulation step, time, temperature, or other
stage metadata. If several intervals are requested, render them as explicit
segments rather than silently concatenating unrelated windows.

## Keep identity stable across frames

Build molecule and bond identity from the topology/source data, preferably from
MolCrysKit at a reference frame. Map selected atoms back to stable global atom
IDs or molecule IDs and carry that mapping through the trajectory. Do not choose
the highlighted fragment by a coordinate box alone: periodic motion and cell
deformation can change its apparent position.

For periodic trajectories:

1. recover the molecule graph from the reference structure;
2. map each molecule to its global atom indices;
3. for every kept frame, unwrap each molecule relative to a reference atom using
   the instantaneous cell and the minimum-image convention;
4. draw the stored intramolecular bonds from the stable graph;
5. preserve the original cell and step metadata alongside the positions.

This prevents a molecule from being cut by a periodic boundary or acquiring a
long artificial bond during animation. Do not silently discard cross-boundary
bond instances when the selected scene contract requires them.

## Render in semantic layers

Use a consistent layer order:

1. **Context**: unit-cell edges, host lattice, or a low-opacity wireframe that
   explains where the selected object sits.
2. **Focus**: the selected molecule or fragment rendered with MatterVis in
   `cluster` mode when a free molecular view is intended. Keep all atoms and
   bonds needed to show the selected fragment's connectivity.
3. **Highlight**: a stable color, size, opacity, or opaque white-dot convention
   applied only to the selected atoms or fragment. Do not duplicate the focus
   in the context layer with a conflicting style.
4. **Overlays**: time, temperature, frame number, order parameters, axis keys,
   or a short system label in reserved whitespace.

For a full-system animation, keep the background as context and make one
representative molecule the focus. For a fragment-only animation, select by
source atom IDs or graph membership and render the complete intended fragment;
do not infer connectivity from screen proximity.

## Keep frames visually comparable

Fix the camera direction, screen-up, projection, canvas, scene extent, viewport,
object scale, palette, and overlay locations for every frame. Do not auto-fit or
change the camera because one frame has a larger excursion. If the focus is
recentered for readability, make that recentering part of the explicit scene
contract and keep the same camera and scale for the full requested segment.

Use the same visual conventions across related animations: element colors,
context opacity, focus size, bond widths, label positions, and frame timing.
Avoid a dense legend or a large title inside the structure region; use a small
top band or another reserved whitespace strip.

## Output and QA

Use one canonical animation stem, such as `flipping_sys1.gif` or
`trajectory_segment.mp4`; use leading underscores for raw frames and caches.
Keep intermediate frames only when they are needed for reproducibility or QA.

Check all of the following:

- the output has the expected frame count, duration, and dimensions;
- first, middle, and last frames decode successfully;
- the camera, canvas, viewport, and overlay positions do not jump;
- the selected molecule or fragment remains the selected identity;
- no bond is stretched across a periodic boundary after unwrapping;
- background context stays subordinate to the focus;
- a frame with the largest displacement does not clip atoms, bonds, or labels;
- the final animation is inspected as an animation, not only as one still frame.

When a static snapshot is also delivered, state whether it shows the first,
middle, last, or a separately selected representative frame.
