from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from ..operations.disorder import resolve_disorder as _resolve_disorder_replicas


class _OperationsBackendMixin:
    def resolve_disorder(
        self,
        scene_id: Optional[str] = None,
        *,
        method: str = "enumerate",
        count: int = 5,
        seed: int | None = None,
    ) -> list[dict[str, Any]]:
        state = self.get_state(scene_id)
        bundle = self.get_bundle(str(state.get("structure") or ""))
        return _resolve_disorder_replicas(bundle, method=method, count=count, seed=seed)


__all__ = ["_OperationsBackendMixin"]
