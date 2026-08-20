"""Exercise the /api/v2 scene-tab CRUD surface."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mat_viewer.app import create_app  # noqa: E402


def main() -> None:
    app = create_app(preset_path=str(REPO_ROOT / ".local" / "scene_tabs_demo.json"))
    client = app.server.test_client()

    initial = client.get("/api/v2/scenes").get_json()
    active_id = initial["active_id"]
    created = client.post("/api/v2/scenes", json={"structure": initial["scenes"][0]["structure_name"], "label": "API comparison"}).get_json()
    renamed = client.patch(f"/api/v2/scenes/{created['id']}", json={"label": "Renamed API scene"}).get_json()
    duplicate = client.post(f"/api/v2/scenes/{renamed['id']}/duplicate", json={"label": "Copy"}).get_json()
    order = [duplicate["id"], renamed["id"], active_id]
    client.post("/api/v2/scenes/reorder", json={"order": order})
    state = client.post(f"/api/v2/state?scene_id={renamed['id']}", json={"display_mode": "unit_cell"}).get_json()
    client.delete(f"/api/v2/scenes/{duplicate['id']}")

    print(json.dumps({"active_id": active_id, "renamed": renamed, "state": state}, indent=2))


if __name__ == "__main__":
    main()
