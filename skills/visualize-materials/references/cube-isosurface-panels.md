# Cube Isosurface Panels

Use this page for CP2K/Gaussian cube files, orbital isosurfaces, electron-density
isosurfaces, and any multi-panel comparison of scalar fields.

## Non-negotiable scientific contract

- Resolve and record the orbital numbers before rendering. Do not infer HOMO or
  LUMO from filenames without checking the electron/orbital count.
- Use one explicit isovalue for all panels representing the same field. Do not
  compare independently auto-fitted percentile isovalues.
- Signed orbitals use stable positive/negative colors across the entire figure.
  Electron density uses a single positive color.
- Use one camera direction, screen-up direction, projection, and viewport
  contract across comparable panels.
- State whether periodic scalar closure is enabled. If a surface is split across
  opposite boundaries, verify image placement before enabling closure.

## Required rendering workflow

1. Render every cube as a separate MatterVis image.
2. Set a common value with `--isovalue VALUE` for comparable fields. Use
  `--isosurface-opacity VALUE` and `--periodic-isosurface` only when those
  choices are scientifically intended.
3. Static CLI output excludes interaction-only traces. Programmatic Plotly
  exports must pass `include_interaction_traces=False`; interactive HTML may
  retain `atom-selection`, `bond-selection`, and `disorder-preview-outline`.
4. Use a square orthographic viewport derived from the common unit-cell or
   atomistic bounds. Never accept the default fit without inspecting the
   effective x/y/z ranges.
5. Crop only contiguous white border from each rendered image and retain a
   recorded safety pad.
6. Compose the native-resolution crops. Do not resize panels under a shared
   physical-scale contract.
7. Use explicit row and column labels appropriate to the comparison.
8. Include the isovalue and units in the figure or manifest.

If the whole crystal makes the chemically relevant orbital too small, declare a
local-region comparison and render the same chemically defined region for every
panel. Do not silently zoom individual panels.

## Validation

A successful render call is not acceptance. Before delivery:

- inspect the final image at its intended size;
- reject all-background panels, clipped surfaces, duplicate/disconnected image
  artifacts, and unexplained empty space;
- check every cell of a 2D grid, not just whole-height column strips;
- report per-cell ink bounds, occupancy, and safety pads;
- verify all panels share the declared isovalue and camera;
- record raw dimensions, crop boxes, composition positions, and whether any
  resampling occurred.

The final delivery record must include the camera axis, screen-up, orthographic
or perspective projection, explicit isovalue, opacity, sign colors, periodic
closure policy, grid dimensions, backend, and objective plus visual QA status.

## Molecular completeness in periodic cells

When a cube panel includes the crystal structure:

- draw the unit-cell edges whenever the caller requires crystallographic cell
  context;
- use complete boundary-fragment replicas for molecular crystals; strict
  home-cell atoms are not acceptable when they cut a molecule at a boundary;
- do not use camera ranges, raster crops, or composition slots that truncate a
  molecule, bond, or isosurface component;
- inspect connectivity at all four source-panel edges before composition;
- treat a visible broken/cut fragment as a blocking failure even if global ink
  occupancy, source clearance, and final composite clipping checks pass;
- document whether complete boundary replicas and cross-boundary bond endpoints
  were enabled.

Validate raw source clipping before any crop, then validate every final grid
cell after composition. Added canvas padding cannot repair a clipped source.
