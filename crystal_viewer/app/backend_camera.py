from __future__ import annotations
# ruff: noqa: F401,F403,F405

import plotly.graph_objects as go

from .shared import *
from .camera_helpers import *
from .style_helpers import *


def _figure_from_cached_dict(cached_dict: dict) -> go.Figure:
    """Reconstruct a ``go.Figure`` from a JSON-style dict snapshot.

    The figure cache stores ``fig.to_plotly_json()`` rather than a
    deep-copied ``go.Figure`` because Plotly's validator chain makes
    ``copy.deepcopy(go.Figure)`` ~10x slower than copying the
    equivalent plain dict and reconstructing with ``_validate=False``.
    See the cache hit branch of ``figure_for_state`` for the why.
    """
    snapshot = copy.deepcopy(cached_dict)
    return go.Figure(snapshot, _validate=False)


class _CameraBackendMixin:
    def figure_for_state(self, state: Optional[dict[str, Any]] = None, click_data: Optional[dict[str, Any]] = None):
        state = self.get_state() if state is None else state
        scene_id = state.get("scene_id")
        cache_key = None
        if click_data is None:
            try:
                # ``version`` and ``server_started_at`` change on every
                # state mutation / restart but do not affect the figure
                # body, so they would only cause spurious cache misses.
                # ``camera`` is applied post-cache via ``update_layout``
                # (the in-figure axis-key compass uses
                # ``_camera_axis_projections`` which reads ``style``
                # directly, so this means the compass arrow stays at
                # whatever projection was captured when the figure was
                # first built; it picks up the live camera on the next
                # genuine state change). Dropping these three fields
                # lets back-to-back tab switches and reverted slider
                # tweaks reuse the previously built figure instead of
                # paying the ~430 ms ``build_figure`` cost every time.
                key_state = {
                    k: v for k, v in state.items()
                    if k not in ("version", "server_started_at", "camera")
                }
                cache_key = json.dumps(_json_safe(key_state), sort_keys=True, separators=(",", ":"))
            except Exception:
                cache_key = None
        # Cache stores plain JSON-style dicts (``fig.to_plotly_json()``)
        # NOT ``go.Figure`` instances. Profiling: ``copy.deepcopy(fig)``
        # is ~225 ms for an HPEP scene with 25 traces because Plotly's
        # validator mixin walks every property on every nested object;
        # ``copy.deepcopy(fig.to_plotly_json())`` of the same payload is
        # ~25 ms (10x cheaper). The ``go.Figure`` is reconstructed from
        # the cached dict on every hit / new build via the
        # ``_validate=False`` constructor. This was the dominant left-
        # sidebar latency complaint ("右边非常迟钝"): each slider tweak
        # invalidated this cache and paid 2 deepcopies on the way out.
        if cache_key is not None and cache_key in self._figure_cache:
            cached_fig_dict, cached_topology = self._figure_cache[cache_key]
            fig = _figure_from_cached_dict(cached_fig_dict)
            # The cached figure was built with whatever camera was
            # current at the time. The corner axis-key compass is a
            # paper-coord overlay whose arrows are pre-projected from
            # that camera, so reusing the cache verbatim freezes the
            # compass at a stale angle. Refresh the compass +
            # disorder legend under the *live* camera, which is cheap
            # (a handful of trig ops, no mesh rebuild).
            try:
                with perf_log.time_block("scene_for_state", kind="event", scene_id=scene_id, cached=True):
                    scene_for_overlay = self.scene_for_state(state)
                style_for_overlay = self.style_for_state(state, scene=scene_for_overlay)
                annotations, shapes = compose_axis_key_layout(scene_for_overlay, style_for_overlay)
                # Plotly's ``_validate=False`` reconstruction path is
                # picky about ``annotations=None`` and ``shapes=None``:
                # subsequent ``fig.layout.annotations`` access raises
                # ``TypeError: 'NoneType' object is not iterable``
                # because the lazy compound-array initialiser tries to
                # iterate the stored value. Pass an empty list instead.
                fig.update_layout(
                    annotations=annotations or [],
                    shapes=shapes or [],
                )
                camera = _plotly_camera(state.get("camera"))
                if camera:
                    fig.update_layout(scene_camera=camera)
            except Exception:  # pragma: no cover - defensive, fall back to verbatim cache
                pass
            # ``topology_data`` is read-only for callers (histogram,
            # results-markdown, structure-summary). The renderer's
            # painter caches live on it but they only grow; sharing
            # the same dict across callers keeps the caches warm
            # across cache hits and saves ~25 ms of redundant
            # deepcopy on the hot slider path.
            return fig, cached_topology
        with perf_log.time_block("scene_for_state", kind="event", scene_id=scene_id):
            scene = self.scene_for_state(state)
        atom_count = len(scene.get("draw_atoms", []))
        bond_count = len(scene.get("bonds", []))
        replica_count = sum(1 for atom in scene.get("draw_atoms", []) if atom.get("_is_boundary_replica"))
        with perf_log.time_block(
            "topology_for_state",
            kind="event",
            scene_id=scene_id,
            n_specs=len((state.get("polyhedron_specs") or [])),
        ):
            topology_data = self.topology_for_state(state, click_data=click_data)
        with perf_log.time_block(
            "build_figure",
            kind="event",
            scene_id=scene_id,
            atoms=atom_count,
            bonds=bond_count,
            replicas=replica_count,
        ):
            fig = build_figure(scene, self.style_for_state(state, scene=scene), topology_data=topology_data)
        camera = _plotly_camera(state.get("camera"))
        if camera:
            fig.update_layout(scene_camera=camera)
        if cache_key is not None:
            # ``topology_data`` is the wrapper produced by
            # ``_attach_spec_colors`` and is keyed on the same paint
            # key as the figure cache; storing the live reference (not
            # a deep copy) lets the renderer's painter caches inside
            # it stay warm across cache hits without paying ~25 ms of
            # ``copy.deepcopy`` on every miss.
            self._figure_cache[cache_key] = (
                fig.to_plotly_json(),
                topology_data,
            )
            self._figure_cache_order.append(cache_key)
            while len(self._figure_cache_order) > 16:
                old_key = self._figure_cache_order.pop(0)
                self._figure_cache.pop(old_key, None)
        return fig, topology_data

    def render_current_png(
        self,
        scene_id: Optional[str] = None,
        *,
        raise_errors: bool = False,
        width: int | None = None,
        height: int | None = None,
        scale: float = 2.0,
        fast: bool = False,
    ) -> bytes:
        state = self.get_state(scene_id)
        if fast:
            state = copy.deepcopy(state)
            state["material"] = "flat"
            state["fast_rendering"] = True
        fig, _ = self.figure_for_state(state)
        kwargs: dict[str, Any] = {"format": "png", "scale": float(scale)}
        if width is not None:
            kwargs["width"] = int(width)
        if height is not None:
            kwargs["height"] = int(height)
        try:
            with perf_log.time_block("http:screenshot", kind="http", scene_id=scene_id, fast=bool(fast)):
                return pio.to_image(fig, **kwargs)
        except Exception as exc:  # pragma: no cover - depends on local Chrome/Kaleido state
            if raise_errors:
                raise
            return _fallback_png(f"Plotly image export failed: {exc}")

    def default_camera(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        scene = self.scene_for_state(self.get_state() if state is None else state)
        return _plotly_camera(scene.get("camera")) or _plotly_camera(None)

    def get_camera(self, scene_id: Optional[str] = None) -> dict[str, Any]:
        state = self.get_state(scene_id)
        return _plotly_camera(state.get("camera")) or self.default_camera(state)

    def set_camera(
        self,
        camera: dict[str, Any],
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        self.patch_state({"camera": camera}, scene_id=scene_id, broadcast=broadcast)
        return self.get_camera(scene_id)

    def camera_action(
        self,
        action: str,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
        **payload,
    ) -> dict[str, Any]:
        if action == "reset":
            self._bump_camera_revision(scene_id=scene_id, broadcast=broadcast)
            return self.set_camera(
                self.default_camera(self.get_state(scene_id)),
                scene_id=scene_id,
                broadcast=broadcast,
            )

        if action == "align":
            self._bump_camera_revision(scene_id=scene_id, broadcast=broadcast)
            return self.align_camera(payload.get("axis"), scene_id=scene_id, broadcast=broadcast)

        if action == "fit":
            self._bump_camera_revision(scene_id=scene_id, broadcast=broadcast)
            state = self.get_state(scene_id)
            camera = self.default_camera(state)
            # Plotly uses unitless eye vectors against the already fixed
            # world-cube ranges; 1.55 fills most structures without clipping.
            eye = camera.get("eye", {})
            norm = np.linalg.norm([eye.get("x", 0.0), eye.get("y", 0.0), eye.get("z", 0.0)])
            if norm > 1e-8:
                scale = 1.55 / norm
                camera["eye"] = {axis: float(eye.get(axis, 0.0)) * scale for axis in ("x", "y", "z")}
            return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

        if action in ("projection", "set_projection"):
            return self.set_projection(
                payload.get("type") or payload.get("projection"),
                scene_id=scene_id,
                broadcast=broadcast,
            )

        current_camera = self.get_camera(scene_id)
        eye, center, up = _camera_vectors(current_camera)
        if action == "zoom":
            factor = float(payload.get("factor", 1.0))
            if abs(factor) > 1e-8:
                eye = eye / factor
        elif action == "pan":
            delta = np.array(
                [
                    float(payload.get("dx", 0.0)),
                    float(payload.get("dy", 0.0)),
                    float(payload.get("dz", 0.0)),
                ],
                dtype=float,
            )
            center = center + delta
        elif action == "orbit":
            yaw_deg = float(payload.get("yaw_deg", 0.0))
            pitch_deg = float(payload.get("pitch_deg", 0.0))
            eye = _rotate_vector(eye, up, yaw_deg)
            right = np.cross(eye, up)
            if np.linalg.norm(right) > 1e-8:
                eye = _rotate_vector(eye, right, pitch_deg)
                up = _rotate_vector(up, right, pitch_deg)
        # Preserve the existing projection across orbit/pan/zoom so the
        # caller doesn't have to repeat ``set_projection`` after every
        # movement (the renderer would otherwise default to perspective).
        projection = None
        proj_payload = (current_camera or {}).get("projection")
        if isinstance(proj_payload, dict) and proj_payload.get("type"):
            projection = str(proj_payload["type"])
        elif isinstance(self.get_state(scene_id).get("projection"), str):
            projection = self.get_state(scene_id)["projection"]
        camera = _camera_payload(eye, center, up, projection=projection)
        return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

    def align_camera(
        self,
        axis: Any,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        """Look down the requested lattice axis (``a``/``b``/``c``) or
        reciprocal axis (``a*``/``b*``/``c*``).

        Preserves the current eye-to-center distance so the user's
        zoom level survives an axis switch (mirrors VESTA's behaviour
        where the alignment buttons rotate but do not zoom).
        """
        key = _normalize_axis_key(axis)
        if key is None:
            raise ValueError(f"unknown axis: {axis!r}; pick one of {_AXIS_VIEW_KEYS}")
        state = self.get_state(scene_id)
        scene = self.scene_for_state(state)
        M = np.asarray(scene["M"], dtype=float)
        current = self.get_camera(scene_id)
        eye, center, _up = _camera_vectors(current)
        eye_distance = float(np.linalg.norm(eye - center))
        if eye_distance < 1e-6:
            eye_distance = 1.8
        # Carry projection through the alignment so users who have
        # opted into orthographic don't get bounced back to perspective
        # every time they hit a "down a" button.
        projection = None
        proj_payload = (current or {}).get("projection")
        if isinstance(proj_payload, dict) and proj_payload.get("type"):
            projection = str(proj_payload["type"])
        elif isinstance(state.get("projection"), str):
            projection = state["projection"]
        camera = camera_for_axis(
            M,
            key,
            eye_distance=eye_distance,
            center=center,
            projection=projection,
        )
        return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

    def set_projection(
        self,
        projection: Any,
        scene_id: Optional[str] = None,
        *,
        broadcast: bool = True,
    ) -> dict[str, Any]:
        """Toggle the camera projection between ``perspective`` and
        ``orthographic``. Persists onto ``state["projection"]`` so the
        next ``style_for_state`` reflects the choice and so the REST
        ``GET /state`` echoes back what was set.
        """
        normalized = _coerce_projection(projection, fallback="perspective")
        self.patch_state({"projection": normalized}, scene_id=scene_id, broadcast=broadcast)
        # Stamp ``projection`` onto the persisted camera dict so a
        # subsequent ``set_camera`` round-trip (e.g. user drags the
        # scene to a new orientation) doesn't drop the choice.
        camera = dict(self.get_camera(scene_id))
        camera["projection"] = {"type": normalized}
        return self.set_camera(camera, scene_id=scene_id, broadcast=broadcast)

    def _bump_camera_revision(self, scene_id: Optional[str] = None, *, broadcast: bool = True) -> int:
        """Increment ``state['camera_revision']`` so the next figure
        rebuild gets a fresh ``layout.scene.uirevision`` and Plotly
        accepts the layout-supplied camera instead of preserving the
        user's last mouse-drag rotation.

        Mouse-drag updates flow through ``patch_state`` directly (not
        through ``camera_action``) so they intentionally do NOT bump
        the revision -- preserving Plotly's drag continuity across
        non-camera UI toggles like Labels/Hydrogens.
        """
        state = self.get_state(scene_id)
        current = int(state.get("camera_revision", 0) or 0)
        self.patch_state({"camera_revision": current + 1}, scene_id=scene_id, broadcast=broadcast)
        return current + 1

