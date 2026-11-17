import pytest
from fastapi.testclient import TestClient

from ipdr import context, explore, main
from ipdr.agent import is_follow_up, period_mismatch
from ipdr.explore import ExploreError, ExploreQuery, FilterIn
from ipdr.sql_guard import GuardError, check

T = "wms_dist.ntc_ssl_log"
RNG = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}


@pytest.mark.parametrize("where", [
    "start_time > 0",
    "start_time >= 1793000000 OR 1=1",
    "start_time < 1793100000",
    "start_time BETWEEN 0 AND 1793100000",
    "(start_time >= 1793000000 OR app_label = 'x')",
])
def test_time_filter_must_bound_the_scan(where):
    with pytest.raises(GuardError, match="time range"):
        check(f"SELECT count() FROM {T} WHERE {where}")


@pytest.mark.parametrize("where", [
    "start_time >= 1793000000",
    "toUnixTimestamp('2026-11-01 00:00:00', 'Asia/Karachi') <= start_time",
    "start_time BETWEEN 1793000000 AND 1793100000",
    "(start_time >= 1793000000 AND start_time < 1793100000) AND (app_label = 'a' OR app_label = 'b')",
])
def test_real_time_filters_pass(where):
    check(f"SELECT count() FROM {T} WHERE {where}")


S7 = "start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') AND start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')"
S10 = "start_time >= toUnixTimestamp('2026-10-31 00:00:00', 'Asia/Karachi') AND start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')"
S8 = "start_time >= toUnixTimestamp('2026-11-08 00:00:00', 'Asia/Karachi') AND start_time < toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi')"


def test_period_check_catches_copied_dates():
    msg = period_mismatch("karachi me sb se zyada kon si app use hoti hai last week", S10, RNG)
    assert msg and "2026-11-03" in msg and "2026-11-10" in msg
    assert period_mismatch("karachi me sb se zyada kon si app use hoti hai last week", S7, RNG) is None
    assert period_mismatch("pichle hafte tiktok", S10, RNG)
    assert period_mismatch("top apps yesterday", S8, RNG)
    assert period_mismatch("kal kitna data", S8, RNG)
    assert period_mismatch("day before yesterday", S8, RNG) is None
    assert period_mismatch("yesterday vs last week", S8, RNG) is None
    assert period_mismatch("top apps on 5 nov", S8, RNG) is None
    assert period_mismatch("last week", "start_time >= 1793000000", RNG) is None


def test_follow_up_detection():
    assert all(is_follow_up(q) for q in ["aur lahore me?", "and yesterday?", "only karachi", "now show users instead"])
    assert not any(is_follow_up(q) for q in ["top 10 cities by users", "which servers do tiktok connect to most"])


def test_reference_queries_lose_their_dates():
    sql = ("SELECT 1 FROM t WHERE start_time >= toUnixTimestamp('2026-10-31 00:00:00', 'Asia/Karachi') "
           "AND start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi') AND x = 'Karachi Pakistan'")
    out = context.without_dates(sql)
    assert "2026" not in out and "'<start>'" in out and "'<end>'" in out and "'Karachi Pakistan'" in out
    msg = context.sql_user_message("q", RNG, "2026-11-13", [], [{"question": "Q", "sql": sql, "notes": ""}])
    ref = msg.split("<reference_queries>")[1]
    assert "2026-10-31" not in ref and "placeholders" in ref


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "data_range", lambda: RNG)
    with TestClient(main.app) as c:
        yield c


def test_blank_question_rejected(client):
    r = client.post("/api/chat", json={"question": "    ", "llm": {"provider": "ollama", "model": "m"}})
    assert r.status_code == 422


def test_blank_term_rejected(client):
    assert client.post("/api/terms", json={"term": "   ", "meaning": "x"}).status_code == 422
    assert client.post("/api/terms", json={"term": "x", "meaning": "  "}).status_code == 422


def test_corrupt_upload_is_a_clear_error(client):
    r = client.post("/api/dictionary", files={"file": ("j.csv", bytes(range(256)) * 10, "text/csv")})
    assert r.status_code == 400 and "could not be read" in r.json()["detail"]


def test_bad_export_link_message(client):
    r = client.get("/api/explore/export", params={"q": "{not json"})
    assert r.status_code == 400 and "export link is not valid" in r.json()["detail"]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e400"])
def test_explore_rejects_non_finite_numbers(value):
    with pytest.raises(ExploreError, match="usable number"):
        explore.build(ExploreQuery(start="2026-11-09T10:00", end="2026-11-09T11:00",
                                   filters=[FilterIn(field="duration", op="gt", value=value)]))


def test_conclusion_numbers_checked_and_sent_back(monkeypatch):
    from ipdr import agent
    from ipdr.llm import LLMConfig, ToolCall, Turn
    from tests.test_agent_loop import FakeSession

    bad = {"headline": "Facebook leads with 156 GB.", "findings": ["TikTok is behind Lenovo by 1.56 GB."], "caveats": []}
    good = {"headline": "Facebook leads with 156 GB.", "findings": ["TikTok is behind Lenovo by 1.6 GB."], "caveats": []}
    s = FakeSession([Turn("", [ToolCall("a", "submit_conclusion", bad)], "tool_use"),
                     Turn("", [ToolCall("b", "submit_conclusion", good)], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    data = {"title": "t", "sql": "", "assumptions": [], "result": {
        "columns": [{"name": "app", "type": "String"}, {"name": "volume_gb", "type": "Float64"}],
        "rows": [["Facebook", 156], ["Lenovo", 80.88], ["TikTok_NonHTTP_Video", 79.28]]}}
    out = agent.conclude(data, "q", LLMConfig(provider="ollama", model="m"))
    assert out["findings"] == good["findings"] and "unverified" not in out
    assert "1.56 GB" in s.tool_results[0][0][1]


def test_conclusion_keeps_warning_when_still_wrong(monkeypatch):
    from ipdr import agent
    from ipdr.llm import LLMConfig, ToolCall, Turn
    from tests.test_agent_loop import FakeSession

    bad = {"headline": "Facebook leads with 160 GB.", "findings": [], "caveats": []}
    s = FakeSession([Turn("", [ToolCall("a", "submit_conclusion", bad)], "tool_use"),
                     Turn("", [ToolCall("b", "submit_conclusion", bad)], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    data = {"title": "t", "sql": "", "assumptions": [], "result": {
        "columns": [{"name": "app", "type": "String"}, {"name": "volume_gb", "type": "Float64"}],
        "rows": [["Facebook", 156], ["Lenovo", 80.88]]}}
    out = agent.conclude(data, "q", LLMConfig(provider="ollama", model="m"))
    assert out["unverified"] == ["160 GB"]


def test_period_text_follows_the_sql():
    from ipdr.agent import align_period_text, sql_period
    from datetime import date

    sql = S7 + " AND client_location = 'Lahore Pakistan'"
    assert sql_period(sql) == (date(2026, 11, 3), date(2026, 11, 9))
    plan = {"understood": "Which adult apps had the most users in Lahore from 31 Oct to 9 Nov 2026?",
            "title": "Adult content in Lahore, 31 Oct to 9 Nov",
            "assumptions": [{"label": "Period", "value": "31 Oct to 9 Nov"}, {"label": "Area", "value": "Lahore"}]}
    out = align_period_text(plan, sql)
    assert "31 Oct" not in out["understood"] and "3 Nov to 9 Nov 2026" in out["understood"]
    assert "31 Oct" not in out["title"]
    assert out["assumptions"][0] == {"label": "Period", "value": "3 Nov to 9 Nov 2026 (Pakistan time)"}
    assert {"label": "Area", "value": "Lahore"} in out["assumptions"]
    same = align_period_text({"understood": "Top apps in the last 7 days?", "title": "Top apps", "assumptions": []}, sql)
    assert same["understood"] == "Top apps from 3 Nov to 9 Nov 2026?"
    assert align_period_text({"understood": "x", "assumptions": []}, "SELECT 1")["understood"] == "x"


def test_assumptions_must_match_the_sql():
    from ipdr.agent import align_period_text

    sql = S7 + " AND app_label IN ('Bet365')"
    plan = {"understood": "q", "title": "t", "assumptions": [
        {"label": "Measure", "value": "users"}, {"label": "Excluded", "value": "generic labels"},
        {"label": "Coverage", "value": "subscribers with location data"}, {"label": "Area", "value": "Lahore"}]}
    labels = [a["label"] for a in align_period_text(plan, sql)["assumptions"]]
    assert labels == ["Period", "Measure"]
    sql2 = S7 + " AND loc ILIKE '%Gulberg%' AND app_label NOT IN ('SSL')"
    plan2 = {"understood": "q", "title": "t", "assumptions": [{"label": "Area", "value": "Gulberg"},
             {"label": "Excluded", "value": "SSL"}, {"label": "Coverage", "value": "cell data only"}]}
    assert [a["label"] for a in align_period_text(plan2, sql2)["assumptions"]] == ["Period", "Area", "Excluded", "Coverage"]


def test_rewind_removes_the_question_and_everything_after(client):
    from ipdr import store

    conv = store.create_conversation("t")
    cid = conv["id"]
    q1 = store.add_message(cid, "user", "question", "first")
    a1 = store.add_message(cid, "assistant", "result", "A1", {})
    q2 = store.add_message(cid, "user", "question", "second")
    store.add_message(cid, "assistant", "result", "A2", {})
    other = store.create_conversation("o")
    oq = store.add_message(other["id"], "user", "question", "keep me")
    try:
        assert client.post(f"/api/conversations/{cid}/rewind", json={"message_id": a1["id"]}).status_code == 400
        assert client.post(f"/api/conversations/{cid}/rewind", json={"message_id": oq["id"]}).status_code == 404
        r = client.post(f"/api/conversations/{cid}/rewind", json={"message_id": q2["id"]})
        assert r.status_code == 200 and r.json()["removed"] == 2
        left = [m["content"] for m in store.get_conversation(cid)["messages"]]
        assert left == ["first", "A1"]
        assert [m["content"] for m in store.get_conversation(other["id"])["messages"]] == ["keep me"]
        client.post(f"/api/conversations/{cid}/rewind", json={"message_id": q1["id"]})
        conv_after = store.get_conversation(cid)
        assert conv_after["messages"] == [] and conv_after["title"] == ""
    finally:
        store.delete_conversation(cid)
        store.delete_conversation(other["id"])


def test_hidden_chart_and_conclusion_are_remembered(client):
    from ipdr import store

    conv = store.create_conversation("t")
    m = store.add_message(conv["id"], "assistant", "result", "A", {"result": {"columns": [], "rows": []}})
    try:
        assert client.patch(f"/api/messages/{m['id']}", json={"conclusion_hidden": True}).status_code == 200
        assert client.patch(f"/api/messages/{m['id']}", json={"chart": 2, "chart_hidden": True}).status_code == 200
        d = store.get_message(m["id"])["data"]
        assert d["conclusion_hidden"] is True and d["chart_hidden"] is True and d["chart_choice"] == 2
        client.patch(f"/api/messages/{m['id']}", json={"conclusion_hidden": False})
        d = store.get_message(m["id"])["data"]
        assert d["conclusion_hidden"] is False and d["chart_choice"] == 2
    finally:
        store.delete_conversation(conv["id"])


def test_clarify_after_a_failed_lookup_is_sent_back(monkeypatch):
    from ipdr import agent
    from ipdr.llm import LLMConfig, ToolCall, Turn
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE

    lazy = plan(sql="", status="clarify", message="No values of app_label contain 'karachi' on 2026-11-09. Try a shorter or different spelling.")
    s = FakeSession([Turn("", [ToolCall("a", "submit_answer", lazy)], "tool_use"),
                     Turn("", [ToolCall("b", "submit_answer", plan())], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    ans = agent.answer("karachi me sb se zyada kon si app use hoti hai on 5 nov", [], LLMConfig(provider="anthropic", model="m"), RANGE, lambda t: None)
    assert ans.result is not None
    assert "client_location = 'Karachi Pakistan'" in s.tool_results[0][0][1]


def test_real_ambiguity_may_still_ask():
    from ipdr.agent import _lazy_clarify
    assert not _lazy_clarify("top apps in model town", {"message": "Model Town exists in Lahore, Islamabad and Rawalpindi. Which city?"})
    assert _lazy_clarify("tiktok users in lahore", {"message": "Which city do you mean?"})


def test_follow_up_with_the_old_city_is_sent_back(monkeypatch):
    from ipdr import agent
    from ipdr.llm import LLMConfig, ToolCall, Turn
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE, GOOD_SQL

    lahore_sql = GOOD_SQL.replace("Karachi Pakistan", "Lahore Pakistan")
    s = FakeSession([Turn("", [ToolCall("a", "submit_answer", plan())], "tool_use"),
                     Turn("", [ToolCall("b", "submit_answer", plan(sql=lahore_sql))], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    turns = [{"role": "user", "kind": "question", "content": "top apps karachi on 5 nov", "data": {}},
             {"role": "assistant", "kind": "result", "content": "t", "data": {"sql": GOOD_SQL, "title": "t"}}]
    ans = agent.answer("aur lahore me?", turns, LLMConfig(provider="anthropic", model="m"), RANGE, lambda t: None)
    assert "Lahore Pakistan" in ans.plan["sql"]
    assert "about Lahore" in s.tool_results[0][0][1]


def test_background_traffic_in_top_apps_is_sent_back(monkeypatch):
    from ipdr import agent
    from ipdr.db import QueryResult
    from ipdr.llm import LLMConfig, ToolCall, Turn
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE, GOOD_SQL

    class DB(FakeDB):
        def cached_query(self, sql, max_rows=None, params=None, ttl=600):
            self.queries.append(sql)
            rows = [["TikTok", 9]] if "NOT IN" in sql else [["SSL", 10], ["TikTok", 9]]
            return QueryResult([{"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}], rows, 0.1, 1, 0)

    clean = GOOD_SQL.replace("GROUP BY", "AND app_label NOT IN ('SSL')\nGROUP BY")
    s = FakeSession([Turn("", [ToolCall("a", "submit_answer", plan())], "tool_use"),
                     Turn("", [ToolCall("b", "submit_answer", plan(sql=clean))], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", DB())
    ans = agent.answer("most used apps in karachi on 5 nov", [], LLMConfig(provider="anthropic", model="m"), RANGE, lambda t: None)
    assert ans.result.rows == [["TikTok", 9]]
    assert "background traffic (SSL)" in s.tool_results[0][0][1]


def test_extra_other_bucket_is_flagged():
    from ipdr.agent import extra_other_bucket
    from ipdr.db import QueryResult
    cols = [{"name": "city", "type": "String"}, {"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}]
    r = QueryResult(cols, [["Karachi", "IMO", 5], ["Karachi", "WhatsApp", 6], ["Karachi", "Other", 9]], 0.1, 1, 0)
    assert extra_other_bucket("whatsapp vs imo usage karachi lahore", r)
    assert extra_other_bucket("whatsapp vs imo vs others", r) is None
    assert extra_other_bucket("share of whatsapp in karachi", r) is None
    ok = QueryResult(cols, [["Lahore", "Other adult content sites", 3]], 0.1, 1, 0)
    assert extra_other_bucket("adult sites in lahore", ok) is None


@pytest.mark.parametrize("text,stored", [("what did 0325-3047295 do on 5 nov", "923253047295"),
                                         ("+92 325 3047295 ka data", "923253047295"),
                                         ("923001234567", "923001234567"), ("0300 1234567", "923001234567")])
def test_phone_numbers_are_converted(text, stored):
    from ipdr import context
    assert [s for _, s in context.phones(text)] == [stored]


@pytest.mark.parametrize("text", ["start_time >= 1793000000", "top 10 apps on 5 nov", "BZ71155028", "2026-11-05"])
def test_non_phones_are_left_alone(text):
    from ipdr import context
    assert context.phones(text) == []


def test_phone_question_gets_the_stored_form():
    from ipdr import context
    msg = context.sql_user_message("what did 0325-3047295 do on 5 nov", {"first": "x", "last": "y", "last_full_day": "2026-11-09"},
                                   "2026-11-13", [], [])
    assert "subscriber_id IN ('923253047295', '03253047295')" in msg


def test_operator_names_become_isp_filters():
    from ipdr import context
    assert context.operators("Jazz users count yesterday") == [
        ("Jazz", "client_asn IN ('PMCL LDI IP TRANSIT', 'IX Peering for Mobilink and Link Direct International.')")]
    assert context.operators("jazz sim users") == [("Jazz", "mcc = '410' AND mnc IN ('01', '07')")]
    assert context.operators("ufone users") == [("Ufone", "mcc = '410' AND mnc IN ('03')")]
    assert [n for n, _ in context.operators("top whatsapp nayatel users")] == ["Nayatel"]
    assert [n for n, _ in context.operators("ptcl users by volume")] == ["PTCL"]
    assert [n for n, _ in context.operators("zong vs telenor")] == ["Zong", "Telenor"]
    assert context.operators("jazzcash app users") == []
    assert context.operators("top apps in karachi") == []
    msg = context.sql_user_message("Jazz users count yesterday", {"first": "x", "last": "y", "last_full_day": "2026-11-09"},
                                   "2026-11-13", [], [])
    from ipdr import agent
    lines = agent.resolved_names("Jazz users count yesterday", {"first": "2026-10-11", "last": "y", "last_full_day": "2026-11-09"})
    assert any("client_asn IN ('PMCL LDI IP TRANSIT'" in line for line in lines)


def test_technical_clarify_is_not_sent_to_the_analyst():
    from ipdr.agent import _lazy_clarify
    assert _lazy_clarify("show me something", {"message": "The `users` metric is not directly available. Please confirm "
                                                          "if you want identified users or connections.", "understood": ""})
    assert _lazy_clarify("show me something", {"message": "What would you like to see?",
                                               "understood": "Show the most used apps, by default the last 7 days."})
    assert _lazy_clarify("average session duration by app category",
                         {"message": "Please provide the time period for the analysis.", "understood": ""})
    assert not _lazy_clarify("top apps in model town", {"message": "Which Model Town: Lahore or Karachi?", "understood": ""})


def test_empty_lookup_is_described_plainly():
    from ipdr.agent import _describe
    from ipdr.llm import ToolCall
    assert _describe(ToolCall("x", "find_values", {"column": "client_location", "search": ""})) == "Listing the most common values of client_location"
    assert _describe(ToolCall("x", "find_values", {"column": "loc", "search": "Gulberg"})).startswith("Looking up")


@pytest.mark.parametrize("understood,expected", [
    ("What is the daily trend of Netflix data usage in November 2026?", "What is the daily trend of Netflix data usage in November 2026?"),
    ("Netflix usage this month (1 Nov to 30 Nov 2026)?", "Netflix usage from 1 Nov to 9 Nov 2026?"),
    ("Netflix usage in November 2026 from 1 Nov to 30 Nov 2026?", "Netflix usage from 1 Nov to 9 Nov 2026?"),
])
def test_month_year_is_not_read_as_a_day(understood, expected):
    from ipdr.agent import align_period_text
    sql = ("WHERE start_time >= toUnixTimestamp('2026-11-01 00:00:00', 'Asia/Karachi') "
           "AND start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")
    assert align_period_text({"understood": understood, "title": "t", "assumptions": []}, sql)["understood"] == expected


@pytest.mark.parametrize("q,first,last", [("users at night in faisalabad", "2026-11-03", "2026-11-09"),
                                          ("subah ke waqt top apps", "2026-11-03", "2026-11-09"),
                                          ("last night tiktok users", "2026-11-09", "2026-11-09"),
                                          ("kal raat kitne log online the", "2026-11-09", "2026-11-09")])
def test_time_of_day_is_not_a_period(q, first, last):
    from ipdr.agent import question_period
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    _, start, end = question_period(q, rng)
    assert (start.isoformat(), end.isoformat()) == (first, last)


def test_night_question_on_one_other_day_is_flagged():
    from ipdr.agent import period_mismatch
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    sql = ("WHERE start_time >= toUnixTimestamp('2026-11-08 00:00:00', 'Asia/Karachi') "
           "AND start_time < toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi')")
    assert period_mismatch("users at night in faisalabad", sql, rng)


SNAP_Q = "snapchat or instagram, which is bigger in multan? also what about gujranwala"
SNAP_SQL = """SELECT app_label AS app, uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS users
FROM wms_dist.ntc_ssl_log
WHERE start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi')
  AND (client_location = 'Multan Pakistan' OR client_location = 'Gujranwala Pakistan')
  AND (app_label IN ('Instagram', 'Instagram_NonHTTP_Video'))
GROUP BY app"""


def test_named_app_left_out_is_flagged():
    from ipdr.agent import named_app_missing
    msg = named_app_missing(SNAP_Q, SNAP_SQL)
    assert msg and "snapchat" in msg and "'SnapChat'" in msg
    assert named_app_missing(SNAP_Q, SNAP_SQL.replace("'Instagram', ", "'SnapChat', 'Instagram', ")) is None
    assert named_app_missing("trend of netflix data daily", "WHERE app_label IN ('Netflix')") is None
    assert named_app_missing("top apps in lahore", "GROUP BY app_label") is None


def test_two_cities_added_together_is_flagged():
    from ipdr.agent import cities_merged
    assert cities_merged(SNAP_Q, SNAP_SQL)
    by_city = SNAP_SQL.replace("SELECT app_label AS app,", "SELECT replaceOne(client_location, ' Pakistan', '') AS city, app_label AS app,")
    assert cities_merged(SNAP_Q, by_city) is None
    assert cities_merged("total users in karachi and lahore combined", SNAP_SQL.replace("Multan", "Karachi").replace("Gujranwala", "Lahore")) is None


def test_cities_in_lists_every_city():
    from ipdr import concepts
    assert concepts.cities_in(SNAP_Q) == ["Multan", "Gujranwala"]
    assert concepts.city_in(SNAP_Q) is None and concepts.city_in("tiktok in isb") == "Islamabad"


def test_unanswered_last_question_stays_in_context():
    from ipdr import context
    turns = [{"role": "user", "kind": "question", "content": "best app for entertainment in this month", "data": None}]
    msg = context.sql_user_message("continue the above query", {"first": "x", "last": "y", "last_full_day": "2026-11-09"},
                                   "2026-11-13", turns, [])
    assert "best app for entertainment in this month" in msg and "interrupted" in msg


@pytest.mark.parametrize("q,expected", [
    ("continue the above query", "best app for entertainment in this month"),
    ("try again", "best app for entertainment in this month"),
    ("dobara", "best app for entertainment in this month"),
    ("please retry", "best app for entertainment in this month"),
    ("continue with lahore and also show volume per day for each app", None),
])
def test_retry_resolves_to_the_unanswered_question(q, expected):
    from ipdr import context
    turns = [{"role": "user", "kind": "question", "content": "best app for entertainment in this month"},
             {"role": "user", "kind": "question", "content": "continue the above query"},
             {"role": "assistant", "kind": "cannot_answer", "content": "There is no previous query."}]
    assert context.retry_target(q, turns) == expected


def test_retry_after_a_real_answer_is_left_to_the_model():
    from ipdr import context
    turns = [{"role": "user", "kind": "question", "content": "top apps"},
             {"role": "assistant", "kind": "result", "content": "Top apps"}]
    assert context.retry_target("again", turns) is None


def test_timezone_is_not_a_place():
    from ipdr.agent import drop_unasked_city
    sql = ("SELECT app_label FROM t WHERE start_time >= toUnixTimestamp('2026-11-01 00:00:00', 'Asia/Karachi') "
           "AND app_label IN ('Netflix')")
    plan = {"understood": "What are the best apps for entertainment in Karachi this month?",
            "title": "Best entertainment apps in Karachi, Nov 2026", "sql": sql,
            "assumptions": [{"label": "Area", "value": "Karachi"}, {"label": "Measure", "value": "users"}]}
    out = drop_unasked_city(plan, "best app for entertainment in this month")
    assert out["understood"] == "What are the best apps for entertainment this month?"
    assert out["title"] == "Best entertainment apps, Nov 2026"
    assert [a["label"] for a in out["assumptions"]] == ["Measure"]
    kept = drop_unasked_city({"understood": "Top apps in Karachi?", "title": "t", "assumptions": [],
                              "sql": sql + " AND client_location = 'Karachi Pakistan'"}, "now show users instead")
    assert kept["understood"] == "Top apps in Karachi?"


@pytest.mark.parametrize("text,kind", [
    ("good", "thanks"), ("Good!", "thanks"), ("thanks", "thanks"), ("thank you so much", "thanks"), ("ok", "thanks"),
    ("shukriya bhai", "thanks"), ("theek hai", "thanks"), ("great job", "thanks"), ("👍🙂", "thanks"),
    ("hello", "hello"), ("assalam o alaikum", "hello"), ("hi", "hello"), ("good morning", "hello"),
    ("bye", "bye"), ("allah hafiz", "bye"), ("help", "help"), ("what can you do?", "help"),
    ("good apps in lahore", None), ("top apps", None), ("ok now show lahore", None), ("thanks, now for karachi", None),
    ("0325-3047295", None), ("tiktok", None),
])
def test_small_talk(text, kind):
    from ipdr import context
    assert context.small_talk(text) == kind


def test_yes_after_a_clarifying_question_is_an_answer():
    from ipdr import context
    assert context.small_talk("yes", [{"role": "assistant", "kind": "clarify", "content": "Model Town in Lahore?"}]) is None
    assert context.small_talk("yes") == "thanks"


_TURNS = [{"role": "user", "kind": "question", "content": "top apps", "id": "u1"},
          {"role": "assistant", "kind": "result", "content": "Top apps", "id": "a1"}]


@pytest.mark.parametrize("text,actions", [
    ("no need to draw chart and conclusion", ["hide_chart", "hide_conclusion"]),
    ("draw a chart", ["chart"]), ("make a graph please", ["chart"]), ("write conclusion", ["conclusion"]),
    ("summarize this", ["conclusion"]), ("export to excel", ["export"]), ("chart mat banao", ["hide_chart"]),
    ("conclusion ki zaroorat nahi", ["hide_conclusion"]), ("hide the chart", ["hide_chart"]),
])
def test_display_instructions_act_on_the_last_answer(text, actions):
    from ipdr import context
    out = context.app_command(text, _TURNS)
    assert out["actions"] == actions and out["target"] == "a1"


@pytest.mark.parametrize("text", ["chart of tiktok users per day", "show me the table of top apps",
                                  "graph of users by city", "top apps in karachi", "summary of vpn usage in lahore"])
def test_data_questions_that_mention_charts_go_to_the_ai(text):
    from ipdr import context
    assert context.app_command(text, _TURNS) is None


_CHARTED = [{"role": "user", "kind": "question", "content": "users by app", "id": "u1"},
            {"role": "assistant", "kind": "result", "content": "Users by app", "id": "a1",
             "data": {"charts": {"options": [{"type": "bar", "title": "Bars: Users by app", "additive": False},
                                             {"type": "column", "title": "Columns: Users by app", "additive": False}]}}}]


@pytest.mark.parametrize("text,action", [
    ("show it as a pie chart", "chart:pie"), ("donut", "chart:pie"), ("make a treemap", "chart:treemap"),
    ("line graph please", "chart:line"), ("stacked area chart", "chart:stacked_area"), ("heatmap", "chart:heatmap"),
    ("sankey diagram", "chart:sankey"), ("pie chart bana do", "chart:pie"), ("draw bar chart", "chart:bar"),
])
def test_a_named_chart_type_is_drawn(text, action):
    from ipdr import context
    out = context.app_command(text, _TURNS)
    assert out["actions"] == [action] and out["target"] == "a1"


@pytest.mark.parametrize("text", ["area wise users", "line rental users", "map of karachi users", "flow of traffic in lahore"])
def test_chart_type_words_alone_stay_data_questions(text):
    from ipdr import context
    assert context.app_command(text, _TURNS) is None


def test_a_chart_type_that_does_not_fit_says_why():
    from ipdr import context
    out = context.app_command("show it as a pie chart", _CHARTED)
    assert out["actions"] == ["chart:pie"]
    assert "does not fit" in out["reply"] and "one user is counted in several groups" in out["reply"]
    assert "bar chart, column chart" in out["reply"]
    ok = context.app_command("column chart", _CHARTED)
    assert "does not fit" not in ok["reply"] and "column chart" in ok["reply"]


def test_display_instruction_without_an_answer_explains():
    from ipdr import context
    out = context.app_command("draw a chart", [])
    assert out["actions"] == [] and "Ask a question first" in out["reply"]


def test_places_the_question_never_named_are_flagged():
    from ipdr.agent import unasked_place
    sql = "WHERE client_location IN ('Karachi Pakistan', 'Lahore Pakistan', 'Islamabad Pakistan') AND mnc IN ('01')"
    msg = unasked_place("Jazz users count yesterday", sql, [])
    assert msg and "Karachi, Lahore, Islamabad" in msg and "all of Pakistan" in msg
    assert unasked_place("which servers do tiktok connect to most", "AND client_location = 'Karachi Pakistan'", [])
    assert unasked_place("tiktok users in karachi", "AND client_location = 'Karachi Pakistan'", []) is None
    turns = [{"role": "user", "kind": "question", "content": "top apps in lahore"}]
    assert unasked_place("now show users instead", "AND client_location = 'Lahore Pakistan'", turns) is None
    assert "Karachi" in unasked_place("tiktok in lahore", "client_location IN ('Lahore Pakistan', 'Karachi Pakistan')", [])


def test_a_city_assumed_for_a_named_neighbourhood_is_kept():
    from ipdr.agent import unasked_place
    sql = "WHERE loc ILIKE '%Model Town%' AND loc ILIKE '%Lahore%'"
    assert unasked_place("top apps in model town", sql, []) is None
    assert unasked_place("top apps in model town", "WHERE client_location = 'Lahore Pakistan' AND loc ILIKE '%Model Town%'", []) is None
    assert unasked_place("top apps", "WHERE loc ILIKE '%Lahore%'", [])


def test_unknown_city_values_are_not_cities():
    from ipdr.agent import unknown_city_in_ranking
    from ipdr.db import QueryResult
    cols = [{"name": "city", "type": "String"}, {"name": "users", "type": "UInt64"}]
    bad = QueryResult(cols, [["Islamabad Pakistan", 9], ["Punjab Pakistan", 5], ["Multan Pakistan", 3]], 0.1, 1, 0)
    assert "Punjab Pakistan" in unknown_city_in_ranking(bad)
    ok = QueryResult(cols, [["Islamabad Pakistan", 9], ["Multan Pakistan", 3]], 0.1, 1, 0)
    assert unknown_city_in_ranking(ok) is None
    apps = QueryResult([{"name": "app", "type": "String"}], [["Pakistan"]], 0.1, 1, 0)
    assert unknown_city_in_ranking(apps) is None


def test_compared_apps_must_be_one_row_each():
    from ipdr.agent import labels_not_grouped
    from ipdr.db import QueryResult
    cols = [{"name": "city", "type": "String"}, {"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}]
    q = "snapchat or instagram, which is bigger in multan? also what about gujranwala"
    split = QueryResult(cols, [["Multan", "SnapChat", 5], ["Multan", "Instagram", 4], ["Multan", "Instagram_NonHTTP_Video", 3]], 0.1, 1, 0)
    msg = labels_not_grouped(q, split)
    assert msg and "instagram" in msg and "multiIf" in msg
    grouped = QueryResult(cols, [["Multan", "snapchat", 5], ["Multan", "instagram", 4]], 0.1, 1, 0)
    assert labels_not_grouped(q, grouped) is None
    assert labels_not_grouped("top tiktok labels", split) is None


def test_a_ranking_question_answered_with_one_total_is_sent_back():
    from ipdr.agent import ranking_as_total
    from ipdr.db import QueryResult
    total = QueryResult([{"name": "users", "type": "UInt64"}], [[4250]], 0.1, 1, 0)
    assert ranking_as_total("gandi videos kon zyada dekhta hai lahore me", total)
    assert ranking_as_total("most used vpn", total)
    assert ranking_as_total("how many ppl used tiktok in isb", total) is None
    assert ranking_as_total("kitna data use hua total last week", total) is None
    one_named = QueryResult([{"name": "hour", "type": "UInt8"}, {"name": "app", "type": "String"}], [[19, "YouTube"]], 0.1, 1, 0)
    assert ranking_as_total("peak hour for youtube most used", one_named) is None


def test_long_assumption_lists_are_shortened():
    from ipdr.agent import short_assumptions
    long = ("SSL, QUIC_AwareData, GoogleAPI, Google_Common, Google_Common_Login, Google_Common_Upload, Google_Gstatic, "
            "Crashlytics, Amazon_SSL, AmazonS3, Aliyun, Apple_Service, GoogleAdsense, Vungle, Liftoff, QQ_Common_HTTP, Yandex_Common")
    out = short_assumptions({"assumptions": [{"label": "Excluded", "value": long}, {"label": "Area", "value": "Model Town, Lahore"}]})
    assert out["assumptions"][0]["value"] == "background traffic: SSL, QUIC_AwareData, GoogleAPI and 14 more"
    assert out["assumptions"][1]["value"] == "Model Town, Lahore"


@pytest.mark.parametrize("text", ["i will ask later", "let me think", "main baad me poochunga", "hold on", "nevermind",
                                  "ok i will check"])
def test_conversation_without_a_data_question_gets_a_reply(text):
    from ipdr import context
    assert context.small_talk(text) == "later"


@pytest.mark.parametrize("text", ["Vungle", "snapchat", "and lahore", "same for yesterday", "only karachi",
                                  "now show users instead", "what about youtube", "i want top apps",
                                  "main karachi ka data dekhna chahta hoon", "0325-3047295"])
def test_anything_that_could_be_a_data_question_goes_to_the_ai(text):
    from ipdr import context
    assert context.small_talk(text) is None


@pytest.mark.parametrize("q,follow", [
    ("koi dating app use hoti hai?", False), ("which vpn is most popular?", False), ("top apps?", False),
    ("aur lahore me?", True), ("and yesterday?", True), ("lahore?", True), ("only karachi", True),
    ("same for last week", True), ("kitne log tiktok use karte hain?", False),
])
def test_short_questions_with_a_subject_are_not_follow_ups(q, follow):
    from ipdr.agent import is_follow_up
    assert is_follow_up(q) is follow


def test_short_self_contained_question_gets_the_default_period():
    from ipdr.agent import question_period
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    label, start, end = question_period("koi dating app use hoti hai?", rng)
    assert (start.isoformat(), end.isoformat()) == ("2026-11-03", "2026-11-09") and "default" in label


def test_an_app_breakdown_nobody_asked_for_is_sent_back():
    from ipdr.agent import unasked_app_breakdown
    from ipdr.db import QueryResult
    by_app = QueryResult([{"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}],
                         [["TikTok", 9], ["Facebook", 8]], 0.1, 1, 0)
    assert unasked_app_breakdown("users at night in faisalabad", by_app)
    for q in ["show me something", "top apps in karachi", "which vpn is most popular", "tiktok users per day",
              "average session duration by app category", "which social media apps are popular in karachi"]:
        assert unasked_app_breakdown(q, by_app) is None, q
    total = QueryResult([{"name": "users", "type": "UInt64"}], [[9]], 0.1, 1, 0)
    assert unasked_app_breakdown("users at night in faisalabad", total) is None


def test_a_kind_of_app_compared_across_cities_is_not_split_by_app():
    from ipdr.agent import unasked_app_breakdown
    from ipdr.db import QueryResult
    cols = [{"name": "city", "type": "String"}, {"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}]
    rows = [["Karachi", "Facebook", 9], ["Karachi", "TikTok", 8], ["Lahore", "Facebook", 7], ["Lahore", "TikTok", 6]]
    r = QueryResult(cols, rows, 0.1, 1, 0)
    assert unasked_app_breakdown("social media usage in karachi vs lahore last week", r)
    assert unasked_app_breakdown("whatsapp vs imo usage karachi lahore", r) is None
    assert unasked_app_breakdown("which social media apps are most used in karachi vs lahore", r) is None


def test_a_follow_up_to_a_new_city_drops_the_old_one():
    from ipdr.agent import follow_up_kept_old_place
    turns = [{"role": "user", "kind": "question", "content": "karachi me sb se zyada kon si app use hoti hai last week"},
             {"role": "assistant", "kind": "result", "content": "t"}]
    both = "AND (client_location = 'Karachi Pakistan' OR client_location = 'Lahore Pakistan')"
    msg = follow_up_kept_old_place("aur lahore me?", both, turns)
    assert msg and "Karachi" in msg and "instead of" in msg
    assert follow_up_kept_old_place("aur lahore me?", "AND client_location = 'Lahore Pakistan'", turns) is None
    assert follow_up_kept_old_place("aur lahore me bhi?", both, turns) is None
    assert follow_up_kept_old_place("compare with lahore", both, turns) is None
    assert follow_up_kept_old_place("aur lahore me?", both, []) is None
