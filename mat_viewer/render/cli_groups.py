"""Compact CLI grammar for selector-based atom and bond render rules."""

from __future__ import annotations

import re
from typing import Any, Iterable, Literal, Sequence

GroupKind = Literal["atom", "bond"]

_ATOM_STYLES = {
    "ball",
    "ball_stick",
    "ortep",
    "space_filling",
    "stick",
    "wireframe",
}
_BOND_STYLES = {"ball_stick", "stick", "wireframe"}
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")


def _csv(value: str, *, name: str) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise ValueError(f"{name} selector requires at least one value")
    return values


def _integers(value: str, *, name: str) -> list[int]:
    try:
        return [int(item) for item in _csv(value, name=name)]
    except ValueError as exc:
        raise ValueError(f"{name} selector values must be integers") from exc


def _selector(expression: str, *, kind: GroupKind) -> dict[str, Any]:
    selector: dict[str, Any] = {}
    clauses = expression.split("+")
    for raw_clause in clauses:
        clause = raw_clause.strip()
        if not clause:
            raise ValueError(f"empty clause in {kind} selector {expression!r}")
        if clause == "all":
            if len(clauses) != 1:
                raise ValueError("'all' cannot be combined with another selector")
            return {"all": True}
        if clause in {"minor", "major"}:
            selector["is_minor"] = clause == "minor"
            continue
        if ":" not in clause:
            raise ValueError(
                f"invalid {kind} selector clause {clause!r}; expected KIND:VALUES"
            )
        key, raw_values = clause.split(":", 1)
        if kind == "atom":
            if key in {"element", "elements"}:
                selector["elements"] = _csv(raw_values, name="element")
            elif key in {"label", "labels"}:
                selector["labels"] = _csv(raw_values, name="label")
            elif key in {"index", "indices"}:
                selector["atom_indices"] = _integers(raw_values, name="index")
            elif key in {"fragment", "fragments"}:
                selector["fragment_labels"] = _csv(raw_values, name="fragment")
            elif key in {
                "fragment-index",
                "fragment-indices",
                "fragment_index",
                "fragment_indices",
            }:
                selector["fragment_indices"] = _integers(
                    raw_values,
                    name="fragment-index",
                )
            elif key in {"molecule", "molecules"}:
                selector["molecule_indices"] = _integers(raw_values, name="molecule")
            else:
                raise ValueError(f"unsupported atom selector kind {key!r}")
        else:
            if key in {"between", "elements"}:
                selector["between_elements"] = _csv(raw_values, name="between")
            elif key in {"label", "labels"}:
                selector["labels"] = _csv(raw_values, name="label")
            else:
                raise ValueError(f"unsupported bond selector kind {key!r}")
    return selector


def _boolean(value: str, *, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "1", "on"}:
        return True
    if lowered in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _unit_interval(value: str, *, name: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must lie in [0, 1]")
    return number


def _positive(value: str, *, name: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if number <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return number


def _color(value: str, *, name: str) -> str:
    if not _HEX_COLOR.fullmatch(value):
        raise ValueError(f"{name} must be #RRGGBB or #RRGGBBAA")
    return value


def _overrides(tokens: Iterable[str], *, kind: GroupKind) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError(
                f"{kind} group override {token!r} must use KEY=VALUE syntax"
            )
        key, value = token.split("=", 1)
        key = key.strip().replace("-", "_")
        value = value.strip()
        if key in {"color", "color_light"}:
            if kind == "bond" and key == "color_light":
                raise ValueError("color_light is only valid for atom groups")
            overrides[key] = _color(value, name=key)
        elif key == "visible":
            overrides[key] = _boolean(value, name=key)
        elif key == "opacity":
            overrides[key] = _unit_interval(value, name=key)
        elif key == "style":
            choices = _ATOM_STYLES if kind == "atom" else _BOND_STYLES
            normalized = value.replace("-", "_")
            if normalized not in choices:
                raise ValueError(
                    f"unsupported {kind} style {value!r}; choose from "
                    + ", ".join(sorted(choices))
                )
            overrides[key] = normalized
        elif key == "material" and kind == "atom":
            normalized = value.lower()
            if normalized not in {"mesh", "flat"}:
                raise ValueError("atom material must be mesh or flat")
            overrides[key] = normalized
        elif key == "radius_scale" and kind == "bond":
            overrides[key] = _positive(value, name=key)
        else:
            raise ValueError(f"unsupported {kind} group override {key!r}")
    if not overrides:
        raise ValueError(f"{kind} group requires at least one KEY=VALUE override")
    return overrides


def parse_group_arguments(
    rows: Sequence[Sequence[str]] | None,
    *,
    kind: GroupKind,
) -> list[dict[str, Any]]:
    """Parse repeatable SELECTOR KEY=VALUE rows into canonical group rules."""

    groups = []
    for index, row in enumerate(rows or ()):
        if len(row) < 2:
            raise ValueError(
                f"--{kind}-group requires SELECTOR and at least one KEY=VALUE"
            )
        groups.append(
            {
                "id": f"cli-{kind}-{index + 1}",
                "name": f"CLI {kind} rule {index + 1}",
                "selector": _selector(row[0], kind=kind),
                "enabled": True,
                **_overrides(row[1:], kind=kind),
            }
        )
    return groups


__all__ = ["parse_group_arguments"]
