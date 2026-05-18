from __future__ import annotations

from pathlib import Path

from crystal_viewer.app import ViewerBackend
from crystal_viewer.presets import default_preset_path


def test_topology_request_is_deduplicated_before_worker_submit(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    submitted: list[tuple] = []

    class Worker:
        def request_topology(self, _state, context):
            submitted.append(context["cache_key"])
            return True

    backend._render_worker = Worker()
    backend.resolve_topology_site = lambda **_kwargs: 0  # type: ignore[method-assign]
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

    assert backend.topology_request(state) is True
    assert submitted


def test_async_worker_result_populates_topology_cache(tmp_path: Path):
    backend = ViewerBackend(preset_path=default_preset_path(), root_dir=str(tmp_path))
    state = backend.get_state()
    cache_key = ("structure", "formula_unit", False, 0, 10.0, frozenset(), ())
    geometry = {"spec_results": []}

    backend._store_topology_geometry(state["structure"], cache_key, geometry)
    bundle = backend.get_bundle(state["structure"])

    assert bundle._topology_state_cache[cache_key] is geometry
