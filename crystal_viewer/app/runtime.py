from __future__ import annotations
# ruff: noqa: F401,F403,F405

from .shared import *

def _install_callback_audit(app) -> None:
    """Log every /_dash-update-component request: which inputs changed
    (changedPropIds), which output owner was targeted, plus the
    response status / payload size and the originating User-Agent
    so we can tell if a "no response" report is coming from an
    embedded webview that does not propagate React events.

    Opt-in via ``MATTERVIS_AUDIT=1``; not safe for production
    because it parses every request body."""
    import sys

    import flask

    server = app.server

    @server.before_request
    def _before():
        flask.g._mv_t0 = time.perf_counter()

    @server.after_request
    def _after(response):
        if flask.request.path != "/_dash-update-component":
            return response
        try:
            payload = flask.request.get_json(silent=True) or {}
            changed = payload.get("changedPropIds") or []
        except Exception:
            changed = []
        # Sample polls 1/100 so the log stays useful; always log everything else.
        if changed == ["agent-state-poll.n_intervals"]:
            counter = getattr(flask.g, "_mv_poll_n", 0) + 1
            try:
                flask.g._mv_poll_n = counter
            except Exception:
                pass
            if counter % 100 != 1:
                return response
        t0 = getattr(flask.g, "_mv_t0", None)
        dt_ms = ((time.perf_counter() - t0) * 1000.0) if t0 is not None else -1.0
        ip = flask.request.headers.get("X-Forwarded-For") or flask.request.remote_addr or "?"
        ua = (flask.request.headers.get("User-Agent") or "?")[:80]
        out_id = payload.get("output", "")[:120]
        try:
            resp_len = len(response.get_data())
        except Exception:
            resp_len = -1
        sys.stdout.write(
            f"[mv-audit] ip={ip} ua={ua!r} {dt_ms:7.1f}ms status={response.status_code} resp={resp_len}B "
            f"changed={changed} out={out_id}\n"
        )
        sys.stdout.flush()
        return response


_PREWARM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mattervis-prewarm")


def _prewarm_bundle_async(backend: ViewerBackend, structure_name: str) -> None:
    def _job():
        try:
            bundle = backend.get_bundle(structure_name)
            defaults = backend.default_state(structure_name)
        except Exception:
            return
        for display_mode in ("formula_unit", "asymmetric_unit", "unit_cell", "cluster"):
            for show_hydrogen in (False, True):
                try:
                    scene = build_bundle_scene(
                        bundle,
                        display_mode=display_mode,
                        show_hydrogen=show_hydrogen,
                        preset=backend.preset,
                    )
                    style = dict(scene.get("style", {}))
                    options = list(defaults.get("display_options") or [])
                    if show_hydrogen and "hydrogens" not in options:
                        options.append("hydrogens")
                    elif not show_hydrogen:
                        options = [opt for opt in options if opt != "hydrogens"]
                    style.update(
                        style_from_controls(
                            defaults["atom_scale"],
                            defaults["bond_radius"],
                            defaults["minor_opacity"],
                            defaults["axis_scale"],
                            options,
                            material=defaults.get("material"),
                            render_style=defaults.get("style"),
                            disorder=defaults.get("disorder"),
                            ortep_mode=defaults.get("ortep_mode"),
                        )
                    )
                    style["display_mode"] = display_mode
                    style["topology_enabled"] = False
                    build_figure(scene, style, topology_data=None)
                except Exception:
                    continue

    try:
        _PREWARM_EXECUTOR.submit(_job)
    except Exception:
        pass


def _start_cache_prewarm(backend: ViewerBackend) -> None:
    """Warm expensive scene / mesh caches after the Dash app is ready.

    Structure and display-scope switching feels slow mostly on the first
    visit to a dense unit cell: building the scene, sphere/cylinder Mesh3d
    arrays, and Plotly trace dicts can cost several seconds for PEP.  The
    renderer already has warm-path caches; this background pass simply fills
    them for the structures that were explicitly loaded at startup or via
    upload, without changing the current UI state.
    """

    def _worker():
        # Let the initial server-side figure finish before trickling through
        # heavier display scopes. The prewarm thread is on by default and
        # can be disabled with MATTERVIS_PREWARM=0 for constrained hosts.
        ready = getattr(backend, "_first_figure_ready", None)
        if ready is not None:
            ready.wait(timeout=1.5)
        else:
            time.sleep(1.5)
        for name in list(backend.bundles.keys()):
            _prewarm_bundle_async(backend, name)

    thread = threading.Thread(target=_worker, name="mattervis-cache-prewarm", daemon=True)
    thread.start()

__all__ = [name for name in globals() if not name.startswith("__")]
