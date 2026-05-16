# Lattice And Coordinate Derivations

This note derives the lattice algebra used by MatterVis and maps it to the
current implementation.

## Derivation

MatterVis stores the lattice as a row-vector matrix:

\[
M =
\begin{bmatrix}
\vec a \\
\vec b \\
\vec c
\end{bmatrix}.
\]

For a fractional row vector \(\vec f=(u,v,w)\), the Cartesian coordinate is

\[
\vec x = \vec fM = u\vec a + v\vec b + w\vec c.
\]

The inverse map is

\[
\vec f = \vec xM^{-1}.
\]

The cell volume is the absolute determinant:

\[
V = |\det M|.
\]

Because fractional coordinates are row vectors, squared distances in fractional
coordinates use the row-vector metric

\[
G = MM^\top,
\qquad
\lVert \Delta\vec x\rVert^2
= \Delta\vec f\,G\,\Delta\vec f^\top.
\]

The reciprocal lattice row matrix whose rows are reciprocal basis vectors is

\[
M^* = 2\pi M^{-\top}.
\]

This satisfies

\[
M^*M^\top = 2\pi I.
\]

If a caller wants crystallographic reciprocal vectors without the \(2\pi\)
factor, use \(M^{-\top}\).

For cell aspect rendering, MatterVis only needs the displayed lengths of the
three row vectors:

\[
\ell_a = \lVert \vec a\rVert,\quad
\ell_b = \lVert \vec b\rVert,\quad
\ell_c = \lVert \vec c\rVert.
\]

The normalized Plotly manual aspect ratio is

\[
\vec a_\mathrm{plotly}
= \frac{(\ell_a,\ell_b,\ell_c)}{\max(\ell_a,\ell_b,\ell_c)}.
\]

This preserves relative lattice-axis lengths while keeping Plotly's largest
axis at aspect component 1.

## Current Code Mapping

The row-vector convention is established at the CIF boundary:

- `crystal_viewer/scene.py` receives `legacy_M` from
  `crystal_viewer/cif_parse.py` and immediately stores `M = legacy_M.T`.
- `crystal_viewer/molcrys_bridge.py:62-68` documents that the rows of `M` are
  the `a`, `b`, `c` vectors before creating the ASE `Atoms` object.
- `crystal_viewer/loader.py:625-637` passes `bundle.M` unchanged into
  `build_scene_from_atoms`, so scene construction receives row-vector `M`.

The normal fractional-to-Cartesian conversion is delegated to MolCrysKit:

- `crystal_viewer/transforms.py:202-205` computes an image translation by
  converting an integer fractional shift with `frac_to_cart(shift_frac, M)`.
- `crystal_viewer/transforms.py:315-317` adds the same integer image shift in
  fractional space when materializing periodic copies.
- `crystal_viewer/molcrys_bridge.py:327-340` translates a selected formula-unit
  molecule by `shift_cart = frac_to_cart(shift_frac, M)` and recomputes
  `atom["frac"] = cart_to_frac(atom["cart"], M)`.

The only explicit bridge back into the legacy column-vector convention is:

- `crystal_viewer/scene.py:156-158`, where `_continuous_components` constructs
  `legacy_M = M.T` for `formula_unit.assemble_component_p1`.

The lattice-length summary is implemented in `cell_aspect_ratio`:

- `crystal_viewer/render/viewport.py:31-40` reads `scene["M"]`, computes
  `lens = np.linalg.norm(M, axis=1)`, divides by `lens.max()`, and returns a
  `{"x", "y", "z"}` dict.

The unit-cell wireframe uses the rows directly:

- `crystal_viewer/render/traces_overlays.py` assigns
  `a = scene["M"][0]`, `b = scene["M"][1]`, `c = scene["M"][2]`, then enumerates
  the eight corners \(0,\vec a,\vec b,\vec c,\vec a+\vec b,\vec a+\vec c,
  \vec b+\vec c,\vec a+\vec b+\vec c\).

## Audit Notes

The row-vector convention is consistent on the main path.  The risk is not the
formula itself, but hidden re-entry into old helpers.  Any new use of `M.T`
outside a clearly named legacy adapter should be reviewed as a likely
convention leak.

`cell_aspect_ratio` uses only row-vector norms.  This correctly represents
lattice-vector lengths but not interaxial angles, and it is not the renderer's
anti-flattening rule.  Plotly scales Cartesian x/y/z axes, so the final
renderer aspect is derived from emitted Cartesian axis ranges in
`docs/derivations/camera.md`.  The actual cell geometry remains correct because
traces are emitted in Cartesian coordinates.

The reciprocal-lattice formula is not currently a first-class MatterVis API.
If it is introduced later, it should follow the row-matrix convention
\(M^*=2\pi M^{-\top}\) and not copy column-vector textbook snippets blindly.

## Invariants

- `M` in all live MatterVis scenes is a row-vector lattice matrix.
- Cartesian coordinates are always computed as `frac @ M`.
- Integer image shifts are fractional row vectors and translate by
  `frac_to_cart(shift, M)`.
- Conversion to legacy column-vector matrices is allowed only at explicit
  legacy boundaries.
- `cell_aspect_ratio(scene)` is a lattice-summary helper, not a geometry
  transform and not the final Plotly aspect contract. It must never rewrite
  atom coordinates or lattice vectors.

