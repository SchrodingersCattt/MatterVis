"""Every registered route stays classified. An unclassified route fails."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "tests" / "fixtures" / "effect_contracts" / "legacy_surface.json"


def _routes() -> set[str]:
    found = set()
    for path in (ROOT / "mat_viewer" / "api").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                owner = decorator.func.value
                if not isinstance(owner, ast.Name) or owner.id not in {"v1", "v2"}:
                    continue
                if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                    continue
                raw = str(decorator.args[0].value).lstrip("/")
                methods = ["GET", "POST"] if decorator.func.attr == "route" else [decorator.func.attr.upper()]
                for method in methods:
                    found.add(f"{method} /api/{owner.id}/{raw}")
    return found


def test_inventory_covers_every_route_and_keeps_supercell_display_only():
    payload = json.loads(INVENTORY.read_text(encoding="utf-8"))
    classified = {entry["id"] for entry in payload["entries"]}
    missing = sorted(_routes() - classified)
    assert not missing, missing
    supercell = next(entry for entry in payload["entries"] if entry["id"] == "state.supercell")
    assert supercell["effect"] == "display"
    assert supercell["writes_structure"] is False
    assert "cli:operate supercell" not in classified
    assert "cli:operate build-supercell" in classified
