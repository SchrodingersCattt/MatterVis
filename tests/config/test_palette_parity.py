from __future__ import annotations

from pathlib import Path

import numpy as np

from mat_viewer.config import element_color, reload_config
from mat_viewer.cube.core import cube_atom_trace
from mat_viewer.cube.io import CubeAtom, CubeData
from mat_viewer.render.cpu.batch import element_style_tables
from mat_viewer.render.style.core import _atom_render_color
from mat_viewer.utils.colors import ansi256_from_hex, element_ansi_color


def _hex_rgb(value: str) -> tuple[int, int, int]:
    text = value.removeprefix("#")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def _cube_for(symbols: tuple[str, ...]) -> CubeData:
    atomic_numbers = {"H": 1, "C": 6, "N": 7, "O": 8, "Cl": 17, "K": 19}
    atoms = [
        CubeAtom(
            atomic_number=atomic_numbers[symbol],
            charge=0.0,
            coord=np.asarray([float(index), 0.0, 0.0]),
        )
        for index, symbol in enumerate(symbols)
    ]
    return CubeData(
        title="palette parity",
        comment="",
        atoms=atoms,
        origin=np.zeros(3),
        axes=np.eye(3),
        values=np.zeros((1, 1, 1)),
        path=Path("palette-parity.cube"),
    )


def test_graphical_adapters_share_the_canonical_element_palette() -> None:
    symbols = ("H", "C", "N", "O", "Cl", "K")
    colors, _radii = element_style_tables()
    atomic_numbers = {"H": 1, "C": 6, "N": 7, "O": 8, "Cl": 17, "K": 19}

    cube_trace = cube_atom_trace(_cube_for(symbols))
    cube_colors = list(cube_trace.marker.color)

    for index, symbol in enumerate(symbols):
        expected = element_color(symbol)
        assert tuple(colors[atomic_numbers[symbol]]) == _hex_rgb(expected)
        assert cube_colors[index] == expected
        assert element_ansi_color(symbol) == ansi256_from_hex(expected)
        assert _atom_render_color({"elem": symbol}, {}) == expected
        assert _atom_render_color({"elem": symbol}, {}, light=True) == element_color(
            symbol, light=True
        )


def test_palette_overrides_reach_every_graphical_adapter() -> None:
    reload_config(overrides={"colors": {"elements": {"O": "#010203"}}})
    try:
        expected = "#010203"
        colors, _radii = element_style_tables()
        cube_trace = cube_atom_trace(_cube_for(("O",)))

        assert tuple(colors[8]) == (1, 2, 3)
        assert list(cube_trace.marker.color) == [expected]
        assert element_ansi_color("O") == ansi256_from_hex(expected)
        assert _atom_render_color({"elem": "O"}, {}) == expected
    finally:
        reload_config("__missing_config__.toml")
