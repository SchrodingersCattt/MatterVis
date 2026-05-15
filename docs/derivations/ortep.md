# ORTEP Geometry Derivations

ORTEP rendering converts atomic displacement parameters into probability
ellipsoids.  MatterVis has two rendering languages for the same mathematics:
3D ellipsoid meshes and camera-facing 2D billboard ellipses.

## Derivation

### ADP Tensor

Let the anisotropic displacement tensor be

\[
U=U^\top\succeq0.
\]

If no tensor is available, MatterVis uses an isotropic fallback

\[
U = U_\mathrm{iso} I.
\]

The displacement distribution is a centered Gaussian with covariance \(U\).
The probability ellipsoid containing probability \(p\) is

\[
\vec y^\top U^{-1}\vec y \le k^2.
\]

For three dimensions,

\[
k_{3D}(p)=\sqrt{\chi^2_3{}^{-1}(p)}.
\]

For the default \(p=0.5\),

\[
k_{3D}(0.5)=\sqrt{2.3659738843753377}.
\]

### Principal Axes

Diagonalize

\[
U = V\Lambda V^\top,
\qquad
\Lambda=\operatorname{diag}(\lambda_1,\lambda_2,\lambda_3),
\]

with eigenvalues sorted from largest to smallest.  The semi-axis lengths are

\[
\vec L = k_{3D}(p)
\left(
\sqrt{\lambda_1},
\sqrt{\lambda_2},
\sqrt{\lambda_3}
\right).
\]

A point on the ellipsoid is generated from a unit sphere vector \(\vec s\):

\[
\vec x = \vec c + V(\vec L\odot\vec s).
\]

### Mesh Parameterization

Use spherical parameters

\[
\vec s(\theta,\phi)=
(\sin\theta\cos\phi,\ \sin\theta\sin\phi,\ \cos\theta).
\]

The north pole is \(\theta=0\), the south pole is \(\theta=\pi\), and
intermediate latitude rings use

\[
\theta_i=\frac{\pi i}{N_\theta},
\qquad
\phi_j=\frac{2\pi j}{N_\phi}.
\]

Triangles connect the north cap, adjacent latitude rings, and south cap.

### 2D Billboard Projection

For flat ORTEP billboards, project the 3D covariance into the screen plane.
Let

\[
P=
\begin{bmatrix}
\hat v_x\\
\hat v_y
\end{bmatrix}
\]

where \(\hat v_x\) and \(\hat v_y\) are the scene's screen-right and screen-up
unit vectors.  The projected covariance is

\[
U_2 = PUP^\top.
\]

For a 2D Gaussian, the probability inside radius \(r\) satisfies

\[
p = 1-\exp(-r^2/2),
\]

so

\[
k_{2D}(p)=\sqrt{-2\ln(1-p)}.
\]

Diagonalize

\[
U_2 = W\operatorname{diag}(\mu_1,\mu_2)W^\top.
\]

The billboard semi-axis lengths are

\[
A_i=k_{2D}(p)\sqrt{\mu_i}.
\]

If \(W_{:,0}\) and \(W_{:,1}\) are 2D screen-plane directions, convert them back
to 3D screen-plane vectors:

\[
\vec e_0=W_{0,0}\hat v_x+W_{1,0}\hat v_y,
\qquad
\vec e_1=W_{0,1}\hat v_x+W_{1,1}\hat v_y.
\]

The billboard polygon is

\[
\vec x(t)=
\vec c
+ A_0\cos t\,\vec e_0
+ A_1\sin t\,\vec e_1.
\]

### Principal-Axis Segments

The three principal-axis outline segments are

\[
[\vec c - L_i V_{:,i},\ \vec c + L_i V_{:,i}]
\quad i=1,2,3.
\]

### Octant Shading

For each sign triple

\[
\vec s=(s_x,s_y,s_z),\qquad s_i\in\{-1,1\},
\]

define

\[
\vec d=V(\vec L\odot\vec s).
\]

The octant center is

\[
\vec c_\mathrm{oct}=\vec c+\frac12\vec d.
\]

An octant is front-lit when

\[
\vec d\cdot\hat v_\mathrm{view}\ge0.
\]

### Visual Uiso Clamp

Some CIFs encode disorder by inflating Uiso.  MatterVis clamps only the visual
ellipsoid size, not the underlying atom data.

For isotropic atoms:

\[
U_\mathrm{iso,render}=\min(U_\mathrm{iso},U_\mathrm{cap}).
\]

For anisotropic atoms, let \(\lambda_\max\) be the largest eigenvalue of
\((U+U^\top)/2\).  If \(\lambda_\max>U_\mathrm{cap}\), scale the full tensor:

\[
U_\mathrm{render} =
U\frac{U_\mathrm{cap}}{\lambda_\max}.
\]

This preserves eigenvectors and ellipsoid shape while reducing all radii by

\[
\sqrt{\frac{U_\mathrm{cap}}{\lambda_\max}}.
\]

## Current Code Mapping

Constants and probability scaling:

- `crystal_viewer/ortep.py:12-15` defines the 50% chi-square constants and
  default Uiso values.
- `crystal_viewer/ortep.py:40-52` implements `_probability_scale`: exact 2D
  formula, exact 3D default for 50%, and Wilson-Hilferty approximation for
  other 3D probabilities.
- `crystal_viewer/ortep.py:55-72` implements the normal quantile approximation
  used by Wilson-Hilferty.

Tensor validation and axes:

- `crystal_viewer/ortep.py:75-87` validates or builds the \(U\) matrix:
  3x3 shape, symmetry, positive semidefinite eigenvalues, and symmetrization.
- `crystal_viewer/ortep.py:90-97` diagonalizes \(U\), sorts eigenvalues
  descending, clips tiny negative values, and computes semi-axis lengths.

3D mesh:

- `crystal_viewer/ortep.py:100-133` generates vertices from spherical
  lat/lon samples and returns triangle indices.

2D billboard:

- `crystal_viewer/ortep.py:136-155` forms
  `P = np.array([view_x, view_y])`, computes `U2 = P @ mat @ P.T`,
  diagonalizes it, scales by the 2D probability radius, maps 2D eigenvectors
  back through `view_x` / `view_y`, and emits a polygon.

Principal axes and octants:

- `crystal_viewer/ortep.py:158-161` emits the three principal-axis segments.
- `crystal_viewer/ortep.py:164-175` evaluates eight sign triples and marks
  an octant lit if its direction dots positively with the view direction.

Visual clamp:

- `crystal_viewer/ortep.py:26-37` defines per-element and default visual Uiso
  caps.
- `crystal_viewer/ortep.py:192-213` clamps isotropic Uiso directly and scales
  anisotropic \(U\) by `cap / max_eig`.
- `crystal_viewer/ortep.py:216-220` chooses fallback Uiso values and applies
  the clamp for each atom.

Renderer integration:

- `crystal_viewer/ortep.py:293-347` starts ORTEP mesh batching by color and
  opacity, using `ortep_probability` from style.
- The API contract for caller-facing ORTEP behavior is summarized separately in
  `agents/ortep_api.md`.

## Audit Notes

The 3D and 2D probability scales are different because the dimensionality of
the Gaussian changes after projection.  Reusing the 3D chi-square radius for a
2D billboard would make projected ellipses too large.

The visual Uiso clamp is a rendering policy, not crystallography.  It should
remain visibly documented anywhere ORTEP size is discussed so users do not
mistake clamped visuals for modified CIF data.

The billboard projection uses `scene["view_x"]` / `scene["view_y"]`, not the
live Plotly camera.  That is appropriate for static flat ORTEP traces generated
from the scene view frame, but it is a different projection authority from the
interactive camera compass in `camera.md`.  A redesign should make projection
authority explicit for every rendered object.

## Invariants

- \(U\) must be symmetric positive semidefinite before rendering.
- 3D ORTEP radii use \(k_{3D}(p)=\sqrt{\chi^2_3{}^{-1}(p)}\).
- 2D billboard radii use \(k_{2D}(p)=\sqrt{-2\ln(1-p)}\).
- The default 50% 3D radius uses the exact stored chi-square constant.
- Anisotropic visual clamping scales the entire tensor, preserving principal
  directions and ellipsoid shape.
- Visual clamping must not mutate the atom's source ADP data.
- Mesh and billboard ORTEP renderings are two projections of the same \(U\)
  semantics; style choices should not change the underlying probability model.

