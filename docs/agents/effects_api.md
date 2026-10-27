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
409. `request_id` is optional. Preview with `"preview": true`, then
`POST /api/v2/operate/commit`. Cancel with `/operate/cancel`.
Undo and redo are `/operate/undo` and `/operate/redo`.

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
