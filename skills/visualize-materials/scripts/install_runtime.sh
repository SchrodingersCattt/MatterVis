#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install_runtime.sh [options]

Options:
  --python PATH          Python environment to install into (default: python3)
  --venv ABSOLUTE_PATH  Optionally create and install into this venv
  --with-system-libs     Install common Chrome libraries with apt-get
EOF
}

venv=""
python_cmd="python3"
with_system_libs=0

while (($#)); do
  case "$1" in
    --venv) venv="${2:-}"; shift 2 ;;
    --python) python_cmd="${2:-}"; shift 2 ;;
    --with-system-libs) with_system_libs=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "$venv" && "$venv" != /* ]]; then
  echo "--venv must be an absolute path" >&2
  exit 2
fi

if [[ -n "$venv" ]]; then
  "$python_cmd" -m venv "$venv"
  runtime_python="$venv/bin/python"
else
  if ! runtime_python="$(command -v "$python_cmd")"; then
    echo "Python executable not found: $python_cmd" >&2
    exit 2
  fi
fi
"$runtime_python" -m pip install "matter-vis==0.0.3"
scripts_dir="$("$runtime_python" -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
mat_vis="$scripts_dir/mat-vis"
chrome_installer="$scripts_dir/plotly_get_chrome"

if ((with_system_libs)); then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "--with-system-libs requires apt-get" >&2
    exit 2
  fi
  apt-get update -qq
  packages=(
    libnspr4 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2
    libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2
  )
  if apt-cache show libasound2t64 >/dev/null 2>&1; then
    packages+=(libasound2t64)
  else
    packages+=(libasound2)
  fi
  apt-get install -y -qq "${packages[@]}"
fi

"$chrome_installer" -y
(
  cd /
  "$mat_vis" --help >/dev/null
  "$mat_vis" render --help >/dev/null
  "$runtime_python" - <<'PY'
import importlib.metadata
import crystal_viewer
import sys
from pathlib import Path

version = importlib.metadata.version("matter-vis")
if version != "0.0.3":
    raise SystemExit(f"expected matter-vis 0.0.3, got {version}")
module_path = Path(crystal_viewer.__file__).resolve()
print("python=", sys.executable)
print("distribution=", version)
print("module=", module_path)
PY
)

echo "mattervis_python=$runtime_python"
if [[ -n "$venv" ]]; then
  echo "mattervis_venv=$venv"
fi
echo "mat_vis=$mat_vis"
