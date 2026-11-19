"""Logging and per-run reasoning traces.

- logs/museiq.log    : human-readable log of every run (steps, guardrail results, errors, latency)
- logs/traces/*.jsonl: one JSON event per line for a single run (plan summaries, tool calls,
                       tool results, guardrail checks, final outcome)
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.config import LOG_DIR, TRACE_DIR

_configured = False


def get_logger() -> logging.Logger:
    global _configured
    logger = logging.getLogger("museiq")
    if not _configured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG_DIR / "museiq.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        _configured = True
    return logger


class Trace:
    """Collects events for one run and writes them to a JSONL file."""

    def __init__(self, request: str, enabled: bool = True, trace_dir: Path = TRACE_DIR, listener=None):
        self.events = []
        self.listener = listener
        self.start = time.perf_counter()
        self.path: Optional[Path] = None
        if enabled:
            trace_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            slug = re.sub(r"[^a-z0-9]+", "-", request.lower())[:40].strip("-") or "request"
            self.path = trace_dir / f"{stamp}-{slug}.jsonl"
        self.log("request", text=request)

    def log(self, event: str, **data: Any) -> None:
        entry = {"t": round(time.perf_counter() - self.start, 3), "event": event, **data}
        self.events.append(entry)
        if self.listener:
            try:
                self.listener(entry)
            except Exception:  # a display problem must never break a run
                pass
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def elapsed(self) -> float:
        return time.perf_counter() - self.start
