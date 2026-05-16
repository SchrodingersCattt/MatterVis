# MatterVis Geometry Derivations

This directory is the geometry contract for MatterVis.  It is intentionally
separate from the public API notes in `agents/`: those files describe what
callers may rely on, while these notes derive the coordinate, display, camera,
transform, and ORTEP formulae that the implementation must obey.

Each document has the same structure:

1. **Derivation**: the formula independent of any particular Python function.
2. **Current code mapping**: where the present implementation encodes that formula.
3. **Audit notes**: places where the code is coupled, duplicated, or known to deviate.
4. **Invariants**: constraints future redesigns must keep true.

## Documents

- `lattice.md`: row-vector lattice convention, fractional and Cartesian
  coordinates, metric tensors, reciprocal lattice, and cell aspect ratios.
- `display_modes.md`: how `asymmetric_unit`, `formula_unit`, `cluster`, and
  `unit_cell` choose atoms, complete boundary images, and choose bond endpoints.
- `transforms.md`: repeat, grow, bond-walk, fragment completion, polyhedron
  completion, symmetry, and slab transforms.
- `camera.md`: Plotly scene-cube normalization, axis ranges, aspect ratio,
  camera basis, compass projection, and the two known display bugs.
- `ortep.md`: ADP tensor probability ellipsoids, 2D billboards, mesh generation,
  and visual Uiso clamping.

## Global Symbols

MatterVis stores all live lattice matrices as row-vector matrices:

\[
M =
\begin{bmatrix}
\vec a \\
\vec b \\
\vec c
\end{bmatrix}
\in \mathbb{R}^{3\times 3}.
\]

Fractional coordinates are row vectors.  The conversion is

\[
\vec x_\mathrm{cart} = \vec x_\mathrm{frac} M,
\qquad
\vec x_\mathrm{frac} = \vec x_\mathrm{cart} M^{-1}.
\]

This is the same convention used by ASE, pymatgen, and MolCrysKit.  The legacy
parser still returns the old column-vector matrix; MatterVis converts it once
at the loader boundary with `M = legacy_M.T` in `crystal_viewer/scene.py`.
Any later `M.T` should be treated as an explicit bridge back into legacy code,
not as a second convention.

The view frame is written

\[
R =
\begin{bmatrix}
\hat v_x \\
\hat v_y \\
\hat v_z
\end{bmatrix},
\]

where `scene["view_x"] = \hat v_x` points screen-right,
`scene["view_y"] = \hat v_y` points screen-up, and
`scene["view_z"] = \hat v_z` is the depth direction used for sorting and label
placement.  The frame is produced by the legacy view helper and stored on the
scene in `crystal_viewer/scene.py:503-505`.

Plotly cameras are written as

\[
(\vec e, \vec c, \vec u)
\]

for `eye`, `center`, and `up`.  The normalized view direction is

\[
\hat v = \frac{\vec c - \vec e}{\lVert \vec c - \vec e\rVert}.
\]

The screen basis used by the compass is

\[
\hat r = \frac{\hat v \times \vec u}{\lVert \hat v \times \vec u\rVert},
\qquad
\hat s = \hat r \times \hat v.
\]

This is implemented by `camera_screen_basis` in
`crystal_viewer/compass.py:46-87`.

For Plotly axis ranges, let

\[
h_k = \frac{r_k^\max - r_k^\min}{2}
\]

be the half-span of data axis \(k\).  If Plotly renders the axis with manual
aspect component \(a_k\), then one rendered cube unit corresponds to

\[
\sigma_k = \frac{h_k}{a_k}
\]

data units along that axis.  In `aspectmode="cube"` after range equalization,
all axes share the same rendered length, so the effective scale is simply the
equalized half-span \(h_k\).  This scale vector \(\vec\sigma\) is the key link
between axis ranges, camera reuse, and compass projection.

For ORTEP probability ellipsoids, the anisotropic displacement tensor is

\[
U = U^\top \succeq 0.
\]

The 3D probability radius is

\[
k_{3D}(p)=\sqrt{\chi^2_3{}^{-1}(p)},
\]

and the 2D projected Gaussian radius is

\[
k_{2D}(p)=\sqrt{-2\ln(1-p)}.
\]

## Boundary Rule

There must be exactly one row/column convention conversion at any boundary.
The normal MatterVis path is row-vector `M`.  Exceptions must be narrow and
named:

- `crystal_viewer/scene.py` converts the static-publication parser output into row
  vectors.
- `crystal_viewer/scene.py:156` passes `M.T` back into a legacy helper that
  still expects column vectors.

Any new geometry code should take row-vector `M`, fractional row vectors, and
Cartesian row vectors.  If a helper requires another convention, adapt at the
call site and document the conversion.

