# Molecule Focus and Mixed Styles

Read this only when one complete molecule or fragment must be highlighted over
structural context.

Use MatterVis's canonical loader. `build_loaded_crystal(...)` runs MolCrysKit and
records its native molecule identity as
`fragment_table[*].source_molecule_index`; it also supplies continuous
`mol_cart_positions`, canonical bonds, and whole-fragment boundary replicas.
Never reconstruct molecules, PBC geometry, bonds, or covalent cutoffs in an ad
hoc plotting script.

Discover the target identity before rendering. For terminal-supported inputs,
`TerminalViewController.inspect_molecule(...)` reports
`source_molecule_index`, and
`focus_molecule({"source_molecule_index": N})` gives a deterministic inspection
view. In a publication scene, resolve the same MCK index through
`scene["fragment_table"]` and all matching `site_indices`; do not substitute the
display row index or label such as `A0`.

For wireframe context with one ball-stick molecule:

1. keep the scene-level style as `wireframe`;
2. add an `atom_groups` rule selecting all resolved target site indices;
3. override those atoms with `material="mesh"` and `style="ball_stick"`;
4. preserve real element names and canonical scene bonds.

MatterVis currently has no `--highlight-molecule-index` render flag, and
atom-group style overrides do not partition bonds. Do not claim that a
convenience command or fully mixed bond style exists. State the limitation and
never replace canonical bonds with screen-distance inference. A future direct
selector should target `_source_molecule_index`.
