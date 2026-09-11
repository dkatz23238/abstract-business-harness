"""Detached agent runs: execution decoupled from the HTTP request.

With `AGUIAdapter.dispatch_request`, the agent run is owned by the POST
request — any client disconnect (page reload, network blip) or server
restart of the response cancels the whole run and every in-flight tool
call with it (observed live: runs died mid-analysis and had to be
re-prompted).

Here a run is owned by a background task keyed by thread id instead. The
POST /agui response merely *attaches* to the run's buffered event stream:
if the browser goes away the run keeps executing, the server persists the
final conversation itself, and the UI can re-discover the run via its
status and re-attach to the outcome.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable


# Set when the server process is asked to stop (server.py chains it onto
# uvicorn's SIGINT/SIGTERM handler). Every long-lived SSE stream waits on it
# alongside its queue so open browser tabs end their responses at once.
# Without this, uvicorn's graceful shutdown waits for every in-flight
# response to finish — and an EventSource never finishes on its own, so
# Ctrl+C hung until the last tab was closed.
shutdown = asyncio.Event()


async def next_or_shutdown(queue: asyncio.Queue, timeout: float | None = None):
    """`await queue.get()` that also returns when the server is stopping.

    Returns the next item, or None once `shutdown` is set. Raises
    TimeoutError if `timeout` seconds pass with neither (keep-alive pings).
    """
    getter = asyncio.ensure_future(queue.get())
    stopper = asyncio.ensure_future(shutdown.wait())
    try:
        done, _ = await asyncio.wait(
            {getter, stopper}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if getter in done:
            return getter.result()
        if stopper in done:
            return None
        raise TimeoutError
    finally:
        for task in (getter, stopper):
            if not task.done():
                task.cancel()


class RunActiveError(RuntimeError):
    """A run is already executing for this thread."""


class ThreadRun:
    """One detached agent run: a buffer of encoded SSE chunks + live fan-out."""

    def __init__(self, thread_id: str, run_id: str | None):
        self.thread_id = thread_id
        self.run_id = run_id
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.error: str | None = None
        self._chunks: list[str] = []
        self._subscribers: set[asyncio.Queue[str | None]] = set()
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self.finished_at is None

    def append(self, chunk: str) -> None:
        self._chunks.append(chunk)
        for queue in self._subscribers:
            queue.put_nowait(chunk)

    def finish(self, error: str | None = None) -> None:
        if self.finished_at is not None:
            return
        self.finished_at = time.time()
        self.error = error
        for queue in self._subscribers:
            queue.put_nowait(None)  # end-of-stream sentinel

    async def attach(self) -> AsyncIterator[str]:
        """Replay everything so far, then follow live until the run ends.

        Safe to call any number of times, concurrently, before or after the
        run finishes. Cancelling the iterator (client disconnect) only drops
        this subscriber — the run itself is untouched. Ends early (without
        touching the run) when the server is shutting down.
        """
        if not self.active:
            for chunk in self._chunks:
                yield chunk
            return
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        # Subscribe before snapshotting: chunks appended while we replay go
        # to the queue, so the sequence stays gapless and duplicate-free.
        self._subscribers.add(queue)
        try:
            for chunk in list(self._chunks):
                yield chunk
            while True:
                chunk = await next_or_shutdown(queue)
                if chunk is None:  # end-of-stream sentinel or server shutdown
                    return
                yield chunk
        finally:
            self._subscribers.discard(queue)

    def status(self) -> dict:
        return {
            "active": self.active,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class RunManager:
    """Latest run per thread. One active run per thread at a time."""

    def __init__(self) -> None:
        self._runs: dict[str, ThreadRun] = {}

    def get(self, thread_id: str) -> ThreadRun | None:
        return self._runs.get(thread_id)

    def start(
        self,
        thread_id: str,
        run_id: str | None,
        executor: Callable[[ThreadRun], Awaitable[None]],
    ) -> ThreadRun:
        """Begin a detached run; `executor` must call `run.finish()` when done."""
        existing = self._runs.get(thread_id)
        if existing is not None and existing.active:
            raise RunActiveError(f"a run is already active for thread {thread_id!r}")
        run = ThreadRun(thread_id, run_id)
        run._task = asyncio.create_task(executor(run))
        self._runs[thread_id] = run
        return run
