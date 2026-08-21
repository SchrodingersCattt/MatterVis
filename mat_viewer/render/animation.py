"""Animation encoding helpers shared by render CLI paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def encode_frame_paths(
    frame_paths: list[Path],
    output_path: Path,
    *,
    fps: float,
) -> None:
    """Encode already-rendered PNG frames without changing their order."""
    from PIL import Image

    images = [Image.open(path).convert("RGB") for path in frame_paths]
    try:
        if output_path.suffix.lower() == ".gif":
            duration_ms = max(1, round(1000.0 / fps))
            images[0].save(
                output_path,
                save_all=True,
                append_images=images[1:],
                duration=duration_ms,
                loop=0,
            )
        else:
            import imageio.v3 as iio
            import numpy as np

            iio.imwrite(
                output_path,
                np.stack([np.asarray(image) for image in images]),
                fps=fps,
                codec="libx264",
                macro_block_size=1,
            )
    finally:
        for image in images:
            image.close()


def summarize_frame_exports(exports: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize one animation's effective backend and fallback evidence."""
    backends = sorted({entry["backend"] for entry in exports})
    reasons = sorted(
        {
            str(entry["fallback_reason"])
            for entry in exports
            if entry.get("fallback_reason")
        }
    )
    return {
        "backend": backends[0] if len(backends) == 1 else "+".join(backends),
        "fallback_reason": "; ".join(reasons) if reasons else None,
    }


def save_prepared_animation(
    prepared_frames: list[tuple[Any, dict[str, Any], dict[str, Any], Any]],
    args,
    output_path: Path,
    *,
    save_static_output: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Save an already-canonicalized animation with one effective backend."""
    import tempfile

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mattervis-frames-") as temp_dir:
        frame_paths: list[Path] = []
        frame_exports: list[dict[str, Any]] = []
        for order, (bundle, scene, style, topology_data) in enumerate(
            prepared_frames
        ):
            frame_path = Path(temp_dir) / f"frame-{order:06d}.png"
            frame_exports.append(
                save_static_output(
                    bundle,
                    scene,
                    style,
                    topology_data,
                    args,
                    frame_path,
                    allow_style_fallback=False,
                )
            )
            frame_paths.append(frame_path)
        encode_frame_paths(frame_paths, output_path, fps=args.fps)
    return summarize_frame_exports(frame_exports)


def save_streaming_animation(
    input_path: Path,
    indices: list[int],
    args,
    overrides: dict[str, Any],
    output_path: Path,
    *,
    prepare_frame: Callable[..., tuple[dict[str, Any], dict[str, Any], Any]],
    save_static_output: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Render two bounded-memory passes and encode one shared-viewport movie."""
    import tempfile

    from ..loader import canonicalise_atomistic_frame, iter_atomistic_frames
    from ..renderer import ViewportAccumulator

    source_indices = sorted(set(indices))
    accumulator = ViewportAccumulator()
    format_name = args.input_format
    for atomistic, detected_format in iter_atomistic_frames(
        input_path,
        input_format=args.input_format,
        type_map=args.type_map,
        frame_indices=source_indices,
    ):
        format_name = detected_format
        selected = canonicalise_atomistic_frame(
            atomistic,
            path=input_path,
            input_format=detected_format,
        )
        scene, style, _topology_data = prepare_frame(
            selected.bundle,
            args,
            dict(overrides),
        )
        accumulator.update(scene, style=style)
    shared_viewport = accumulator.viewport()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mattervis-frames-") as temp_dir:
        temp_root = Path(temp_dir)
        exports_by_index: dict[int, dict[str, Any]] = {}
        paths_by_index: dict[int, Path] = {}
        for atomistic, detected_format in iter_atomistic_frames(
            input_path,
            input_format=args.input_format,
            type_map=args.type_map,
            frame_indices=source_indices,
        ):
            selected = canonicalise_atomistic_frame(
                atomistic,
                path=input_path,
                input_format=detected_format,
            )
            scene, style, topology_data = prepare_frame(
                selected.bundle,
                args,
                dict(overrides),
            )
            scene["viewport"] = dict(shared_viewport)
            frame_path = temp_root / f"source-{atomistic.index:09d}.png"
            exports_by_index[atomistic.index] = save_static_output(
                selected.bundle,
                scene,
                style,
                topology_data,
                args,
                frame_path,
                allow_style_fallback=False,
            )
            paths_by_index[atomistic.index] = frame_path
        encode_frame_paths(
            [paths_by_index[index] for index in indices],
            output_path,
            fps=args.fps,
        )

    summary = summarize_frame_exports(list(exports_by_index.values()))
    return {
        **summary,
        "input_format": format_name,
        "shared_viewport": shared_viewport,
    }


def save_prepared_from_cli(
    prepared_frames: list[tuple[Any, dict[str, Any], dict[str, Any], Any]],
    args,
    output_path: Path,
) -> dict[str, Any]:
    """CLI adapter that avoids a circular import during module loading."""
    from .cli import _save_static_output

    return save_prepared_animation(
        prepared_frames,
        args,
        output_path,
        save_static_output=_save_static_output,
    )


def save_streaming_from_cli(
    input_path: Path,
    indices: list[int],
    args,
    overrides: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    """CLI adapter for two-pass, bounded-memory animation rendering."""
    from .cli import _prepare_frame, _save_static_output

    return save_streaming_animation(
        input_path,
        indices,
        args,
        overrides,
        output_path,
        prepare_frame=_prepare_frame,
        save_static_output=_save_static_output,
    )