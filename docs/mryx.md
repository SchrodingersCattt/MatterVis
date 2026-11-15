# Mercury MRYX input

MatterVis imports `.mryx` using **MolCrysKit `read_mryx_atoms`**. The viewer has
no Mercury XML parser, element-code table or chemistry decoder of its own. The
required upstream revision is pinned in `pyproject.toml` and
`requirements/ci-constraints.txt`; install that dependency rather than an older
MolCrysKit release without this API. No CCDC installation is needed.

```bash
mat-vis render document.mryx --frame 0 -o stored-sites.png --backend cpu
mat-vis tui document.mryx --frame 1 --no-interaction
```

The unified Python APIs `load_atomistic_input`, `iter_atomistic_frames`,
`count_structure_frames`, and `load_structure_input` recognize `.mryx` (or
`input_format="mryx"` / `"mercury"`). Each stored structure is a frame in document
order. The load APIs accept negative indices. `iter_atomistic_frames` still
requires pre-resolved non-negative indices, but MRYX is not a bounded-memory
stream: the adapter calls `read_mryx_atoms(index=":")`, so every stored
structure and its provenance are parsed before the first frame is yielded.
Selecting one frame does not make that read lazy. Stored structures are
document indices, not a physical time trajectory.

Browser upload and `build_loaded_crystal` show the first stored structure and
do not start a playback clock. CLI and TUI selection uses `--frame`, which is
that document index. Animation export does not draw a simulation-time label
from the index. `--display-time` is accepted only when every selected frame
already carries `time_fs`, `time_ps`, or `time_ns`. A missing step field is not
turned into femtoseconds. To print the index, pass
`--frame-field index --frame-label 'structure {index}'`.

`build_loaded_crystal(cif_path="document.mryx", ...)` and browser/Dash/native
uploads load the **first stored structure only**. Native upload retains the
`.mryx` suffix and existing filename sanitization / asynchronous job handling.
Other structures are accessible through frame-aware APIs/CLI; uploading a
multi-structure document does not create a tab for every structure.

## Import scope

This is read-only **stored-site geometry** import, not Mercury session
restoration. Upstream warnings are emitted and retained in
`bundle.frame_info["mryx"]["diagnostics"]`; source metadata also appears in
`bundle.scene["source_info"]["mryx"]`. Single-bundle uploads additionally expose
`input_diagnostics` and `source_structure_count` on the scene, including the
scene served after display rebuild. The browser upload status repeats the
first diagnostic under the loaded name. Atom labels and
arrays pass through the shared ASE adapter.

Important upstream limitations apply unchanged: no automatic symmetry expansion
(the stored sites may be only an asymmetric unit), no verified occupancy or
bond-order decoding, no interpretation of raw disorder/ADP fields, and no saved
camera/style reconstruction. Display unit weights are placeholders, not measured
occupancies. Do not infer full-cell composition from this import. Geometric bond
perception uses the existing MolCrysKit bridge, not Mercury enumerator decoding.
For chemistry/format semantics see MolCrysKit's `docs/mryx.md`.

Unsafe/malformed input is rejected upstream with `ValueError`, subject to its
compressed/expanded size and XML tree limits. Missing upstream reader support
produces an actionable dependency error, not a second local parsing fallback.
