"""Workload-driven selection between general and array batch renderers."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_BATCH_ATOM_FRAMES = 100_000


@dataclass(frozen=True, slots=True)
class RendererDecision:
    requested: str
    selected: str
    atom_frames: int
    threshold: int
    reason: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "requested": self.requested,
            "selected": self.selected,
            "atom_frames": self.atom_frames,
            "threshold": self.threshold,
            "reason": self.reason,
        }


def select_renderer(
    requested: str,
    *,
    atom_frames: int,
    backend: str,
    representation: str,
    output_format: str,
    threshold: int = DEFAULT_BATCH_ATOM_FRAMES,
) -> RendererDecision:
    """Select by workload and numeric-kernel capability, never input suffix.

    Labels, axes, vectors, and polyhedra are independent overlay layers and
    deliberately do not participate in this decision.
    """

    requested = str(requested).lower()
    if requested not in {"auto", "batch", "general"}:
        raise ValueError("renderer must be auto, batch, or general")
    if atom_frames < 0:
        raise ValueError("atom_frames cannot be negative")
    batch_capable = (
        backend == "cpu"
        and representation in {"ball", "ball_stick"}
        and output_format in {"png", "gif", "mp4"}
    )
    if requested == "general":
        selected, reason = "general", "explicitly requested"
    elif requested == "batch":
        if not batch_capable:
            raise ValueError(
                "--renderer batch requires CPU PNG/GIF/MP4 with ball or ball_stick"
            )
        selected, reason = "batch", "explicitly requested"
    elif batch_capable and atom_frames >= threshold:
        selected, reason = "batch", f"atom_frames >= {threshold}"
    else:
        selected = "general"
        reason = (
            f"atom_frames < {threshold}"
            if batch_capable
            else "numeric batch kernel does not implement this representation/output"
        )
    return RendererDecision(
        requested=requested,
        selected=selected,
        atom_frames=int(atom_frames),
        threshold=int(threshold),
        reason=reason,
    )


__all__ = ["DEFAULT_BATCH_ATOM_FRAMES", "RendererDecision", "select_renderer"]
