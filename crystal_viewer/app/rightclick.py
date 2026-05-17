from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from .normalizers import *
from .style_helpers import _POLYHEDRON_AUTO_COLORS

def _dispatch_rightclick_action(
    backend: Any,
    scene_id: Optional[str],
    action: str,
    kind: Optional[str],
    payload: dict[str, Any],
    target: dict[str, Any],
    *,
    color: Optional[str] = None,
    radius: Optional[float] = None,
    hops: Optional[int] = None,
) -> None:
    if action in ("supercell_2x", "supercell_clear"):
        n = 2 if action == "supercell_2x" else 1
        backend.patch_state(
            {"supercell": {"a": n, "b": n, "c": n}},
            scene_id=scene_id,
        )
        return

    if not kind or kind == "_global":
        # Bare keyboard shortcut without a hovered target. ``r`` /
        # ``R`` were handled above; everything else needs a target.
        return

    if action == "hide":
        if kind == "atom":
            label = payload.get("label")
            if not label:
                return
            backend.add_atom_group(
                selector={"labels": [str(label)]},
                color="#888888",
                visible=False,
                name=f"hide {label}",
                scene_id=scene_id,
            )
        elif kind == "polyhedron":
            spec_id = payload.get("spec_id")
            frag = payload.get("fragment_label")
            if not spec_id or not frag:
                return
            existing = backend.list_polyhedron_specs(scene_id=scene_id)
            base = next((s for s in existing if s["id"] == spec_id), None)
            if base is None:
                return
            overrides = dict(base.get("instance_overrides") or {})
            overrides[str(frag)] = dict(overrides.get(str(frag), {}), visible=False)
            backend.update_polyhedron_spec(
                spec_id, {"instance_overrides": overrides}, scene_id=scene_id
            )
        elif kind == "bond":
            elements = payload.get("element_pair") or ""
            parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
                p.strip() for p in str(elements).split("-") if p.strip()
            ]
            selector = (
                {"between_elements": parts}
                if len(parts) == 2
                else {"labels": [str(payload.get("label_pair") or "")]}
            )
            backend.add_bond_group(
                selector=selector,
                color="#888888",
                visible=False,
                name=f"hide {elements or 'bond'}",
                scene_id=scene_id,
            )
        return

    if action == "set_color":
        if not color:
            return
        if kind == "atom":
            label = payload.get("label")
            if not label:
                return
            backend.add_atom_group(
                selector={"labels": [str(label)]},
                color=color,
                visible=True,
                name=f"colour {label}",
                scene_id=scene_id,
            )
        elif kind == "polyhedron":
            spec_id = payload.get("spec_id")
            frag = payload.get("fragment_label")
            if not spec_id or not frag:
                return
            existing = backend.list_polyhedron_specs(scene_id=scene_id)
            base = next((s for s in existing if s["id"] == spec_id), None)
            if base is None:
                return
            overrides = dict(base.get("instance_overrides") or {})
            overrides[str(frag)] = dict(overrides.get(str(frag), {}), color=color)
            backend.update_polyhedron_spec(
                spec_id, {"instance_overrides": overrides}, scene_id=scene_id
            )
        elif kind == "bond":
            elements = payload.get("element_pair") or ""
            parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
                p.strip() for p in str(elements).split("-") if p.strip()
            ]
            selector = (
                {"between_elements": parts}
                if len(parts) == 2
                else {"labels": [str(payload.get("label_pair") or "")]}
            )
            backend.add_bond_group(
                selector=selector,
                color=color,
                visible=True,
                name=f"colour {elements or 'bond'}",
                scene_id=scene_id,
            )
        return

    if action == "grow_bonds":
        seeds = _seeds_from_payload(kind, payload)
        if seeds is None:
            return
        n_hops = int(target.get("hops") or hops or 1)
        backend.add_transform(
            kind="grow_bonds",
            params={"seeds": seeds, "hops": max(1, n_hops)},
            scene_id=scene_id,
        )
        return

    if action == "grow_radius":
        seeds = _seeds_from_payload(kind, payload)
        if seeds is None:
            return
        r = float(target.get("radius") or radius or 4.0)
        backend.add_transform(
            kind="grow_radius",
            params={"seeds": seeds, "radius": max(0.0, r)},
            scene_id=scene_id,
        )
        return

    if action == "complete_fragment":
        seeds = _seeds_from_payload(kind, payload)
        if seeds is None:
            return
        backend.add_transform(
            kind="complete_fragment",
            params={"seeds": seeds, "max_hops": 32},
            scene_id=scene_id,
        )
        return

    if action == "promote_to_group":
        if kind == "atom":
            elem = payload.get("element")
            if not elem:
                return
            backend.add_atom_group(
                selector={"elements": [str(elem)]},
                color="#888888",
                visible=True,
                name=f"all {elem}",
                scene_id=scene_id,
            )
        elif kind == "bond":
            elements = payload.get("element_pair") or ""
            parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
                p.strip() for p in str(elements).split("-") if p.strip()
            ]
            if len(parts) != 2:
                return
            backend.add_bond_group(
                selector={"between_elements": parts},
                color="#7C5CBF",
                visible=True,
                name=f"all {parts[0]}\u2013{parts[1]}",
                scene_id=scene_id,
            )
        elif kind == "polyhedron":
            # Already a polyhedron spec -- promotion would be a no-op.
            return
        return

    if action == "colour_picker":
        # Keyboard shortcut: just leave the popover open; the JS will
        # not have created the popover yet, so we open one centred on
        # the screen by re-pushing target with no action.
        # The render callback ignores the absence of x/y and uses
        # 0/0 -- good enough for this MVP.
        return

    if action == "analyze":
        # Sets the topology focus to this fragment so the right-side
        # analysis panel updates. We piggyback on the existing
        # ``topology_site_index`` state field (per scene) -- the
        # update_view callback already re-renders when it changes.
        if kind == "atom":
            atom_index = payload.get("index")
            if atom_index is None:
                return
            try:
                state = backend.get_state(scene_id)
                site_index = backend.fragment_index_for_atom(
                    backend.scene_for_state(state), int(atom_index)
                )
                if site_index is not None:
                    backend.patch_state(
                        {"topology_site_index": int(site_index)}, scene_id=scene_id
                    )
            except Exception:
                pass
        elif kind == "polyhedron":
            # The picked-payload already came from the topology side;
            # promoting the spec to "topology focus" is a no-op for
            # now.
            pass
        return


def _seeds_from_payload(kind: Optional[str], payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    if kind == "atom":
        idx = payload.get("index")
        if idx is None:
            label = payload.get("label")
            if label:
                return {"labels": [str(label)]}
            return None
        return {"atom_indices": [int(idx)]}
    if kind == "polyhedron":
        frag = payload.get("fragment_label")
        if frag:
            return {"fragment_labels": [str(frag)]}
        return None
    if kind == "bond":
        # Bonds aren't atoms, but we can seed from the constituent
        # element pair as a coarse fallback.
        elements = payload.get("element_pair") or ""
        parts = [p.strip() for p in str(elements).split("\u2013") if p.strip()] or [
            p.strip() for p in str(elements).split("-") if p.strip()
        ]
        if parts:
            return {"elements": parts}
        return None
    return None


def _normalize_polyhedron_specs(
    raw_specs: Any,
    *,
    fallback_color: str = "#7C5CBF",
) -> list[dict[str, Any]]:
    """Validate a list of polyhedron-spec dicts coming from a state patch
    or REST payload. Drops malformed rows silently; callers that need to
    surface validation errors should use ``_normalize_polyhedron_spec``
    directly."""
    if raw_specs is None:
        return []
    if not isinstance(raw_specs, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    existing_ids: set[str] = set()
    for index, raw in enumerate(raw_specs):
        spec_fallback = _POLYHEDRON_AUTO_COLORS[index % len(_POLYHEDRON_AUTO_COLORS)]
        spec = _normalize_polyhedron_spec(
            raw,
            fallback_color=fallback_color if index == 0 else spec_fallback,
            existing_ids=existing_ids,
        )
        if spec is not None:
            out.append(spec)
    return out

__all__ = [name for name in globals() if not name.startswith("__")]
