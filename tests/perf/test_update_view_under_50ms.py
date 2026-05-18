from __future__ import annotations

import time
from pathlib import Path

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


def _async_topology_state(backend: ViewerBackend) -> dict:
    state = backend.get_state()
    state.update(
        {
            "topology_enabled": True,
            "polyhedron_specs": [
                {
                    "id": "spec_a",
                    "name": "Async",
                    "center_species": "A",
                    "ligand_species": "X",
                    "color": "#7C5CBF",
                    "enabled": True,
                }
            ],
        }
    )
    return state


def test_async_figure_path_repaints_base_scene_for_cold_topology(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend.resolve_topology_site = lambda **_kwargs: 0  # type: ignore[method-assign]

    class Worker:
        def request_topology(self, _state, _context):
            return True

    backend._render_worker = Worker()

    def fail_if_called(**_kwargs):
        raise AssertionError("cold topology must not run on the request path")

    backend._compute_topology_geometry = fail_if_called  # type: ignore[method-assign]

    started = time.perf_counter()
    fig, topology = backend.figure_for_state(_async_topology_state(backend), async_topology=True)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert fig is not None
    assert topology is None
    assert not (isinstance(fig, dict) and fig.get("_mattervis_pending"))
    assert hasattr(fig, "to_plotly_json")
    assert elapsed_ms < 1000.0


def test_async_figure_path_repaints_when_topology_geometry_is_warm(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    backend.resolve_topology_site = lambda **_kwargs: 0  # type: ignore[method-assign]
    state = _async_topology_state(backend)
    context = backend._topology_context(state)
    assert context is not None
    backend._store_topology_geometry(
        context["structure"],
        context["cache_key"],
        {"spec_results": []},
    )

    fig, topology = backend.figure_for_state(state, async_topology=True)

    assert topology is not None
    assert not (isinstance(fig, dict) and fig.get("_mattervis_pending"))
    assert hasattr(fig, "to_plotly_json")
