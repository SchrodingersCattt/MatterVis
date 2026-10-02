"""Small color-space adapters shared by renderer boundaries."""

from __future__ import annotations

from functools import lru_cache


def _palette() -> tuple[tuple[int, int, int], ...]:
    colors: list[tuple[int, int, int]] = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ]

    def level(value: int) -> int:
        return 0 if value == 0 else 55 + 40 * value

    for red in range(6):
        for green in range(6):
            for blue in range(6):
                colors.append((level(red), level(green), level(blue)))
    for value in range(24):
        level_value = 8 + 10 * value
        colors.append((level_value, level_value, level_value))
    return tuple(colors)


_ANSI_PALETTE = _palette()


def _parse_hex(value: str) -> tuple[int, int, int]:
    text = str(value).strip().lstrip("#")
    if len(text) != 6:
        return (128, 128, 128)
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (128, 128, 128)


@lru_cache(maxsize=512)
def ansi256_from_hex(value: str) -> int:
    """Map an RGB hex color to the nearest xterm-256 color deterministically."""
    rgb = _parse_hex(value)
    distances = [
        (candidate[0] - rgb[0]) ** 2
        + (candidate[1] - rgb[1]) ** 2
        + (candidate[2] - rgb[2]) ** 2
        for candidate in _ANSI_PALETTE
    ]
    return int(min(range(len(_ANSI_PALETTE)), key=lambda index: distances[index]))


def element_ansi_color(symbol: str, *, default: int = 252) -> int:
    """Resolve one element through MatterVis's canonical palette."""
    from .config import element_color

    value = element_color(symbol)
    if not value:
        return int(default)
    return ansi256_from_hex(value)


__all__ = ["ansi256_from_hex", "element_ansi_color"]
