"""Deferred bindings for CLI helpers that import the scientific stack.

Importing this module must not import numpy, ASE, or ``mat_viewer.render``.
Symbols resolve on first call so ``mat-vis skills`` can load ``mat_viewer.cli``
without those dependencies. The bindings live outside ``cli.py`` so that module
can stay within its line cap.
"""

from __future__ import annotations

from importlib import import_module
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


class _Deferred:
    """Call a module attribute, importing that module on first use."""

    def __init__(self, module: str, name: str) -> None:
        self._module = module
        self._name = name
        self._fn: Any = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        function = self._fn
        if function is None:
            function = getattr(import_module(self._module, __package__), self._name)
            self._fn = function
        return function(*args, **kwargs)


def bind_deferred_cli_symbols(namespace: dict[str, Any]) -> None:
    """Install lazy command helpers into ``namespace`` (``cli`` globals)."""
    for key, (module, name) in _BINDINGS.items():
        namespace[key] = _Deferred(module, name)
