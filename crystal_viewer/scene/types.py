"""Internal scene TypedDict definitions for type-safe rendering pipelines.

These are documentation-first type annotations; they are not enforced at
runtime and do not change the shape of any payload.  Use ``total=False``
throughout so existing code can adopt them incrementally without
breaking on missing keys.

Never import from this module inside constructors that are called at
module-import time; the TypedDict classes must be importable without
pulling in the full MolCrysKit / Plotly dependency graph.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AtomDict(TypedDict, total=False):
    """A single atom in a drawable scene.

    Keys are populated at different stages of the pipeline (loader,
    scene assembly, style tagging, render).  Rendering code should
    guard with ``.get()`` on optional keys rather than assuming every
    key is present.
    """

    # --- identity (loader) ---
    label: str
    elem: str
    frac: list[float]
    cart: list[float]
    atom_radius: float
    is_minor: bool
    disorder_alpha: float
    dg: str  # disorder group (SHELX convention)
    da: str  # disorder assembly (SHELX convention)
    occ: float  # occupancy fraction

    # --- geometry helpers (scene assembly) ---
    _source_index: int
    _source_molecule_index: int
    _image_shift: tuple[int, int, int]
    _wrapped_frac: list[float]
    _depth_t: float
    _is_boundary_replica: bool

    # --- style (applied by style/atom_groups.py & scene core) ---
    color: str
    color_light: str
    _render_color: str
    _render_color_light: str
    _render_visible: bool
    _render_opacity_scale: float
    _render_material: str
    _render_style: str

    # --- publication helpers ---
    _label_x: float
    _label_y: float
    _label_offset: list[float]


class BondDict(TypedDict, total=False):
    """A single bond between two drawable-scene atom indices."""

    i: int
    j: int
    start: list[float]
    end: list[float]
    color_i: str
    color_j: str
    alpha_i: float
    alpha_j: float
    is_minor: bool
    depth_t: float

    # --- style (applied by style/bond_groups.py) ---
    _render_color: str
    _render_visible: bool
    _render_opacity_scale: float
    _render_radius_scale: float


class FragmentDict(TypedDict, total=False):
    """A row in a scene's ``fragment_table`` or ``topology_fragment_table``."""

    index: int
    label: str
    formula: str
    species: str
    type: str  # e.g. "organic", "inorganic", "solvent"
    site_indices: list[int]
    center: list[float]
    frac_center: list[float]
    source_molecule_index: int
    elements: list[str]
    heavy_atom_count: int
    atom_count: int


class CellDict(TypedDict, total=False):
    """Lattice cell parameters (both SimpleNamespace and flat dict forms)."""

    a: float
    b: float
    c: float
    alpha: float
    beta: float
    gamma: float
    volume: float


class StyleDict(TypedDict, total=False):
    """Per-scene rendering style knobs."""

    material: str  # "mesh" | "flat"
    style: str  # "ball" | "ball_stick" | "stick" | "ortep" | "wireframe"
    disorder: str  # "opacity" | "dashed_bonds" | "outline_rings" | "none"
    show_title: bool
    show_hydrogen: bool
    monochrome: bool
    minor_wireframe: bool
    minor_opacity: float
    fast_rendering: bool
    projection: str  # "perspective" | "orthographic"
    camera_eye_distance: float
    element_colors: dict[str, str]
    element_colors_light: dict[str, str]
    axes_labels: list[str]
    display_options: list[str]  # e.g. ["axes", "compass", "scale_bar"]


class SceneDict(TypedDict, total=False):
    """The central drawable scene dict consumed by ``build_figure()``.

    This is the "rendered scene": atoms have been filtered by
    display mode, hydrogens resolved, element colours applied, and
    bonds detected.  It is not the raw parsed CIF.
    """

    name: str
    title: str
    structure_name: str
    display_mode: str
    cell: Any  # SimpleNamespace or CellDict
    M: Any  # lattice matrix (numpy array)
    R: Any  # rotation / view-direction matrix
    view_x: Any
    view_y: Any
    view_z: Any
    selected_atoms: list[AtomDict]
    draw_atoms: list[AtomDict]
    bonds: list[BondDict]
    fragment_table: list[FragmentDict]
    atom_fragment_labels: list[str]
    bounds: dict[str, Any]
    camera: dict[str, Any]
    style: StyleDict
    has_minor: bool

    # --- internal caches (DO NOT modify externally) ---
    _mesh_trace_cache: dict[str, Any]
    _label_trace_cache: dict[str, Any]


class OverlayOverrideDict(TypedDict, total=False):
    """A single row in ``state["overlay_overrides"]``."""

    kind: str  # "compass" | "scale_bar" | "label" | ...
    enabled: bool
    anchors: list[dict[str, Any]]


class PolyhedronSpecDict(TypedDict, total=False):
    """A single row in ``state["polyhedron_specs"]``."""

    id: str
    name: str
    center_species: str
    ligand_species: str
    color: str
    enabled: bool
    enforce_enclosure: bool
    centroid_offset_frac: float
    level: str  # "atom" | "molecule"
    center_kind: str  # "centroid" | ...
    hard_cutoff: float | None
    fallback_max: int | None
    instance_overrides: dict[str, dict[str, Any]]


class TransformSpecDict(TypedDict, total=False):
    """A single row in ``state["transforms"]``."""

    id: str
    name: str
    kind: str  # "repeat" | "grow_radius" | "grow_bonds" | ...
    params: dict[str, Any]
    enabled: bool
