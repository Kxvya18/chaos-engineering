"""Runtime AI boundary (Claude API). Used ONLY for experiment selection and findings analysis.

Every call returns schema-constrained JSON that the caller validates again; any failure returns
None and the caller falls back to deterministic logic. Nothing here touches faults or safety.
"""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_MODEL = "claude-opus-5-5"


class AIUnavailable(Exception):
    pass


def model_name() -> str:
    return os.environ.get("CHAOS_AI_MODEL", DEFAULT_MODEL)


def available() -> tuple[bool, str]:
    if os.environ.get("CHAOS_AI_DISABLED") == "1":
        return False, "disabled by CHAOS_AI_DISABLED=1"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic SDK not installed"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False, "no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in environment"
    return True, "ok"


def structured_call(system: str, payload: dict[str, Any], schema: dict[str, Any],
                    effort: str = "medium", max_tokens: int = 16000) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (parsed_json, meta). Raises AIUnavailable on any failure (caller falls back)."""
    ok, why = available()
    if not ok:
        raise AIUnavailable(why)
    import anthropic

    client = anthropic.Anthropic()
    model = model_name()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    }
    try:
        if os.environ.get("CHAOS_AI_FALLBACKS", "1") == "1":
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                               **kwargs)
        else:
            resp = client.messages.create(**kwargs)
    except anthropic.APIStatusError as exc:
        raise AIUnavailable(f"API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailable(f"connection error: {exc}") from exc
    except TypeError as exc:  # SDK too old for a parameter
        raise AIUnavailable(f"SDK does not support request shape: {exc}") from exc
    if resp.stop_reason == "refusal":
        raise AIUnavailable("model refused the request")
    if resp.stop_reason == "max_tokens":
        raise AIUnavailable("response truncated (max_tokens)")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
    if not text:
        raise AIUnavailable("no text block in response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIUnavailable(f"invalid JSON from model: {exc}") from exc
    usage = getattr(resp, "usage", None)
    meta = {"model_requested": model, "model_served": getattr(resp, "model", model),
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None), "id": getattr(resp, "id", None)}
    return data, meta
