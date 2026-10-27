# Display, Operate, and Analysis

Baseline for this refactor: `4a5b4da53cda177e74e89d086792b68ed3771bdb`.
MolCrysKit pin: `9bf0ffb0fb321d1084dbc444250b5f186af3c873`
(`molcrys-kit>=0.8.0,<0.9.0` in the published range; the checkout uses
that git commit because `BondRecord.role` and
`get_connectivity_report()` are not in the PyPI 0.8.0 release).

## Effects

```text
Display:  (S, V, request) -> (S, V')
Analysis: (S, Q)          -> AnalysisResult
Operate:  (S, command)    -> Candidate -> validated commit -> S'
```

`EffectSession` in `mat_viewer/effects/session.py` is the only component
that appends a structure revision. `DisplayService` writes view
transforms and layer bindings. `mat_viewer.effects.analysis` reads a
snapshot and does not consult `topology_enabled`, camera, color, or
visibility.

`geometry_version` remains the view-geometry clock. `StructureRef.revision`
is the scientific clock. They are not interchangeable.

## What stayed display

- `POST /api/v2/state` field `supercell` replaces one `repeat`
  transform. `1×1×1` removes that repeat and leaves other transforms.
- `/api/v2/transforms` keeps ordered composition, enabled flags, and
  `auto_promote`.
- `by_symmetry` is still an explicit unsupported input.
- `scene_for_state` returns a derived scene and does not assign
  `bundle.scene` or `bundle.fragment_table`. Source metadata stays on
  the load-time scene and the scientific snapshot.

## Structural commands

| Command | MolCrysKit entry | Commit result |
|---|---|---|
| `build_supercell` | `create_supercell` | positive integer diagonal repeats only |
| `build_slab` | `generate_topological_slab` | slab lattice, PBC, and vacuum |
| `insert_molecule` | molecule construction plus clash check | no energy minimization |
| `delete_sites` | explicit site ids | source sites, not display indexes |
| `replace_species` | explicit site ids | occupancy is not rewritten |
| `add_hydrogens` | `add_hydrogens` | torsion search is rejected |
| `remove_solvent` | `remove_solvents` or explicit molecule indexes | no automatic solvent guess |
| `materialize_selection` | explicit site/image pairs | finite cluster, `PBC=False` |
| `commit_disorder_candidate` | ordered-replica crystal | highlight indexes are not the structure |

`delete_periodic_copy` is one transaction: build the supercell, delete
the mapped image, commit once.

Present site or structure properties require a policy of `copy`,
`transform`, `recompute`, or `drop` with a reason. Energy and stress are
not silently copied onto a supercell.

## Persistence

Internal sessions use schema `mattervis.effects/v1` and rebuild the
crystal from stored molecules, occupancies, and site ids. Legacy scene
files migrate to view configuration, copy a `.bak` once, and commit
zero structures. Unknown schemas and unknown structure names fail.
CIF export reports fields it does not preserve.

## Removed

The mixed `_OperationsBackendMixin`, `_STRUCTURAL_OP_KINDS`, and
`_DISPLAY_TO_STRUCTURAL_HINT` are gone. Transform and polyhedron-layer
bodies live in `DisplayService`. `molecular_crystal_from_scene` rejects
rendered traces and materializes only an explicit `scientific_snapshot`.
There is no `mat_viewer.ops.source` or `mat_viewer.ops.display` package.
