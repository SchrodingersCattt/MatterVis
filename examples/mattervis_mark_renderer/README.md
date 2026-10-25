# mattervis-mark-renderer

Example terminal renderer for MatterVis. It is a separate distribution: install
it beside MatterVis and select it by id. The package is not part of
`mat_viewer` and does not replace `mattervis.native-v1`.

The picture is ASCII only. Each visible site is one character: `1` (front),
`2` (middle), or `3` (back) when depth marks are enabled, otherwise the
configured marker (`#` by default). Enabled bonds leave a `.` in an empty
midpoint cell. Element symbols, Braille, and the native axes footer are not
drawn. Host-projected coordinates are reused; this package does not own the
camera.

## Install

From a checkout that already has MatterVis installed:

```bash
python -m pip install --no-deps examples/mattervis_mark_renderer
```

`--no-deps` avoids resolving MatterVis again. The package version is `0.1.0`.

## Select it

Static frame:

```bash
mat-vis tui structure.cif --no-interaction --charset ascii7 \
  --width 80 --height 24 \
  --renderer example.marks-v1 \
  --renderer-config examples/mattervis_mark_renderer/pins/example.marks-v1.json
```

Stateful JSONL session (same flags):

```bash
printf '%s\n' '{"action":"observe"}' '{"action":"orbit","arguments":{"yaw_deg":20}}' '{"action":"close"}' | \
  mat-vis tui structure.cif --session-format jsonl --charset ascii7 \
    --width 80 --height 24 \
    --renderer example.marks-v1 \
    --renderer-config examples/mattervis_mark_renderer/pins/example.marks-v1.json
```

Python, without a shell patch:

```python
from mat_viewer.tui import TerminalSession

session = TerminalSession.from_file(
    "structure.cif",
    width=80,
    height=24,
    charset="ascii7",
    renderer="example.marks-v1",
    renderer_config="examples/mattervis_mark_renderer/pins/example.marks-v1.json",
)
print(session.observe().as_dict()["frame"]["text"])
print(session.replay_manifest())
```

An isolated directory of wheels can be passed as `renderer_search_path=[path]`.
The package must also be importable. Renderer choice is fixed for the episode.
It is not a `set_display` action.

## Pin the implementation

`require_package_version` is in the sample config. To pin the source digest
too, read it from the installed backend and set
`require_implementation_digest` to that sha256 hex string:

```bash
python -c "from mattervis_mark_renderer import create_renderer; print(create_renderer().implementation_digest)"
```

A missing id, a wrong API version, an unknown option, a pin mismatch, or a
malformed frame fails the session. MatterVis does not draw the native picture
instead.

Python entry points run with host privileges. This package is a trusted local
plugin, not a sandbox. An untrusted renderer must run in a separate process
that only receives the public render request.
