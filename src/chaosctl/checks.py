"""Business-level checks that health checks miss.

cart_durability: before the fault, create sentinel carts with known contents; after recovery,
read them back. A datastore that "recovers" but lost every customer's cart is NOT resilient.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .config import Config
from .http import Http, HttpError


def _base(cfg: Config) -> str:
    return cfg.sandbox["base_url"].rstrip("/")


def cart_seed(cfg: Config, http: Http, n: int = 5) -> list[dict[str, Any]]:
    seeds = []
    products = cfg.probes["product_ids"]
    for i in range(n):
        user = f"chaos-sentinel-{uuid.uuid4()}"
        item = {"productId": products[i % len(products)], "quantity": i + 1}
        try:
            status, _, _ = http.request("POST", f"{_base(cfg)}/api/cart",
                                        json.dumps({"userId": user, "item": item}).encode(),
                                        {"Content-Type": "application/json"})
            ok = status < 400 and cart_read(cfg, http, user) == {item["productId"]: item["quantity"]}
        except HttpError:
            ok = False
        seeds.append({"user": user, "item": item, "seeded": ok})
    return seeds


def cart_read(cfg: Config, http: Http, user: str) -> dict[str, int] | None:
    try:
        status, body, _ = http.request("GET", f"{_base(cfg)}/api/cart?sessionId={user}&currencyCode=USD")
    except HttpError:
        return None
    if status >= 400:
        return None
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return None
    return {i.get("productId"): i.get("quantity") for i in data.get("items") or []}


def cart_verify(cfg: Config, http: Http, seeds: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for s in seeds:
        if not s["seeded"]:
            continue
        got = cart_read(cfg, http, s["user"])
        intact = got == {s["item"]["productId"]: s["item"]["quantity"]}
        results.append({"user": s["user"], "expected": s["item"], "got": got, "intact": intact})
    seeded = len(results)
    intact = sum(r["intact"] for r in results)
    return {"check": "cart_durability", "seeded": seeded, "intact": intact, "lost": seeded - intact,
            "lost_pct": round(100 * (seeded - intact) / seeded, 1) if seeded else None, "details": results}
