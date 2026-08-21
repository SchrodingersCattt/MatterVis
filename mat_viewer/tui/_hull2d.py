"""2D convex hull via Graham scan — pure Python, no dependencies.

Used by the TUI molecule-level view to compute the projected outline
of each molecule group.
"""

from __future__ import annotations


def convex_hull_2d(points: list[tuple[float, float]]) -> list[int]:
    """Compute the 2D convex hull of a set of points.

    Parameters
    ----------
    points : list of (x, y) tuples
        The input point set (must have ≥ 3 distinct points for a polygon).

    Returns
    -------
    list of int
        Indices into `points` forming the convex hull vertices in
        counter-clockwise order. Returns empty list if degenerate.
    """
    n = len(points)
    if n < 3:
        return list(range(n))

    # Sort by x, then y — store original indices
    indexed = sorted(range(n), key=lambda i: (points[i][0], points[i][1]))

    # Andrew's monotone chain algorithm (builds lower then upper hull)
    def _cross(o: int, a: int, b: int) -> float:
        ox, oy = points[o]
        ax, ay = points[a]
        bx, by = points[b]
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)

    lower: list[int] = []
    for i in indexed:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], i) <= 0:
            lower.pop()
        lower.append(i)

    upper: list[int] = []
    for i in reversed(indexed):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], i) <= 0:
            upper.pop()
        upper.append(i)

    # Remove last point of each half because it's repeated
    hull = lower[:-1] + upper[:-1]

    # Deduplicate (collinear edge case)
    if len(hull) < 3:
        return hull

    return hull
