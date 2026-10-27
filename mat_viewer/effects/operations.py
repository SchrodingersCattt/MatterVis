"""Scientific operation adapters.

Each function consumes a private crystal copy and returns a new crystal
plus an index map. None of them commit, touch a view, or write a CIF.
Chemistry that MolCrysKit already publishes is called directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from molcrys_kit.constants.config import KEY_ATOM_ID
from molcrys_kit.operations import (
    add_hydrogens,
    create_supercell,
    generate_topological_slab,
    remove_solvents,
)

from .errors import (
    AmbiguousSelection,
    ClashError,
    UnsupportedOperation,
    ValidationError,
)
from .materialize import (
    crystal_from_molecules,
    molecule_from_spec,
    molecule_without_locals,
    replace_symbols,
)
from .refs import parse_image


@dataclass
class Outcome:
    crystal: Any
    index_map: list[int | None]
    mappings: list[dict[str, Any]]
    diagnostics: list[str] = field(default_factory=list)
    invalid_structure_fields: set[str] = field(default_factory=lambda: {"energy", "stress"})
    status: str = "ok"
    provenance: dict[str, Any] = field(default_factory=dict)


def run_operation(command: str, crystal, params: Mapping[str, Any] | None) -> Outcome:
    params = dict(params or {})
    if command == "build_supercell":
        return build_supercell(crystal, params)
    if command == "build_slab":
        return build_slab(crystal, params)
    if command == "insert_molecule":
        return insert_molecule(crystal, params)
    if command == "delete_sites":
        return delete_sites(crystal, params)
    if command == "replace_species":
        return replace_species(crystal, params)
    if command == "add_hydrogens":
        return add_hydrogens_op(crystal, params)
    if command == "remove_solvent":
        return remove_solvent(crystal, params)
    if command == "materialize_selection":
        return materialize_selection(crystal, params)
    if command == "commit_disorder_candidate":
        return commit_disorder_candidate(crystal, params)
    if command == "delete_periodic_copy":
        return delete_periodic_copy(crystal, params)
    raise UnsupportedOperation(
        f"unknown structural command {command!r}",
        hint="Display repetition stays on /api/v2/state supercell and /transforms.",
    )


IMPLEMENTED_COMMANDS = (
    "build_supercell",
    "build_slab",
    "insert_molecule",
    "delete_sites",
    "replace_species",
    "add_hydrogens",
    "remove_solvent",
    "materialize_selection",
    "commit_disorder_candidate",
)


def build_supercell(crystal, params: Mapping[str, Any]) -> Outcome:
    scaling = _strict_scaling(params, pbc=tuple(bool(value) for value in crystal.pbc))
    result = create_supercell(crystal.copy(), scaling)
    mappings, index_map = _supercell_mapping(crystal, result, scaling)
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "build_supercell", "scaling": list(scaling)},
        diagnostics=[
            f"supercell {scaling[0]}x{scaling[1]}x{scaling[2]}",
        ],
    )


def build_slab(crystal, params: Mapping[str, Any]) -> Outcome:
    miller = _miller(params.get("miller"))
    layers = params.get("layers")
    vacuum = params.get("vacuum", 10.0)
    min_thickness = params.get("min_thickness")
    if layers is None and min_thickness is None:
        layers = 1
    if layers is not None:
        layers = _positive_int(layers, label="layers")
    try:
        vacuum_value = float(vacuum)
    except (TypeError, ValueError) as exc:
        raise ValidationError("vacuum must be a number") from exc
    if vacuum_value < 0:
        raise ValidationError("vacuum must be non-negative")
    kwargs = {"vacuum": vacuum_value}
    if layers is not None:
        kwargs["layers"] = layers
    if min_thickness is not None:
        kwargs["min_thickness"] = float(min_thickness)
    result = generate_topological_slab(crystal.copy(), miller, **kwargs)
    mappings, index_map = _match_ids(crystal, result, command="build_slab")
    parameters = result.get_lattice_parameters()
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={
            "command": "build_slab",
            "miller": list(miller),
            "layers": layers,
            "vacuum": vacuum_value,
            "pbc": [bool(value) for value in result.pbc],
            "lattice_parameters": [float(value) for value in parameters],
        },
        diagnostics=["slab candidate is not a bulk-source edit"],
    )


def insert_molecule(crystal, params: Mapping[str, Any]) -> Outcome:
    symbols = [str(symbol) for symbol in params.get("symbols") or []]
    positions = params.get("positions")
    if not symbols or positions is None:
        raise ValidationError("insert_molecule requires symbols and positions")
    rotation = _rotation(params.get("rotation"))
    origin = np.asarray(params.get("origin", (0.0, 0.0, 0.0)), dtype=float).reshape(3)
    system = str(params.get("origin_system") or "cartesian")
    if system == "fractional":
        origin = origin @ np.asarray(crystal.lattice, dtype=float)
    elif system != "cartesian":
        raise ValidationError("origin_system must be cartesian or fractional")
    local = np.asarray(positions, dtype=float)
    if local.shape != (len(symbols), 3):
        raise ValidationError("positions must have shape (N, 3)")
    placed = (rotation @ local.T).T + origin
    clash_policy = str(params.get("clash_policy") or "reject")
    if clash_policy not in {"reject", "allow"}:
        raise ValidationError("clash_policy must be reject or allow")
    threshold = float(params.get("clash_threshold", 1.0))
    if threshold <= 0:
        raise ValidationError("clash_threshold must be positive")
    distance = _nearest_host_distance(crystal, placed)
    if clash_policy == "reject" and distance < threshold:
        raise ClashError(
            f"insertion clashes at {distance:.4f} Å (threshold {threshold:.4f} Å); no optimization was attempted"
        )
    molecule = molecule_from_spec(
        symbols,
        placed,
        lattice=crystal.lattice,
        pbc=tuple(bool(value) for value in crystal.pbc),
    )
    molecules = [item.copy() for item in crystal.molecules]
    molecules.append(molecule)
    result = crystal_from_molecules(
        crystal.lattice,
        molecules,
        pbc=tuple(bool(value) for value in crystal.pbc),
        metadata=dict(crystal.metadata),
    )
    mappings, index_map = _match_ids(crystal, result, command="insert_molecule")
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={
            "command": "insert_molecule",
            "origin_system": system,
            "clash_policy": clash_policy,
            "clash_threshold": threshold,
            "minimum_host_distance": distance,
            "rotation": rotation.tolist(),
        },
        diagnostics=[] if distance >= threshold else [f"clash allowed at {distance:.4f} Å"],
    )


def delete_sites(crystal, params: Mapping[str, Any]) -> Outcome:
    targets = _explicit_source_sites(params)
    by_molecule: dict[int, set[int]] = {}
    records = {str(record.site_id): record for record in crystal.get_site_records()}
    missing = sorted(set(targets) - set(records))
    if missing:
        raise ValidationError("unknown site ids: " + ", ".join(missing))
    for site_id in targets:
        record = records[site_id]
        by_molecule.setdefault(int(record.molecule_index), set()).add(int(record.local_index))
    molecules = []
    for index, molecule in enumerate(crystal.molecules):
        if index not in by_molecule:
            molecules.append(molecule.copy())
            continue
        kept = molecule_without_locals(molecule, by_molecule[index])
        if kept is not None:
            molecules.append(kept)
    if not molecules:
        raise ValidationError("delete_sites would remove every atom")
    result = crystal_from_molecules(
        crystal.lattice,
        molecules,
        pbc=tuple(bool(value) for value in crystal.pbc),
        metadata=dict(crystal.metadata),
    )
    mappings, index_map = _match_ids(crystal, result, command="delete_sites")
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "delete_sites", "site_ids": sorted(targets)},
    )


def replace_species(crystal, params: Mapping[str, Any]) -> Outcome:
    symbol = str(params.get("symbol") or "").strip()
    if not symbol:
        raise ValidationError("replace_species requires symbol")
    _known_element(symbol)
    targets = _explicit_source_sites(params)
    records = {str(record.site_id): record for record in crystal.get_site_records()}
    missing = sorted(set(targets) - set(records))
    if missing:
        raise ValidationError("unknown site ids: " + ", ".join(missing))
    by_molecule: dict[int, dict[int, str]] = {}
    for site_id in targets:
        record = records[site_id]
        by_molecule.setdefault(int(record.molecule_index), {})[int(record.local_index)] = symbol
    molecules = []
    for index, molecule in enumerate(crystal.molecules):
        if index in by_molecule:
            molecules.append(replace_symbols(molecule, by_molecule[index]))
        else:
            molecules.append(molecule.copy())
    result = crystal_from_molecules(
        crystal.lattice,
        molecules,
        pbc=tuple(bool(value) for value in crystal.pbc),
        metadata=dict(crystal.metadata),
    )
    mappings, index_map = _match_ids(crystal, result, command="replace_species")
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "replace_species", "symbol": symbol, "site_ids": sorted(targets)},
    )


def add_hydrogens_op(crystal, params: Mapping[str, Any]) -> Outcome:
    if params.get("optimize_torsion"):
        raise UnsupportedOperation("add_hydrogens does not run torsion or energy optimization")
    elements = params.get("target_elements")
    if elements is not None:
        elements = [str(item) for item in elements]
    result = add_hydrogens(
        crystal.copy(),
        target_elements=elements,
        optimize_torsion=False,
        use_formula_moiety=bool(params.get("use_formula_moiety", False)),
    )
    if sum(len(molecule) for molecule in result.molecules) == sum(len(molecule) for molecule in crystal.molecules):
        raise ValidationError("add_hydrogens did not insert any hydrogen atoms")
    mappings, index_map = _match_ids(crystal, result, command="add_hydrogens")
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "add_hydrogens", "target_elements": elements},
    )


def remove_solvent(crystal, params: Mapping[str, Any]) -> Outcome:
    targets = params.get("targets")
    indices = params.get("molecule_indices")
    if targets and indices:
        raise ValidationError("remove_solvent accepts formulas or molecule indices, not both")
    if indices is not None:
        return _remove_molecule_indices(crystal, indices)
    if not isinstance(targets, (list, tuple)) or not targets:
        raise ValidationError("remove_solvent requires explicit formula targets or molecule_indices")
    formulas = [str(item) for item in targets]
    before = _formulas(crystal)
    result = remove_solvents(crystal.copy(), formulas)
    after = _formulas(result)
    if before == after:
        raise ValidationError("no molecule matched the explicit solvent targets: " + ", ".join(formulas))
    mappings, index_map = _match_ids(crystal, result, command="remove_solvent")
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "remove_solvent", "targets": formulas},
    )


def materialize_selection(crystal, params: Mapping[str, Any]) -> Outcome:
    if params.get("bake_display_transforms"):
        raise UnsupportedOperation("materialize_selection does not bake display-object transforms")
    instances = params.get("instances")
    if not isinstance(instances, (list, tuple)) or not instances:
        raise ValidationError("materialize_selection requires explicit instances")
    pbc_value = params.get("pbc", False)
    if pbc_value not in (False, (False, False, False), [False, False, False]) and (
        not params.get("cell") or not params.get("equivalences")
    ):
        raise UnsupportedOperation(
            "periodic materialization requires an explicit cell and equivalence relations"
        )
    records = {str(record.site_id): record for record in crystal.get_site_records()}
    symbols = []
    positions = []
    mappings = []
    index_map: list[int | None] = []
    for instance in instances:
        if not isinstance(instance, Mapping):
            raise AmbiguousSelection("each instance must name a source site_id and image")
        ghost = instance.get("kind") in {"ghost", "annotation"} or instance.get("provenance") in {None, "none", "rendered"}
        if ghost and not instance.get("site_id"):
            raise AmbiguousSelection("provenance-free ghosts and annotations are not scientific atoms")
        if "display_index" in instance and "site_id" not in instance:
            raise AmbiguousSelection("a display index is not a scientific site")
        site_id = str(instance.get("site_id") or "")
        if site_id not in records:
            raise AmbiguousSelection(f"instance {site_id!r} is not a source site of this revision")
        image = parse_image(instance.get("image"))
        record = records[site_id]
        cartesian = np.asarray(record.cartesian_position_A, dtype=float) + np.asarray(image, dtype=float) @ np.asarray(crystal.lattice, dtype=float)
        symbols.append(str(record.symbol))
        positions.append(cartesian)
        index_map.append(int(record.global_index))
        mappings.append(
            {
                "source_site_id": site_id,
                "image": list(image),
                "output_site_id": f"cluster:{len(symbols) - 1}",
            }
        )
    center = np.mean(np.asarray(positions, dtype=float), axis=0)
    span = np.ptp(np.asarray(positions, dtype=float), axis=0)
    length = float(max(10.0, np.max(span) + 8.0))
    lattice = np.eye(3) * length
    shifted = np.asarray(positions, dtype=float) - center + np.array([length, length, length]) / 2.0
    molecule = molecule_from_spec(symbols, shifted, lattice=lattice, pbc=(False, False, False))
    # Stamp the declared cluster ids before MolecularCrystal deduplicates.
    molecule.set_array(KEY_ATOM_ID, np.array([item["output_site_id"] for item in mappings], dtype=str))
    result = crystal_from_molecules(lattice, [molecule], pbc=(False, False, False), metadata={"materialized": "finite-cluster"})
    actual_ids = _ids(result.molecules[0])
    for mapping, actual in zip(mappings, actual_ids):
        mapping["output_site_id"] = actual
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "materialize_selection", "pbc": [False, False, False], "n_instances": len(mappings)},
        diagnostics=["finite cluster uses PBC=False and does not infer a cell from the view"],
    )


def commit_disorder_candidate(crystal, params: Mapping[str, Any]) -> Outcome:
    retained = params.get("crystal")
    if retained is None:
        from molcrys_kit.analysis.disorder.process import (
            generate_ordered_replicas_from_disordered_sites,
        )

        method = str(params.get("method") or "enumerate")
        if method not in {"optimal", "enumerate", "random"}:
            raise ValidationError("disorder method must be optimal, enumerate, or random")
        count = int(params.get("count") or 1)
        seed = params.get("seed")
        seed = None if seed in (None, "") else int(seed)
        if method == "random" and seed is None:
            raise ValidationError("unseeded random disorder generation cannot be committed reproducibly; store the candidate first")
        replicas = generate_ordered_replicas_from_disordered_sites(
            crystal=crystal.copy(),
            generate_count=count,
            method=method,
            random_seed=seed,
            return_kept_indices=True,
        )
        if not replicas:
            raise ValidationError("disorder generation produced no scientific candidate")
        index = int(params.get("candidate_index") or 0)
        if not 0 <= index < len(replicas):
            raise ValidationError(f"candidate_index {index} is outside the generated set")
        retained, kept = replicas[index]
        kept_indices = [int(item) for item in kept]
    else:
        kept_indices = [int(item) for item in params.get("kept_indices") or []]
    mappings, index_map = _match_ids(crystal, retained, command="commit_disorder_candidate")
    return Outcome(
        crystal=retained,
        index_map=index_map,
        mappings=mappings,
        provenance={
            "command": "commit_disorder_candidate",
            "kept_indices": kept_indices,
            "method": params.get("method"),
            "seed": params.get("seed"),
            "candidate_index": params.get("candidate_index", 0),
        },
        diagnostics=["committed the scientific disorder candidate, not highlight indices"],
    )


def delete_periodic_copy(crystal, params: Mapping[str, Any]) -> Outcome:
    """Build a real supercell and delete one mapped image in the same result."""

    scaling = _strict_scaling({"a": params.get("a"), "b": params.get("b"), "c": params.get("c"), **params}, pbc=tuple(bool(value) for value in crystal.pbc))
    site_id = str(params.get("source_site_id") or "")
    if not site_id:
        raise AmbiguousSelection("delete_periodic_copy requires source_site_id")
    image = parse_image(params.get("image"))
    if image == (0, 0, 0) and not params.get("allow_home_image"):
        raise AmbiguousSelection(
            "deleting the home image of a display repeat is a source-site edit; "
            "pass the periodic image explicitly or call delete_sites"
        )
    staged = build_supercell(crystal, {"a": scaling[0], "b": scaling[1], "c": scaling[2]})
    match = next(
        (
            item
            for item in staged.mappings
            if item["source_site_id"] == site_id and tuple(item["image"]) == image
        ),
        None,
    )
    if match is None:
        raise ValidationError(f"no supercell site matches {site_id} at image {image}")
    deleted = delete_sites(staged.crystal, {"sites": [{"site_id": match["output_site_id"]}]})
    return Outcome(
        crystal=deleted.crystal,
        index_map=deleted.index_map,
        mappings=deleted.mappings,
        provenance={
            "command": "delete_periodic_copy",
            "scaling": list(scaling),
            "source_site_id": site_id,
            "image": list(image),
            "deleted_site_id": match["output_site_id"],
            "steps": ["build_supercell", "delete_sites"],
        },
        diagnostics=["single transaction; the source revision is not partially expanded"],
    )


def _strict_scaling(params: Mapping[str, Any], *, pbc: tuple[bool, bool, bool]) -> tuple[int, int, int]:
    if params.get("matrix") is not None:
        matrix = np.asarray(params["matrix"], dtype=float)
        if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
            raise ValidationError("supercell matrix must be a finite 3x3")
        if not np.allclose(matrix, np.diag(np.diag(matrix))):
            raise UnsupportedOperation(
                "nondiagonal supercell matrices are not supported and are not replaced by their diagonal"
            )
        raw_values = [(matrix[index, index], True) for index in range(3)]
    else:
        missing = [axis for axis in "abc" if axis not in params]
        if missing:
            raise ValidationError("build_supercell requires positive integer a, b, and c")
        raw_values = [(params[axis], False) for axis in "abc"]
    cleaned = []
    for axis, (raw, from_matrix), periodic in zip("abc", raw_values, pbc):
        if from_matrix:
            if isinstance(raw, bool) or not float(raw).is_integer():
                raise ValidationError(f"repetition along {axis} must be a positive integer, not {raw!r}")
        elif not _is_strict_int(raw):
            raise ValidationError(f"repetition along {axis} must be a positive integer, not {raw!r}")
        number = int(raw)
        if number < 1:
            raise ValidationError(f"repetition along {axis} must be a positive integer")
        if not periodic and number != 1:
            raise ValidationError(f"cannot repeat a nonperiodic direction {axis}")
        cleaned.append(number)
    return tuple(cleaned)  # type: ignore[return-value]


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(value, bool)


def _positive_int(value: Any, *, label: str) -> int:
    if not _is_strict_int(value) or int(value) < 1:
        raise ValidationError(f"{label} must be a positive integer")
    return int(value)


def _miller(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValidationError("miller must be three integers")
    try:
        miller = tuple(int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("miller must be three integers") from exc
    if miller == (0, 0, 0):
        raise ValidationError("miller indices cannot all be zero")
    return miller  # type: ignore[return-value]


def _rotation(value: Any) -> np.ndarray:
    if value is None:
        return np.eye(3)
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValidationError("rotation must be a finite 3x3 matrix")
    if abs(float(np.linalg.det(matrix)) - 1.0) > 1e-6 or not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-6):
        raise ValidationError("rotation must be a proper orthogonal matrix")
    return matrix


def _known_element(symbol: str) -> None:
    from ase.data import chemical_symbols

    if symbol not in set(chemical_symbols[1:]):
        raise ValidationError(f"unknown element symbol: {symbol}")


def _explicit_source_sites(params: Mapping[str, Any]) -> set[str]:
    sites = params.get("sites")
    if isinstance(sites, (str, bytes)) or not isinstance(sites, (list, tuple)) or not sites:
        raise ValidationError("sites must be a non-empty list of source site references")
    found = set()
    for site in sites:
        if isinstance(site, str):
            found.add(site)
            continue
        if not isinstance(site, Mapping):
            raise AmbiguousSelection("site references must be objects with site_id")
        if "display_index" in site and "site_id" not in site:
            raise AmbiguousSelection("a display index is not a source site; materialize the supercell first")
        image = parse_image(site.get("image"))
        if image != (0, 0, 0):
            raise AmbiguousSelection(
                "a periodic image is not a source site; build_supercell and delete the mapped site, "
                "or call delete_periodic_copy as one transaction"
            )
        site_id = str(site.get("site_id") or "")
        if not site_id:
            raise AmbiguousSelection("structural edits require an explicit site_id")
        found.add(site_id)
    return found


def _ids(molecule) -> list[str]:
    return [str(value) for value in molecule.arrays[KEY_ATOM_ID]]


def _source_index(crystal) -> dict[str, int]:
    return {str(record.site_id): int(record.global_index) for record in crystal.get_site_records()}


def _match_ids(source, result, *, command: str) -> tuple[list[dict[str, Any]], list[int | None]]:
    source_index = _source_index(source)
    mappings = []
    index_map: list[int | None] = []
    for record in result.get_site_records():
        site_id = str(record.site_id)
        source_site = site_id if site_id in source_index else site_id.split("~", 1)[0]
        source_global = source_index.get(source_site)
        if source_global is None and site_id in source_index:
            source_global = source_index[site_id]
        index_map.append(source_global)
        mappings.append(
            {
                "source_site_id": source_site if source_global is not None else None,
                "output_site_id": site_id,
                "output_global_index": int(record.global_index),
                "command": command,
            }
        )
    return mappings, index_map


def _supercell_mapping(source, result, scaling: tuple[int, int, int]):
    import itertools

    source_ids = [_ids(molecule) for molecule in source.molecules]
    source_index = _source_index(source)
    images = list(itertools.product(range(scaling[0]), range(scaling[1]), range(scaling[2])))
    if len(result.molecules) != len(images) * len(source.molecules):
        return _match_ids(source, result, command="build_supercell")
    by_output: dict[str, dict[str, Any]] = {}
    cursor = 0
    for image in images:
        for molecule_ids in source_ids:
            new_ids = _ids(result.molecules[cursor])
            cursor += 1
            for source_id, output_id in zip(molecule_ids, new_ids):
                by_output[output_id] = {
                    "source_site_id": source_id,
                    "output_site_id": output_id,
                    "image": list(image),
                    "source_global_index": source_index[source_id],
                    "command": "build_supercell",
                }
    mappings = []
    index_map = []
    for record in result.get_site_records():
        item = dict(by_output[str(record.site_id)])
        item["output_global_index"] = int(record.global_index)
        mappings.append(item)
        index_map.append(int(item["source_global_index"]))
    return mappings, index_map


def _nearest_host_distance(crystal, placed: np.ndarray) -> float:
    host = np.vstack([np.asarray(molecule.positions, dtype=float) for molecule in crystal.molecules])
    lattice = np.asarray(crystal.lattice, dtype=float)
    inverse = np.linalg.inv(lattice)
    fractional = placed @ inverse
    host_fractional = host @ inverse
    delta = fractional[:, None, :] - host_fractional[None, :, :]
    periodic = np.asarray(crystal.pbc, dtype=bool)
    delta[:, :, periodic] -= np.round(delta[:, :, periodic])
    distances = np.linalg.norm(delta @ lattice, axis=-1)
    return float(np.min(distances)) if distances.size else float("inf")


def _formulas(crystal) -> list[str]:
    return [molecule.get_chemical_formula() for molecule in crystal.molecules]


def _remove_molecule_indices(crystal, indices) -> Outcome:
    if not isinstance(indices, (list, tuple)) or not indices:
        raise ValidationError("molecule_indices must be a non-empty list")
    selected = {int(index) for index in indices}
    if any(index < 0 or index >= len(crystal.molecules) for index in selected):
        raise ValidationError("molecule index is outside the crystal")
    kept = [molecule.copy() for index, molecule in enumerate(crystal.molecules) if index not in selected]
    if not kept:
        raise ValidationError("remove_solvent would remove every molecule")
    result = crystal_from_molecules(
        crystal.lattice,
        kept,
        pbc=tuple(bool(value) for value in crystal.pbc),
        metadata=dict(crystal.metadata),
    )
    mappings, index_map = _match_ids(crystal, result, command="remove_solvent")
    return Outcome(
        crystal=result,
        index_map=index_map,
        mappings=mappings,
        provenance={"command": "remove_solvent", "molecule_indices": sorted(selected)},
    )


__all__ = ["IMPLEMENTED_COMMANDS", "Outcome", "run_operation"]
