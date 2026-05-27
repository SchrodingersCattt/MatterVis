from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .schema import (
    BUILTIN_COLORS,
    BUILTIN_CUBE,
    BUILTIN_MCK_OVERRIDES,
    BUILTIN_STYLE,
    Config,
    ConfigSection,
)

try:  # pragma: no cover - stdlib path on Python >= 3.11
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


CONFIG_ENV_VAR = "MATTERVIS_CONFIG"


def default_config_path() -> Path:
    return Path.home() / ".config" / "mattervis" / "config.toml"


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    if not override:
        return merged
    for raw_key, value in override.items():
        key = str(raw_key)
        if key not in merged:
            # Unknown keys are intentionally ignored. This keeps old config
            # files harmless when the schema shrinks, and lets future config
            # files be read by older MatterVis versions.
            continue
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    if tomllib is None:
        # Last-resort parser for the subset we write in ``write_user_config``:
        # section headers, scalars, and simple arrays. It keeps Python 3.10
        # installs without tomli usable without adding a dependency.
        return _read_simple_toml(path.read_text(encoding="utf-8"))
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _parse_simple_toml_value(raw: str) -> Any:
    raw = raw.strip()
    if raw in {"true", "false"}:
        return raw == "true"
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_simple_toml_value(part.strip()) for part in inner.split(",")]
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(raw)
    try:
        if any(ch in raw for ch in ".eE"):
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _read_simple_toml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    section: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = [part.strip() for part in stripped[1:-1].split(".") if part.strip()]
            target = root
            for part in section:
                target = target.setdefault(part, {})
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        target = root
        for part in section:
            target = target.setdefault(part, {})
        target[key.strip()] = _parse_simple_toml_value(value)
    return root


def candidate_config_paths(path: str | os.PathLike[str] | None = None) -> list[Path]:
    if path is not None:
        return [Path(path).expanduser()]
    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        return [Path(env_path).expanduser()]
    return [default_config_path()]


def load_config(path: str | os.PathLike[str] | None = None, *, overrides: Mapping[str, Any] | None = None) -> Config:
    style = copy.deepcopy(BUILTIN_STYLE)
    colors = copy.deepcopy(BUILTIN_COLORS)
    cube = copy.deepcopy(BUILTIN_CUBE)
    mck = copy.deepcopy(BUILTIN_MCK_OVERRIDES)
    used_paths: list[str] = []

    for candidate in candidate_config_paths(path):
        raw = _read_config_file(candidate)
        if raw:
            used_paths.append(str(candidate))
        style = _deep_merge(style, raw.get("style") if isinstance(raw, Mapping) else None)
        colors = _deep_merge(colors, raw.get("colors") if isinstance(raw, Mapping) else None)
        cube = _deep_merge(cube, raw.get("cube") if isinstance(raw, Mapping) else None)
        mck = _deep_merge(mck, raw.get("mck_overrides") if isinstance(raw, Mapping) else None)

    if overrides:
        style = _deep_merge(style, overrides.get("style") if isinstance(overrides, Mapping) else None)
        colors = _deep_merge(colors, overrides.get("colors") if isinstance(overrides, Mapping) else None)
        cube = _deep_merge(cube, overrides.get("cube") if isinstance(overrides, Mapping) else None)
        mck = _deep_merge(mck, overrides.get("mck_overrides") if isinstance(overrides, Mapping) else None)

    return Config(
        style=ConfigSection(style),
        colors=ConfigSection(colors),
        cube=ConfigSection(cube),
        mck_overrides=ConfigSection(mck),
        source_paths=tuple(used_paths),
    )


def _toml_scalar(value: Any) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def _toml_lines(data: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[str]:
    lines: list[str] = []
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, Mapping[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, Mapping):
            table_items.append((str(key), value))
        else:
            scalar_items.append((str(key), value))
    if prefix:
        lines.append(f"[{'.'.join(prefix)}]")
    for key, value in scalar_items:
        if isinstance(value, (list, tuple)):
            rendered = ", ".join(_toml_scalar(item) for item in value)
            lines.append(f"{key} = [{rendered}]")
        else:
            lines.append(f"{key} = {_toml_scalar(value)}")
    if lines and table_items:
        lines.append("")
    for index, (key, value) in enumerate(table_items):
        lines.extend(_toml_lines(value, (*prefix, key)))
        if index != len(table_items) - 1:
            lines.append("")
    return lines


def write_user_config(payload: Mapping[str, Any], path: str | os.PathLike[str] | None = None) -> Path:
    target = Path(path).expanduser() if path is not None else default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(_toml_lines(payload)) + "\n", encoding="utf-8")
    return target


def delete_user_config(path: str | os.PathLike[str] | None = None) -> bool:
    target = Path(path).expanduser() if path is not None else default_config_path()
    if not target.exists():
        return False
    target.unlink()
    return True


__all__ = [name for name in globals() if not name.startswith("_")]
