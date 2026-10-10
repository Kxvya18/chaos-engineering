"""Black-box customer-journey probes through frontend-proxy (our primary SLI source)."""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from typing import Any, Callable

from .config import Config
from .http import Http, HttpError
from .util import JsonlWriter, now

# Demo checkout identity (same shape as the demo's load-generator people.json; fake data).
CHECKOUT_PERSON = {
    "email": "chaos-probe@example.com",
    "address": {"streetAddress": "1 Probe Street", "zipCode": "94043", "city": "Mountain View",
                "state": "CA", "country": "United States"},
    "userCurrency": "USD",
    "creditCard": {"creditCardNumber": "4432-8015-6152-0454", "creditCardExpirationMonth": 1,
                   "creditCardExpirationYear": 2039, "creditCardCvv": 672},
}

StepResult = tuple[bool, int | None, str | None]
JourneyFn = Callable[[], StepResult]


class Journeys:
    def __init__(self, cfg: Config, http: Http, rng: random.Random | None = None):
        self.base = cfg.sandbox["base_url"].rstrip("/")
        self.cur = cfg.probes["currency"]
        self.products = cfg.probes["product_ids"]
        self.ads = cfg.probes["ad_categories"]
        self.http = http
        self.rng = rng or random.Random()

    def _do(self, method: str, path: str, body: Any = None) -> StepResult:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            status, _, _ = self.http.request(method, self.base + path, data, headers)
        except HttpError as exc:
            return False, None, str(exc)
        return (200 <= status < 400), status, None if status < 400 else f"HTTP {status}"

    def _chain(self, *steps: Callable[[], StepResult]) -> StepResult:
        last: StepResult = (True, None, None)
        for step in steps:
            last = step()
            if not last[0]:
                return last
        return last

    def home(self) -> StepResult:
        return self._do("GET", "/")

    def browse(self) -> StepResult:
        return self._do("GET", f"/api/products?currencyCode={self.cur}")

    def product(self) -> StepResult:
        return self._do("GET", f"/api/products/{self.rng.choice(self.products)}?currencyCode={self.cur}")

    def recommendations(self) -> StepResult:
        return self._do("GET", f"/api/recommendations?productIds={self.rng.choice(self.products)}"
                               f"&currencyCode={self.cur}")

    def ads_(self) -> StepResult:
        return self._do("GET", f"/api/data?contextKeys={self.rng.choice(self.ads)}")

    def cart(self) -> StepResult:
        user = str(uuid.uuid4())
        item = {"userId": user, "item": {"productId": self.rng.choice(self.products), "quantity": 1}}
        return self._chain(lambda: self._do("POST", "/api/cart", item),
                           lambda: self._do("GET", f"/api/cart?sessionId={user}&currencyCode={self.cur}"))

    def checkout(self) -> StepResult:
        user = str(uuid.uuid4())
        item = {"userId": user, "item": {"productId": self.rng.choice(self.products), "quantity": 1}}
        order = {**CHECKOUT_PERSON, "userId": user}
        return self._chain(lambda: self._do("POST", "/api/cart", item),
                           lambda: self._do("POST", f"/api/checkout?currencyCode={self.cur}", order))

    def all(self) -> dict[str, JourneyFn]:
        return {"home": self.home, "browse": self.browse, "product": self.product,
                "recommendations": self.recommendations, "ads": self.ads_, "cart": self.cart,
                "checkout": self.checkout}


class ProbeRunner:
    """One thread per journey; each sample is appended to metrics.jsonl immediately."""

    def __init__(self, journeys: dict[str, JourneyFn], writer: JsonlWriter, interval_s: float,
                 clock: Callable[[], float] = now):
        self.journeys = journeys
        self.writer = writer
        self.interval = interval_s
        self.clock = clock
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.count = 0
        self._lock = threading.Lock()

    def probe_once(self, name: str, fn: JourneyFn) -> dict[str, Any]:
        ts = self.clock()
        t0 = time.perf_counter()
        try:
            ok, status, err = fn()
        except Exception as exc:  # a probe bug must surface as a failed sample, not kill the thread
            ok, status, err = False, None, f"probe exception: {exc}"
        rec = {"ts": ts, "journey": name, "ok": ok, "status": status,
               "latency_ms": round((time.perf_counter() - t0) * 1000, 2), "error": err}
        self.writer.write(rec)
        with self._lock:
            self.count += 1
        return rec

    def _loop(self, name: str, fn: JourneyFn) -> None:
        # stagger start so journeys don't fire in lock-step
        self._stop.wait(random.uniform(0, self.interval))
        while not self._stop.is_set():
            started = time.monotonic()
            self.probe_once(name, fn)
            self._stop.wait(max(0.0, self.interval - (time.monotonic() - started)))

    def start(self) -> None:
        for name, fn in self.journeys.items():
            t = threading.Thread(target=self._loop, args=(name, fn), name=f"probe-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=10)
