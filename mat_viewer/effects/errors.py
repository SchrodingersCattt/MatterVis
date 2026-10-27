"""Errors for the Display / Analysis / Operate effect core.

These types stay free of HTTP and UI imports. Adapters map
``status_code`` onto REST responses.
"""

from __future__ import annotations


class EffectError(ValueError):
    """Base class for effect-core failures that must not commit."""

    status_code = 400

    def __init__(self, message: str, *, hint: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.hint = hint
        if status_code is not None:
            self.status_code = int(status_code)


class ValidationError(EffectError):
    """Strict structural-command validation failed."""


class UnsupportedOperation(EffectError):
    """The command is outside the supported scientific contract."""


class AmbiguousSelection(EffectError):
    """A display instance was used where a source site is required."""


class ClashError(EffectError):
    """Insertion or substitution violated the declared clash policy."""


class PropertyPolicyError(EffectError):
    """A scientific property would be copied, dropped, or resized unsafely."""


class RevisionConflict(EffectError):
    """The candidate's expected revision is no longer current."""

    status_code = 409


class OperationRejected(EffectError):
    """The candidate exists only as a rejection and cannot be committed."""


class InjectedFailure(EffectError):
    """Test-only failure injected at a named commit stage."""


class PersistenceError(EffectError):
    """A session file is corrupt, unknown, or not losslessly reloadable."""


class MigrationError(PersistenceError):
    """Legacy session migration refused the input."""


class ExportError(EffectError):
    """A requested export format cannot represent the scientific payload."""


__all__ = [
    "AmbiguousSelection",
    "ClashError",
    "EffectError",
    "ExportError",
    "InjectedFailure",
    "MigrationError",
    "OperationRejected",
    "PersistenceError",
    "PropertyPolicyError",
    "RevisionConflict",
    "UnsupportedOperation",
    "ValidationError",
]
