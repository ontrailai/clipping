from types import SimpleNamespace

import pytest

from clipfactory.analyst.judge import SCHEMA as JUDGE_SCHEMA
from clipfactory.analyst.judge import refine_bounds
from clipfactory.llm import Claude, LLMError, LLMRefusal, strict_schema


def test_strict_schema_adds_required_and_closes_objects():
    s = strict_schema(JUDGE_SCHEMA)
    assert s["additionalProperties"] is False
    item = s["properties"]["moments"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _client(response):
    messages = FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def _response(stop_reason="end_turn", text='{"ok": true}', stop_details=None):
    return SimpleNamespace(
        stop_reason=stop_reason, stop_details=stop_details, model="claude-opus-5-5",
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def test_json_call_shape_and_parse():
    client, messages = _client(_response())
    out = Claude(client=client).json("sys", "hello", {"type": "object", "properties": {"ok": {"type": "boolean"}}}, effort="low")
    assert out == {"ok": True}
    kw = messages.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kw and "temperature" not in kw


def test_refusal_and_truncation_raise():
    client, _ = _client(_response("refusal", "", SimpleNamespace(category="cyber")))
    with pytest.raises(LLMRefusal):
        Claude(client=client).json("s", "u", {"type": "object", "properties": {}})
    client, _ = _client(_response("max_tokens"))
    with pytest.raises(LLMError):
        Claude(client=client).json("s", "u", {"type": "object", "properties": {}})


def test_refine_bounds():
    words = [{"w": "hello", "s": 4.8, "e": 5.4}, {"w": "there", "s": 19.7, "e": 20.6}]
    # starts mid-word -> snapped back; ends mid-word -> extended past the word
    s, e = refine_bounds(5.0, 20.0, words, 60, 15, 58)
    assert s < 4.8 and e > 20.6
    # too long -> keep the payoff end
    s, e = refine_bounds(0, 70, [], 80, 15, 58)
    assert (s, e) == (12.0, 70.0)
    # too short -> grow backwards
    s, e = refine_bounds(30, 35, [], 80, 15, 58)
    assert (s, e) == (20.0, 35.0)
