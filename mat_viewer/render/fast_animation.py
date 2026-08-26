"""Streaming CPU animation path for large LAMMPS trajectories."""

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import multiprocessing
import os
from pathlib import Path
import resource
import time
from typing import Iterator

import numpy as np

from ..loader.lammps_batch import (
    LammpsDumpIndex,
    LammpsFrameRecord,
    index_lammps_dump,
    read_lammps_frame,
    repeat_frame,
)
from .contracts import CameraSpec
from .cpu.batch import (
    NUMBA_AVAILABLE,
    project_frame,
    quantize_global_palette,
    render_projected_frame,
    warm_batch_renderer,
)
from .frame_selection import parse_frame_indices


@dataclass(frozen=True, slots=True)
class FastAnimationConfig:
    """Worker-safe render settings for one fixed animation viewport."""

    type_map: tuple[str, ...] | None
    repeat: tuple[int, int, int]
    camera: CameraSpec
    width: int
    height: int
    scale: int
    atom_scale: float
    background: tuple[int, int, int, int]
    show_hydrogen: bool
    show_cell: bool
    cell_color: tuple[int, int, int]
    cell_width_px: float
    bonded: bool
    bond_radius: float
    bond_skin: float
    indexed_gif: bool


@dataclass(frozen=True, slots=True)
class FastFrameResult:
    """Rendered RGB bytes plus stage timings for one source frame."""

    source_index: int
    timestep: int
    rgb: bytes
    parse_s: float
    expand_s: float
    project_s: float
    raster_s: float
    resize_s: float
    maxrss_kib: int
    bond_s: float
    bond_count: int
    candidate_rebuilds: int
    quantize_s: float


@dataclass(frozen=True, slots=True)
class FastAnimationResult:
    """Public result from the streaming LAMMPS animation path."""

    output: Path
    output_sha256: str
    camera: CameraSpec
    selected_frames: tuple[int, ...]
    profile: dict


_WORKER_INDEX: LammpsDumpIndex | None = None
_WORKER_CONFIG: FastAnimationConfig | None = None
_THREADPOOL_LIMITER = None
_WORKER_BOND_TRACKER = None


def _worker_init(
    dump_index: LammpsDumpIndex,
    config: FastAnimationConfig,
) -> None:
    global _THREADPOOL_LIMITER, _WORKER_BOND_TRACKER, _WORKER_CONFIG, _WORKER_INDEX
    _WORKER_INDEX = dump_index
    _WORKER_CONFIG = config
    if config.bonded:
        from molcrys_kit.structures import VerletBondTracker

        _WORKER_BOND_TRACKER = VerletBondTracker(skin=config.bond_skin)
    else:
        _WORKER_BOND_TRACKER = None
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        _THREADPOOL_LIMITER = None
    else:
        _THREADPOOL_LIMITER = threadpool_limits(limits=1)


def _render_worker(frame_index: int) -> FastFrameResult:
    if _WORKER_INDEX is None or _WORKER_CONFIG is None:
        raise RuntimeError("large-frame worker was not initialized")
    config = _WORKER_CONFIG
    start = time.perf_counter()
    frame = read_lammps_frame(
        _WORKER_INDEX,
        frame_index,
        type_map=config.type_map,
        sort_by_id=config.bonded,
    )
    parsed = time.perf_counter()
    frame = repeat_frame(frame, config.repeat)
    expanded = time.perf_counter()
    bonds = None
    rebuilds = 0
    bond_start = expanded
    if config.bonded:
        if _WORKER_BOND_TRACKER is None:
            raise RuntimeError("bonded worker has no MCK Verlet tracker")
        previous_rebuilds = _WORKER_BOND_TRACKER.rebuild_count
        bonds = _WORKER_BOND_TRACKER.update(
            frame.positions,
            frame.atomic_numbers,
            cell=frame.cell,
            pbc=frame.pbc,
        )
        rebuilds = _WORKER_BOND_TRACKER.rebuild_count - previous_rebuilds
    bonded = time.perf_counter()
    camera_positions = project_frame(frame, config.camera)
    projected = time.perf_counter()
    rendered = render_projected_frame(
        frame,
        camera_positions,
        config.camera,
        width=config.width * config.scale,
        height=config.height * config.scale,
        atom_scale=config.atom_scale,
        background=config.background,
        show_hydrogen=config.show_hydrogen,
        show_cell=config.show_cell,
        cell_color=config.cell_color,
        cell_width_px=config.cell_width_px * config.scale,
        bonds=bonds,
        bond_radius=config.bond_radius,
    )
    rasterized = time.perf_counter()
    rgb = rendered.rgba[:, :, :3]
    if config.scale != 1:
        from PIL import Image

        image = Image.fromarray(rgb)
        image = image.resize(
            (config.width, config.height),
            resample=Image.Resampling.LANCZOS,
        )
        rgb = np.asarray(image)
    resized = time.perf_counter()
    pixels = quantize_global_palette(rgb) if config.indexed_gif else rgb
    finished = time.perf_counter()
    return FastFrameResult(
        source_index=frame_index,
        timestep=frame.timestep,
        rgb=np.ascontiguousarray(pixels).tobytes(),
        parse_s=parsed - start,
        expand_s=expanded - parsed,
        project_s=projected - bonded,
        raster_s=rasterized - projected,
        resize_s=resized - rasterized,
        maxrss_kib=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        bond_s=bonded - bond_start,
        bond_count=0 if bonds is None else len(bonds.pairs),
        candidate_rebuilds=rebuilds,
        quantize_s=finished - resized,
    )


def _frame_box_points(
    records: tuple[LammpsFrameRecord, ...],
    repeat: tuple[int, int, int],
) -> np.ndarray:
    factors = np.asarray(repeat, dtype=np.float64)
    fractions = np.asarray(
        [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
            [1, 1, 0],
            [1, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
        ],
        dtype=np.float64,
    )
    points = [
        record.origin + fractions @ (record.cell * factors[:, None])
        for record in records
    ]
    return np.concatenate(points, axis=0)


def fit_shared_camera(
    records: tuple[LammpsFrameRecord, ...],
    *,
    repeat: tuple[int, int, int],
    width: int,
    height: int,
    projection: str,
    camera_axis: str | None = None,
    view_direction: tuple[float, float, float] | None = None,
    camera_position: tuple[float, float, float] | None = None,
    camera_up: tuple[float, float, float] | None = None,
    fit_multiplier: float = 1.8,
    zoom: float = 1.0,
    framing_margin: float = 1.12,
) -> CameraSpec:
    """Fit one camera to the union of all frame boxes."""
    if not records:
        raise ValueError("cannot fit a camera without frames")
    for name, value in (
        ("fit_multiplier", fit_multiplier),
        ("zoom", zoom),
        ("framing_margin", framing_margin),
    ):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and positive")
    points = _frame_box_points(records, repeat)
    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    target = (mins + maxs) * 0.5
    radius = max(float(np.linalg.norm(points - target, axis=1).max()), 1.0)
    matrix = records[0].cell * np.asarray(repeat, dtype=float)[:, None]

    if view_direction is not None:
        direction = np.asarray(view_direction, dtype=float)
    elif camera_position is not None:
        direction = np.asarray(camera_position, dtype=float) - target
    else:
        axis = camera_axis or "c"
        if axis.endswith("*"):
            reciprocal = np.linalg.inv(matrix).T
            direction = reciprocal[{"a*": 0, "b*": 1, "c*": 2}[axis]]
        else:
            direction = matrix[{"a": 0, "b": 1, "c": 2}[axis]]
    norm = float(np.linalg.norm(direction))
    if norm <= 1.0e-12:
        raise ValueError("camera direction must be non-zero")
    direction /= norm

    up = np.asarray(camera_up if camera_up is not None else matrix[1], dtype=float)
    if np.linalg.norm(up) <= 1.0e-12 or np.linalg.norm(np.cross(direction, up)) < 1e-10:
        up = np.asarray([0.0, 1.0, 0.0])
        if np.linalg.norm(np.cross(direction, up)) < 1e-10:
            up = np.asarray([1.0, 0.0, 0.0])
    up /= np.linalg.norm(up)
    forward = -direction
    right = np.cross(forward, up)
    right /= np.linalg.norm(right)
    screen_up = np.cross(right, forward)
    screen_up /= np.linalg.norm(screen_up)
    relative = points - target
    aspect = width / height
    half_width = float(np.max(np.abs(relative @ right)))
    half_height = float(np.max(np.abs(relative @ screen_up)))
    ortho_scale = max(half_height, half_width / aspect, 0.5) * framing_margin / zoom

    if camera_position is not None:
        position = np.asarray(camera_position, dtype=float)
        distance = float(np.linalg.norm(position - target))
    elif projection == "perspective":
        half_fov = np.radians(45.0 * 0.5)
        fit_radius = max(half_height, half_width / aspect) * framing_margin
        distance = max(
            fit_multiplier * fit_radius / (np.tan(half_fov) * zoom),
            1.5 * radius,
        )
        position = target + direction * distance
    else:
        distance = max(fit_multiplier * radius, 1.5 * radius)
        position = target + direction * distance
    near = max(radius * 1.0e-4, distance - 1.5 * radius)
    far = max(near * 2.0, distance + 1.5 * radius)
    return CameraSpec(
        position=tuple(position),
        target=tuple(target),
        up=tuple(up),
        projection=projection,
        near=near,
        far=far,
        ortho_scale=ortho_scale,
    )


def _default_workers(requested: int | None) -> int:
    cpus = max(1, os.cpu_count() or 1)
    try:
        available_bytes = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        available_bytes = 4 << 30
    memory_bound = max(1, int(available_bytes // (512 << 20)))
    automatic = min(cpus, memory_bound, 32)
    if requested is None:
        return automatic
    if requested <= 0:
        raise ValueError("workers must be positive")
    return int(requested)


def _render_worker_chunk(
    frame_indices: tuple[int, ...],
) -> tuple[FastFrameResult, ...]:
    return tuple(_render_worker(frame_index) for frame_index in frame_indices)


def _ordered_frames(
    pool: ProcessPoolExecutor,
    frame_indices: list[int],
    *,
    window: int,
    chunk_size: int = 2,
) -> Iterator[FastFrameResult]:
    chunks = [
        tuple(frame_indices[start : start + chunk_size])
        for start in range(0, len(frame_indices), chunk_size)
    ]
    futures: dict[int, Future[tuple[FastFrameResult, ...]]] = {}
    next_submit = 0
    for expected in range(len(chunks)):
        while next_submit < len(chunks) and len(futures) < window:
            futures[next_submit] = pool.submit(
                _render_worker_chunk,
                chunks[next_submit],
            )
            next_submit += 1
        future = futures.pop(expected)
        yield from future.result()


def _mp4_writer(path: Path, *, width: int, height: int, fps: float):
    import imageio_ffmpeg

    writer = imageio_ffmpeg.write_frames(
        str(path),
        (width, height),
        fps=fps,
        codec="libx264",
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        output_params=["-preset", "ultrafast", "-crf", "18"],
        macro_block_size=1,
    )
    writer.send(None)
    return writer


class _GlobalPaletteGifWriter:
    """Stream P-mode frames with one deterministic global 6x7x6 palette."""

    def __init__(self, path: Path, *, fps: float):
        self._handle = path.open("wb")
        self._duration_ms = max(1, int(round(1000.0 / fps)))
        self._started = False
        palette: list[int] = []
        for red in range(6):
            for green in range(7):
                for blue in range(6):
                    palette.extend(
                        (
                            round(red * 255 / 5),
                            round(green * 255 / 6),
                            round(blue * 255 / 5),
                        )
                    )
        palette.extend([0] * (768 - len(palette)))
        self._palette = palette

    def append_data(self, rgb: np.ndarray) -> None:
        self.append_indices(quantize_global_palette(rgb))

    def append_indices(self, indices: np.ndarray) -> None:
        from PIL import GifImagePlugin, Image

        values = np.ascontiguousarray(indices, dtype=np.uint8)
        if values.ndim != 2:
            raise ValueError("GIF palette indices must have shape (height, width)")
        frame = Image.fromarray(indices, mode="P")
        frame.putpalette(self._palette)
        if not self._started:
            for block in GifImagePlugin._get_global_header(
                frame,
                {"loop": 0, "duration": self._duration_ms},
            ):
                self._handle.write(block)
            self._started = True
        GifImagePlugin._write_frame_data(
            self._handle,
            frame,
            (0, 0),
            {"duration": self._duration_ms, "disposal": 1},
        )

    def close(self) -> None:
        if self._handle.closed:
            return
        try:
            if not self._started:
                raise ValueError("cannot close an empty GIF animation")
            self._handle.write(b";")
        finally:
            self._handle.close()


def _gif_writer(path: Path, *, fps: float) -> _GlobalPaletteGifWriter:
    return _GlobalPaletteGifWriter(path, fps=fps)


def render_lammps_animation(
    input_path: str | Path,
    output_path: str | Path,
    *,
    input_format: str | None = None,
    type_map: tuple[str, ...] | None = None,
    frame_range: str | None = None,
    stride: int = 1,
    repeat: tuple[int, int, int] = (1, 1, 1),
    width: int = 1200,
    height: int = 900,
    scale: int = 1,
    fps: float = 10.0,
    projection: str = "orthographic",
    camera_axis: str | None = None,
    view_direction: tuple[float, float, float] | None = None,
    camera_position: tuple[float, float, float] | None = None,
    camera_up: tuple[float, float, float] | None = None,
    fit_multiplier: float = 1.8,
    zoom: float = 1.0,
    framing_margin: float = 1.12,
    atom_scale: float = 1.0,
    background: tuple[int, int, int, int] = (255, 255, 255, 255),
    show_hydrogen: bool = True,
    show_cell: bool = True,
    cell_color: tuple[int, int, int] = (51, 51, 51),
    cell_width_px: float = 2.0,
    bonded: bool = False,
    bond_radius: float = 0.15,
    bond_skin: float = 0.5,
    workers: int | None = None,
    profile_path: str | Path | None = None,
) -> FastAnimationResult:
    """Render a large LAMMPS dump with bounded frame workers and ordered encoding."""
    del input_format
    if not NUMBA_AVAILABLE:
        raise RuntimeError(
            "large LAMMPS animation requires numba; the standard CPU renderer remains available"
        )
    output = Path(output_path).expanduser().resolve()
    if output.suffix.lower() not in {".gif", ".mp4"}:
        raise ValueError("large LAMMPS animation output must be GIF or MP4")
    if width <= 0 or height <= 0 or scale <= 0:
        raise ValueError("width, height, and scale must be positive")
    if fps <= 0.0 or not math.isfinite(fps):
        raise ValueError("fps must be finite and positive")
    if stride <= 0:
        raise ValueError("stride must be positive")
    if any(value <= 0 for value in repeat):
        raise ValueError("repeat values must be positive")
    if bonded and (not math.isfinite(bond_radius) or bond_radius <= 0.0):
        raise ValueError("bond_radius must be finite and positive")
    if bonded and (not math.isfinite(bond_skin) or bond_skin <= 0.0):
        raise ValueError("bond_skin must be finite and positive")

    total_start = time.perf_counter()
    dump_index = index_lammps_dump(input_path)
    indexed = time.perf_counter()
    frame_indices = parse_frame_indices(len(dump_index), frame_range, stride)
    if len(frame_indices) < 2:
        raise ValueError("animation requires at least two selected frames")
    selected_records = tuple(dump_index.records[index] for index in frame_indices)
    camera = fit_shared_camera(
        selected_records,
        repeat=repeat,
        width=width,
        height=height,
        projection=projection,
        camera_axis=camera_axis,
        view_direction=view_direction,
        camera_position=camera_position,
        camera_up=camera_up,
        fit_multiplier=fit_multiplier,
        zoom=zoom,
        framing_margin=framing_margin,
    )
    camera_ready = time.perf_counter()
    warm_batch_renderer()
    warmed = time.perf_counter()

    worker_count = _default_workers(workers)
    config = FastAnimationConfig(
        type_map=type_map,
        repeat=tuple(int(value) for value in repeat),
        camera=camera,
        width=int(width),
        height=int(height),
        scale=int(scale),
        atom_scale=float(atom_scale),
        background=background,
        show_hydrogen=bool(show_hydrogen),
        show_cell=bool(show_cell),
        cell_color=cell_color,
        cell_width_px=float(cell_width_px),
        bonded=bool(bonded),
        bond_radius=float(bond_radius),
        bond_skin=float(bond_skin),
        indexed_gif=output.suffix.lower() == ".gif",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded_start = time.perf_counter()
    suffix = output.suffix.lower()
    writer = (
        _mp4_writer(output, width=width, height=height, fps=fps)
        if suffix == ".mp4"
        else _gif_writer(output, fps=fps)
    )
    metrics: list[FastFrameResult] = []
    encode_s = 0.0
    context = multiprocessing.get_context("fork")
    try:
        with ProcessPoolExecutor(
            max_workers=worker_count,
            mp_context=context,
            initializer=_worker_init,
            initargs=(dump_index, config),
        ) as pool:
            for result in _ordered_frames(
                pool,
                frame_indices,
                window=max(2, worker_count),
                chunk_size=2,
            ):
                encode_start = time.perf_counter()
                if suffix == ".mp4":
                    writer.send(result.rgb)
                else:
                    indices = np.frombuffer(result.rgb, dtype=np.uint8).reshape(
                        height, width
                    )
                    writer.append_indices(indices)
                encode_s += time.perf_counter() - encode_start
                metrics.append(result)
    finally:
        writer.close()
    completed = time.perf_counter()

    timing_s = {
        "index": indexed - total_start,
        "camera": camera_ready - indexed,
        "jit_warmup": warmed - camera_ready,
        "pool_and_encode": completed - encoded_start,
        "encode": encode_s,
        "pool_wait_and_transfer": completed - encoded_start - encode_s,
        "parse_cpu_sum": sum(item.parse_s for item in metrics),
        "expand_cpu_sum": sum(item.expand_s for item in metrics),
        "project_cpu_sum": sum(item.project_s for item in metrics),
        "raster_cpu_sum": sum(item.raster_s for item in metrics),
        "quantize_cpu_sum": sum(item.quantize_s for item in metrics),
        "resize_cpu_sum": sum(item.resize_s for item in metrics),
        "bond_cpu_sum": sum(item.bond_s for item in metrics),
        "total": completed - total_start,
    }
    profile = {
        "schema": "mattervis.large-animation-profile/v1",
        "source": {
            "path": str(dump_index.path),
            "bytes": dump_index.path.stat().st_size,
            "frames_total": len(dump_index),
            "frames_selected": len(frame_indices),
            "atoms_per_source_frame": selected_records[0].natoms,
            "atoms_per_rendered_frame": selected_records[0].natoms
            * int(np.prod(repeat)),
        },
        "output": {
            "path": str(output),
            "bytes": output.stat().st_size,
            "format": suffix.lstrip("."),
        },
        "settings": {
            "width": width,
            "height": height,
            "scale": scale,
            "fps": fps,
            "projection": projection,
            "repeat": list(repeat),
            "workers": worker_count,
            "zoom": zoom,
            "framing_margin": framing_margin,
            "shared_viewport": True,
            "representation": (
                "analytic_spheres_and_bonds" if bonded else "analytic_spheres"
            ),
            "sampling": None,
            "bond_radius": bond_radius if bonded else None,
            "bond_skin": bond_skin if bonded else None,
        },
        "camera": asdict(camera),
        "timing_s": timing_s,
        "peak_worker_rss_mib": (
            max(item.maxrss_kib for item in metrics) / 1024.0 if metrics else 0.0
        ),
        "timesteps": [item.timestep for item in metrics],
        "bonds": {
            "enabled": bonded,
            "counts": [item.bond_count for item in metrics],
            "candidate_rebuilds": sum(item.candidate_rebuilds for item in metrics),
        },
    }
    if profile_path is not None:
        destination = Path(profile_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    output_hash = sha256(output.read_bytes()).hexdigest()
    return FastAnimationResult(
        output=output,
        output_sha256=output_hash,
        camera=camera,
        selected_frames=tuple(frame_indices),
        profile=profile,
    )


__all__ = [
    "FastAnimationResult",
    "fit_shared_camera",
    "render_lammps_animation",
]
