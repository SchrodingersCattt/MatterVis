"""Public, chemistry-free TUI render contract.

MatterVis owns the source structure, camera, projection, and session.
A renderer receives only this request and returns text. The request is
JSON-serializable and deliberately has no slot for ``CrystalIR``,
MolCrysKit chemistry, names, gold answers, or scorer output.

Coordinate frame (host-derived, not recomputed by the contract):

- Cartesian site positions and the cell matrix are Angstrom. Cell rows
  are the ``a``, ``b``, and ``c`` vectors.
- Fractional coordinates are unitless, in that cell.
- ``basis_right_up_forward`` rows are the camera right, up, and forward
  unit vectors in world Cartesian coordinates.
- ``projected_xy`` and ``projected_depth`` are the arrays
  :func:`mat_viewer.math.camera.project_points` already produced for this
  frame. Orthographic ``xy`` is the camera-plane position divided by
  camera distance. Depth is the camera-forward component; larger is closer.
- A null ``viewport`` means the native compositor still computes its own
  fit. Active sessions pass the controller's fitted viewport in projected
  ``xy`` units.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

import numpy as np

from .compositor import atom_is_drawn
from .text import terminal_text

RENDER_API_VERSION = "mattervis.tui.render/v1"
REQUEST_SCHEMA = "mattervis.tui.render_request/v1"
DISPLAY_IR_SCHEMA = "mattervis.tui.display_ir/v1"
FRAME_SCHEMA = "mattervis.tui.rendered_frame/v1"
CONFIG_SCHEMA = "mattervis.tui.renderer_config/v1"
REPLAY_SCHEMA = "mattervis.tui.renderer_replay/v1"
NATIVE_BACKEND_ID = "mattervis.native-v1"
RENDERER_ENTRY_GROUP = "mattervis.tui.renderers"
GROUPING_PROVENANCE = "manifested_molecule_index"

BOND_POLICIES = ("manifested", "provided_only", "bonds_off")
OBSERVATION_PROFILES = ("legacy", "model")

_BOND_SOURCE_PROVENANCE = {
    "distance_heuristic": "inferred",
    "molcryskit": "inferred",
    "canonical_scene": "inferred",
    "explicit": "provided",
    "provided": "provided",
}
_FORBIDDEN_KEY_FRAGMENTS = (
    "chemistry",
    "gold",
    "scorer",
    "cip",
    "iupac",
    "smiles",
    "inchi",
    "warning",
    "spacegroup",
    "canonical_formula",
    "answer",
    "benchmark",
    "stereochem",
)
_FORBIDDEN_EXACT_KEYS = frozenset({"score", "split", "repair", "task"})


class RendererContractError(ValueError):
    """Fail-closed renderer selection, config, or frame-contract error.

    Callers must not catch this and draw the native backend instead.
    """


def canonical_dumps(value: Any) -> str:
    """Stable ASCII JSON used for renderer digests."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return sha256(value.encode("ascii")).hexdigest()


def frame_content_sha256(text: str, *, charset: str) -> str:
    """Hash exact backend text.

    This is not the session screen hash. Session observations hash the
    padded ``width × height`` screen. ASCII-7 uses ASCII bytes; the Unicode
    profile uses UTF-8.
    """
    encoding = "ascii" if charset == "ascii7" else "utf-8"
    return sha256(text.encode(encoding)).hexdigest()


def renderer_config_hash(
    *,
    backend_id: str,
    api_version: str,
    options: Mapping[str, Any],
    bond_policy: str,
) -> str:
    payload = {
        "api_version": api_version,
        "backend_id": backend_id,
        "bond_policy": bond_policy,
        "options": dict(options),
    }
    return sha256_text(canonical_dumps(payload))


def validate_bond_policy(value: str) -> str:
    if value not in BOND_POLICIES:
        allowed = ", ".join(BOND_POLICIES)
        raise RendererContractError(f"bond_policy must be one of {allowed}")
    return value


def validate_observation_profile(value: str) -> str:
    if value not in OBSERVATION_PROFILES:
        allowed = ", ".join(OBSERVATION_PROFILES)
        raise RendererContractError(
            f"observation_profile must be one of {allowed}"
        )
    return value


def key_is_forbidden(key: str) -> bool:
    lowered = str(key).lower()
    if lowered in _FORBIDDEN_EXACT_KEYS:
        return True
    return any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS)


def assert_public_payload(value: Any, *, path: str = "$") -> None:
    """Reject contract documents that grew a chemistry or answer field."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key_is_forbidden(str(key)):
                raise RendererContractError(
                    f"render payload field {path}.{key} is not public display data"
                )
            assert_public_payload(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_public_payload(item, path=f"{path}[{index}]")


def bond_graph_provenance(crystal: Any) -> str:
    """Classify the manifested bond graph without copying raw metadata.

    Provenance is graph-level because ``BondIR`` does not carry a per-edge
    source. Unknown metadata strings stay ``unspecified`` and are not copied
    into the request. ``explicit_bond_table`` is the only provided-input flag.
    """
    metadata = getattr(crystal, "metadata", None)
    if not isinstance(metadata, Mapping):
        return "unspecified"
    if metadata.get("explicit_bond_table") is True:
        return "provided"
    source = metadata.get("bond_source")
    if isinstance(source, str):
        return _BOND_SOURCE_PROVENANCE.get(source, "unspecified")
    return "unspecified"


def bond_is_enabled(policy: str, provenance: str) -> bool:
    if policy == "manifested":
        return True
    if policy == "bonds_off":
        return False
    return provenance == "provided"


@dataclass(frozen=True)
class BackendFrame:
    """Optional backend return when a frame has audit diagnostics.

    Diagnostics are host-side. They are not appended to the picture and are
    not copied into agent observations.
    """

    text: str
    diagnostics: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DisplaySite:
    index: int
    atom_id: str
    source_site_id: str
    display_copy_id: str
    display_fragment_id: str
    element: str
    label: str
    cart_angstrom: tuple[float, float, float]
    frac: tuple[float, float, float]
    occupancy: float
    image_shift: tuple[int, int, int]
    symmetry_operation_index: int
    is_minor: bool
    is_boundary_replica: bool
    visible: bool
    molecule_index: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "atom_id": self.atom_id,
            "source_site_id": self.source_site_id,
            "display_copy_id": self.display_copy_id,
            "display_fragment_id": self.display_fragment_id,
            "element": self.element,
            "label": self.label,
            "cart_angstrom": list(self.cart_angstrom),
            "frac": list(self.frac),
            "occupancy": self.occupancy,
            "image_shift": list(self.image_shift),
            "symmetry_operation_index": self.symmetry_operation_index,
            "is_minor": self.is_minor,
            "is_boundary_replica": self.is_boundary_replica,
            "visible": self.visible,
            "molecule_index": self.molecule_index,
        }


@dataclass(frozen=True)
class DisplayBond:
    i: int
    j: int
    image_translation: tuple[int, int, int]
    start_display_copy_id: str
    end_display_copy_id: str
    distance_angstrom: float
    provenance: str
    enabled: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "i": self.i,
            "j": self.j,
            "image_translation": list(self.image_translation),
            "start_display_copy_id": self.start_display_copy_id,
            "end_display_copy_id": self.end_display_copy_id,
            "distance_angstrom": self.distance_angstrom,
            "provenance": self.provenance,
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class DisplayCell:
    a_angstrom: float
    b_angstrom: float
    c_angstrom: float
    alpha_deg: float
    beta_deg: float
    gamma_deg: float
    matrix_angstrom: tuple[tuple[float, float, float], ...]
    periodic_boundary: tuple[bool, bool, bool] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "a_angstrom": self.a_angstrom,
            "b_angstrom": self.b_angstrom,
            "c_angstrom": self.c_angstrom,
            "alpha_deg": self.alpha_deg,
            "beta_deg": self.beta_deg,
            "gamma_deg": self.gamma_deg,
            "matrix_angstrom": [list(row) for row in self.matrix_angstrom],
            "periodic_boundary": (
                None if self.periodic_boundary is None else list(self.periodic_boundary)
            ),
        }


@dataclass(frozen=True)
class DisplayIR:
    sites: tuple[DisplaySite, ...]
    bonds: tuple[DisplayBond, ...]
    cell: DisplayCell | None
    bond_policy: str
    bond_graph_provenance: str
    molecule_groups: tuple[tuple[int, ...], ...]
    unassigned_site_indices: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": DISPLAY_IR_SCHEMA,
            "sites": [site.as_dict() for site in self.sites],
            "bonds": [bond.as_dict() for bond in self.bonds],
            "cell": None if self.cell is None else self.cell.as_dict(),
            "bond_policy": self.bond_policy,
            "bond_graph_provenance": self.bond_graph_provenance,
            "molecule_groups": [list(group) for group in self.molecule_groups],
            "unassigned_site_indices": list(self.unassigned_site_indices),
            "grouping_provenance": GROUPING_PROVENANCE,
        }


@dataclass(frozen=True)
class RenderViewport:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    scale: float
    width: int
    height: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "x_min": self.x_min,
            "x_max": self.x_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "scale": self.scale,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class RenderView:
    projection: str
    azimuth_deg: float
    elevation_deg: float
    roll_deg: float
    distance: float
    fov_deg: float
    perspective_near_is_larger: bool
    target_angstrom: tuple[float, float, float]
    basis_right_up_forward: tuple[tuple[float, float, float], ...]
    viewport_zoom: float
    pan_x: float
    pan_y: float
    projected_xy: tuple[tuple[float, float], ...]
    projected_depth: tuple[float, ...]
    viewport: RenderViewport | None
    ghost_projected_xy: tuple[tuple[float, float], ...] | None
    selected_display_index: int | None
    selected_display_copy_id: str | None
    focus_copy_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "projection": self.projection,
            "azimuth_deg": self.azimuth_deg,
            "elevation_deg": self.elevation_deg,
            "roll_deg": self.roll_deg,
            "distance": self.distance,
            "fov_deg": self.fov_deg,
            "perspective_near_is_larger": self.perspective_near_is_larger,
            "target_angstrom": list(self.target_angstrom),
            "basis_right_up_forward": [
                list(row) for row in self.basis_right_up_forward
            ],
            "viewport_zoom": self.viewport_zoom,
            "pan_x": self.pan_x,
            "pan_y": self.pan_y,
            "projected_xy": [list(point) for point in self.projected_xy],
            "projected_depth": list(self.projected_depth),
            "coordinate_frame": "angstrom_camera_plane",
            "depth_convention": "larger_is_closer",
            "viewport": None if self.viewport is None else self.viewport.as_dict(),
            "ghost_projected_xy": (
                None
                if self.ghost_projected_xy is None
                else [list(point) for point in self.ghost_projected_xy]
            ),
            "selected_display_index": self.selected_display_index,
            "selected_display_copy_id": self.selected_display_copy_id,
            "focus_copy_ids": list(self.focus_copy_ids),
        }


@dataclass(frozen=True)
class RenderOptions:
    charset: str
    width: int
    height: int
    mono: bool
    display_level: str
    label_mode: str
    show_bonds: bool
    show_cell: bool
    show_minor: bool
    show_hydrogen: bool
    show_boundary: bool
    show_axes: bool
    show_depth: bool
    slice_axis: str
    slice_index: int
    slice_width: float
    bond_policy: str
    backend_options: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "charset": self.charset,
            "width": self.width,
            "height": self.height,
            "mono": self.mono,
            "display_level": self.display_level,
            "label_mode": self.label_mode,
            "show_bonds": self.show_bonds,
            "show_cell": self.show_cell,
            "show_minor": self.show_minor,
            "show_hydrogen": self.show_hydrogen,
            "show_boundary": self.show_boundary,
            "show_axes": self.show_axes,
            "show_depth": self.show_depth,
            "slice_axis": self.slice_axis,
            "slice_index": self.slice_index,
            "slice_width": self.slice_width,
            "bond_policy": self.bond_policy,
            "backend_options": dict(self.backend_options),
        }


@dataclass(frozen=True)
class RenderRequest:
    display_ir: DisplayIR
    view: RenderView
    render_options: RenderOptions
    input_digest: str
    view_digest: str
    request_schema_version: str = REQUEST_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "request_schema_version": self.request_schema_version,
            "display_ir": self.display_ir.as_dict(),
            "view": self.view.as_dict(),
            "render_options": self.render_options.as_dict(),
            "input_digest": self.input_digest,
            "view_digest": self.view_digest,
        }
        assert_public_payload(payload)
        return payload


@dataclass(frozen=True)
class RenderedFrame:
    """Host-owned frame identity. Hashes are recomputed by the host."""

    text: str
    lines: tuple[str, ...]
    width: int
    height: int
    charset: str
    backend_id: str
    api_version: str
    package_version: str
    implementation_digest: str
    config_hash: str
    input_digest: str
    view_digest: str
    content_sha256: str
    diagnostics: Mapping[str, Any] | None = None
    schema: str = FRAME_SCHEMA


class RendererBackend(Protocol):
    """Presentation backend. The host owns :class:`RenderedFrame`.

    ``render`` returns picture text, or a :class:`BackendFrame` when the
    backend also has audit diagnostics. MatterVis recomputes the content
    hash, config hash, and backend identity. A plugin cannot supply those
    fields, and it does not receive ``CrystalIR``.
    """

    backend_id: str
    api_version: str
    deterministic: bool
    package_version: str
    implementation_digest: str

    def validate_options(self, options: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return the namespaced options this backend will honor."""
        ...

    def render(self, request: RenderRequest) -> str | BackendFrame:
        """Draw one frame from the public request."""
        ...


def _finite_float(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RendererContractError(f"{name} must be a finite number") from exc
    if not np.isfinite(number):
        raise RendererContractError(f"{name} must be a finite number")
    return number


def _finite_vec3(value: Any, name: str) -> tuple[float, float, float]:
    array = np.asarray(value, dtype=float).reshape(-1)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise RendererContractError(f"{name} must be a finite 3-vector")
    return (float(array[0]), float(array[1]), float(array[2]))


def _int3(value: Any, name: str) -> tuple[int, int, int]:
    try:
        items = tuple(value)
    except TypeError as exc:
        raise RendererContractError(f"{name} must have three integers") from exc
    if len(items) != 3:
        raise RendererContractError(f"{name} must have three integers")
    out: list[int] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, np.integer)):
            raise RendererContractError(f"{name} must have three integers")
        out.append(int(item))
    return (out[0], out[1], out[2])


def _projected_xy(value: Any, count: int, name: str) -> tuple[tuple[float, float], ...]:
    array = np.asarray(value, dtype=float)
    if count == 0:
        if array.size == 0:
            return ()
        raise RendererContractError(f"{name} must align with display sites")
    if array.shape != (count, 2) or not np.all(np.isfinite(array)):
        raise RendererContractError(f"{name} must align with display sites")
    return tuple((float(point[0]), float(point[1])) for point in array)


def _projected_depth(value: Any, count: int) -> tuple[float, ...]:
    array = np.asarray(value, dtype=float).reshape(-1)
    if array.shape != (count,) or not np.all(np.isfinite(array)):
        raise RendererContractError("projected depth must align with display sites")
    return tuple(float(item) for item in array)


def _safe_text(value: Any) -> str:
    return terminal_text("" if value is None else value)


def _periodic_boundary(crystal: Any) -> tuple[bool, bool, bool] | None:
    metadata = getattr(crystal, "metadata", None)
    raw = metadata.get("pbc") if isinstance(metadata, Mapping) else None
    if (
        isinstance(raw, (list, tuple))
        and len(raw) == 3
        and all(isinstance(item, (bool, np.bool_)) for item in raw)
    ):
        return (bool(raw[0]), bool(raw[1]), bool(raw[2]))
    if getattr(crystal, "lattice", None) is not None:
        return (True, True, True)
    return None


def _display_cell(crystal: Any) -> DisplayCell | None:
    lattice = getattr(crystal, "lattice", None)
    if lattice is None:
        return None
    matrix = np.asarray(lattice.matrix, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise RendererContractError("cell matrix must be a finite 3x3 matrix")
    rows = tuple(
        (float(matrix[row, 0]), float(matrix[row, 1]), float(matrix[row, 2]))
        for row in range(3)
    )
    return DisplayCell(
        a_angstrom=_finite_float(lattice.a, "cell.a"),
        b_angstrom=_finite_float(lattice.b, "cell.b"),
        c_angstrom=_finite_float(lattice.c, "cell.c"),
        alpha_deg=_finite_float(lattice.alpha, "cell.alpha"),
        beta_deg=_finite_float(lattice.beta, "cell.beta"),
        gamma_deg=_finite_float(lattice.gamma, "cell.gamma"),
        matrix_angstrom=rows,
        periodic_boundary=_periodic_boundary(crystal),
    )


def _display_sites(crystal: Any, *, show_minor: bool, show_hydrogen: bool, show_boundary: bool) -> tuple[DisplaySite, ...]:
    sites: list[DisplaySite] = []
    for index, atom in enumerate(crystal.atoms):
        sites.append(
            DisplaySite(
                index=index,
                atom_id=_safe_text(atom.atom_id),
                source_site_id=_safe_text(atom.source_site_id),
                display_copy_id=_safe_text(atom.display_copy_id),
                display_fragment_id=_safe_text(atom.display_fragment_id),
                element=_safe_text(atom.element),
                label=_safe_text(atom.label or atom.element),
                cart_angstrom=_finite_vec3(atom.cart, f"site[{index}].cart"),
                frac=_finite_vec3(atom.frac, f"site[{index}].frac"),
                occupancy=_finite_float(atom.occupancy, f"site[{index}].occupancy"),
                image_shift=_int3(atom.image_shift, f"site[{index}].image_shift"),
                symmetry_operation_index=int(atom.symmetry_operation_index),
                is_minor=bool(atom.is_minor),
                is_boundary_replica=bool(atom.is_boundary_replica),
                visible=atom_is_drawn(
                    atom,
                    show_minor=show_minor,
                    show_hydrogen=show_hydrogen,
                    show_boundary=show_boundary,
                ),
                molecule_index=int(atom.molecule_index),
            )
        )
    return tuple(sites)


def _display_bonds(crystal: Any, *, policy: str, provenance: str) -> tuple[DisplayBond, ...]:
    enabled = bond_is_enabled(policy, provenance)
    bonds: list[DisplayBond] = []
    atoms = crystal.atoms
    for bond in crystal.bonds:
        start_id = bond.start_display_copy_id or atoms[bond.i].display_copy_id
        end_id = bond.end_display_copy_id or atoms[bond.j].display_copy_id
        bonds.append(
            DisplayBond(
                i=int(bond.i),
                j=int(bond.j),
                image_translation=_int3(bond.image_relation, "bond image translation"),
                start_display_copy_id=_safe_text(start_id),
                end_display_copy_id=_safe_text(end_id),
                distance_angstrom=_finite_float(bond.distance, "bond distance"),
                provenance=provenance,
                enabled=enabled,
            )
        )
    return tuple(bonds)


def _molecule_groups(
    sites: tuple[DisplaySite, ...],
) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...]]:
    grouped: dict[int, list[int]] = {}
    unassigned: list[int] = []
    for site in sites:
        if site.molecule_index >= 0:
            grouped.setdefault(site.molecule_index, []).append(site.index)
        else:
            unassigned.append(site.index)
    groups = tuple(tuple(grouped[key]) for key in sorted(grouped))
    return groups, tuple(unassigned)


def _viewport_payload(viewport: Any) -> RenderViewport | None:
    if viewport is None:
        return None
    return RenderViewport(
        x_min=_finite_float(viewport.x_min, "viewport.x_min"),
        x_max=_finite_float(viewport.x_max, "viewport.x_max"),
        y_min=_finite_float(viewport.y_min, "viewport.y_min"),
        y_max=_finite_float(viewport.y_max, "viewport.y_max"),
        scale=_finite_float(viewport.scale, "viewport.scale"),
        width=int(viewport.width),
        height=int(viewport.height),
    )


def build_render_request(
    crystal: Any,
    camera: Any,
    pts_2d: Any,
    depth: Any,
    *,
    width: int,
    height: int,
    mono: bool,
    label_mode: str,
    show_bonds: bool,
    show_cell: bool,
    show_minor: bool,
    show_hydrogen: bool,
    show_boundary: bool,
    zoom: float,
    pan_x: float,
    pan_y: float,
    display_level: str,
    viewport: Any,
    selected_display_index: int | None,
    selected_display_copy_id: str | None,
    focus_copy_ids: tuple[str, ...],
    charset: str,
    show_axes: bool,
    show_depth: bool,
    slice_axis: str,
    slice_index: int,
    slice_width: float,
    ghost_points: Any,
    bond_policy: str,
    backend_options: Mapping[str, Any],
) -> RenderRequest:
    """Build a public request from host geometry without reading chemistry."""
    policy = validate_bond_policy(bond_policy)
    provenance = bond_graph_provenance(crystal)
    sites = _display_sites(
        crystal,
        show_minor=show_minor,
        show_hydrogen=show_hydrogen,
        show_boundary=show_boundary,
    )
    count = len(sites)
    groups, unassigned = _molecule_groups(sites)
    basis = np.asarray(camera.rotation_matrix, dtype=float)
    if basis.shape != (3, 3) or not np.all(np.isfinite(basis)):
        raise RendererContractError("camera basis must be a finite 3x3 matrix")
    ghost = None if ghost_points is None else _projected_xy(ghost_points, count, "ghost projection")
    display_ir = DisplayIR(
        sites=sites,
        bonds=_display_bonds(crystal, policy=policy, provenance=provenance),
        cell=_display_cell(crystal),
        bond_policy=policy,
        bond_graph_provenance=provenance,
        molecule_groups=groups,
        unassigned_site_indices=unassigned,
    )
    view = RenderView(
        projection=str(camera.projection.value),
        azimuth_deg=_finite_float(camera.azimuth, "camera.azimuth"),
        elevation_deg=_finite_float(camera.elevation, "camera.elevation"),
        roll_deg=_finite_float(camera.roll, "camera.roll"),
        distance=_finite_float(camera.distance, "camera.distance"),
        fov_deg=_finite_float(camera.fov_deg, "camera.fov_deg"),
        perspective_near_is_larger=bool(camera.perspective_near_is_larger),
        target_angstrom=_finite_vec3(camera.target, "camera.target"),
        basis_right_up_forward=tuple(
            (float(basis[row, 0]), float(basis[row, 1]), float(basis[row, 2]))
            for row in range(3)
        ),
        viewport_zoom=_finite_float(zoom, "viewport zoom"),
        pan_x=_finite_float(pan_x, "pan_x"),
        pan_y=_finite_float(pan_y, "pan_y"),
        projected_xy=_projected_xy(pts_2d, count, "projected coordinates"),
        projected_depth=_projected_depth(depth, count),
        viewport=_viewport_payload(viewport),
        ghost_projected_xy=ghost,
        selected_display_index=selected_display_index,
        selected_display_copy_id=(
            None if selected_display_copy_id is None else _safe_text(selected_display_copy_id)
        ),
        focus_copy_ids=tuple(_safe_text(item) for item in focus_copy_ids),
    )
    options = RenderOptions(
        charset=charset,
        width=int(width),
        height=int(height),
        mono=bool(mono),
        display_level=str(display_level),
        label_mode=str(label_mode),
        show_bonds=bool(show_bonds),
        show_cell=bool(show_cell),
        show_minor=bool(show_minor),
        show_hydrogen=bool(show_hydrogen),
        show_boundary=bool(show_boundary),
        show_axes=bool(show_axes),
        show_depth=bool(show_depth),
        slice_axis=str(slice_axis),
        slice_index=int(slice_index),
        slice_width=_finite_float(slice_width, "slice width"),
        bond_policy=policy,
        backend_options=dict(backend_options),
    )
    display_payload = display_ir.as_dict()
    view_payload = {
        "view": view.as_dict(),
        "render_options": options.as_dict(),
    }
    assert_public_payload(display_payload)
    assert_public_payload(view_payload)
    return RenderRequest(
        display_ir=display_ir,
        view=view,
        render_options=options,
        input_digest=sha256_text(canonical_dumps(display_payload)),
        view_digest=sha256_text(canonical_dumps(view_payload)),
    )


def validate_backend_text(
    text: str,
    *,
    width: int,
    height: int,
    charset: str,
) -> tuple[str, ...]:
    """Reject external frames that overflow or leave the charset profile.

    ASCII-7 is printable 7-bit text with no ANSI escapes. The Unicode profile
    allows the characters the native terminal already accepts, including
    Braille and superscripts, and still rejects C0/C1 controls and ANSI.
    Lines may be shorter than ``width``; the session pads them. A trailing
    newline is rejected so line count stays explicit.
    """
    if not isinstance(text, str):
        raise RendererContractError("renderer frame must be text")
    if text.endswith("\n") or "\r" in text or "\x00" in text:
        raise RendererContractError("renderer frame has an invalid line ending")
    lines = text.split("\n")
    if len(lines) > height:
        raise RendererContractError("renderer frame exceeds the viewport height")
    for line in lines:
        if len(line) > width:
            raise RendererContractError("renderer frame exceeds the viewport width")
        if terminal_text(line) != line or "\x1b" in line:
            raise RendererContractError("renderer frame contains a control character")
        if charset == "ascii7" and any(
            ord(character) < 0x20 or ord(character) > 0x7E for character in line
        ):
            raise RendererContractError(
                "ascii7 frame must be printable 7-bit ASCII without ANSI escapes"
            )
    return tuple(lines)


def validate_diagnostics(diagnostics: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if diagnostics is None:
        return None
    if not isinstance(diagnostics, Mapping):
        raise RendererContractError("renderer diagnostics must be an object")
    try:
        encoded = json.loads(canonical_dumps(dict(diagnostics)))
    except (TypeError, ValueError) as exc:
        raise RendererContractError("renderer diagnostics must be finite JSON") from exc
    assert_public_payload(encoded)
    return encoded


__all__ = [
    "BOND_POLICIES",
    "CONFIG_SCHEMA",
    "DISPLAY_IR_SCHEMA",
    "FRAME_SCHEMA",
    "GROUPING_PROVENANCE",
    "NATIVE_BACKEND_ID",
    "OBSERVATION_PROFILES",
    "RENDERER_ENTRY_GROUP",
    "RENDER_API_VERSION",
    "REPLAY_SCHEMA",
    "REQUEST_SCHEMA",
    "BackendFrame",
    "DisplayBond",
    "DisplayCell",
    "DisplayIR",
    "DisplaySite",
    "RenderOptions",
    "RenderRequest",
    "RenderView",
    "RenderViewport",
    "RenderedFrame",
    "RendererContractError",
    "assert_public_payload",
    "bond_graph_provenance",
    "bond_is_enabled",
    "build_render_request",
    "canonical_dumps",
    "frame_content_sha256",
    "key_is_forbidden",
    "renderer_config_hash",
    "sha256_text",
    "validate_backend_text",
    "validate_bond_policy",
    "validate_diagnostics",
    "validate_observation_profile",
]
