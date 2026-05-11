# Topology scores reference

`crystal_viewer.topology.analyze_topology` returns one JSON-serialisable dict
that bundles **five** distinct scores characterising a coordination shell.
This page explains what each score means, how it is computed, and how to
interpret the value.

All scores are derived from the **shell** (the `CN` nearest neighbouring
fragments of the chosen centre). The shell itself is extracted by
`extract_coordination_shell`, which walks every periodic image of every
fragment inside `cutoff` Å and sorts them by distance.

```python
from crystal_viewer.loader import build_loaded_crystal
from crystal_viewer.topology import analyze_topology

bundle = build_loaded_crystal(name="DAP-4", cif_path="scripts/data/DAP-4.cif")
# The first A-site fragment in DAP-4 is at index 8; tune cutoff to your lattice.
result = analyze_topology(bundle, center_index=8, cutoff=8.0)
print(result.keys())
```

The dict has the following top-level keys (schema v1):

| Key | Type | Provenance |
| --- | --- | --- |
| `center_index` / `center_label` / `center_type` | int / str / str | echoes the input |
| `center_coords` / `source_center_coords` | `list[float]` (3) | centre in plot and source space |
| `cutoff` | float | echoes the input, Å |
| `neighbor_pool_size` | int | candidates within `cutoff` before the gap cut |
| `coordination_number` | int | see **1. Coordination number** below |
| `gap_info` | dict | see **1. Coordination number** |
| `shell` / `shell_coords` / `distances` | list / list / list[float] | the selected `CN` neighbours |
| `all_distances` / `pool_coords` | list[float] / list | every candidate in the pool |
| `angular` | dict | see **2. Angular RMSD** |
| `planarity` | dict | see **3. Planarity RMS** |
| `prism_analysis` | dict | see **4. Prism vs antiprism twist** |
| `hull` | dict | see **5. Convex-hull overlay** |

---

## 1. Coordination number (`coordination_number`, `gap_info`)

**What it is:** the number of neighbouring fragments deemed "inside the first
shell".

**How it is computed** (`detect_coordination_number`):

1. Sort every candidate distance ascending → `sorted_distances`.
2. Compute successive differences → `gaps`.
3. `CN = argmax(gaps) + 1` (one past the biggest jump).

So `CN` is the cluster size that maximises the distance gap between "in the
shell" and "out of the shell" — no manual cutoff required.

**Output sub-dict `gap_info`:**

| Field | Type | Meaning |
| --- | --- | --- |
| `coordination_number` | int | the chosen CN |
| `gap_index` | int | position of the chosen gap in `gaps` |
| `gap_value` | float (Å) | size of the jump the chosen gap represents |
| `sorted_distances` | `list[float]` (Å) | every candidate in the pool, ascending |
| `gaps` | `list[float]` (Å) | first-differences of `sorted_distances` |

**Interpretation tips:**

- A large `gap_value` (> 0.3 Å) means a clean, textbook coordination shell.
- A small `gap_value` (< 0.1 Å) means the shell is fuzzy — e.g. the A-site in a
  distorted perovskite where several next-nearest anions sit just outside.
- If `len(pool_coords) < CN + 2` the algorithm has too few candidates; widen
  `cutoff` or the result is unreliable.

---

## 2. Shape classification (`shape.*`)

**What it is:** a CShM (Continuous Shape Measure)-based classification of the
shell against `molcrys_kit`'s registry of ideal polyhedra, with optional
core-residual decomposition for shells that are *almost* a smaller registered
polyhedron with one or two extra atoms (face caps, vertex extensions, edge
bridges).

**How it is computed** (`molcrys_kit.analysis.shape.classify_shell`):

1. Project the shell onto the unit sphere (translate by `center`, normalise).
2. For `k = 0, 1` residual atoms (a cheap superset of the rigid CShM
   classifier), enumerate the `C(CN, k)` choices of which atoms to peel
   off and which `CN-k` core polyhedra in the registry to fit.
3. Fit each candidate via CShM — alternating Hungarian assignment and
   Kabsch rotation steps over multiple random initial rotations — and
   classify any peeled residual atom by its position in the ideal frame
   (`face_cap`, `off_axis_cap`, `edge_bridge`, `vertex_extension`,
   `interstitial`, `floating`).
4. Pick the candidate with the lowest combined CShM + role penalty score;
   that becomes `primary_label` (e.g. `"cuboctahedron"`,
   `"tetrahedron"`, `"capped_square_antiprism"`).
5. Tag the result with `label_modifier ∈ {"clean", "distorted",
   "ambiguous", "irregular"}` based on the absolute CShM value
   (clean < 0.5, distorted < 3.0) and the gap to the second-best
   candidate.

**Library** (CN coverage as of `molcrys_kit ≥ 0.4`):

| CN | Ideal shapes |
| --- | --- |
| 4 | tetrahedron · square_planar |
| 5 | trigonal_bipyramid · square_pyramid |
| 6 | octahedron · trigonal_prism |
| 7 | pentagonal_bipyramid · capped_octahedron |
| 8 | cube · square_antiprism · dodecahedron |
| 9 | capped_square_antiprism · tricapped_trigonal_prism |
| 10 | bicapped_square_antiprism · bicapped_dodecahedron · sphenocorona · gyrobifastigium |
| 11 | capped_pentagonal_antiprism · tricapped_cube · etc. |
| 12 | icosahedron · cuboctahedron |

If the CN is outside the registry, `primary_label is None`,
`candidates == []`, and `cshm_value is None`.

**Output sub-dict `shape`:**

| Field | Type | Meaning |
| --- | --- | --- |
| `coordination_number` | int | CN actually classified |
| `primary_label` | str \| None | best polyhedron name (e.g. `"cuboctahedron"`) |
| `label_modifier` | str \| None | `"clean"` / `"distorted"` / `"ambiguous"` / `"irregular"` |
| `cshm_value` | float \| None | combined CShM + role-penalty score (lower is better) |
| `confidence_gap` | float \| None | score gap between best and second-best candidate |
| `core` | dict \| None | `{prototype, cn, indices, cshm, quality, topology}` |
| `residuals` | `list[dict]` | per-residual `{index, role, confidence, ...}` records |
| `structural_description` | str | one-line human summary |
| `candidates` | `list[{name, cshm, ...}]` | top-K alternatives, sorted ascending |
| `best_match` | dict \| None | `candidates[0]` (kept for back-compat with v1 callers) |

**Interpretation tips:**

- `cshm_value < 0.5` — essentially the ideal polyhedron (`label_modifier = "clean"`).
- `0.5 ≤ cshm_value < 3.0` — clearly distorted but still in the same family
  (`label_modifier = "distorted"`).
- `cshm_value ≥ 3.0` and a small `confidence_gap` — `label_modifier = "ambiguous"`;
  inspect `candidates` for the runners-up.
- `residuals` non-empty means the classifier had to peel one atom off the
  shell to fit a smaller core polyhedron; the `role` field tells you whether
  that atom is sitting over a face, off-axis, on an edge, etc.

---

## 3. Planarity RMS (`planarity.*`)

**What it is:** the lowest out-of-plane RMS displacement you can achieve by
picking any 5-neighbour subset of the shell, in Å.

**How it is computed** (`planarity_analysis`):

1. For every combination of `group_size` (default **5**) shell atoms:
   - Centre the subset at its own centroid.
   - Fit the best plane via SVD (the last right-singular vector is the normal).
   - Record the RMS distance from the subset to that plane.
2. Keep the smallest RMS and the atom indices that produced it.

**Output sub-dict `planarity`:**

| Field | Type | Meaning |
| --- | --- | --- |
| `best_rms` | float \| None (Å) | best RMS out-of-plane displacement |
| `best_indices` | `list[int]` | which shell positions form that plane |
| `group_size` | int | number of atoms per plane (5 by default) |

**Interpretation tips:**

- A five-atom plane with `best_rms` ≲ 0.05 Å is effectively flat — a signal
  that the coordination shell contains a pentagonal face (icosahedron,
  bicapped square antiprism, etc.).
- `best_indices` can be fed back into the renderer to colour-highlight that
  face.
- The routine returns `None` if the shell has fewer than `group_size` members.

---

## 4. Prism / antiprism twist (`prism_analysis.*`)

**What it is:** a quick check for whether a CN ≥ 10 polyhedron is better
described as a **prism** (stacked faces) or **antiprism** (rotated faces).

**How it is computed** (`detect_prism_vs_antiprism`):

1. Require `len(shell_coords) >= 10`; otherwise return `None` for both fields.
2. Sort shell atoms by `z`, take the bottom 5 and top 5 as two pentagonal
   rings.
3. For each paired atom compute the azimuthal rotation `Δφ` (mod ±180°).
4. Take the **average absolute twist** — this is `twist_deg`.
5. `classification = "antiprism" if twist_deg > 18° else "prism"`.

**Output sub-dict `prism_analysis`:**

| Field | Type | Meaning |
| --- | --- | --- |
| `classification` | `"prism"` \| `"antiprism"` \| None | verdict based on the 18° threshold |
| `twist_deg` | float \| None | average inter-ring twist in degrees |

**Interpretation tips:**

- The 18° threshold sits halfway between an ideal pentagonal prism (0°) and
  an ideal pentagonal antiprism (36°).
- Use this alongside `shape.primary_label` — a `"bicapped_square_antiprism"`
  reported as a prism (twist < 18°) is probably a mis-assignment.
- For CN < 10 the twist is undefined and `classification` is `None`.

---

## 5. Convex-hull overlay (`hull.*`)

Not strictly a *score* but a very useful geometric summary for visualisation:

| Field | Type | Meaning |
| --- | --- | --- |
| `vertices` | `list[list[float]]` | Cartesian coordinates of every shell atom |
| `simplices` | `list[list[int]]` | triangle indices of the convex hull |
| `edges` | `list[list[int]]` | unique undirected edges of the hull |

The renderer draws `hull.simplices` as a translucent mesh and `hull.edges` as
a thick line trace to produce the purple polyhedra in the README screenshots.
If `scipy.spatial.ConvexHull` is unavailable the hull gracefully falls back
to `{vertices: [...], simplices: [], edges: []}` and only the vertices are
shown.

---

## Example output (DAP-4, first A-site cation)

`python scripts/02_coordination_analysis.py` writes
`scripts/_outputs/02_coordination_summary.json` with an abridged version of
the dict:

```json
{
  "structure": "DAP-4",
  "center_label": "A0",
  "center_type": "A",
  "cutoff_A": 8.0,
  "neighbor_pool_size": 12,
  "coordination_number": 9,
  "gap_value_A": 0.124,
  "shell_distances_A": [4.98, 4.98, 4.98, 5.10, 5.10, 5.10, 5.10, 5.10, 5.10],
  "shape": {
    "primary_label": "tricapped_trigonal_prism",
    "label_modifier": "distorted",
    "cshm_value": 0.83,
    "candidates": [
      {"name": "tricapped_trigonal_prism", "cshm": 0.83},
      {"name": "capped_square_antiprism",  "cshm": 1.42}
    ]
  },
  "planarity":      {"best_rms_A": 0.093, "best_indices": [1, 4, 5, 6, 7], "group_size": 5},
  "prism_analysis": {"classification": null, "twist_deg": null}
}
```

**What this says about DAP-4:**

- The diaminopropane A-cation sits in a **9-coordinate pocket** of perchlorate
  anions.
- Three anions form a tight inner shell at 4.98 Å, six more at 5.10 Å, and
  the pool drops off ~0.12 Å later (`gap_value`) — a clean shell.
- Of the two CN = 9 ideals, the geometry is closest to a
  **tricapped trigonal prism** (15° RMSD — visibly distorted but recognisable).
- A sub-set of 5 neighbours forms an almost perfect plane
  (`planarity.best_rms_A ≈ 0.09 Å`) — the capping triangle of the prism.
- No prism/antiprism verdict is emitted because the twist-test requires CN ≥ 10.

Run example 02 to regenerate the full JSON with the raw gaps, hull edges,
planarity indices and every pool distance included.
