"""Deferred bindings for CLI helpers that import the scientific stack.

Importing this module must not import numpy, ASE, or ``mat_viewer.render``.
Symbols resolve on first call so ``mat-vis skills`` can load ``mat_viewer.cli``
without those dependencies. The bindings live outside ``cli.py`` so that module
can stay within its line cap.
"""

from __future__ import annotations

from functools import update_wrapper
from importlib import import_module
import inspect
from typing import Any


_BINDINGS: dict[str, tuple[str, str]] = {
    "_add_atom_property_arguments": (".properties.cli", "add_atom_property_arguments"),
    "_add_render_control_arguments": (".render.cli_controls", "_add_render_control_arguments"),
    "_animation_time_from_args": (".render.cli_controls", "_animation_time_from_args"),
    "_atom_property_spec": (".properties.cli", "atom_property_spec"),
    "_camera_request": (".render.cli_controls", "_camera_request"),
    "_file_sha256": (".structure.inspect", "file_sha256"),
    "_frame_annotation_from_args": (".render.cli_controls", "_frame_annotation_from_args"),
    "_inspect_payload": (".structure.inspect", "inspect_payload"),
    "_is_animation_output": (".render.cli_controls", "_is_animation_output"),
    "_is_nonperiodic_structure": (".structure.inspect", "is_nonperiodic_structure"),
    "_load_overlay_file": (".render.overlay.io", "load_overlay_file"),
    "_render_ortep_mode": (".render.cli_controls", "_render_ortep_mode"),
    "_render_shading": (".render.cli_controls", "_render_shading"),
    "_style_groups": (".render.cli_controls", "_style_groups"),
    "_validate_render_options": (".render.cli_controls", "_validate_render_options"),
    "add_batch_render_arguments": (".render.fast_cli", "add_batch_render_arguments"),
    "add_bond_policy_arguments": (".render.cli_controls", "add_bond_policy_arguments"),
    "bond_thresholds_from_args": (".render.cli_controls", "bond_thresholds_from_args"),
    "render_batch_if_selected": (".render.fast_cli", "render_batch_if_selected"),
}


_TARGET_METADATA = frozenset({
    "__annotations__",
    "__doc__",
    "__name__",
    "__qualname__",
})


class _Deferred:
    """Call a module attribute, importing that module on first use.

    Reading ``__name__``, ``__doc__`` or the signature resolves the target and
    then reports that function. Importing this module does not.
    """

    def __init__(self, module: str, name: str) -> None:
        self._module = module
        self._name = name
        self._fn: Any = None

    def _resolve(self) -> Any:
        function = self._fn
        if function is None:
            function = getattr(import_module(self._module, __package__), self._name)
            self._fn = function
            update_wrapper(self, function)
        return function

    def __getattribute__(self, name: str) -> Any:
        if name in _TARGET_METADATA or name == "__signature__":
            function = object.__getattribute__(self, "_resolve")()
            if name == "__signature__":
                return inspect.signature(function)
            return getattr(function, name)
        return object.__getattribute__(self, name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._resolve()(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


def bind_deferred_cli_symbols(namespace: dict[str, Any]) -> None:
    """Install lazy command helpers into ``namespace`` (``cli`` globals)."""
    for key, (module, name) in _BINDINGS.items():
        namespace[key] = _Deferred(module, name)


def fail_cli(message: str, *, json_output: bool, emit, code: int = 2, details: dict | None = None) -> None:
    """Print a CLI error and exit. ``details`` is included in ``--json`` output."""

    import sys

    print(f"Error: {message}", file=sys.stderr)
    if json_output:
        payload = {"schema": "mattervis.error/v1", "ok": False, "error": message}
        if details:
            payload["details"] = details
        emit(payload, json_output=True)
    raise SystemExit(code)


def fail_cli_exception(exc: BaseException, *, json_output: bool, fail) -> None:
    """Exit from a render failure, keeping polyhedron shell errors structured."""

    from .topology.disorder_policy import PolyhedronShellError

    details = exc.to_dict() if isinstance(exc, PolyhedronShellError) else None
    fail(str(exc), json_output=json_output, details=details)
