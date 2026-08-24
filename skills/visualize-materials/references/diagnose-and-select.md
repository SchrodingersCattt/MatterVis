# Diagnose and Select the Scene

Read this before selecting a MatterVis display mode or formal-figure style.

## Required diagnosis

Start with the bounded public command:

```bash
mat-vis inspect INPUT --json
```

Record, where available:

- `periodic`, `pbc`, `has_lattice`, and `synthetic_cell`;
- raw/asymmetric-site and symmetry-expanded atom counts;
- selected-scene atom and bond counts;
- major and minor occupancy counts and fraction;
- fragment count and formulas or species;
- formula/moiety, disorder, bond-table, parser, and performance warnings.

For a uniformly compressed or expanded structure, diagnose bonding with one
global MolCrysKit `bond_scale` first. Values below `1.0` tighten all calibrated
cutoffs; values above `1.0` loosen them. Keep the same scale through molecule
identification, periodic unwrapping, and visible-bond construction. Do not use
the unrelated render-width controls (`bond_radius` or `scatter_bond_scale`) to
change chemical connectivity.

Choose a scale from the actual structure rather than from a generic pressure
label. Verify that every intended covalent or coordination bond remains and
that the shortest compressed intermolecular contacts remain excluded. Use
explicit element-pair thresholds only when no single global scale separates
those two sets; record that exception as chemistry provenance.

A decoded export with an untrustworthy object selection is a diagnostic artifact,
not a publication figure.

Do not start Dash, the TUI, or a private loader merely to discover counts. For
custom Python automation use only `mat_viewer.load_structure`; the returned
structure consumes MolCrysKit's public site and bond records. A missing public
MolCrysKit contract is fatal and must not fall back to `.info` fields.

Auto mode uses `unit_cell` with visible cell edges for periodic inputs and
`cluster` without cell edges for nonperiodic inputs. A synthetic padding cell
created only to carry finite coordinates is nonperiodic metadata, not a box to
draw. Override auto only for a stated scientific reason.

## Display modes

- `formula_unit` is a MolCrysKit chemical selection, not a guarantee of one
  compact molecule. Do not choose it automatically when it exceeds 500 atoms,
  retains over 50% of expanded atoms, contains over 25% minor atoms, or emits a
  formula/moiety warning.
- `asymmetric_unit` deduplicates by label, element, disorder group, and disorder
  assembly. It is useful for diagnosis but is not raw `_atom_site` or major-only.
- `unit_cell` gives crystal context and normally adds complete boundary-fragment
  replicas. Disclose that behavior; the image CLI does not expose strict
  `include_boundary_replicas=False`.
- `cluster` disables PBC for an already finite input. It does not select a centre,
  radius, molecule, or connected component.

For a local environment, first extract an auditable target or use an API that
accepts the intended centre/fragment. Do not use bare `cluster` as a crop tool.

If the target contains a 1D chain, 2D layer, or 3D framework and the default
`unit_cell` boundary replicas are too dense or visually one-sided, read
`periodic-finite-views.md`. This is a topology/window-selection problem, not a
camera, clipping, atom-scale, or bond-radius problem.

## Disorder strategy

High-disorder scenes need an explicit choice: major-only, hidden minor sites,
opacity, or an ASU diagnostic. `minor_opacity` does not reduce
`disorder="outline_rings"`.

When minor sites exceed 25%, never run the unmodified ball-stick/mesh defaults as
the primary candidate. For a crystal-context view, start with `unit_cell`, hidden
hydrogen, `disorder="opacity"`, `minor_opacity` between 0.08 and 0.15,
`atom-scale` between 0.65 and 0.8, and `bond-radius` between 0.08 and 0.12. If the
scene remains dense, deliver an ASU diagnostic rather than shrinking a black
unit-cell tangle and calling it formal output.

For an ordered crystal scene above 500 displayed atoms, use one deterministic
low-noise first candidate rather than trying a gallery: `unit_cell`, hidden
hydrogen, orthographic lattice `+c`, `ball_stick`, `mesh`, white background,
hidden axes and labels, `atom-scale=0.65`, `bond-radius=0.08`, `2400x1800`, and
`scale=1`. This preserves the crystal context while reducing occlusion. If the
target is one local event or molecule rather than the whole cell, use the
auditable focus workflow instead of drawing the entire dense box.

The current agent render CLI has no public disorder-style or major-only switch.
It rejects the legacy `--config` escape hatch instead of accepting an option it
cannot translate to `RenderSpec`. If a formal figure needs a different disorder
policy, report that limitation and stop; do not invoke a private Web normalizer.

TUI `--hide-partial` removes all occupancy below approximately 0.99, not only
minor disorder alternatives.

## Warning classes

- **export**: backend/export failure; stop and preserve the requested backend;
- **display**: dense scene or likely occlusion; label the output diagnostic;
- **chemistry**: formula, moiety, disorder, or bond-table degradation; block an automatic formal-figure claim;
- **semantic-fatal**: selected content cannot be shown to match the target; stop or deliver an explicitly labelled diagnostic.
