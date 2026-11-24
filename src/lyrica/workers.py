"""One running job, one replacement and one completed result per kind."""
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerResult:
    """An answer for the render thread, stamped with its track and raster size."""

    gen: int
    kind: str
    value: object = None
    shape_gen: int | None = None


Job = Callable[[threading.Event], WorkerResult | None]


class TrackWorkers:
    def __init__(self):
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._latest: dict[str, threading.Event] = {}
        self._pending: dict[str, Job] = {}
        self._ready: dict[str, WorkerResult] = {}
        self._closed = False

    def submit(self, kind: str, job: Job) -> None:
        with self._lock:
            if self._closed:
                return
            if kind in self._latest:
                self._latest[kind].set()
            self._latest[kind] = threading.Event()
            self._pending[kind] = job
            self._ready.pop(kind, None)
            if kind in self._active:
                return
            self._active.add(kind)
            threading.Thread(target=self._run, args=(kind,), daemon=True,
                             name=f"lyrica-{kind}").start()

    def _run(self, kind: str) -> None:
        while True:
            with self._lock:
                job = self._pending.pop(kind, None)
                if job is None:
                    self._active.remove(kind)
                    return
                cancelled = self._latest[kind]
            try:
                result = job(cancelled) if not cancelled.is_set() else None
            except Exception:
                logger.exception("%s worker failed", kind)
                result = None
            with self._lock:
                if not cancelled.is_set():
                    self._latest.pop(kind)
                    if result is not None:
                        self._ready[kind] = result
            del job, result  # release cover bytes before another blocking call

    def drain(self) -> list[WorkerResult]:
        with self._lock:
            results = list(self._ready.values())
            self._ready.clear()
            return results

    def _discard(self) -> None:
        for cancelled in self._latest.values():
            cancelled.set()
        self._latest.clear()
        self._pending.clear()
        self._ready.clear()

    def new_track(self) -> None:
        with self._lock:
            self._discard()

    def close(self) -> None:
        """Drop pending and completed jobs. In-flight provider calls finish as daemons."""
        with self._lock:
            self._closed = True
            self._discard()
