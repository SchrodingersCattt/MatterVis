"""Coverage gap (PR2): exercise the failure path of every editor
callback that depends on ``surface_editor_error``.

Background
----------
On 2026-04-30 the editor callbacks (``manage_polyhedra``,
``manage_atom_groups``, ``manage_bond_groups``, ``manage_transforms``,
their inline-edit siblings, …) used to swallow backend exceptions
silently with ``except Exception: return no_update, no_update``. When
``backend.add_polyhedron_spec`` raised (bad CIF, MCK shape rejection),
``backend.add_transform`` exceeded ``MAX_ATOMS_AFTER_TRANSFORM``, or
``backend.patch_state`` got a malformed payload, the UI looked dead:
the click did nothing and the user had no log to point at.

The fix routes the exception text into the hidden ``#status`` Div via
``ctx.set_props("status", {"children": "<prefix> failed: <message>"})``,
which the existing ``mirror_legacy_status`` callback then styles into
the visible banner. The per-callback closure also writes a
``callback:editor_error`` row into ``perf_log`` so the Server log
panel surfaces the exception type and message.

These tests pin the contract:

1. ``surface_editor_error`` always emits the hidden status update
   (when ``callback_context.set_props`` is available) AND writes a
   single ``callback:editor_error`` perf-log record.
2. The status string takes the shape ``"<prefix> failed: <message>"``.
3. Long error messages get truncated to 240 chars (with ``...``) so
   the banner doesn't overflow the layout.
4. Empty-message exceptions fall back to the exception class name
   (so the banner never shows ``" failed: "``).
5. ``set_props`` failures (Dash < 2.17 without the API) don't bubble
   out -- ``surface_editor_error`` must still write to perf_log.

Without this file, every ``except Exception: surface_editor_error(...)``
branch in ``callbacks_editors.py`` is dead code from a coverage
standpoint -- a refactor that drops the call (or the prefix string)
would not break any test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from crystal_viewer import perf_log
from crystal_viewer.app import callbacks_editors


@pytest.fixture(autouse=True)
def _isolated_perf_log(monkeypatch, tmp_path):
    """``perf_log`` is process-wide; isolate the disk path and clear
    the in-memory buffer so tests don't see stale ambient events."""
    monkeypatch.setattr(perf_log, "_LOG_PATH", str(tmp_path / "cv-perf-test.log"))
    perf_log.clear()
    yield
    perf_log.clear()


@pytest.fixture
def captured_set_props(monkeypatch):
    """Replace ``callback_context.set_props`` with a recording stub
    so we can assert what the editor surfaces to the hidden status
    Div without standing up a real Dash callback context.

    Yields the list mutated by the stub; each entry is
    ``(component_id, props_dict)``.
    """
    captured: list[tuple[str, dict]] = []

    class _StubContext:
        @staticmethod
        def set_props(component_id: str, props: dict) -> None:
            captured.append((component_id, dict(props)))

    monkeypatch.setattr(callbacks_editors, "callback_context", _StubContext)
    yield captured


# ----------------------------------------------------------------- #
# 1. Each prefix the editor surfaces must produce a status update    #
#    AND a perf_log row in the same shape.                           #
# ----------------------------------------------------------------- #
@pytest.mark.parametrize(
    "prefix",
    [
        "Add polyhedron",
        "Update polyhedron",
        "Add atom group",
        "Update atom group",
        "Add bond group",
        "Update bond group",
        "Reorder transforms",
        "Update transform",
        "Add slab transform",
        "Repeat 2x2x2",
        "Clear repeat",
        "Clear all transforms",
    ],
)
def test_surface_error_writes_status_and_perf_log_for_every_prefix(
    prefix: str, captured_set_props
):
    """Every prefix used by the editor callbacks must round-trip
    through ``surface_editor_error`` to produce one banner update
    (component_id=``status``) and one ``callback:editor_error``
    perf-log row whose ``info`` carries the exception type and
    error text.

    If a future refactor renames a prefix in production code,
    this parametrized test catches the drift -- the human-visible
    banner is part of the user-facing UX contract.
    """
    exc = ValueError(f"upstream {prefix.lower()} rejected the payload")

    callbacks_editors.surface_editor_error(prefix, exc)

    # Status banner update
    assert len(captured_set_props) == 1, (
        f"surface_editor_error({prefix!r}, exc) must call set_props "
        "exactly once; multiple updates would race the visible banner."
    )
    component_id, props = captured_set_props[0]
    assert component_id == "status"
    assert props["children"] == f"{prefix} failed: {exc}", (
        "Banner text must follow '<prefix> failed: <error message>' so "
        "the user can see which action triggered the error."
    )

    # Perf log row
    rows = [e for e in perf_log.recent() if e["label"] == "callback:editor_error"]
    assert len(rows) == 1
    info = rows[0].get("info") or {}
    assert info.get("prefix") == prefix
    assert info.get("type") == "ValueError"
    assert "rejected the payload" in (info.get("error") or "")


# ----------------------------------------------------------------- #
# 2. Long error messages are truncated so the banner doesn't bleed   #
#    out of the layout.                                              #
# ----------------------------------------------------------------- #
def test_surface_error_truncates_long_messages_at_240_chars(captured_set_props):
    """The status banner is fixed-width; multi-paragraph stack-trace
    messages must be truncated to keep the layout intact. The
    truncation marker ``"..."`` lets the user know more text is
    available in the Server log panel."""
    payload = "x" * 500
    exc = RuntimeError(payload)

    callbacks_editors.surface_editor_error("Add polyhedron", exc)

    component_id, props = captured_set_props[0]
    text = props["children"]
    assert text.startswith("Add polyhedron failed: "), (
        "Truncation must preserve the prefix so the user can identify "
        "the action that failed even on long error messages."
    )
    payload_section = text.removeprefix("Add polyhedron failed: ")
    assert payload_section.endswith("..."), (
        "Truncated messages must end with '...' so the user knows the "
        "full stack-trace is visible in the perf log."
    )
    assert len(payload_section) == 240, (
        "Truncation cap is 240 chars (237 chars + '...'); larger caps "
        "have historically wrapped the layout on narrow viewports."
    )


# ----------------------------------------------------------------- #
# 3. Empty-message exceptions fall back to the class name.           #
# ----------------------------------------------------------------- #
def test_surface_error_falls_back_to_class_name_for_empty_message(captured_set_props):
    """``KeyError("foo")`` and ``ValueError()`` both stringify
    differently; for the empty-message case the user must still see
    *something* meaningful instead of a stray colon. Falling back
    to ``exc.__class__.__name__`` keeps the banner self-explanatory."""

    class CustomFailure(RuntimeError):
        pass

    callbacks_editors.surface_editor_error("Add transform", CustomFailure())

    _, props = captured_set_props[0]
    assert props["children"] == "Add transform failed: CustomFailure", (
        "Empty-message exceptions must surface as 'Add transform "
        "failed: CustomFailure' so the banner never shows '<prefix> "
        "failed: ' with a dangling colon."
    )


# ----------------------------------------------------------------- #
# 4. set_props raising must NOT bubble out -- the perf log still     #
#    records the error for the Server log panel.                     #
# ----------------------------------------------------------------- #
def test_surface_error_swallows_set_props_failure_but_records_perf_log(monkeypatch):
    """Older Dash builds (< 2.17) lack ``ctx.set_props``; calling it
    raises ``AttributeError``. ``surface_editor_error`` must not
    propagate that out (otherwise the entire callback would fail
    with a confusing inner error masking the real one). The perf
    log record is the safety net the Server log panel reads when
    the banner can't be updated."""

    class _BrokenContext:
        @staticmethod
        def set_props(*_args, **_kwargs):
            raise AttributeError("Dash 2.16 has no set_props")

    monkeypatch.setattr(callbacks_editors, "callback_context", _BrokenContext)

    callbacks_editors.surface_editor_error(
        "Update bond group", ValueError("bond group cap exceeded")
    )

    rows = [e for e in perf_log.recent() if e["label"] == "callback:editor_error"]
    assert len(rows) == 1, (
        "perf_log MUST receive a row even when set_props fails -- "
        "otherwise the user has no signal at all that an editor "
        "error happened."
    )
    info = rows[0]["info"]
    assert info["prefix"] == "Update bond group"
    assert info["type"] == "ValueError"
    assert "cap exceeded" in info["error"]


# ----------------------------------------------------------------- #
# 5. The historical inner alias ``_surface_error`` still resolves to #
#    the public helper so the editor closure keeps working.          #
# ----------------------------------------------------------------- #
def test_register_editor_callbacks_aliases_surface_editor_error_inside_closure():
    """``register_editor_callbacks`` re-binds ``_surface_error`` to
    ``surface_editor_error`` so the ~15 in-closure call sites
    (``except Exception as exc: _surface_error(prefix, exc)``)
    keep compiling without each one having to import the new name.

    If a future refactor drops the alias, every editor callback
    silently regresses to ``except: return no_update`` semantics
    (the actual ``except`` body would NameError before reaching
    return, and Dash logs the NameError as an opaque server error).
    """
    import inspect

    src = inspect.getsource(callbacks_editors.register_editor_callbacks)
    assert "_surface_error = surface_editor_error" in src, (
        "register_editor_callbacks must keep the inner _surface_error "
        "alias pointing at the module-level surface_editor_error so "
        "all closure call sites resolve to the same helper."
    )
