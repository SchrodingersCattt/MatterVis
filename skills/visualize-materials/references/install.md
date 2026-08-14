# Install and Repair MatterVis

Use this only for installation, version verification, or Chrome/Kaleido repair.

## Installation

```bash
bash scripts/install_runtime.sh
```

By default this installs into the current Python environment. A venv is
optional, not a prerequisite:

```bash
bash scripts/install_runtime.sh --venv /absolute/path/to/mattervis-venv
```

Installing may update an existing `molcrys-kit`. Use a venv only when that
dependency change is undesirable or isolation was requested.

The script installs `matter-vis==0.0.2`, installs Chrome with
`plotly_get_chrome -y`, and verifies the `mat-vis` console entry point.

The distribution version is authoritative. Record the Python executable,
distribution version, module path, and `mat-vis render --help`; do not treat an
internal module-version string as the release identity.

## Chrome shared libraries

`Chrome installed successfully` means the executable was downloaded, not that it
can start. If a real static render reports `BrowserFailedError`, inspect it:

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


Do not install nonexistent `kaleido[chromium]` extras or silently switch to
Playwright/HTML. HTML is a deliverable only when explicitly requested.
