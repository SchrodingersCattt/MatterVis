"""Small synchronization helpers for the optional WebGPU renderer."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


def submit_and_wait(device: Any, commands: Any) -> None:
    """Submit commands and give the device a chance to complete them."""

    queue = device.queue
    queue.submit(commands)
    poll = getattr(device, "poll", None)
    if callable(poll):
        for argument in (True, getattr(getattr(device, "__class__", object()), "Maintain", None)):
            try:
                poll(argument)
                break
            except Exception:
                continue


def map_readback_sync(
    readback: Any,
    *,
    wgpu: Any,
    device: Any,
    error_type: Callable[[str], Exception] = RuntimeError,
) -> bytes:
    """Map a staging buffer and return its bytes after GPU completion."""

    mode = getattr(getattr(wgpu, "MapMode", None), "READ", 1)
    map_sync = getattr(readback, "map_sync", None)
    if callable(map_sync):
        map_sync(mode)
    else:
        map_async = getattr(readback, "map_async", None)
        if not callable(map_async):
            raise error_type("wgpu readback buffer does not expose map_async/map_sync")
        completed = threading.Event()
        failure: list[BaseException] = []
        poll = getattr(device, "poll", None)

        def done(status: Any = None, *args: Any) -> None:
            # wgpu-py reports ``None``/0/"success" on success depending on
            # the release.  Preserve any non-success status for a useful
            # error instead of reading before the mapping is ready.
            if status not in (None, 0, "success"):
                failure.append(RuntimeError(f"wgpu buffer map failed: {status!r}"))
            completed.set()

        try:
            result = map_async(mode, done)
        except TypeError:
            # A few test doubles and older synchronous wrappers accept only
            # the mode argument.  Give the device a chance to drain the
            # mapping operation before exposing the mapped range; otherwise
            # a wrapper that completes from ``poll`` can still race here.
            result = map_async(mode)
            if callable(poll):
                try:
                    poll(True)
                except Exception:
                    pass
            completed.set()
        if hasattr(result, "__await__"):
            # Newer wrappers may return an awaitable.  A running event loop is
            # not expected in the render worker; if one is present, polling
            # below still drives the native callback.
            try:
                import asyncio

                asyncio.run(result)
            except RuntimeError:
                pass
            completed.set()
        deadline = time.monotonic() + 10.0
        while not completed.is_set() and time.monotonic() < deadline:
            if callable(poll):
                try:
                    poll(True)
                except Exception:
                    pass
            time.sleep(0.001)
        if not completed.is_set():
            raise error_type("timed out waiting for wgpu readback mapping")
        if failure:
            raise error_type(str(failure[0])) from failure[0]

    # wgpu-py 0.20+ exposes ``read_mapped`` instead of the browser
    # ``getMappedRange`` spelling. Keep the latter for test doubles and older
    # wrappers, but prefer the supported Python API when available.
    read_mapped = getattr(readback, "read_mapped", None)
    if callable(read_mapped):
        return bytes(read_mapped())
    get_range = getattr(readback, "get_mapped_range", None)
    if callable(get_range):
        return bytes(get_range())
    raise error_type("wgpu readback buffer does not expose mapped bytes")


def release_gpu_resources(resources: list[Any], device: Any) -> None:
    """Best-effort release of transient GPU resources and the device."""

    for resource in reversed(resources):
        unmap = getattr(resource, "unmap", None)
        if callable(unmap):
            try:
                unmap()
            except Exception:
                pass
        destroy = getattr(resource, "destroy", None)
        if callable(destroy):
            try:
                destroy()
            except Exception:
                pass
    destroy_device = getattr(device, "destroy", None)
    if callable(destroy_device):
        try:
            destroy_device()
        except Exception:
            pass
