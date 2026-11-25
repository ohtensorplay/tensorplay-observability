"""Batched Better Stack log sink (stdlib only, fail-soft).

Better Stack ingests JSON lines over HTTP at ``https://in.logs.betterstack.com``
with a source token. This handler reuses the shared ``JsonLogFormatter``
payload, so logs shipped to Better Stack carry the same ``event`` / ``level`` /
``trace_id`` fields as stderr and Loki. It is opt-in: nothing is started until
a token is supplied.

For metrics and traces, prefer the standard OTLP path (``configure_telemetry``
with Better Stack's OTLP endpoint); no vendor-specific code is needed there.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import threading
import time
import urllib.request
from typing import Any

from .jsonlog import JsonLogFormatter

_DEFAULT_ENDPOINT = "https://in.logs.betterstack.com"


class BetterStackHandler(logging.Handler):
    """Batch JSON log records and POST them to Better Stack.

    Bounded queue, bounded retries on the flush thread, and a drop counter in
    the record stream: a slow or down log backend must never stall training.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        endpoint: str | None = None,
        batch_size: int = 100,
        flush_interval: float = 5.0,
        timeout: float = 3.0,
        max_retries: int = 2,
        queue_size: int = 10000,
    ) -> None:
        super().__init__()
        self.token = token or os.getenv("BETTER_STACK_SOURCE_TOKEN", "")
        self.endpoint = endpoint or os.getenv("TP_BETTERSTACK_ENDPOINT", _DEFAULT_ENDPOINT)
        self._batch_size = batch_size
        self._interval = flush_interval
        self._timeout = timeout
        self._retries = max_retries
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=queue_size)
        self._formatter = JsonLogFormatter()
        self._dropped = 0
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="tp-betterstack", daemon=True
        )
        self._thread.start()
        atexit.register(self.close)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.token:
            return
        try:
            payload = self._formatter.format(record)
        except Exception:  # pragma: no cover - formatter dependent
            self.handleError(record)
            return
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def _run(self) -> None:
        batch: list[str] = []
        deadline = time.monotonic() + self._interval
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                item = self._queue.get(timeout=remaining)
            except queue.Empty:
                item = None
            if item is None:
                if batch:
                    self._send(batch)
                    batch = []
                deadline = time.monotonic() + self._interval
                if self._closed and self._queue.empty():
                    return
                continue
            batch.append(item)
            if len(batch) >= self._batch_size:
                self._send(batch)
                batch = []
                deadline = time.monotonic() + self._interval

    def _send(self, batch: list[str]) -> None:
        body = "\n".join(batch).encode("utf-8")
        for attempt in range(self._retries + 1):
            try:
                request = urllib.request.Request(
                    self.endpoint,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/jsonl",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self._timeout):
                    return
            except Exception:  # pragma: no cover - network dependent
                if attempt == self._retries:
                    with self._lock:
                        self._dropped += len(batch)
                    return
                time.sleep(min(0.5 * (2**attempt), 2.0))

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout=10)
        super().close()


def configure_betterstack(logger: logging.Logger, **kwargs: Any) -> BetterStackHandler | None:
    """Attach a Better Stack handler when a source token is available."""

    token = kwargs.get("token") or os.getenv("BETTER_STACK_SOURCE_TOKEN")
    if not token:
        return None
    handler = BetterStackHandler(token, **{k: v for k, v in kwargs.items() if k != "token"})
    logger.addHandler(handler)
    return handler


__all__ = ["BetterStackHandler", "configure_betterstack"]
