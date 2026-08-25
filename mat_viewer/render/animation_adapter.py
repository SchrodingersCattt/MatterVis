"""Optional GIF/MP4 encoding over the shared CPU RenderPlan renderer."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .contracts import RENDER_RESULT_SCHEMA, RenderResult


def render_animation(
    source: Any,
    output: str | Path,
    *,
    view: Any = None,
    camera: Any = None,
    render_spec: Any = None,
    topology_data: Mapping[str, Any] | None = None,
    atom_groups: Any = None,
    bond_groups: Any = None,
    fps: float = 12.0,
    time_spec: Any = None,
) -> RenderResult:
    """Render selected source frames with one CPU camera, then encode lazily."""
    try:
        import imageio.v2 as imageio
    except ImportError as exc:  # pragma: no cover - minimal CI
        from ..capabilities import resolve_requirements

        install = resolve_requirements("animation").install_command
        raise ImportError(
            "GIF/MP4 encoding requires the animation capability. "
            f"Install with: {install}"
        ) from exc
    if not np.isfinite(fps) or float(fps) <= 0.0:
        raise ValueError("animation fps must be finite and positive")
    frames = tuple(getattr(source, "frames", ()) or ())
    if len(frames) < 2:
        raise ValueError("animation output requires at least two selected frames")

    from .animation_time import (
        coerce_animation_time_spec,
        draw_time_label,
        resolve_animation_times,
    )

    resolved_time_spec = coerce_animation_time_spec(time_spec)
    time_series = (
        resolve_animation_times(frames, resolved_time_spec)
        if resolved_time_spec is not None
        else None
    )
    from PIL import Image

    from .cpu import render_png
    from .planning import prepare_render

    output_path = Path(output).expanduser().resolve()
    output_format = output_path.suffix.lower().lstrip(".")
    if output_format not in {"gif", "mp4"}:
        raise ValueError("animation output must be GIF or MP4")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "gif":
        writer_kwargs: dict[str, Any] = {
            "duration": 1.0 / float(fps),
            "loop": 0,
        }
    else:
        writer_kwargs = {
            "fps": float(fps),
            "codec": "libx264",
            "macro_block_size": None,
        }

    plan_hashes: list[str] = []
    warnings: list[str] = []
    dimensions: tuple[int, int] | None = None
    with imageio.get_writer(output_path, mode="I", **writer_kwargs) as writer:
        for frame_number, frame in enumerate(frames):
            plan = prepare_render(
                frame,
                view=view,
                camera=camera,
                render=render_spec,
                topology_data=topology_data,
                atom_groups=atom_groups,
                bond_groups=bond_groups,
            )
            frame_result = render_png(plan)
            if frame_result.data is None:  # pragma: no cover - renderer contract guard
                raise RuntimeError("CPU frame renderer did not return PNG bytes")
            with Image.open(BytesIO(frame_result.data)) as image:
                rendered = image.convert("RGBA")
                if time_series is not None:
                    rendered = draw_time_label(
                        rendered,
                        time_series.labels[frame_number],
                        time_series.spec.position,
                    )
                rgb = np.asarray(rendered.convert("RGB"), dtype=np.uint8)
            if dimensions is None:
                dimensions = (int(rgb.shape[1]), int(rgb.shape[0]))
            elif dimensions != (int(rgb.shape[1]), int(rgb.shape[0])):
                raise ValueError("all animation frames must have identical dimensions")
            writer.append_data(rgb)
            plan_hashes.append(plan.fingerprint())
            for warning in frame_result.warnings:
                if warning not in warnings:
                    warnings.append(warning)

    data = output_path.read_bytes()
    width, height = dimensions or (0, 0)
    return RenderResult(
        schema=RENDER_RESULT_SCHEMA,
        backend="cpu",
        format=output_format,
        width=width,
        height=height,
        plan_sha256=sha256("".join(plan_hashes).encode()).hexdigest(),
        output_sha256=sha256(data).hexdigest(),
        output=output_path,
        warnings=tuple(warnings),
        metadata={
            "fallback": None,
            "frame_count": len(frames),
            "fps": float(fps),
            "frame_duration_ms": 1000.0 / float(fps),
            "duration_seconds": len(frames) / float(fps),
            "frame_plan_sha256": plan_hashes,
            "simulation_time": (
                time_series.to_metadata()
                if time_series is not None
                else {"displayed": False}
            ),
        },
    )


__all__ = ["render_animation"]
