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
  `cutoff=` is the hard cap. See `agents/polyhedron_api.md` for the
  full field table.
- Shape classification: use `shape.classify_shell` (not the deprecated
  `angular_rmsd_vs_ideals`). Pass `max_strip=0` for clean labels.

### Molecular fragmentation

- `_fragment_table_from_atoms` consumes `molcrys_analysis.mol_indices`
  directly. Do not reintroduce a parallel `ops.find_bonds → cluster`
  path — it breaks on disorder + special-position structures.
- Atoms must carry `_source_index` pointing back to `raw_atoms` so
  molecule lookup works for translated copies (formula-unit, repeat).

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
  `crystal_viewer/transforms/core.py` is a thin adapter — add missing
  params as passthrough kwargs, don't duplicate the math.

### When molcrys_kit deprecates a function

1. Read both the new module's docstring and the deprecation note.
2. Surface new fields in `agents/*.md`; don't silently coerce.
3. If the replacement is more expensive, push it into a cache layer
   with a key that includes every input affecting the result.

## MatterVis-only code — intentionally not upstream

These paths look like duplicate chemistry but are intentional.
Do not delete them in favour of a `molcrys_kit` call unless the
upstream API has grown the exact hook.

- **Scene bond perception** (`scene.py` → `ops.find_bonds`): runs on
  manifested display atoms (formula-unit slice, boundary replicas,
  transformed cluster), not the original raw atom list.
- **Transform bond perception** (`transforms.py`): re-detects bonds
  after grow/repeat because transformed scenes are no longer a
  periodic unit cell.
- **Cube bond helpers** (`cube.py`): operate on Gaussian-cube cluster
  coordinates, not crystallographic PBC.
- **Minor-disorder outlines** (`renderer.py`): visual annotations
  coloured from per-atom render colours, not hard-coded ink.
- **Lattice matrix convention**: MatterVis uses row vectors
  (`cart = frac @ M`), matching ASE/pymatgen/molcrys_kit. The static
  CIF parser returns column vectors — convert once at the boundary.
- **CIF symmetry expansion**: minimal expansion at the loader boundary
  for non-P1 CIFs missing explicit symmetry ops. This is a rendering
  precondition, not chemistry.
