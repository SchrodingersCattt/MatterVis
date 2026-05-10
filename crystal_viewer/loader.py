from __future__ import annotations

import base64
import copy
from collections import defaultdict
import os
import re
import tempfile
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Optional

import networkx as nx
import numpy as np
from molcrys_kit.utils.geometry import unwrap_positions_along_bonds

from .presets import get_default_catalog, workspace_root
from . import molcrys_bridge
from .scene import build_scene_from_atoms, legacy_scene, pc, scene_json, scene_metadata, scene_ops


@dataclass
class LoadedCrystal:
    name: str
    title: str
    cif_path: str
    scene: Dict[str, Any]
    raw_atoms: list[dict[str, Any]] = field(default_factory=list)
    cell: Any | None = None
    M: Any | None = None
    view_direction: list[float] = field(default_factory=list)
    up: list[float] = field(default_factory=list)
    scene_cache: dict[tuple[str, bool], Dict[str, Any]] = field(default_factory=dict)
    pymatgen_structure: Any | None = None
    crystal: Any | None = None
    molcrys_analysis: Any | None = None
    formula_unit_atoms: list[dict[str, Any]] = field(default_factory=list)
    unwrapped_atoms: list[dict[str, Any]] = field(default_factory=list)
    unwrap_overflow: list[list[int]] = field(default_factory=list)
    fragment_table: list[dict[str, Any]] = field(default_factory=list)
    topology_fragment_table: list[dict[str, Any]] = field(default_factory=list)
    fragment_table_cache: dict[tuple[Any, ...], tuple[list[dict[str, Any]], list[str]]] = field(default_factory=dict)
    atom_fragment_labels: list[str] = field(default_factory=list)
    source: str = "catalog"

    def metadata(self) -> Dict[str, Any]:
        meta = scene_metadata(self.scene)
        meta.update({
            "source": self.source,
            "fragment_count": len(self.topology_fragment_table or self.fragment_table),
            "has_topology": bool(self.topology_fragment_table or self.fragment_table),
        })
        return meta


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "uploaded"


def _unique_name(base: str, existing: Iterable[str]) -> str:
    existing_set = set(existing)
    if base not in existing_set:
        return base
    idx = 2
    while f"{base}_{idx}" in existing_set:
        idx += 1
    return f"{base}_{idx}"


def _infer_title_from_scene(scene: Dict[str, Any]) -> str:
    title = scene.get("title")
    if title:
        return str(title)
    return scene.get("name", "Uploaded Structure")


def build_empty_bundle(
    *,
    name: str = "__upload__",
    title: str = "Upload CIF to begin",
) -> LoadedCrystal:
    cell = SimpleNamespace(
        a=1.0,
        b=1.0,
        c=1.0,
        alpha=90.0,
        beta=90.0,
        gamma=90.0,
        volume=1.0,
    )
    M = np.eye(3, dtype=float)
    R = np.eye(3, dtype=float)
    scene = {
        "name": name,
        "title": title,
        "cell": cell,
        "M": M,
        "R": R,
        "view_x": np.array([1.0, 0.0, 0.0], dtype=float),
        "view_y": np.array([0.0, 1.0, 0.0], dtype=float),
        "view_z": np.array([0.0, 0.0, 1.0], dtype=float),
        "selected_atoms": [],
        "draw_atoms": [],
        "bonds": [],
        "label_items": [],
        "bounds": {
            "center": [0.0, 0.0, 0.0],
            "ranges": [1.0, 1.0, 1.0],
            "mins": [0.0, 0.0, 0.0],
            "maxs": [1.0, 1.0, 1.0],
            "screen_ranges": [1.0, 1.0, 1.0],
        },
        "camera": {
            "position": [0.0, 0.0, 8.0],
            "focal_point": [0.0, 0.0, 0.0],
            "up": [0.0, 1.0, 0.0],
        },
        "style": {},
        "show_hydrogen": False,
        "has_minor": False,
        "preset_entry": {},
        "display_mode": "formula_unit",
        "cif_path": None,
        "view_direction": np.array([0.0, 0.0, 1.0], dtype=float),
        "up": np.array([0.0, 1.0, 0.0], dtype=float),
        "fragment_table": [],
        "atom_fragment_labels": [],
        "unwrap_overflow": [],
    }
    return LoadedCrystal(
        name=name,
        title=title,
        cif_path="",
        scene=scene,
        raw_atoms=[],
        cell=cell,
        M=M,
        view_direction=[0.0, 0.0, 1.0],
        up=[0.0, 1.0, 0.0],
        scene_cache={("formula_unit", False): scene},
        fragment_table=[],
        topology_fragment_table=[],
        fragment_table_cache={("scene", "formula_unit", False): ([], [])},
        atom_fragment_labels=[],
        source="placeholder",
    )


def _cluster_components(n_items: int, pairs: Iterable[tuple[int, int]]) -> list[list[int]]:
    parents = list(range(n_items))

    def find(idx: int) -> int:
        while parents[idx] != idx:
            parents[idx] = parents[parents[idx]]
            idx = parents[idx]
        return idx

    def union(a: int, b: int) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parents[ra] = rb

    for i, j in pairs:
        union(int(i), int(j))

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n_items):
        groups[find(idx)].append(idx)
    return [sorted(group) for _, group in sorted(groups.items(), key=lambda item: min(item[1]))]


DEFAULT_UNWRAP_MAX_ATOMS = 500


def _unwrap_atom_pool(
    atoms: list[dict[str, Any]],
    bond_pairs: Iterable[tuple[int, int]],
    cell,
    M,
    components: Iterable[list[int]],
    *,
    max_atoms: int | None = DEFAULT_UNWRAP_MAX_ATOMS,
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    out = [dict(atom) for atom in atoms]
    graph = nx.Graph()
    graph.add_nodes_from(range(len(out)))
    for i, j in bond_pairs:
        i = int(i)
        j = int(j)
        start = np.asarray(out[i]["cart"], dtype=float)
        near = np.asarray(pc._nearest_pbc_cart(out[i]["cart"], out[j]["cart"], cell), dtype=float)
        vector = near - start if i < j else start - near
        graph.add_edge(i, j, vector=vector)

    positions = np.asarray([atom["cart"] for atom in out], dtype=float)
    inv_m = np.linalg.inv(np.asarray(M, dtype=float))
    overflow: list[list[int]] = []
    for component in components:
        unwrapped, completed = unwrap_positions_along_bonds(
            graph,
            component,
            positions,
            max_atoms=max_atoms,
        )
        if not completed:
            overflow.append(list(component))
            continue
        for local_idx, atom_idx in enumerate(component):
            out[atom_idx]["cart"] = unwrapped[local_idx]
            out[atom_idx]["frac"] = inv_m @ unwrapped[local_idx]
            out[atom_idx]["_unwrapped"] = True
    return out, overflow


def _unwrapped_atoms_from_atoms(
    atoms,
    cell,
    M,
    *,
    include_minor: bool = True,
    max_atoms: int | None = DEFAULT_UNWRAP_MAX_ATOMS,
) -> tuple[list[dict[str, Any]], list[list[int]]]:
    ops = scene_ops()
    atom_pool = []
    source_indices = []
    for idx, atom in enumerate(atoms):
        copied = dict(atom)
        copied["_source_index"] = idx
        copied["_unwrapped"] = False
        if ops.is_minor(copied) and not include_minor:
            continue
        atom_pool.append(copied)
        source_indices.append(idx)
    if not atom_pool:
        return [], []

    bond_pairs = ops.find_bonds(atom_pool, cell=cell)
    components = _cluster_components(len(atom_pool), bond_pairs)
    unwrapped_pool, overflow_local = _unwrap_atom_pool(
        atom_pool,
        bond_pairs,
        cell,
        M,
        components,
        max_atoms=max_atoms,
    )

    unwrapped_atoms = [dict(atom) for atom in atoms]
    for atom in unwrapped_atoms:
        atom["_unwrapped"] = False
    for local_idx, source_idx in enumerate(source_indices):
        unwrapped_atoms[source_idx] = dict(unwrapped_pool[local_idx])
    overflow = [[source_indices[idx] for idx in component] for component in overflow_local]
    return unwrapped_atoms, overflow


def _fragment_table_from_atoms(
    bundle_name: str,
    atoms,
    cell,
    M,
    *,
    use_source_indices: bool = True,
    include_minor: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    ops = scene_ops()
    atom_pool = []
    source_indices = []
    for idx, atom in enumerate(atoms):
        if ops.is_minor(atom) and not include_minor:
            continue
        atom_pool.append(dict(atom))
        source_indices.append(idx if use_source_indices else len(source_indices))
    if not atom_pool:
        return [], []

    bond_pairs = ops.find_bonds(atom_pool, cell=cell)
    components = _cluster_components(len(atom_pool), bond_pairs)
    atom_pool, _ = _unwrap_atom_pool(atom_pool, bond_pairs, cell, M, components)

    fragments = []
    for component in components:
        site_indices = sorted(source_indices[idx] for idx in component)
        component_atoms = [atom_pool[idx] for idx in component]
        heavy_atoms = [atom for atom in component_atoms if atom["elem"] != "H"]
        center_atoms = heavy_atoms or component_atoms
        elem_set = {atom["elem"] for atom in heavy_atoms}
        if not center_atoms:
            continue
        center_cart = np.mean([atom["cart"] for atom in center_atoms], axis=0)
        center_frac = np.mean([atom["frac"] for atom in center_atoms], axis=0)
        # Disorder-aware heavy-atom counts: atoms that belong to the
        # same SHELX disorder assembly (e.g. PEP's C1/C1A pair, both
        # ``da="B"`` with ``dg`` 1 vs 2) collapse to one chemical
        # carbon, so the displayed formula matches what the molecule
        # actually contains rather than counting both alternatives.
        elem_counts: dict[str, int] = {}
        assemblies: dict[tuple[str, str], dict[str, int]] = {}
        for atom in heavy_atoms:
            elem = atom["elem"]
            da = str(atom.get("da") or ".").strip()
            dg = str(atom.get("dg") or ".").strip()
            if da in ("", ".", "?"):
                elem_counts[elem] = elem_counts.get(elem, 0) + 1
                continue
            bucket = assemblies.setdefault((elem, da), {})
            bucket[dg] = bucket.get(dg, 0) + 1
        for (elem, _da), bucket in assemblies.items():
            elem_counts[elem] = elem_counts.get(elem, 0) + max(bucket.values())
        # Hill-ish ordering for the public formula: C, N, then alphabetical.
        # (Pure mineral fragments without C come out alphabetical.) The result
        # is a stable string identifier we can group on across A/B/X labels --
        # e.g. "C8N1" is the DAP-4 DABCO ring; "N1" is the NH4+.
        ordered: list[tuple[str, int]] = []
        for elem in ("C", "N"):
            if elem in elem_counts:
                ordered.append((elem, elem_counts.pop(elem)))
        ordered.extend(sorted(elem_counts.items()))
        formula = "".join(f"{elem}{count}" if count > 1 else elem for elem, count in ordered) or "?"
        fragments.append({
            "site_indices": site_indices,
            "center": [float(x) for x in center_cart],
            "frac_center": [float(x) for x in center_frac],
            "elem_set": sorted(elem_set),
            "heavy_atom_count": len(heavy_atoms),
            "cluster_size": len(component_atoms),
            "species": "".join(sorted(elem_set)) or "?",
            "formula": formula,
        })

    x_fragments = [frag for frag in fragments if "Cl" in frag["elem_set"]]
    non_x = [frag for frag in fragments if frag not in x_fragments]

    # A vs B classification follows the molecular-perchlorate convention
    # A2B(ClO4)4: B is the *smaller* non-X cluster. This handles three cases:
    #   1. Real metal B-site: single heavy atom (size = 1) is the smallest by
    #      definition -> B. Organic cations are bigger -> A.
    #   2. Pure organic salt with two distinct cation sizes (e.g. PEP has
    #      heavy=4 and heavy=6 cations): smallest -> B, larger -> A.
    #   3. Pure organic salt with a single cation type (e.g. DAP-4 has two
    #      identical heavy=8 cations): only one size class exists -> all A.
    # Non-organic, non-X clusters (e.g. lone halide counterions) fall through
    # to "?" so they don't pollute either A or B.
    NON_METAL_HEAVY = {
        "H", "B", "C", "N", "O", "F",
        "Si", "P", "S", "Cl",
        "Ge", "As", "Se", "Br",
        "Sb", "Te", "I",
    }
    organic_or_metal = []
    for frag in non_x:
        elems = set(frag["elem_set"])
        is_single_metal = frag["heavy_atom_count"] == 1 and not (elems & NON_METAL_HEAVY)
        is_organic = bool(elems & {"C", "N"})
        if is_single_metal or is_organic:
            organic_or_metal.append(frag)
        else:
            frag["type"] = "?"
    if organic_or_metal:
        sizes = sorted({frag["heavy_atom_count"] for frag in organic_or_metal})
        if len(sizes) >= 2:
            smallest = sizes[0]
            for frag in organic_or_metal:
                frag["type"] = "B" if frag["heavy_atom_count"] == smallest else "A"
        else:
            for frag in organic_or_metal:
                frag["type"] = "A"
    for frag in x_fragments:
        frag["type"] = "X"

    type_order = {"B": 0, "A": 1, "X": 2, "?": 3}
    fragments.sort(
        key=lambda frag: (
            type_order.get(frag["type"], 9),
            *[float(x % 1.0) for x in frag["frac_center"]],
            frag["heavy_atom_count"],
            frag["cluster_size"],
        )
    )

    counters: dict[str, int] = defaultdict(int)
    atom_fragment_labels = ["?"] * len(atoms)
    final_table = []
    for frag_idx, frag in enumerate(fragments):
        frag_type = frag["type"]
        label_index = counters[frag_type]
        counters[frag_type] += 1
        for site_idx in frag["site_indices"]:
            atom_fragment_labels[site_idx] = frag_type
        final_table.append({
            "index": frag_idx,
            "type": frag_type,
            "label": f"{frag_type}{label_index}",
            "species": frag["species"],
            "formula": frag.get("formula"),
            "elem_set": frag.get("elem_set", []),
            "center": frag["center"],
            "frac_center": frag["frac_center"],
            "site_indices": frag["site_indices"],
            "source": bundle_name,
            "heavy_atom_count": frag["heavy_atom_count"],
            "cluster_size": frag["cluster_size"],
        })
    return final_table, atom_fragment_labels


def build_bundle_scene(
    bundle: LoadedCrystal,
    *,
    display_mode: str = "formula_unit",
    show_hydrogen: bool = False,
    preset: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cache_key = (display_mode, bool(show_hydrogen))
    if cache_key in bundle.scene_cache:
        return bundle.scene_cache[cache_key]

    ops = scene_ops()
    view_dir = np.array(bundle.view_direction, dtype=float)
    up = np.array(bundle.up, dtype=float)
    R = ops.view_rotation(view_dir, up)
    scene = build_scene_from_atoms(
        name=bundle.name,
        title=bundle.title,
        atoms=bundle.raw_atoms,
        cell=bundle.cell,
        M=bundle.M,
        R=R,
        show_hydrogen=show_hydrogen,
        preset=preset,
        display_mode=display_mode,
        ops=ops,
        formula_unit_atoms=bundle.formula_unit_atoms if display_mode == "formula_unit" else None,
        unwrapped_atoms=bundle.unwrapped_atoms,
    )
    scene["cif_path"] = bundle.cif_path
    scene["view_direction"] = view_dir
    scene["up"] = up
    scene["unwrap_overflow"] = copy.deepcopy(bundle.unwrap_overflow)
    fragment_cache_key = ("scene", display_mode, bool(show_hydrogen))
    cached_fragments = bundle.fragment_table_cache.get(fragment_cache_key)
    if cached_fragments is None:
        fragment_table, atom_fragment_labels = _fragment_table_from_atoms(
            bundle.name,
            scene["draw_atoms"],
            scene["cell"],
            scene["M"],
            use_source_indices=False,
            include_minor=True,
        )
        bundle.fragment_table_cache[fragment_cache_key] = (
            copy.deepcopy(fragment_table),
            list(atom_fragment_labels),
        )
    else:
        fragment_table = copy.deepcopy(cached_fragments[0])
        atom_fragment_labels = list(cached_fragments[1])
    scene["fragment_table"] = fragment_table
    scene["atom_fragment_labels"] = atom_fragment_labels
    bundle.scene_cache[cache_key] = scene
    return scene


def build_loaded_crystal(
    *,
    name: str,
    cif_path: str,
    title: Optional[str] = None,
    preset: Optional[Dict[str, Any]] = None,
    source: str = "catalog",
) -> LoadedCrystal:
    # Each sub-block is wrapped in a ``perf_log.time_block`` so the
    # /api/v1/perf endpoint shows exactly which leg of an upload is
    # slow (CIF parse vs. molcryskit analysis vs. bond perception
    # vs. fragment-table build). See ``crystal_viewer.perf_log``.
    from . import perf_log

    ops = scene_ops()
    preset = preset or {}
    with perf_log.time_block("loader:parse_asu", kind="event", structure=name, cif_path=cif_path):
        raw_atoms, cell, M = ops.parse_asu(cif_path)
    n_atoms = len(raw_atoms) if raw_atoms is not None else 0
    with perf_log.time_block(
        "loader:molcrys_analyze",
        kind="event",
        structure=name,
        n_atoms=n_atoms,
    ):
        molcrys_analysis = molcrys_bridge.analyze(raw_atoms, M)
    with perf_log.time_block("loader:select_formula_unit", kind="event", structure=name):
        formula_unit_atoms = molcrys_bridge.select_formula_unit(raw_atoms, M, analysis=molcrys_analysis)
    with perf_log.time_block("loader:unwrap_atoms", kind="event", structure=name):
        unwrapped_atoms, unwrap_overflow = _unwrapped_atoms_from_atoms(raw_atoms, cell, M, include_minor=True)
    # ``_resolve_view`` is happy to short-circuit on a preset entry
    # (camera or view_direction explicitly provided) but otherwise
    # falls through to ``ops.auto_view_dir`` which scores >1000 view
    # candidates by ray-projecting every heavy atom -- ~12 s for a
    # 1024-atom unit cell. For uploaded CIFs there is no preset
    # entry to short-circuit on, so the user paid that cost on every
    # upload. The browser camera is fully interactive so a sensible
    # default direction (look down +z, up = +y) gives a usable initial
    # view in <1 ms; users that want the full auto-orient can call
    # the v2 API or add a preset entry. Catalog structures keep the
    # legacy behaviour because their preset can pin a known-good
    # camera, and the cost is paid once at boot, not per upload.
    is_upload = source == "upload"
    if is_upload:
        with perf_log.time_block("loader:default_view", kind="event", structure=name, reason="skip_auto_view_for_upload"):
            view_dir = np.array([0.0, 0.0, 1.0])
            up = np.array([0.0, 1.0, 0.0])
    else:
        with perf_log.time_block("loader:resolve_view", kind="event", structure=name):
            view_dir, up = legacy_scene._resolve_view(ops, name, raw_atoms, M, cell, preset)
    R = ops.view_rotation(view_dir, up)
    final_title = title or name
    with perf_log.time_block(
        "loader:build_scene_from_atoms",
        kind="event",
        structure=name,
        n_atoms=n_atoms,
    ):
        initial_scene = build_scene_from_atoms(
            name=name,
            title=final_title,
            atoms=raw_atoms,
            cell=cell,
            M=M,
            R=R,
            preset=preset,
            show_hydrogen=False,
            display_mode="formula_unit",
            ops=ops,
            formula_unit_atoms=formula_unit_atoms,
            unwrapped_atoms=unwrapped_atoms,
        )
    initial_scene["cif_path"] = cif_path
    initial_scene["view_direction"] = np.array(view_dir, dtype=float)
    initial_scene["up"] = np.array(up, dtype=float)
    initial_scene["unwrap_overflow"] = copy.deepcopy(unwrap_overflow)
    with perf_log.time_block(
        "loader:fragment_table_scene",
        kind="event",
        structure=name,
    ):
        fragment_table, atom_fragment_labels = _fragment_table_from_atoms(
            name,
            initial_scene["draw_atoms"],
            initial_scene["cell"],
            initial_scene["M"],
            use_source_indices=False,
            include_minor=True,
        )
    initial_scene["fragment_table"] = fragment_table
    initial_scene["atom_fragment_labels"] = atom_fragment_labels
    with perf_log.time_block(
        "loader:fragment_table_topology",
        kind="event",
        structure=name,
    ):
        topology_fragment_table, _ = _fragment_table_from_atoms(name, raw_atoms, cell, M, use_source_indices=True, include_minor=True)
    fragment_table_cache = {
        ("scene", "formula_unit", False): (
            copy.deepcopy(fragment_table),
            list(atom_fragment_labels),
        ),
        ("topology",): (
            copy.deepcopy(topology_fragment_table),
            [],
        ),
    }

    bundle = LoadedCrystal(
        name=name,
        title=final_title,
        cif_path=cif_path,
        scene=initial_scene,
        raw_atoms=[dict(atom) for atom in raw_atoms],
        cell=cell,
        M=M,
        view_direction=np.array(view_dir, dtype=float).tolist(),
        up=np.array(up, dtype=float).tolist(),
        crystal=molcrys_analysis.crystal,
        molcrys_analysis=molcrys_analysis,
        formula_unit_atoms=[dict(atom) for atom in formula_unit_atoms],
        unwrapped_atoms=[dict(atom) for atom in unwrapped_atoms],
        unwrap_overflow=[list(component) for component in unwrap_overflow],
        scene_cache={("formula_unit", False): initial_scene},
        fragment_table=fragment_table,
        topology_fragment_table=topology_fragment_table,
        fragment_table_cache=fragment_table_cache,
        atom_fragment_labels=atom_fragment_labels,
        source=source,
    )
    return bundle


def load_default_catalog(
    *,
    root_dir: Optional[str] = None,
    names: Optional[Iterable[str]] = None,
    preset: Optional[Dict[str, Any]] = None,
) -> Dict[str, LoadedCrystal]:
    catalog = get_default_catalog(root_dir=root_dir or workspace_root())
    selected = list(names) if names else list(catalog.keys())
    loaded = {}
    for name in selected:
        entry = catalog[name]
        loaded[name] = build_loaded_crystal(
            name=name,
            cif_path=entry["cif_path"],
            title=entry["title"],
            preset=preset,
            source="catalog",
        )
    return loaded


def infer_uploaded_name(filename: str, existing_names: Iterable[str]) -> str:
    stem = os.path.splitext(os.path.basename(filename))[0]
    return _unique_name(_slugify(stem), existing_names)


def write_uploaded_cif(contents: str, filename: str, upload_dir: Optional[str] = None) -> str:
    if not contents.startswith("data:"):
        raise ValueError("Dash upload contents must be a data URL.")
    header, encoded = contents.split(",", 1)
    if "base64" not in header:
        raise ValueError("Only base64 CIF uploads are supported.")
    data = base64.b64decode(encoded)
    target_dir = upload_dir or os.path.join(tempfile.gettempdir(), "crystal_viewer_uploads")
    os.makedirs(target_dir, exist_ok=True)
    safe_name = _slugify(filename)
    path = os.path.join(target_dir, safe_name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def load_uploaded_cif(
    *,
    contents: str,
    filename: str,
    existing_names: Iterable[str],
    preset: Optional[Dict[str, Any]] = None,
    upload_dir: Optional[str] = None,
) -> LoadedCrystal:
    cif_path = write_uploaded_cif(contents, filename, upload_dir=upload_dir)
    name = infer_uploaded_name(filename, existing_names)
    title = os.path.splitext(os.path.basename(filename))[0]
    return build_loaded_crystal(name=name, cif_path=cif_path, title=title, preset=preset, source="upload")


def bundle_json(bundle: LoadedCrystal) -> Dict[str, Any]:
    return {
        "name": bundle.name,
        "title": bundle.title,
        "cif_path": bundle.cif_path,
        "scene": scene_json(bundle.scene),
        "fragment_table": copy.deepcopy(bundle.fragment_table),
        "topology_fragment_table": copy.deepcopy(bundle.topology_fragment_table),
        "unwrap_overflow": copy.deepcopy(bundle.unwrap_overflow),
        "source": bundle.source,
    }
