# Terminal renderer backends

MatterVis owns the structure, the camera, projection, visibility, selection,
session lifecycle, and JSONL action transport. A renderer owns only the
picture: glyphs, depth notation, labels, occlusion, and layout.

This is a Display-side extension. It does not add a second camera, a second
action reducer, or an Operate/Analysis tool. Selecting a renderer is episode
configuration. It is not an Agent `set_display` action, and it does not change
orbit, reset, viewport, or saved-view meanings.

The built-in id is `mattervis.native-v1`. With no renderer selected, static
`mat-vis tui` output and default JSONL sessions stay on that adapter.
`state_fingerprint` does not include renderer identity. Default observation
objects do not grow a `renderer` field. Picture bytes are the cross-interpreter
byte-identity contract for the default native backend. The fingerprint hashes
viewport floats as produced by that process. Those floats can differ by one
unit in the last place across CPython builds, so a full JSONL trace is not
required to be byte-identical across interpreters. Within one process the
fingerprint stays stable: reset restores it, and a camera change replaces it.

## Request and result

```python
from mat_viewer.tui import (
    NATIVE_BACKEND_ID,
    RENDER_API_VERSION,
    TerminalSession,
    render_picture,
    resolve_renderer_binding,
)

binding = resolve_renderer_binding("example.marks-v1")
session = TerminalSession.from_file(
    "structure.cif",
    width=100,
    height=36,
    charset="ascii7",
    renderer="example.marks-v1",
    renderer_config="renderer.json",
)
manifest = session.replay_manifest()
```

`render_picture(...)` is the single dispatch used by
`compose_static_frame` and `DepthControls.render_frame`. `compose_frame`
remains the native implementation. The native adapter calls it with the host
crystal and camera, so the default picture is the compositor's picture.

Public schemas:

| Name | Value |
| --- | --- |
| API | `mattervis.tui.render/v1` |
| Request | `mattervis.tui.render_request/v1` |
| Display IR | `mattervis.tui.display_ir/v1` |
| Frame | `mattervis.tui.rendered_frame/v1` |
| Config | `mattervis.tui.renderer_config/v1` |
| Replay manifest | `mattervis.tui.renderer_replay/v1` |
| Entry point group | `mattervis.tui.renderers` |

`RenderRequest` carries display sites, manifested bonds, cell vectors, the
host camera basis, and the host `project_points` arrays. Cartesian positions
and the cell matrix are Angstrom. Basis rows are camera right, up, and
forward. Projected `xy` is the camera-plane coordinate returned by
`project_points` (orthographic values are divided by camera distance). Depth
is the camera-forward component; larger is closer. A null viewport means the
native compositor still computes its own fit. Active sessions pass the
controller's fitted viewport.

Bond provenance is graph-level (`provided`, `inferred`, or `unspecified`).
`explicit_bond_table: true` is provided input. Loader sources
`distance_heuristic`, `molcryskit`, and `canonical_scene` are inferred.
Any other metadata string is `unspecified` and is not copied into the
request. `bond_policy` is `manifested` (default, current drawing),
`provided_only` (inferred edges disabled, source graph not repaired), or
`bonds_off`.

The request has no field for `CrystalIR`, MolCrysKit chemistry, CIP or IUPAC
names, gold answers, scorers, or benchmark labels. Building it does not call
MolCrysKit.

`RenderedFrame.text` for the native backend is the compositor string.
External frames must be at most `width` by `height`, without a trailing
newline. `ascii7` is printable 7-bit ASCII (0x20–0x7E) with no ANSI escapes.
The static CLI status line can still contain a degree sign; JSONL screens do
not include that line.
The Unicode profile allows the terminal-safe characters the native viewer
already uses, including Braille and superscripts, and still rejects controls
and ANSI. The host recomputes `content_sha256` over those exact bytes
(`ascii` for ascii7, `utf-8` for Unicode). Session `frame.sha256` remains the
hash of the padded screen and is unchanged for default sessions.

## Registration

A backend is a zero-argument entry point in group `mattervis.tui.renderers`.
The entry point name is the backend id. The factory returns an object with:

- `backend_id`, `api_version` (`mattervis.tui.render/v1`)
- `deterministic` (`True`; other values are rejected)
- `package_version`, `implementation_digest` (lowercase sha256 hex)
- `validate_options(options) -> mapping`
- `render(request) -> str | BackendFrame`

`RendererBackend` is the `Protocol` for that object. `render` does not return
`RenderedFrame`; the host builds that record so a plugin cannot forge the
content hash or the backend digest.

`mattervis.native-v1` is reserved. An entry point cannot replace it.
Options are schema-checked by the backend. Unknown config fields, a missing
id, an API mismatch, a package or digest pin mismatch, a malformed frame, and
a plugin exception fail closed. Dispatch does not draw the native picture
instead.

CLI, for both `--no-interaction` and `--session-format jsonl`:

```bash
mat-vis tui structure.cif --no-interaction --charset ascii7 \
  --width 80 --height 24 \
  --renderer example.marks-v1 \
  --renderer-config examples/mattervis_mark_renderer/pins/example.marks-v1.json
```

`--observation-profile model` is JSONL-only. `legacy` (the default) is the
historical observation. `model` keeps the frame, camera, display toggles,
selection identities, and `state_fingerprint`, and drops the source title,
chemistry warnings, and formula/composition scope fields. The title becomes
`display`.

An installed example lives in `examples/mattervis_mark_renderer`. It is its
own wheel. See that directory's README for install and pin commands.

`renderer_search_path` on `TerminalSession` / `resolve_renderer_binding`
discovers entry points in an extra directory of installed distributions. It
is an operator argument, not a JSONL action, and it is not read from CIF
metadata. The package still has to be importable. Do not pass
`module:function` strings from a model.

## Replay identity

`session.replay_manifest()` records backend id, API, package version,
implementation digest, config hash, options, bond policy, charset, and
viewport size. It is not part of `state_fingerprint` and is omitted from
default observations. Non-default episodes also attach that manifest, plus
the frame's `input_digest`, `view_digest`, and unpadded `content_sha256`,
to the observation under `renderer`.

Same display IR, view, config, and implementation digest produce the same
frame bytes for a deterministic backend. Nondeterministic backends are
rejected. Renderer choice is an experiment factor. It does not change task
identity, action names, the scoring oracle, or the allowed tool set. MatterVis
does not own those.

## Trust boundary

Loading a Python entry point executes code with host privileges. A `Protocol`
is not a sandbox. A trusted local plugin may be an installed wheel. An
untrusted contestant renderer must run in a separate process or container
with isolated mounts and network, receiving only the public request JSON.
This version does not ship a second subprocess renderer.

The native adapter is allowed to read the host crystal so it can keep calling
`compose_frame`. External `render` methods are not given that object. The
native molecule overview can still draw manifested species labels, because
that compositor reads `CrystalIR`. Experiments that must not expose those
labels should use atom level or an external renderer whose request has been
audited.

Diagnostics on `BackendFrame` are a host audit artifact. They are not written
into the agent observation.

## What this does not validate

Satisfying the interface does not show that a renderer cannot infer chemistry
from coordinates. It does not validate a benchmark, a scorer, a task split, or
a human reading of either picture. It does not implement research harness
issue 17, and it does not close MatterVis issue 163. Regression coverage here
is byte identity of the default compositor, fail-closed selection, charset
bounds, camera and action stability across backends, and canaries for hidden
chemistry fields.
