import json

from ipdr import agent, context, llm
from ipdr.llm import LLMConfig, Tool, Turn, openai_llm
from ipdr.llm.openai_llm import text_tool_calls

KNOWN = {"find_values", "run_sql", "submit_answer"}
TOOLS = [Tool(n, n, {"type": "object", "properties": {}, "additionalProperties": False}) for n in sorted(KNOWN)]


def test_fenced_json_tool_call():
    text = '```json\n{\n  "name": "find_values",\n  "arguments": {"column": "client_location", "search": "karachi"}\n}\n```'
    assert text_tool_calls(text, KNOWN) == [("find_values", {"column": "client_location", "search": "karachi"})]


def test_qwen_tool_call_tags_and_several_calls():
    text = ('<tool_call>\n{"name": "find_values", "arguments": {"column": "app_label", "search": "tiktok"}}\n</tool_call>\n'
            '<tool_call>\n{"name": "run_sql", "arguments": "{\\"sql\\": \\"SELECT 1\\"}"}\n</tool_call>')
    assert text_tool_calls(text, KNOWN) == [("find_values", {"column": "app_label", "search": "tiktok"}),
                                            ("run_sql", {"sql": "SELECT 1"})]


def test_bare_json_and_unknown_names_ignored():
    assert text_tool_calls('I will call {"name": "run_sql", "parameters": {"sql": "SELECT 2"}} now.', KNOWN) == [("run_sql", {"sql": "SELECT 2"})]
    assert text_tool_calls('{"name": "rm_rf", "arguments": {}}', KNOWN) == []
    assert text_tool_calls("TikTok is the most used app.", KNOWN) == []


class FakeResp:
    def __init__(self, body=None, lines=None):
        self.status_code = 200
        self._body = body
        self._lines = lines
        self.headers = {"content-type": "text/event-stream" if lines else "application/json"}

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=True):
        yield from self._lines

    def close(self):
        pass


def test_text_tool_call_becomes_real_call_in_history(monkeypatch):
    content = '```json\n{"name": "find_values", "arguments": {"column": "app_label", "search": "imo"}}\n```'
    monkeypatch.setattr(openai_llm.net, "post", lambda *a, **k: FakeResp(
        body={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}))
    s = llm.open_session(LLMConfig(provider="ollama", model="qwen", base_url="http://x/v1"), "sys", TOOLS, "q")
    turn = s.send()
    assert turn.stop == "tool_use" and turn.tool_calls[0].name == "find_values"
    stored = s.messages[-1]
    assert stored["content"] is None and stored["tool_calls"][0]["id"] == turn.tool_calls[0].id
    s.add_tool_results([(turn.tool_calls[0], "values…", False)])
    assert s.messages[-1]["tool_call_id"] == turn.tool_calls[0].id


def test_streamed_tool_call_is_assembled(monkeypatch):
    def sse(obj):
        return "data: " + json.dumps(obj)
    lines = [
        sse({"choices": [{"delta": {"role": "assistant", "content": ""}}]}),
        sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "run_sql", "arguments": "{\"sql\": "}}]}}]}),
        "",
        sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"SELECT 1\"}"}}]}}]}),
        sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        sse({"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 9}}),
        "data: [DONE]",
    ]
    seen = {}

    def fake_post(url, headers, json, timeout, stream):
        seen.update(body=json, stream=stream)
        return FakeResp(lines=lines)

    monkeypatch.setattr(openai_llm.net, "post", fake_post)
    turn = llm.open_session(LLMConfig(provider="ollama", model="m", base_url="http://x/v1"), "s", TOOLS, "q").send()
    assert seen["stream"] is True and seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}
    assert turn.tool_calls[0].args == {"sql": "SELECT 1"} and turn.usage["input"] == 50


def test_event_stream_without_charset_is_read_as_utf8(monkeypatch):
    class BytesResp(FakeResp):
        encoding = "ISO-8859-1"

        def iter_lines(self, decode_unicode=True):
            for line in self._lines:
                yield line.encode("utf-8").decode(self.encoding)

    text = "Top apps in Sādiqābād, لاہور"
    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False),
             "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}), "data: [DONE]"]
    monkeypatch.setattr(openai_llm.net, "post", lambda *a, **k: BytesResp(lines=lines))
    turn = llm.open_session(LLMConfig(provider="ollama", model="m", base_url="http://x/v1"), "s", TOOLS, "q").send()
    assert turn.text == text


def test_date_helpers():
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    text = "\n".join(context.date_helpers(rng))
    assert "last week / last 7 days: 2026-11-03 to 2026-11-09" in text
    assert "toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')" in text
    assert "today / yesterday / last day (all mean the last full day of data): 2026-11-09 to 2026-11-09" in text
    assert "day before yesterday): 2026-11-08 to 2026-11-08" in text
    assert "previous calendar month (as far as the data goes): 2026-10-11 to 2026-10-31" in text
    assert "last weekend (Saturday and Sunday): 2026-11-07 to 2026-11-08" in text
    assert context.date_helpers({}) == []
    assert "Ready-made periods" in context.sql_user_message("q", rng, "2026-11-13", [], [])


def test_review_runs_once_for_non_claude(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE

    s = FakeSession([
        Turn("", [llm.ToolCall("a", "submit_answer", plan())], "tool_use"),
        Turn("", [llm.ToolCall("b", "submit_answer", plan())], "tool_use"),
    ])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    steps = []
    ans = agent.answer("top apps in karachi on 5 nov, compare users and data", [], LLMConfig(provider="ollama", model="m"), RANGE, steps.append)
    assert ans.result is not None
    feedback = s.tool_results[0][0][1]
    assert "Checklist" in feedback and "Understood question: Which apps" in feedback
    assert "Checking the query against the question" in steps


def test_no_review_for_claude_by_default():
    assert agent.review_enabled(LLMConfig(provider="anthropic", model="claude-opus-5-5")) is False
    assert agent.review_enabled(LLMConfig(provider="ollama", model="m")) is True


def test_confirm_answer_returns_the_checked_answer(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE

    s = FakeSession([
        Turn("", [llm.ToolCall("a", "submit_answer", plan())], "tool_use"),
        Turn("", [llm.ToolCall("b", "confirm_answer", {})], "tool_use"),
    ])
    fake_db = FakeDB()
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", fake_db)
    steps = []
    ans = agent.answer("top apps in karachi on 5 nov, compare users and data", [], LLMConfig(provider="ollama", model="m"), RANGE, steps.append)
    assert ans.result is not None and ans.plan["title"] == "Top apps, Karachi, 5 Nov"
    assert len(fake_db.queries) == 1
    assert "Answer confirmed" in steps


def test_confirm_without_a_submitted_answer_is_refused(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE

    s = FakeSession([
        Turn("", [llm.ToolCall("x", "confirm_answer", {})], "tool_use"),
        Turn("", [llm.ToolCall("a", "submit_answer", plan())], "tool_use"),
        Turn("", [llm.ToolCall("b", "confirm_answer", {})], "tool_use"),
    ])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    ans = agent.answer("top apps in karachi on 5 nov", [], LLMConfig(provider="ollama", model="m"), RANGE, lambda t: None)
    assert ans.result is not None
    assert "Nothing to confirm" in s.tool_results[0][0][1]


def test_simple_question_skips_the_self_check(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE
    s = FakeSession([Turn("", [llm.ToolCall("a", "submit_answer", plan())], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    steps = []
    ans = agent.answer("top apps in karachi on 5 nov", [], LLMConfig(provider="ollama", model="m"), RANGE, steps.append)
    assert ans.result is not None and "Checking the query against the question" not in steps


def test_names_and_period_are_resolved_in_code():
    from tests.test_agent_loop import RANGE
    got = agent.resolved_names("gandi videos kon zyada dekhta hai lahore me last week", RANGE)
    text = " ".join(got)
    assert "client_location = 'Lahore Pakistan'" in text and "'Xhamster'" in text and "requested_server" in text
    assert "2026-11-03 00:00:00" in text and "2026-11-10 00:00:00" in text
    assert any(r.startswith("snapchat (app") for r in agent.resolved_names("snapchat users", RANGE))


import pytest


@pytest.mark.parametrize("q,expected", [
    ("top 5 apps by data in lahore yesterday", False), ("aur lahore me?", False), ("users and data for tiktok", False),
    ("whatsapp vs imo", True), ("karachi and lahore tiktok users", True), ("share of tiktok in karachi", True),
    ("snapchat or instagram which is bigger", True), ("growth of tiktok vs last week", True),
])
def test_which_questions_get_a_self_check(q, expected):
    assert agent.needs_review(q) is expected


def test_a_self_check_answered_in_text_counts_as_confirmed(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE
    s = FakeSession([Turn("", [llm.ToolCall("a", "submit_answer", plan())], "tool_use"),
                     Turn("Understood question: ... Checklist: 1. yes 2. yes", [], "end")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    steps = []
    ans = agent.answer("top apps in karachi on 5 nov, compare users and data", [], LLMConfig(provider="ollama", model="m"), RANGE, steps.append)
    assert ans.result is not None and "Answer confirmed" in steps and s.user_notes == []


def test_a_trend_alone_gets_no_self_check():
    assert agent.needs_review("trend of netflix data daily this month") is False
