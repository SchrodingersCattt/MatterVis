"""Resolve and draw physical simulation time for trajectory animations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Literal, Mapping, Sequence

TimeUnit = Literal["fs", "ps", "ns"]
TimePosition = Literal["top-left", "top-right", "bottom-left", "bottom-right"]

_FS_PER_UNIT: dict[str, float] = {"fs": 1.0, "ps": 1_000.0, "ns": 1_000_000.0}
_PHYSICAL_TIME_KEYS = ("time_fs", "time_ps", "time_ns")
_STEP_KEYS = ("timestep", "step", "nstep")


@dataclass(frozen=True, slots=True)
class AnimationTimeSpec:
    """Physical-time mapping independent of video playback speed.

    time_step is the MD integrator step in time_step_unit.
    dump_frequency is only used when source frames do not carry an
    integer simulation-step field.
    """

    display_unit: TimeUnit
    time_step: float | None = None
    time_step_unit: TimeUnit = "fs"
    dump_frequency: int | None = None
    first_frame_step: int = 0
    position: TimePosition = "top-left"

    def __post_init__(self) -> None:
        if self.display_unit not in _FS_PER_UNIT:
            raise ValueError("display_unit must be fs, ps, or ns")
        if self.time_step_unit not in _FS_PER_UNIT:
            raise ValueError("time_step_unit must be fs, ps, or ns")
        if self.time_step is not None and (
            not math.isfinite(self.time_step) or self.time_step <= 0.0
        ):
            raise ValueError("time_step must be finite and greater than zero")
        if self.dump_frequency is not None and self.dump_frequency <= 0:
            raise ValueError("dump_frequency must be greater than zero")
        if self.position not in {
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
        }:
            raise ValueError("unsupported time-label position")


@dataclass(frozen=True, slots=True)
class AnimationTimeSeries:
    """Resolved labels and provenance for selected source frames."""

    values: tuple[float, ...]
    labels: tuple[str, ...]
    source: str
    simulation_steps: tuple[float, ...] | None
    spec: AnimationTimeSpec

    def to_metadata(self) -> dict[str, Any]:
        payload = asdict(self.spec)
        payload.update(
            {
                "displayed": True,
                "source": self.source,
                "values": list(self.values),
                "labels": list(self.labels),
                "simulation_steps": (
                    list(self.simulation_steps)
                    if self.simulation_steps is not None
                    else None
                ),
            }
        )
        return payload


def coerce_animation_time_spec(
    value: AnimationTimeSpec | Mapping[str, Any] | None,
) -> AnimationTimeSpec | None:
    if value is None or isinstance(value, AnimationTimeSpec):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("animation_time must be AnimationTimeSpec or a mapping")
    return AnimationTimeSpec(**dict(value))


def _frame_info(frame: Any) -> Mapping[str, Any]:
    info = getattr(frame, "info", None)
    if isinstance(info, Mapping):
        return info
    if isinstance(frame, Mapping):
        nested = frame.get("frame_info")
        if isinstance(nested, Mapping):
            return nested
    return {}


def _common_numeric_metadata(
    frames: Sequence[Any],
    keys: Sequence[str],
) -> tuple[str, tuple[float, ...]] | None:
    infos = [_frame_info(frame) for frame in frames]
    for key in keys:
        if not all(key in info for info in infos):
            continue
        try:
            values = tuple(float(info[key]) for info in infos)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(value) for value in values):
            return key, values
    return None


def _source_frame_indices(frames: Sequence[Any]) -> tuple[int, ...]:
    indices = []
    for ordinal, frame in enumerate(frames):
        value = getattr(frame, "index", None)
        if value is None and isinstance(frame, Mapping):
            value = frame.get("frame_index")
        indices.append(ordinal if value is None else int(value))
    return tuple(indices)


def _format_time(value: float, unit: str) -> str:
    clean = 0.0 if abs(value) < 5.0e-13 else value
    return f"t = {clean:.6g} {unit}"


def resolve_animation_times(
    frames: Sequence[Any],
    spec: AnimationTimeSpec | Mapping[str, Any],
) -> AnimationTimeSeries:
    """Resolve selected frames to physical time without using playback FPS."""

    resolved_spec = coerce_animation_time_spec(spec)
    if resolved_spec is None:
        raise TypeError("animation time spec is required")
    selected = tuple(frames)
    if not selected:
        raise ValueError("cannot resolve animation time for an empty trajectory")

    physical = _common_numeric_metadata(selected, _PHYSICAL_TIME_KEYS)
    simulation_steps: tuple[float, ...] | None = None
    if physical is not None:
        key, source_values = physical
        source_unit = key.removeprefix("time_")
        values = tuple(
            value * _FS_PER_UNIT[source_unit] / _FS_PER_UNIT[resolved_spec.display_unit]
            for value in source_values
        )
        source = f"frame_info.{key}"
    else:
        if resolved_spec.time_step is None:
            raise ValueError(
                "--display-time requires --time-step unless every selected frame "
                "contains time_fs, time_ps, or time_ns metadata"
            )
        step_metadata = _common_numeric_metadata(selected, _STEP_KEYS)
        if step_metadata is not None:
            key, simulation_steps = step_metadata
            source = f"frame_info.{key}"
        else:
            if resolved_spec.dump_frequency is None:
                raise ValueError(
                    "selected frames contain no timestep/step metadata; pass "
                    "--dump-frequency with --time-step"
                )
            simulation_steps = tuple(
                float(
                    resolved_spec.first_frame_step
                    + index * resolved_spec.dump_frequency
                )
                for index in _source_frame_indices(selected)
            )
            source = "frame_index*dump_frequency"
        step_in_fs = (
            resolved_spec.time_step * _FS_PER_UNIT[resolved_spec.time_step_unit]
        )
        values = tuple(
            step * step_in_fs / _FS_PER_UNIT[resolved_spec.display_unit]
            for step in simulation_steps
        )

    labels = tuple(_format_time(value, resolved_spec.display_unit) for value in values)
    return AnimationTimeSeries(
        values=values,
        labels=labels,
        source=source,
        simulation_steps=simulation_steps,
        spec=resolved_spec,
    )


def draw_time_label(image: Any, label: str, position: TimePosition = "top-left") -> Any:
    """Return an RGBA copy with a deterministic paper-space time label."""

    from PIL import Image, ImageDraw, ImageFont

    canvas = image.convert("RGBA")
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font_size = max(14, int(round(min(canvas.size) * 0.045)))
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), label, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    padding = max(5, int(round(font_size * 0.38)))
    margin = max(8, int(round(font_size * 0.55)))
    width, height = canvas.size
    left = (
        margin
        if position.endswith("left")
        else width - margin - text_width - 2 * padding
    )
    top = (
        margin
        if position.startswith("top")
        else height - margin - text_height - 2 * padding
    )
    rectangle = (
        left,
        top,
        left + text_width + 2 * padding,
        top + text_height + 2 * padding,
    )
    draw.rounded_rectangle(
        rectangle,
        radius=max(4, padding),
        fill=(255, 255, 255, 220),
        outline=(35, 35, 35, 210),
        width=max(1, font_size // 14),
    )
    draw.text(
        (left + padding - box[0], top + padding - box[1]),
        label,
        fill=(20, 20, 20, 255),
        font=font,
    )
    return Image.alpha_composite(canvas, layer)


__all__ = [
    "AnimationTimeSeries",
    "AnimationTimeSpec",
    "coerce_animation_time_spec",
    "draw_time_label",
    "resolve_animation_times",
]
