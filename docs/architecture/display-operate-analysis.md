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
| `translate_atom`, `translate_molecule`, `rotate_molecule`, `rotate_fragment` | MolCrysKit coordinate operations | source site IDs; original graph retained; no relaxation. Rotations apply `R` to Cartesian vectors and `R U R^T` to `u_cart` / `adp` on moved atoms |
| `reperceive_bonds` | `MolecularCrystal.from_ase` | revision-local scale and element-pair thresholds; source site IDs mapped; image shifts rebased to the unwrapped coordinates; stale `frac_x/y/z` arrays dropped |
| `add_hydrogens` | `add_hydrogens` | torsion search is rejected |
| `remove_solvent` | `remove_solvents` or explicit molecule indexes | no automatic solvent guess |
| `materialize_selection` | explicit site/image pairs | finite cluster, `PBC=False` |
| `commit_disorder_candidate` | ordered-replica crystal | highlight indexes are not the structure |

`delete_periodic_copy` is one transaction: build the supercell, delete
the mapped image, commit once.

Present site or structure properties require a policy of `copy`,
`transform`, `recompute`, or `drop` with a reason. Energy and stress are
not silently copied onto a supercell. For a rotation, `transform` rotates
Cartesian `forces` and `velocities` and replaces `adp` with `R U R^T`.
That rotation applies to the moving rigid body, including a bond-axis
atom whose position stays put. The fixed side keeps its original tensor.
`copy` keeps the stored numbers. Preview candidates are readable at
`GET /api/v2/operate/candidates/<candidate_id>` before commit.

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

## Phone snapshots

`mat_viewer.phone` is the Android-facing facade over the same effects. It
imports no desktop chemistry (ASE, SciPy, pymatgen, spglib, Matplotlib,
Pillow) on its own path; the MolCrysKit commit that provides the phone
path is named in `pyproject.toml`.

`mat_viewer/phone/` is a folder-first package: `__init__.py` re-exports the
public functions below and `snapshot.py` holds the implementation, so
`from mat_viewer.phone import analyze_cif` is unchanged.

| Function | Returns |
|---|---|
| `analyze_cif(path)` | Revision-1 snapshot: `cell`, `sites` (Cartesian `position`, `fractional_position`, `image_shift`, `occupancy`, `alpha`, `disorder_group`, `disorder_assembly`), `bonds` |
| `display(snapshot, disorder_group=None)` | View-only copy; sets `visible`, never `occupancy` |
| `operate_commit_major(path)` | Snapshot of the committed major disorder component (`operation = "commit_disorder_candidate"`, `revision = 2`) |
| `operate_commit_major_with_cif(path)` | `{"snapshot": <identical to operate_commit_major>, "cif": <str>}` |

Each entry of `bonds` is a pass-through of one MolCrysKit `BondRecord`:

| Key | Meaning |
|---|---|
| `left`, `right` | `left_global_index`, `right_global_index` |
| `role` | `"covalent"` or `"coordination"`, verbatim from `BondRecord.role` |
| `vector_A` | Canonical Cartesian bond vector in Å, drawn from the `left` site |
| `right_image_shift` | Integer lattice translation of the right site relative to the left site |

`vector_A` and `right_image_shift` were added to the original
`left`/`right`/`role` keys; existing keys are unchanged. A client must draw
a bond as `position[left]` to `position[left] + vector_A`. Connecting the
two displayed endpoints is wrong for periodic-image bonds, where
`position[right] - position[left]` differs from `vector_A`. Self-image bonds
(`left == right`, non-zero shift) are valid. Draw sticks for `covalent`;
`coordination` bonds are kept for topology and are not sticks. MatterVis
copies these fields and derives no chemistry.

Each entry of `sites` also passes through two MolCrysKit `SiteRecord` fields:

| Key | Meaning |
|---|---|
| `fractional_position` | `SiteRecord.fractional_position`, three floats, verbatim |
| `image_shift` | `SiteRecord.image_shift`, three ints: lattice translation used to make the molecule contiguous |

These were added next to the existing site keys; `position`, ordering and
all existing keys are unchanged. `position` stays the contiguous-molecule
Cartesian site, so `fractional_position` can lie outside `[0, 1)`. A client
that offers a unit-cell view may wrap a site with
`fractional_position - floor(fractional_position)` and convert with `cell`.
`image_shift` is per-site provenance only; it is not a generally valid
whole-molecule or home-cell wrapping shortcut, so do not wrap by subtracting
it; a periodic bond is drawn from the wrapped `left` using
`vector_A`. Wrapping is a display choice; MatterVis derives no chemistry and
the snapshot keeps the contiguous view by default.

`operate_commit_major` and `operate_commit_major_with_cif` share one
internal operation: a single `EffectSession.operate` call and a single
MolCrysKit replica generation. The CIF is MolCrysKit's public
`write_cif(crystal, representation="p1")` applied to the same checked-out
crystal that produced the snapshot — never to display coordinates and never
after a second chemistry run. The CIF is a complete P1 cell and re-reads
through `analyze_cif` to the same cell, symbols, positions and occupancies.
The writer must be importable without ASE; that is a MolCrysKit contract,
and the phone-like test in `tests/test_phone_snapshot.py` enforces it.


### Multiple ordered disorder candidates

`generate_disorder_candidates(path, *, method="enumerate", count, seed=None,
coupled=False, independent_assemblies=False, max_candidates=16,
max_payload_bytes=8 MiB)` (in `mat_viewer/phone/candidates.py`) is additive;
the four functions above are unchanged. It calls MolCrysKit's
`generate_ordered_replicas_from_disordered_sites` exactly once and commits
each returned replica through its own `EffectSession` registered from the
original source, so candidates are siblings (each snapshot is `revision = 2`
of its own session), never a chain. The source file is not modified and is
not replaced by any candidate; Android keeps the original.

Result (JSON-serializable, `schema_version` 1):

```
{method, requested_count, seed, coupled, independent_assemblies,
 actual_count, unique_count, source: {path, sha256, bytes},
 candidates: [{candidate_id, index, kept_indices, duplicate_of,
               fingerprint, snapshot, cif}],
 warnings: [{source, category, message}]}
```

- `candidate_id` is `candidate-NNN`, the 1-based position in MolCrysKit's
  return order; order is preserved. `snapshot` has the same shape as
  `operate_commit_major`; `cif` is the P1 `write_cif` text of the same
  checked-out crystal, so both describe the same candidate.
- `enumerate` is MolCrysKit's deterministic enumeration; `count` is a top-N
  cap and `seed` must be `None`. `random` is occupancy-weighted sampling with
  replacement and requires an explicit integer `seed` (0..2^32-1), which is
  echoed back. Random samples can repeat: repeats keep their own ID and
  position and set `duplicate_of` to the first matching `candidate_id`;
  `unique_count` counts distinct `kept_indices` sets.
- MolCrysKit may return fewer than requested (`actual_count < requested_count`);
  a `FewerCandidates` warning is added and nothing is padded.
- `independent_assemblies` defaults to `False` (MolCrysKit legacy semantics:
  separate assemblies are not assumed independent) and MolCrysKit's warning
  is returned in `warnings` (`source = "molcrys_kit"`). Pass `True` only when
  the source confirms independent assemblies. `coupled` is forwarded as is.
  Deprecation/Future warnings from libraries are not included.
- Invalid `method` (including `"optimal"`; use `operate_commit_major`),
  `count`, `seed`, flags, a `count` above `max_candidates` (hard cap 16), a
  source above 2 MiB, or a serialized result above `max_payload_bytes` raise
  `CandidateRequestError` (`ValueError`; `CandidatePayloadError` for payload
  size). Results are never truncated.

Android can preview any candidate's `snapshot`, export its `cif`, or zip all
`cif` strings; the ZIP is built in the Android layer from this payload.
Limitation: the payload carries full P1 CIF plus snapshot per candidate, so it
is roughly linear in count and structure size. The call is synchronous and
not thread-safe while MolCrysKit log records are captured.