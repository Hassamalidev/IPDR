import json

import pytest
from fastapi.testclient import TestClient

from ipdr import agent, context, dictionary, main, store


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "data_range", lambda: {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59",
                                                     "last_full_day": "2026-11-09"})
    with TestClient(main.app) as c:
        yield c


def test_starter_terms_seeded_once():
    terms = {t["term"] for t in store.list_terms()}
    assert "Zong" in terms and "last week" in terms
    n = len(store.list_terms())
    store.init()
    assert len(store.list_terms()) == n


def test_terms_api_and_prompt(client):
    t = client.post("/api/terms", json={"term": "heavy users", "meaning": "MARKER: more than 1 GB a day"}).json()
    try:
        assert "heavy users: MARKER: more than 1 GB a day" in dictionary.render_for_prompt()
        assert client.patch(f"/api/terms/{t['id']}", json={"term": "heavy users", "meaning": "MARKER2"}).status_code == 200
        assert "heavy users: MARKER2" in dictionary.render_for_prompt()
        assert client.post("/api/terms", json={"term": "", "meaning": "x"}).status_code == 422
    finally:
        client.delete(f"/api/terms/{t['id']}")
    assert "MARKER2" not in dictionary.render_for_prompt()


def test_understood_is_required_and_kept(client, monkeypatch):
    assert "understood" in context.PLAN_SCHEMA["required"]

    def fake_answer(question, turns, cfg, rng, step, cancel):
        return agent.Answer(plan={"understood": "Which apps had the most identified users in Karachi from 2 to 8 Nov 2026?",
                                  "status": "clarify", "title": "Which Karachi?", "sql": "", "explanation": "",
                                  "assumptions": [], "chart": {"type": "", "x": "", "y": "", "series": ""},
                                  "message": "Do you mean Karachi city or Karachi division?"})

    monkeypatch.setattr(main.agent, "answer", fake_answer)
    r = client.post("/api/chat", headers={"X-LLM-Key": "k"},
                    json={"question": "khi me sb se zyada kon si app", "llm": {"provider": "anthropic", "model": "claude-opus-5-5"}})
    msg = json.loads(r.text.strip().splitlines()[-1])["message"]
    assert msg["data"]["understood"].startswith("Which apps had the most")
    turns = store.recent_turns(msg["conversation_id"])
    text = context.sql_user_message("and lahore?", {"first": "", "last": "", "last_full_day": ""}, "2026-11-13", turns, [])
    assert "Understood as: Which apps had the most identified users in Karachi" in text
    store.delete_conversation(msg["conversation_id"])


def test_instructions_cover_messy_questions():
    s = context.SQL_INSTRUCTIONS
    assert "Understand the question first" in s and "Roman Urdu" in s and "team_terms" in s
