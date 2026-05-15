# Camera, Viewport, And Projection Derivations

This is the critical geometry note for the current display bugs.  Plotly 3D
does not render data coordinates directly: it first maps every data axis into a
normalized scene cube, then interprets `scene.camera` inside that normalized
space.  Therefore axis ranges, aspect mode, camera reuse, and compass
projection are one mathematical system.

## Derivation

### Plotly Scene-Cube Model

Let the data axis range for coordinate \(k\in\{x,y,z\}\) be

\[
[r_k^\min,r_k^\max],
\qquad
\mu_k=\frac{r_k^\min+r_k^\max}{2},
\qquad
h_k=\frac{r_k^\max-r_k^\min}{2}.
\]

Plotly maps a data coordinate \(\vec x\) to scene-cube coordinates
\(\vec q\).  Up to a global constant that does not affect directions, the
mapping is

\[
q_k = \frac{x_k-\mu_k}{\sigma_k}.
\]

The scale \(\sigma_k\) is the data-units-per-rendered-cube-unit value.

For manual aspect ratio with aspect component \(a_k\),

\[
\sigma_k=\frac{h_k}{a_k}.
\]

For `aspectmode="cube"` after MatterVis equalizes all axis ranges, the rendered
cube is isotropic and

\[
\sigma_k=h_k.
\]

The reverse map is

\[
x_k=\mu_k+\sigma_kq_k.
\]

This means Plotly camera vectors live in cube space, not raw data space.  A
camera vector \(\vec d_q\) corresponds to a data-space direction

\[
\vec d_x = \vec d_q\odot\vec\sigma,
\]

where \(\odot\) is elementwise multiplication.

### Manual Cell Aspect

For unit-cell display, the desired Plotly aspect component is the normalized
lattice-axis length:

\[
a_k =
\frac{\lVert M_{k,:}\rVert}
{\max_j\lVert M_{j,:}\rVert}.
\]

Then a data-space vector \(\vec v_x\) must be converted to cube space before
camera-plane projection:

\[
\vec v_q = \vec v_x \oslash \vec\sigma,
\qquad
\sigma_k=\frac{h_k}{a_k}.
\]

For non-unit-cell displays, MatterVis chooses content-first rendering:
axis ranges are equalized and Plotly uses `aspectmode="cube"`.  The content
retains a 1:1:1 data aspect in the viewport, while long or skewed unit-cell
wireframes may be clipped or leave whitespace depending on the range policy.

### Axis Ranges

Start with the visible atom hull:

\[
\vec m_\mathrm{atom}
= \min_i(\vec x_i-\rho_i),
\qquad
\vec M_\mathrm{atom}
= \max_i(\vec x_i+\rho_i),
\]

where \(\rho_i\) is the atom radius after `atom_scale`.

Additional points may expand the hull:

- focus topology center and shell points;
- unit-cell corners \(0,\vec a,\vec b,\vec c,\vec a+\vec b,\vec a+\vec c,
  \vec b+\vec c,\vec a+\vec b+\vec c\);
- extra topology overlays.

The current policy is mode-dependent:

\[
\text{cell corners included}
\iff
\text{display\_mode}=\texttt{unit\_cell}
\land
\texttt{show\_unit\_cell}.
\]

After the min/max hull is formed, MatterVis pads it:

\[
\mathrm{span}_k=\max(M_k-m_k,0.8),
\qquad
\mathrm{pad}_k=\max(0.06\,\mathrm{span}_k,0.25),
\]

and emits

\[
[r_k^\min,r_k^\max]=[m_k-\mathrm{pad}_k,\ M_k+\mathrm{pad}_k].
\]

For non-manual modes, `_equalize_axis_ranges` sets every axis span to the
largest span while preserving each axis midpoint:

\[
L = \max_k(r_k^\max-r_k^\min),
\qquad
[r_k^\min,r_k^\max]\leftarrow[\mu_k-L/2,\mu_k+L/2].
\]

### Camera Screen Basis

Given camera eye \(\vec e\), center \(\vec c\), and up vector \(\vec u\):

\[
\hat v=\frac{\vec c-\vec e}{\lVert\vec c-\vec e\rVert},
\]

\[
\hat r=
\frac{\hat v\times\vec u}{\lVert\hat v\times\vec u\rVert},
\qquad
\hat s=\hat r\times\hat v.
\]

The vectors \(\hat r\) and \(\hat s\) are orthonormal and lie in the screen
plane because both are perpendicular to \(\hat v\), and \(\hat s\) is the
Gram-Schmidt correction of the supplied up vector into the plane.

Any cube-space vector \(\vec v_q\) projects to screen components

\[
(\Delta_x,\Delta_y)=
(\vec v_q\cdot\hat r,\ \vec v_q\cdot\hat s).
\]

### Compass Paper Coordinates

Let a compass arrow have projected screen delta \((\Delta_x,\Delta_y)\).
Normalize the longest arrow to `pixel_length`:

\[
s_\mathrm{px} =
\frac{L_\mathrm{px}}
{\max_i\sqrt{\Delta_{x,i}^2+\Delta_{y,i}^2}}.
\]

For one arrow:

\[
dx_\mathrm{px}=s_\mathrm{px}\Delta_x,
\qquad
dy_\mathrm{px}=s_\mathrm{px}\Delta_y.
\]

With figure size \((W,H)\) and paper-coordinate anchor \((x_0,y_0)\), the
arrow head is

\[
x_\mathrm{tip}=x_0+\frac{dx_\mathrm{px}}{W},
\qquad
y_\mathrm{tip}=y_0+\frac{dy_\mathrm{px}}{H}.
\]

Plotly annotation tails use `axref="pixel"` / `ayref="pixel"` and pixel y grows
downward, so the tail offset is

\[
ax=-dx_\mathrm{px},\qquad ay=dy_\mathrm{px}.
\]

### Camera Remapping Across Aspect Changes

Suppose a rebuild changes the cube scale from \(\vec\sigma^{(o)}\) to
\(\vec\sigma^{(n)}\).  To preserve the same data-space view direction, do not
reuse the old cube vector unchanged.  If

\[
\vec d_q^{(o)}=\vec e_q^{(o)}-\vec c_q^{(o)},
\]

then the old data-space direction is

\[
\vec d_x=\vec d_q^{(o)}\odot\vec\sigma^{(o)}.
\]

The new cube-space direction that represents the same data direction is

\[
\vec d_q^{(n)}
=
\vec d_x\oslash\vec\sigma^{(n)}
=
\vec d_q^{(o)}
\odot
\left(\vec\sigma^{(o)}\oslash\vec\sigma^{(n)}\right).
\]

Thus

\[
\vec e_q^{(n)}
=
\vec c_q^{(n)}+\vec d_q^{(n)}.
\]

The camera `up` vector must be remapped by the same elementwise scale and then
renormalized:

\[
\vec u_q^{(n)}
=
\operatorname{normalize}
\left(
\vec u_q^{(o)}
\odot
\left(\vec\sigma^{(o)}\oslash\vec\sigma^{(n)}\right)
\right).
\]

If MatterVis does not remap, it must explicitly reset the camera when the cube
scale changes.

## Current Code Mapping

Default camera creation:

- `crystal_viewer/renderer_viewport.py:19-28` creates a Plotly camera from
  `scene["view_direction"]`, `scene["up"]`, and `camera_eye_distance`.

Cell aspect:

- `crystal_viewer/renderer_viewport.py:31-40` implements the normalized
  lattice-length aspect formula.
- `crystal_viewer/renderer_viewport.py:43-55` gates manual cell aspect to
  `display_mode == "unit_cell"`.

Cube scale:

- `crystal_viewer/renderer_viewport.py:58-83` computes manual scale
  \(\sigma_k=h_k/a_k\).
- `crystal_viewer/renderer_viewport.py:137-170` falls back to viewport or
  bounds-derived scale for non-manual scenes.
- `crystal_viewer/renderer_viewport.py:129-133` divides lattice axes by this
  cube scale before projecting compass axes.

Ranges and equalization:

- `crystal_viewer/renderer_viewport.py:180-274` computes atom/topology/cell
  axis ranges.
- `crystal_viewer/renderer_viewport.py:219-239` includes unit-cell corners only
  when `cell_owns_cube` is true.
- `crystal_viewer/renderer_viewport.py:240-252` includes focus topology in all
  modes but `extra_overlays` only when the cell owns the cube.
- `crystal_viewer/renderer_viewport.py:277-304` equalizes axis ranges to the
  longest span.
- `crystal_viewer/renderer_viewport.py:307-331` writes the final Plotly
  `layout.scene`: axis ranges, camera, `uirevision`, background, and either
  manual aspect or cube aspect.

Figure assembly:

- `crystal_viewer/renderer.py:116-118` calls `_scene_ranges` before building
  traces.
- `crystal_viewer/renderer.py:201` installs `figure_axis_layout`.
- `crystal_viewer/renderer_scene_traces.py:1623-1656` draws the full unit-cell
  box from the eight lattice corners whenever `scene["M"]` exists; visibility
  is controlled later by style.

Camera persistence and overwrite:

- `crystal_viewer/dash_app_impl.py:4099-4122` excludes `camera` from the figure
  cache key.
- `crystal_viewer/dash_app_impl.py:4145-4147` applies the live camera on a
  cached figure.
- `crystal_viewer/dash_app_impl.py:4171-4174` builds a fresh figure and then
  overwrites `scene_camera` with `state["camera"]`.

Compass:

- `crystal_viewer/compass.py:46-87` implements the screen basis.
- `crystal_viewer/compass.py:90-102` projects 3D vectors onto that basis.
- `crystal_viewer/compass.py:171-190` normalizes arrows to pixels and flips the
  tail sign for Plotly pixel-y semantics.
- `crystal_viewer/renderer_compass.py:74-110` caps baked compass arrows so they
  stay inside the figure edge.
- `crystal_viewer/renderer_compass.py:155-168` emits the same paper/pixel
  arrow structure as the lower-level compass helper.

## Audit Notes

### Bug 1: ASU / Formula Cell Box Is Incomplete

The unit-cell wireframe and the viewport use different ownership rules.

The wireframe always draws the full lattice parallelepiped from

\[
\{0,\vec a,\vec b,\vec c,\vec a+\vec b,\vec a+\vec c,\vec b+\vec c,
\vec a+\vec b+\vec c\}.
\]

That is implemented in `crystal_viewer/renderer_scene_traces.py:1626-1647`.

But `_scene_ranges` includes those eight corners only when

\[
\text{display\_mode}=\texttt{unit\_cell}.
\]

That condition is implemented in `crystal_viewer/renderer_viewport.py:219-239`.
For `formula_unit`, `asymmetric_unit`, and `cluster`, the atom hull owns the
scene cube even if `show_unit_cell=True`.

Therefore, if a lattice corner \(C\) satisfies

\[
|C_k-\mu_k| > L/2
\]

for any equalized axis center \(\mu_k\) and half-span \(L/2\), Plotly clips the
wireframe.  The cell matrix is still correct; the range contract clips it.

The minimal formula-level repair is:

\[
\text{show\_unit\_cell}\Rightarrow
\text{include eight cell corners in range extras},
\]

for every display mode.  The separate `extra_overlays` topology expansion can
remain gated to `unit_cell` so molecule-focused displays are not dwarfed by
polyhedron replicas.

This deliberately changes the old aesthetic rule "molecule wins, box loses".
Tests that assert formula-unit boxes may be clipped should be rewritten to
assert the new invariant: enabling the box may enlarge ranges to include the
box, but topology `extra_overlays` still do not enlarge non-unit-cell views.

### Bug 2: Display Changes Can Squish The View

When display mode changes, the figure is rebuilt with a new range/aspect
normalization.  The code then reapplies the stored camera cube vector without
remapping:

\[
\vec e_q^{(n)} := \vec e_q^{(o)}.
\]

But the same cube vector corresponds to the new data-space direction

\[
\vec d_x^{(n)}
=
\vec d_q^{(o)}\odot\vec\sigma^{(n)},
\]

not the old direction

\[
\vec d_x^{(o)}
=
\vec d_q^{(o)}\odot\vec\sigma^{(o)}.
\]

The apparent data-space view is therefore scaled componentwise by

\[
\vec\sigma^{(n)}\oslash\vec\sigma^{(o)}.
\]

That is a formula-level explanation for the perceived flattening or
non-uniform stretch after changing display settings.  It is not a second
`aspectratio` write.  The Python path has one layout writer
(`figure_axis_layout`) and then one camera overwrite
(`fig.update_layout(scene_camera=camera)`).

The implementation already knows how to scale lattice vectors into cube space
for the compass (`renderer_viewport.py:129-133`).  The missing piece is applying
the same old/new cube-scale reasoning to the camera itself when the viewport
signature changes.

### Duplicated Viewport Math

The active Dash path imports `_scene_ranges`, `_axis_cube_scale`, and
`uniform_viewport` from `renderer_viewport.py` after the star import from
`renderer_scene_traces.py`.  That makes `renderer_viewport.py` authoritative
for `build_figure`.  Any duplicate helper left in trace modules is drift risk
and should delegate to the authoritative implementation.

## Invariants

- Axis ranges, aspect mode, aspect ratio, and initial camera belong to one
  layout calculation: `figure_axis_layout`.
- A stored Plotly camera is a scene-cube camera.  It may be reused across a
  rebuild only if the cube scale is unchanged or if the camera is remapped by
  \(\vec\sigma^{(o)}\oslash\vec\sigma^{(n)}\).
- If camera remapping is not implemented, display changes that alter
  \(\vec\sigma\) must reset or revision-bump the camera.
- The compass must project lattice vectors after converting them with the same
  cube scale that the main scene uses.
- If `show_unit_cell=True`, the range policy must explicitly say whether the
  full cell owns the viewport.  Drawing a full cell while excluding its corners
  from the range is a clipping contract, not an accidental visual effect.
- Topology focus points and topology extra overlays are separate viewport
  concepts.  Fixing unit-cell clipping must not automatically let every overlay
  replica grow molecule-focused views.

