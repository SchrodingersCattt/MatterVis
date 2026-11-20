"""Figure text follows the drawing, not a phrase in the skill file.

Structural panels stay free of titles, ticks, and legends. A measured
atom property still shows its scale, unit, and range. A numerical plot
shows axis labels, units, and ticks. An explicit request wins.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from ase import Atoms
from ase.io import write
from PIL import Image, ImageDraw, ImageFont

from mat_viewer.loader.structure_input import load_structure_input
from mat_viewer.properties import AtomPropertyColorSpec
from mat_viewer.render.planning import prepare_render
from mat_viewer.render.plotly import build_figure
from mat_viewer.render.property_colorbar import draw_raster_colorbar
from mat_viewer.render.topology import topology_histogram_figure


def _source(tmp_path: Path):
    atoms = Atoms(
        "CO",
        positions=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0]],
        cell=[8.0, 8.0, 8.0],
        pbc=True,
    )
    path = tmp_path / "pair.extxyz"
    write(path, atoms, format="extxyz")
    return load_structure_input(path, frame_indices=[0])


def _visible_text(figure) -> list[str]:
    texts: list[str] = []
    for item in figure.layout.annotations or []:
        text = str(getattr(item, "text", "") or "")
        if text.strip():
            texts.append(text)
    title = getattr(figure.layout.title, "text", None)
    if title:
        texts.append(str(title))
    for trace in figure.data:
        if "text" in str(getattr(trace, "mode", "") or ""):
            value = getattr(trace, "text", None)
            if isinstance(value, (list, tuple)):
                texts.extend(str(item) for item in value if str(item).strip())
            elif value:
                texts.append(str(value))
        colorbar = getattr(getattr(trace, "marker", None), "colorbar", None)
        title_text = getattr(getattr(colorbar, "title", None), "text", None)
        if title_text:
            texts.append(str(title_text))
    return texts


def _scene_axes_hidden(figure) -> bool:
    scene = figure.layout.scene
    return (
        scene.xaxis.visible is False
        and scene.yaxis.visible is False
        and scene.zaxis.visible is False
    )


def test_structure_panel_has_no_visible_text(tmp_path: Path) -> None:
    plan = prepare_render(
        _source(tmp_path),
        render={"show_cell": False, "show_labels": False, "show_axes": False},
    )
    figure = build_figure(plan)
    assert _visible_text(figure) == []
    assert _scene_axes_hidden(figure)
    assert figure.layout.showlegend is False
    assert all("text" not in str(getattr(trace, "mode", "") or "") for trace in figure.data)


def test_requested_atom_labels_and_axis_letters_are_shown(tmp_path: Path) -> None:
    source = _source(tmp_path)
    labeled = build_figure(
        prepare_render(source, render={"show_cell": False, "show_labels": True})
    )
    texts = _visible_text(labeled)
    assert texts
    assert any(text.startswith("C") for text in texts)
    assert any(text.startswith("O") for text in texts)

    axes = build_figure(
        prepare_render(source, render={"show_cell": True, "show_axes": True})
    )
    compass = _visible_text(axes)
    assert "<b>a</b>" in compass
    assert "<b>b</b>" in compass
    assert "<b>c</b>" in compass


def test_measured_property_shows_unit_and_range_and_can_be_hidden(tmp_path: Path) -> None:
    atoms = Atoms(
        "CO",
        positions=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0]],
        cell=[8.0, 8.0, 8.0],
        pbc=True,
    )
    atoms.arrays["charge"] = np.asarray([-1.0, 1.0])
    path = tmp_path / "charges.extxyz"
    write(path, atoms, format="extxyz")
    source = load_structure_input(path, frame_indices=[0])
    spec = AtomPropertyColorSpec(
        fields=("array:charges",),
        label="charge",
        unit="eV",
        show_colorbar=True,
    )
    plan = prepare_render(source, render={"show_cell": False}, atom_property_color=spec)
    figure = build_figure(plan)
    colorbars = [
        trace
        for trace in figure.data
        if (getattr(trace, "meta", None) or {}).get("mv_role") == "atom_property_colorbar"
    ]
    assert len(colorbars) == 1
    marker = colorbars[0].marker
    assert marker.cmin == -1.0
    assert marker.cmax == 1.0
    assert marker.colorbar.title.text == "charge (eV)"

    hidden = prepare_render(
        source,
        render={"show_cell": False},
        atom_property_color=AtomPropertyColorSpec(
            fields=("array:charges",),
            label="charge",
            unit="eV",
            show_colorbar=False,
        ),
    )
    hidden_figure = build_figure(hidden)
    assert _visible_text(hidden_figure) == []
    assert all(
        (getattr(trace, "meta", None) or {}).get("mv_role") != "atom_property_colorbar"
        for trace in hidden_figure.data
    )


def test_raster_colorbar_text_stays_inside_the_export(tmp_path: Path) -> None:
    atoms = Atoms(
        "CO",
        positions=[[0.0, 0.0, 0.0], [1.4, 0.0, 0.0]],
        cell=[8.0, 8.0, 8.0],
        pbc=True,
    )
    atoms.arrays["charge"] = np.asarray([-1.0, 1.0])
    path = tmp_path / "charges.extxyz"
    write(path, atoms, format="extxyz")
    source = load_structure_input(path, frame_indices=[0])
    plan = prepare_render(
        source,
        render={"show_cell": False, "width": 320, "height": 240},
        atom_property_color=AtomPropertyColorSpec(
            fields=("array:charges",),
            label="charge",
            unit="eV",
        ),
    )
    image = Image.new("RGBA", (plan.width, plan.height), (255, 255, 255, 255))
    boxes: list[tuple[int, int, int, int, str]] = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, **kwargs):
        font = kwargs.get("font") or ImageFont.load_default()
        left, top, right, bottom = font.getbbox(str(text))
        x, y = xy
        boxes.append((x + left, y + top, x + right, y + bottom, str(text)))
        return original(self, xy, text, **kwargs)

    ImageDraw.ImageDraw.text = capture
    try:
        draw_raster_colorbar(image, plan)
    finally:
        ImageDraw.ImageDraw.text = original
    width, height = image.size
    assert boxes
    for left, top, right, bottom, text in boxes:
        assert left >= 0 and top >= 0, text
        assert right <= width and bottom <= height, text
    drawn = [text for *_bounds, text in boxes]
    assert "charge (eV)" in drawn
    assert "-1" in drawn
    assert "1" in drawn


def test_distance_histogram_shows_labels_units_and_ticks() -> None:
    distances = [2.0, 2.2, 3.5]
    figure = topology_histogram_figure(
        {"all_distances": distances, "distances": [2.0, 2.2]}
    )
    assert figure.layout.xaxis.title.text == "Neighbor rank"
    assert figure.layout.yaxis.title.text == "Distance (Å)"
    assert figure.layout.font.size >= 8
    assert figure.layout.margin.l >= figure.layout.font.size
    assert figure.layout.margin.b >= figure.layout.font.size
    assert figure.layout.xaxis.showticklabels is not False
    assert figure.layout.yaxis.showticklabels is not False
    assert list(figure.data[0].y) == distances
    assert list(figure.data[0].x) == [1, 2, 3]
