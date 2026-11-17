import json

import pytest
from fastapi.testclient import TestClient

from ipdr import agent, main, retrieval, store
from ipdr.db import QueryResult


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "data_range", lambda: {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59",
                                                     "last_full_day": "2026-11-09"})
    with TestClient(main.app) as c:
        yield c


def fake_result():
    return QueryResult([{"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}],
                       [["TikTok", 10], ["Facebook", 8]], 0.3, 1000, 0)


def test_meta_lists_providers(client):
    m = client.get("/api/meta").json()
    assert "anthropic" in m["providers"] and "gemini" in m["providers"] and "ollama" in m["providers"]
    assert m["defaults"]["provider"] == "anthropic"


def test_chat_requires_a_key(client):
    r = client.post("/api/chat", json={"question": "top apps", "llm": {"provider": "openai", "model": "m"}})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


def test_server_key_never_used_for_another_provider(monkeypatch):
    monkeypatch.setattr(main.settings.__class__, "server_api_key", property(lambda self: "server-secret"))
    cfg = main.llm_config(main.LLMBody(provider="openai", model="m"), None)
    assert cfg.api_key == ""
    cfg = main.llm_config(main.LLMBody(provider="anthropic", model="claude-opus-5-5"), None)
    assert cfg.api_key == "server-secret"
    cfg = main.llm_config(main.LLMBody(provider="anthropic", model="claude-opus-5-5"), "browser-key")
    assert cfg.api_key == "browser-key"


def test_chat_stream_creates_conversation_and_stores_answer(client, monkeypatch):
    seen = {}

    def fake_answer(question, turns, cfg, rng, step, cancel):
        seen["key"] = cfg.api_key
        seen["turns"] = turns
        step("Testing the query")
        return agent.Answer(plan={"status": "ok", "title": "Top apps", "sql": "SELECT 1", "explanation": "e",
                                  "assumptions": [{"label": "Period", "value": "5 Nov"}],
                                  "chart": {"type": "bar", "x": "app", "y": "users", "series": ""}, "message": ""},
                            sql="SELECT 1", result=fake_result(), steps=["Testing the query"])

    monkeypatch.setattr(main.agent, "answer", fake_answer)
    r = client.post("/api/chat", headers={"X-LLM-Key": "k-123"},
                    json={"question": "top apps in karachi", "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and "step" in types and types[-1] == "message"
    assert seen["key"] == "k-123"
    msg = events[-1]["message"]
    assert msg["kind"] == "result" and msg["data"]["charts"]["options"][0]["type"] == "bar"

    cid = events[0]["id"]
    conv = client.get(f"/api/conversations/{cid}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["title"] == "top apps in karachi"

    client.post("/api/chat", headers={"X-LLM-Key": "k"}, json={"conversation_id": cid, "question": "same for lahore",
                                                               "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    assert [m["role"] for m in seen["turns"]] == ["user", "assistant"]

    mid = msg["id"]
    ex = client.post(f"/api/messages/{mid}/verify", json={}).json()
    assert ex["question"] == "top apps in karachi"
    assert any(e["id"] == ex["id"] for e in retrieval.similar("most used apps in karachi"))
    store.delete_example(ex["id"])

    csv = client.get(f"/api/messages/{mid}/export.csv")
    assert csv.status_code == 200 and "TikTok" in csv.text

    assert client.patch(f"/api/conversations/{cid}", json={"title": "Renamed"}).status_code == 200
    assert any(c["title"] == "Renamed" for c in client.get("/api/conversations?q=Renamed").json())
    assert client.delete(f"/api/conversations/{cid}").status_code == 200
    assert client.get(f"/api/conversations/{cid}").status_code == 404


def test_agent_error_is_stored_as_message(client, monkeypatch):
    def boom(*a, **k):
        raise agent.AgentError("The Anthropic API key was rejected.")

    monkeypatch.setattr(main.agent, "answer", boom)
    r = client.post("/api/chat", headers={"X-LLM-Key": "bad"}, json={"question": "show me the top apps", "llm": {"provider": "anthropic", "model": "x"}})
    last = json.loads(r.text.strip().splitlines()[-1])
    assert last["message"]["kind"] == "error" and "rejected" in last["message"]["content"]


def test_sql_endpoint_blocks_writes_and_keeps_history(client):
    r = client.post("/api/sql", json={"sql": "DROP TABLE wms_dist.ntc_ssl_log"}).json()
    kinds = [m["kind"] for m in r["messages"]]
    assert kinds == ["sql", "error"] and "SELECT" in r["messages"][1]["content"]


def test_sql_endpoint_runs_and_suggests_charts(client, monkeypatch):
    monkeypatch.setattr(main.db, "cached_query", lambda sql, **k: fake_result())
    r = client.post("/api/sql", json={"sql": "SELECT app_label AS app, uniq(subscriber_id) AS users FROM wms_dist.ntc_ssl_log "
                                             "WHERE start_time >= 1793000000 GROUP BY app"}).json()
    res = r["messages"][1]
    assert res["kind"] == "result" and res["data"]["result"]["row_count"] == 2
    assert res["data"]["sql_run"].rstrip().endswith("LIMIT 5000")


def test_dictionary_upload_and_template(client):
    t = client.get("/api/dictionary/template.csv")
    assert t.status_code == 200 and "app_label" in t.text
    r = client.post("/api/dictionary", files={"file": ("guide.csv", b"field,description\ncid,Cell tower id\n", "text/csv")},
                    data={"name": "Guide"})
    assert r.status_code == 200
    d = r.json()
    assert d["name"] == "Guide" and d["report"]["matched"] == 1
    info = client.get("/api/dictionary").json()
    assert any(c["name"] == "cid" and c["source"] == "Guide" for c in info["columns"])
    bad = client.post("/api/dictionary", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
    assert bad.status_code == 400
    client.delete(f"/api/dictionary/{d['id']}")


def test_builtin_examples_seeded(client):
    ex = client.get("/api/examples").json()
    assert sum(1 for e in ex if e["source"] == "built-in") >= 7


def test_interrupted_question_is_marked_and_continue_answers_it(client, monkeypatch):
    conv = store.create_conversation()
    cid = conv["id"]
    store.add_message(cid, "user", "question", "best app for entertainment in this month")
    got = client.get(f"/api/conversations/{cid}").json()
    assert got["messages"][-1]["unanswered"] == "interrupted"

    seen = {}

    def fake_answer(question, turns, cfg, rng, step, cancel):
        seen["question"], seen["turns"] = question, turns
        return agent.Answer(plan={"status": "ok", "title": "Entertainment apps", "sql": "SELECT 1", "explanation": "e",
                                  "assumptions": [], "chart": {"type": "bar", "x": "app", "y": "users", "series": ""},
                                  "message": ""}, sql="SELECT 1", result=fake_result(), steps=[])

    monkeypatch.setattr(main.agent, "answer", fake_answer)
    r = client.post("/api/chat", headers={"X-LLM-Key": "k"}, json={"conversation_id": cid, "question": "continue the above query",
                                                                   "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert seen["question"] == "best app for entertainment in this month"
    assert any(e["type"] == "step" and "earlier question" in e["text"] for e in events)
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m.get("unanswered") for m in msgs] == [None, None, None]
    store.delete_conversation(cid)


def test_small_talk_is_answered_without_the_ai(client, monkeypatch):
    def no_ai(*a, **k):
        raise AssertionError("the AI must not be asked")

    monkeypatch.setattr(main.agent, "answer", no_ai)
    conv = store.create_conversation()
    cid = conv["id"]
    store.add_message(cid, "user", "question", "top apps in karachi")
    store.add_message(cid, "assistant", "result", "Top apps", {"sql": "SELECT 1"})
    r = client.post("/api/chat", headers={"X-LLM-Key": "k"}, json={"conversation_id": cid, "question": "good",
                                                                   "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    last = json.loads(r.text.strip().splitlines()[-1])["message"]
    assert last["kind"] == "reply" and "Glad" in last["content"]
    turns = main._without_small_talk(store.recent_turns(cid, limit=6))
    assert [m["content"] for m in turns] == ["top apps in karachi", "Top apps"]
    store.delete_conversation(cid)


def test_display_instruction_is_answered_without_the_ai(client, monkeypatch):
    def no_ai(*a, **k):
        raise AssertionError("the AI must not be asked")

    monkeypatch.setattr(main.agent, "answer", no_ai)
    cid = store.create_conversation()["id"]
    store.add_message(cid, "user", "question", "top apps in karachi")
    res = store.add_message(cid, "assistant", "result", "Top apps", {"sql": "SELECT 1"})
    r = client.post("/api/chat", headers={"X-LLM-Key": "k"}, json={"conversation_id": cid, "question": "no need to draw chart and conclusion",
                                                                   "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    last = json.loads(r.text.strip().splitlines()[-1])["message"]
    assert last["kind"] == "reply" and last["data"]["target"] == res["id"]
    assert last["data"]["actions"] == ["hide_chart", "hide_conclusion"]
    store.delete_conversation(cid)
