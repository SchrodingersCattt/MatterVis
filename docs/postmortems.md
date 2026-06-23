# Postmortems — past mis-fixes worth remembering

These are short case studies of mistakes that have already happened
in this repo. Re-read them before touching the affected modules; the
goal is "don't repeat known failures", not blame.

## "Atom-centred polyhedra" misadventure (PR #11, reverted as #12)

- **Symptom report:** "SY's perchlorate is drawn wrong, EAP-4's
  default polyhedra look weird — please update the latest
  `molcrys_kit` and adapt."
- **Wrong fix that landed (and was reverted):** added a `kind`
  field (`"atom"` vs `"fragment"`) to `polyhedron_specs`, plus a
  hard-coded `_ATOM_POLY_DEFAULT_CHEMISTRY` table mapping element
  pairs to short covalent cutoffs (e.g. Cl-O at 2.0 Å), then routed
  those cases through `find_polyhedra(search_cutoff=2.0)` to "force"
  ClO₄ tetrahedra. Visually it produced the right pictures for
  ClO₄ — but it disabled `find_polyhedra`'s gap+enclosure
  intelligence everywhere, including for the organic cations that
  the user actually cared about. The cuboctahedron-around-cation
  case the user wanted to *keep* was the one this fix broke.
- **Root causes:**
  1. *Misread of the symptom.* The user said "the labels are
     weird"; I read "the geometry is wrong". The geometry was
     fine; the labels (from the deprecated
     `angular_rmsd_vs_ideals` → "ambiguous cuboctahedron + 1 face
     cap+1") were the actual confusing thing.
  2. *Skipped the upstream library docstring.* `molcrys_kit`'s
     `packing_shell` module already documents that
     `find_polyhedra` does both covalent and packing shells from
     the same call, and that `angular_rmsd_vs_ideals` is
     superseded by `shape.classify_shell`. Reading it would have
     pointed at the one-line replacement.
  3. *Added a parallel concept instead of swapping a primitive.*
     Introducing `kind="atom"` doubled the spec table, the API
     surface, and the tests — none of which the chemistry needed.
- **Right fix (this PR):** replace the deprecated
  `angular_rmsd_vs_ideals` call site in
  `crystal_viewer/topology/analysis.py` with `shape.classify_shell`
  (`max_strip=0` to keep labels clean), surface
  `primary_label` / `label_modifier` / `cshm_value` in the
  analysis-text panel, and leave the `polyhedron_specs` shape and
  the `find_polyhedra` invocation strictly alone.
- **Permanent rule (already in AGENTS.md as principle "reuse before
  reinvent"):** before adding a new concept to MatterVis, verify the
  upstream library doesn't already express it. Before wrapping an
  upstream function, verify that function isn't itself deprecated.

## "Re-derived bonds on top of MolCrysKit's molecule graph" misadventure (PR #14)

- **Symptom report:** "DAP-4 / SY produce N1H1 / N1H2 / N1H4 / N1H8
  ammonium fragments + 18 orphan-H "?" rows, even though MolCrysKit
  groups the molecules correctly."
- **Wrong code that lived in the loader:**
  `_unwrapped_atoms_from_atoms` was already routing through
  `molcrys_bridge.analyze` to get unwrapped coordinates, but
  `_fragment_table_from_atoms` then **threw the molecule grouping
  away** and called `ops.find_bonds(atom_pool, cell=cell)` again to
  re-derive connectivity. On atoms sitting on cell-face / corner
  special positions with SHELX-style occupancy disorder, the legacy
  bond detector picked inconsistent PBC images per atom (the
  `_prune_duplicate_label_bond_candidates` step further dropped
  bonds that shared an atom label across symmetry images), splitting
  the eight chemically equivalent NH4⁺ cations into four different
  formulae and stranding 18 disorder-H atoms in their own
  components.
- **Root causes:**
  1. *MolCrysKit was already wired in but the result was discarded.*
     `analyze()` returned `mol_indices` and `mol_cart_positions` at
     load time; the loader copied the unwrapped Cartesian positions
     onto raw_atoms but threw the molecule-membership map away
     before reaching `_fragment_table_from_atoms`. The fix was a
     plumbing change, not new chemistry.
  2. *"It works on the catalog" was misread as "it works".* The
     shipped `scripts/data/DAP-4.cif` is a slightly different SHELX
     export from the user's own DAP-4 (no `occ < 1` rotamers), so
     internal QA didn't trip the bug. The fix landed only after a
     diagnostic that ran the full topology table on the catalog CIF
     and counted "?" / variable-cluster_size rows directly.
  3. *Two formula counters disagreed silently.* The MatterVis loader
     formula (`heavy_atom_count` only) said `"N1"`; the caller-side
     `_formula` (counts including H) said `"N1H8"`. Neither side
     noticed because both happened to put the cation in the same
     A/B bucket. Tests now assert
     `Counter(f["formula"] for f in topology_fragment_table)` has a
     known shape, which would have flagged the regression
     immediately.
- **Right fix (this PR):**
  - Expose `bond_pairs` on `CrystalAnalysis` (the flattened molecule
    graph in raw-index pairs).
  - Rewrite `_fragment_table_from_atoms` to consume `mol_indices`
    directly, dropping the legacy `find_bonds` →
    `_cluster_components` path entirely.
  - Add `_source_index` / `_image_shift` plumbing so formula-unit
    and repeat / supercell paths still resolve back to the right
    molecule.
  - Drop the legacy fallback in `_unwrapped_atoms_from_atoms`;
    `molcrys_analysis` is now a required keyword argument
    everywhere.
  - Add `_has_shelx_occupancy_disorder` + `_tag_shelx_occupancy_disorder`
    so SHELX-occupancy CIFs auto-resolve via
    `generate_ordered_replicas_from_disordered_sites(method="optimal")`
    and the discarded rotamer image gets `_is_minor=True`.
  - Patch `is_minor` to trust explicit `_is_minor` / PART markers, not
    blank partial occupancy alone; otherwise ordered special-position
    atoms render as false minor disorder.
- **Permanent rule:** when MolCrysKit already computes a quantity
  (molecule grouping, bond graph, ordered replica, …), MatterVis must
  consume it as the single source of truth. Re-deriving the same
  thing in MatterVis "for safety" is reinvention by another name and
  has historically been the source of disorder + PBC bugs.
