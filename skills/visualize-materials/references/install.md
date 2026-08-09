# Install and Verify MatterVis

Read this when MatterVis must be installed or a caller supplies a Git revision.

## Immutable installation

Prefer an isolated environment in the task workspace:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install \
  "matter-vis @ git+https://github.com/SchrodingersCattt/MatterVis.git@<full-commit>"
```

Use that interpreter for every subsequent command. Do not install an unpinned
branch, substitute another same-named repository, modify dependency declarations,
or silently use an unrelated preinstalled copy.

## Verification

Run:

```bash
.venv/bin/mat-vis render --help
.venv/bin/python - <<'PY'
import importlib.metadata
import sys
import crystal_viewer
print("python=", sys.executable)
print("version=", importlib.metadata.version("matter-vis"))
print("module=", crystal_viewer.__file__)
PY
```

Record the requested Git revision, Python executable, installed distribution
version, module path, and successful CLI probe. Preserve installation errors;
do not claim that the requested revision is active without evidence.
