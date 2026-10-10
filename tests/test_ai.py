import json
import types

import pytest

from chaosctl import ai


class FakeMessages:
    def __init__(self, text, stop="end_turn"):
        self.text, self.stop, self.kwargs = text, stop, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = types.SimpleNamespace(type="text", text=self.text)
        usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)
        return types.SimpleNamespace(content=[block], stop_reason=self.stop, model=kwargs["model"], usage=usage, id="m1")


def _patch(monkeypatch, msgs):
    anthropic = pytest.importorskip("anthropic", reason="run tests with the venv's python: python -m pytest")

    client = types.SimpleNamespace(beta=types.SimpleNamespace(messages=msgs), messages=msgs)
    monkeypatch.setattr(anthropic, "Anthropic", lambda: client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("CHAOS_AI_DISABLED", raising=False)


def test_request_shape_structured_output_and_fallbacks(monkeypatch):
    msgs = FakeMessages(json.dumps({"ok": True}))
    _patch(monkeypatch, msgs)
    data, meta = ai.structured_call("sys", {"x": 1}, {"type": "object"}, effort="high")
    assert data == {"ok": True}
    k = msgs.kwargs
    assert k["model"] == "claude-opus-5-5"
    assert k["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in k and "temperature" not in k


def test_refusal_and_bad_json_raise_unavailable(monkeypatch):
    _patch(monkeypatch, FakeMessages("{}", stop="refusal"))
    with pytest.raises(ai.AIUnavailable):
        ai.structured_call("s", {}, {})
    _patch(monkeypatch, FakeMessages("not json"))
    with pytest.raises(ai.AIUnavailable):
        ai.structured_call("s", {}, {})


def test_no_key_means_unavailable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    ok, why = ai.available()
    assert not ok and "ANTHROPIC_API_KEY" in why
