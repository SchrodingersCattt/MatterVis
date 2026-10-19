"""Regression guards for the native CIF upload surface."""

from pathlib import Path


ASSET = Path(__file__).parents[2] / "frontend" / "assets" / "native_upload.js"


def test_upload_surface_rebinds_after_dash_replaces_input() -> None:
    """Scene callbacks may replace the hidden file input in place.

    The upload dropzone is intentionally kept as a stable wrapper, so the
    browser listener must follow a newly mounted input and use the current
    element when opening the file picker. A one-time listener leaves the
    visible "click to upload" control dead after a tab refresh.
    """

    source = ASSET.read_text(encoding="utf-8")
    assert "dropzone.__mattervisUploadInput === input" in source
    assert "dropzone.__mattervisUploadInput = input" in source
    assert "currentInput.click()" in source
    assert "new MutationObserver" in source
    assert "observer.observe(root, { childList: true, subtree: true })" in source
