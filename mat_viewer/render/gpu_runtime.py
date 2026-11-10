"""Lazy wgpu import, adapter/device requests, and the capability probe.

Importing this module does not import ``wgpu``. The optional package is
loaded only when a caller probes or renders.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.metadata
import importlib.util
from typing import Any, Mapping

from .gpu_errors import GPUUnavailableError


@dataclass(frozen=True, slots=True)
class GPUProbe:
    """JSON-safe description of the optional WebGPU runtime."""

    available: bool
    backend: str = "wgpu"
    wgpu_version: str | None = None
    adapter_name: str | None = None
    adapter_backend: str | None = None
    device_name: str | None = None
    hardware_accelerated: bool | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": bool(self.available),
            "backend": self.backend,
            "wgpu_version": self.wgpu_version,
            "adapter_name": self.adapter_name,
            "adapter_backend": self.adapter_backend,
            "device_name": self.device_name,
            "hardware_accelerated": self.hardware_accelerated,
            "reason": self.reason,
        }

    # A small mapping-like surface makes probe payloads convenient for callers
    # while retaining a typed record for Python users.
    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def _import_wgpu() -> Any:
    """Import ``wgpu`` lazily and raise a stable, actionable error."""

    if not _wgpu_spec_available():
        raise GPUUnavailableError(
            "The GPU backend requires the optional 'wgpu' package; install "
            "matter-vis[gpu]."
        )
    try:
        return importlib.import_module("wgpu")
    except Exception as exc:  # backend DLL/driver imports can fail broadly
        raise GPUUnavailableError(
            f"The wgpu runtime could not be imported: {type(exc).__name__}: {exc}"
        ) from exc


def _version() -> str | None:
    try:
        return importlib.metadata.version("wgpu")
    except importlib.metadata.PackageNotFoundError:
        return None


def _wgpu_spec_available() -> bool:
    try:
        return importlib.util.find_spec("wgpu") is not None
    except (ImportError, ValueError):
        return False


def _adapter_name(adapter: Any) -> str | None:
    info = getattr(adapter, "info", None)
    if callable(info):
        try:
            info = info()
        except Exception:
            info = None
    if isinstance(info, Mapping):
        value = info.get("device") or info.get("name") or info.get("description")
    else:
        value = getattr(info, "device", None) or getattr(info, "description", None)
    value = value or getattr(adapter, "name", None)
    return str(value) if value else None


def _request_adapter(wgpu: Any) -> Any:
    gpu = getattr(wgpu, "gpu", None)
    if gpu is None:
        raise GPUUnavailableError("wgpu.gpu is unavailable in this runtime")
    request = getattr(gpu, "request_adapter_sync", None)
    if callable(request):
        try:
            return request(power_preference="high-performance")
        except TypeError:
            return request()
    request = getattr(gpu, "request_adapter", None)
    if callable(request):
        result = request(power_preference="high-performance")
        if hasattr(result, "__await__"):
            raise GPUUnavailableError(
                "This GPU backend requires synchronous wgpu adapter requests"
            )
        return result
    raise GPUUnavailableError("wgpu does not expose an adapter request method")


def _request_device(adapter: Any) -> Any:
    request = getattr(adapter, "request_device_sync", None)
    if callable(request):
        try:
            return request()
        except TypeError:
            return request(required_limits={})
    request = getattr(adapter, "request_device", None)
    if callable(request):
        result = request()
        if hasattr(result, "__await__"):
            raise GPUUnavailableError(
                "This GPU backend requires synchronous wgpu device requests"
            )
        return result
    raise GPUUnavailableError("wgpu adapter does not expose a device request method")


def probe() -> GPUProbe:
    """Probe the wgpu runtime and a usable adapter/device without rendering."""

    version = _version()
    if not _wgpu_spec_available():
        return GPUProbe(
            available=False,
            wgpu_version=version,
            reason="optional 'wgpu' package is not installed",
        )
    try:
        wgpu = _import_wgpu()
        adapter = _request_adapter(wgpu)
        if adapter is None:
            return GPUProbe(
                available=False,
                wgpu_version=version,
                reason="wgpu could not find a compatible adapter",
            )
        device = _request_device(adapter)
        if device is None:
            return GPUProbe(
                available=False,
                wgpu_version=version,
                adapter_name=_adapter_name(adapter),
                reason="wgpu adapter did not provide a device",
            )
        info = getattr(adapter, "info", None)
        adapter_backend = None
        adapter_type = None
        if isinstance(info, Mapping):
            adapter_backend = info.get("backend_type") or info.get("backend")
            adapter_type = info.get("adapter_type") or info.get("type")
        else:
            adapter_backend = getattr(info, "backend_type", None) or getattr(
                info, "backend", None
            )
            adapter_type = getattr(info, "adapter_type", None) or getattr(
                info, "type", None
            )
        result = GPUProbe(
            available=True,
            wgpu_version=version,
            adapter_name=_adapter_name(adapter),
            adapter_backend=str(adapter_backend) if adapter_backend else None,
            device_name=_adapter_name(adapter),
            hardware_accelerated=(
                str(adapter_type).lower() not in {"", "cpu", "software", "unknown"}
                if adapter_type is not None
                else None
            ),
        )
        destroy = getattr(device, "destroy", None)
        if callable(destroy):
            destroy()
        return result
    except GPUUnavailableError as exc:
        return GPUProbe(available=False, wgpu_version=version, reason=str(exc))
    except Exception as exc:
        return GPUProbe(
            available=False,
            wgpu_version=version,
            reason=f"{type(exc).__name__}: {exc}",
        )


def probe_gpu() -> GPUProbe:
    """Alias for :func:`probe` used by capability callers."""

    return probe()


def is_available() -> bool:
    """Return whether a synchronous wgpu adapter and device can be created."""

    return probe().available
