# Scientific effects API

MatterVis separates three effects:

| Effect | Contract | Writes |
|---|---|---|
| Display | `(structure, view, request) -> (structure, view')` | view state only |
| Analysis | `(structure, query) -> AnalysisResult` | analysis results only |
| Operate | `(structure, command) -> candidate -> commit -> structure'` | a new structure revision |

The legacy `supercell` field and `/api/v2/transforms` `repeat` draw periodic
copies. `build_supercell` is the structural command. Do not send
`operate supercell`.

## Python

```python
from mat_viewer.effects import EffectSession

session = EffectSession()
ref = session.register(crystal, structure_id="urea")
result = session.operate(
    "build_supercell",
    {"a": 2, "b": 2, "c": 1},
    structure_id=ref.id,
    expected_revision=ref.revision,
    request_id="req-1",
)
snapshot = session.snapshot(result.output_ref)
```

`expected_revision` must match the current revision. A candidate built
against an older revision is rejected and does not change history.
Revision numbers are monotonic for a structure frame. Undo does not
make the next commit reuse a retired number, so two scientific
structures never share one `StructureRef`.

Repeating `request_id` with the same canonical command, parameters, and
property policy returns the recorded commit and leaves the current
revision where it is. The same id with a different payload is rejected.
A retry does not rewind the pointer to the older output.

Session files keep crystal metadata, formula moiety, and the
`image_shift` site array. A field the file cannot represent raises
instead of being replaced.

The same session implements `build_slab`, `insert_molecule`,
`delete_sites`, `replace_species`, `add_hydrogens`, `remove_solvent`,
`materialize_selection`, and `commit_disorder_candidate`.
`translate_atom` and `translate_molecule` take source site IDs in `sites`
and a Cartesian `vector` in Å:

```json
{"sites": ["site-1"], "vector": [0.25, 0.0, 0.0]}
```

`rotate_molecule` takes source site IDs, a Cartesian `axis` with three
components, `angle` in degrees, and an optional `center` of `com` or
`centroid`:

```json
{"sites": ["site-1"], "axis": [0, 0, 1], "angle": 90, "center": "com"}
```

`rotate_fragment` does not take `axis`. The directed bond is the two
ordered source sites, from the first site toward the second. `angle` is in
degrees and `moving_side` is `j` (default) or `i`:

```json
{"sites": ["site-2", "site-3"], "angle": 60, "moving_side": "j"}
```

These geometric edits preserve the input bond graph and do not relax the
structure. On the moved atoms, per-atom `u_cart` and a `transform` policy
for `adp` become `R U R^T`. A `transform` policy for `forces` or
`velocities` rotates those Cartesian vectors. `copy` keeps the stored
numbers. The fixed side of a fragment rotation is not rotated. Translations
do not rotate vectors or tensors.

`reperceive_bonds` builds a revision-local candidate using `bond_scale` and an
optional list of `{ "elements": ["C", "O"], "distance_A": 1.7 }` thresholds.
It does not patch global configuration. Source site IDs are preserved.
Image shifts are rebased onto the unwrapped coordinates, and stored
`frac_x`, `frac_y`, and `frac_z` arrays are dropped because they describe
the previous unwrapping. Review the candidate bond graph and molecule
grouping, then commit the candidate explicitly before subsequent structural
operations. `session.candidate_snapshot(candidate_id)` returns that
read-only site, bond, molecule-index, and image-shift snapshot without
committing.

`delete_periodic_copy` runs supercell construction and one site deletion
as a single commit.

Site properties must be named in `property_policy`. Omitting a present
field rejects the operation. `drop` requires a reason.

Analysis queries are `composition`, `coordination`, and `distance`.
Distance requests must set `distance_mode` to `picked_image` or
`minimum_image`. Camera, color, and visibility keys are ignored.

## CLI

```bash
mat-vis operate build-supercell structure.cif --a 2 --b 3 --c 1 -o supercell.cif --json
```

## REST

`POST /api/v2/operate` requires `command`, `structure_id`, `scene_id`,
and `expected_revision`. It does not inherit the active tab or the
bundle's current revision. A missing target is HTTP 400. A structure
that does not match the scene, or a stale `expected_revision`, is HTTP
409. `request_id` is optional. Preview with `"preview": true`. The preview
body identifies the candidate and includes `candidate_snapshot_path`. It
does not include coordinates or bonds. Read those from
`GET /api/v2/operate/candidates/<candidate_id>` before
`POST /api/v2/operate/commit`. That route returns the candidate sites,
bonds, molecule indices, and image shifts and does not create a revision.
`ref` inside the snapshot is the input revision the candidate was built
from. A missing candidate is HTTP 404. A cancelled candidate is HTTP 400.
Cancel with `/operate/cancel`. Undo and redo are `/operate/undo` and
`/operate/redo`.

`GET /api/v2/operate/capabilities` lists the implemented commands.

A WebSocket intent `{"type": "operate", "payload": {...}}` calls the
same adapter. The view reducer does not treat that intent as a state patch.

## Identity

`StructureRef` is `(id, revision, frame_ref)`. A display image is not a
source site. Deleting one periodic copy requires `delete_periodic_copy`
or an explicit `build_supercell` followed by `delete_sites` on the
mapped output site. `materialize_selection` freezes explicit
site/image pairs into a `PBC=False` cluster and rejects provenance-free
ghosts. A periodic request must supply `cell` and `equivalences`; the
committed crystal then uses that cell and those PBC flags. A periodic
request without them is rejected and does not return a finite cluster.
