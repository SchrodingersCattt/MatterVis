# Issue 163 execution ledger

- Base: `4a5b4da53cda177e74e89d086792b68ed3771bdb` (`main`)
- Branch: `refactor/issue-163-display-operate-analysis`
- MolCrysKit: git `9bf0ffb0fb321d1084dbc444250b5f186af3c873` (installed distribution `0.8.1.dev1`)
- Python: 3.12.3

## Decisions

- One `EffectSession` owns revisions. Display and analysis cannot append one.
- Legacy `supercell` and `/transforms` stay display-only. Structural work is `build_supercell`.
- `scene_for_state` no longer writes `bundle.scene` or `bundle.fragment_table`.
- In-memory `loaded_crystal_from_crystal` uses `analyze_crystal` on the committed crystal. It does not write a temporary CIF.
- Property arrays require an explicit policy. Energy is not copied onto a supercell.
- `delete_periodic_copy` is one commit composed of supercell construction and site deletion.
- Legacy scene migration writes view configuration, keeps a `.bak`, and commits nothing.

## Checks

- Focused effect tests passed (44) on the first implementation pass.
- Full `python -m pytest tests/ -n 4 --dist loadfile` after the PR #246 review fixes, at `84ff09d`: 2073 passed, 70 skipped, exit 0, 95.60 s.
- Evidence file: `docs/architecture/issue-163-acceptance-report.json`. The report commit does not change runtime code.
- Review follow-ups on that commit: revision numbers are monotonic, a repeated request id does not move the pointer, candidates are sealed, publication failure rolls back, minimum-image distances use ASE, periodic coordination includes image neighbors, disorder crystals come from one generation, the TUI keeps one session, REST operate requires a target and expected revision, periodic materialization uses the supplied cell, and session files keep metadata and site arrays.
