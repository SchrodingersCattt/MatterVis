from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from ase import Atoms
from ase.io import write

from _layout_helpers import find_component, layout_ids
from mat_viewer.app import callbacks_state, create_app
from mat_viewer.properties import (
    available_colormaps,
    web_colormap_options,
    web_colormaps,
)


def _trajectory(path: Path) -> None:
    frames = []
    for scale in (1.0, 2.0):
        atoms = Atoms(
            "C" * 31 + "O",
            positions=np.column_stack(
                (np.arange(32, dtype=float), np.zeros(32), np.zeros(32))
            ),
            cell=[40.0, 8.0, 8.0],
            pbc=True,
        )
        atoms.arrays["charge"] = np.linspace(-scale, scale, 32)
        frames.append(atoms)
    write(path, frames, format="extxyz")


def test_web_colormap_catalog_is_curated_but_specs_keep_full_registry() -> None:
    assert web_colormaps() == (
        "viridis",
        "plasma",
        "inferno",
        "magma",
        "cividis",
        "coolwarm",
        "RdBu",
        "PiYG",
        "PRGn",
        "BrBG",
        "tab10",
        "Set2",
        "Dark2",
        "Paired",
        "viridis_r",
        "plasma_r",
        "coolwarm_r",
        "RdBu_r",
    )
    assert len(web_colormaps()) < len(available_colormaps())
    assert set(web_colormaps()) <= set(available_colormaps())
    assert "Spectral" in available_colormaps()
    assert "Spectral" not in web_colormaps()


def test_active_noncurated_colormap_stays_in_browser_options(tmp_path: Path) -> None:
    path = tmp_path / "charges.extxyz"
    _trajectory(path)
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        input_path=str(path),
        atom_property_color={"fields": ["array:charges"], "colormap": "Spectral"},
    )
    try:
        control = find_component(app.layout(), "property-colormap-input")
        values = [item["value"] for item in control.options]
        assert control.value == "Spectral"
        assert "Spectral" in values
        assert "viridis" in values
    finally:
        app.crystal_backend.close()


def test_web_colormap_options_keep_active_noncurated_map() -> None:
    options = web_colormap_options("Spectral")
    assert options[:2] == [
        {"label": "viridis", "value": "viridis"},
        {"label": "plasma", "value": "plasma"},
    ]
    assert options[-1] == {"label": "Spectral", "value": "Spectral"}
    assert web_colormap_options("viridis") == [
        {"label": name, "value": name} for name in web_colormaps()
    ]


def test_sync_state_refresh_keeps_active_noncurated_map_option(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "charges.extxyz"
    _trajectory(path)
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        input_path=str(path),
        atom_property_color={
            "fields": ["array:charges"],
            "colormap": "Spectral",
        },
    )

    class _CallbackRegistry:
        def __init__(self):
            self.callbacks = {}

        def callback(self, *args, **kwargs):
            def register(fn):
                self.callbacks[fn.__name__] = fn
                return fn

            return register

    try:
        backend = app.crystal_backend
        scene_id = backend.active_scene_id()
        backend.patch_state(
            {
                "atom_property_color": {
                    "fields": ["array:charges"],
                    "colormap": "Spectral",
                }
            },
            scene_id=scene_id,
            broadcast=True,
        )
        registry = _CallbackRegistry()
        callbacks_state.register_state_callbacks(registry, backend)
        monkeypatch.setattr(
            callbacks_state,
            "callback_context",
            SimpleNamespace(
                triggered=[{"prop_id": "agent-state-poll.n_intervals"}]
            ),
        )
        result = registry.callbacks["sync_agent_state"](1, None, scene_id)
        options = result[17]
        value = result[18]
        assert value == "Spectral"
        assert {item["value"] for item in options} >= {"viridis", "Spectral"}
    finally:
        app.crystal_backend.close()


def test_web_property_catalog_state_controls_and_trace_batching(tmp_path: Path) -> None:
    path = tmp_path / "charges.extxyz"
    _trajectory(path)
    app = create_app(
        preset_path=str(tmp_path / "preset.json"),
        root_dir=str(tmp_path),
        input_path=str(path),
        frame=1,
        atom_property_color={"fields": ["array:charges"]},
    )
    backend = app.crystal_backend
    state = backend.get_state()
    assert backend.get_bundle(state["structure"]).frame_info["frame_index"] == 1

    client = app.server.test_client()
    catalog = client.get("/api/v2/atom-properties").get_json()
    assert catalog["fields"] == [
        {
            "field": "array:charges",
            "source": "array",
            "name": "charges",
            "dtype": "float64",
            "shape_tail": [],
            "components": [],
            "unit": None,
        }
    ]
    assert catalog["active"]["fields"] == ["array:charges"]

    expected_controls = {
        "property-field-selector",
        "property-reduction-selector",
        "property-component-input",
        "property-colormap-input",
        "property-range-mode",
        "property-range-min",
        "property-range-max",
        "property-center-input",
        "property-nan-color-input",
        "property-colorbar-toggle",
    }
    layout = app.layout()
    assert expected_controls <= layout_ids(layout)
    field_control = find_component(layout, "property-field-selector")
    assert field_control.options == [
        {"label": "array:charges", "value": "array:charges"}
    ]
    colormap_control = find_component(layout, "property-colormap-input")
    colormap_names = {item["value"] for item in colormap_control.options}
    assert {"viridis", "plasma", "coolwarm"} <= colormap_names

    figure, _ = backend.figure_for_state(state)
    traces = figure.to_plotly_json()["data"]
    property_atoms = [
        trace
        for trace in traces
        if (trace.get("meta") or {}).get("mv_role") == "atom" and "vertexcolor" in trace
    ]
    assert len(property_atoms) == 1
    assert len(set(property_atoms[0]["vertexcolor"])) > 16
    assert figure.layout.scene.domain.x == (0, 0.86)

    bond_colors_with_property = sorted(
        str(trace.get("color") or trace.get("line", {}).get("color"))
        for trace in traces
        if (trace.get("meta") or {}).get("mv_role") == "bond"
    )
    client.post("/api/v2/state", json={"atom_property_color": None})
    plain_state = backend.get_state()
    plain_figure, _ = backend.figure_for_state(plain_state)
    bond_colors_without_property = sorted(
        str(trace.get("color") or trace.get("line", {}).get("color"))
        for trace in plain_figure.to_plotly_json()["data"]
        if (trace.get("meta") or {}).get("mv_role") == "bond"
    )
    assert bond_colors_with_property != bond_colors_without_property
    assert len(set(bond_colors_with_property)) > len(set(bond_colors_without_property))

    patched = client.post(
        "/api/v2/state",
        json={
            "atom_property_color": {
                "fields": ["charges"],
                "colormap": "plasma",
                "value_range": [-3, 3],
            },
            "atom_groups": [{"selector": {"elements": ["O"]}, "color": "#FF00FF"}],
        },
    ).get_json()
    assert patched["atom_property_color"]["value_range"] == [-3.0, 3.0]
    override_figure, _ = backend.figure_for_state(backend.get_state())
    atom_traces = [
        trace
        for trace in override_figure.to_plotly_json()["data"]
        if (trace.get("meta") or {}).get("mv_role") == "atom"
    ]
    assert len(atom_traces) == 2
    assert sum("vertexcolor" in trace for trace in atom_traces) == 1
