# Install and Repair MatterVis

Use this only for installation, version verification, or Chrome/Kaleido repair.

## Isolated installation

Never install MatterVis into a shared scientific environment. Its dependency
resolver may replace the environment's existing `molcrys-kit`.

```bash
bash scripts/install_runtime.sh --venv /absolute/path/to/mattervis-venv
```

The script installs `matter-vis==0.0.2`, installs Chrome with
`plotly_get_chrome -y`, verifies the `mat-vis` console entry point, and runs a
nonblank 3D export at a production-like canvas. Use its venv's `mat-vis`.

The distribution version is authoritative. Record the Python executable,
distribution version, module path, and `mat-vis render --help`; do not treat an
internal module-version string as the release identity.

## Chrome shared libraries

`Chrome installed successfully` means the executable was downloaded, not that it
can start. If the probe reports `BrowserFailedError`, inspect the exact browser:

```bash
ldd /path/to/chrome | grep 'not found'
```

On Ubuntu/Debian the common runtime set is:

```bash
apt-get update -qq
apt-get install -y -qq \
  libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2
```

Install `libasound2t64` on distributions that provide it, otherwise
`libasound2`. The observed hard blockers include `libnspr4`, `libnss3`,
`libnssutil3`, `libsmime3`, and `libgbm1`; the first four are supplied by the
NSS packages above.

Pass `--with-system-libs` to the installer only when apt changes are authorized.
Otherwise preserve the missing-library output and report the blocker.

## Decisive probe

Always probe at the intended final width, height, and scale:

```bash
python scripts/check_static_export.py \
  --width WIDTH --height HEIGHT --scale SCALE
```

A browser-path check or tiny scatter image cannot admit a production render.
The probe must start Chrome, export a 3D mesh, decode the PNG, and find
foreground pixels. Rerun it after installing libraries.

Do not install nonexistent `kaleido[chromium]` extras or silently switch to
Playwright/HTML. HTML is a deliverable only when explicitly requested.
