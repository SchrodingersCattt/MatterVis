"""Launch the Dash app in the background and drive it via the REST API.

Run from the repository root:

    python scripts/05_app_and_api.py

The script:

1. Starts ``crystal_viewer.create_app`` with the bundled DAP-4 CIF preloaded.
2. Hits ``GET  /api/v2/state``           - the live viewer state.
3. Hits ``POST /api/v2/topology``        - coordination analysis for fragment 0.
4. Hits ``POST /api/v2/camera/action``   - rotates the camera.
5. Hits ``GET  /api/v2/screenshot``      - captures the resulting view as PNG.

The app keeps running for ~3 seconds after the screenshot so you can
``open http://127.0.0.1:8051`` (or whatever port was free) to interact with
the live viewer; afterwards it shuts itself down cleanly.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib import request as urlrequest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from crystal_viewer.app import create_app  # noqa: E402
from crystal_viewer.loader import build_loaded_crystal  # noqa: E402


HERE = Path(__file__).resolve().parent
CIF = HERE / "data" / "DAP-4.cif"
OUTPUT_DIR = HERE / "_outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _api(method: str, base: str, path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        f"{base}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urlrequest.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_screenshot(base: str, dest: Path) -> Path:
    with urlrequest.urlopen(f"{base}/api/v2/screenshot", timeout=30) as resp:
        dest.write_bytes(resp.read())
    return dest


def main() -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    preset_dir = Path(tempfile.mkdtemp(prefix="cv_example_"))
    preset_path = preset_dir / "crystal_view_preset.json"
    app = create_app(preset_path=str(preset_path))
    backend = app.crystal_backend
    bundle = build_loaded_crystal(name="DAP-4", cif_path=str(CIF), title="DAP-4")
    backend.bundles[bundle.name] = bundle
    if bundle.name not in backend.structure_names:
        backend.structure_names.append(bundle.name)

    backend.patch_state({
        "structure": "DAP-4",
        "display_mode": "unit_cell",
        "display_options": ["unit_cell_box"],
        "topology_fragment_type": "A",
    })

    server_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    server_thread.start()

    for _ in range(50):
        try:
            urlrequest.urlopen(f"{base}/api/v2/state", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        raise SystemExit("Backend did not come up in time.")

    print(f"App live at {base}")

    state = _api("GET", base, "/api/v2/state")
    print(f"Initial structure: {state['structure']}  display_options={state['display_options']}")

    a_indices = [f["index"] for f in bundle.topology_fragment_table if f["type"] == "A"]
    if not a_indices:
        raise SystemExit("No A-site fragments found.")
    # The backend honours `center_index` only when it matches the current
    # `topology_fragment_type`; we set that to "A" above via patch_state.
    topology = _api("POST", base, "/api/v2/topology", {
        "structure": "DAP-4",
        "center_index": a_indices[0],
        "cutoff": 8.0,
    })
    shape = topology.get("shape") or {}
    label = shape.get("primary_label") or "n/a"
    modifier = shape.get("label_modifier") or ""
    label_text = f"{modifier} {label}".strip() if modifier else label
    print(f"Topology: {topology['center_label']} (type={topology['center_type']})  "
          f"CN={topology['coordination_number']}  "
          f"shape={label_text}")

    _api("POST", base, "/api/v2/camera/action", {
        "action": "orbit",
        "yaw_deg": 25,
        "pitch_deg": -8,
    })

    shot = _api_screenshot(base, OUTPUT_DIR / "05_api_screenshot.png")
    print(f"Wrote screenshot: {shot}  ({os.path.getsize(shot)} bytes)")

    summary = OUTPUT_DIR / "05_api_summary.json"
    summary.write_text(json.dumps({
        "url": base,
        "structure": "DAP-4",
        "topology": {
            "center_index": topology["center_index"],
            "coordination_number": topology["coordination_number"],
            "shell_distances_A": topology["distances"],
            "shape": {
                "primary_label": shape.get("primary_label"),
                "label_modifier": shape.get("label_modifier"),
                "cshm_value": shape.get("cshm_value"),
            },
        },
    }, indent=2))
    print(f"Wrote summary: {summary}")
    print("App will keep serving for 3 more seconds...")
    time.sleep(3)


if __name__ == "__main__":
    main()
