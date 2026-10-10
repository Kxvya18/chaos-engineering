"""Minimal stdlib HTTP client (keeps the dependency list to the Anthropic SDK only)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class HttpError(Exception):
    pass


class Http:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    def request(self, method: str, url: str, body: bytes | None = None,
                headers: dict[str, str] | None = None, timeout: float | None = None) -> tuple[int, bytes, float]:
        """Return (status, body, latency_ms). Network errors raise HttpError."""
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                data = resp.read()
                return resp.status, data, (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as exc:
            data = exc.read() if exc.fp else b""
            return exc.code, data, (time.perf_counter() - t0) * 1000
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise HttpError(f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}") from exc

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        status, data, _ = self.request("GET", url)
        if status >= 400:
            raise HttpError(f"GET {url} -> {status}")
        return json.loads(data or b"null")

    def post_form(self, url: str, form: dict[str, Any]) -> Any:
        body = urllib.parse.urlencode(form).encode()
        status, data, _ = self.request("POST", url, body, {"Content-Type": "application/x-www-form-urlencoded"})
        if status >= 400:
            raise HttpError(f"POST {url} -> {status}")
        try:
            return json.loads(data or b"null")
        except json.JSONDecodeError:
            return {"raw": data.decode(errors="replace")[:200]}
