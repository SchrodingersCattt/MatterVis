# MatterVis Quickstart

Use this page for the normal path from an input structure to one verified PNG.

## 1. Install once

From the skill directory:

```bash
bash scripts/install_runtime.sh
```

This installs into the current Python environment. To isolate it explicitly:

```bash
bash scripts/install_runtime.sh --venv /absolute/path/to/mattervis-venv
```

Add `--with-system-libs` only when system package installation is authorized.
The installer verifies the distribution and CLI. See `install.md` for repairs.

## 2. Admit the scene

Confirm the input exists. For a small structure, inspect bounded structured
output:

```bash
mat-vis tui INPUT.cif --no-interaction --format structured \
  --display formula_unit --view c --projection orthographic
```

Do not serialize more than 200 visible atoms through the TUI. For larger,
disordered, or ambiguous scenes, read `diagnose-and-select.md`.

Choose the scene from scientific intent:

- one molecule or formula unit: `formula_unit`;
- crystal packing or periodic context: `unit_cell`;
- diagnostic symmetry sites: `asymmetric_unit`;
- an already finite nonperiodic input: `cluster`.

`cluster` is not a crop or neighbour-shell selector.

## 3. Choose one explicit render command

Small molecular/formula-unit view:

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view formula_unit --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --no-hydrogen --no-cell --no-axes --no-labels \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 1
```

Periodic unit-cell view:

```bash
mat-vis render INPUT.cif -o OUTPUT.png \
  --view unit_cell --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --no-hydrogen --show-cell --no-axes --no-labels \
  --background '#FFFFFF' --atom-scale 0.85 --bond-radius 0.12 \
  --width 1600 --height 1200 --scale 1
```

For an admitted ordered scene above 500 displayed atoms, start with
`unit_cell`, `atom-scale=0.65`, `bond-radius=0.08`, `2400x1800`, and `scale=1`.
Do not use the former `1600x1200, scale=2` recipe: a successful command can
still produce an all-white `3200x2400` export.

Do not use `--config` for fields that have CLI flags. Current parser defaults
overwrite those config values even when their flags are omitted. Reserve config
for config-only policies such as disorder handling or atom groups.

## 4. Render with automatic evidence

Run the same `mat-vis render` command through the wrapper:

```bash
python scripts/render_verified.py \
  --manifest OUTPUT.manifest.json \
  --crop-padding 24 \
  --min-bbox-coverage 0.70 \
  -- mat-vis render INPUT.cif -o OUTPUT.png \
  --view formula_unit --style ball_stick --material mesh \
  --camera-axis c --orthogonal \
  --no-hydrogen --no-cell --no-axes --no-labels \
  --background '#FFFFFF' --atom-scale 1.0 --bond-radius 0.15 \
  --width 1200 --height 900 --scale 1
```

The wrapper executes the literal installed `mat-vis` command, preserves its exit
code and logs, identifies the effective backend from dispatch and fallback
evidence, rejects blank/undecodable output, and writes the manifest. With
`--crop-padding`, it detects the CLI's actual background color, crops only
contiguous paper border, and records `rescaled=false`. It does not use the
Python rendering API.

For figures with intentional paper whitespace, omit or lower
`--min-bbox-coverage`; blank-image rejection remains mandatory.

## 5. Deliver

Require:

- command exit 0;
- requested output exists and decodes;
- `blank=false` in the manifest;
- effective backend/style matches the requested visual language;
- no chemistry or semantic-fatal warning;
- final-size visual inspection by a vision-capable reviewer.

Report input/output paths and hashes, dimensions, display, style, material,
camera, backend, fallback reason, and visual-review status. Deliver one selected
PNG unless the caller asked for other formats.
