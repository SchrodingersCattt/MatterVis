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

## Warning classes

- **export**: backend/export failure; an explicit visual-language fallback may proceed;
- **display**: dense scene or likely occlusion; label the output diagnostic;
- **chemistry**: formula, moiety, disorder, or bond-table degradation; block an automatic formal-figure claim;
- **semantic-fatal**: selected content cannot be shown to match the target; stop or deliver an explicitly labelled diagnostic.
