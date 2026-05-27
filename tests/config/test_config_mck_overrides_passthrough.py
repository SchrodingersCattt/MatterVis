from __future__ import annotations

from crystal_viewer.config import reload_config
from crystal_viewer.topology.analysis import _mck_override_kwargs


def test_mck_overrides_are_omitted_when_unset():
    reload_config("__missing_config__.toml")

    def fake_find_polyhedra(*, gap_threshold=None):
        return []

    assert _mck_override_kwargs(fake_find_polyhedra) == {}


def test_mck_overrides_are_forwarded_only_when_supported(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[mck_overrides]
gap_threshold = 0.25
enclosure_expand_max = 2.0
default_search_cutoff = 12.5
""",
        encoding="utf-8",
    )
    try:
        reload_config(str(path))

        def fake_find_polyhedra(*, gap_threshold=None, default_search_cutoff=None):
            return []

        assert _mck_override_kwargs(fake_find_polyhedra) == {
            "gap_threshold": 0.25,
            "default_search_cutoff": 12.5,
        }
    finally:
        reload_config("__missing_config__.toml")
