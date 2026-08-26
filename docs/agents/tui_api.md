# Terminal-view controller API

Use this API when a Python caller or local agent adapter needs to control a
MatterVis terminal view semantically. It shares the terminal renderer and
canonical `CrystalIR`/MolCrysKit loader path with `mat-vis tui`; it is **not** an
HTTP service and it does not create a second chemistry or render pipeline.

When the installed MolCrysKit exposes its chemistry contract, the loader also
copies stable atom identities, bond order/type, entity dimensionality,
stereochemical descriptors, CIP order, warnings, and evidence into immutable
`CrystalChemistryRecords`. MatterVis does not infer any of those values from
screen distances. Older MolCrysKit releases leave `CrystalIR.chemistry` unset.

```python
from mat_viewer.tui import TerminalViewController

view = TerminalViewController.from_file("structure.cif", width=80, height=22)
view.set_camera(azimuth=45, elevation=30, projection="orthographic")
view.set_display(label_mode="element", show_cell=False, show_bonds=True)
observation = view.observe()
payload = observation.as_dict()
```

## Construction and state

- `TerminalViewController(crystal, ...)` accepts an already loaded terminal
  `CrystalIR`.
- `TerminalViewController.from_file(path, display_mode="auto", ...)` loads via
  the canonical terminal loader.
- `state` returns a detached `TerminalViewState`; each successful active
  view-state mutation increments its monotonic `revision` exactly once.
  Snapshot-registry operations (`save_view`, `list_views`) do not alter active
  view state and therefore do not increment it.
- `observe()` returns `TerminalObservation` with schema
  `mattervis.tui.observation/v1`. `as_dict()` is JSON-safe.

The structured observation returns terminal frame text, state, title, scoped
canonical/display/visible counts, capabilities, and warnings. It intentionally
does **not** include atom coordinates, projected depth, pair distances,
front/back answers, collision scores, or a recommended camera.

## Perceptual controls

| Method | Meaning |
| --- | --- |
| `set_camera(azimuth=..., elevation=..., roll=..., target=..., projection=..., zoom=..., pan_x=..., pan_y=...)` | Apply absolute partial camera state. `target` is a Cartesian three-vector retained in observation state for reproducible projection. |
| `orbit(yaw_deg=..., pitch_deg=..., roll_deg=...)` | World-up turntable motion: yaw is about Cartesian `+Z`; pitch is about current screen-right; roll is about view axis. |
| `align(axis)` | Look along crystallographic `a`, `b`, `c`, `a*`, `b*`, or `c*`; uses real/reciprocal lattice directions. |
| `pan(dx=..., dy=...)`, `zoom(factor=...)` | Move/crop the stable terminal viewport. |
| `fit(target="all"\|"focus")` | Explicitly refit; orbit and display toggles do not refit. |
| `set_display(...)` | Partial absolute update for `display_level`, `label_mode`, `show_cell`, `show_bonds`, `show_minor`, and `mono`. |
| `set_selection_mode(active)` | Enter or leave atom selection while retaining the current stable atom identity. |
| `select_atom(reference)`, `select_next(step=...)` | Select by exact reference or traverse visible atoms in stable atom-id order. |
| `select_direction(dx=..., dy=...)`, `select_screen(row=..., col=...)` | Select from the retained projection hit map; no chemistry is inferred from screen distance. |
| `select_neighbor(step=...)` | Traverse only bonds already supplied by MolCrysKit/`CrystalIR`. |
| `pin_selection()`, `clear_selection()` | Keep a highlight after leaving Select mode or clear it explicitly. |
| `focus_local(reference, bond_depth=1)` | Fit one exact displayed atom and its manifested bond neighborhood. |
| `reset_view()` | Restore startup camera and all-view framing while preserving display settings. |

The controller fits at construction, explicit `fit`, resize, and reset. It
keeps fit bounds fixed while rotating, panning, changing label mode, or toggling
cell/bond/minor visibility. This prevents the previous auto-fit “breathing”.

## Focus and saved views

- `focus_atom(reference)` and `focus_selection(references)` accept an exact
  label, `source_index`, `display_copy_id`, or an explicit mapping containing
  one of those keys.
- `focus_molecule(reference)` accepts `source_molecule_index`,
  `display_molecule_index`, or `display_fragment_id`.
- Focus performs a local fit but leaves unselected atoms/molecules rendered as
  context. It never filters `CrystalIR`, regenerates copies, or changes display
  visibility. A target hidden by `show_minor=False` raises `ValueError` and
  leaves state unchanged.
- A label means every currently manifested matching source/display copy; callers
  needing one copy must pass `display_copy_id`.
- `save_view(name, overwrite=False)`, `restore_view(name)`, and `list_views()`
preserve camera, display, focus, selection, and stable fit bounds. Snapshots never store
structure data or chemistry results.

The compositor retains a `ProjectedAtomHit` for every visible atom using the
same projection and viewport as the ASCII frame. Selection is held by the
manifested copy and carries the stable MolCrysKit `atom_id`; camera rotation
therefore moves `[C12]` without changing which atom is selected. Brackets are
part of the plain text, so selection remains legible when ANSI color is off.

Selecting an atom opens a chemistry inspector. At terminal widths of 100 or
more it occupies a right-hand column; narrower terminals place the identical
plain-text inspector below the viewport. It reports site provenance,
occupancy/disorder, coordinates, MolCrysKit atom and entity records, manifested
distances/angles, MCK bond semantics, ring provenance, stereochemical status,
CIP order, crystal metadata, deposited CIF names, and absolute-structure
parameters with their standard uncertainties. Missing MCK records are printed
as `unavailable`; the inspector never fills gaps from screen distances.

MolCrysKit inference or ambiguity warnings remain in a one-line warning bar
above the main viewport. `:why` expands the evidence, status, retained
alternative count, and complete warning text. Deposited CIF systematic/common
names remain explicitly labelled as source metadata and are not presented as
newly validated IUPAC names.

## Analytical inspection

`inspect_atom(references=None)` and `inspect_molecule(reference=None)` are pure
reads. They return JSON-safe canonical loader provenance: source/display IDs,
occupancy, disorder render classification, periodic copy shift, display
fragment/source molecule IDs, raw MolCrysKit species ID, and per-formula-unit
counts.

`inspect_local_geometry(reference, include_angles=True)` is also a pure read.
It resolves exactly one displayed atom and reports the current manifested bond
neighbors, coordination number, rendered/direct and minimum-image distances,
periodic image shifts, neighbor-pair angles, and topology provenance. It
describes the topology already present in `CrystalIR`; it does not perceive new
bonds or label any coordination, distance, angle, or ring as chemically normal
or abnormal. Ambiguous labels must be replaced by an exact `display_copy_id`.
`inspect_local_geometries(references=None, include_angles=True)` batches the
same records; omitted references mean every manifested atom. This is intended
for bounded agent audits where one-tool-call-per-atom would waste the action
budget. It adds no anomaly classification or replacement topology.

`measure_distance`, `measure_angle`, and `measure_dihedral` are explicit,
read-only measurements over caller-selected atoms. Distance and angle support
`direct`/`mic`; dihedral supports `direct`/`mic_chain`. Every result reports
the image shifts actually used. Labels are accepted only when they resolve to
exactly the requested number of displayed atoms; ambiguous labels require
`display_copy_id`.

These are analytical product capabilities. Active-view evaluations must omit
them and must not offer direct answers such as distances, neighbor queries,
front/back classification, collision counts, or `best_camera`.

`render_classification="not_minor"` only means the current loader did not mark
the atom as a minor disorder image. It does not prove that a partial-occupancy
site is ordered or major.

## Keyboard compatibility

`mat-vis tui` retains keyboard control through the same controller:

- `q/e`, `w/z`, `a/d`: yaw/pitch/roll.
- Outside Select mode, arrows or `i/j/k/l`: pan.
- `s`: enter/leave Select mode. In Select mode, arrows choose the nearest atom
  in that projected direction, `Tab`/`Shift+Tab` traverse stable atom IDs,
  `[`/`]` traverse manifested bond neighbors, `Enter` pins, and `Esc` clears.
- Clicking the canvas selects the nearest projected atom.
- `u` zooms out; `o` zooms in. Existing `+/-` and `[/]` aliases remain.
- `p`, `c`, `b`, `t`, `m`, `n`, `Shift+L`, `r`: projection, cell, bonds,
  labels, monochrome, minor disorder, display level, and reset.
- `x`: quit. `Ctrl+Q` is intentionally not used because it conflicts with
  common terminal/editor shortcuts.

Legacy static `ascii` and `structured` CLI output stays unchanged. The
controller observation is the machine-readable local API.

## Interactive command mode

Press `:` in `mat-vis tui` to open a one-line command prompt:

- `:select C12`, `:next atom`, `:clear`
- `:inspect [C12]`, `:stereo [C12]`, `:name [C12]`, `:why [C12]`
- `:focus N9 [bond_depth]`
- `:distance A B [direct|mic]`
- `:angle A B C [direct|mic]`
- `:dihedral A B C D [direct|mic_chain]`
- `:help`

Measurements use the same controller methods as programmatic callers; the UI
does not implement a second geometry path. Command results are transient view
text and do not mutate the source structure or manifested topology.

## Visual verification artifacts

The checked-in `verification_screens/tui_controller/` text frames are generated
by `scripts/10_tui_controller_visuals.py`. They cover initial/orbited mono
views, focused mono context, disorder-visible molecule mode, and ANSI-colour
molecule mode for DAP-4 at 80×22.
