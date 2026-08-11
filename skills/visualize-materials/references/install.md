# Install and Verify MatterVis

Read this when MatterVis must be installed or its active release must be verified.

## Released installation

```bash
pip install "matter-vis==0.0.0"
```

The release installs `molcrys-kit==0.6.1` or a compatible `>=0.6.1` build from
the package index. Environment placement is the executing agent's responsibility.
Do not substitute another same-named repository or silently use an unrelated
installation.

## Verification

Run:

```bash
mat-vis --help
mat-vis render --help
mat-vis tui --help
python - <<'PY'
import importlib.metadata
import sys
import crystal_viewer
print("python=", sys.executable)
print("distribution=", importlib.metadata.version("matter-vis"))
print("module_version=", crystal_viewer.__version__)
print("module=", crystal_viewer.__file__)
PY
```

Require the installed distribution version to equal `0.0.0`. Record, but do not
equate, the package's internal `crystal_viewer.__version__`; release 0.0.0 may
report a different internal module version. Also record the Python executable,
module path, console-script name, and live help output. The installed CLI is
authoritative: it currently exposes `render`, `serve`, and `tui`. Do not invent
commands or options absent from the probe.

## Static PNG runtime

Kaleido 1.x is installed by pip but Chrome/Chromium is an external runtime, not
a Python dependency. Before the first Plotly-backed PNG render, install Chrome
non-interactively with the helper shipped by Plotly:

```bash
plotly_get_chrome -y
python - <<'PY'
from crystal_viewer.cli import _plotly_static_export_available
available, reason = _plotly_static_export_available()
print("plotly_static_export=", available, reason)
raise SystemExit(0 if available else 1)
PY
```

Run this preflight before rendering, not after accepting a fallback PNG. Do not
try `kaleido[chromium]` (that extra does not exist), Playwright, or HTML as the
default workaround. If the helper fails because of transient network access,
retry that exact helper through the available network proxy. Do not start the
PNG render until the browser check passes.

If the caller explicitly requests a Git revision instead of the release, install
that immutable revision in a separate environment and record the resolved commit.
