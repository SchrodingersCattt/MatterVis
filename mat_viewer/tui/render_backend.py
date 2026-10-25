"""Renderer dispatch, native adapter, and entry-point registry.

Both the static CLI and the live terminal session call
:func:`render_picture`. The default backend is ``mattervis.native-v1``.
It delegates to :func:`mat_viewer.tui.compositor.compose_frame` with the
host's crystal and camera, so an unset renderer leaves picture bytes
unchanged.

External backends are loaded from the ``mattervis.tui.renderers`` entry
point group. A requested backend that is missing, incompatible, or
crashes raises :class:`RendererContractError`. Dispatch never substitutes
the native backend.

The native adapter is the only renderer that may see host crystal
objects, and only through a context set by this module. The object passed
to ``render`` is still the public :class:`RenderRequest`.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import Distribution, PackageNotFoundError, entry_points
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np

from .compositor import compose_frame
from .render_contract import (
    CONFIG_SCHEMA,
    NATIVE_BACKEND_ID,
    RENDER_API_VERSION,
    RENDERER_ENTRY_GROUP,
    BackendFrame,
    RenderedFrame,
    RendererContractError,
    RenderRequest,
    build_render_request,
    frame_content_sha256,
    renderer_config_hash,
    validate_backend_text,
    validate_bond_policy,
    validate_diagnostics,
    validate_observation_profile,
)

_HOST_SOURCES: ContextVar[_HostFrameSources | None] = ContextVar(
    "mattervis_native_host_sources",
    default=None,
)
_CONFIG_KEYS = frozenset(
    {
        "schema",
        "backend_id",
        "api_version",
        "options",
        "bond_policy",
        "require_package_version",
        "require_implementation_digest",
    }
)


@dataclass(frozen=True)
class _HostFrameSources:
    """Private compositor inputs. Not part of the public request."""

    crystal: Any
    camera: Any
    pts_2d: Any
    depth: Any
    compose_kwargs: Mapping[str, Any]


def native_host_sources_bound() -> bool:
    """Whether dispatch is inside the built-in native adapter.

    In-process plugins can import private modules. This flag is an audit
    aid, not a sandbox. Untrusted renderers need a separate process.
    """
    return _HOST_SOURCES.get() is not None


@lru_cache(maxsize=1)
def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("matter-vis")
    except PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=1)
def native_implementation_digest() -> str:
    """Digest the terminal package that produces the native picture."""
    root = Path(__file__).resolve().parent
    digest = sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class NativeRenderer:
    """Built-in adapter. Picture bytes come from ``compose_frame``."""

    backend_id = NATIVE_BACKEND_ID
    api_version = RENDER_API_VERSION
    deterministic = True

    @property
    def package_version(self) -> str:
        return _package_version()

    @property
    def implementation_digest(self) -> str:
        return native_implementation_digest()

    def validate_options(self, options: Mapping[str, Any]) -> Mapping[str, Any]:
        if options:
            keys = ", ".join(sorted(str(key) for key in options))
            raise RendererContractError(
                f"{self.backend_id} does not accept backend options ({keys})"
            )
        return MappingProxyType({})

    def render(self, request: RenderRequest) -> str:
        del request
        sources = _HOST_SOURCES.get()
        if sources is None:
            raise RendererContractError(
                "mattervis.native-v1 renders only through MatterVis frame dispatch"
            )
        return compose_frame(
            sources.crystal,
            sources.camera,
            sources.pts_2d,
            sources.depth,
            **sources.compose_kwargs,
        )


def create_native_renderer() -> NativeRenderer:
    """Entry-point factory for the reserved built-in id."""
    return NativeRenderer()


@dataclass(frozen=True)
class RendererBinding:
    """Renderer chosen once for an episode."""

    backend: Any
    backend_id: str
    api_version: str
    package_version: str
    implementation_digest: str
    options: Mapping[str, Any]
    bond_policy: str
    observation_profile: str
    deterministic: bool
    emit_provenance: bool
    selection: str
    config_hash: str

    def replay_dict(self) -> dict[str, Any]:
        from .render_contract import REPLAY_SCHEMA

        return {
            "schema": REPLAY_SCHEMA,
            "backend_id": self.backend_id,
            "api_version": self.api_version,
            "package_version": self.package_version,
            "implementation_digest": self.implementation_digest,
            "config_hash": self.config_hash,
            "options": dict(self.options),
            "bond_policy": self.bond_policy,
            "deterministic": self.deterministic,
            "observation_profile": self.observation_profile,
            "selection": self.selection,
        }


def _entry_points(search_path: Sequence[str] | None) -> list[Any]:
    if search_path is None:
        return list(entry_points(group=RENDERER_ENTRY_GROUP))
    found = []
    for distribution in Distribution.discover(path=list(search_path)):
        for item in distribution.entry_points:
            if item.group == RENDERER_ENTRY_GROUP:
                found.append(item)
    return found


def list_renderers(search_path: Sequence[str] | None = None) -> dict[str, dict[str, Any]]:
    """Return the built-in id plus installed entry points.

    An entry point that reuses ``mattervis.native-v1`` is ignored. The
    built-in id is reserved so a plugin cannot replace the default adapter.
    """
    found: dict[str, dict[str, Any]] = {
        NATIVE_BACKEND_ID: {
            "builtin": True,
            "api_version": RENDER_API_VERSION,
            "ambiguous": False,
        }
    }
    for item in _entry_points(search_path):
        if item.name == NATIVE_BACKEND_ID:
            continue
        current = found.get(item.name)
        if current is None:
            found[item.name] = {
                "builtin": False,
                "api_version": None,
                "ambiguous": False,
            }
        else:
            current["ambiguous"] = True
    return found


def _require_backend(backend: Any, backend_id: str) -> None:
    required = (
        "backend_id",
        "api_version",
        "deterministic",
        "package_version",
        "implementation_digest",
        "validate_options",
        "render",
    )
    missing = [name for name in required if not hasattr(backend, name)]
    if missing:
        names = ", ".join(missing)
        raise RendererContractError(
            f"renderer {backend_id!r} is missing required attributes: {names}"
        )
    if backend.backend_id != backend_id:
        raise RendererContractError(
            f"renderer {backend_id!r} loaded a backend id of {backend.backend_id!r}"
        )
    if backend.api_version != RENDER_API_VERSION:
        raise RendererContractError(
            f"renderer {backend_id!r} implements API {backend.api_version!r}, "
            f"not {RENDER_API_VERSION!r}"
        )
    if backend.deterministic is not True:
        raise RendererContractError(
            f"renderer {backend_id!r} is not deterministic; "
            "nondeterministic backends are rejected"
        )
    if not isinstance(backend.package_version, str) or not backend.package_version:
        raise RendererContractError(
            f"renderer {backend_id!r} has no package version"
        )
    digest = backend.implementation_digest
    if not isinstance(digest, str) or len(digest) != 64:
        raise RendererContractError(
            f"renderer {backend_id!r} has no sha256 implementation digest"
        )


def _load_entry_backend(backend_id: str, search_path: Sequence[str] | None) -> Any:
    matches = [item for item in _entry_points(search_path) if item.name == backend_id]
    if not matches:
        raise RendererContractError(
            f"renderer {backend_id!r} is not installed "
            f"(entry point group {RENDERER_ENTRY_GROUP!r}); "
            f"refusing to fall back to {NATIVE_BACKEND_ID}"
        )
    if len(matches) > 1:
        raise RendererContractError(
            f"renderer {backend_id!r} is provided by multiple installations"
        )
    try:
        factory = matches[0].load()
    except Exception as exc:
        raise RendererContractError(f"failed to load renderer {backend_id!r}") from exc
    try:
        backend = factory()
    except Exception as exc:
        raise RendererContractError(
            f"renderer {backend_id!r} failed to construct"
        ) from exc
    return backend


def _plain_json(value: Any, path: str) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not np.isfinite(value):
            raise RendererContractError(f"{path} must be finite")
        return value
    if isinstance(value, Mapping):
        output = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise RendererContractError(f"{path} keys must be non-empty strings")
            output[key] = _plain_json(item, f"{path}.{key}")
        return output
    if isinstance(value, (list, tuple)):
        return [_plain_json(item, f"{path}[]") for item in value]
    raise RendererContractError(f"{path} is not a JSON value")


def load_renderer_config(source: str | os.PathLike[str] | Mapping[str, Any]) -> dict[str, Any]:
    """Read a renderer config object. Unknown fields fail closed."""
    if isinstance(source, Mapping):
        data = dict(source)
    else:
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RendererContractError(f"renderer config not found: {path}") from exc
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RendererContractError("renderer config is not valid JSON") from exc
    if not isinstance(data, dict):
        raise RendererContractError("renderer config must be a JSON object")
    if data.get("schema") != CONFIG_SCHEMA:
        raise RendererContractError(f"renderer config schema must be {CONFIG_SCHEMA!r}")
    extra = sorted(set(data) - _CONFIG_KEYS)
    if extra:
        raise RendererContractError(
            "renderer config has unsupported fields: " + ", ".join(extra)
        )
    if "api_version" in data and data["api_version"] != RENDER_API_VERSION:
        raise RendererContractError(
            f"renderer config API {data['api_version']!r} is not {RENDER_API_VERSION!r}"
        )
    if "backend_id" in data and (
        not isinstance(data["backend_id"], str) or not data["backend_id"]
    ):
        raise RendererContractError("renderer config backend_id must be text")
    if "bond_policy" in data:
        validate_bond_policy(data["bond_policy"])
    if "options" in data:
        data["options"] = _plain_json(data["options"], "options")
        if not isinstance(data["options"], dict):
            raise RendererContractError("renderer options must be an object")
    for pin in ("require_package_version", "require_implementation_digest"):
        if pin in data and data[pin] is not None and not isinstance(data[pin], str):
            raise RendererContractError(f"renderer config {pin} must be text")
    return data


def _validated_options(backend: Any, options: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        validated = backend.validate_options(dict(options))
    except RendererContractError:
        raise
    except Exception as exc:
        raise RendererContractError(
            f"renderer {backend.backend_id!r} rejected its options: {exc}"
        ) from exc
    if not isinstance(validated, Mapping):
        raise RendererContractError(
            f"renderer {backend.backend_id!r} validate_options must return a mapping"
        )
    return MappingProxyType(dict(validated))


def _check_pins(backend: Any, config: Mapping[str, Any] | None) -> bool:
    if not config:
        return False
    version = config.get("require_package_version")
    digest = config.get("require_implementation_digest")
    pinned = bool(version or digest)
    if version is not None and backend.package_version != version:
        raise RendererContractError(
            f"renderer {backend.backend_id!r} package version "
            f"{backend.package_version!r} does not match pin {version!r}"
        )
    if digest is not None and backend.implementation_digest != digest:
        raise RendererContractError(
            f"renderer {backend.backend_id!r} implementation digest does not match the pin"
        )
    return pinned


def resolve_renderer_binding(
    renderer: Any = None,
    *,
    renderer_options: Mapping[str, Any] | None = None,
    renderer_config: str | os.PathLike[str] | Mapping[str, Any] | None = None,
    renderer_search_path: Sequence[str] | None = None,
    bond_policy: str | None = None,
    observation_profile: str = "legacy",
) -> RendererBinding:
    """Resolve one renderer for an episode. Never falls back to native."""
    profile = validate_observation_profile(observation_profile)
    config = None if renderer_config is None else load_renderer_config(renderer_config)
    config_id = None if config is None else config.get("backend_id")
    config_policy = None if config is None else config.get("bond_policy")
    if bond_policy is not None and config_policy is not None and bond_policy != config_policy:
        raise RendererContractError("renderer bond_policy conflicts with the config file")
    policy = validate_bond_policy(bond_policy or config_policy or "manifested")
    direct_options = dict(renderer_options or {})
    config_options = dict(config.get("options") or {}) if config else {}
    if direct_options and config_options:
        raise RendererContractError(
            "renderer options were passed both directly and in the config"
        )
    options = direct_options or config_options

    selection = "builtin"
    if isinstance(renderer, NativeRenderer):
        backend_id = NATIVE_BACKEND_ID
        backend: Any = renderer
        selection = "instance"
    elif renderer is None or renderer == NATIVE_BACKEND_ID:
        backend_id = NATIVE_BACKEND_ID
        backend = NativeRenderer()
        selection = "builtin"
    elif isinstance(renderer, str):
        backend_id = renderer
        backend = _load_entry_backend(backend_id, renderer_search_path)
        selection = "entry_point"
    else:
        backend = renderer
        backend_id = str(getattr(backend, "backend_id", ""))
        selection = "instance"
        if not isinstance(backend, NativeRenderer) and backend_id == NATIVE_BACKEND_ID:
            raise RendererContractError(
                f"{NATIVE_BACKEND_ID} is reserved for the built-in adapter"
            )

    if config_id is not None and config_id != backend_id:
        raise RendererContractError(
            f"renderer id {backend_id!r} does not match config id {config_id!r}"
        )
    _require_backend(backend, backend_id)
    validated = _validated_options(backend, options)
    pinned = _check_pins(backend, config)
    emit = (
        profile != "legacy"
        or backend_id != NATIVE_BACKEND_ID
        or bool(validated)
        or policy != "manifested"
        or pinned
        or selection == "instance" and backend_id != NATIVE_BACKEND_ID
    )
    stored_backend = None if selection == "builtin" and backend_id == NATIVE_BACKEND_ID and not pinned else backend
    if selection == "builtin" and not pinned:
        stored_backend = None
    return RendererBinding(
        backend=stored_backend,
        backend_id=backend_id,
        api_version=backend.api_version,
        package_version=backend.package_version,
        implementation_digest=backend.implementation_digest,
        options=validated,
        bond_policy=policy,
        observation_profile=profile,
        deterministic=True,
        emit_provenance=emit,
        selection="builtin" if stored_backend is None else selection,
        config_hash=renderer_config_hash(
            backend_id=backend_id,
            api_version=backend.api_version,
            options=validated,
            bond_policy=policy,
        ),
    )


def _resolve_frame_size(width: int | None, height: int | None) -> tuple[int, int]:
    """Match ``compose_frame`` when width or height is omitted."""
    if width is None or height is None:
        try:
            term_size = os.get_terminal_size()
            width = width or term_size.columns - 2
            height = height or term_size.lines - 4
        except OSError:
            width = width or 80
            height = height or 40
    return max(int(width), 1), max(int(height), 1)


def _drawing_crystal(crystal: Any, bond_policy: str, show_bonds: bool) -> tuple[Any, bool]:
    """Return the crystal and bond toggle actually passed to ``compose_frame``.

    The default policy returns the original crystal object. ``provided_only``
    drops inferred edges for drawing and does not mutate the source graph.
    """
    policy = validate_bond_policy(bond_policy)
    if policy == "bonds_off":
        return crystal, False
    if policy == "manifested":
        return crystal, show_bonds
    from .render_contract import bond_graph_provenance

    if bond_graph_provenance(crystal) == "provided":
        return crystal, show_bonds
    clone = copy.copy(crystal)
    clone.bonds = []
    return clone, show_bonds


def _backend_result(raw: Any) -> tuple[str, Mapping[str, Any] | None]:
    if isinstance(raw, str):
        return raw, None
    if isinstance(raw, BackendFrame):
        return raw.text, raw.diagnostics
    raise RendererContractError("renderer must return str or BackendFrame")


def _host_frame(
    text: str,
    request: RenderRequest,
    backend: Any,
    *,
    strict: bool,
    diagnostics: Mapping[str, Any] | None,
) -> RenderedFrame:
    charset = request.render_options.charset
    width = request.render_options.width
    height = request.render_options.height
    if strict:
        lines = validate_backend_text(text, width=width, height=height, charset=charset)
        checked = validate_diagnostics(diagnostics)
    else:
        lines = tuple(text.split("\n"))
        checked = None
    return RenderedFrame(
        text=text,
        lines=lines,
        width=width,
        height=height,
        charset=charset,
        backend_id=backend.backend_id,
        api_version=backend.api_version,
        package_version=backend.package_version,
        implementation_digest=backend.implementation_digest,
        config_hash=renderer_config_hash(
            backend_id=backend.backend_id,
            api_version=backend.api_version,
            options=request.render_options.backend_options,
            bond_policy=request.render_options.bond_policy,
        ),
        input_digest=request.input_digest,
        view_digest=request.view_digest,
        content_sha256=frame_content_sha256(text, charset=charset),
        diagnostics=checked,
    )


def render_picture(
    crystal: Any,
    camera: Any,
    pts_2d: Any,
    depth: Any,
    *,
    width: int | None = None,
    height: int | None = None,
    mono: bool = False,
    label_mode: str = "label",
    show_bonds: bool = True,
    show_cell: bool = True,
    show_minor: bool = True,
    show_hydrogen: bool = False,
    show_boundary: bool = False,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    display_level: str = "atom",
    viewport: Any = None,
    selected_display_index: int | None = None,
    selected_display_copy_id: str | None = None,
    focus_copy_ids: tuple[str, ...] = (),
    charset: str = "unicode",
    show_axes: bool = True,
    show_depth: bool = True,
    slice_axis: str = "off",
    slice_index: int = 0,
    slice_width: float = 1.0,
    ghost_points: Any = None,
    backend: Any = None,
    backend_options: Mapping[str, Any] | None = None,
    bond_policy: str = "manifested",
) -> RenderedFrame:
    """Render one terminal picture through the selected backend.

    ``backend=None`` is ``mattervis.native-v1``. Native text is the return
    value of ``compose_frame`` for the same arguments.
    """
    if charset not in {"unicode", "ascii7"}:
        raise ValueError("charset must be 'unicode' or 'ascii7'")
    active = NativeRenderer() if backend is None else backend
    if active.backend_id == NATIVE_BACKEND_ID and not isinstance(active, NativeRenderer):
        raise RendererContractError(
            f"{NATIVE_BACKEND_ID} is reserved for the built-in adapter"
        )
    options = _validated_options(active, dict(backend_options or {}))
    frame_width, frame_height = _resolve_frame_size(width, height)
    draw_crystal, draw_bonds = _drawing_crystal(crystal, bond_policy, show_bonds)
    request = build_render_request(
        crystal,
        camera,
        pts_2d,
        depth,
        width=frame_width,
        height=frame_height,
        mono=mono,
        label_mode=label_mode,
        show_bonds=show_bonds,
        show_cell=show_cell,
        show_minor=show_minor,
        show_hydrogen=show_hydrogen,
        show_boundary=show_boundary,
        zoom=zoom,
        pan_x=pan_x,
        pan_y=pan_y,
        display_level=display_level,
        viewport=viewport,
        selected_display_index=selected_display_index,
        selected_display_copy_id=selected_display_copy_id,
        focus_copy_ids=focus_copy_ids,
        charset=charset,
        show_axes=show_axes,
        show_depth=show_depth,
        slice_axis=slice_axis,
        slice_index=slice_index,
        slice_width=slice_width,
        ghost_points=ghost_points,
        bond_policy=bond_policy,
        backend_options=options,
    )
    compose_kwargs = {
        "width": frame_width,
        "height": frame_height,
        "mono": mono,
        "label_mode": label_mode,
        "show_bonds": draw_bonds,
        "show_cell": show_cell,
        "show_minor": show_minor,
        "show_hydrogen": show_hydrogen,
        "show_boundary": show_boundary,
        "zoom": zoom,
        "pan_x": pan_x,
        "pan_y": pan_y,
        "display_level": display_level,
        "viewport": viewport,
        "selected_display_index": selected_display_index,
        "charset": charset,
        "show_axes": show_axes,
        "show_depth": show_depth,
        "slice_axis": slice_axis,
        "slice_index": slice_index,
        "slice_width": slice_width,
        "ghost_points": ghost_points,
    }
    if isinstance(active, NativeRenderer):
        token = _HOST_SOURCES.set(
            _HostFrameSources(
                crystal=draw_crystal,
                camera=camera,
                pts_2d=pts_2d,
                depth=depth,
                compose_kwargs=compose_kwargs,
            )
        )
        try:
            raw = active.render(request)
        finally:
            _HOST_SOURCES.reset(token)
        text, _diagnostics = _backend_result(raw)
        return _host_frame(text, request, active, strict=False, diagnostics=None)
    raw = active.render(request)
    text, diagnostics = _backend_result(raw)
    return _host_frame(text, request, active, strict=True, diagnostics=diagnostics)


def renderer_binding_from_cli(args: Any) -> RendererBinding:
    """Resolve ``--renderer`` / ``--renderer-config`` from a CLI namespace."""
    return resolve_renderer_binding(
        getattr(args, "renderer", None),
        renderer_config=getattr(args, "renderer_config", None),
        observation_profile=getattr(args, "observation_profile", "legacy"),
    )


__all__ = [
    "NATIVE_BACKEND_ID",
    "RENDERER_ENTRY_GROUP",
    "RENDER_API_VERSION",
    "NativeRenderer",
    "RendererBinding",
    "RendererContractError",
    "create_native_renderer",
    "list_renderers",
    "load_renderer_config",
    "native_host_sources_bound",
    "native_implementation_digest",
    "render_picture",
    "renderer_binding_from_cli",
    "resolve_renderer_binding",
]
