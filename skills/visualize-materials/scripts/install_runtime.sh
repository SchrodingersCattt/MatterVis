#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install_runtime.sh --venv ABSOLUTE_PATH [options]

Options:
  --python PATH          Python used to create the venv (default: python3)
  --with-system-libs     Install common Chrome libraries with apt-get
  --probe-width N        Static probe width (default: 2400)
  --probe-height N       Static probe height (default: 1800)
  --probe-scale N        Static probe scale (default: 1)
EOF
}

venv=""
python_cmd="python3"
with_system_libs=0
probe_width=2400
probe_height=1800
probe_scale=1

while (($#)); do
  case "$1" in
    --venv) venv="${2:-}"; shift 2 ;;
    --python) python_cmd="${2:-}"; shift 2 ;;
    --with-system-libs) with_system_libs=1; shift ;;
    --probe-width) probe_width="${2:-}"; shift 2 ;;
    --probe-height) probe_height="${2:-}"; shift 2 ;;
    --probe-scale) probe_scale="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$venv" || "$venv" != /* ]]; then
  echo "--venv must be an absolute path" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$python_cmd" -m venv "$venv"
"$venv/bin/python" -m pip install "matter-vis==0.0.1"

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

"$venv/bin/plotly_get_chrome" -y
(
cd /
"$venv/bin/mat-vis" --help >/dev/null
"$venv/bin/mat-vis" render --help >/dev/null
"$venv/bin/python" - <<'PY'
import importlib.metadata
import crystal_viewer
import sys
from pathlib import Path

version = importlib.metadata.version("matter-vis")
if version != "0.0.1":
    raise SystemExit(f"expected matter-vis 0.0.1, got {version}")
module_path = Path(crystal_viewer.__file__).resolve()
venv_path = Path(sys.prefix).resolve()
if venv_path not in module_path.parents:
    raise SystemExit(f"crystal_viewer is shadowed by source outside the venv: {module_path}")
print("python=", sys.executable)
print("distribution=", version)
print("module=", module_path)
PY
"$venv/bin/python" "$script_dir/check_static_export.py" \
  --width "$probe_width" --height "$probe_height" --scale "$probe_scale"
)

echo "mattervis_venv=$venv"
echo "mat_vis=$venv/bin/mat-vis"
