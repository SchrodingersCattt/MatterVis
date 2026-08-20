"""Dependency capability registry for MatterVis.

This module is intentionally limited to the Python standard library.  It is
safe to import in a minimal installation and is the single source of truth for
extras, install hints, and the agent preflight API.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata, util
import sys
from types import ModuleType
from typing import Iterable, Mapping

CAPABILITIES_SCHEMA = "mattervis.capabilities/v1"
RESOLUTION_SCHEMA = "mattervis.requirements/v1"
DIST_NAME = "matter-vis"
MOLCRYSKIT_MINIMUM = "0.6.2.dev2"
MOLCRYSKIT_CONTRACT_SHA = "a503bbd110dca068b18c06f777f2009f6feb671f"
MOLCRYSKIT_DEVELOPMENT_INSTALL = (
    'python -m pip install "molcrys-kit @ '
    "git+https://github.com/SchrodingersCattt/MolCrysKit.git@"
    f'{MOLCRYSKIT_CONTRACT_SHA}"'
)


def molcryskit_contract_missing() -> tuple[str, ...]:
    """Return missing parts of the public renderer contract.

    The development minimum is intentionally backed by the methods MatterVis
    calls.  This keeps ``--check`` accurate even when an older MolCrysKit is
    importable under the same module name.
    """

    if util.find_spec("molcrys_kit") is None:
        return ("molcrys_kit",)
    try:
        from molcrys_kit.analysis import (
            FormulaUnitSelection,
            RingGeometry,
            StoichiometryAnalyzer,
        )
        from molcrys_kit.structures.crystal import MolecularCrystal
    except (ImportError, AttributeError):
        return ("public structure-contract imports",)
    missing = [
        f"MolecularCrystal.{method}"
        for method in ("get_site_records", "get_bond_records")
        if not callable(getattr(MolecularCrystal, method, None))
    ]
    if not callable(getattr(StoichiometryAnalyzer, "select_formula_unit", None)):
        missing.append("StoichiometryAnalyzer.select_formula_unit")
    if "cycle_atom_indices" not in getattr(RingGeometry, "__dataclass_fields__", {}):
        missing.append("RingGeometry.cycle_atom_indices")
    if not getattr(FormulaUnitSelection, "__dataclass_fields__", None):
        missing.append("FormulaUnitSelection")
    return tuple(missing)


def _molcryskit_contract_available() -> bool:
    return not molcryskit_contract_missing()


@dataclass(frozen=True)
class CapabilitySpec:
    """One optional boundary and the imports that prove it is installed."""

    name: str
    description: str
    extra: str | None
    packages: tuple[str, ...]
    imports: tuple[str, ...]
    includes: tuple[str, ...] = ()
    note: str | None = None

    def available(self) -> bool:
        imports_available = all(
            util.find_spec(module) is not None for module in self.imports
        )
        if not imports_available:
            return False
        return self.name != "core" or _molcryskit_contract_available()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "extra": self.extra,
            "packages": list(self.packages),
            "includes": list(self.includes),
            "available": self.available(),
            "install": install_command((self.extra,) if self.extra else ()),
            "note": self.note,
        }


# Keep the order stable: it is used by JSON output and the skill sync test.
CAPABILITY_REGISTRY: Mapping[str, CapabilitySpec] = {
    "core": CapabilitySpec(
        name="core",
        description=(
            "CPU PNG/PDF/SVG, structure inspection, ORTEP, rings, and polyhedra"
        ),
        extra=None,
        packages=("numpy", "ase", "matplotlib", "pillow", "molcrys-kit"),
        imports=("numpy", "ase", "matplotlib", "PIL", "molcrys_kit"),
        note=(
            "Requires the renderer-ready MolCrysKit public contracts from "
            f"molcrys-kit>={MOLCRYSKIT_MINIMUM}. Until that release exists, "
            "development and CI installs must use the exact contract commit: "
            f"{MOLCRYSKIT_DEVELOPMENT_INSTALL}."
        ),
    ),
    "plotly": CapabilitySpec(
        name="plotly",
        description="Interactive Plotly/WebGL HTML",
        extra="plotly",
        packages=("plotly",),
        imports=("plotly",),
    ),
    "plotly-export": CapabilitySpec(
        name="plotly-export",
        description="Plotly static PNG/PDF/SVG export",
        extra="plotly-export",
        packages=("plotly", "kaleido"),
        imports=("plotly", "kaleido"),
        includes=("plotly",),
        note=(
            "Kaleido static export may also require a working Chrome installation; "
            "MatterVis never installs it automatically."
        ),
    ),
    "web": CapabilitySpec(
        name="web",
        description="Dash viewer, REST API, and WebSocket service",
        extra="web",
        packages=("plotly", "dash", "flask-sock", "flask-compress"),
        imports=("plotly", "dash", "flask_sock", "flask_compress"),
        includes=("plotly",),
    ),
    "tui": CapabilitySpec(
        name="tui",
        description="Interactive Textual terminal viewer",
        extra="tui",
        packages=("textual",),
        imports=("textual",),
    ),
    "cube": CapabilitySpec(
        name="cube",
        description="Cube volumetric isosurfaces",
        extra="cube",
        packages=("scikit-image",),
        imports=("skimage",),
    ),
    "animation": CapabilitySpec(
        name="animation",
        description="GIF and MP4 encoding",
        extra="animation",
        packages=("imageio", "imageio-ffmpeg"),
        imports=("imageio", "imageio_ffmpeg"),
    ),
}


REQUIREMENT_ALIASES: Mapping[str, tuple[str, ...]] = {
    # Base representations and outputs.
    "core": ("core",),
    "cpu": ("core",),
    "structure": ("core",),
    "inspect": ("core",),
    "cif": ("core",),
    "png": ("core",),
    "pdf": ("core",),
    "svg": ("core",),
    "ortep": ("core",),
    "rings": ("core",),
    "polyhedra": ("core",),
    # Optional frontends and encoders.
    "plotly": ("plotly",),
    "html": ("plotly",),
    "plotly-export": ("plotly-export",),
    "kaleido": ("plotly-export",),
    "web": ("web",),
    "dash": ("web",),
    "rest": ("web",),
    "websocket": ("web",),
    "tui": ("tui",),
    "cube": ("cube",),
    "isosurface": ("cube",),
    "animation": ("animation",),
    "gif": ("animation",),
    "mp4": ("animation",),
}


class MissingCapabilityError(RuntimeError):
    """Raised when an explicitly requested capability is unavailable."""

    def __init__(self, resolution: "RequirementResolution") -> None:
        self.resolution = resolution
        missing = ", ".join(resolution.missing_capabilities)
        detail = f" Notes: {' '.join(resolution.notes)}" if resolution.notes else ""
        super().__init__(
            f"MatterVis capability unavailable: {missing}. "
            f"Install with: {resolution.install_command}.{detail}"
        )


@dataclass(frozen=True)
class RequirementResolution:
    """Resolved extras and current availability for agent preflight."""

    requested: tuple[str, ...]
    capabilities: tuple[str, ...]
    extras: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    install_command: str
    notes: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return not self.missing_capabilities

    def require(self) -> "RequirementResolution":
        if not self.available:
            raise MissingCapabilityError(self)
        return self

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": RESOLUTION_SCHEMA,
            "requested": list(self.requested),
            "capabilities": list(self.capabilities),
            "extras": list(self.extras),
            "available": self.available,
            "missing_capabilities": list(self.missing_capabilities),
            "install": self.install_command,
            "notes": list(self.notes),
        }


def _normalise_requirements(
    requirements: str | Iterable[str] | None,
) -> tuple[str, ...]:
    if requirements is None:
        return ()
    values = (requirements,) if isinstance(requirements, str) else requirements
    normalised: list[str] = []
    for raw in values:
        for value in str(raw).split(","):
            key = value.strip().lower().replace("_", "-")
            if key and key not in normalised:
                normalised.append(key)
    return tuple(normalised)


def install_command(extras: Iterable[str] = ()) -> str:
    """Return the exact pip command for a set of MatterVis extras."""

    selected = sorted({str(extra) for extra in extras if extra})
    target = DIST_NAME if not selected else f"{DIST_NAME}[{','.join(selected)}]"
    return f'python -m pip install "{target}"'


def resolve_requirements(
    requirements: str | Iterable[str] | None = None,
) -> RequirementResolution:
    """Resolve user-facing drawing requirements to extras and availability.

    Requirement names are stable CLI vocabulary (for example ``png``,
    ``html``, ``web``, ``cube``, or ``mp4``), not Python package names.
    Unknown names fail instead of being silently ignored.
    """

    requested = _normalise_requirements(requirements)
    unknown = [value for value in requested if value not in REQUIREMENT_ALIASES]
    if unknown:
        supported = ", ".join(sorted(REQUIREMENT_ALIASES))
        raise ValueError(
            f"unknown MatterVis requirement(s): {', '.join(unknown)}; "
            f"supported requirements: {supported}"
        )

    names: list[str] = ["core"]
    for requirement in requested:
        for name in REQUIREMENT_ALIASES[requirement]:
            if name not in names:
                names.append(name)
    extras = tuple(
        sorted(
            {
                CAPABILITY_REGISTRY[name].extra
                for name in names
                if CAPABILITY_REGISTRY[name].extra is not None
            }
        )
    )
    missing = tuple(name for name in names if not CAPABILITY_REGISTRY[name].available())
    notes = tuple(
        spec.note
        for name in names
        if (spec := CAPABILITY_REGISTRY[name]).note is not None
    )
    return RequirementResolution(
        requested=requested,
        capabilities=tuple(names),
        extras=extras,
        missing_capabilities=missing,
        install_command=install_command(extras),
        notes=notes,
    )


def capabilities() -> dict[str, object]:
    """Return the complete JSON-safe capability registry for this environment."""

    try:
        version = metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        version = None
    return {
        "schema": CAPABILITIES_SCHEMA,
        "distribution": DIST_NAME,
        "version": version,
        "capabilities": [spec.to_dict() for spec in CAPABILITY_REGISTRY.values()],
        "requirement_names": sorted(REQUIREMENT_ALIASES),
    }


def requirements_for_render(output: str, backend: str = "cpu") -> tuple[str, ...]:
    """Translate an output suffix/backend pair to preflight requirements."""

    suffix = str(output).lower().rsplit(".", 1)[-1] if "." in str(output) else ""
    if suffix not in {"png", "pdf", "svg", "html", "gif", "mp4"}:
        raise ValueError(f"unsupported MatterVis output format: {suffix or output!r}")
    backend_name = str(backend).strip().lower()
    if backend_name not in {"cpu", "plotly"}:
        raise ValueError("backend must be 'cpu' or 'plotly'")
    if suffix == "html" and backend_name != "plotly":
        raise ValueError("HTML output requires --backend plotly")
    if suffix in {"gif", "mp4"} and backend_name != "cpu":
        raise ValueError("GIF/MP4 output requires --backend cpu")

    required: list[str] = [suffix]
    if backend_name == "plotly":
        required.append("plotly" if suffix == "html" else "plotly-export")
    if suffix in {"gif", "mp4"}:
        required.append("animation")
    return tuple(required)


class _CallableCapabilitiesModule(ModuleType):
    """Preserve ``mat_viewer.capabilities()`` after submodule import."""

    def __call__(self) -> dict[str, object]:
        return capabilities()


__all__ = [
    "CAPABILITIES_SCHEMA",
    "CAPABILITY_REGISTRY",
    "MOLCRYSKIT_MINIMUM",
    "MOLCRYSKIT_CONTRACT_SHA",
    "MOLCRYSKIT_DEVELOPMENT_INSTALL",
    "CapabilitySpec",
    "MissingCapabilityError",
    "RequirementResolution",
    "capabilities",
    "install_command",
    "molcryskit_contract_missing",
    "requirements_for_render",
    "resolve_requirements",
]


sys.modules[__name__].__class__ = _CallableCapabilitiesModule
