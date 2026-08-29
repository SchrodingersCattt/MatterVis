# MatterVis performance notes

This file records developer benchmark results for the Phase 1 performance
cleanup. Benchmarks are run with:

The public user-facing command is `mat-vis`; the benchmark modules below are
developer-only entry points and remain Python module invocations.

```bash
python -m mat_viewer.perf.bench --repeat 3
python -m mat_viewer.perf.profile_app
```

## Pytest suite baseline and tiers (issue #119)

The reproducible full-suite command is:

```bash
python -m pytest tests/ -n 4 --dist loadfile \
  --durations=50 --durations-min=0.25
```

`loadfile` keeps every test module on one worker, preserving module-level
fixture and state assumptions while allowing independent files to run in
parallel. Four workers are fixed deliberately: MatterVis tests create Plotly,
Matplotlib, Dash, and topology objects, so unbounded `-n auto` can trade wall
time for excessive memory pressure.

For a quick local signal, exclude full app/subprocess/export integration tests
and explicit performance fixtures:

```bash
python -m pytest tests/ -n 4 --dist loadfile \
  -m "not integration and not slow"
```

On Windows with Python 3.12.8, ASE 3.29.0, and NumPy 2.5.1, the pre-fix
baseline on 2026-08-29 was 1021 passed, 43 skipped, and 20,689 warnings in
447.89 s serial. Adding four workers without removing repeated setup took
278.45 s and preserved the same pass/skip result. After the issue #119 changes,
the full command completed 1025 passed and 43 skipped in 145.50 s with no
unhandled warnings; the fast command completed 594 passed and 32 skipped in
77.89 s. This is a 67.5% reduction from the serial baseline and a 47.7%
reduction from xdist alone. Both results meet the acceptance budgets of under
190 s for the full command and under 120 s for the fast command on the same
host. CI stores pytest logs and JUnit XML so later changes can compare both
total time and the slowest tests.

The test-only built-in catalog cache is content-addressed by the CIF SHA-256
and every loader input. It stores one template per pytest worker and returns a
deep copy to each backend. Tests that validate real catalog loading use the
`real_catalog_load` marker and bypass the cache.

## Pipeline oracle benchmark

Use the pipeline benchmark to record loader, scene, figure, JSON-encoding, and
scientific-signature baselines without mixing cold and warm cache paths:

```bash
python -m mat_viewer.perf.bench_pipeline scripts/data/DAP-4.cif --repeat 3 --output /tmp/dap4-pipeline.json
```

Each scene and figure report keeps one explicit cold sample separate from the
warm repeated samples. Do not compare cProfile wall time with these values: the
profiler changes the absolute runtime substantially.

The JSON report includes the fixture SHA-256, MatterVis revision, dependency
provenance, peak RSS, existing perf events, and a versioned oracle with separate
section digests. A section digest changing is a chemistry/rendering regression
signal, not a performance result by itself.

`tests/perf/oracles/pipeline_v1.json` stores compact expected signatures. The
external CSD DAP-O4 fixture is intentionally not committed. Run its slow,
formula-unit-only oracle explicitly when the fixture and matching MolCrysKit
revision are available:

```bash
MATTERVIS_DAP_O4_CIF=/absolute/path/DAP-O4.cif \
   pytest tests/perf/test_dap_o4_oracle.py
```

The test verifies the fixture hash and skips when the installed MolCrysKit
revision differs from the baseline entry. It must not be enabled in the normal
fast test suite.

## Baseline

Captured on 2026-05-01 with:

```bash
python -m mat_viewer.perf.bench --repeat 3 --json
python -m mat_viewer.perf.profile_app --output /tmp/mattervis-profile-baseline.txt
```

Test structure: `scripts/data/DAP-4.cif` (`atom_count_unit_cell=192`,
`fragment_count=40`).

| Benchmark | Mean (s) | Median (s) | Notes |
| --- | ---: | ---: | --- |
| `neighbor_pool` | 0.0057 | 0.0057 | 30 candidates |
| `topology_full` | 0.0082 | 0.0074 | CN 6, pool 30 |
| `atom_mesh_unit_cell` | 0.7065 | 0.6872 | 192 atoms, 168 bonds, 8 traces |
| `planarity cn_8` | 0.0054 | 0.0056 | exhaustive combinations |
| `planarity cn_10` | 0.0214 | 0.0219 | exhaustive combinations |
| `planarity cn_12` | 0.0685 | 0.0690 | exhaustive combinations |
| `planarity cn_14` | 0.2079 | 0.2131 | exhaustive combinations |

Profile scenario: 5 representative `ViewerBackend.figure_for_state` calls
took 11.559 s total. Top cumulative hot spots:

| Function | Cumulative (s) | Note |
| --- | ---: | --- |
| `renderer.build_figure` | 9.317 | main figure assembly |
| `renderer._cached_atom_bond_meshes` | 4.816 | atom/bond payload construction |
| `copy.deepcopy` | 4.068 | Plotly validation / object copying |
| `renderer._atom_mesh_traces` | 2.350 | atom sphere tessellation |
| `app.scene_for_state` / `loader.build_bundle_scene` | 1.808 | scene build/cache path |
| `crystal_scene._label_payload` | 1.186 | label collision placement |

## After Phase 1

Captured after the topology, renderer, Dash callback, scene-cache, and cleanup
changes in the same environment.

| Benchmark | Mean (s) | Median (s) | Baseline mean (s) | Change |
| --- | ---: | ---: | ---: | ---: |
| `neighbor_pool` | 0.0010 | 0.0009 | 0.0057 | 5.7x faster |
| `topology_full` | 0.0023 | 0.0020 | 0.0082 | 3.5x faster |
| `atom_mesh_unit_cell` | 0.0654 | 0.0177 | 0.7065 | 10.8x faster |
| `planarity cn_8` | 0.0008 | 0.0007 | 0.0054 | 6.5x faster |
| `planarity cn_10` | 0.0026 | 0.0025 | 0.0214 | 8.4x faster |
| `planarity cn_12` | 0.0075 | 0.0073 | 0.0685 | 9.1x faster |
| `planarity cn_14` | 0.0181 | 0.0179 | 0.2079 | 11.5x faster |

Profile scenario: 5 representative `ViewerBackend.figure_for_state` calls took
4.238 s total, down from 11.559 s (2.7x faster). Top remaining cumulative hot
spots:

| Function | Cumulative (s) | Note |
| --- | ---: | --- |
| `renderer.build_figure` | 2.161 | mostly Plotly object construction/layout |
| `app.scene_for_state` / `loader.build_bundle_scene` | 1.858 | scene build/cache path |
| `crystal_scene._label_payload` | 1.228 | label collision placement |
| `renderer.topology_foreground_traces` | 0.806 | primary/extra overlay markers |
| `loader._fragment_table_from_atoms` | 0.484 | only cold display scopes |

## After Phase 2 (auto_view + label cache, 2026-05-11)

The single biggest user-facing latency was ``loader.build_loaded_crystal``
on a *cold* CIF: ~9.85 s on DAP-4. ``cProfile`` showed
``plot_crystal.auto_view_dir`` accounting for ~9.0 s of that, with the
hot path being ~1080 candidate-view scoring iterations × O(N^2) Python
occlusion loops × per-pair ``_pair_weight`` dict lookups, plus 8 656
``np.percentile`` calls inside ``_cluster_crowding_penalty``.

Three changes (see git log around ``perf(legacy): auto_view_dir LRU
cache + vectorise occlusion loop``) cut both the cold-load cost and the
per-interaction stutter:

1. **``auto_view_dir`` content-hashed LRU**. Cache key = (rounded atom
   positions, labels, elements, ``M``, cell, compound name); cache
   capped at 64 entries. Catalog presets, REST round-trips, dev
   reloads, and the test suite (which loads the same CIFs many times)
   now pay the cost once.
2. **Vectorised occlusion / weight precomputation**. The O(N^2) Python
   loop that re-evaluated ``_pair_weight`` for every (i, j) pair × every
   candidate view is replaced with a single hoisted ``(N, N)`` weight
   matrix and a numpy mask reduction. ``excluded_pairs`` likewise lifts
   to a precomputed boolean matrix.
3. **``_cluster_shape_p80`` + manual p10/p90**. ``np.percentile`` for a
   single quantile on tiny (5-10 element) arrays was ~50 us of dispatch
   overhead per call; replaced with ``np.sort`` + linear interpolation,
   which exactly matches numpy's default mode.
4. **``_compute_label_positions`` content-hashed LRU**. Style toggles,
   palette swaps, and re-renders with identical geometry now hit the
   cache instead of running the 80-iteration force-directed sweep.

Wall-clock impact (DAP-4):

| Scenario | Before | After | Speedup |
| --- | ---: | ---: | ---: |
| cold ``build_loaded_crystal`` | 9.85 s | 4.7 s | 2.1x |
| warm ``build_loaded_crystal`` (cache hit) | 9.85 s | 0.74 s | 13.3x |
| ``build_bundle_scene`` repeat 2x2x1, 2nd call | 1.06 s | 0.000 s | full cache |
| pytest (whole suite) | 185.9 s | 137.5 s | 1.35x |

The cached values are content-addressed so they are safe to share
across structures, and across processes that touch the same CIF
file. Cache invalidation happens automatically when the input atom
positions change (e.g. after a transform).

## Million-atom LAMMPS animation path (2026-08-26)

Acceptance fixture: 161-frame LAMMPS text dump with 342,384 atoms per source
frame, repeated `3 1 1` at runtime to 1,027,152 atoms per rendered frame. The
CPU benchmark uses 32 workers, 1200x900, 10 fps, orthographic projection, one
shared viewport, analytic spheres, and no atom sampling.

```bash
mat-vis render all.lammpstrj -o million.gif \
  --backend cpu --style ball --repeat 3 1 1 \
  --width 1200 --height 900 --scale 1 --fps 10 --orthogonal \
  --camera-axis a --workers 32 --profile-json million-profile.json
```

| Output | Cold total | Bytes | Peak worker RSS | Notes |
| --- | ---: | ---: | ---: | --- |
| GIF spheres | 21.27 s | 8,942,353 | 206.0 MiB | one streaming global 6x7x6 palette |
| MP4 spheres | 17.46 s | 26,847,460 | 213.4 MiB | H.264, yuv420p, ultrafast CRF 18 |
| MP4 spheres + bonds | 58.30 s | 24,505,331 | 1,347.7 MiB | MCK inference on every frame |

The profile separates indexing, camera fitting, JIT warm-up, parsing,
replication, bond inference, projection, rasterization, palette quantization,
encoding, and total wall time. Explicit worker counts are preserved; automatic
mode remains bounded by available CPU and memory. Camera, projection, zoom,
margin, dimensions, fps/frame duration, and view vectors are user-adjustable,
but are fixed across all selected frames after fitting.

All three outputs decode to 161 frames at 1200x900 and 10 fps. The bonded run
rebuilt its Verlet candidate list on all 161 frames because the displacement
between saved frames exceeded half the 0.5 Angstrom skin. Its summed worker CPU
time was 868.14 s for bond inference versus 78.88 s for bonded rasterization,
