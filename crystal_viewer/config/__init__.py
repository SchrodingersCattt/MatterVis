from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from typing import Any

from .loader import delete_user_config, load_config, write_user_config
from .schema import Config

_CURRENT_CONFIG: Config = load_config()


class ConfigProxy:
    """Read-only facade over the currently loaded global config."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_CURRENT_CONFIG, name)

    def as_dict(self) -> dict[str, Any]:
        return _CURRENT_CONFIG.as_dict()


class ConfigMapping(Mapping[str, Any]):
    """Live mapping view for legacy module constants such as DEFAULT_STYLE."""

    def __init__(self, section: str):
        self.section = section

    @property
    def _values(self) -> Mapping[str, Any]:
        return getattr(_CURRENT_CONFIG, self.section).values

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def __deepcopy__(self, memo: dict) -> dict[str, Any]:
        return copy.deepcopy(getattr(_CURRENT_CONFIG, self.section).as_dict(), memo)

    def copy(self) -> dict[str, Any]:
        return getattr(_CURRENT_CONFIG, self.section).as_dict()


CONFIG = ConfigProxy()
DEFAULT_STYLE = ConfigMapping("style")
CUBE_CONFIG = ConfigMapping("cube")
COLOR_CONFIG = ConfigMapping("colors")
MCK_OVERRIDES = ConfigMapping("mck_overrides")


def reload_config(path: str | None = None, *, overrides: Mapping[str, Any] | None = None) -> Config:
    global _CURRENT_CONFIG
    _CURRENT_CONFIG = load_config(path, overrides=overrides)
    return _CURRENT_CONFIG


def current_config() -> Config:
    return _CURRENT_CONFIG


def element_color(symbol: str, *, light: bool = False) -> str:
    palette = _CURRENT_CONFIG.colors.get("elements_light" if light else "elements", {})
    return str(palette.get(symbol, palette.get("default", "#808080")))


def atom_radius(symbol: str) -> float:
    radii = _CURRENT_CONFIG.colors.get("atom_radius", {})
    return float(radii.get(symbol, radii.get("default", 0.18)))


def covalent_radius(symbol: str) -> float:
    radii = _CURRENT_CONFIG.colors.get("covalent_radius", {})
    return float(radii.get(symbol, 0.80))


__all__ = [
    "COLOR_CONFIG",
    "CONFIG",
    "CUBE_CONFIG",
    "ConfigMapping",
    "DEFAULT_STYLE",
    "MCK_OVERRIDES",
    "atom_radius",
    "covalent_radius",
    "current_config",
    "delete_user_config",
    "element_color",
    "load_config",
    "reload_config",
    "write_user_config",
]
