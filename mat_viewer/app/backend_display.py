"""One-way display facade.

Public view methods forward into :class:`DisplayService`. This module
does not keep a second copy of the transform or layer logic.
"""

from __future__ import annotations

from typing import Any

from .display_service import DisplayService


class _DisplayBackendMixin:
    def _display_service(self) -> DisplayService:
        service = getattr(self, "_display_service_obj", None)
        if service is None:
            service = DisplayService(self)
            self._display_service_obj = service
        return service

    def _note_display_edit(self) -> None:
        session = getattr(self, "_effect_session", None)
        if session is not None:
            session.note_display_edit()

    def validate_transform(self, *args, **kwargs):
        return self._display_service().validate_transform(*args, **kwargs)

    def list_transforms(self, scene_id: str | None = None):
        return self._display_service().list_transforms(scene_id)

    def add_transform(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().add_transform(*args, **kwargs)

    def update_transform(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().update_transform(*args, **kwargs)

    def remove_transform(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().remove_transform(*args, **kwargs)

    def reorder_transforms(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().reorder_transforms(*args, **kwargs)

    def list_polyhedron_specs(self, scene_id: str | None = None):
        return self._display_service().list_polyhedron_specs(scene_id)

    def add_polyhedron_spec(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().add_polyhedron_spec(*args, **kwargs)

    def update_polyhedron_spec(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().update_polyhedron_spec(*args, **kwargs)

    def remove_polyhedron_spec(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().remove_polyhedron_spec(*args, **kwargs)

    def reorder_polyhedron_specs(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().reorder_polyhedron_specs(*args, **kwargs)

    def set_polyhedron_instance_override(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().set_polyhedron_instance_override(*args, **kwargs)

    def clear_polyhedron_instance_override(self, *args, **kwargs):
        self._note_display_edit()
        return self._display_service().clear_polyhedron_instance_override(*args, **kwargs)

    def _effective_polyhedron_specs(self, state: dict[str, Any]):
        return self._display_service()._effective_polyhedron_specs(state)


__all__ = ["_DisplayBackendMixin"]
