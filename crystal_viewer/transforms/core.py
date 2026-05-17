"""Scene-level geometric transforms (Phase 4: repeat / grow / slab).

A *transform* takes a base scene dict (the output of
:func:`crystal_viewer.scene.build_scene_from_atoms`) and returns a
new scene dict whose ``draw_atoms`` / ``bonds`` / ``fragment_table``
reflect the transform applied. Transforms compose in list order via
:func:`apply_transforms`.

Layered API (per ``AGENTS.md``):

1. **Pure math primitives** -- operate on plain Python lists of atom
   dicts and a 3x3 row-lattice ``M`` (rows = a, b, c vectors).

   * :func:`replicate_atoms` -- ``Na x Nb x Nc`` supercell of a flat
     atom list. Returns the new atoms plus a list of ``image_shift``
     tags so callers can group/colour by replica.
   * :func:`atoms_within_radius` -- pick neighbouring periodic-image
     atoms whose distance to any seed is below ``radius``.
   * :func:`atoms_within_bonds` -- bond-walk ``hops`` steps outward
     from a set of seed atoms, including periodic images.
   * :func:`atoms_completing_fragment` -- expand seeds to the full
     bonded cluster across cell boundaries.
   * :func:`atoms_completing_polyhedron` -- around each seed centre,
     pull in image atoms that fall inside the requested coordination
     cutoff so the convex-hull polyhedron is closed.
   * :func:`atoms_under_symmetry` -- map each seed by every space-group
     operation in ``sym_ops`` (each ``(R, t)``).

2. **Composable building blocks**:

   * :func:`rebuild_scene_with_atoms` -- given a base scene dict and a
     fresh ``draw_atoms`` list, re-detect bonds (no PBC, all atoms are
     manifested), recompute bounds, label items, fragment table, then
     return a new scene dict with the same metadata as the base
     scene.
   * :func:`apply_one_transform` -- dispatch one transform-spec dict
     onto a scene; returns a new scene dict.

3. **User-facing wrappers**:

   * :func:`apply_transforms` -- apply a list of transform specs in
     order to a base scene; the result is the rendered scene.

The renderer / Dash app are wired in via
:meth:`ViewerBackend.scene_for_state`, which calls
:func:`apply_transforms` on the cached base scene. Transform results
are cached on the bundle keyed on a stable hash of the transform list
so a sidebar checkbox toggle is a hash-lookup, not a recompute.

Transform spec shape (also documented in
``agents/transforms_api.md``)::

    {
        "id": "<stable id, auto-generated>",
        "name": "<display label>",
        "kind": "repeat" | "grow_radius" | "grow_bonds"
              | "complete_fragment" | "complete_polyhedron"
              | "by_symmetry" | "slab",
        "params": { ... kind-specific ... },
        "enabled": True,
    }

The ``params`` schema per ``kind``:

* ``repeat``: ``{"a": int, "b": int, "c": int}`` -- supercell counts
  along each lattice direction. Always >= 1; negative / 0 silently
  clamped to 1.
* ``grow_radius``: ``{"seeds": <selector>, "radius": float}`` --
  radius in Angstroms.
* ``grow_bonds``: ``{"seeds": <selector>, "hops": int}`` -- bond
  graph walks outward across cells.
* ``complete_fragment``: ``{"seeds": <selector>}`` -- expand each
  seed's bonded cluster across cells.
* ``complete_polyhedron``: ``{"seeds": <selector>, "cutoff": float}``.
* ``by_symmetry``: ``{"seeds": <selector>, "ops": [[[r11..r33], [tx,ty,tz]], ...]}``
  where each op is a 3x3 rotation matrix (in fractional coords) plus
  a 3-vector translation (also fractional).
* ``slab``: ``{"miller": [h, k, l], "layers": int|None,
  "min_thickness": float|None, "vacuum": float}`` -- delegates to
  ``molcrys_kit.operations.surface.generate_topological_slab``.

The ``seeds`` selector mirrors :mod:`crystal_viewer.atom_groups`:

* ``{"all": True}``
* ``{"labels": ["Pb1", "C1"]}``
* ``{"indices": [0, 5]}``
* ``{"elements": ["Pb"]}``

Caching contract: every transform writes a deterministic
``_transform_lineage`` list onto the returned scene so callers can
reason about which transforms produced which atoms (e.g. for the
"x in supercell replica [1,0,1]" tooltip).
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from molcrys_kit.utils.geometry import cart_to_frac, frac_to_cart

from ..disorder import atom_is_minor, bond_is_minor


# Transform kinds the dispatcher recognises. Anything else is rejected
# at the normaliser layer (api.py) before reaching this module so the
# renderer never sees an unknown kind.
KNOWN_TRANSFORM_KINDS = (
    "repeat",
    "grow_radius",
    "grow_bonds",
    "complete_fragment",
    "complete_polyhedron",
    "by_symmetry",
    "slab",
)

# How many atoms we will ever materialise after a transform pipeline.
# A 4x4x4 supercell of a 200-atom unit cell is 12 800 atoms, which is
# still inside the renderer's interactive budget once material=flat is
# selected. Beyond that the round-trip JSON cost dominates and the
# user is probably better served by a screenshot-only batch script.
MAX_ATOMS_AFTER_TRANSFORM = 50000

# Maximum periodic-image search range we will sweep when growing or
# completing polyhedra. The neighbour search is O(Na*Nb*Nc); 4 in each
# direction = 729 image cells, which is already overkill for any
# realistic coordination shell. Callers can tune via the explicit
# ``cutoff`` parameter; this is the hard ceiling.
MAX_IMAGE_RANGE = 4


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------


def resolve_seed_indices(
    atoms: Sequence[Dict[str, Any]],
    seeds: Optional[Dict[str, Any]],
) -> List[int]:
    """Return the list of atom indices in ``atoms`` matching ``seeds``.

    The selector grammar mirrors :mod:`crystal_viewer.atom_groups` so
    a caller who already wrote an atom-group rule can reuse the dict
    verbatim as a transform seed.

    A ``None`` / empty selector matches **no atoms** (callers must opt
    in explicitly with ``{"all": True}`` to operate on everything).
    """
    if not seeds or not isinstance(seeds, dict):
        return []
    if seeds.get("all"):
        return list(range(len(atoms)))
    out: List[int] = []
    label_set = {str(item) for item in seeds.get("labels", []) or []}
    index_set = {int(item) for item in seeds.get("indices", []) or []}
    element_set = {str(item) for item in seeds.get("elements", []) or []}
    for idx, atom in enumerate(atoms):
        if idx in index_set:
            out.append(idx)
            continue
        label = str(atom.get("label") or "")
        if label and label in label_set:
            out.append(idx)
            continue
        elem = str(atom.get("elem") or "")
        if elem and elem in element_set:
            out.append(idx)
            continue
    return sorted(set(out))


# ---------------------------------------------------------------------------
# Pure math: replicate / radius / bonds / fragment / polyhedron / symmetry
# ---------------------------------------------------------------------------


def _atom_copy(atom: Dict[str, Any], *, image_shift: Tuple[int, int, int],
               new_label_suffix: Optional[str] = None) -> Dict[str, Any]:
    """Deep-enough copy of ``atom`` with a fresh label and image_shift tag.

    We copy the dict and the cart/frac numpy arrays so callers can
    mutate the copy without aliasing the source. Other nested objects
    (e.g. ``U`` ADP tensors, when present) are shared by reference --
    they are immutable for our purposes.
    """
    new = dict(atom)
    new["cart"] = np.asarray(atom["cart"], dtype=float).copy()
    if "frac" in atom and atom["frac"] is not None:
        new["frac"] = np.asarray(atom["frac"], dtype=float).copy()
    new["_image_shift"] = tuple(int(x) for x in image_shift)
    base_label = str(atom.get("label") or "")
    if image_shift != (0, 0, 0):
        suffix = new_label_suffix or f"[{image_shift[0]},{image_shift[1]},{image_shift[2]}]"
        new["label"] = f"{base_label}{suffix}" if base_label else suffix
    else:
        new["label"] = base_label
    new["_origin_label"] = base_label
    return new


def _shift_cart(M: np.ndarray, image_shift: Tuple[int, int, int]) -> np.ndarray:
    M = np.asarray(M, dtype=float)
    shift_frac = np.array(image_shift, dtype=float)
    return frac_to_cart(shift_frac, M)


def replicate_atoms(
    atoms: Sequence[Dict[str, Any]],
    M: np.ndarray,
    *,
    na: int,
    nb: int,
    nc: int,
) -> List[Dict[str, Any]]:
    """``Na x Nb x Nc`` supercell of ``atoms``.

    The replica with image_shift ``(0,0,0)`` keeps the original atom
    labels untouched so existing atom_groups rules and click handlers
    keep working unchanged. Every other replica gets a label suffix
    ``"[na,nb,nc]"`` so labels stay unique.
    """
    na = max(1, int(na))
    nb = max(1, int(nb))
    nc = max(1, int(nc))
    M_arr = np.asarray(M, dtype=float)
    out: List[Dict[str, Any]] = []
    for ia in range(na):
        for ib in range(nb):
            for ic in range(nc):
                shift = (ia, ib, ic)
                shift_cart = _shift_cart(M_arr, shift)
                for atom in atoms:
                    new = _atom_copy(atom, image_shift=shift)
                    new["cart"] = new["cart"] + shift_cart
                    if "frac" in new and new["frac"] is not None:
                        new["frac"] = new["frac"] + np.array(shift, dtype=float)
                    out.append(new)
    return out


def _periodic_image_grid(
    M: np.ndarray,
    radius: float,
    *,
    max_range: int = MAX_IMAGE_RANGE,
) -> List[Tuple[int, int, int]]:
    """Return the ``(na, nb, nc)`` image shifts whose cell origin lies
    within ``radius + ||longest cell vector||`` of the home cell. We
    can't know the per-atom offset cheaply, so we over-shoot: any
    atom in the home cell whose position lies within ``radius`` of a
    seed atom in the home cell is reachable from one of these images.
    """
    M_arr = np.asarray(M, dtype=float)
    spans: List[int] = []
    for axis in range(3):
        length = float(np.linalg.norm(M_arr[axis]))
        if length < 1e-9:
            spans.append(0)
            continue
        spans.append(min(max_range, max(1, int(math.ceil((radius + length) / length)))))
    shifts: List[Tuple[int, int, int]] = []
    for ia in range(-spans[0], spans[0] + 1):
        for ib in range(-spans[1], spans[1] + 1):
            for ic in range(-spans[2], spans[2] + 1):
                shifts.append((ia, ib, ic))
    return shifts


def atoms_within_radius(
    atoms: Sequence[Dict[str, Any]],
    M: np.ndarray,
    *,
    seed_indices: Sequence[int],
    radius: float,
    include_seeds: bool = True,
) -> List[Dict[str, Any]]:
    """Return image-shifted copies of ``atoms`` whose Cartesian position
    lies within ``radius`` of any seed atom.

    The home-cell ``(0,0,0)`` versions of the seed atoms themselves are
    returned when ``include_seeds=True`` (default). Other home-cell
    atoms inside the radius come along for the ride too -- a "grow"
    operation that didn't include nearby same-cell atoms would feel
    broken to the user (Diamond's Grow includes them). Duplicates
    (same source atom + same image_shift) are de-duped by their
    ``(_origin_label, _image_shift)`` tuple.
    """
    seed_indices = list(seed_indices)
    if not seed_indices or radius <= 0.0:
        return []
    M_arr = np.asarray(M, dtype=float)
    seed_cart = np.array([atoms[i]["cart"] for i in seed_indices], dtype=float)
    shifts = _periodic_image_grid(M_arr, radius)
    radius_sq = float(radius) * float(radius)
    seen: dict[Tuple[str, Tuple[int, int, int]], Dict[str, Any]] = {}
    base_carts = np.array([atom["cart"] for atom in atoms], dtype=float)
    for shift in shifts:
        shifted = base_carts + _shift_cart(M_arr, shift)
        # Pairwise squared distances between every (seed, atom) pair.
        diff = shifted[:, None, :] - seed_cart[None, :, :]
        d2 = np.sum(diff * diff, axis=-1)
        nearest = d2.min(axis=1)
        in_range = np.where(nearest <= radius_sq)[0]
        for idx in in_range:
            if shift == (0, 0, 0) and not include_seeds and idx in seed_indices:
                continue
            atom = atoms[int(idx)]
            label = str(atom.get("label") or "")
            key = (label, shift)
            if key in seen:
                continue
            new = _atom_copy(atom, image_shift=shift)
            new["cart"] = shifted[int(idx)].copy()
            if "frac" in new and new["frac"] is not None:
                new["frac"] = np.asarray(atom["frac"], dtype=float) + np.array(shift, dtype=float)
            seen[key] = new
    return list(seen.values())


def atoms_within_bonds(
    atoms: Sequence[Dict[str, Any]],
    M: np.ndarray,
    *,
    seed_indices: Sequence[int],
    hops: int,
    ops,
    cell,
) -> List[Dict[str, Any]]:
    """Bond-walk ``hops`` steps outward from each seed.

    We do this geometrically rather than relying on a precomputed bond
    table because seeds may pull in periodic-image atoms that the
    home-cell bond detector never connected. The bond detector is
    re-run on each (atoms-in-frontier, image candidates) batch.
    """
    if hops <= 0 or not seed_indices:
        return []
    seed_set = set(int(i) for i in seed_indices)
    # Use the existing bond detector to find first-neighbour pairs in
    # the home cell + a 1-cell halo each step. We grow incrementally:
    # current frontier -> add bonded neighbours -> repeat ``hops``
    # times. Periodic images in the halo come along automatically.
    typical_bond_len = 3.5  # generous default for inorganic / molecular
    halo_radius = float(typical_bond_len) * float(max(1, hops))
    candidate = atoms_within_radius(
        atoms,
        M,
        seed_indices=list(seed_set),
        radius=halo_radius,
        include_seeds=True,
    )
    # The bond detector wants atoms keyed by integer index, so we walk
    # over the candidate list with explicit indexing.
    bond_pairs = ops.find_bonds(candidate, cell=None)
    label_to_idx = {
        (atom.get("_origin_label") or atom.get("label"), atom.get("_image_shift", (0, 0, 0))): idx
        for idx, atom in enumerate(candidate)
    }
    seed_keys = {
        (atoms[i].get("label"), (0, 0, 0))
        for i in seed_indices
    }
    seed_in_candidate = {
        label_to_idx[key] for key in seed_keys if key in label_to_idx
    }
    if not seed_in_candidate:
        return []
    adj: dict[int, set[int]] = {}
    for i, j in bond_pairs:
        i = int(i)
        j = int(j)
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    visited = set(seed_in_candidate)
    frontier = set(seed_in_candidate)
    for _ in range(int(hops)):
        next_frontier: set[int] = set()
        for node in frontier:
            for neighbour in adj.get(node, ()):
                if neighbour not in visited:
                    next_frontier.add(neighbour)
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
    return [candidate[i] for i in sorted(visited)]


def atoms_completing_fragment(
    atoms: Sequence[Dict[str, Any]],
    M: np.ndarray,
    *,
    seed_indices: Sequence[int],
    ops,
    cell,
    max_hops: int = 64,
) -> List[Dict[str, Any]]:
    """Pull in every atom bonded (transitively) to any seed across cells.

    Implemented as ``atoms_within_bonds`` with a large hop count and
    convergence detection so big organic ligands end up complete even
    when they wrap across multiple cell faces. The hop ceiling
    (``max_hops``) is a safety net against accidentally walking into
    a covalent crystal (graphite / diamond) and exploding the atom
    count.
    """
    if not seed_indices:
        return []
    # Short-circuit: when the seed set already covers every atom in
    # the scene there is no fragment to "complete" -- the user's home
    # cell already holds the full graph. Without this guard the halo
    # below blows up to (3.5 * max_hops) angstrom and the
    # ``atoms_within_radius`` broadcast becomes O(N_atoms^2 * N_cells)
    # which can easily hang the figure render on a supercell.
    if len(set(int(i) for i in seed_indices)) >= len(atoms):
        return [_atom_copy(atom, image_shift=(0, 0, 0)) for atom in atoms]
    typical_bond_len = 3.5
    # Cap the halo so a runaway max_hops doesn't pull in tens of
    # thousands of replicas. 24 angstrom is enough for any sane
    # organic ligand (~6-7 bond hops) and keeps the periodic image
    # grid bounded; the BFS over ``adj`` will still respect the full
    # ``max_hops`` budget for chains that wrap across cells.
    halo_radius = min(typical_bond_len * float(max_hops), 24.0)
    candidate = atoms_within_radius(
        atoms,
        M,
        seed_indices=list(seed_indices),
        radius=halo_radius,
        include_seeds=True,
    )
    if not candidate:
        return []
    bond_pairs = ops.find_bonds(candidate, cell=None)
    seed_keys = {(atoms[i].get("label"), (0, 0, 0)) for i in seed_indices}
    label_to_idx = {
        (atom.get("_origin_label") or atom.get("label"), atom.get("_image_shift", (0, 0, 0))): idx
        for idx, atom in enumerate(candidate)
    }
    seed_in_candidate = {
        label_to_idx[key] for key in seed_keys if key in label_to_idx
    }
    if not seed_in_candidate:
        return []
    adj: dict[int, set[int]] = {}
    for i, j in bond_pairs:
        i = int(i)
        j = int(j)
        adj.setdefault(i, set()).add(j)
        adj.setdefault(j, set()).add(i)
    visited = set(seed_in_candidate)
    frontier = set(seed_in_candidate)
    for _ in range(int(max_hops)):
        next_frontier: set[int] = set()
        for node in frontier:
            next_frontier.update(adj.get(node, ()))
        next_frontier -= visited
        if not next_frontier:
            break
        visited.update(next_frontier)
        frontier = next_frontier
    return [candidate[i] for i in sorted(visited)]


def atoms_completing_polyhedron(
    atoms: Sequence[Dict[str, Any]],
    M: np.ndarray,
    *,
    seed_indices: Sequence[int],
    cutoff: float,
) -> List[Dict[str, Any]]:
    """For each seed centre, return all neighbour atoms within ``cutoff``.

    Used to "close" a coordination polyhedron when the home-cell
    fragment table only kept some of the ligands. This is a
    geometry-only operation; chemistry-aware neighbour-typing lives in
    :mod:`crystal_viewer.topology`.
    """
    if cutoff <= 0.0 or not seed_indices:
        return []
    return atoms_within_radius(
        atoms,
        M,
        seed_indices=list(seed_indices),
        radius=float(cutoff),
        include_seeds=True,
    )


def atoms_under_symmetry(
    atoms: Sequence[Dict[str, Any]],
    M: np.ndarray,
    *,
    seed_indices: Sequence[int],
    sym_ops: Sequence[Tuple[Sequence[Sequence[float]], Sequence[float]]],
) -> List[Dict[str, Any]]:
    """Apply each symmetry operation ``(R_frac, t_frac)`` to each seed.

    ``R_frac`` and ``t_frac`` are in fractional coordinates -- the
    standard convention for spacegroup operations. The identity is
    NOT skipped; callers who want to retain only the symmetry-related
    extras should remove the seed atoms from the result themselves.
    Duplicates (same final fractional position, modulo small jitter)
    are dropped.
    """
    if not seed_indices or not sym_ops:
        return []
    M_arr = np.asarray(M, dtype=float)
    out: Dict[Tuple[str, Tuple[int, int, int]], Dict[str, Any]] = {}
    for seed_idx in seed_indices:
        atom = atoms[int(seed_idx)]
        cart = np.asarray(atom["cart"], dtype=float)
        frac = (
            np.asarray(atom["frac"], dtype=float)
            if atom.get("frac") is not None
            else cart_to_frac(cart, M_arr)
        )
        for op_idx, (R, t) in enumerate(sym_ops):
            R_arr = np.asarray(R, dtype=float)
            t_arr = np.asarray(t, dtype=float)
            new_frac = R_arr @ frac + t_arr
            new_cart = frac_to_cart(new_frac, M_arr)
            shift_int = (int(op_idx), 0, 0)  # encode op id in image_shift slot
            new = _atom_copy(atom, image_shift=shift_int, new_label_suffix=f"<sym{op_idx}>")
            new["cart"] = new_cart.copy()
            new["frac"] = new_frac.copy()
            label_key = (str(atom.get("label") or ""), shift_int)
            if label_key in out:
                continue
            out[label_key] = new
    return list(out.values())


# ---------------------------------------------------------------------------
# Slab transform: delegates to molcrys_kit
# ---------------------------------------------------------------------------


def slab_atoms_from_bundle(
    bundle: Any,
    *,
    miller: Tuple[int, int, int],
    layers: Optional[int] = None,
    min_thickness: Optional[float] = None,
    vacuum: float = 10.0,
) -> Tuple[List[Dict[str, Any]], np.ndarray]:
    """Generate slab atoms via :func:`molcrys_kit.operations.surface.generate_topological_slab`.

    Returns ``(atoms_list, slab_M)`` where ``atoms_list`` is a list of
    MatterVis-shaped atom dicts (``elem``, ``cart``, ``frac``,
    ``label``, ``occ``, ...) and ``slab_M`` is the slab cell as a 3x3
    row-lattice matrix matching MatterVis's lattice convention.
    """
    from molcrys_kit.operations.surface import generate_topological_slab

    crystal = getattr(bundle, "crystal", None)
    if crystal is None:
        raise ValueError(
            "slab transform requires bundle.crystal to be a MolecularCrystal "
            "(set during loader.build_loaded_crystal)."
        )
    if layers is None and min_thickness is None:
        # Default to 3 layers, matching molcrys_kit's documented default.
        layers = 3
    slab = generate_topological_slab(
        crystal,
        miller_indices=tuple(int(x) for x in miller),
        layers=layers,
        min_thickness=min_thickness,
        vacuum=float(vacuum),
    )
    # MolecularCrystal stores lattice as row vectors, matching MatterVis.
    slab_M = np.asarray(slab.lattice, dtype=float)
    out: List[Dict[str, Any]] = []
    counter = 0
    for mol in slab.molecules:
        ase_atoms = mol.atoms if hasattr(mol, "atoms") else mol
        symbols = ase_atoms.get_chemical_symbols()
        positions = ase_atoms.get_positions()
        for elem, cart in zip(symbols, positions):
            cart = np.asarray(cart, dtype=float)
            frac = cart_to_frac(cart, slab_M)
            out.append(
                {
                    "elem": str(elem),
                    "cart": cart,
                    "frac": frac,
                    "label": f"{elem}{counter}",
                    "occ": 1.0,
                    "da": "",
                    "dg": "",
                    "_image_shift": (0, 0, 0),
                    "_origin_label": f"{elem}{counter}",
                }
            )
            counter += 1
    return out, slab_M


# ---------------------------------------------------------------------------
# Scene rebuild + dispatcher
# ---------------------------------------------------------------------------


def rebuild_scene_with_atoms(
    base_scene: Dict[str, Any],
    new_atoms: Sequence[Dict[str, Any]],
    *,
    style: Optional[Dict[str, Any]] = None,
    cell_override: Any = None,
    M_override: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Build a new scene dict from ``base_scene`` keeping the metadata
    (camera defaults, view rotation, axes, preset entry) intact but
    swapping in a fresh ``draw_atoms`` list. Bonds are re-detected
    without PBC because every transform manifests its atoms in a
    single global Cartesian frame.

    ``style`` overrides ``base_scene["style"]`` for derived knobs
    (atom_scale on bounds computation, element_colors). Callers that
    don't care can pass ``None``.
    """
    from ..static_publication import crystal_scene as legacy_scene
    from ..scene import scene_ops

    ops = scene_ops()
    style = style or dict(base_scene.get("style", {}))

    cell = cell_override if cell_override is not None else base_scene.get("cell")
    M = (
        np.asarray(M_override, dtype=float)
        if M_override is not None
        else np.asarray(base_scene.get("M"), dtype=float)
    )
    view_x = np.asarray(base_scene["view_x"], dtype=float)
    view_y = np.asarray(base_scene["view_y"], dtype=float)
    view_z = np.asarray(base_scene["view_z"], dtype=float)

    draw_atoms = [dict(atom) for atom in new_atoms]
    if draw_atoms:
        depths = np.array([np.asarray(atom["cart"]) @ view_z for atom in draw_atoms], dtype=float)
        z_min, z_max = float(depths.min()), float(depths.max())
        z_span = max(z_max - z_min, 1e-6)
        for atom, depth in zip(draw_atoms, depths):
            atom["_depth_t"] = float((depth - z_min) / z_span)
            atom["is_minor"] = atom_is_minor(atom)
            atom["disorder_alpha"] = float(ops.disorder_alpha(atom))
            atom.setdefault("color", ops.elem_color(atom["elem"]))
            atom.setdefault("color_light", ops.elem_color_light(atom["elem"]))
            atom.setdefault("atom_radius", float(ops.atom_r(atom["elem"])))

    # The legacy bond detector keys its bond-table check
    # (``_bond_allowed_by_table``) on per-atom ``label`` strings; replica
    # atoms produced by ``replicate_atoms`` carry suffixed labels (``Cl1``
    # -> ``Cl1[1,0,0]``) but their ``_bond_partners`` list still
    # references the canonical (unsuffixed) labels. Without help, the
    # table check would reject every cross-replica bond. Swap each atom's
    # ``label`` back to its ``_origin_label`` for the duration of the
    # detection call, then restore.
    saved_labels: List[Tuple[Dict[str, Any], str]] = []
    for atom in draw_atoms:
        origin = atom.get("_origin_label")
        if origin and origin != atom.get("label"):
            saved_labels.append((atom, atom["label"]))
            atom["label"] = origin
    try:
        bond_pairs = ops.find_bonds(draw_atoms, cell=None)
    finally:
        for atom, original_label in saved_labels:
            atom["label"] = original_label
    bonds = []
    for i, j in bond_pairs:
        ai = draw_atoms[int(i)]
        aj = draw_atoms[int(j)]
        start = np.asarray(ai["cart"], dtype=float)
        end = np.asarray(aj["cart"], dtype=float)
        bonds.append(
            {
                "i": int(i),
                "j": int(j),
                "start": start.copy(),
                "end": end.copy(),
                "color_i": ai["color"],
                "color_j": aj["color"],
                "alpha_i": ai["disorder_alpha"],
                "alpha_j": aj["disorder_alpha"],
                # Derived only from loader-authored atom ``_is_minor`` flags.
                "is_minor": bond_is_minor(ai, aj),
                "depth_t": float((ai["_depth_t"] + aj["_depth_t"]) / 2.0),
            }
        )

    fragment_table, atom_fragment_labels = _fragment_table_from_current_bonds(draw_atoms, bonds)

    label_items = legacy_scene._label_payload(ops, draw_atoms, view_x, view_y, view_z)
    bounds = legacy_scene._compute_bounds(
        draw_atoms,
        view_x,
        view_y,
        view_z,
        atom_scale=float(style.get("atom_scale", 1.0)),
    )

    M_arr = np.asarray(M, dtype=float)
    projected_axes = [
        (float(M_arr[i] @ view_x), float(M_arr[i] @ view_y))
        for i in range(3)
    ]

    out = dict(base_scene)
    out["cell"] = cell
    out["M"] = M
    out["draw_atoms"] = draw_atoms
    out["bonds"] = bonds
    out["label_items"] = label_items
    out["fragment_table"] = fragment_table
    out["atom_fragment_labels"] = atom_fragment_labels
    out["bounds"] = bounds
    out["projected_axes"] = projected_axes
    out["has_minor"] = any(bool(atom.get("is_minor")) for atom in draw_atoms)
    return out


def _component_formula(component_atoms: Sequence[Dict[str, Any]]) -> tuple[str, set[str], int]:
    heavy_atoms = [atom for atom in component_atoms if atom.get("elem") != "H"]
    elem_counts: dict[str, int] = {}
    for atom in heavy_atoms:
        elem = str(atom.get("elem") or "?")
        elem_counts[elem] = elem_counts.get(elem, 0) + 1
    elem_set = set(elem_counts)
    heavy_count = len(heavy_atoms)
    ordered: list[tuple[str, int]] = []
    for elem in ("C", "N"):
        if elem in elem_counts:
            ordered.append((elem, elem_counts.pop(elem)))
    ordered.extend(sorted(elem_counts.items()))
    formula = "".join(f"{elem}{count}" if count > 1 else elem for elem, count in ordered) or "?"
    return formula, elem_set, heavy_count


def _fragment_table_from_current_bonds(
    atoms: Sequence[Dict[str, Any]],
    bonds: Sequence[Dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build fragment rows from the manifested post-transform bond graph."""
    if not atoms:
        return [], []

    adj: dict[int, set[int]] = defaultdict(set)
    for bond in bonds:
        try:
            i = int(bond["i"])
            j = int(bond["j"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < len(atoms) and 0 <= j < len(atoms):
            adj[i].add(j)
            adj[j].add(i)

    components: list[list[int]] = []
    seen: set[int] = set()
    for idx in range(len(atoms)):
        if idx in seen:
            continue
        stack = [idx]
        seen.add(idx)
        component: list[int] = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in adj.get(current, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        components.append(sorted(component))

    fragments = []
    for component in components:
        component_atoms = [atoms[idx] for idx in component]
        formula, elem_set, heavy_count = _component_formula(component_atoms)
        center_atoms = [atom for atom in component_atoms if atom.get("elem") != "H"] or component_atoms
        center_cart = np.mean([np.asarray(atom["cart"], dtype=float) for atom in center_atoms], axis=0)
        frac_values = [
            np.asarray(atom.get("frac"), dtype=float)
            for atom in center_atoms
            if atom.get("frac") is not None
        ]
        center_frac = np.mean(frac_values, axis=0) if frac_values else np.zeros(3, dtype=float)
        fragments.append(
            {
                "site_indices": component,
                "source_molecule_index": None,
                "center": [float(x) for x in center_cart],
                "frac_center": [float(x) for x in center_frac],
                "elem_set": sorted(elem_set),
                "heavy_atom_count": int(heavy_count),
                "cluster_size": len(component_atoms),
                "species": "".join(sorted(elem_set)) or "?",
                "formula": formula,
            }
        )

    x_fragments = [frag for frag in fragments if "Cl" in frag["elem_set"]]
    non_x = [frag for frag in fragments if frag not in x_fragments]
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
            type_order.get(frag.get("type"), 9),
            *[float(x % 1.0) for x in frag["frac_center"]],
            frag["heavy_atom_count"],
            frag["cluster_size"],
        )
    )

    counters: dict[str, int] = defaultdict(int)
    atom_fragment_labels = ["?"] * len(atoms)
    final_table = []
    for frag_idx, frag in enumerate(fragments):
        frag_type = frag.get("type", "?")
        label_index = counters[frag_type]
        counters[frag_type] += 1
        for site_idx in frag["site_indices"]:
            atom_fragment_labels[site_idx] = frag_type
        final_table.append(
            {
                "index": frag_idx,
                "type": frag_type,
                "label": f"{frag_type}{label_index}",
                "species": frag["species"],
                "formula": frag["formula"],
                "elem_set": frag["elem_set"],
                "center": frag["center"],
                "frac_center": frag["frac_center"],
                "site_indices": frag["site_indices"],
                "source_molecule_index": None,
                "source": "transform",
                "heavy_atom_count": frag["heavy_atom_count"],
                "cluster_size": frag["cluster_size"],
            }
        )
    return final_table, atom_fragment_labels


