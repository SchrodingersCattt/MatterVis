# Install and Verify MatterVis

Read this when MatterVis must be installed or its active release must be verified.

## Released installation

Prefer an isolated environment in the task workspace:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install "matter-vis==0.0.0"
```

The release installs `molcrys-kit==0.6.1` or a compatible `>=0.6.1` build from
the package index. Use the same interpreter for all later commands. Do not
substitute another same-named repository or silently use an unrelated Python.

## Verification

Run:

```bash
.venv/bin/mat-vis --help
.venv/bin/mat-vis render --help
.venv/bin/mat-vis tui --help
.venv/bin/python - <<'PY'
import importlib.metadata
import sys
import crystal_viewer
print("python=", sys.executable)
print("distribution=", importlib.metadata.version("matter-vis"))
print("module_version=", crystal_viewer.__version__)
print("module=", crystal_viewer.__file__)
PY
```

Require distribution and module versions to equal `0.0.0`. Record the Python
executable, module path, console-script name, and live help output. The installed
CLI is authoritative: it currently exposes `render`, `serve`, and `tui`. Do not
invent commands or options absent from the probe.

If the caller explicitly requests a Git revision instead of the release, install
that immutable revision in a separate environment and record the resolved commit.
