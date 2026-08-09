from __future__ import annotations

import pytest

from crystal_viewer.cli import main


def test_help_uses_mat_vis_command_name(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "usage: mat-vis" in capsys.readouterr().out
