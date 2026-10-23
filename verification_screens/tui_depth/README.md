# TUI depth rendering: issue #211

Headless Textual captures at **160 × 48**, diagonal view (`az=30°, el=20°`),
monochrome element labels, default visibility. The warning banner is mounted.
The before revision is `95ebb7c` (includes the baseline rendering from #212).
The after captures use this PR. PNGs use DejaVu Sans Mono for fixed cell widths;
the adjacent `.txt` files are the actual canvas characters.

| Fixture / level | Before | After | Plain text |
| --- | --- | --- | --- |
| DAP-4 / atom | [PNG](before/dap4-atom.png) | [PNG](after/dap4-atom.png) | [before](before/dap4-atom.txt), [after](after/dap4-atom.txt) |
| DAP-4 / molecule | [PNG](before/dap4-molecule.png) | [PNG](after/dap4-molecule.png) | [before](before/dap4-molecule.txt), [after](after/dap4-molecule.txt) |
| CaF₂ (COD 1000043) / atom | [PNG](before/caf2-atom.png) | [PNG](after/caf2-atom.png) | [before](before/caf2-atom.txt), [after](after/caf2-atom.txt) |
| CaF₂ (COD 1000043) / molecule | [PNG](before/caf2-molecule.png) | [PNG](after/caf2-molecule.png) | [before](before/caf2-molecule.txt), [after](after/caf2-molecule.txt) |

The after picture centres the aspect-correct fit, uses explicit depth suffixes,
and reserves an orientation/scale/legend row below the scene. DAP-4 packing uses
centroid tokens at this density; sparse and selected molecules use opaque hulls.
The CaF₂ fixture supplies one connected entity, which stays a silhouette at
molecule level. Bonds follow the supplied IR; the renderer does not invent
ionic bonds.

To reproduce in either checkout (with `matter-vis[tui]` and optional `cairosvg`):

```sh
PYTHONPATH=/path/to/checkout python /path/to/this/scripts/11_tui_depth_visuals.py \
  --output /path/to/captures
```

Parallax remains experimental and off by default. The text ghosts help compare
successive rotations without replacing current geometry, but add clutter in a
dense full-cell atom view; `rotation_sequence` provides independently readable
frames for an agent. Layer slicing and local focus are preferable when depth
bands overlap heavily. Molecular silhouettes use mean entity depth; switch to
atom level for exact per-cell atom and bond occlusion.
