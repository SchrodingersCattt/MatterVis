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

## Checks recorded before the acceptance report

- `python -m pytest tests/architecture tests/contracts tests/operations tests/analysis` passed (44).
- `python -m pytest tests/ -n 4 --dist loadfile -m "not integration and not slow"` passed 931, skipped 34, before the CLI line-limit adjustment.
- Full `tests/` run is recorded in the acceptance report.
