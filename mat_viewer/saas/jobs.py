"""Durable job state plus a local worker implementation.

The queue boundary is deliberately small.  A Redis/RQ or Celery adapter can
replace ``LocalJobRunner`` without changing API or repository contracts.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from queue import Empty, Queue
import threading
import traceback
from typing import Callable, Iterator

from .repository import SaaSRepository


JobCallable = Callable[[str], None]


class LocalEventBus:
    """Small in-process pub/sub used by local mode and WebSocket tests."""

    def __init__(self):
        self._queues: dict[str, set[Queue]] = {}
        self._lock = threading.RLock()

    def publish(self, project_id: str, event: dict) -> None:
        with self._lock:
            for queue in list(self._queues.get(project_id, set())):
                queue.put(dict(event))

    @contextmanager
    def subscribe(self, project_id: str) -> Iterator[Queue]:
        queue: Queue = Queue()
        with self._lock:
            self._queues.setdefault(project_id, set()).add(queue)
        try:
            yield queue
        finally:
            with self._lock:
                self._queues.get(project_id, set()).discard(queue)

    @staticmethod
    def receive(queue: Queue, timeout: float = 0.5) -> dict | None:
        try:
            return queue.get(timeout=timeout)
        except Empty:
            return None


class LocalJobRunner:
    def __init__(self, repository: SaaSRepository, max_workers: int = 4,
                 event_callback: Callable[[str, dict], None] | None = None):
        self.repository = repository
        self.event_callback = event_callback
        self.executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="mattervis-saas-job")
        self._futures: dict[str, Future] = {}
        self._lock = threading.RLock()

    def submit(self, job_id: str, work: JobCallable) -> None:
        def run() -> None:
            running = self.repository.update_job(job_id, status="running", progress=0.01)
            if self.event_callback:
                self.event_callback(running["project_id"], {"type": "job.updated", "job": running})
            try:
                work(job_id)
                succeeded = self.repository.update_job(job_id, status="succeeded", progress=1.0)
                if self.event_callback:
                    self.event_callback(succeeded["project_id"], {"type": "job.updated", "job": succeeded})
            except Exception as exc:
                failed = self.repository.update_job(
                    job_id,
                    status="failed",
                    progress=1.0,
                    error={"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(limit=4)},
                )
                if self.event_callback:
                    self.event_callback(failed["project_id"], {"type": "job.updated", "job": failed})

        future = self.executor.submit(run)
        with self._lock:
            self._futures[job_id] = future

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=True)


class RedisJobRunner:
    """Redis queue adapter used by the Compose worker process."""

    queue_name = "mattervis:jobs"

    def __init__(self, repository: SaaSRepository, redis_url: str,
                 event_callback: Callable[[str, dict], None] | None = None):
        import redis

        self.repository = repository
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.event_callback = event_callback

    def submit(self, job_id: str, work: JobCallable | None = None) -> None:
        self.client.lpush(self.queue_name, job_id)

    def run_worker(self, dispatcher: JobCallable, *, timeout: int = 5) -> None:
        while True:
            item = self.client.brpop(self.queue_name, timeout=timeout)
            if item is None:
                continue
            job_id = str(item[1])
            running = self.repository.update_job(job_id, status="running", progress=0.01)
            if self.event_callback:
                self.event_callback(running["project_id"], {"type": "job.updated", "job": running})
            try:
                dispatcher(job_id)
                succeeded = self.repository.update_job(job_id, status="succeeded", progress=1.0)
                if self.event_callback:
                    self.event_callback(succeeded["project_id"], {"type": "job.updated", "job": succeeded})
            except Exception as exc:
                failed = self.repository.update_job(
                    job_id, status="failed", progress=1.0,
                    error={"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(limit=4)},
                )
                if self.event_callback:
                    self.event_callback(failed["project_id"], {"type": "job.updated", "job": failed})

    def shutdown(self, wait: bool = True) -> None:
        self.client.close()
