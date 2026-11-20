"""Explicit failures for the optional WebGPU backend."""

from __future__ import annotations


class GPUError(RuntimeError):
    """Base class for explicit GPU backend failures."""


class GPUUnavailableError(GPUError):
    """Raised when ``wgpu`` or a usable adapter/device is unavailable."""


class GPUUnsupportedError(GPUError):
    """Raised when a render plan is outside the GPU contract."""
