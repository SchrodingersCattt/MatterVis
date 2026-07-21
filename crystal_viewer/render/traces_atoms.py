from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .common import *
from .meshes import *
from .style import *
from .traces_overlays import _dashed_segments, _ring_segments, _segment_cylinder_trace

def _bond_segments(scene: dict, style: dict, *, with_scales: bool = False):
    """Yield ``(color, is_minor, start, end)`` tuples for every bond half.

    When ``with_scales=True`` each yield is extended with
    ``(radius_scale, opacity_scale)`` (floats, default 1.0) so callers
    that build mesh traces can bucket on the bond_groups radius/opacity
    overrides. Default ``False`` keeps the legacy 4-tuple API for the
    other callers (cylinder schematic / line traces) that don't need
    per-bond cosmetics.

    A ``style["force_bond_color"]`` (hex string) overrides per-atom bond
    colouring without touching any other colour in the scene.  This is the
    knob the open-ellipsoid ORTEP path uses to render every bond as plain
    black ink, matching the publication ORTEP-III convention without
    forcing ``monochrome=True`` (which would also blacken atom fills).
    """
    forced = style.get("force_bond_color")
    atoms = scene.get("draw_atoms") or []
    n_atoms = len(atoms)
    for bond in scene["bonds"]:
        if style.get("show_minor_only", False) and float(bond.get("occ", 1.0)) >= 0.999:
            continue
        # Phase 4: bond_groups can mark a bond invisible directly. We
        # honour both the bond-level ``_render_visible`` (set by
        # ``tag_bonds_with_groups``) and the per-atom visibility (set
        # by ``tag_atoms_with_groups``); a half-bond that survives
        # both is drawn.
        if not bool(bond.get("_render_visible", True)):
            continue
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if 0 <= i < n_atoms and not _atom_render_visible(atoms[i]):
            continue
        if 0 <= j < n_atoms and not _atom_render_visible(atoms[j]):
            continue
        start = np.array(bond["start"], dtype=float)
        end = np.array(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        # Per-bond ``_render_color`` (bond_groups override) wins over
        # everything except ``style.force_bond_color`` (which is the
        # global "publication ORTEP-III black ink" knob).
        bond_render_color = bond.get("_render_color")
        if bond_render_color:
            i_color = forced if forced else bond_render_color
            j_color = forced if forced else bond_render_color
        else:
            i_color = forced if forced else (atoms[i].get("_render_color") if 0 <= i < n_atoms else None) or _style_color(bond["color_i"], style)
            j_color = forced if forced else (atoms[j].get("_render_color") if 0 <= j < n_atoms else None) or _style_color(bond["color_j"], style)
        c_i = i_color
        c_j = j_color
        radius_scale = float(bond.get("_render_radius_scale", 1.0) or 1.0)
        opacity_scale = float(bond.get("_render_opacity_scale", 1.0) or 1.0)
        opacity_group = _bond_opacity_group_id(bond)
        bond_occ = float(bond.get("occ", 1.0))
        halves = [
            (c_i, bond["is_minor"], start, mid),
            (c_j, bond["is_minor"], mid, end),
        ]
        for color, is_minor, seg_start, seg_end in halves:
            if bond_occ < 0.999 and style.get("disorder") == "dashed_bonds":
                length = float(np.linalg.norm(seg_end - seg_start))
                # Gap scales with disorder intensity: lower occ → bigger gaps
                intensity = 1.0 - bond_occ
                dash_len = max(0.08, 0.22 * length * bond_occ)
                gap_len = max(0.05, 0.14 * length * (1.0 + intensity))
                for dash_start, dash_end in _dashed_segments([(seg_start, seg_end)], dash_len=dash_len, gap_len=gap_len):
                    if with_scales:
                        yield color, is_minor, dash_start, dash_end, radius_scale, opacity_scale, opacity_group, bond_occ
                    else:
                        yield color, is_minor, dash_start, dash_end
            else:
                if with_scales:
                    yield color, is_minor, seg_start, seg_end, radius_scale, opacity_scale, opacity_group, bond_occ
                else:
                    yield color, is_minor, seg_start, seg_end


def _bond_mesh_traces(scene: dict, style: dict):
    """Build the bond Mesh3d traces, bucketed by ``(color, is_minor,
    radius_bin, opacity_bin)`` so per-bond ``_render_radius_scale`` /
    ``_render_opacity_scale`` (set by ``tag_bonds_with_groups``)
    survive the one-trace-per-colour grouping."""
    groups: Dict[Tuple[str, bool, int, str | None, str], dict] = {}
    base_radius = max(0.04, float(style["bond_radius"]))
    mesh_lighting = style.get("mesh_lighting")
    for color, is_minor, start, end, radius_scale, opacity_scale, opacity_group, bond_occ in _bond_segments(
        scene, style, with_scales=True
    ):
        # Bin to two decimals so e.g. a 1.50 vs 1.51 slider tick doesn't
        # fragment the trace list. Same trick is used in _atom_mesh_traces.
        radius_bin = int(round(float(radius_scale) * 100))
        eff_opacity = bond_effective_opacity(
            {"is_minor": is_minor, "_render_opacity_scale": opacity_scale, "occ": bond_occ},
            style,
        )
        opacity_bin = f"{eff_opacity:.2f}"
        key = (color, is_minor, radius_bin, opacity_group, opacity_bin)
        groups.setdefault(
            key,
            {"segments": [], "radius_scale": radius_scale, "opacity_scale": opacity_scale, "opacity_group": opacity_group, "opacity": eff_opacity},
        )["segments"].append((start, end))

    traces = []
    for (color, is_minor, _r_bin, opacity_group, _opc_bin), payload in groups.items():
        radius_scale = float(payload["radius_scale"])
        radius = base_radius * radius_scale
        vertices, triangles = _cylinder_mesh_batch(
            payload["segments"],
            radius,
            sides=6,
        )
        if len(vertices) == 0:
            continue
        # Build raw dict directly — avoids go.Mesh3d() validator overhead
        # (~100ms per trace for large meshes).
        n_verts = len(vertices)
        trace_dict = {
            "type": "mesh3d",
            "x": np.ascontiguousarray(vertices[:, 0], dtype=np.float32),
            "y": np.ascontiguousarray(vertices[:, 1], dtype=np.float32),
            "z": np.ascontiguousarray(vertices[:, 2], dtype=np.float32),
            "i": np.ascontiguousarray(triangles[:, 0], dtype=np.int16 if n_verts < 32768 else np.int32),
            "j": np.ascontiguousarray(triangles[:, 1], dtype=np.int16 if n_verts < 32768 else np.int32),
            "k": np.ascontiguousarray(triangles[:, 2], dtype=np.int16 if n_verts < 32768 else np.int32),
            "color": color,
            "opacity": payload["opacity"],
            "hoverinfo": "skip",
            "showlegend": False,
            "flatshading": False,
            "meta": _latency_meta("bond", is_minor=is_minor, opacity_group=opacity_group),
        }
        if mesh_lighting:
            trace_dict["lighting"] = mesh_lighting
        traces.append(trace_dict)
    return traces


def _atom_mesh_traces(scene: dict, style: dict):
    # Per-atom tessellation budget. User can override with
    # ortep_lat_steps / ortep_lon_steps (shared key name with ORTEP
    # for simplicity — controls sphere density in ball-stick too).
    user_lat = style.get("ortep_lat_steps")
    user_lon = style.get("ortep_lon_steps")
    if user_lat is not None and user_lon is not None:
        lat_steps, lon_steps = int(user_lat), int(user_lon)
    else:
        n_atoms = len(scene.get("draw_atoms", []))
        if n_atoms > 400:
            lat_steps, lon_steps = 3, 6
        elif n_atoms > 150:
            lat_steps, lon_steps = 4, 7
        elif n_atoms > 60:
            lat_steps, lon_steps = 5, 9
        else:
            lat_steps, lon_steps = 6, 10

    mesh_lighting = style.get("mesh_lighting")
    # Bucket key extends to (color, is_minor, opacity_scale_bin) so
    # per-group ``opacity`` overrides survive the Mesh3d
    # one-trace-per-colour grouping (Plotly bakes opacity into the
    # trace, not per-vertex). Quantise the scale to two decimals so a
    # slider that emits 0.523 vs 0.524 doesn't fragment the trace
    # list and tank the figure-JSON cache hit rate.
    # Bucket key extends to (color, is_minor, effective_opacity_bin) so
    # per-group ``opacity`` overrides survive the Mesh3d
    # one-trace-per-colour grouping (Plotly bakes opacity into the
    # trace, not per-vertex). Quantise the opacity to two decimals so a
    # slider that emits 0.523 vs 0.524 doesn't fragment the trace
    # list and tank the figure-JSON cache hit rate.
    groups: Dict[Tuple[str, bool, str | None, str], dict] = {}
    for atom in scene["draw_atoms"]:
        if style.get("show_minor_only", False) and float(atom.get("occ", 1.0)) >= 0.999:
            continue
        if not _atom_render_visible(atom):
            continue
        occ = float(atom.get("occ", 1.0))
        is_partial = occ < 0.999
        color = _atom_render_color(atom, style, light=is_partial)
        eff_opacity = _atom_effective_opacity(atom, style)
        opacity_group = _atom_opacity_group_id(atom)
        # Quantise opacity to 2 decimals so near-identical slider values
        # don't fragment traces and tank cache hit rate.
        opacity_bin = f"{eff_opacity:.2f}"
        key = (color, atom["is_minor"], opacity_group, opacity_bin)
        groups.setdefault(key, {"centers": [], "radii": [], "opacity": eff_opacity, "opacity_group": opacity_group})
        radius = float(atom["atom_radius"]) * float(style["atom_scale"])
        groups[key]["centers"].append(atom["cart"])
        groups[key]["radii"].append(radius)

    traces = []
    for (color, is_minor, opacity_group, _opc_bin), payload in groups.items():
        vertices, triangles = _sphere_mesh_batch(
            payload["centers"],
            payload["radii"],
            lat_steps=lat_steps,
            lon_steps=lon_steps,
        )
        # Build raw dict directly — avoids go.Mesh3d() validator overhead.
        n_verts = len(vertices)
        trace_dict = {
            "type": "mesh3d",
            "x": np.ascontiguousarray(vertices[:, 0], dtype=np.float32),
            "y": np.ascontiguousarray(vertices[:, 1], dtype=np.float32),
            "z": np.ascontiguousarray(vertices[:, 2], dtype=np.float32),
            "i": np.ascontiguousarray(triangles[:, 0], dtype=np.int16 if n_verts < 32768 else np.int32),
            "j": np.ascontiguousarray(triangles[:, 1], dtype=np.int16 if n_verts < 32768 else np.int32),
            "k": np.ascontiguousarray(triangles[:, 2], dtype=np.int16 if n_verts < 32768 else np.int32),
            "color": color,
            "opacity": payload["opacity"],
            "hoverinfo": "skip",
            "showlegend": False,
            "flatshading": False,
            "meta": _latency_meta("atom", is_minor=is_minor, opacity_group=opacity_group),
        }
        if mesh_lighting:
            trace_dict["lighting"] = mesh_lighting
        traces.append(trace_dict)
    return traces


def _bond_scatter_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool, str | None, str, str], dict] = {}
    for color, is_minor, start, end, _radius_scale, opacity_scale, opacity_group, bond_occ in _bond_segments(
        scene, style, with_scales=True
    ):
        eff_opacity = bond_effective_opacity(
            {"is_minor": is_minor, "_render_opacity_scale": opacity_scale, "occ": bond_occ},
            style,
        )
        opacity_bin = f"{eff_opacity:.2f}"
        occ_bin = f"{bond_occ:.2f}"
        groups.setdefault(
            (color, is_minor, opacity_group, opacity_bin, occ_bin),
            {"segments": [], "opacity_scale": opacity_scale, "occ": bond_occ, "opacity": eff_opacity},
        )["segments"].append([start, end])

    traces = []
    base_width = max(4.0, 72.0 * float(style["bond_radius"]) * float(style.get("scatter_bond_scale", 1.0)))
    for (color, is_minor, opacity_group, _opc_bin, _occ_bin), payload in groups.items():
        segments = payload["segments"]
        bond_occ = float(payload.get("occ", 1.0))
        xs, ys, zs = [], [], []
        for start, end in segments:
            xs.extend([float(start[0]), float(end[0]), None])
            ys.extend([float(start[1]), float(end[1]), None])
            zs.extend([float(start[2]), float(end[2]), None])
        # Raw dict avoids go.Scatter3d() validator overhead.
        trace_dict = {
            "type": "scatter3d",
            "x": xs,
            "y": ys,
            "z": zs,
            "mode": "lines",
            "line": {
                "color": color,
                "width": base_width,
                "dash": "dash" if bond_occ < 0.999 and style.get("disorder") == "dashed_bonds" else "solid",
            },
            "opacity": payload["opacity"],
            "hoverinfo": "skip",
            "showlegend": False,
            "meta": _latency_meta("bond", is_minor=is_minor, opacity_group=opacity_group),
        }
        traces.append(trace_dict)
    return traces


def _atom_scatter_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool, str, str | None], dict] = {}
    fragment_labels = scene.get("atom_fragment_labels") or []
    for idx, atom in enumerate(scene["draw_atoms"]):
        if style.get("show_minor_only", False) and float(atom.get("occ", 1.0)) >= 0.999:
            continue
        if not _atom_render_visible(atom):
            continue
        occ = float(atom.get("occ", 1.0))
        is_partial = occ < 0.999
        color = _atom_render_color(atom, style, light=is_partial)
        eff_opacity = _atom_effective_opacity(atom, style)
        opacity_group = _atom_opacity_group_id(atom)
        # Per-trace key = (element, is_minor, effective_color, effective_opacity_bin).
        # Adding colour to the key means a per-element atom_groups
        # rule still groups its atoms in one Scatter3d (so legend
        # entries still read element-by-element) but doesn't merge
        # red-O with default-O when the user splits them.
        key = (atom["elem"], atom["is_minor"], color, opacity_group)
        groups.setdefault(
            key,
            {"x": [], "y": [], "z": [], "size": [], "text": [], "color": color, "customdata": [], "opacity": eff_opacity},
        )
        base_size = max(10.0, 95.0 * atom["atom_radius"] * float(style["atom_scale"]) * float(style.get("scatter_atom_scale", 0.8)))
        groups[key]["x"].append(float(atom["cart"][0]))
        groups[key]["y"].append(float(atom["cart"][1]))
        groups[key]["z"].append(float(atom["cart"][2]))
        groups[key]["size"].append(base_size * (1.12 if atom["is_minor"] else 1.0))
        groups[key]["text"].append(atom["label"])
        frag_label = (
            str(fragment_labels[idx]) if idx < len(fragment_labels) and fragment_labels[idx] is not None else ""
        )
        groups[key]["customdata"].append([
            "atom",
            int(idx),
            str(atom["label"]),
            str(atom["elem"]),
            int(atom["is_minor"]),
            frag_label,
        ])

    traces = []
    for (elem, is_minor, _color, opacity_group), payload in groups.items():
        # Raw dict avoids go.Scatter3d() validator overhead.
        trace_dict = {
            "type": "scatter3d",
            "x": payload["x"],
            "y": payload["y"],
            "z": payload["z"],
            "mode": "markers",
            "text": payload["text"],
            "customdata": payload["customdata"],
            "hovertemplate": "%{text}<extra></extra>",
            "marker": {
                "size": payload["size"],
                "color": payload["color"],
                "opacity": payload["opacity"],
                "line": {"color": "#444444" if is_minor else payload["color"], "width": 3.5 if is_minor else 0},
            },
            "showlegend": False,
            "name": f"{elem}{' minor' if is_minor else ''}",
            "meta": _latency_meta("atom", is_minor=is_minor, opacity_group=opacity_group),
        }
        traces.append(trace_dict)
    return traces


def _minor_bond_wireframe_traces(scene: dict, style: dict):
    if style.get("disorder") not in ("outline_rings", "dashed_bonds") and not style.get("minor_wireframe", False):
        return []
    atoms = scene.get("draw_atoms") or []
    n_atoms = len(atoms)
    groups: Dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for bond in scene["bonds"]:
        if not bond["is_minor"]:
            continue
        # Phase 2: skip bonds whose endpoint atom was hidden by an
        # atom_groups ``visible: false`` rule -- otherwise the wireframe
        # ring sits in empty space and reads as a rendering bug.
        i = int(bond.get("i", -1))
        j = int(bond.get("j", -1))
        if 0 <= i < n_atoms and not _atom_render_visible(atoms[i]):
            continue
        if 0 <= j < n_atoms and not _atom_render_visible(atoms[j]):
            continue
        start = np.array(bond["start"], dtype=float)
        end = np.array(bond["end"], dtype=float)
        mid = (start + end) / 2.0
        i_color = (
            _atom_render_color(atoms[i], style, light=True)
            if 0 <= i < n_atoms
            else _style_color(bond.get("color_i", "#888888"), style)
        )
        j_color = (
            _atom_render_color(atoms[j], style, light=True)
            if 0 <= j < n_atoms
            else _style_color(bond.get("color_j", "#888888"), style)
        )
        groups.setdefault(i_color, []).append((start, mid))
        groups.setdefault(j_color, []).append((mid, end))
    if not groups:
        return []
    traces = []
    radius = max(0.015, 0.55 * float(style["bond_radius"]))
    for color, segments in groups.items():
        if style.get("disorder") == "dashed_bonds":
            lengths = [float(np.linalg.norm(end - start)) for start, end in segments]
            typical = float(np.median(lengths)) if lengths else 1.0
            segments = _dashed_segments(
                segments,
                dash_len=max(0.08, 0.18 * typical),
                gap_len=max(0.05, 0.12 * typical),
            )
        trace = _segment_cylinder_trace(
            segments,
            radius=radius,
            color=color,
            opacity=0.9,
            sides=4,
            name="minor-bond-wireframe",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "bond", is_minor=True))
    return traces


def _wireframe_atom_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool], list[tuple[np.ndarray, np.ndarray]]] = {}
    axes = [
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ]
    for atom in scene["draw_atoms"]:
        if style.get("show_minor_only", False) and not atom["is_minor"]:
            continue
        if not _atom_render_visible(atom):
            continue
        radius = max(0.05, float(atom["atom_radius"]) * float(style["atom_scale"]))
        occ = float(atom.get("occ", 1.0))
        key = (_atom_render_color(atom, style, light=(occ < 0.999)), atom["is_minor"])
        bucket = groups.setdefault(key, [])
        center = np.asarray(atom["cart"], dtype=float)
        for axis in axes:
            bucket.extend(_ring_segments(center, radius, axis, segments=18))

    traces = []
    for (color, is_minor), segments in groups.items():
        trace = _segment_cylinder_trace(
            segments,
            radius=max(0.008, 0.065 * float(style["bond_radius"])),
            color=color,
            opacity=_minor_opacity_for(style, is_minor),
            sides=4,
            name="wireframe-atoms",
        )
        if trace is not None:
            traces.append(_annotate_trace(trace, "atom", is_minor=is_minor))
    return traces


def _wireframe_bond_traces(scene: dict, style: dict):
    groups: Dict[Tuple[str, bool, str | None], dict] = {}
    for color, is_minor, start, end, _radius_scale, opacity_scale, opacity_group, bond_occ in _bond_segments(
        scene, style, with_scales=True
    ):
        groups.setdefault(
            (color, is_minor, opacity_group),
            {"segments": [], "opacity_scale": opacity_scale, "occ": bond_occ},
        )["segments"].append((start, end))
    traces = []
    for (color, is_minor, opacity_group), payload in groups.items():
        segments = payload["segments"]
        opacity_scale = float(payload["opacity_scale"])
        trace = _segment_cylinder_trace(
            segments,
            radius=max(0.01, 0.40 * float(style["bond_radius"])),
            color=color,
            opacity=bond_effective_opacity(
                {"is_minor": is_minor, "_render_opacity_scale": opacity_scale},
                style,
            ),
            sides=4,
            name="wireframe-bonds",
        )
        if trace is not None:
            traces.append(
                _annotate_trace(
                    trace,
                    "bond",
                    is_minor=is_minor,
                    opacity_group=opacity_group,
                    opacity_scale=opacity_scale,
                )
            )
    return traces



__all__ = [name for name in globals() if not name.startswith("__")]
