# Diagnose and Select the Scene

Read this before selecting a MatterVis display mode or formal-figure style.

## Required diagnosis

Record, where available:

- raw/asymmetric-site and symmetry-expanded atom counts;
- selected-scene atom and bond counts;
- major and minor occupancy counts and fraction;
- fragment count and formulas or species;
- formula/moiety, disorder, bond-table, parser, and performance warnings.

A decoded export with an untrustworthy object selection is a diagnostic artifact,
not a publication figure.

MatterVis 0.0.2 has no `diagnose` subcommand. Use capability tiers:

1. For an admitted small scene, `mat-vis tui INPUT --no-interaction --format
  structured --display <mode>` reports formulas, source/expanded/displayed/
  visible atom counts, cell, atoms, disorder markers, and bonds. It is an
  unbounded per-atom serialization, so do not use it automatically above 200
  visible atoms.
2. For large or ambiguous CIFs, use the canonical Python API:

```python
from crystal_viewer.loader.core import build_loaded_crystal, build_bundle_scene

bundle = build_loaded_crystal(name="input", cif_path="INPUT.cif")
scene = build_bundle_scene(bundle, display_mode="asymmetric_unit")
print("raw", len(bundle.raw_atoms))
print("formula_unit", len(bundle.formula_unit_atoms))
print("fragments", len(bundle.fragment_table))
print("displayed", len(scene["draw_atoms"]), "bonds", len(scene["bonds"]))
```

Capture Python warnings around the loader call. The function is keyword-only and
can be expensive because loading performs symmetry expansion and MolCrysKit
analysis before display-mode reduction.

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

For opacity rendering, use a style config of this shape:

```json
{
  "style": {
    "disorder": "opacity",
    "minor_opacity": 0.15
  }
}
```

Do not pass an object as the `disorder` value. If the CLI/API cannot express the
needed policy, report that limitation rather than forcing a formal figure.

Supported image disorder modes are `opacity`, `dashed_bonds`, `outline_rings`,
`color_shift`, and `none`. Image render has no `--show-minor`, `--hide-minor`, or
canonical `--major-only` flag. To hide rendered minor instances, use an
`atom_groups` selector on `is_minor`; this changes visibility, not chemistry:

```json
{"style":{"atom_groups":[{"id":"hide-minor","selector":{"is_minor":true},"visible":false}]}}
```

TUI `--hide-partial` removes all occupancy below approximately 0.99, not only
minor disorder alternatives.

## Config precedence

`--config` may be flat or contain `{"style": {...}}`. Config-only fields such
as `disorder`, `minor_opacity`, and `atom_groups` survive. Every field represented
by a normal render CLI option is overwritten by that option's parser value even
when the flag was omitted; set style, material, projection, visibility, scale,
and colours explicitly on the command line.

## Warning classes

- **export**: backend/export failure; an explicit visual-language fallback may proceed;
- **display**: dense scene or likely occlusion; label the output diagnostic;
- **chemistry**: formula, moiety, disorder, or bond-table degradation; block an automatic formal-figure claim;
- **semantic-fatal**: selected content cannot be shown to match the target; stop or deliver an explicitly labelled diagnostic.
