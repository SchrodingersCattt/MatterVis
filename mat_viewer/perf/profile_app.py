from __future__ import annotations

import argparse
import cProfile
import io
import pstats
from pathlib import Path

from mat_viewer.app import DEFAULT_PRESET_PATH, ViewerBackend


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CIF = ROOT / "scripts" / "data" / "DAP-4.cif"


def _load_backend(cif_path: Path) -> ViewerBackend:
    backend = ViewerBackend(preset_path=DEFAULT_PRESET_PATH, names=[], root_dir=str(ROOT))
    with cif_path.open("rb") as handle:
        backend.add_uploaded_file_bytes(handle.read(), cif_path.name)
    return backend


def run_profile(cif_path: Path) -> str:
    backend = _load_backend(cif_path)
    state = backend.get_state()

    def scenario() -> None:
        for display_mode in ("formula_unit", "asymmetric_unit", "unit_cell", "formula_unit"):
            scenario_state = backend.normalize_state(
                {
                    **state,
                    "display_mode": display_mode,
                    "topology_enabled": True,
                    "topology_species_keys": state.get("topology_species_keys") or [],
                }
            )
            backend.figure_for_state(scenario_state)
        fragments = backend.fragment_options(state)
        if fragments:
            clicked_state = backend.normalize_state(
                {
                    **state,
                    "topology_site_index": fragments[-1]["value"],
                    "topology_enabled": True,
                }
            )
            backend.figure_for_state(clicked_state)

    profiler = cProfile.Profile()
    profiler.enable()
    scenario()
    profiler.disable()

    buffer = io.StringIO()
    stats = pstats.Stats(profiler, stream=buffer).strip_dirs().sort_stats("cumtime")
    stats.print_stats(30)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile representative MatterVis Dash backend actions.")
    parser.add_argument("--cif", default=str(DEFAULT_CIF), help="CIF path to profile.")
    parser.add_argument("--output", help="Optional path to write the cProfile summary.")
    args = parser.parse_args(argv)

    summary = run_profile(Path(args.cif))
    if args.output:
        Path(args.output).write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
