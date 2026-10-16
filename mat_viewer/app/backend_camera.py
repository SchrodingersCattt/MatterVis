from __future__ import annotations
# ruff: noqa: F401,F403,F405

import plotly.graph_objects as go

from .shared import *
from .camera_helpers import *
from .style_helpers import *
from .backend_core import FIGURE_CACHE_MAX


def _figure_from_cached_dict(cached_dict: dict) -> go.Figure:
    """Reconstruct a ``go.Figure`` from a JSON-style dict snapshot.

    The figure cache stores ``fig.to_plotly_json()`` rather than a
    deep-copied ``go.Figure`` because Plotly's validator chain makes
    ``copy.deepcopy(go.Figure)`` ~10x slower than copying the
    equivalent plain dict and reconstructing with ``_validate=False``.
    See the cache hit branch of ``figure_for_state`` for the why.
    """
    # Cached figure dicts are canonical cache entries shared across hits.
    # Reconstruct from a deep snapshot so per-request layout patches stay
    # request-local and never mutate the cache owner's copy.
    snapshot = copy.deepcopy(cached_dict)
    return go.Figure(snapshot, _validate=False)


def _polyhedron_enabled_map(state: dict[str, Any] | None) -> dict[str, bool]:
    """``{spec_id: enabled}`` lookup used by the post-cache trace-
    visibility patch in ``figure_for_state``. Specs without an id
    are skipped because the renderer can't route a trace to them.
    """
    return _polyhedron_enabled_lookup(state)


def _apply_polyhedron_visibility_patch(fig: go.Figure, state: dict[str, Any] | None) -> None:
    """Post-cache patch: walk ``fig.data``, find every trace tagged
    with ``meta={"spec_id": ..., "kind": "polyhedron"}`` and force
    ``trace.visible`` to match the current spec's ``enabled`` flag.

    This is what makes spec-level ``enabled`` toggles cheap. The
    figure cache key drops ``enabled`` (see
    ``_figure_state_cache_key``) so two states differing only in a
    row's checkbox land on the SAME cached figure; this loop then
    flips the relevant traces' ``visible`` attribute in place
    (~hundreds of microseconds for a ~30-trace figure) instead of
    rebuilding 200-400 ms of Mesh3d arrays.
    """
    enabled_map = _polyhedron_enabled_map(state)
    if not enabled_map:
        return
    for trace in fig.data:
        meta = getattr(trace, "meta", None)
        if not isinstance(meta, dict):
            continue
        if meta.get("kind") != "polyhedron":
            continue
        spec_id = meta.get("spec_id")
        if not spec_id:
            continue
        # Plotly accepts ``True`` / ``False`` / ``"legendonly"`` for
        # ``visible``; we only need on/off here.
        trace.visible = bool(enabled_map.get(str(spec_id), True))


def _with_state_projection(
    camera: Optional[dict[str, Any]], state: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Attach the scene projection when a stamped camera omitted it.

    Position/focal cameras convert to an eye dict without ``projection``.
    Plotly then paints perspective even when the radio is orthographic,
    and the next ortho apply looks at that eye through the wrong frustum.
    """
    if not isinstance(camera, dict):
        return camera
    if _camera_projection_name(camera):
        return camera
    stamped = copy.deepcopy(camera)
    stamped["projection"] = {
        "type": _coerce_projection(state.get("projection") or "perspective")
    }
    return stamped


def _camera_projection_name(camera: Optional[dict[str, Any]]) -> Optional[str]:
    if not isinstance(camera, dict):
        return None
    raw = camera.get("projection")
    if isinstance(raw, dict) and raw.get("type"):
        return _coerce_projection(raw.get("type"))
    if isinstance(raw, str) and raw:
        return _coerce_projection(raw)
    return None


def _component_values(raw: Any) -> Optional[tuple[float, float, float]]:
    if isinstance(raw, dict):
        try:
            return (float(raw.get("x", 0.0)), float(raw.get("y", 0.0)), float(raw.get("z", 0.0)))
        except (TypeError, ValueError):
            return None
    try:
        values = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    if len(values) != 3:
        return None
    return (values[0], values[1], values[2])


def _camera_components_finite(camera: dict[str, Any]) -> bool:
    """Reject a drag whose eye, center, or up is present but non-finite.

    Missing center or up is a usable camera: the Plotly converter fills
    those from its defaults. A present NaN is not.
    """
    import math
    if "eye" in camera:
        required = ("eye",)
    elif "position" in camera or "focal_point" in camera:
        required = tuple(
            group for group in ("position", "focal_point") if group in camera
        )
    else:
        return False
    for group in required:
        values = _component_values(camera.get(group))
        if values is None or not all(math.isfinite(value) for value in values):
            return False
    for group in ("center", "up", "focal_point"):
        if group not in camera or camera.get(group) is None:
            continue
        values = _component_values(camera.get(group))
        if values is None or not all(math.isfinite(value) for value in values):
            return False
    return True


class _CameraBackendMixin:
    def figure_for_state(
        self,
        state: Optional[dict[str, Any]] = None,
        click_data: Optional[dict[str, Any]] = None,
        *,
        async_topology: bool = False,
        async_figure: bool = False,
    ):
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
                cache_key = self._figure_state_cache_key(state)
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
        cached_entry = None
        if cache_key is not None:
            with self._figure_cache_lock:
                cached_entry = self._figure_cache.get(cache_key)
                if cached_entry is not None:
                    self._figure_cache.move_to_end(cache_key)
        if cached_entry is not None:
            cached_fig_dict, cached_topology = cached_entry
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
                title_text = (
                    scene_for_overlay.get("display_title")
                    or scene_for_overlay.get("title")
                    or scene_for_overlay.get("name")
                    or ""
                )
                fig.update_layout(
                    annotations=annotations or [],
                    shapes=shapes or [],
                    uirevision=style_for_overlay["uirevision"],
                    scene_uirevision=style_for_overlay["uirevision"],
                    title=dict(text=str(title_text), x=0.5),
                )
                camera = self._layout_camera(state, scene_for_overlay, style_for_overlay)
                if camera:
                    fig.update_layout(scene_camera=camera)
            except Exception:  # pragma: no cover - defensive, fall back to verbatim cache
                pass
            # Phase 6: spec-level ``enabled`` is patched onto trace
            # visibility here so the figure cache can stay
            # invariant to row-checkbox toggles (see
            # ``_figure_state_cache_key`` for the matching key
            # change). Cheap walk, no Mesh3d rebuild.
            _apply_polyhedron_visibility_patch(fig, state)
            # ``topology_data`` is read-only for callers (histogram,
            # results-markdown, structure-summary). The renderer's
            # painter caches live on it but they only grow; sharing
            # the same dict across callers keeps the caches warm
            # across cache hits and saves ~25 ms of redundant
            # deepcopy on the hot slider path.
            return self._stamp_figure_render_metadata(fig, state), cached_topology

        # --- CACHE MISS ---
        # If async_figure is requested, offload the full build to a
        # background thread and return a skeleton figure immediately.
        if async_figure:
            worker = getattr(self, "_render_worker", None)
            if worker is not None:
                worker.request_figure_build(dict(state))
                perf_log.record(
                    "figure_for_state:async-submitted",
                    kind="event",
                    info={"scene_id": scene_id},
                )
                # Return a minimal skeleton figure (unit cell box only)
                # so the Dash callback returns immediately.
                skeleton = self._skeleton_figure(state)
                return self._stamp_figure_render_metadata(skeleton, state), None

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
            topology_pending = False
            if async_topology:
                context = self._topology_context(state, click_data=click_data, scene=scene)
                if context is None:
                    topology_data = None
                else:
                    bundle = context["bundle"]
                    cache = self._topology_cache(bundle)
                    with bundle._topology_state_cache_lock:
                        cached_geometry = cache.get(context["cache_key"])
                        if cached_geometry is not None:
                            cache.move_to_end(context["cache_key"])
                    if cached_geometry is None:
                        worker = getattr(self, "_render_worker", None)
                        topology_pending = bool(
                            worker is not None and worker.request_topology(dict(state), context)
                        )
                        topology_data = None
                    else:
                        topology_data = self._attach_spec_colors(
                            cached_geometry,
                            context["effective_specs"],
                            analysis_spec_id=context["analysis_spec_id"],
                        )
            else:
                topology_data = self.topology_for_state(state, click_data=click_data)
        with perf_log.time_block(
            "build_figure",
            kind="event",
            scene_id=scene_id,
            atoms=atom_count,
            bonds=bond_count,
            replicas=replica_count,
        ):
            style = self.style_for_state(state, scene=scene)
            camera = self._layout_camera(state, scene, style)
            # Flat + ORTEP: render via Matplotlib 2D and embed as a static
            # image in a Plotly figure (the web viewer needs a go.Figure).
            if style.get("material") == "flat" and style.get("style") == "ortep":
                # The publication image follows the camera the caller stored.
                # ``camera`` may be a framing substitute for the WebGL orbit
                # anchors; baking that substitute would ignore an orbit that
                # still points at the atoms.
                stored_camera = state.get("camera")
                ortep_camera = stored_camera if isinstance(stored_camera, dict) else camera
                fig = self._flat_ortep_figure(
                    scene,
                    style,
                    camera=ortep_camera,
                )
            else:
                fig = build_figure(scene, style, topology_data=topology_data)
        if camera:
            fig.update_layout(scene_camera=camera)
        if cache_key is not None and not topology_pending:
            # ``topology_data`` is the wrapper produced by
            # ``_attach_spec_colors`` and is keyed on the same paint
            # key as the figure cache; storing the live reference (not
            # a deep copy) lets the renderer's painter caches inside
            # it stay warm across cache hits without paying ~25 ms of
            # ``copy.deepcopy`` on every miss.
            #
            # The cached snapshot is taken BEFORE the visibility patch
            # below: the cache stores the canonical "all enabled"
            # figure shape, so subsequent hits with different
            # ``enabled`` combinations all land on the same entry.
            with self._figure_cache_lock:
                self._figure_cache[cache_key] = (
                    fig.to_plotly_json(),
                    topology_data,
                )
                self._figure_cache.move_to_end(cache_key)
                while len(self._figure_cache) > FIGURE_CACHE_MAX:
                    self._figure_cache.popitem(last=False)
        # Apply the live ``enabled`` flags to this fresh build too --
        # the figure cache snapshot above stored the un-patched
        # version, so we need to mirror the cache-hit branch's
        # behaviour for the current response.
        _apply_polyhedron_visibility_patch(fig, state)
        return self._stamp_figure_render_metadata(fig, state), topology_data

    def _skeleton_figure(self, state: dict[str, Any]) -> "go.Figure":
        """Build a lightweight placeholder figure for async rendering.

        Contains only the unit cell box and compass annotations so the
        user sees something immediately while the full figure builds in
        the background.
        """
        scene = self.scene_for_state(state)
        style = self.style_for_state(state, scene=scene)
        from ..render.figures import _unit_cell_traces, _axis_traces, _scene_ranges
        from ..render.figures import _style_trace_dicts, _traces_to_dicts
        from ..render.viewport import figure_axis_layout
        xr, yr, zr = _scene_ranges(scene, style, topology_data=None)
        trace_dicts: list[dict] = []
        trace_dicts.extend(_traces_to_dicts(_unit_cell_traces(scene, style)))
        trace_dicts.extend(_traces_to_dicts(_axis_traces(scene, style)))
        trace_dicts = _style_trace_dicts(trace_dicts, style)
        fig = go.Figure(data=trace_dicts, _validate=False)
        scene_layout = figure_axis_layout(scene, style, xr, yr, zr)
        camera = self._layout_camera(state, scene, style)
        if camera:
            scene_layout["camera"] = camera
        fig.update_layout(
            scene=scene_layout,
            meta={
                "mattervis_pending": True,
                "scene_id": state.get("scene_id"),
            },
            margin=dict(l=0, r=0, t=0, b=0),
            showlegend=False,
        )
        try:
            fig._mattervis_pending = True
        except Exception:
            pass
        return fig

    def _flat_ortep_figure(
        self,
        scene: dict,
        style: dict,
        *,
        camera: dict[str, Any] | None = None,
    ) -> "go.Figure":
        """Render flat+ORTEP with a hidden 3D camera interaction layer."""
        import io
        import base64
        import matplotlib.pyplot as plt
        from ..ortep.flat_render import render_ortep_flat

        render_scene = dict(scene)
        if camera:
            eye, center, up = _camera_vectors(camera)
            view_direction = eye - center
            norm = float(np.linalg.norm(view_direction))
            if norm > 1e-8:
                render_scene["view_direction"] = view_direction / norm
                render_scene["up"] = up
        mpl_fig = render_ortep_flat(render_scene, style)
        buf = io.BytesIO()
        mpl_fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(mpl_fig)
        buf.seek(0)
        encoded = base64.b64encode(buf.read()).decode("ascii")

        fig = go.Figure()
        anchor_points = [
            np.asarray(atom.get("cart"), dtype=float)
            for atom in scene.get("draw_atoms", [])
            if np.asarray(atom.get("cart"), dtype=float).shape == (3,)
        ]
        if scene.get("cell") is not None and scene.get("M") is not None:
            anchor_points.extend(
                np.asarray(scene["M"], dtype=float)[index]
                for index in range(3)
            )
        anchors = np.asarray(anchor_points or [[0.0, 0.0, 0.0]], dtype=float)
        fig.add_trace(
            go.Scatter3d(
                x=anchors[:, 0],
                y=anchors[:, 1],
                z=anchors[:, 2],
                mode="markers",
                marker=dict(size=1, opacity=0.0),
                hoverinfo="skip",
                showlegend=False,
                name="flat-ortep-camera",
            )
        )
        fig.add_layout_image(
            dict(
                source=f"data:image/png;base64,{encoded}",
                xref="paper", yref="paper",
                x=0, y=1, sizex=1, sizey=1,
                xanchor="left", yanchor="top",
                layer="below",
            )
        )
        fig.update_layout(
            scene=dict(
                xaxis=dict(visible=False),
                yaxis=dict(visible=False),
                zaxis=dict(visible=False),
                bgcolor="white",
                dragmode="orbit",
                camera=camera or _plotly_camera(scene.get("camera")),
                uirevision=style.get("uirevision", "flat-ortep"),
            ),
            xaxis=dict(visible=False, range=[0, 1]),
            yaxis=dict(visible=False, range=[0, 1]),
            paper_bgcolor="white",
            plot_bgcolor="white",
            margin=dict(l=0, r=0, t=0, b=0),
            annotations=[dict(
                text="2D ORTEP Publication Mode — use Export for vector PDF",
                xref="paper", yref="paper", x=0.5, y=0.02,
                showarrow=False, font=dict(size=10, color="#666"),
            )],
        )
        return fig

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
            # Fast PNG tweaks are request-local UI state; clone the canonical
            # scene snapshot before forcing flat material / fast rendering.
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
            raise RuntimeError(f"Plotly/Kaleido PNG export failed: {exc}") from exc

    def camera_that_frames(
        self,
        state: dict[str, Any],
        camera: Optional[dict[str, Any]],
        *,
        scene: dict[str, Any],
        style: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Return a camera that shows ``scene``, and whether it replaced ``camera``.

        A non-finite eye, or an eye that has collapsed onto the center, misses
        the frustum. A view that only clips a speck or a thin line is treated
        the same way. When the structure default shows the atoms, that default
        is returned. The default keeps the candidate's projection, so an
        orthographic drag is not replaced by a perspective camera. A zoom
        whose visible centers still span the viewport is returned unchanged.
        When neither camera shows the scene, the candidate is kept.
        """
        from ..render.screen_projection import camera_shows_scene
        from ..render.viewport import _plotly_camera_from_scene
        from ..render.viewport_spec import resolve_viewport

        probe = dict(style)
        if isinstance(camera, dict):
            probe["camera"] = copy.deepcopy(camera)
        viewport = resolve_viewport(scene, probe)
        points = [
            atom.get("cart")
            for atom in (scene.get("draw_atoms") or [])
            if isinstance(atom, dict) and atom.get("cart") is not None
        ]
        if not points:
            xr, yr, zr = viewport.ranges
            points = [[x, y, z] for x in xr for y in yr for z in zr]
        projection = probe.get("projection")

        def _frames(candidate: Optional[dict[str, Any]], candidate_projection: Any) -> bool:
            if not isinstance(candidate, dict) or not _camera_components_finite(candidate):
                return False
            return camera_shows_scene(
                candidate,
                points,
                ranges=viewport.ranges,
                aspectratio=viewport.aspectratio,
                projection=candidate_projection,
            )

        current = None
        if isinstance(camera, dict):
            try:
                current = _plotly_camera_from_scene(scene, probe)
            except (TypeError, ValueError, ZeroDivisionError):
                current = None
        if _frames(current, projection):
            return copy.deepcopy(camera), False

        fitted_state = dict(state)
        candidate_projection = _camera_projection_name(camera)
        if candidate_projection:
            fitted_state["projection"] = candidate_projection
        fitted = self.default_camera(fitted_state)
        fitted_projection = _camera_projection_name(fitted) or projection
        if _frames(fitted, fitted_projection):
            return fitted, True
        if isinstance(camera, dict):
            return copy.deepcopy(camera), False
        return fitted, False

    def _layout_camera(
        self,
        state: dict[str, Any],
        scene: dict[str, Any],
        style: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Camera stamped onto a figure.

        The stored camera is used when it shows the scene. A camera that
        leaves every atom outside the frustum is replaced for this figure
        only; capture and startup persist that replacement separately.
        """
        raw = state.get("camera")
        if not isinstance(raw, dict):
            return _with_state_projection(_plotly_camera(raw), state)
        try:
            fitted, replaced = self.camera_that_frames(
                state, raw, scene=scene, style=style
            )
        except (KeyError, ValueError, OSError, TypeError):
            return _with_state_projection(_plotly_camera(raw), state)
        if not replaced:
            return _with_state_projection(_plotly_camera(raw), state)
        return _with_state_projection(fitted, state)

    def refit_scene_camera(
        self, scene_id: Optional[str] = None, *, force: bool = False
    ) -> dict[str, Any]:
        """Persist the default camera when the stored one misses the scene.

        Display-scope changes install the structure default: the previous
        look-at belongs to a different frustum, and Reset was the only
        control that brought the atoms back. ``force`` does that install.
        A drag or a startup camera that no longer shows the scene is
        replaced the same way. A zoom that still spans the viewport is
        left unchanged, including its revision.
        """
        state = self.get_state(scene_id)
        target = state.get("scene_id") or scene_id
        camera = state.get("camera")
        if force:
            fitted = self.default_camera(state)
            if isinstance(camera, dict) and camera == fitted:
                return state
            self.patch_state(
                {
                    "camera": fitted,
                    "camera_revision": int(state.get("camera_revision", 0) or 0),
                },
                scene_id=target,
                broadcast=False,
            )
            return self.get_state(target)
        if not isinstance(camera, dict):
            return state
        try:
            scene = self.scene_for_state(state)
            style = self.style_for_state(state, scene=scene)
            fitted, replaced = self.camera_that_frames(
                state, camera, scene=scene, style=style
            )
        except (KeyError, ValueError, OSError, TypeError):
            return state
        if not replaced:
            return state
        self.patch_state(
            {
                "camera": fitted,
                "camera_revision": int(state.get("camera_revision", 0) or 0),
            },
            scene_id=target,
            broadcast=False,
        )
        return self.get_state(target)

    def refit_offscreen_cameras(self) -> list[str]:
        """Replace persisted cameras that leave the scene outside the frustum.

        Scene cameras survive a restart. A drag that was stored on the
        wrong scene, or zoomed until the atoms left the view, is painted
        again on the next launch. The same check runs on every drag and
        display capture. Reset already installs the structure default;
        this pass does that for cameras loaded from the store when the
        default itself shows the scene.
        """
        refit: list[str] = []
        for scene_id in list(self.scene_store.order):
            try:
                before = self.scene_state(scene_id)
            except (KeyError, ValueError, OSError):
                continue
            if not isinstance(before.get("camera"), dict):
                continue
            try:
                after = self.refit_scene_camera(scene_id)
            except (KeyError, ValueError, OSError, TypeError):
                continue
            before_camera = before.get("camera")
            after_camera = after.get("camera")
            if before_camera != after_camera:
                refit.append(str(scene_id))
        return refit

    def default_camera(self, state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Canonical unzoomed camera for Reset and Fit.

        The eye comes from the structure scene, not from the live camera
        the browser persisted after a wheel zoom. The active projection
        is kept so Reset does not bounce orthographic back to perspective.
        """
        state = self.get_state() if state is None else state
        scene = self.scene_for_state(state)
        camera = copy.deepcopy(_plotly_camera(scene.get("camera")) or {})
        if "eye" not in camera:
            camera = {
                "eye": {"x": 1.25, "y": 1.25, "z": 1.25},
                "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "up": {"x": 0.0, "y": 0.0, "z": 1.0},
            }
        projection = _coerce_projection((state or {}).get("projection"))
        camera["projection"] = {"type": projection}
        return camera

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
        # Orbit, pan, and zoom are commands, not drag persistence. Advance
        # the camera clock explicitly with the camera write so Plotly takes
        # the new eye.  Drag patches omit this key so a later display toggle
        # does not snap the WebGL camera back.  ``patch_state`` treats an
        # explicit value as the requested next revision and keeps it
        # monotonic across scene-scoped updates.
        revision = int(self.get_state(scene_id).get("camera_revision", 0) or 0) + 1
        self.patch_state(
            {"camera": camera, "camera_revision": revision},
            scene_id=scene_id,
            broadcast=broadcast,
        )
        return self.get_camera(scene_id)

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
        state = self.get_state(scene_id)
        camera = dict(self.get_camera(scene_id))
        camera["projection"] = {"type": normalized}
        trial = dict(state)
        trial["projection"] = normalized
        trial["camera"] = camera
        try:
            scene = self.scene_for_state(trial)
            style = self.style_for_state(trial, scene=scene)
            fitted, replaced = self.camera_that_frames(
                trial, camera, scene=scene, style=style
            )
        except (KeyError, ValueError, OSError, TypeError):
            fitted, replaced = camera, False
        # Projection is a camera command.  Commit it atomically with the
        # camera so it advances only the camera clock and cannot enqueue two
        # competing full-frame deliveries. A projection that pushes every
        # atom outside the new frustum is replaced in the same write.
        payload: dict[str, Any] = {
            "projection": normalized,
            "camera": fitted if replaced else camera,
        }
        if replaced:
            payload["camera_revision"] = int(state.get("camera_revision", 0) or 0)
        self.patch_state(payload, scene_id=scene_id, broadcast=broadcast)
        return self.get_camera(scene_id)

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
        key = str(scene_id or self.scene_store.active_id or "")
        current = max(
            int(state.get("camera_revision", 0) or 0),
            int(getattr(self, "_camera_revisions", {}).get(key, 0) or 0),
        )
        updated = self.patch_state(
            {"camera_revision": current + 1}, scene_id=scene_id, broadcast=broadcast
        )
        return int(updated.get("camera_revision", current + 1) or current + 1)

