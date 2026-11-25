"""Multi-backend training tracker shared by RL trainers.

The API follows the production pattern used by ROLL / SkyRL / verl / slime:
one ``log`` call fans out to every configured backend, metric names carry an
optional reducer tag (``actor/pg_loss@sum``), and W&B step axes are declared
per namespace so curves line up across rollout and training steps.

Backends are optional and fail-soft: an unavailable SDK never breaks training.
Writes are buffered off the hot path (a single writer thread for TensorBoard),
``finish`` is idempotent and always flushes.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from .metrics import MetricRegistry, get_registry

_LOGGER = logging.getLogger("tp.tracking")
_TAG_RE = re.compile(r"@(mean|sum|min|max|p50|p90|p95|p99)$")
_STEP_AXES = {
    "train": "train/step",
    "rollout": "rollout/step",
    "eval": "eval/step",
    "perf": "rollout/step",
    "multi_turn": "rollout/step",
    "passrate": "rollout/step",
    "system": "system/step",
}
_SUPPORTED = ("tensorboard", "wandb", "stdout", "jsonl", "prometheus")


def parse_key(key: str) -> tuple[str, str | None]:
    """Split ``actor/pg_loss@sum`` into its dashboard key and reducer tag."""

    match = _TAG_RE.search(key)
    if match:
        return key[: match.start()], match.group(1)
    return key, None


class Tracking:
    """Log RL metrics once to several backends with bounded, fail-soft exports."""

    def __init__(
        self,
        project: str,
        experiment: str,
        *,
        backends: Mapping[str, Any] | tuple[str, ...] = ("stdout",),
        config: Mapping[str, Any] | None = None,
        tags: Mapping[str, Any] | None = None,
        registry: MetricRegistry | None = None,
        jsonl_path: str | os.PathLike[str] | None = None,
        flush_secs: float = 10.0,
    ) -> None:
        if isinstance(backends, Mapping):
            options = dict(backends)
        else:
            options = {name: {} for name in backends}
        unknown = set(options) - set(_SUPPORTED)
        if unknown:
            raise ValueError(f"unsupported tracking backends: {sorted(unknown)}")

        self.project = project
        self.experiment = experiment
        self.options = options
        self.registry = registry or get_registry()
        self._finished = False
        self._lock = threading.RLock()
        self._writer: ThreadPoolExecutor | None = None
        self._tb: Any = None
        self._wandb: Any = None
        self._axes_initialized = False
        self._file = None

        if "tensorboard" in options:
            self._init_tensorboard(options["tensorboard"], flush_secs)
        if "wandb" in options:
            self._init_wandb(config, tags, options["wandb"])
        if "jsonl" in options:
            path = options["jsonl"].get("path") or jsonl_path
            if path is None:
                raise ValueError("jsonl backend requires a path")
            self._path = Path(path)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Unbuffered: a crashed job must not lose the last training step.
            self._file = self._path.open("a", encoding="utf-8", buffering=1)
        atexit.register(self.finish)

    # -- backend setup -------------------------------------------------
    def _init_tensorboard(self, options: Mapping[str, Any], flush_secs: float) -> None:
        try:
            from torch.utils.tensorboard import SummaryWriter
        except Exception:  # pragma: no cover - optional dependency
            try:
                from tensorboardX import SummaryWriter
            except Exception:
                _LOGGER.warning("tensorboard_unavailable")
                return
        log_dir = options.get("log_dir") or os.getenv("TP_TENSORBOARD_DIR")
        if not log_dir:
            _LOGGER.warning("tensorboard_missing_log_dir")
            return
        self._tb = SummaryWriter(log_dir=str(log_dir), flush_secs=flush_secs)
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tp-tb")

    def _init_wandb(
        self,
        config: Mapping[str, Any] | None,
        tags: Mapping[str, Any] | None,
        options: Mapping[str, Any],
    ) -> None:
        try:
            import wandb
        except Exception:  # pragma: no cover - optional dependency
            _LOGGER.warning("wandb_unavailable")
            return
        mode = options.get("mode") or os.getenv("TP_WANDB_MODE", "online")
        kwargs: dict[str, Any] = {
            "project": self.project,
            "name": self.experiment,
            "config": dict(config or {}),
            "tags": list(tags or []),
            "reinit": True,
            "settings": {"console": "off"},
        }
        if mode in {"offline", "disabled"}:
            kwargs["mode"] = mode
        try:
            self._wandb = wandb.init(**kwargs)
        except Exception as exc:  # pragma: no cover - network dependent
            _LOGGER.warning("wandb_init_failed", extra={"error": str(exc)})
            self._wandb = None

    # -- public API ----------------------------------------------------
    def log(self, data: Mapping[str, Any], step: int | None = None) -> None:
        """Fan out a step's metrics to every configured backend."""

        if self._finished:
            return
        with self._lock:
            cleaned: dict[str, Any] = {}
            for key, value in data.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                name, _reducer = parse_key(str(key))
                cleaned[name] = value

            if not cleaned:
                return

            if "stdout" in self.options:
                # Machine-parseable line used by launchers and log shippers.
                print(json.dumps({"step": step, "metrics": cleaned}, sort_keys=True))
            if "jsonl" in self.options and self._file is not None:
                self._file.write(
                    json.dumps({"step": step, "data": cleaned}, sort_keys=True) + "\n"
                )
                self._file.flush()
            if "prometheus" in self.options:
                for name, value in cleaned.items():
                    self.registry.gauge(name.replace("/", "."), float(value))

            if self._tb is not None:
                self._submit_tb(cleaned, step)
            if self._wandb is not None:
                self._log_wandb(cleaned, step)

    def summary(self, data: Mapping[str, Any]) -> None:
        """Set run-level summary values (W&B only; no-op elsewhere)."""

        if self._wandb is not None and not self._finished:
            with self._lock:
                try:
                    self._wandb.summary.update(dict(data))
                except Exception as exc:  # pragma: no cover
                    _LOGGER.warning("wandb_summary_failed", extra={"error": str(exc)})

    def _submit_tb(self, data: dict[str, Any], step: int | None) -> None:
        if self._writer is None:
            return
        try:
            self._writer.submit(self._write_tb, dict(data), step)
        except RuntimeError:  # pool already shut down during final flush
            self._write_tb(data, step)

    def _write_tb(self, data: dict[str, Any], step: int | None) -> None:
        try:
            for name, value in data.items():
                self._tb.add_scalar(name, value, global_step=step)
        except Exception as exc:  # pragma: no cover - writer dependent
            _LOGGER.warning("tensorboard_write_failed", extra={"error": str(exc)})

    def _log_wandb(self, data: dict[str, Any], step: int | None) -> None:
        if not self._axes_initialized:
            self._axes_initialized = True
            try:
                for namespace, axis in _STEP_AXES.items():
                    self._wandb.define_metric(f"{namespace}/*", step_metric=axis)
                    self._wandb.define_metric(axis)
            except Exception:  # pragma: no cover - old wandb versions
                pass
        try:
            self._wandb.log(data, step=step, commit=True)
        except Exception as exc:  # pragma: no cover - network dependent
            _LOGGER.warning("wandb_log_failed", extra={"error": str(exc)})

    def finish(self, exit_code: int = 0) -> None:
        """Flush all backends. Safe to call repeatedly and from ``atexit``."""

        if self._finished:
            return
        self._finished = True
        with self._lock:
            if self._writer is not None:
                self._writer.shutdown(wait=True)
                self._writer = None
            if self._tb is not None:
                try:
                    self._tb.flush()
                    self._tb.close()
                except Exception:  # pragma: no cover
                    pass
                self._tb = None
            if self._file is not None:
                try:
                    self._file.flush()
                    self._file.close()
                except Exception:  # pragma: no cover
                    pass
                self._file = None
            if self._wandb is not None:
                try:
                    self._wandb.finish(exit_code=exit_code)
                except Exception:  # pragma: no cover
                    pass
                self._wandb = None

    def __enter__(self) -> Tracking:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.finish(exit_code=0 if exc_info[0] is None else 1)

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown
        try:
            self.finish()
        except Exception:
            pass


__all__ = ["Tracking", "parse_key", "_SUPPORTED"]
