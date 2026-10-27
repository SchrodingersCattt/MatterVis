# Effect-contract fixtures

| Fixture | Builder | Expectation |
|---|---|---|
| F1 | `f1_ordered_nonorthogonal` | nonorthogonal C–O cell, occupancies 1 |
| F2 | `f2_hydrogen_pbc` | molecule with explicit H near a cell face |
| F3 | `f3_octahedron` | Ni center, six O ligands at 2 Å, cutoff 2.5 Å, CN=6 |
| F4 | `f4_disorder` | two occupancy-0.5 oxygen alternatives plus carbon |
| F5 | `f5_host` plus `WATER` | insertion, clash, and substitution host |
| F6 | `f6_frames` | two frames with separate charge arrays |
| F7 | JSON written by `test_t21_legacy_migration_is_idempotent_and_strict` | legacy scene store |

`legacy_surface.json` classifies REST, CLI, WebSocket, and TUI entry
points. Builders are imported by tests through `tests/effect_builders.py`
because `tests/` is not a package.

Display `supercell` and structural `build_supercell` are different
commands. The inventory marks only the latter as `writes_structure`.
