"""Isolated MatterVis processes for real-app browser tests.

Every mutating browser test gets its own ``ViewerBackend``, temporary scene
store, and HTTP listener. A fresh browser context is not server-state
isolation: tabs, cameras, uploads, and render workers live in the process.

The listener binds port 0. Looking up a free port and binding it later is a
TOCTOU window where another test can take the same port.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass

from werkzeug.serving import make_server

from mat_viewer.app.factory import create_app

# The HTTP thread is joined before the backend workers. Five seconds is enough
# for ``serve_forever`` to leave ``shutdown``; it is not a readiness sleep.
_HTTP_THREAD_JOIN_SECONDS = 5.0


@dataclass
class RunningMatterVis:
    """One function-scoped app, bound to an OS-assigned loopback port."""

    url: str
    port: int
    app: object
    server: object
    thread: threading.Thread

    def stop(self) -> None:
        """Stop the listener, join it, and close workers. Raise on leaks."""
        errors: list[str] = []
        try:
            self.server.shutdown()
        except Exception as exc:  # noqa: BLE001 - record any shutdown failure
            errors.append(f"server shutdown: {exc}")
        try:
            self.server.server_close()
        except Exception as exc:  # noqa: BLE001 - record any socket close failure
            errors.append(f"server close: {exc}")
        self.thread.join(timeout=_HTTP_THREAD_JOIN_SECONDS)
        if self.thread.is_alive():
            errors.append("http thread still alive after shutdown")
        backend = getattr(self.app, "crystal_backend", None)
        if backend is not None:
            try:
                backend.close()
            except Exception as exc:  # noqa: BLE001 - record any backend close failure
                errors.append(f"backend close: {exc}")
        if _port_still_bound(self.port):
            errors.append(f"port {self.port} still bound")
        if errors:
            raise RuntimeError("browser app teardown leak: " + "; ".join(errors))


def _port_still_bound(port: int) -> bool:
    """Return whether something still accepts connections on ``port``.

    A bind probe false-fails after a clean close: the kernel can keep the
    address reserved while no listener remains. A refused connection means
    the test server is gone.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _render_status(app):
    """Read-only snapshot the browser failure artifact can attach."""
    backend = app.crystal_backend
    worker = backend._render_worker
    with worker._lock:
        pending = {
            "render": len(worker._pending_render),
            "topology": len(worker._pending),
        }
    return {
        "pending": pending,
        "pending_state": backend.peek_pending_state() is not None,
        "cursor": backend.figure_journal_seq(),
        "active_id": backend.active_scene_id(),
        "tasks": worker.task_status(),
        "threads": sorted(thread.name for thread in threading.enumerate()),
    }


def bind_failed(exc: BaseException) -> bool:
    """Werkzeug turns ``EADDRINUSE`` into ``SystemExit(1)``."""
    if isinstance(exc, OSError):
        return True
    return isinstance(exc, SystemExit) and exc.code == 1


def start_mattervis(
    root_dir: str,
    cif_paths: list[str],
    *,
    port: int = 0,
) -> RunningMatterVis:
    """Create an app and serve it.

    ``port`` 0 asks the OS for an ephemeral port. A nonzero port is only for
    the restart regression, and only after the previous listener has stopped.
    A failed bind closes the app so the retry does not leak workers.
    """
    app = create_app(root_dir=root_dir, cif_paths=cif_paths)
    try:
        @app.server.get("/_test/render-status")
        def render_status():
            return _render_status(app)

        server = make_server("127.0.0.1", port, app.server, threaded=True)
    except BaseException:
        backend = getattr(app, "crystal_backend", None)
        if backend is not None:
            backend.close()
        raise
    thread = threading.Thread(
        target=server.serve_forever,
        name=f"mattervis-test-http-{server.server_port}",
        daemon=False,
    )
    thread.start()
    port = int(server.server_port)
    return RunningMatterVis(
        url=f"http://127.0.0.1:{port}",
        port=port,
        app=app,
        server=server,
        thread=thread,
    )
