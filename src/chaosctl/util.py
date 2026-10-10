"""Small shared helpers: time, JSON/JSONL I/O, hashing, event log."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now() -> float:
    return time.time()


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else now(), tz=timezone.utc).isoformat(timespec="seconds")


def stamp(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else now(), tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False, default=str) + "\n")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a partially written last line
    return out


class JsonlWriter:
    """Thread-safe append-only JSONL writer, flushed per record."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._fh = path.open("a", buffering=1)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


class JsonlTail:
    """Incrementally read new complete lines from a JSONL file."""

    def __init__(self, path: Path):
        self.path = path
        self._offset = 0
        self._buf = ""

    def read_new(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
            self._offset = fh.tell()
        self._buf += chunk
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


class EventLog:
    """events.jsonl for one run: every safety-relevant action is recorded."""

    def __init__(self, path: Path, source: str):
        self._w = JsonlWriter(path)
        self.source = source

    def emit(self, action: str, **fields: Any) -> dict[str, Any]:
        rec = {"ts": now(), "time": iso(), "source": self.source, "action": action, **fields}
        self._w.write(rec)
        return rec

    def close(self) -> None:
        self._w.close()
