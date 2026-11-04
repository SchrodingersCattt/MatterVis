# Worker shutdown and scene-store durability

`ViewerBackend.close()` must return, including when a render worker is
blocked, and a scene mutation that already landed in memory must survive
the flush that races with that shutdown.

## Lock order

1. Render-worker lock, then the backend lock. Frame publication reads
   scene state outside the worker lock. A caller that already holds the
   backend lock enqueues only after releasing it.
2. Backend lock, then the scene-store lock. Mutations take that order
   when they mark the store dirty.
3. The scene file is replaced only after both of those locks are
   released. `save` and `flush_durable` also take a per-store flush lock
   across snapshot, replace, and conditional clean, so one writer cannot
   install an older generation after another writer has marked a newer
   one clean. The backend lock is not held across the replace.
   `flush_scene_store` clears dirty only when the snapshot generation
   is still current. The file records that generation.
4. The perf-log lock is separate. `record` assigns `seq` under that lock.
   The on-disk perf append still happens inside it; that write is not
   the shutdown hang.

`close()` sets the persist-stop signal, shuts the render pools down,
joins the persist thread, then flushes. The flush and the `atexit`
unregister run in `finally`, so a shutdown exception still persists a
dirty store and does not leave a second flush registered.

## Bounds

These are fixed constants, not a longer pytest timeout.

| Constant | Value | Where |
| --- | --- | --- |
| `_POOL_SHUTDOWN_WAIT_SECONDS` | 5s | each executor join inside `shutdown_executor` |
| `_PERSIST_THREAD_JOIN_TIMEOUT_SECONDS` | 2s | persist thread join |
| `_SCENE_STORE_LOCK_WAIT_SECONDS` | 1s | one attempt to take the backend lock around a snapshot |
| `_SCENE_STORE_FLUSH_ATTEMPTS` | 3 | retries while a newer generation arrived during the replace |

`ThreadPoolExecutor.shutdown(wait=True)` has no timeout on Python 3.10,
3.11, or 3.12. The join runs on a daemon thread and `close` stops
waiting at the pool bound. The executor has already rejected new work.
The running callable is not killed. Workers still alive after the bound
are detached from interpreter shutdown so the process can exit, and
`workers_quiescent` is false. `close` returning does not mean those
workers have stopped. If the final flush cannot persist the current
generation, `close` raises `SceneStorePersistError` after unregistering
the atexit flush. It does not start another flush that might still be
running when the process exits.

## Root cause

| Symptom | Cause |
| --- | --- |
| Teardown sits in `close` until the pytest timeout, with stacks in `shutdown` and `flush_scene_store` | `flush_scene_store` held the backend lock for the whole `save()`, including the file write. A worker inside `get_state` or `broadcast_figure` needs that lock. `shutdown(wait=True)` then joins the worker with no timeout. |
| A scene mutation disappears from the next reader even though it was applied in memory | `save()` released the store lock for the write, then set dirty to false unconditionally. A `mark_dirty` during the write was cleared. The file was also truncated in place, so a crash mid-write could leave a partial document. |
| Perf-log `seq` advanced by more than 1 between two records in one test | `seq` is one process-wide counter. Other threads record through the same lock. `clear()` drops the ring and does not restart the counter. Adjacency was a test assumption. |
| A later flush installs an older snapshot and the store looks clean | Two flushes could pass the replace. The older writer finished second, `mark_clean_if` failed, and the next look saw `dirty == false`. `save` and `flush_durable` now hold one flush lock across snapshot, replace, and clean. |
| `close` returns while a blocked worker keeps the process alive | The bounded join only stops waiting. Interpreter shutdown still joins that worker. After the bound, the worker is detached and `workers_quiescent` is false. |
| A failed final flush is retried on a daemon after `close` returns | The process can exit before that retry writes. `close` raises `SceneStorePersistError` when the store is still dirty. |

The scene file is now written to a temporary file in the same directory
and installed with `os.replace`. A crash mid-write leaves the previous
file.

## What this pass did not change

- Perf-log `seq` is still process-wide and is not reset by `clear()`.
  The disk append stays inside the perf-log lock. It was not the
  shutdown stack.
- The topology cache still stores the geometry object it was given.
  The `is` check in the off-thread topology test matches that store.
- `pytest.ini` already ignores the Dash `pytest_addhooks`
  `PytestRemovedIn10Warning`. No filter was widened.
- No test was skipped, xfailed, or given a higher pytest timeout.

## Red, then green

Before the production change, these assertions failed against the same
tests:

- `save()` cleared dirty after a mutation that landed inside the write.
- A second thread could not take the backend lock during
  `flush_scene_store`'s write.
- The scene added during that write was absent from the reloaded file.
- `close()` was still inside the blocked finalize-pool join after 12
  seconds. Releasing the job let the process continue; the assertion
  did not.

After the change, the same assertions pass. Twenty consecutive runs:

```
python3 -m pytest tests/app/test_worker_shutdown.py tests/scenes/test_scene_store_durability.py tests/scenes/test_perf_log.py tests/app/test_backend_lifecycle.py -q --tb=line
```

Each run builds a fresh backend and, in the shutdown test, blocks a
real finalize-pool job until `close` has returned. Those four files
also passed when run individually and once under
`-n 4 --dist loadfile`. Five fresh CI workers are not claimed from
this local repeat.

The review regressions are in the same shutdown and durability files.
Two threads flush one store while the first replace is stalled inside
`json.dump`. The second flush cannot finish during that replace, and a
clean store's on-disk generation matches the in-memory generation for
both `save` and `flush_durable`. Shutdown is also checked in a child
process. A permanently blocked finalize worker lets that process exit
on its own with `workers_quiescent` false. A final flush that cannot
take the backend lock raises `SceneStorePersistError`, and the scene
added after the lock was held is absent from disk after the child
exits. Those tests passed together with the perf-log, display-worker
synchronization, scene-store recovery, and backend lifecycle neighbors.
