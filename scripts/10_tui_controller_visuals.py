"""Generate inspectable terminal-controller verification frames.

Run with ``python scripts/10_tui_controller_visuals.py``. The text artifacts
under ``verification_screens/tui_controller/`` are intentionally committed as
human-review evidence for terminal rendering behavior.
"""

from __future__ import annotations

from pathlib import Path

from mat_viewer.tui import TerminalViewController


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "verification_screens" / "tui_controller"


def _write(name: str, controller: TerminalViewController) -> None:
    observation = controller.observe()
    (OUTPUT / f"{name}.txt").write_text(
        f"{observation.title}\n\n{observation.frame}\n",
        encoding="utf-8",
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    dap4 = ROOT / "scripts" / "data" / "DAP-4.cif"

    mono = TerminalViewController.from_file(str(dap4), width=80, height=22, mono=True)
    _write("01_initial_mono", mono)
    mono.orbit(yaw_deg=45.0, pitch_deg=20.0)
    _write("02_orbit_mono", mono)
    mono.focus_molecule({"display_molecule_index": 0})
    _write("03_focus_mono", mono)
    mono.set_display(display_level="molecule", show_minor=True)
    _write("04_molecule_minor_mono", mono)

    color = TerminalViewController.from_file(str(dap4), width=80, height=22, mono=False)
    color.orbit(yaw_deg=45.0, pitch_deg=20.0)
    color.set_display(display_level="molecule", show_minor=True)
    _write("05_molecule_minor_color", color)


if __name__ == "__main__":
    main()