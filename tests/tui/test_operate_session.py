"""Successive terminal operations accumulate one scientific revision chain."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mat_viewer.structure.cif_parse import load_cif
from mat_viewer.tui.operate_command import execute_operate_command

CIF = Path("docs/qa/urea-default.cif")


def test_second_operate_command_does_not_reload_the_original_cif(monkeypatch):
    original = load_cif
    poisoned = {"on": False}

    def counted(path, *args, **kwargs):
        loaded = original(path, *args, **kwargs)
        if poisoned["on"]:
            loaded.crystal.metadata["reloaded"] = True
        return loaded

    monkeypatch.setattr("mat_viewer.structure.cif_parse.load_cif", counted)
    crystal = SimpleNamespace(source_path=str(CIF.resolve()), title="urea")
    _message, first = execute_operate_command(crystal, ["build-supercell", "2", "1", "1"])
    session = first._effect_session
    sites = session.snapshot(session.current_ref("urea")).n_sites
    poisoned["on"] = True
    _message, second = execute_operate_command(first, ["build-supercell", "2", "1", "1"])
    assert second._effect_session is session
    assert session.current_ref("urea").revision == 3
    assert session.snapshot(session.current_ref("urea")).n_sites == sites * 2
    assert "reloaded" not in session.checkout(session.current_ref("urea")).metadata
