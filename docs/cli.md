# CLI Reference

MatterVis renders atomistic structures and trajectories without launching the
browser viewer. Every input format is parsed into the same canonical structure
frame before scene construction.

## Quick start

~~~bash
# Inspect and preflight without writing a file
mat-vis inspect structure.cif --json
mat-vis inspect trajectory.extxyz --properties --json
mat-vis render structure.cif -o figure.png --backend cpu --check --json

# Static CIF through the base CPU renderer
mat-vis render structure.cif -o figure.png --backend cpu --json

# VASP structure
mat-vis render POSCAR -o figure.png --view unit_cell

# One frame from an ASE trajectory
mat-vis render trajectory.traj --frame 20 -o frame.png

# LAMMPS trajectory with explicit atom-type order
mat-vis render run.dump --type-map O H -o trajectory.gif \
  --frame-range 0:100:2 --fps 12 \
  --display-time ps --time-step 0.5 --time-step-unit fs

# NEB/path animation with progress and an independently derived observable
mat-vis render neb.extxyz -o neb.gif --fps 6 \
  --frame-field 'lambda=metadata:lambda,role=progress' \
  --frame-field 'angle=metadata:rotation_deg,role=observable,unit=deg' \
  --frame-label 'lambda={lambda:.2f}  rotation={angle:.1f} deg'

# Interactive HTML
mat-vis render structure.extxyz -o interactive.html --backend plotly --orthogonal

# Discover the bundled Agent Skills without cloning the repository
mat-vis skills list --json
mat-vis skills show visualize-materials
mat-vis skills show visualize-materials --file references/camera.md
mat-vis skills tree visualize-materials
mat-vis skills show visualize-materials --all
~~~

## Subcommands

| Command | Description |
|---------|-------------|
| inspect | Report bounded source and structure metadata as JSON |
| capabilities | Resolve requested features to exact optional extras |
| skills | Discover and read bundled Agent Skills progressively |
| render | Render a structure, one trajectory frame, or a GIF/MP4 animation |
| serve | Launch the interactive Dash browser viewer |
| tui | Open a supported structure or trajectory frame in the terminal |
| operate | Commit a structural operation. `operate build-supercell` builds a new lattice. There is no `operate supercell` alias. Display repeats stay on `tui --repeat` and `POST /api/v2/state` `supercell`. |

### operate — structural commits

```bash
mat-vis operate build-supercell structure.cif --a 2 --b 3 --c 1 -o supercell.cif --json
```

`build-supercell` requires three positive integers. A nondiagonal matrix is
rejected. `--preview` builds a candidate and does not write a new revision.
Other commands (`build-slab`, `insert-molecule`, `delete-sites`,
`replace-species`, `add-hydrogens`, `remove-solvent`,
`materialize-selection`, `commit-disorder-candidate`) take `--params-json`.
`operate supercell` is not a command.

`inspect`, `capabilities`, and `render --check` are the agent preflight surface.
`--check` resolves requirements only and never creates the output.

### skills — bundled Agent Skills

The `skills` viewer is read-only and uses the installed skill resources, so it
also works from a different current directory after installing a wheel. It does
not import the renderer or optional numeric dependencies. The
default `show` command prints only the raw `SKILL.md` entrypoint. Use
`--file` for one referenced resource, `tree` to inspect local links without
reading their bodies, or `--all` to expand reachable Markdown in deterministic
link order. Scripts and external links are never executed or expanded. Links
inside fenced or inline code are examples, not resources. A closing fence must
be at least as long as its opener, so a shorter fence shown inside the example
stays inside the block. Four-space indented code blocks, and inline spans whose
backtick runs differ in length, are still ordinary text. A link to a directory
is a non-document target. If a default `show` document links to a missing file,
the document is still printed, each broken link is reported on stderr, and the
process exits with status 2. `tree` and `show --all` stop before printing a
partial resource graph. Add `--json` to `list`, `show`, or `tree` for
machine-readable output. Failures that reject the command, including an unknown
skill, write `Error: ...` to stderr and, with `--json`, a
`mattervis.error/v1` object (`schema`, `ok: false`, `error`) to stdout.

## tui — terminal and online agent sessions

`mat-vis tui INPUT` opens the existing Textual terminal viewer. Large periodic
cells start at a bounded overview. Discrete molecular crystals use one marker
per manifested molecule and label the species. A large connected fragment, as
in a MOF or polymer, uses a screen-space bonded skeleton so the network remains
visible instead of collapsing to one centroid. Press `L` in the interactive
viewer or pass `--level atom` to inspect atom geometry; `--level molecule`
forces the overview for smaller cells as well. `--level auto` is the default.
`--no-interaction` prints a titled static frame. `--session-format jsonl` instead
keeps one stateful controller alive and accepts caller-selected semantic
actions on stdin:

~~~bash
printf '%s\n' \
  '{"action":"observe"}' \
  '{"action":"orbit","arguments":{"yaw_deg":30,"pitch_deg":15}}' \
  '{"action":"close"}' | \
  mat-vis tui structure.extxyz --session-format jsonl \
    --charset ascii7 --width 100 --height 36
~~~

Every input line receives one JSON response line and stdout is flushed after
each response. Actions are selected online by the caller; MatterVis does not
pre-plan a trajectory or repair malformed natural-language commands.

JSONL observations are always monochrome and ANSI-free. The default charset is
`unicode`, using Unicode Braille geometry without ANSI colour; pass
`--charset ascii7` when every observation field must also serialize as ASCII
bytes.

`--charset unicode|ascii7` is available for both static and JSONL output.
`ascii7` selects printable ASCII geometry and implies monochrome output;
`unicode` preserves the default Braille renderer. `ascii7` frames are
printable 7-bit ASCII without ANSI escapes. The static status line above a
non-interactive frame can still contain a degree sign; it is not part of the
frame or of a JSONL observation. The Unicode profile may contain
Braille and superscripts; JSONL sessions stay monochrome, while interactive
and static colour Unicode can still contain ANSI from the built-in compositor.

`--renderer ID` and `--renderer-config FILE` select one frame backend for both
static output and `--session-format jsonl`. The default is `mattervis.native-v1`,
and omitting the flags leaves the picture and default JSONL traces unchanged.
A missing id, incompatible API, bad pin, or malformed frame exits with an
error and does not draw the native picture instead. Renderer choice is not a
JSONL action. `--observation-profile model` applies only to JSONL observations
and strips chemistry warnings, formulas, and the source title. See
[the renderer contract](agents/tui_renderer_api.md).

JSONL sessions support
`observe`, `reset`, `orbit`, `align`, `pan`, `zoom`, `fit`, `set_display`,
`select`, `focus`, `clear_selection`, `clear_focus`, and `close`.

The `--format structured` form keeps a compact crystal summary and molecule
overview. For large cells it includes a bounded atom sample and reports how
many rows were omitted; its ASCII art follows the same molecule or framework
overview policy. Use JSONL `select`/`focus` actions for exact local inspection
instead of passing a whole unit-cell atom dump through the terminal.

---

## render — Structure and trajectory export

~~~
mat-vis render INPUT -o OUTPUT [options]
~~~

### Input and output arguments

| Argument | Description |
|----------|-------------|
| INPUT | Atomistic structure or trajectory path |
| -o, --output | Output path; format is inferred from its extension |
| --input-format FORMAT | ASE format name for an ambiguous filename |
| --type-map ELEMENT ... | Complete LAMMPS atom-type order |
| --frame INDEX | Static frame index; negative indices are accepted |

Supported inputs:

| Input | Reader |
|-------|--------|
| CIF | MolCrysKit renderer-ready public records |
| Cube | Cube parser and volumetric isosurfaces (`[cube]`) |
| POSCAR, CONTCAR, .vasp | ASE VASP reader |
| XYZ, extxyz | ASE, including multi-frame files |
| .traj | ASE trajectory reader |
| LAMMPS dump, lammpstrj | ASE LAMMPS text-dump reader |
| LAMMPS data/configuration | ASE LAMMPS data reader |
| Other ASE-readable files | ASE auto-detection or --input-format |

LAMMPS numeric types are not elements. Pass --type-map whenever the source does
not encode element identity unambiguously; the order is type 1, type 2, and so
on. MatterVis never guesses it from a model filename.

### Per-atom property colors

Discover fields before selecting one:

~~~bash
mat-vis inspect INPUT --properties --json
mat-vis inspect INPUT --property-data properties.json --properties --json
~~~

Fields are qualified as `array:NAME`, `column:NAME`, or `sidecar:NAME`.
Unqualified names are accepted only when unique. Discovery reports source,
dtype, trailing shape, components, and unit; LAMMPS discovery reads the indexed
`ITEM: ATOMS` header without parsing atom rows.

| Flag | Default | Description |
|------|---------|-------------|
| `--property-data MANIFEST` | — | `mattervis.atom-properties/v1` JSON + relative NPY sidecar |
| `--color-by FIELD [FIELD ...]` | — | Enable continuous atom colors |
| `--color-reduction MODE` | `auto` | `auto`, `scalar`, `magnitude`, `component`, `trace`, `mean_normal`, or `von_mises` |
| `--color-component NAME_OR_INDEX` | — | Component used by `component` reduction |
| `--colormap NAME` | `viridis` | Colormap used to build the shared 256-color LUT |
| `--color-range MIN MAX` | exact selected range | Fixed clipping range; skips the global property prescan |
| `--color-center VALUE` | — | Put this physical reference at the LUT midpoint |
| `--nan-color COLOR` | `#BDBDBD` | Color for NaN/Inf values |
| `--color-label TEXT` | field name | Colorbar label |
| `--color-unit UNIT` | declared unit | Display/provenance only; no conversion |
| `--no-colorbar` | off | Omit the reserved right-side colorbar region |

Scalar fields color directly and a three-component vector uses magnitude under
`auto`. Tensor fields require an explicit reduction; six-component tensors
also require declared component order. Automatic range is the exact finite
minimum/maximum over all selected source atoms and frames, before repeat or
display filtering. Non-finite values use the missing color and are counted; an
all-non-finite selection fails. Explicit atom-group colors override the
property base color. Each bond half inherits its endpoint's final atom color,
so property-colored atoms produce a two-tone bond by default. Override selected
bonds directly with `--bond-group SELECTOR color=#RRGGBB`; for example,
`--bond-group all color=#333333` makes every bond monochrome. See
[`agents/atom_property_coloring.md`](agents/atom_property_coloring.md) for the
sidecar schema, alignment, metadata, and Python/REST APIs.

### Renderer selection

`--renderer auto|batch|general` defaults to `auto`. Every supported input is
first normalised to the same contiguous frame arrays. Auto selection uses the
total atom-frame workload (atoms across all selected frames, after `--repeat`),
so both one large static structure and a long trajectory can select the array
batch renderer. It does not branch on CIF/XYZ/LAMMPS filenames. Labels, axes,
vectors, and other overlays are composed as rendering layers and do not decide
the numeric base renderer. PNG, GIF, and MP4 can all use the batch path.

### Supported output formats

| Extension | Format | Backend |
|-----------|--------|---------|
| .png | Raster image | CPU 3D, Matplotlib projected 2D, explicit Plotly + Kaleido, or native GPU (wgpu) |
| .pdf | Vector PDF | CPU 3D, Matplotlib projected 2D, or explicit Plotly + Kaleido |
| .svg | Vector SVG | CPU 3D, Matplotlib projected 2D, or explicit Plotly + Kaleido |
| .html | Interactive 3D | Plotly (`[plotly]`) |
| .gif | Multi-frame animation | CPU + `[animation]` |
| .mp4 | H.264 animation | CPU + `[animation]` |

Use `--backend cpu|matplotlib|plotly|gpu`; there is no backend or representation fallback.
The `gpu` backend is an opt-in, PNG-only offscreen renderer. Install
`matter-vis[gpu]` and verify a usable adapter with
`mat-vis capabilities --require gpu --json` before rendering. Unsupported
transparent, dashed, text, or depth-disabled primitives fail explicitly; the
command never falls back to CPU.
Base MatterVis provides CPU 3D and Matplotlib projected 2D PNG/PDF/SVG, ORTEP, rings, polyhedra, and
ordinary structure inputs. Run `mat-vis capabilities --require FEATURE --json`
for an exact install command before using an optional frontend.

### Frames and animations

| Flag | Default | Description |
|------|---------|-------------|
| --frame INDEX | 0 | Select one frame for PNG/PDF/SVG/HTML |
| --frame-range START:STOP[:STEP] | all | Python half-open frame slice for GIF/MP4 |
| --stride N | 1 | Keep every Nth selected animation frame |
| --fps FPS | 12 | Video playback rate; never used as physical simulation time |
| --frame-duration SECONDS | - | Alternative to `--fps`; seconds per frame |
| --repeat NX NY NZ | 1 1 1 | Repeat each periodic frame before workload selection |
| --workers N | automatic | Bound CPU animation workers |
| --display-time UNIT | off | Draw physical time in `fs`, `ps`, or `ns` on every frame |
| --time-step DT | - | MD integrator timestep |
| --time-step-unit UNIT | fs | Unit of `--time-step`: `fs`, `ps`, or `ns` |
| --dump-frequency STEPS | - | MD steps between stored frames when source metadata has no step |
| --first-frame-step STEP | 0 | Step represented by source frame 0 for the fallback mapping |
| --time-position CORNER | top-left | `top-left`, `top-right`, `bottom-left`, or `bottom-right` |
| --frame-field SPEC | - | Repeatable `NAME=SOURCE` field with optional role, unit, scale, and offset |
| --frame-label TEMPLATE | - | Format template over the declared frame-field names |
| --frame-label-position CORNER | top-left | Corner for the generic frame annotation |

GIF/MP4 require at least two selected frames. All selected frames use one camera,
canvas, and shared world-space viewport scale.

Physical time and playback time are independent. `--fps` controls only how fast
the movie plays. With `--display-time`, MatterVis resolves time in this order:

1. per-frame `time_fs`, `time_ps`, or `time_ns`;
2. per-frame `timestep`, `step`, or `nstep`, multiplied by `--time-step`;
3. original source-frame index and `--dump-frequency`, offset by
   `--first-frame-step`, then multiplied by `--time-step`.

Selection by `--frame-range` and `--stride` never renumbers source frames, so
stride is not multiplied twice. If neither time nor step metadata exists,
`--time-step` and `--dump-frequency` are both required.

Generic frame annotations use one or more repeatable `--frame-field` values and
one `--frame-label` template. Sources are `index`, `metadata:KEY`,
`linear:START:STEP`, or `table:PATH:COLUMN`. Index, linear, and table lookup
all use the original source-frame index, so slicing and stride retain scientific
alignment. A field role is `progress`, `observable`, or `stage`; units are
recorded but appear only when explicitly written into the label template.
Table provenance includes the resolved path, column, row mapping, and SHA256.
Generic annotations and the physical-time shortcut are mutually exclusive.

Animations preserve one requested representation, camera, and CPU backend
across every frame. Plotly GIF/MP4 is rejected explicitly; MatterVis never
substitutes one frame backend for another.

### Display options

| Flag | Default | Description |
|------|---------|-------------|
| --view MODE | auto | Periodic input uses unit_cell; nonperiodic or synthetic-cell input uses cluster |
| --style STYLE | ball_stick | ball_stick, ball, space_filling, stick, ortep, or wireframe |
| --shading MODE | smooth | smooth or flat mesh shading |
| --backend BACKEND | cpu | cpu (3D), matplotlib (projected 2D), plotly, or explicit native GPU PNG; never selected by fallback |
| --orthogonal | yes | Orthographic projection |
| --perspective | no | Perspective projection |
| --isovalue VALUE | automatic | Explicit positive cube isosurface magnitude |
| --isosurface-opacity VALUE | style default | Cube surface opacity in (0, 1] |
| --isosurface-mode MODE | surface | surface or complete triangulated wireframe |
| --isosurface-wireframe-width VALUE | 1.0 | Wireframe line width in pixels |
| --isosurface-wireframe-opacity VALUE | 1.0 | Wireframe line opacity in [0, 1] |
| --periodic-isosurface / --no-periodic-isosurface | off | Enable or disable scalar closure across cell faces |

Static exports omit browser-only picking traces. Interactive HTML retains them.
For comparable cube panels, pass the same explicit `--isovalue` rather than
allowing each source to select its own percentile threshold.

### Camera

Periodic static renders default to an orthographic view normal to the largest
lattice face: `ab -> c*`, `ac -> b*`, or `bc -> a*`, with `c*` winning
equal-area ties. Nonperiodic inputs fit the atomic coordinates and do not treat
an ASE padding box as crystallographic data. Camera direction options are
mutually exclusive. When `--no-cell` hides a real periodic cell, automatic
framing likewise fits the visible atoms rather than hidden cell edges or vacuum
padding.

| Flag | Default | Description |
|------|---------|-------------|
| `--camera-axis a|b|c|a*|b*|c*` | auto | Override the largest-face default with a real or reciprocal lattice axis |
| `--view-direction X Y Z` | — | Cartesian direction from scene toward camera |
| `--camera-position X Y Z` | — | Explicit absolute Cartesian camera position in Å |
| `--camera-up X Y Z` | `+b` / `+Y` | Preferred screen-up direction |
| `--camera-target X Y Z` | scene centre | Explicit Cartesian look-at target in angstrom |
| `--camera-distance D` | `1.8` | Positive scene-fit multiplier for axis/direction views |
| `--field-of-view DEG` | `45` | Perspective vertical field of view; requires `--perspective` |
| `--ortho-scale ANGSTROM` | fitted | Orthographic half-height; requires `--orthogonal` |
| `--camera-clip NEAR FAR` | fitted | Positive near/far clipping distances |

`--camera-up` is orthogonalized against the view direction. CPU is the default
static backend and needs neither Chrome nor Kaleido. Explicit Plotly static
export requires `[plotly-export]`; a failure is reported without substitution.

### Visibility toggles

| Flag | Default | Description |
|------|---------|-------------|
| `--show-hydrogen` / `--no-hydrogen` | on | Show/hide hydrogen atoms |
| `--show-cell` / `--no-cell` | auto | On for periodic input and off for nonperiodic or synthetic-cell input; explicit flags override |
| `--show-labels` / `--no-labels` | off | Show/hide atom labels |
| `--show-axes` / `--no-axes` | off | Show/hide the camera-projected crystallographic a/b/c compass |
| `--cell-color COLOR` | `#333333` | Unit-cell edge colour |
| `--cell-width PX` | 2.0 | Unit-cell edge width |
| `--cell-overlays JSON` | — | Auxiliary cells with independent matrix, origin, color, width, dash, alpha, and depth-test settings |

The cell and lattice compass are foreground overlays, so dense atoms, bonds,
or polyhedra do not depth-occlude them. `--monochrome` remains a rejected
legacy flag.

### Numeric parameters

| Flag | Default | Range | Description |
|------|---------|-------|-------------|
| `--atom-scale` | 1.0 | 0.3–1.8 | Atom radius scale factor |
| `--bond-radius` | 0.15 | 0.05–0.40 | Bond cylinder radius (Å) |
| `--bond-scale` | loader default | > 0 | MolCrysKit bond-perception coefficient; does not change visual bond radius |
| `--bond-threshold ELEMENT1,ELEMENT2=CUTOFF` | none | CUTOFF > 0 | Pair-specific MolCrysKit cutoff; repeat for multiple element pairs |
| `--camera-distance` | 1.8 | > 0 | Scene-fit multiplier (not Å) |
| `--width` | 900 | — | Image width in pixels |
| `--height` | 720 | — | Image height in pixels |
| `--scale` | 2 | 1–4 | Supersampling factor (effective DPI = 72 × scale) |
| `--sphere-detail LAT LON` | 12 20 | LAT >= 2, LON >= 3 | Sphere and ellipsoid mesh resolution |
| `--cylinder-sides N` | 12 | N >= 3 | Bond-cylinder mesh resolution |

### Selector-based mixed styles

Repeat `--atom-group` and `--bond-group` in later-wins order. Each occurrence
uses one selector token followed by one or more `KEY=VALUE` overrides:

```bash
mat-vis render structure.extxyz -o mixed.png \
  --atom-group all style=ball \
  --atom-group molecule:0 style=ball_stick material=mesh \
  --bond-group between:C,N style=wireframe color=#336699
```

Atom selectors are `all`, `minor`, `major`, `element:...`, `label:...`,
`index:...`, `fragment:...`, `fragment-index:...`, and `molecule:...`.
`fragment` selects a fragment label; `fragment-index` selects its numeric index.
Join clauses with `+` for AND, for example `element:O+minor`. Atom overrides are
`color`, `color_light`,
`visible`, `opacity`, `style`, and `material`.

Bond selectors are `all`, `minor`, `major`, `between:...`, and `label:...`.
Bond overrides are `color`, `visible`, `opacity`, `style`, and `radius_scale`.
Use `--check --json` to inspect the normalized rules without loading the input.

For CPU GIF/MP4, `--vector-overlays JSON` applies one fixed source-frame vector
overlay to every selected frame. This supports equilibrium-centred vibration
arrows over moving atoms; per-frame-changing vector fields require a future
separate contract.

### Colour and ORTEP

| Flag | Default | Description |
|------|---------|-------------|
| `--background` | `#FFFFFF` | Background hex colour |
| `--ortep-probability` | 0.5 | Ellipsoid probability (0.0–1.0) |
| `--ortep-mode` | `ortep_axes` | ORTEP decoration: `ortep_solid`, `ortep_axes`, or `ortep_hatch` |
| `--aromatic-rings` | `bonds` | `bonds`, `circle`, or `disk` |
| `--missing-adp-policy` | `error` | ORTEP missing-ADP policy: `error` or explicit `sphere` placeholder |

`--style ortep` selects the representation. `--shading smooth|flat` selects
smooth or flat surface shading independently. `--ortep-mode` independently
adds no marks, principal axes, or hatch marks.

Legacy `--config`, `--view-weights`, `--publication-*`, `--title`, and
`--subtitle` options are rejected by this agent-facing path instead of being
silently ignored. Polyhedron overlays remain available through repeatable
`--polyhedron` JSON objects plus `--polyhedron-site` and
`--polyhedron-cutoff`; they use the base CPU topology path and require no Web
module. An atom-level specification draws every matching visible centre by
default and colours each hull from its centre element, with same-hue face
lightness. The JSON `site` or `sites` keys and `--polyhedron-site` select
source atom indices; molecule-level selectors use source fragment indices. The
JSON `center_images:true` option works at both levels. At molecule level,
MatterVis follows each displayed complete fragment image and translates the
center, ligand shell, and hull together. Receipts keep source and display counts
separate.

The same polyhedron path is used for animated CPU renders, so `--polyhedron`
stays depth-tested and frame-synchronous in GIF and MP4 output as well.

`--polyhedron-disorder` chooses which crystallographic disorder component is
used to build those shells. It is forwarded to MolCrysKit `find_polyhedra`.

| Value | Meaning |
|---|---|
| `major` | Default. Highest-occupancy component of each mutually exclusive family. |
| `exclusive` | One shell per orientation. |
| `as_refined` | Every refined site, including orientations fused into one molecule. |

A JSON `disorder_policy` on one `--polyhedron` object overrides the flag for
that overlay. The flag selects shell membership only; it does not change atom
opacity or which atoms are drawn. Molecule identification defaults to
`as_refined` and keeps every refined site. Only this shell flag defaults to
`major`.

`as_refined` keeps every refined site. `major` and `exclusive` query each
realization of the indexed molecule, so a fused displayed formula still
matches a split orientation. `exclusive` draws one shell per full
orientation. If the
chosen policy still cannot produce at
least four non-coplanar ligand points, `mat-vis render` exits with
`mattervis.polyhedron-shell-error/v1` (nested in `mattervis.error/v1` under
`--json`). The command does not invent ligand coordinates.

`--cell-overlays` and `--bond-scale` require the general renderer. An explicit batch request fails instead of silently dropping either option.


---

## `serve` — Browser viewer

Requires `python -m pip install "matter-vis[web]"`; this extra includes Plotly.

```
mat-vis serve [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Host to bind |
| `--port` | `50001` | Port to expose |
| `--cif` | — | CIF path to preload (repeat for multiple) |
| `--input` | — | Any supported structure/trajectory input to preload |
| `--input-format` | auto | Explicit format for an ambiguous `--input` |
| `--type-map` | — | LAMMPS atom-type order for `--input` |
| `--frame` | `0` | Frame of `--input`; Web v1 has no playback timeline |
| property-color flags | — | Same sidecar, field, reduction, range, and LUT flags as render |
| `--structure` | — | Limit catalog to named structure(s) |
| `--preset` | — | Preset JSON to load |
| `--api-only` | — | Reserved for automation mode |

## tui — Terminal view

Ordinary structure input requires `python -m pip install "matter-vis[tui]"`.
Cube input requires the combined
`python -m pip install "matter-vis[cube,tui]"`; resolve it with
`mat-vis capabilities --require cube tui --json`. Use `mat-vis inspect --json`
for normal bounded agent diagnosis without installing Textual.

Open any supported structure or one trajectory frame without starting Dash:

~~~bash
mat-vis tui POSCAR
mat-vis tui trajectory.traj --frame 20
mat-vis tui run.dump --type-map O H --frame 20
~~~

The interactive ASCII view is the primary non-visual observation path. Use
--no-interaction only when a deterministic single frame is required;
--format structured remains an optional machine contract rather than the
source of chemical conclusions. --input-format, --type-map, and --frame use the
same shared IO contract as render.

Important options:

- --display auto|unit_cell|formula_unit|asymmetric_unit selects a canonical scene;
- --view a|b|c|diagonal|ab|ac|bc or explicit angles selects the camera;
- --projection, --zoom, and --center work in static and interactive modes;
- --show-minor and --hide-partial control crystallographic disorder;
- --width and --height bound static output exactly.

Interactive controls: q/e and w/z orbit, a/d roll, arrows or i/j/k/l pan, u/o
zoom, b/c/t/m/n/h/Shift+B/Shift+A toggle bonds/cell/labels/monochrome/minor
disorder/hydrogen/periodic boundary replicas/axes, Shift+L switches atom and
molecule levels, r resets the view, and x quits. Hydrogen and boundary
replicas are hidden unless `--hydrogen` or `--boundary` is set. `--axes`
turns the triad on at startup in static, session, and interactive modes;
Shift+A toggles it afterwards. The scene is fitted into the rows above that
footer. Atom level draws element symbols, solid bonds, and `:` cell edges,
and drops labels that would overlap. Press s for
Select mode. There, arrows choose projected neighbors, Tab traverses stable
atom IDs, `[`/`]` traverse supplied chemical bonds, Enter pins, Esc clears, and a
mouse click uses the retained hit map. Press : for deterministic commands such
as `:select C12`, `:inspect`, `:stereo`, `:name`, and `:why`.

## Common recipes

### Publication-quality ball-and-stick (JACS style)

```bash
mat-vis render mol.cif -o fig.png \
  --view formula_unit \
  --no-hydrogen --show-cell \
  --atom-scale 0.9 --bond-radius 0.14 \
  --background "#FFFFFF" \
  --width 1200 --height 900 --scale 3
```

### Full unit cell overview

```bash
mat-vis render crystal.cif -o cell.png \
  --backend cpu --view unit_cell \
  --no-hydrogen --show-cell \
  --atom-scale 0.7 --bond-radius 0.12
```

### ORTEP for crystallographic papers

```bash
mat-vis render crystal.cif -o ortep.pdf \
  --backend cpu --style ortep --shading flat \
  --ortep-mode ortep_hatch --show-labels \
  --ortep-probability 0.5 --missing-adp-policy error
```

### Orthographic projection (no foreshortening)

```bash
mat-vis render crystal.cif -o ortho.png \
  --orthogonal --atom-scale 1.0 --camera-distance 2.5
```

### Interactive HTML for SI / supplementary info

```bash
mat-vis render crystal.cif -o si_figure.html \
  --backend plotly --view unit_cell --show-hydrogen --show-labels
```

---

## Troubleshooting

### Optional capability is not installed

Ask the resolver instead of guessing packages:

```bash
mat-vis capabilities --require plotly-export --json
mat-vis capabilities --require web-screenshot --json
mat-vis capabilities --require static-web-export --json
mat-vis render structure.cif -o figure.png --backend plotly --check --json
```

Ordinary CPU PNG/PDF/SVG does not require Kaleido or Chrome.
Both Web screenshot aliases intentionally resolve to `[web,plotly-export]`;
the service frontend and its static-image encoder are separate capabilities.

### Large cell runs out of memory

For cells with >500 atoms, reduce supersampling:

```bash
mat-vis render big.cif -o fig.png --scale 1
```

If the scientific request is a local fragment, select it by a public molecule
or source identifier before rendering; `cluster` does not crop a periodic cell.

### Fonts not rendering correctly in PDF

Use a Unicode-capable font already available in the execution environment.
MatterVis does not install operating-system packages automatically.

### Terminal depth and layers

`mat-vis tui INPUT` shows depth bands (`¹` front, `²` middle, `³` back;
ASCII-7: `^1`, `^2`, `^3`) and a persistent rotating a/b/c compass and scale
bar. `--no-depth` hides band suffixes and styling; `--no-axes` hides the compass.
Back bonds are dashed; crossing bonds have a gap in the farther line.
`h` toggles hydrogen, `Shift+B` toggles periodic boundary copies, and `Shift+A`
toggles the compass. Dense automatic atom labels retain element symbols.

`--parallax` or `g` enables experimental previous-rotation ghost markers.
`f` toggles depth slicing around the median layer; `,` / `.` step down/up.
Use `:slice c 2 0.25` for lattice layers, `:slice depth 3 1.0` for a depth
layer, and `:slice off` to restore the full picture. Adjacent layers are dim
and parenthesized; other layers are hidden. The CLI equivalents are
`--slice-axis c --slice-index 2 --slice-width 0.25`. Width is in fractional
coordinates for a/b/c and Angstrom for camera depth.

Agent JSONL sessions accept `set_slice`, `step_slice`, and
`rotation_sequence` (1–12 frames, default three 10-degree yaw steps); the latter
returns an `observations` array so each picture can be compared independently.
See [the TUI API](agents/tui_api.md) for the state and response contracts.
