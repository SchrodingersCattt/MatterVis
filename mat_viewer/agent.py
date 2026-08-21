"""Small, lazy, agent-facing MatterVis API.

No renderer or optional frontend is imported until its operation is called.
MolCrysKit's public records are a hard runtime contract; MatterVis never falls
back to its historical private ``.info`` payloads.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from .capabilities import (
    MOLCRYSKIT_DEVELOPMENT_INSTALL,
    MOLCRYSKIT_MINIMUM,
    MissingCapabilityError,
    capabilities,
    molcryskit_contract_missing,
    requirements_for_render,
    resolve_requirements,
)


def _require_plan_backend(plan: Any, backend_name: str) -> None:
    requested_backend = plan.metadata.get("requested_backend")
    if requested_backend and str(requested_backend) != backend_name:
        raise ValueError(
            "RenderPlan requested backend "
            f"{requested_backend!r}, but render() received backend={backend_name!r}"
        )


def _bind_render_backend(render_spec: Any, backend_name: str) -> Any:
    """Inject an omitted backend and reject an explicitly conflicting one."""

    from .render.contracts import RenderSpec

    if render_spec is None:
        return {"backend": backend_name}
    if isinstance(render_spec, RenderSpec):
        if render_spec.backend != backend_name:
            raise ValueError(
                f"RenderSpec requested backend {render_spec.backend!r}, but "
                f"render() received backend={backend_name!r}"
            )
        return render_spec
    if isinstance(render_spec, Mapping):
        payload = dict(render_spec)
        requested = payload.get("backend")
        if requested is not None and str(requested) != backend_name:
            raise ValueError(
                f"render spec requested backend {requested!r}, but "
                f"render() received backend={backend_name!r}"
            )
        payload["backend"] = backend_name
        return payload
    return render_spec


def _camera_metadata(camera: Any) -> Any:
    if is_dataclass(camera):
        return asdict(camera)
    if isinstance(camera, Mapping):
        return dict(camera)
    return camera


def _source_metadata(source: Any, plan: Any | None) -> dict[str, Any]:
    plan_metadata = dict(getattr(plan, "metadata", {}) or {})
    path = plan_metadata.get("source") or getattr(source, "path", None)
    result: dict[str, Any] = {
        "path": str(path) if path is not None else None,
        "input_format": plan_metadata.get("input_format")
        or getattr(source, "input_format", None),
        "frame": plan_metadata.get("frame_index"),
    }
    frames = tuple(getattr(source, "frames", ()) or ())
    if frames:
        result["selected_frames"] = [getattr(frame, "index", None) for frame in frames]
        if result["frame"] is None:
            result["frame"] = getattr(frames[0], "index", None)
    if path is not None:
        source_path = Path(path)
        if source_path.is_file():
            digest = sha256()
            with source_path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            result["sha256"] = digest.hexdigest()
    return result


def _enrich_result(
    result: Any,
    *,
    source: Any,
    plan: Any | None,
    camera: Any,
    resolution: Any,
    backend_name: str,
) -> Any:
    metadata = dict(getattr(result, "metadata", {}) or {})
    if plan is not None:
        cameras = [_camera_metadata(viewport.camera) for viewport in plan.viewports]
        camera_payload: Any = cameras[0] if len(cameras) == 1 else cameras
    else:
        camera_payload = _camera_metadata(camera)
    metadata.update(
        {
            "actual_backend": backend_name,
            "camera": camera_payload,
            "source": _source_metadata(source, plan),
            "requirements": resolution.to_dict(),
            "install": resolution.install_command,
            "fallback": None,
        }
    )
    return replace(result, metadata=metadata)


def require_molcryskit_contract() -> None:
    """Fail clearly when the renderer-ready MolCrysKit API is unavailable."""

    missing = molcryskit_contract_missing()
    if missing:
        raise RuntimeError(
            "The installed MolCrysKit lacks the renderer-ready public contract "
            f"({', '.join(missing)}). MatterVis requires "
            f"molcrys-kit>={MOLCRYSKIT_MINIMUM}; "
            "upgrade MolCrysKit before rendering. During the two-PR development "
            f"window, install the exact contract commit with: "
            f"{MOLCRYSKIT_DEVELOPMENT_INSTALL}."
        )


def load_structure(
    source: str | Path,
    *,
    input_format: str | None = None,
    type_map: Iterable[str] | None = None,
    frame: int = 0,
    frame_indices: Iterable[int] | None = None,
) -> Any:
    """Load one canonical structure frame without importing any frontend."""

    requirements = ["structure"]
    if Path(source).suffix.lower() == ".cube" or str(input_format).lower() == "cube":
        requirements.append("cube")
    resolve_requirements(requirements).require()
    require_molcryskit_contract()
    from .loader.structure_input import load_structure_input

    return load_structure_input(
        source,
        input_format=input_format,
        type_map=type_map,
        frame_indices=list(frame_indices) if frame_indices is not None else [frame],
    )


def prepare_render(
    source: Any,
    view: Any = None,
    camera: Any = None,
    render_spec: Any = None,
    *,
    topology_data: Mapping[str, Any] | None = None,
) -> Any:
    """Compile a structure and explicit specs into a backend-neutral plan."""

    if isinstance(source, (str, Path)):
        source = load_structure(source)
    if (
        getattr(source, "input_format", None) == "cube"
        or getattr(source, "cube_data", None) is not None
    ):
        resolve_requirements("cube").require()
        from .cube.cpu import ensure_cube_isosurfaces

        source = ensure_cube_isosurfaces(source)
    resolve_requirements("cpu").require()
    from .render.planning import prepare_render as _prepare_render

    plan = _prepare_render(
        source,
        view=view,
        camera=camera,
        render=render_spec,
        topology_data=topology_data,
    )
    topology_warnings = tuple(
        str(warning) for warning in (topology_data or {}).get("warnings", ())
    )
    if topology_warnings:
        merged_warnings = tuple(dict.fromkeys((*plan.warnings, *topology_warnings)))
        plan = replace(plan, warnings=merged_warnings)
    return plan


def render(
    source_or_plan: Any,
    *,
    output: str | Path | None = None,
    backend: str = "cpu",
    view: Any = None,
    camera: Any = None,
    render_spec: Any = None,
    topology_data: Mapping[str, Any] | None = None,
    fps: float = 12.0,
) -> Any:
    """Render with an explicit backend; no backend or representation fallback."""

    backend_name = str(backend).strip().lower()
    from .render.contracts import RenderPlan

    if isinstance(source_or_plan, RenderPlan):
        _require_plan_backend(source_or_plan, backend_name)
        if any(
            value is not None for value in (view, camera, render_spec, topology_data)
        ):
            raise ValueError(
                "view, camera, render_spec, and topology_data cannot be supplied "
                "when rendering an existing RenderPlan"
            )
        bound_render_spec = None
    else:
        bound_render_spec = _bind_render_backend(render_spec, backend_name)
    if output is not None:
        resolution = resolve_requirements(
            requirements_for_render(str(output), backend_name)
        )
    else:
        resolution = resolve_requirements(
            "plotly" if backend_name == "plotly" else "cpu"
        )
    resolution.require()

    output_suffix = Path(output).suffix.lower() if output is not None else ""
    if output_suffix in {".gif", ".mp4"}:
        if backend_name != "cpu":
            raise ValueError(
                "GIF/MP4 use the shared CPU frame renderer; select backend='cpu'"
            )
        from .render.animation_adapter import render_animation

        effective_camera = camera
        if effective_camera is None:
            frames = tuple(getattr(source_or_plan, "frames", ()) or ())
            if not frames:
                raise ValueError("animation source contains no selected frames")
            first_plan = prepare_render(
                frames[0],
                view=view,
                render_spec=bound_render_spec,
                topology_data=topology_data,
            )
            _require_plan_backend(first_plan, backend_name)
            effective_camera = first_plan.camera
        result = render_animation(
            source_or_plan,
            output,
            view=view,
            camera=effective_camera,
            render_spec=bound_render_spec,
            topology_data=topology_data,
            fps=fps,
        )
        return _enrich_result(
            result,
            source=source_or_plan,
            plan=None,
            camera=effective_camera,
            resolution=resolution,
            backend_name=backend_name,
        )

    plan = source_or_plan
    if not isinstance(source_or_plan, RenderPlan):
        plan = prepare_render(
            source_or_plan,
            view=view,
            camera=camera,
            render_spec=bound_render_spec,
            topology_data=topology_data,
        )
        _require_plan_backend(plan, backend_name)

    if backend_name == "cpu":
        from .render.cpu import render as _render_cpu

        result = _render_cpu(plan, output=output)
    elif backend_name == "matplotlib":
        from .render.matplotlib import render as _render_matplotlib

        result = _render_matplotlib(plan, output=output)
    elif backend_name == "plotly":
        try:
            from .render.plotly import render as _render_plotly
        except ImportError as exc:
            raise RuntimeError(
                "The Plotly backend adapter is unavailable in this build; "
                "MatterVis will not fall back to CPU implicitly."
            ) from exc
        result = _render_plotly(plan, output=output)
    else:
        raise ValueError("backend must be 'cpu', 'matplotlib', or 'plotly'")
    return _enrich_result(
        result,
        source=source_or_plan,
        plan=plan,
        camera=plan.camera if len(plan.viewports) == 1 else None,
        resolution=resolution,
        backend_name=backend_name,
    )


__all__ = [
    "MissingCapabilityError",
    "capabilities",
    "load_structure",
    "prepare_render",
    "render",
    "require_molcryskit_contract",
    "resolve_requirements",
]
