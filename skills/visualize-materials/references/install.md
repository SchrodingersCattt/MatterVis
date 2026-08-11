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

If the caller explicitly requests a Git revision instead of the release, install
that immutable revision in a separate environment and record the resolved commit.
