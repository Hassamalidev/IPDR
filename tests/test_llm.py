import json
from types import SimpleNamespace as NS

import pytest

from ipdr import llm
from ipdr.llm import LLMConfig, LLMError, Tool, openai_llm

TOOLS = [Tool("run_sql", "Run SQL", {"type": "object", "properties": {"sql": {"type": "string"}},
                                     "required": ["sql"], "additionalProperties": False})]


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_openai_tool_call_round_trip(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout, **kw):
        calls.append({"url": url, "headers": headers, "body": json})
        return FakeResp(200, {
            "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "run_sql", "arguments": '{"sql": "SELECT 1"}'}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 80}},
        })

    monkeypatch.setattr(openai_llm.net, "post", fake_post)
    cfg = LLMConfig(provider="openai", model="m", api_key="sk-x")
    s = llm.open_session(cfg, "system text", TOOLS, "question")
    turn = s.send()
    assert turn.stop == "tool_use" and turn.tool_calls[0].args == {"sql": "SELECT 1"}
    assert turn.usage == {"input": 100, "output": 10, "cache_read": 80}
    assert calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer sk-x"
    body = calls[0]["body"]
    assert body["messages"][0] == {"role": "system", "content": "system text"}
    assert body["tools"][0]["function"]["strict"] is True
    s.add_tool_results([(turn.tool_calls[0], "ok", False)])
    assert s.messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}


def test_other_providers_skip_strict_and_key_when_local(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout, **kw):
        seen.update(url=url, headers=headers, body=json)
        return FakeResp(200, {"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]})

    monkeypatch.setattr(openai_llm.net, "post", fake_post)
    s = llm.open_session(LLMConfig(provider="ollama", model="llama"), "sys", TOOLS, "q")
    t = s.send()
    assert t.stop == "end" and t.text == "hi"
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in seen["headers"]
    assert "strict" not in seen["body"]["tools"][0]["function"]


def test_openai_bad_arguments_flagged(monkeypatch):
    monkeypatch.setattr(openai_llm.net, "post", lambda *a, **k: FakeResp(200, {"choices": [{"finish_reason": "tool_calls", "message": {
        "tool_calls": [{"id": "c", "function": {"name": "run_sql", "arguments": "{not json"}}]}}]}))
    t = llm.open_session(LLMConfig(provider="groq", model="m", api_key="k"), "s", TOOLS, "q").send()
    assert t.tool_calls[0].bad_args


@pytest.mark.parametrize("status,match", [(401, "rejected"), (404, "does not know"), (429, "rate limiting"), (500, "error")])
def test_openai_errors(monkeypatch, status, match):
    monkeypatch.setattr(openai_llm.net, "post", lambda *a, **k: FakeResp(status, {"error": {"message": "detail here"}}))
    with pytest.raises(LLMError, match=match):
        llm.open_session(LLMConfig(provider="openai", model="m", api_key="k"), "s", TOOLS, "q").send()


def test_missing_key_is_reported_before_any_call():
    with pytest.raises(LLMError, match="API key"):
        llm.check_config(LLMConfig(provider="gemini", model="m"))
    with pytest.raises(LLMError, match="model"):
        llm.check_config(LLMConfig(provider="openai", model="", api_key="k"))
    llm.check_config(LLMConfig(provider="ollama", model="m"))


def test_anthropic_session_request_shape(monkeypatch):
    from ipdr.llm import anthropic_llm

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return NS(stop_reason="tool_use", usage=NS(input_tokens=5, output_tokens=2, cache_read_input_tokens=3),
                      content=[NS(type="thinking", thinking=""), NS(type="tool_use", id="tu1", name="run_sql", input={"sql": "SELECT 1"})])

    fake_client = NS(beta=NS(messages=FakeMessages()))
    monkeypatch.setattr(anthropic_llm, "_client", lambda cfg: fake_client)
    s = llm.open_session(LLMConfig(provider="anthropic", model="claude-opus-5-5", api_key="k", effort="high"), "SYS", TOOLS, "Q")
    t = s.send()
    assert t.stop == "tool_use" and t.tool_calls[0].id == "tu1"
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["tools"][0]["strict"] is True and captured["tools"][0]["input_schema"]["required"] == ["sql"]
    assert captured["output_config"] == {"effort": "high"}
    assert captured["fallbacks"] == "default" and captured["betas"] == [anthropic_llm.FALLBACK_BETA]
    assert s.messages[-1]["content"][0].type == "thinking"
    s.add_tool_results([(t.tool_calls[0], "rows", False)])
    assert s.messages[-1]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "rows", "is_error": False}


def test_anthropic_older_model_gets_no_effort_or_fallback(monkeypatch):
    from ipdr.llm import anthropic_llm

    captured = {}
    fake = NS(beta=NS(messages=NS(create=lambda **kw: captured.update(kw) or NS(
        stop_reason="end_turn", usage=NS(input_tokens=1, output_tokens=1), content=[NS(type="text", text="ok")]))))
    monkeypatch.setattr(anthropic_llm, "_client", lambda cfg: fake)
    llm.open_session(LLMConfig(provider="anthropic", model="claude-haiku-4-5", api_key="k"), "S", TOOLS, "Q").send()
    assert "output_config" not in captured and "fallbacks" not in captured


def test_tunnel_error_page_is_explained_not_dumped():
    import requests
    from ipdr.llm.openai_llm import _error
    r = requests.Response()
    r.status_code = 530
    r.url = "https://thorough-rna-selective-strips.trycloudflare.com/v1/chat/completions"
    r._content = b"<!DOCTYPE html><html><head><title>Cloudflare Tunnel error</title></head></html>"
    msg = str(_error(r, "Ollama"))
    assert "<" not in msg and "not reachable" in msg and "Kaggle" in msg and "530" in msg
