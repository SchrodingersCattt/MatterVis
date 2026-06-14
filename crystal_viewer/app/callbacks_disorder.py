from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *
from ..render.selection import disorder_preview_outline_trace


def _coerce_count(value: Any) -> int:
    try:
        return max(1, min(int(value), 128))
    except (TypeError, ValueError):
        return 5


def _coerce_seed(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _matched_draw_count(scene: dict[str, Any], raw_indices: Iterable[int]) -> int:
    """How many *drawn* atoms a replica's raw highlight indices resolve
    to (via ``_source_index``). Used only for the row meta count."""
    wanted = {int(idx) for idx in raw_indices}
    if not wanted:
        return 0
    count = 0
    for atom in scene.get("draw_atoms") or []:
        source = atom.get("_source_index")
        if source is None:
            continue
        try:
            if int(source) in wanted:
                count += 1
        except (TypeError, ValueError):
            continue
    return count


def _preview_mesh_for_indices(scene: dict[str, Any], style: dict[str, Any], raw_indices: Iterable[int]) -> dict[str, list]:
    """Pre-compute the outline mesh once (at resolve time) so the hover
    path can be a pure-browser ``Plotly.restyle`` with zero server round
    trip. Matched by ``_source_index`` (not label) so symmetry copies that
    share a scene label don't collapse distinct replicas onto one set."""
    source_indices = {int(idx) for idx in raw_indices}
    trace_json = disorder_preview_outline_trace(
        scene, style, highlight_source_indices=source_indices
    ).to_plotly_json()

    def _coerce(arr: Any) -> list:
        if arr is None:
            return []
        return [float(v) if isinstance(v, float) else v for v in list(arr)]

    return {key: _coerce(trace_json.get(key)) for key in ("x", "y", "z", "i", "j", "k")}


def _replica_rows(replicas: list[dict[str, Any]], *, status: str = "ok") -> list[Any]:
    if status == "error":
        return [html.Div("Resolve disorder failed. See server log for details.", className="disorder-empty disorder-empty--error")]
    if not replicas:
        return [html.Div("No SHELX-style disorder replicas found.", className="disorder-empty")]

    rows: list[Any] = []
    for replica in replicas:
        replica_id = str(replica.get("id") or "")
        added = len(replica.get("added_indices") or [])
        dropped = len(replica.get("dropped_indices") or [])
        highlight = int(replica.get("highlight_count") or 0)
        rows.append(
            html.Div(
                [
                    html.Div(
                        str(replica.get("label") or replica_id or "Replica"),
                        className="disorder-row-title",
                    ),
                    html.Div(
                        f"kept {int(replica.get('kept_count') or 0)} atoms | +{added} / -{dropped} | highlight {highlight}",
                        className="disorder-row-meta",
                    ),
                ],
                id={"type": "disorder-row", "replica_id": replica_id},
                className="disorder-row",
                **{"data-replica-id": replica_id},
            )
        )
    return rows


# Pure-browser preview path:
#
# 1. When ``disorder-replicas-store`` changes after Resolve, inject one
#    transparent Mesh3d trace per replica. This is the only point where preview
#    geometry is added to Plotly.
# 2. Hover only flips opacity on those already-existing traces. It never edits
#    ``x/y/z/i/j/k`` geometry or ``visible``. In Plotly gl3d, both geometry
#    edits and ``visible=false -> true`` can re-enter scene setup and snap the
#    camera before any restore can run.
_PREPARE_PREVIEW_JS = """
function(store) {
    var nope = window.dash_clientside.no_update;
    var gd = document.querySelector('#crystal-graph .js-plotly-plot')
             || document.getElementById('crystal-graph');
    if (!gd || !window.Plotly || !gd.data) { return nope; }

    var prefix = 'disorder-preview-outline:';
    var oldIndices = [];
    for (var i = 0; i < gd.data.length; i++) {
        if (String(gd.data[i].name || '').indexOf(prefix) === 0) {
            oldIndices.push(i);
        }
    }

    function liveViewport() {
        try {
            var sc = gd._fullLayout && gd._fullLayout.scene && gd._fullLayout.scene._scene;
            if (!sc || typeof sc.getCamera !== 'function') { return null; }
            return {
                scene: sc,
                camera: JSON.parse(JSON.stringify(sc.getCamera())),
                aspectratio: sc.glplot && sc.glplot.getAspectratio && sc.glplot.getAspectratio(),
            };
        } catch (e) {
            return null;
        }
    }

    function restoreViewport(viewport) {
        if (!viewport || !viewport.scene || !viewport.camera) { return; }
        try {
            viewport.scene.setViewport({
                camera: viewport.camera,
                aspectratio: viewport.aspectratio
                    || (viewport.scene.glplot && viewport.scene.glplot.getAspectratio && viewport.scene.glplot.getAspectratio()),
            });
        } catch (e) {}
    }

    var traces = [];
    if (store && store.replicas) {
        for (var r = 0; r < store.replicas.length; r++) {
            var rep = store.replicas[r] || {};
            var mesh = rep.preview_mesh || {};
            var replicaId = String(rep.id || '');
            if (!replicaId || !mesh.x || !mesh.x.length) { continue; }
            traces.push({
                type: 'mesh3d',
                name: prefix + replicaId,
                x: mesh.x || [],
                y: mesh.y || [],
                z: mesh.z || [],
                i: mesh.i || [],
                j: mesh.j || [],
                k: mesh.k || [],
                color: '#FFD400',
                opacity: 0,
                flatshading: false,
                hoverinfo: 'skip',
                showlegend: false,
                visible: true,
                meta: {kind: 'disorder-preview-outline', replica_id: replicaId},
            });
        }
    }

    var viewport = liveViewport();
    var p = Promise.resolve();
    if (oldIndices.length) {
        p = p.then(function() { return window.Plotly.deleteTraces(gd, oldIndices); });
    }
    if (traces.length) {
        p = p.then(function() { return window.Plotly.addTraces(gd, traces); });
    }
    p.then(function() {
        window.requestAnimationFrame(function() { restoreViewport(viewport); });
    });
    return nope;
}
"""

_HOVER_PREVIEW_JS = """
function(replicaId, store) {
    var nope = window.dash_clientside.no_update;
    var gd = document.querySelector('#crystal-graph .js-plotly-plot')
             || document.getElementById('crystal-graph');
    if (!gd || !window.Plotly || !gd.data) { return nope; }
    var prefix = 'disorder-preview-outline:';
    var indices = [];
    var opacity = [];
    for (var t = 0; t < gd.data.length; t++) {
        var name = String(gd.data[t].name || '');
        if (name.indexOf(prefix) !== 0) { continue; }
        indices.push(t);
        opacity.push(Boolean(replicaId) && name === prefix + String(replicaId) ? 0.55 : 0);
    }
    if (!indices.length) {
        // Trace preparation may still be in flight right after Resolve.
        return nope;
    }
    window.Plotly.restyle(gd, {opacity: opacity}, indices);
    return nope;
}
"""


def register_disorder_callbacks(app, backend):
    @app.callback(
        Output("disorder-replicas-store", "data"),
        Output("disorder-replicas-list", "children"),
        Input("disorder-resolve-btn", "n_clicks"),
        State("scene-tabs", "value"),
        State("disorder-resolve-method", "value"),
        State("disorder-resolve-count", "value"),
        State("disorder-resolve-seed", "value"),
        prevent_initial_call=True,
    )
    def on_resolve_clicked(_clicks, scene_id, method, count, seed):
        # NOTE: this callback deliberately does NOT write
        # ``agent-state-store``. That store is the Input of ``update_view``
        # (callbacks_view), so echoing state here would rebuild the whole
        # Plotly figure on every Resolve click -- and ``update_view``
        # repaints with ``camera-state-store``'s camera, which only tracks
        # axis-button moves (NOT mouse-drag rotation). The result was a
        # full rebuild that reset the user's view and felt slow. The
        # disorder preview lives entirely in ``disorder-replicas-store``
        # (browser) + per-replica preview traces injected by the client, so no
        # figure rebuild is needed.
        scene_id = scene_id or backend.active_scene_id()
        if scene_id and scene_id not in backend.scene_store.scenes:
            return no_update, no_update
        method = str(method or "enumerate")
        count = _coerce_count(count)
        seed = _coerce_seed(seed)
        try:
            replicas = backend.resolve_disorder(
                scene_id,
                method=method,
                count=count,
                seed=seed,
            )
            status = "ok" if replicas else "no_disorder"
            # Pre-compute each replica's highlight mesh against the live
            # scene so hover is a pure-browser restyle.
            if replicas:
                state = backend.get_state(scene_id)
                scene = backend.scene_for_state(state)
                style = backend.style_for_state(state, scene=scene)
                for replica in replicas:
                    highlight_indices = replica.get("highlight_indices") or []
                    replica["highlight_count"] = _matched_draw_count(scene, highlight_indices)
                    replica["preview_mesh"] = _preview_mesh_for_indices(scene, style, highlight_indices)
            # Persist only the lightweight resolve settings + index list;
            # the mesh stays in the browser store, not in scene state.
            backend.patch_state(
                {
                    "disorder_resolve": {"method": method, "count": count, "seed": seed},
                    "disorder_replicas": [
                        {k: v for k, v in replica.items() if k != "preview_mesh"}
                        for replica in replicas
                    ],
                },
                scene_id=scene_id,
                broadcast=False,
            )
        except Exception as exc:
            replicas = []
            status = "error"
            perf_log.record(
                "callback:resolve_disorder",
                duration_ms=0.0,
                kind="cb",
                info={"scene_id": scene_id, "error": str(exc), "type": exc.__class__.__name__},
            )
        store = {
            "scene_id": scene_id,
            "status": status,
            "method": method,
            "count": count,
            "seed": seed,
            "replicas": replicas,
        }
        return store, _replica_rows(replicas, status=status)

    app.clientside_callback(
        _PREPARE_PREVIEW_JS,
        Output("disorder-preview-sink", "data", allow_duplicate=True),
        Input("disorder-replicas-store", "data"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        _HOVER_PREVIEW_JS,
        Output("disorder-preview-sink", "data", allow_duplicate=True),
        Input("disorder-hover-id", "data"),
        State("disorder-replicas-store", "data"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("disorder-persist-sink", "data"),
        Input("disorder-resolve-method", "value"),
        Input("disorder-resolve-count", "value"),
        Input("disorder-resolve-seed", "value"),
        State("scene-tabs", "value"),
        prevent_initial_call=True,
    )
    def persist_resolve_inputs(method, count, seed, scene_id):
        # Persists the resolve inputs to backend state for reload/REST, but
        # outputs to a dummy sink (NOT ``agent-state-store``) so changing
        # the mode / count / seed never rebuilds the Plotly figure.
        scene_id = scene_id or backend.active_scene_id()
        if scene_id and scene_id not in backend.scene_store.scenes:
            return no_update
        method = str(method or "enumerate")
        count = _coerce_count(count)
        seed = _coerce_seed(seed)
        current = backend.get_state(scene_id).get("disorder_resolve") or {}
        next_value = {"method": method, "count": count, "seed": seed}
        if current == next_value:
            return no_update
        backend.patch_state({"disorder_resolve": next_value}, scene_id=scene_id, broadcast=False)
        return no_update


__all__ = [name for name in globals() if not name.startswith("__")]
