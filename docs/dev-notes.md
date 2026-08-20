# Developer notes — implementation details

This file holds implementation-level details that were previously in
AGENTS.md. They are important for developers working on specific
modules but are too granular for the top-level developer contract.

## MolCrysKit delegation — detailed rules

The boundary rule ("molcrys_kit owns chemistry, MatterVis owns
rendering") is in AGENTS.md. Below are the concrete delegation
points and pitfalls.

### Polyhedra / coordination shells

- `find_polyhedra` does gap+enclosure detection for both atom-level
  and molecule-centroid packing shells. Do not wrap it with hard-coded
  covalent cutoffs.
- On `level="molecule"` the kwarg `cutoff=` is the candidate search
  radius (gap+enclosure picks the natural shell). On `level="atom"`,
  `cutoff=` is the hard cap. See `docs/agents/polyhedron_api.md` for the
  full field table.
- Shape classification: use `shape.classify_shell` (not the deprecated
  `angular_rmsd_vs_ideals`). Pass `max_strip=0` for clean labels.

### Molecular fragmentation

- `_fragment_table_from_atoms` consumes `molcrys_analysis.mol_indices`
  directly. Do not reintroduce a parallel `ops.find_bonds → cluster`
  path — it breaks on disorder + special-position structures.
- Atoms must carry `_source_index` pointing back to `raw_atoms` so
  molecule lookup works for translated copies (formula-unit, repeat).
- Input bond-scale and pair overrides are forwarded into
  `molcrys_bridge.analyze`. Every display mode and transform then projects that
  analysis's signed `BondRecord` graph through SiteRecord source/image
  identities. Do not add a manifested-scene re-detection pass.

### SHELX-style occupancy disorder

- Must go through `generate_ordered_replicas_from_disordered_sites`.
- Two trigger patterns: (1) sibling labels with `occ < 1` and blank
  disorder tags; (2) `occ < 1` with `dg` starting with `"-"`.
- The matcher tags discarded images with `_is_minor=True` AND kept
  images with `_is_minor=False` — both flags are mandatory.
- Disorder selection uses MolCrysKit's kept indices
  (`return_kept_indices=True`), not local Cartesian matching.
- Minor atoms are excluded from the bond graph via
  `identify_molecules(exclude_indices=...)`.
- Cross-orientation bonds are filtered at scene build time (bonds
  whose endpoints disagree on `is_minor` are skipped).

### Slab generation

- Delegate to `molcrys_kit.operations.surface.generate_topological_slab`.
  `mat_viewer/transforms/core.py` is a thin adapter — add missing
  params as passthrough kwargs, don't duplicate the math.

### When molcrys_kit deprecates a function

1. Read both the new module's docstring and the deprecation note.
2. Surface new fields in `docs/agents/*.md`; don't silently coerce.
3. If the replacement is more expensive, push it into a cache layer
   with a key that includes every input affecting the result.

## MatterVis-only code — intentionally not upstream

These paths look like duplicate chemistry but are intentional.
Do not delete them in favour of a `molcrys_kit` call unless the
upstream API has grown the exact hook.

- **Scene/transform bond projection**: lifts MolCrysKit BondRecords onto
  manifested SiteRecord source/image identities; it is visual mapping, not
  chemical perception.
- **Cube mesh helpers**: operate on scalar-field geometry. Cube atom bonds
  still require explicit MolCrysKit BondRecords.
- **Minor-disorder outlines** (`renderer.py`): visual annotations
  coloured from per-atom render colours, not hard-coded ink.
- **Lattice matrix convention**: MatterVis uses row vectors
  (`cart = frac @ M`), matching ASE/pymatgen/molcrys_kit. The static
  CIF parser returns column vectors — convert once at the boundary.
- **CIF symmetry expansion**: MolCrysKit owns the expanded public SiteRecords;
  MatterVis does not run an independent symmetry path.

## Terminal semantic controller boundary

- `mat_viewer.tui.controller.TerminalViewController` owns terminal camera,
  display, focus, stable viewport bounds, and named view snapshots. Textual
  keyboard input and local agent adapters are both thin clients of this single
  controller.
- `CrystalIR` remains immutable in spirit across view controls. Orbit, fit,
  focus, label/cell/bond/minor toggles, and snapshots must not regenerate
  bonds, periodic copies, molecule partitions, or disorder assignments.
- Loader adapters retain MolCrysKit source-molecule membership and species IDs
  in `CrystalIR.source_molecules` / `source_molecule_species` for inspection.
  Do not reconstruct molecules from displayed bonds or hulls.
- The terminal camera uses a scene-to-viewer depth vector: larger projected
  depth is closer. Turntable yaw is around Cartesian world `+Z`; pitch is
  around the current screen-right axis. The world scene stays fixed.
- The compositor may receive explicit stable viewport bounds. Do not quietly
  re-fit during orbit or display toggles, or the terminal view will breathe.
  Refit only at construction, explicit fit/reset, and explicit semantic resize.
- `inspect_atom` / `inspect_molecule` / `inspect_local_geometry` /
  `inspect_local_geometries` are analytical
  reads. Local geometry consumes the manifested `CrystalIR.bonds` topology and
  may derive direct/MIC distances and neighbor-pair angles, but must not infer
  replacement bonds or emit an abnormality verdict. Include topology
  provenance so callers can distinguish canonical scene bonds from a
  distance-heuristic non-CIF topology.
  observations intentionally omit coordinates, depths, distances, front-order,
  collision scores, and recommended cameras. Active-view evaluation must not
  register analytical inspection tools.
  - Interactive `:` measurements are thin wrappers over controller measurement
    methods. Angle MIC is center-anchored; dihedral `mic_chain` unwraps A-B-C-D
    consecutively. Keep the applied image shifts in the returned payload and do
    not move this geometry math into Textual event handlers.
