from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .renderer_common import *
from .renderer_style import *
from .renderer_traces_atoms import *
from .renderer_traces_overlays import _minor_outline_traces
from .renderer_serialize import _round_coord_arrays
from .bond_groups import bond_groups_cache_key

def _hashable_selector(selector: dict | None) -> tuple:
    if not isinstance(selector, dict):
        return ()
    items = []
    for key in sorted(selector.keys()):
        value = selector[key]
        if isinstance(value, list):
            items.append((key, tuple(value)))
        else:
            items.append((key, value))
    return tuple(items)


def _atom_groups_cache_key(atom_groups: list[dict] | None) -> tuple:
    """Hashable summary of atom_groups for the figure JSON cache.

    All nested lists are flattened to tuples so the resulting key
    survives use as a dict key (Python lists are unhashable).
    """
    if not atom_groups:
        return ()
    return tuple(
        (
            str(g.get("id", "")),
            _hashable_selector(g.get("selector")),
            str(g.get("color") or ""),
            str(g.get("color_light") or ""),
            bool(g.get("visible", True)),
            str(g.get("material") or ""),
            str(g.get("style") or ""),
        )
        for g in atom_groups
    )


def _atom_subscene(scene: dict, sub_atoms: list[dict]) -> dict:
    """Shallow copy of ``scene`` with ``draw_atoms`` replaced. Other
    fields (``bonds``, ``M``, ``label_items``, ...) keep the original
    references; the per-partition atom builders only ever read
    ``draw_atoms`` so this is safe."""
    sub = dict(scene)
    sub["draw_atoms"] = sub_atoms
    sub.pop("_mesh_trace_cache", None)
    return sub


def _atom_traces_for_partition(
    sub_scene: dict,
    sub_style: dict,
    *,
    use_fast: bool,
):
    """Pick the right atom-trace builder for ``(material, style)`` and
    run it on ``sub_scene``. Used both by the default scene-style
    partition and by per-group material/style overrides."""
    sty = str(sub_style.get("style", "ball_stick"))
    if sty == "wireframe":
        return _wireframe_atom_traces(sub_scene, sub_style)
    if sty == "ortep":
        from .ortep import (
            ortep_atom_billboard_traces,
            ortep_atom_mesh_traces,
            ortep_atom_fill_traces,
        )

        is_open_ortep = (
            bool(sub_style.get("ortep_silhouette_outline", False))
            and bool(sub_style.get("ortep_octant_hatching", False))
        )
        if is_open_ortep:
            return ortep_atom_fill_traces(sub_scene, sub_style)
        return (
            ortep_atom_billboard_traces(sub_scene, sub_style)
            if use_fast
            else ortep_atom_mesh_traces(sub_scene, sub_style)
        )
    if use_fast or sub_style.get("material") == "flat":
        return _atom_scatter_traces(sub_scene, sub_style)
    return _atom_mesh_traces(sub_scene, sub_style)


def _cached_atom_bond_meshes(scene: dict, style: dict, *, use_fast: bool):
    """Cache atom + bond mesh trace dicts on the scene. Building Mesh3d
    objects (sphere tessellation + Plotly array validation) is by far the
    dominant cost when the user toggles a cosmetic checkbox like Labels
    or Axes -- but the vertex arrays themselves only depend on positions,
    `atom_scale`, `bond_radius`, `minor_opacity`, `minor_bond_scale` and
    the fast-rendering switch. Cache the list of trace dicts under that
    key and replay them on subsequent rebuilds, so toggling Labels no
    longer regenerates ~1500 sphere triangles."""
    # ``show_minor_only`` and opacity controls are now trace-property edits:
    # build the full geometry once, then hide/restyle major/minor traces on
    # replay. Keep this local so the caller's style still reflects the UI.
    style = dict(style)
    style["show_minor_only"] = False
    atom_groups = style.get("atom_groups") or []
    bond_groups = style.get("bond_groups") or []
    cache = scene.setdefault("_mesh_trace_cache", {})
    key = (
        bool(use_fast),
        str(style.get("material", "mesh")),
        str(style.get("style", "ball_stick")),
        str(style.get("disorder", "outline_rings")),
        str(style.get("ortep_mode", "")),
        str(style.get("ortep_mode_minor", "")),
        round(float(style.get("ortep_probability", 0.5)), 3),
        bool(style.get("minor_wireframe", False)),
        bool(style.get("monochrome", False)),
        round(float(style.get("atom_scale", 1.0)), 3),
        round(float(style.get("bond_radius", 0.1)), 3),
        round(float(style.get("minor_bond_scale", 0.6)), 3),
        round(float(style.get("major_opacity", 1.0)), 3),
        bool(style.get("force_minor_fade", False)),
        bool(style.get("ortep_atom_fill", False)),
        bool(style.get("ortep_silhouette_outline", False)),
        bool(style.get("ortep_octant_hatching", False)),
        str(style.get("force_bond_color", "")),
        str(style.get("ortep_atom_fill_color", "#FFFFFF")),
        _atom_groups_cache_key(atom_groups),
        bond_groups_cache_key(bond_groups),
    )
    if key in cache:
        return cache[key]

    # Phase 4: bond_groups go through ``tag_bonds_with_groups`` BEFORE
    # ``_bond_segments`` reads the bond render fields, so any
    # _render_color / _render_visible / _render_radius_scale /
    # _render_opacity_scale set by a matching rule survives into the
    # mesh trace bucket key.
    if bond_groups:
        from .bond_groups import tag_bonds_with_groups

        original_bonds = scene["bonds"]
        scene["bonds"] = tag_bonds_with_groups(
            original_bonds,
            bond_groups,
            atoms=scene.get("draw_atoms") or [],
        )
    else:
        original_bonds = None

    # Phase 2: when atom_groups is set, decorate every atom with
    # per-render override fields, then partition by effective
    # (material, style) and run the matching trace builder per
    # partition. Bonds stay scene-level: their endpoint colours come
    # from the per-atom render colour (see ``_bond_segments``), and
    # bonds touching a hidden atom are dropped there as well, but we
    # don't fragment the bond render across partitions.
    scene_material = str(style.get("material", "mesh"))
    scene_style = str(style.get("style", "ball_stick"))
    if atom_groups:
        from .atom_groups import (
            partition_atoms_by_render_pipeline,
            tag_atoms_with_groups,
        )

        fragment_labels = scene.get("atom_fragment_labels") or None
        tagged_atoms = tag_atoms_with_groups(
            scene["draw_atoms"], atom_groups,
            scene_material=scene_material, scene_style=scene_style,
            fragment_labels=fragment_labels,
        )
        # Mutate the scene so ``_bond_segments`` (called below) sees
        # the per-atom ``_render_visible`` / ``_render_color`` flags.
        # We carefully restore the original list afterwards so cache
        # keys based on the unmutated scene id stay valid.
        original_atoms = scene["draw_atoms"]
        scene["draw_atoms"] = tagged_atoms
        try:
            partitions = partition_atoms_by_render_pipeline(
                tagged_atoms,
                scene_material=scene_material,
                scene_style=scene_style,
            )
            atom_traces = []
            for (part_material, part_style), part_atoms in partitions.items():
                sub_style = dict(style)
                sub_style["material"] = part_material
                sub_style["style"] = part_style
                # When the partition style flips to ``flat`` the
                # parent ``use_fast`` (set by the caller for the
                # scene-level material) may not match. Recompute it
                # locally so the partition actually picks the scatter
                # builder.
                part_use_fast = (
                    bool(sub_style.get("fast_rendering", False))
                    or part_material == "flat"
                    or len(part_atoms) > 2000
                )
                sub_scene = _atom_subscene(scene, part_atoms)
                atom_traces.extend(
                    _atom_traces_for_partition(sub_scene, sub_style, use_fast=part_use_fast)
                )
            # Bonds use the (mutated) scene with tagged atoms so the
            # endpoint visibility check works. Per-atom-group style
            # overrides only swing bond *style* when the rule covers
            # ALL drawn atoms uniformly -- otherwise we'd need to
            # split bonds across partitions (and pick the "right" half
            # per endpoint), which is visually ugly. The common case
            # ("flip the whole scene to ortep via an atom_group rule")
            # still works because the partition collapses to one
            # bucket whose style we read here.
            effective_bond_style = scene_style
            if len(partitions) == 1:
                only_key = next(iter(partitions))
                effective_bond_style = only_key[1]
            if effective_bond_style == "wireframe":
                bond_traces = _wireframe_bond_traces(scene, style)
            elif effective_bond_style == "ortep":
                bond_traces = (
                    _bond_scatter_traces(scene, style) if use_fast
                    else _bond_mesh_traces(scene, style)
                )
            elif use_fast:
                bond_traces = _bond_scatter_traces(scene, style)
            else:
                bond_traces = _bond_mesh_traces(scene, style)
            minor_outline = _minor_outline_traces(scene, style)
            minor_bonds = _minor_bond_wireframe_traces(scene, style)
            payload = {
                "atom_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in atom_traces],
                "bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in bond_traces],
                "minor_outline_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_outline],
                "minor_bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_bonds],
            }
            cache[key] = payload
            return payload
        finally:
            scene["draw_atoms"] = original_atoms
            if original_bonds is not None:
                scene["bonds"] = original_bonds

    if style.get("style") == "wireframe":
        atom_traces = _wireframe_atom_traces(scene, style)
        bond_traces = _wireframe_bond_traces(scene, style)
    elif style.get("style") == "ortep":
        from .ortep import (
            ortep_atom_billboard_traces,
            ortep_atom_mesh_traces,
            ortep_atom_fill_traces,
            ortep_axis_dash_traces,
            ortep_octant_shade_traces,
            ortep_octant_hatch_traces,
            ortep_silhouette_outline_traces,
        )

        # Classic ORTEP-III "open ellipsoid" mode: when the hatch octant +
        # silhouette outline are both enabled, the renderer drops every 3D
        # mesh body (atoms AND bonds) in favour of flat 2D-style lines.
        # Two reasons for this:
        #   1. Plotly's WebGL z-buffer aggressively culls Scatter3d line
        #      segments that share depth with a Mesh3d facet, occluding
        #      both the silhouette and the hatch arcs at most camera
        #      angles.  Removing the meshes removes the conflict.
        #   2. ORTEP-III publication figures are pure ink-on-paper drawings
        #      with no shading or lighting — the bonds are plain straight
        #      strokes, not shaded cylinders.  Flat scatter bonds match
        #      that convention exactly.
        is_open_ortep = (
            bool(style.get("ortep_silhouette_outline", False))
            and bool(style.get("ortep_octant_hatching", False))
        )
        if is_open_ortep:
            # Open-ellipsoid layered z-stack (far → near):
            #   1. bond strokes (flat black scatter)            ← bottom
            #   2. white atom-fill billboard disks
            #   3. hatch arcs + octant boundary arcs
            #   4. silhouette ellipse outline                    ← top
            # Each successive layer is pushed toward the camera by a small
            # ``ortep_z_lift_*`` offset so Plotly's WebGL z-buffer reliably
            # draws them in this order at every camera angle.
            bond_traces = _bond_scatter_traces(scene, style)
            atom_traces = ortep_atom_fill_traces(scene, style)
        else:
            atom_traces = (
                ortep_atom_billboard_traces(scene, style)
                if use_fast
                else ortep_atom_mesh_traces(scene, style)
            )
            bond_traces = (
                _bond_scatter_traces(scene, style) if use_fast
                else _bond_mesh_traces(scene, style)
            )
        atom_traces.extend(ortep_axis_dash_traces(scene, style))
        atom_traces.extend(ortep_octant_shade_traces(scene, style))
        atom_traces.extend(ortep_octant_hatch_traces(scene, style))
        atom_traces.extend(ortep_silhouette_outline_traces(scene, style))
    elif use_fast:
        atom_traces = _atom_scatter_traces(scene, style)
        bond_traces = _bond_scatter_traces(scene, style)
    else:
        atom_traces = _atom_mesh_traces(scene, style)
        bond_traces = _bond_mesh_traces(scene, style)
    minor_outline = _minor_outline_traces(scene, style)
    minor_bonds = _minor_bond_wireframe_traces(scene, style)
    payload = {
        "atom_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in atom_traces],
        "bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in bond_traces],
        "minor_outline_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_outline],
        "minor_bond_dicts": [_round_coord_arrays(tr.to_plotly_json()) for tr in minor_bonds],
    }
    cache[key] = payload
    if original_bonds is not None:
        scene["bonds"] = original_bonds
    return payload



# Export all helper symbols, including private ones, for renderer.py facade compatibility.
__all__ = [name for name in globals() if not name.startswith("__")]

__all__ = [name for name in globals() if not name.startswith("__")]
