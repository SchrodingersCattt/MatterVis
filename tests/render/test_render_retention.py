"""Render caches stay bounded as styles, supercells, and scenes accumulate."""

from __future__ import annotations

import threading

import numpy as np

from mat_viewer.app import ViewerBackend, backend_journal
from mat_viewer.cache_keys import transformed_scene_cache_key
from mat_viewer.loader import build_bundle_scene
from mat_viewer.loader.bundle_builder import build_loaded_crystal_from_atoms
from mat_viewer.render import cache as mesh_cache
from mat_viewer.render import retention
from mat_viewer.render.retention import (
    FIGURE_CACHE_MAX_BYTES,
    FIGURE_CACHE_MAX_ENTRIES,
    FIGURE_JOURNAL_MAX_BYTES,
    MESH_CACHE_MAX_BYTES,
    MESH_CACHE_MAX_ENTRIES,
    MESH_POOL_MAX_BYTES,
    TRANSFORMED_SCENE_CACHE_MAX_BYTES,
    TRANSFORMED_SCENE_CACHE_MAX_ENTRIES,
    BudgetedCache,
    mesh_pool_nbytes,
    payload_nbytes,
    remember_plain_lru,
    unique_retained_nbytes,
)
from mat_viewer.structure.cif_parse import load_cif


def _fat_mesh(n_floats: int) -> dict:
    return {
        "atom_dicts": [
            {
                "type": "mesh3d",
                "x": np.zeros(n_floats, dtype=np.float32),
                "y": np.zeros(n_floats, dtype=np.float32),
                "z": np.zeros(n_floats, dtype=np.float32),
            }
        ],
        "bond_dicts": [],
        "minor_outline_dicts": [],
        "minor_bond_dicts": [],
    }


def test_many_style_keys_do_not_grow_mesh_cache_without_bound():
    scene = {"draw_atoms": [], "bonds": []}
    latest = None
    for index in range(MESH_CACHE_MAX_ENTRIES + 12):
        style = {
            "material": "mesh",
            "style": "ball_stick",
            "atom_scale": round(0.5 + index * 0.01, 3),
            "bond_radius": 0.1,
        }
        latest = mesh_cache._cached_atom_bond_meshes(scene, style, use_fast=False)
    cache = scene["_mesh_trace_cache"]
    assert len(cache) <= MESH_CACHE_MAX_ENTRIES
    assert len(cache) < MESH_CACHE_MAX_ENTRIES + 12
    again = mesh_cache._cached_atom_bond_meshes(
        scene,
        {
            "material": "mesh",
            "style": "ball_stick",
            "atom_scale": round(0.5 + (MESH_CACHE_MAX_ENTRIES + 11) * 0.01, 3),
            "bond_radius": 0.1,
        },
        use_fast=False,
    )
    assert again is latest
    cache.clear()


def test_supercell_like_mesh_traces_respect_byte_budget(monkeypatch):
    n_floats = 700_000  # about 8 MiB of xyz float32

    def fat_atoms(_scene, _style):
        return _fat_mesh(n_floats)["atom_dicts"]

    monkeypatch.setattr(mesh_cache, "_atom_mesh_traces", fat_atoms)
    monkeypatch.setattr(mesh_cache, "_bond_mesh_traces", lambda *_a, **_k: [])
    monkeypatch.setattr(mesh_cache, "_minor_outline_traces", lambda *_a, **_k: [])
    monkeypatch.setattr(
        mesh_cache, "_minor_bond_wireframe_traces", lambda *_a, **_k: []
    )

    scene = {"draw_atoms": [{"elem": "Na"}], "bonds": []}
    inserted = MESH_CACHE_MAX_ENTRIES + 6
    try:
        for index in range(inserted):
            mesh_cache._cached_atom_bond_meshes(
                scene,
                {
                    "material": "mesh",
                    "style": "ball_stick",
                    "atom_scale": round(0.4 + index * 0.03, 3),
                    "bond_radius": 0.1,
                },
                use_fast=False,
            )
        cache = scene["_mesh_trace_cache"]
        assert len(cache) <= MESH_CACHE_MAX_ENTRIES
        assert len(cache) < inserted
        assert cache.accounted_bytes() <= MESH_CACHE_MAX_BYTES
        assert mesh_pool_nbytes() <= MESH_POOL_MAX_BYTES
    finally:
        scene.get("_mesh_trace_cache", BudgetedCache("mesh")).clear()


def test_mesh_pool_evicts_idle_scenes_before_the_hot_entry():
    caches = [BudgetedCache("mesh") for _ in range(6)]
    # Three float32 axes, about 18 MiB per entry. Six of them exceed the
    # shared pool; each one is under the per-scene cap.
    payload_floats = 1_500_000
    try:
        for cache in caches:
            cache.remember("hot", _fat_mesh(payload_floats))
        assert mesh_pool_nbytes() <= MESH_POOL_MAX_BYTES
        assert sum(len(cache) for cache in caches) < len(caches)
        assert "hot" in caches[-1]
    finally:
        for cache in caches:
            cache.clear()


def test_figure_cache_byte_budget_evicts_before_entry_cap():
    cache = BudgetedCache("figure")
    inserted = 10
    for index in range(inserted):
        cache.remember(f"fig-{index}", _fat_mesh(2_000_000))
    assert len(cache) <= FIGURE_CACHE_MAX_ENTRIES
    assert len(cache) < inserted
    assert cache.accounted_bytes() <= FIGURE_CACHE_MAX_BYTES
    assert "fig-9" in cache
    cache.clear()


def test_plain_scene_lru_keeps_the_newest_key():
    sizes: dict = {}
    cache = None
    for index in range(12):
        cache = remember_plain_lru(
            cache,
            ("repeat", index),
            {"draw_atoms": [{"i": index}]},
            sizes,
            max_entries=6,
            max_bytes=1024 * 1024,
            measure=lambda value: 64,
        )
    assert cache is not None
    assert len(cache) <= 6
    assert ("repeat", 11) in cache
    assert ("repeat", 0) not in cache


def test_transformed_scene_cache_does_not_keep_every_repeat():
    parsed = load_cif("benchmarks/structures/ionic_nacl.cif")
    bundle = build_loaded_crystal_from_atoms(
        name="ionic-nacl",
        source_path="benchmarks/structures/ionic_nacl.cif",
        raw_atoms=parsed.atoms,
        cell=parsed.cell,
        M=parsed.matrix,
        source="upload",
    )
    for count in range(1, 13):
        build_bundle_scene(
            bundle,
            display_mode="unit_cell",
            show_hydrogen=True,
            preset={},
            transforms=[
                {
                    "id": f"r{count}",
                    "kind": "repeat",
                    "enabled": True,
                    "params": {"a": count, "b": 1, "c": 1},
                }
            ],
        )
    transformed = bundle._transformed_scene_cache
    assert len(transformed) <= TRANSFORMED_SCENE_CACHE_MAX_ENTRIES
    assert len(transformed) < 12
    last_key = transformed_scene_cache_key(
        display_mode="unit_cell",
        show_hydrogen=True,
        transforms=[
            {
                "id": "ignored",
                "kind": "repeat",
                "enabled": True,
                "params": {"a": 12, "b": 1, "c": 1},
            }
        ],
    )
    assert last_key in transformed
    total = sum(
        bundle._transformed_scene_cache_nbytes.get(key, 0) for key in transformed
    )
    assert total <= TRANSFORMED_SCENE_CACHE_MAX_BYTES


def test_closing_one_scene_drops_its_figure_and_keeps_the_other(tmp_path):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        first = backend.get_state()
        backend.figure_for_state(first)
        first_key = backend._figure_state_cache_key(first)
        assert first_key in backend._figure_cache
        scene = backend.scene_for_state(first)
        mesh_before = len(scene.get("_mesh_trace_cache") or {})
        assert mesh_before >= 1
        second = backend.create_scene()
        backend.delete_scene(first["scene_id"])
        assert first_key not in backend._figure_cache
        assert len(scene.get("_mesh_trace_cache") or {}) == mesh_before
        fig, _topology = backend.figure_for_state(backend.get_state(second["id"]))
        assert fig is not None
    finally:
        backend.close()


def test_closing_the_last_scene_drops_that_structures_mesh_cache(tmp_path):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        state = backend.get_state()
        backend.figure_for_state(state)
        scene = backend.scene_for_state(state)
        assert len(scene.get("_mesh_trace_cache") or {}) >= 1
        for scene_id in list(backend.scene_store.scenes):
            backend.delete_scene(scene_id)
        assert len(scene.get("_mesh_trace_cache") or {}) == 0
    finally:
        backend.close()


def test_figure_cache_still_hits_after_a_version_bump(tmp_path):
    """The byte budget must not bring back the version-bump cache wipe."""

    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        state = backend.get_state()
        first, _topology = backend.figure_for_state(state)
        before = len(backend._figure_cache)
        assert before >= 1
        backend._bump_version()
        second, _topology = backend.figure_for_state(backend.get_state())
        assert len(backend._figure_cache) == before
        assert first.to_dict()["data"] == second.to_dict()["data"]
    finally:
        backend.close()


def test_one_mesh_payload_above_the_byte_budget_is_not_retained():
    cache = BudgetedCache("mesh")
    # Three float32 axes. This is past the 32 MiB mesh ceiling and still
    # under the 96 MiB pool, so only the per-cache admission can reject it.
    payload = _fat_mesh(3_000_000)
    assert payload_nbytes(payload) > MESH_CACHE_MAX_BYTES
    before = mesh_pool_nbytes()
    try:
        assert cache.remember("only", payload) is False
        assert "only" not in cache
        assert cache.accounted_bytes() == 0
        assert mesh_pool_nbytes() == before
    finally:
        cache.clear()


def test_one_figure_payload_above_the_byte_budget_is_not_retained():
    cache = BudgetedCache("figure")
    payload = _fat_mesh(12_000_000)
    assert payload_nbytes(payload) > FIGURE_CACHE_MAX_BYTES
    assert cache.remember("only", payload) is False
    assert "only" not in cache
    assert cache.accounted_bytes() == 0


def test_plain_lru_drops_a_single_entry_that_exceeds_its_budget():
    sizes: dict = {}
    cache = remember_plain_lru(
        None,
        "scene",
        {"draw_atoms": [{"i": 0}]},
        sizes,
        max_entries=4,
        max_bytes=128,
        measure=lambda _value: 10_000,
    )
    assert "scene" not in cache
    assert sizes == {}


def test_shared_array_is_one_retained_copy_and_survives_one_cache():
    shared = np.zeros(4_000, dtype=np.float32)
    mesh = BudgetedCache("label")
    figure = BudgetedCache("figure")
    try:
        mesh.remember("mesh", {"x": shared})
        figure.remember("figure", {"x": shared})
        separate = mesh.accounted_bytes() + figure.accounted_bytes()
        distinct = unique_retained_nbytes([mesh, figure])
        assert distinct < separate
        assert distinct >= shared.nbytes
        mesh.clear()
        assert figure["figure"]["x"] is shared
        assert unique_retained_nbytes([mesh, figure]) >= shared.nbytes
    finally:
        mesh.clear()
        figure.clear()


def test_pool_drops_a_protected_entry_that_alone_exceeds_the_pool(monkeypatch):
    monkeypatch.setattr(retention, "MESH_POOL_MAX_BYTES", 64)
    cache = BudgetedCache("mesh")
    try:
        assert cache.remember("hot", _fat_mesh(64)) is False
        assert "hot" not in cache
        assert mesh_pool_nbytes() == 0
    finally:
        cache.clear()


def test_concurrent_remember_and_pop_stay_inside_the_budget():
    cache = BudgetedCache("figure")
    errors: list[BaseException] = []

    def remember(index: int) -> None:
        try:
            cache.remember(index, {"n": np.zeros(32, dtype=np.float32)})
        except Exception as exc:  # noqa: BLE001 - record any cache failure
            errors.append(exc)

    def drop(index: int) -> None:
        try:
            cache.pop(index, None)
            cache.accounted_bytes()
        except Exception as exc:  # noqa: BLE001 - record any cache failure
            errors.append(exc)

    workers = []
    for index in range(FIGURE_CACHE_MAX_ENTRIES + 24):
        workers.append(threading.Thread(target=remember, args=(index,)))
        workers.append(threading.Thread(target=drop, args=(index,)))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert errors == []
    assert len(cache) <= FIGURE_CACHE_MAX_ENTRIES
    assert cache.accounted_bytes() <= FIGURE_CACHE_MAX_BYTES
    cache.clear()


class _Journal(backend_journal._JournalBackendMixin):
    def __init__(self) -> None:
        self._figure_broadcast_lock = threading.Lock()
        self._figure_broadcasts: list[dict] = []
        self._figure_broadcast_bytes: dict = {}
        self._journal_drop_scene_ids: set[str] = set()

    def _note(self, payload: dict) -> None:
        with self._figure_broadcast_lock:
            backend_journal._JournalBackendMixin._note_journal_locked(self, payload)


def test_journal_keeps_the_newest_figure_that_fits(monkeypatch):
    first_body = {"n": 1}
    second_body = {"n": 2}
    monkeypatch.setattr(
        backend_journal,
        "FIGURE_JOURNAL_MAX_BYTES",
        max(payload_nbytes(first_body), payload_nbytes(second_body)),
    )
    journal = _Journal()
    first = {"type": "figure", "scene_id": "s", "figure": first_body, "figure_seq": 1}
    second = {"type": "figure", "scene_id": "s", "figure": second_body, "figure_seq": 2}
    journal._note(first)
    journal._note(second)
    assert journal._figure_broadcasts == [second]
    latest = backend_journal._JournalBackendMixin.latest_figure_broadcast(journal)
    assert latest is not None
    assert latest["figure"] == second_body


def test_journal_does_not_retain_one_figure_above_the_real_budget():
    body = {"x": np.zeros(18_000_000, dtype=np.float32)}
    assert payload_nbytes(body) > FIGURE_JOURNAL_MAX_BYTES
    payload = {
        "type": "figure",
        "scene_id": "s",
        "figure": body,
        "figure_seq": 1,
    }
    journal = _Journal()
    journal._note(payload)
    assert journal._figure_broadcasts == []
    assert payload["figure"] is body
    assert (
        backend_journal._JournalBackendMixin.latest_figure_broadcast(journal) is None
    )


def test_closing_a_scene_drops_its_accounted_figure_bytes(tmp_path):
    backend = ViewerBackend(
        preset_path=str(tmp_path / "preset.json"), root_dir=str(tmp_path)
    )
    try:
        state = backend.get_state()
        backend.figure_for_state(state)
        assert backend._figure_cache.accounted_bytes() > 0
        scene = backend.scene_for_state(state)
        mesh = scene.get("_mesh_trace_cache")
        assert mesh is not None and mesh.accounted_bytes() > 0
        for scene_id in list(backend.scene_store.scenes):
            backend.delete_scene(scene_id)
        assert backend._figure_cache.accounted_bytes() == 0
        assert mesh.accounted_bytes() == 0
        assert mesh_pool_nbytes() == 0
    finally:
        backend.close()
