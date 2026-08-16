from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "visualize-materials" / "scripts"


def test_installer_keeps_venv_optional_and_pins_current_release() -> None:
    installer = SCRIPTS / "install_runtime.sh"
    run = subprocess.run(
        ["bash", str(installer), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Usage: install_runtime.sh [options]" in run.stdout
    assert "--venv ABSOLUTE_PATH" in run.stdout
    assert "--venv ABSOLUTE_PATH [options]" not in run.stdout
    assert '"matter-vis==0.0.3"' in installer.read_text(encoding="utf-8")
