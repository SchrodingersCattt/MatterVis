"""Compile chemistry-aware sources into backend-neutral render plans."""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..config import atom_radius as configured_atom_radius
from .compass_overlay import lattice_compass_metadata
from .contracts import (
    CameraSpec,
    LinePrimitive,
    Primitive,
    RenderPlan,
    RenderSpec,
    TextPrimitive,
    TriangleMeshPrimitive,
    ViewSpec,
    ViewportPlan,
)
from .geometry import (
    aromatic_ring_primitive,
    bond_primitives,
    color_to_rgba,
    ellipsoid_axes_primitive,
    ellipsoid_hatch_primitive,
    ellipsoid_primitive,
    mesh_primitive,
    polyhedron_edges_primitive,
    polyhedron_primitive,
    sphere_primitive,
    unit_cell_primitive,
)
from .overlay.vectors import vector_primitives

_ELEMENT_COLORS = {
    "H": "#FFFFFF",
    "D": "#E8F5FF",
    "C": "#4A4A4A",
    "N": "#3050F8",
    "O": "#FF0D0D",
    "F": "#90E050",
    "Cl": "#1FF01F",
    "Br": "#A62929",
    "I": "#940094",
    "P": "#FF8000",
    "S": "#FFFF30",
    "B": "#FFB5B5",
    "Si": "#F0C8A0",
    "Fe": "#E06633",
    "Cu": "#C88033",
    "Zn": "#7D80B0",
}


def prepare_render(
    source: Any,
    view: ViewSpec | Mapping[str, Any] | None = None,
    camera: CameraSpec | Mapping[str, Any] | None = None,
    render: RenderSpec | Mapping[str, Any] | None = None,
    *,
    topology_data: Mapping[str, Any] | None = None,
    vector_overlays: Any = None,
) -> RenderPlan:
    """Compile a scene, CrystalIR, or MolCrysKit object into a RenderPlan.

    The function does no drawing and imports no graphics backend.  Mappings
    accept the public dataclass field names; a small set of historic style
    aliases is normalised explicitly for migration callers.
    """
    view_spec = _coerce_dataclass(ViewSpec, view)
    render_spec = _coerce_render_spec(render)
    camera_spec = _coerce_dataclass(CameraSpec, camera) if camera is not None else None
    scene = _normalise_source(
        source,
        display_mode=view_spec.display,
        show_hydrogen=render_spec.show_hydrogen,
    )
    if vector_overlays is not None:
        scene["vector_overlays"] = vector_overlays
    scene_display = scene.get("display_mode")
    if scene_display is not None:
        scene_view = ViewSpec(display=str(scene_display))
        if view is not None and scene_view.display != view_spec.display:
            raise ValueError(
                "a prebuilt scene cannot be reinterpreted as a different display mode; "
                "pass LoadedCrystal/StructureInput or rebuild the scene"
            )
        if view is None:
            view_spec = scene_view
    atoms = list(scene.get("atoms") or [])
    bonds = list(scene.get("bonds") or [])
    warnings: list[str] = []
    primitives: list[Primitive] = []

    visible_indices: set[int] = set()
    disordered_source_indices: set[int] = set()
    atom_colors: dict[int, Any] = {}
    atom_positions: dict[int, np.ndarray] = {}
    display_atoms_by_source: dict[int, dict[tuple[Any, ...], int]] = {}
    representation = str(render_spec.representation).lower().replace("-", "_")
    lat_steps, lon_steps = render_spec.sphere_detail
    ortep_view_direction = (
        np.asarray(camera_spec.position) - np.asarray(camera_spec.target)
        if camera_spec is not None
        else np.asarray([1.0, 1.0, 0.8])
    )

    for index, atom in enumerate(atoms):
        element = (
            str(_value(atom, "symbol", "elem", "element", default="C"))
            .strip()
            .capitalize()
        )
        if not render_spec.show_hydrogen and element in {"H", "D"}:
            continue
        if not bool(_value(atom, "_render_visible", "visible", default=True)):
            continue
        position = np.asarray(
            _value(
                atom,
                "cartesian_position_A",
                "cart",
                "position",
                "cart_coords",
            ),
            dtype=float,
        )
        if position.shape != (3,) or not np.all(np.isfinite(position)):
            warnings.append(
                f"atom {index} has invalid Cartesian coordinates and was skipped"
            )
            continue
        visible_indices.add(index)
        atom_positions[index] = position
        color = _value(
            atom,
            "_render_color",
            "color",
            default=_ELEMENT_COLORS.get(element, "#808080"),
        )
        atom_colors[index] = color
        source_index = _source_atom_index(atom, index)
        occupancy = float(_value(atom, "occ", "occupancy", default=1.0))
        disorder_group = _value(atom, "disorder_group", "dg", default=None)
        if occupancy < 1.0 - 1.0e-8 or disorder_group not in (
            None,
            0,
            "0",
            ".",
            "?",
        ):
            disordered_source_indices.add(source_index)
        opacity_scale = float(_value(atom, "_render_opacity_scale", default=1.0))
        alpha = float(np.clip(occupancy * opacity_scale, 0.0, 1.0))
        label = str(_value(atom, "label", default=f"{element}{index + 1}"))
        semantic_id = f"atom:{index}:{label}"
        copy_key = _display_copy_key(atom)
        source_instances = display_atoms_by_source.setdefault(source_index, {})
        previous_index = source_instances.setdefault(copy_key, index)
        if previous_index != index:
            warnings.append(
                f"atoms {previous_index} and {index} have the same source/copy identity; "
                "the first instance is used for topology"
            )
        metadata = {
            "kind": "atom",
            "atom_index": index,
            "element": element,
            "label": label,
            "source_index": source_index,
            "occupancy": occupancy,
            "disorder_group": disorder_group,
            "disorder_assembly": _value(atom, "disorder_assembly", "da", default=None),
            "image_shift": _integer_triplet(
                _value(atom, "image_shift", "_image_shift", default=(0, 0, 0))
            ),
        }

        if representation == "ortep":
            displacement = _displacement_matrix(atom)
            if displacement is None:
                if render_spec.missing_adp_policy == "error":
                    raise ValueError(
                        f"ORTEP requires Ucart/Uij or Uiso for atom {label}; "
                        "set missing_adp_policy='sphere' to request an explicit placeholder"
                    )
                placeholder_radius = (
                    _atom_radius(atom, representation="ball_stick")
                    * render_spec.atom_scale
                )
                displacement = np.eye(3) * (placeholder_radius / 1.54) ** 2
                warnings.append(
                    f"atom {label} has no ADP; rendered with an explicit isotropic placeholder"
                )
            primitives.append(
                ellipsoid_primitive(
                    semantic_id,
                    position,
                    displacement,
                    color,
                    probability=render_spec.ortep_probability,
                    lat_steps=lat_steps,
                    lon_steps=lon_steps,
                    alpha=alpha,
                    metadata=metadata,
                )
            )
            if render_spec.ortep_mode in {"axes", "hatch"}:
                primitives.append(
                    ellipsoid_axes_primitive(
                        f"{semantic_id}:axes",
                        position,
                        displacement,
                        probability=render_spec.ortep_probability,
                    )
                )
            if render_spec.ortep_mode == "hatch":
                primitives.append(
                    ellipsoid_hatch_primitive(
                        f"{semantic_id}:hatch",
                        position,
                        displacement,
                        ortep_view_direction,
                        probability=render_spec.ortep_probability,
                    )
                )
        elif representation not in {"stick", "wireframe"}:
            radius = (
                _atom_radius(atom, representation=representation)
                * render_spec.atom_scale
            )
            primitives.append(
                sphere_primitive(
                    semantic_id,
                    position,
                    radius,
                    color,
                    lat_steps=lat_steps,
                    lon_steps=lon_steps,
                    alpha=alpha,
                    metadata=metadata,
                )
            )
        elif representation == "stick":
            primitives.append(
                sphere_primitive(
                    semantic_id,
                    position,
                    render_spec.bond_radius,
                    color,
                    lat_steps=max(6, lat_steps // 2),
                    lon_steps=max(8, lon_steps // 2),
                    alpha=alpha,
                    metadata=metadata,
                )
            )

        if render_spec.show_labels:
            primitives.append(
                TextPrimitive(
                    semantic_id=f"label:{index}:{label}",
                    position=tuple(position),
                    text=label,
                    rgba=color_to_rgba(color),
                    offset_px=(5.0, -5.0),
                    metadata={"kind": "atom_label", "atom_index": index},
                )
            )

    if disordered_source_indices:
        warnings.append(
            "scene contains disorder at "
            f"{len(disordered_source_indices)} source sites; occupancies are "
            "rendered as opacity without automatic disorder resolution"
        )

    if representation != "ball":
        for bond_index, bond in enumerate(bonds):
            first_index = int(
                _value(
                    bond,
                    "left_global_index",
                    "i",
                    "start_index",
                    "atom_i",
                    default=-1,
                )
            )
            second_index = int(
                _value(
                    bond,
                    "right_global_index",
                    "j",
                    "end_index",
                    "atom_j",
                    default=-1,
                )
            )
            if (
                first_index not in visible_indices
                or second_index not in visible_indices
            ):
                continue
            start = np.asarray(
                _value(bond, "start", default=atom_positions[first_index]), dtype=float
            )
            vector = _value(bond, "vector_A", default=None)
            default_end = (
                start + np.asarray(vector, dtype=float)
                if vector is not None
                else atom_positions[second_index]
            )
            end = np.asarray(_value(bond, "end", default=default_end), dtype=float)
            if (
                start.shape != (3,)
                or end.shape != (3,)
                or not np.all(np.isfinite([start, end]))
            ):
                warnings.append(
                    f"bond {bond_index} has invalid endpoints and was skipped"
                )
                continue
            alpha = float(np.clip(_value(bond, "alpha", default=1.0), 0.0, 1.0))
            semantic_id = f"bond:{bond_index}:{first_index}-{second_index}"
            bond_metadata = {
                "kind": "bond",
                "atom_indices": [first_index, second_index],
                "right_image_shift": _integer_triplet(
                    _value(bond, "right_image_shift", default=(0, 0, 0))
                ),
            }
            if representation == "wireframe":
                primitives.append(
                    LinePrimitive(
                        semantic_id=semantic_id,
                        segments=np.asarray([[start, end]]),
                        rgba=color_to_rgba(
                            _value(bond, "color", default="#333333"), alpha=alpha
                        ),
                        width_px=max(1.0, render_spec.bond_radius * 8.0),
                        metadata=bond_metadata,
                    )
                )
            else:
                primitives.extend(
                    bond_primitives(
                        semantic_id,
                        start,
                        end,
                        render_spec.bond_radius,
                        _value(bond, "color_i", default=atom_colors[first_index]),
                        _value(bond, "color_j", default=atom_colors[second_index]),
                        sides=render_spec.cylinder_sides,
                        alpha=alpha,
                        metadata=bond_metadata,
                    )
                )

    if render_spec.aromatic_rings != "bonds":
        for ring_index, ring in enumerate(scene.get("rings") or []):
            if not bool(_value(ring, "aromatic", "is_aromatic", default=False)):
                continue
            source_indices = [
                int(index)
                for index in _value(
                    ring,
                    "cycle_atom_indices",
                    "source_atom_indices",
                    "global_atom_indices",
                    "atom_indices",
                    default=[],
                )
            ]
            display_cycles = _ring_display_cycles(
                ring,
                source_indices,
                display_atoms_by_source,
            )
            if len(source_indices) < 3 or not display_cycles:
                warnings.append(
                    f"aromatic ring {ring_index} lacks a complete ordered cycle and was skipped"
                )
                continue
            for copy_index, (copy_key, display_indices) in enumerate(display_cycles):
                points = np.asarray(
                    [atom_positions[index] for index in display_indices]
                )
                normal = _value(ring, "normal", default=None)
                semantic_id = (
                    f"ring:{ring_index}"
                    if len(display_cycles) == 1
                    else f"ring:{ring_index}:copy:{copy_index}"
                )
                primitives.append(
                    aromatic_ring_primitive(
                        semantic_id,
                        points,
                        _value(ring, "color", default="#333333"),
                        mode=render_spec.aromatic_rings,
                        normal=normal,
                        alpha=0.85 if render_spec.aromatic_rings == "disk" else 1.0,
                        metadata={
                            "kind": "aromatic_ring",
                            "atom_indices": display_indices,
                            "source_atom_indices": source_indices,
                            "display_copy": copy_key,
                        },
                    )
                )

    lattice = scene.get("matrix")
    if render_spec.show_cell and lattice is not None:
        primitives.append(
            unit_cell_primitive(
                "unit-cell",
                lattice,
                color=render_spec.cell_color,
                width_px=render_spec.cell_width_px,
                depth_test=False,
            )
        )

    primitives.extend(_polyhedron_primitives(scene, topology_data))
    isosurface_primitives, isosurface_warnings = _isosurface_primitives(scene)
    primitives.extend(isosurface_primitives)
    warnings.extend(isosurface_warnings)
    primitives.extend(
        vector_primitives(scene.get("vector_overlays"), lattice=scene.get("matrix"))
    )
    if render_spec.shading == "flat":
        primitives = [
            (
                replace(primitive, vertex_normals=None)
                if isinstance(primitive, TriangleMeshPrimitive)
                else primitive
            )
            for primitive in primitives
        ]
    primitives = sorted(primitives, key=lambda primitive: primitive.semantic_id)
    _assert_unique_ids(primitives)

    fitted_camera = camera_spec or _fit_camera(
        primitives,
        width=render_spec.width,
        height=render_spec.height,
    )
    viewport = ViewportPlan(
        semantic_id="main",
        camera=fitted_camera,
        primitives=tuple(primitives),
    )
    source_path = scene.get("source_path")
    metadata = {
        "view": view_spec.display,
        "display_mode": scene.get("display_mode", view_spec.display),
        "representation": render_spec.representation,
        "shading": render_spec.shading,
        "ortep_mode": render_spec.ortep_mode,
        "requested_backend": render_spec.backend,
        "scale": render_spec.scale,
        "source": str(Path(source_path)) if source_path else None,
        "input_format": scene.get("input_format"),
        "frame_index": scene.get("frame_index"),
        "frame_info": scene.get("frame_info"),
        "molcrys_provenance": scene.get("molcrys_provenance"),
    }
    if render_spec.show_axes:
        compass = lattice_compass_metadata(lattice)
        if compass is None:
            warnings.append(
                "lattice axes were requested but the source has no finite lattice"
            )
        else:
            metadata["lattice_compass"] = compass
    return RenderPlan(
        width=render_spec.width,
        height=render_spec.height,
        background=render_spec.background,
        viewports=(viewport,),
        metadata=metadata,
        warnings=tuple(warnings),
    )


def _coerce_dataclass(cls: type, value: Any):
    if value is None:
        return cls()
    if isinstance(value, cls):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"expected {cls.__name__} or a mapping")
    names = {item.name for item in fields(cls)}
    unknown = sorted(set(value) - names)
    if unknown:
        raise ValueError(f"unknown {cls.__name__} fields: {', '.join(unknown)}")
    return cls(**dict(value))


def _coerce_render_spec(value: RenderSpec | Mapping[str, Any] | None) -> RenderSpec:
    if value is None or isinstance(value, RenderSpec):
        return value or RenderSpec()
    if not isinstance(value, Mapping):
        raise TypeError("render must be a RenderSpec or mapping")
    payload = dict(value)
    aliases = {
        "style": "representation",
        "show_unit_cell": "show_cell",
    }
    for old, new in aliases.items():
        if old not in payload:
            continue
        if new in payload:
            raise ValueError(f"conflicting RenderSpec fields: {old} and {new}")
        payload[new] = payload.pop(old)
    material = payload.pop("material", None)
    if material is not None:
        material_key = str(material).strip().lower()
        material_shading = {"mesh": "smooth", "smooth": "smooth", "flat": "flat"}.get(
            material_key
        )
        if material_shading is None:
            raise ValueError("material must be mesh, smooth, or flat")
        if "shading" in payload and payload["shading"] != material_shading:
            raise ValueError("material and shading request conflicting surface shading")
        payload["shading"] = material_shading
    names = {item.name for item in fields(RenderSpec)}
    unknown = sorted(set(payload) - names)
    if unknown:
        raise ValueError(f"unknown RenderSpec fields: {', '.join(unknown)}")
    return RenderSpec(**payload)


def _normalise_source(
    source: Any,
    *,
    display_mode: str,
    show_hydrogen: bool,
) -> dict[str, Any]:
    if not isinstance(source, Mapping) and hasattr(source, "frames"):
        frames = tuple(source.frames)
        if not frames:
            raise ValueError("StructureInput contains no selected frames")
        scene = _normalise_source(
            frames[0],
            display_mode=display_mode,
            show_hydrogen=show_hydrogen,
        )
        source_path = getattr(source, "path", None)
        if source_path is not None:
            scene["source_path"] = source_path
        scene["input_format"] = getattr(source, "input_format", None)
        scene["total_frames"] = getattr(source, "total_frames", len(frames))
        return scene
    if not isinstance(source, Mapping) and hasattr(source, "bundle"):
        scene = _normalise_source(
            source.bundle,
            display_mode=display_mode,
            show_hydrogen=show_hydrogen,
        )
        scene["frame_index"] = getattr(source, "index", 0)
        scene["frame_info"] = dict(getattr(source, "info", {}) or {})
        scene["atom_arrays"] = dict(getattr(source, "atom_arrays", {}) or {})
        return scene
    if isinstance(source, Mapping):
        return {
            **source,
            "atoms": list(source.get("draw_atoms") or source.get("atoms") or []),
            "bonds": list(source.get("bonds") or []),
            "matrix": (
                source.get("M") if source.get("M") is not None else source.get("matrix")
            ),
        }
    if hasattr(source, "scene") and isinstance(source.scene, Mapping):
        if _is_loaded_crystal(source):
            from ..loader.core import build_bundle_scene

            built_scene = build_bundle_scene(
                source,
                display_mode=display_mode,
                show_hydrogen=show_hydrogen,
            )
        else:
            built_scene = source.scene
        scene = _normalise_source(
            built_scene,
            display_mode=display_mode,
            show_hydrogen=show_hydrogen,
        )
        cube_data = getattr(source, "cube_data", None)
        if cube_data is not None:
            scene["cube_data"] = cube_data
        precomputed = getattr(source, "isosurfaces", None)
        if precomputed is not None:
            scene["isosurfaces"] = precomputed
        source_path = getattr(source, "cif_path", None)
        if source_path:
            scene.setdefault("source_path", source_path)
        provenance = getattr(source, "molcrys_analysis", None)
        if provenance is not None:
            scene["molcrys_provenance"] = provenance
        return scene
    if hasattr(source, "get_site_records"):
        site_getter = getattr(source, "get_site_records")
        bond_getter = getattr(source, "get_bond_records", None)
        if not callable(site_getter):
            raise TypeError(
                "MolCrysKit source get_site_records must be a public callable"
            )
        if not callable(bond_getter):
            raise TypeError(
                "MolCrysKit source exposes get_site_records but is missing the "
                "required public get_bond_records callable; MatterVis will not "
                "infer connectivity"
            )
        sites = list(site_getter())
        bonds = list(bond_getter())
        matrix = _source_matrix(source)
        return _normalise_molecular_crystal(
            source,
            sites,
            bonds,
            matrix,
            display_mode=display_mode,
        )
    if hasattr(source, "atoms"):
        lattice = getattr(source, "lattice", None)
        matrix = getattr(lattice, "matrix", None) if lattice is not None else None
        return {
            "atoms": list(source.atoms),
            "bonds": list(getattr(source, "bonds", [])),
            "matrix": matrix,
            "rings": list(getattr(source, "rings", [])),
            "source_path": getattr(source, "source_path", None),
        }
    raise TypeError(
        "source must be a scene mapping, CrystalIR, or MolCrysKit structure"
    )


def _is_loaded_crystal(source: Any) -> bool:
    """Recognise a canonical bundle without importing the loader eagerly."""
    return all(
        hasattr(source, name)
        for name in ("raw_atoms", "scene_cache", "M", "cell", "formula_unit_atoms")
    )


def _normalise_molecular_crystal(
    source: Any,
    sites: Sequence[Any],
    bonds: Sequence[Any],
    matrix: Any,
    *,
    display_mode: str,
) -> dict[str, Any]:
    """Apply public MolCrysKit selection contracts to a direct Python input."""
    lattice = None if matrix is None else np.asarray(matrix, dtype=float)
    if lattice is not None and lattice.shape != (3, 3):
        raise ValueError("MolecularCrystal lattice must be a 3x3 matrix")
    rings = _molecular_crystal_rings(source, sites)
    common = {
        "matrix": lattice,
        "source_path": getattr(source, "source_path", None),
        "display_mode": display_mode,
    }
    if display_mode == "unit_cell":
        return {**common, **_unit_cell_records(sites, bonds, rings)}
    if display_mode == "cluster":
        raise ValueError(
            "display='cluster' is undefined for direct MolecularCrystal input; "
            "pass a preselected scene or LoadedCrystal"
        )
    if display_mode == "asymmetric_unit":
        return {
            **common,
            **_asymmetric_unit_records(sites, bonds, rings),
        }
    if display_mode != "formula_unit":
        raise ValueError(f"unsupported MolecularCrystal display mode: {display_mode}")
    if lattice is None:
        raise ValueError("formula-unit selection requires MolecularCrystal.lattice")
    if not hasattr(source, "molecules"):
        raise ValueError(
            "formula-unit selection requires a real MolCrysKit MolecularCrystal; "
            "use display='unit_cell' for record-only sources"
        )
    try:
        from molcrys_kit.analysis import StoichiometryAnalyzer
    except ImportError as exc:  # pragma: no cover - runtime contract guard
        raise RuntimeError(
            "MolCrysKit StoichiometryAnalyzer is required for formula-unit display"
        ) from exc
    selection = StoichiometryAnalyzer(source).select_formula_unit()
    if selection is None or not getattr(selection, "members", None):
        raise RuntimeError("MolCrysKit could not select a deterministic formula unit")
    return {
        **common,
        **_formula_unit_records(sites, bonds, rings, selection.members, lattice),
    }


def _site_mapping(
    site: Any,
    *,
    position: Any,
    display_image_shift: Any | None = None,
) -> dict[str, Any]:
    symbol = str(_value(site, "symbol"))
    source_image_shift = _integer_triplet(
        _value(site, "image_shift", default=(0, 0, 0))
    )
    return {
        "symbol": symbol,
        "label": str(_value(site, "label", default="")),
        "cartesian_position_A": np.asarray(position, dtype=float),
        "atom_radius": configured_atom_radius(symbol),
        "occupancy": float(_value(site, "occupancy", default=1.0)),
        "disorder_group": _value(site, "disorder_group", default=None),
        "disorder_assembly": _value(site, "disorder_assembly", default=None),
        "uiso_A2": _value(site, "uiso_A2", default=None),
        "u_cart_A2": _value(site, "u_cart_A2", default=None),
        "_source_index": int(_value(site, "global_index")),
        "_molecule_index": int(_value(site, "molecule_index")),
        "image_shift": source_image_shift,
        "_image_shift": (
            source_image_shift
            if display_image_shift is None
            else _integer_triplet(display_image_shift)
        ),
    }


def _unit_cell_records(
    sites: Sequence[Any],
    bonds: Sequence[Any],
    rings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(sites, key=lambda site: int(_value(site, "global_index")))
    atoms = [
        _site_mapping(
            site,
            position=_value(site, "cartesian_position_A"),
            # molecule_index already distinguishes each manifested molecule;
            # per-site PBC image shifts must not fragment an unwrapped ring.
            display_image_shift=(0, 0, 0),
        )
        for site in ordered
    ]
    display_by_global = {
        int(_value(site, "global_index")): index for index, site in enumerate(ordered)
    }
    display_bonds = _remap_bond_records(bonds, atoms, display_by_global)
    return {"atoms": atoms, "bonds": display_bonds, "rings": list(rings)}


def _remap_bond_records(
    bonds: Sequence[Any],
    atoms: Sequence[Mapping[str, Any]],
    display_by_global: Mapping[int, int],
) -> list[dict[str, Any]]:
    result = []
    for bond in bonds:
        left_source = int(_value(bond, "left_global_index"))
        right_source = int(_value(bond, "right_global_index"))
        if (
            left_source not in display_by_global
            or right_source not in display_by_global
        ):
            continue
        left = int(display_by_global[left_source])
        right = int(display_by_global[right_source])
        vector = np.asarray(_value(bond, "vector_A"), dtype=float)
        start = np.asarray(atoms[left]["cartesian_position_A"], dtype=float)
        result.append(
            {
                "i": left,
                "j": right,
                "start": start,
                "end": start + vector,
                "vector_A": vector,
                "right_image_shift": _integer_triplet(
                    _value(bond, "right_image_shift", default=(0, 0, 0))
                ),
                "source_left_global_index": left_source,
                "source_right_global_index": right_source,
            }
        )
    return result


def _formula_unit_records(
    sites: Sequence[Any],
    bonds: Sequence[Any],
    rings: Sequence[Mapping[str, Any]],
    members: Sequence[Any],
    lattice: np.ndarray,
) -> dict[str, Any]:
    sites_by_molecule: dict[int, list[Any]] = {}
    bonds_by_molecule: dict[int, list[Any]] = {}
    for site in sites:
        sites_by_molecule.setdefault(int(_value(site, "molecule_index")), []).append(
            site
        )
    for bond in bonds:
        bonds_by_molecule.setdefault(int(_value(bond, "molecule_index")), []).append(
            bond
        )

    atoms: list[dict[str, Any]] = []
    display_bonds: list[dict[str, Any]] = []
    selected_molecules: set[int] = set()
    for member in members:
        molecule_index = int(member.molecule_index)
        shift = _integer_triplet(member.image_shift)
        shift_cart = np.asarray(shift, dtype=float) @ lattice
        selected_molecules.add(molecule_index)
        display_by_global: dict[int, int] = {}
        for site in sorted(
            sites_by_molecule.get(molecule_index, ()),
            key=lambda item: int(_value(item, "local_index")),
        ):
            position = (
                np.asarray(_value(site, "cartesian_position_A"), dtype=float)
                + shift_cart
            )
            mapped = _site_mapping(site, position=position)
            mapped["_formula_species_id"] = str(member.species_id)
            mapped["_formula_image_shift"] = shift
            display_by_global[int(_value(site, "global_index"))] = len(atoms)
            atoms.append(mapped)
        for bond in bonds_by_molecule.get(molecule_index, ()):
            left_source = int(_value(bond, "left_global_index"))
            right_source = int(_value(bond, "right_global_index"))
            if (
                left_source not in display_by_global
                or right_source not in display_by_global
            ):
                continue
            left = display_by_global[left_source]
            right = display_by_global[right_source]
            vector = np.asarray(_value(bond, "vector_A"), dtype=float)
            start = np.asarray(atoms[left]["cartesian_position_A"], dtype=float)
            display_bonds.append(
                {
                    "i": left,
                    "j": right,
                    "start": start,
                    "end": start + vector,
                    "vector_A": vector,
                    "right_image_shift": _integer_triplet(
                        _value(bond, "right_image_shift", default=(0, 0, 0))
                    ),
                    "source_left_global_index": left_source,
                    "source_right_global_index": right_source,
                }
            )
    selected_rings = [
        dict(ring)
        for ring in rings
        if int(ring["molecule_index"]) in selected_molecules
    ]
    return {"atoms": atoms, "bonds": display_bonds, "rings": selected_rings}


def _asymmetric_unit_records(
    sites: Sequence[Any],
    bonds: Sequence[Any],
    rings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    representatives: dict[int, Any] = {}
    for site in sites:
        asym_index = _value(site, "asym_index", default=None)
        if asym_index is None:
            raise ValueError(
                "asymmetric-unit display requires SiteRecord.asym_index for every site"
            )
        key = int(asym_index)
        candidate_key = (
            int(_value(site, "sym_op_index", default=0)),
            _integer_triplet(_value(site, "image_shift", default=(0, 0, 0))),
            int(_value(site, "global_index")),
        )
        previous = representatives.get(key)
        if previous is None:
            representatives[key] = site
        else:
            previous_key = (
                int(_value(previous, "sym_op_index", default=0)),
                _integer_triplet(_value(previous, "image_shift", default=(0, 0, 0))),
                int(_value(previous, "global_index")),
            )
            if candidate_key < previous_key:
                representatives[key] = site

    ordered = [representatives[index] for index in sorted(representatives)]
    atoms = [
        _site_mapping(
            site,
            position=_value(site, "cartesian_position_A"),
            display_image_shift=(0, 0, 0),
        )
        for site in ordered
    ]
    display_by_global = {
        int(_value(site, "global_index")): display_index
        for display_index, site in enumerate(ordered)
    }
    display_bonds = _remap_bond_records(bonds, atoms, display_by_global)
    selected_sources = set(display_by_global)
    selected_rings = [
        dict(ring)
        for ring in rings
        if set(int(index) for index in ring["cycle_atom_indices"]) <= selected_sources
    ]
    return {"atoms": atoms, "bonds": display_bonds, "rings": selected_rings}


def _molecular_crystal_rings(source: Any, sites: Sequence[Any]) -> list[dict[str, Any]]:
    """Map MolCrysKit molecule-local ring cycles onto stable global indices."""
    if not hasattr(source, "molecules"):
        return []
    try:
        from molcrys_kit.analysis import LocalGeometryCache
    except ImportError as exc:  # pragma: no cover - runtime contract guard
        raise RuntimeError(
            "MolCrysKit LocalGeometryCache is required for aromatic ring geometry"
        ) from exc

    global_by_local: dict[tuple[int, int], int] = {}
    for site in sites:
        try:
            key = (
                int(_value(site, "molecule_index")),
                int(_value(site, "local_index")),
            )
            global_by_local[key] = int(_value(site, "global_index"))
        except (TypeError, ValueError):
            continue

    cache = LocalGeometryCache(source)
    result: list[dict[str, Any]] = []
    for molecule_index in range(len(source.molecules)):
        for ring_index, ring in enumerate(cache[molecule_index].rings()):
            cycle = getattr(ring, "cycle_atom_indices", None)
            if not cycle:
                raise RuntimeError(
                    "MolCrysKit RingGeometry is missing cycle_atom_indices; "
                    "install the structure-contract release required by MatterVis"
                )
            try:
                cycle_global = tuple(
                    global_by_local[(molecule_index, int(local_index))]
                    for local_index in cycle
                )
            except KeyError as exc:
                raise RuntimeError(
                    "MolCrysKit ring topology does not match its public SiteRecord indices"
                ) from exc
            sorted_local = tuple(getattr(ring, "atom_indices", cycle))
            try:
                sorted_global = tuple(
                    global_by_local[(molecule_index, int(local_index))]
                    for local_index in sorted_local
                )
            except KeyError as exc:
                raise RuntimeError(
                    "MolCrysKit ring topology does not match its public SiteRecord indices"
                ) from exc
            result.append(
                {
                    "molecule_index": molecule_index,
                    "ring_index": ring_index,
                    "atom_indices": sorted_global,
                    "cycle_atom_indices": cycle_global,
                    "is_aromatic": bool(getattr(ring, "is_aromatic", False)),
                    "normal": tuple(float(value) for value in ring.normal),
                }
            )
    return result


def _source_matrix(source: Any) -> Any:
    for candidate in ("matrix", "cell", "lattice"):
        value = getattr(source, candidate, None)
        if value is not None:
            if hasattr(value, "array"):
                return np.asarray(value.array)
            if hasattr(value, "matrix"):
                return np.asarray(value.matrix)
            return np.asarray(value)
    atoms = getattr(source, "atoms", None)
    if atoms is not None and hasattr(atoms, "cell"):
        return np.asarray(atoms.cell)
    return None


def _value(record: Any, *names: str, default: Any = ...):
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    if default is not ...:
        return default
    raise ValueError(f"record is missing required field; tried {', '.join(names)}")


def _source_atom_index(atom: Any, fallback_index: int) -> int:
    """Return the topology source index, independent of display ordering."""
    value = _value(
        atom,
        "_source_index",
        "global_index",
        "source_index",
        default=fallback_index,
    )
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback_index)


def _display_copy_key(atom: Any) -> tuple[Any, ...]:
    """Identify one displayed molecular image shared by all its source atoms.

    Formula-unit atoms can carry different crystallographic ``_image_shift``
    values after PBC unwrapping.  Their selection shift is the stable copy
    identity, so it takes precedence over the ordinary boundary-image shift.
    """
    molecule = _value(
        atom,
        "_source_molecule_index",
        "_molecule_index",
        "source_molecule_index",
        "molecule_index",
        default=-1,
    )
    try:
        molecule_index = int(molecule)
    except (TypeError, ValueError):
        molecule_index = -1
    formula_shift = _value(
        atom,
        "_formula_image_shift",
        "formula_image_shift",
        default=None,
    )
    if formula_shift is not None:
        return ("formula", molecule_index, _integer_triplet(formula_shift))
    image_shift = _value(atom, "_image_shift", "image_shift", default=(0, 0, 0))
    return ("image", molecule_index, _integer_triplet(image_shift))


def _integer_triplet(value: Any) -> tuple[int, int, int]:
    try:
        array = np.asarray(value, dtype=int)
    except (TypeError, ValueError):
        return (0, 0, 0)
    if array.shape != (3,):
        return (0, 0, 0)
    return tuple(int(item) for item in array)


def _ring_display_cycles(
    ring: Any,
    source_indices: Sequence[int],
    display_atoms_by_source: Mapping[int, Mapping[tuple[Any, ...], int]],
) -> list[tuple[tuple[Any, ...], list[int]]]:
    """Lift an ordered source cycle to every complete displayed molecule copy."""
    if len(source_indices) < 3:
        return []
    instance_maps = [
        display_atoms_by_source.get(int(index), {}) for index in source_indices
    ]
    if any(not instances for instances in instance_maps):
        return []
    common_keys = set(instance_maps[0])
    for instances in instance_maps[1:]:
        common_keys.intersection_update(instances)

    molecule_hint = _value(
        ring,
        "_source_molecule_index",
        "source_molecule_index",
        "molecule_index",
        default=None,
    )
    if molecule_hint is not None:
        try:
            common_keys = {key for key in common_keys if key[1] == int(molecule_hint)}
        except (TypeError, ValueError):
            common_keys = set()
    shift_hint = _value(
        ring,
        "_formula_image_shift",
        "formula_image_shift",
        "_image_shift",
        "image_shift",
        default=None,
    )
    if shift_hint is not None:
        expected_shift = _integer_triplet(shift_hint)
        common_keys = {key for key in common_keys if key[2] == expected_shift}

    result = []
    for key in sorted(common_keys):
        # Lookup in source-cycle order; never sort atom identifiers.
        display_indices = [int(instances[key]) for instances in instance_maps]
        result.append((key, display_indices))
    return result


def _atom_radius(atom: Any, *, representation: str) -> float:
    value = float(_value(atom, "atom_radius", "radius", default=0.5))
    if representation == "space_filling" or representation == "ball":
        return max(value, 0.2)
    return max(value * 0.46, 0.16)


def _displacement_matrix(atom: Any) -> np.ndarray | None:
    value = _value(atom, "u_cart_A2", "u_cart", "Ucart", "U", "uij_cart", default=None)
    if value is not None:
        matrix = np.asarray(value, dtype=float)
        if matrix.shape == (3, 3) and np.all(np.isfinite(matrix)):
            return 0.5 * (matrix + matrix.T)
    uiso = _value(atom, "uiso_A2", "uiso", "u_iso", default=None)
    if uiso is not None and np.isfinite(float(uiso)) and float(uiso) > 0.0:
        return np.eye(3) * float(uiso)
    return None


def _polyhedron_primitives(
    scene: Mapping[str, Any], topology_data: Mapping[str, Any] | None
) -> list[Primitive]:
    results: list[Primitive] = []
    explicit = list(scene.get("polyhedra") or [])
    if topology_data:
        for specification in topology_data.get("spec_results") or []:
            for overlay in specification.get("overlays") or []:
                explicit.append(
                    {
                        "vertices": overlay.get("shell_coords"),
                        "faces": (overlay.get("hull") or {}).get("simplices"),
                        "color": overlay.get("color")
                        or specification.get("color")
                        or "#7C5CBF",
                        "opacity": specification.get("opacity", 0.55),
                        "edge_opacity": specification.get("edge_opacity", 0.9),
                        "spec_id": specification.get("spec_id"),
                    }
                )
    for index, item in enumerate(explicit):
        vertices = _value(item, "vertices", "shell_coords", default=[])
        faces = _value(item, "faces", "simplices", default=[])
        if vertices is None or faces is None or len(vertices) == 0 or len(faces) == 0:
            continue
        semantic_id = f"polyhedron:{index}:{_value(item, 'spec_id', default='')}"
        color = _value(item, "color", default="#7C5CBF")
        results.append(
            polyhedron_primitive(
                semantic_id,
                vertices,
                faces,
                color,
                alpha=float(_value(item, "opacity", default=0.55)),
                metadata={
                    "kind": "polyhedron",
                    "spec_id": _value(item, "spec_id", default=None),
                },
            )
        )
        results.append(
            polyhedron_edges_primitive(
                f"{semantic_id}:edges",
                vertices,
                faces,
                color,
                alpha=float(_value(item, "edge_opacity", default=0.9)),
            )
        )
    return results


def _isosurface_primitives(
    scene: Mapping[str, Any],
) -> tuple[list[TriangleMeshPrimitive], list[str]]:
    """Consume meshes prepared lazily by the optional cube adapter."""
    entries = scene.get("isosurfaces")
    cube_data = scene.get("cube_data")
    if entries is None and cube_data is not None:
        for name in ("isosurfaces", "surface_meshes"):
            candidate = (
                cube_data.get(name)
                if isinstance(cube_data, Mapping)
                else getattr(cube_data, name, None)
            )
            if candidate is not None:
                entries = candidate
                break
    if entries is None:
        if cube_data is not None:
            raise RuntimeError(
                "cube_data has no prepared isosurface meshes; the [cube] adapter "
                "must lazily run marching cubes and populate scene['isosurfaces']"
            )
        return [], []

    results: list[TriangleMeshPrimitive] = []
    warnings: list[str] = []
    for index, entry in enumerate(entries):
        if isinstance(entry, Mapping):
            vertices = entry.get("vertices")
            if vertices is None:
                vertices = entry.get("verts")
            triangles = entry.get("triangles")
            if triangles is None:
                triangles = entry.get("faces")
            normals = entry.get("normals")
            name = str(entry.get("id") or entry.get("name") or index)
            color = entry.get("color", "#D55E00" if index == 0 else "#0072B2")
            opacity = float(entry.get("opacity", 0.55))
            metadata = {
                "kind": "isosurface",
                "name": name,
                "phase": entry.get("phase"),
                "level": entry.get("level"),
            }
        elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
            vertices, triangles = entry[0], entry[1]
            normals = entry[2] if len(entry) >= 3 else None
            name = str(index)
            color = "#D55E00" if index == 0 else "#0072B2"
            opacity = 0.55
            metadata = {"kind": "isosurface", "name": name}
        else:
            warnings.append(
                f"isosurface {index} has an unsupported mesh record and was skipped"
            )
            continue
        if vertices is None or triangles is None:
            warnings.append(f"isosurface {name} has no vertices/faces and was skipped")
            continue
        vertex_array = np.asarray(vertices, dtype=float)
        triangle_array = np.asarray(triangles, dtype=np.int64)
        if len(vertex_array) == 0 or len(triangle_array) == 0:
            warnings.append(f"isosurface {name} is empty and was skipped")
            continue
        results.append(
            mesh_primitive(
                f"isosurface:{index}:{name}",
                vertex_array,
                triangle_array,
                color,
                normals=normals,
                alpha=opacity,
                metadata=metadata,
            )
        )
    return results, warnings


def _fit_camera(
    primitives: Sequence[Primitive],
    *,
    width: int,
    height: int,
) -> CameraSpec:
    points: list[np.ndarray] = []
    for primitive in primitives:
        if isinstance(primitive, TriangleMeshPrimitive):
            points.append(primitive.vertices)
        elif isinstance(primitive, LinePrimitive):
            points.append(primitive.segments.reshape(-1, 3))
        elif isinstance(primitive, TextPrimitive):
            points.append(np.asarray([primitive.position]))
    if not points:
        return CameraSpec()
    combined = np.vstack(points)
    minimum, maximum = combined.min(axis=0), combined.max(axis=0)
    target = 0.5 * (minimum + maximum)
    radius = max(float(np.linalg.norm(combined - target, axis=1).max()), 0.5)
    direction = np.asarray([1.0, 1.0, 0.8])
    direction /= np.linalg.norm(direction)
    distance = max(radius * 3.2, 2.0)
    aspect = float(width) / float(height)
    ortho_scale = radius * 1.18 / min(1.0, aspect)
    return CameraSpec.looking_along(
        direction,
        target=target,
        distance=distance,
        projection="orthographic",
        ortho_scale=ortho_scale,
        near=max(1e-3, distance - radius * 2.2),
        far=distance + radius * 2.2,
    )


def _assert_unique_ids(primitives: Sequence[Primitive]) -> None:
    seen: set[str] = set()
    for primitive in primitives:
        if primitive.semantic_id in seen:
            raise ValueError(
                f"duplicate primitive semantic_id: {primitive.semantic_id}"
            )
        seen.add(primitive.semantic_id)


__all__ = ["prepare_render"]
