import pytest

from ipdr import agent, concepts, dictionary, explore
from ipdr.explore import ExploreQuery, FilterIn
from ipdr.llm import LLMConfig, ToolCall, Turn


@pytest.mark.parametrize("question,concept", [
    ("what is the app adult uses most", "adult_content"),
    ("gandi videos kon dekhta hai", "adult_content"),
    ("most used social media in karachi", "social_media"),
    ("how many people use vpn", "vpn_proxy"),
    ("top dating apps", "dating"),
    ("AI chat usage last week", "ai_assistants"),
    ("jua satta apps", "gambling"),
    ("which games are popular", "gaming"),
])
def test_questions_map_to_concepts(question, concept):
    assert concept in [c["key"] for c in concepts.match(question)]


@pytest.mark.parametrize("question", ["busiest cell sites islamabad map", "users by date", "top 10 cities by users",
                                      "how many iphone users", "peak hour", "data usage per day"])
def test_ordinary_questions_match_no_concept(question):
    assert concepts.match(question) == []


def test_catalogue_is_well_formed():
    cat = concepts.catalogue()
    assert cat["adult_content"]["labels"] == ["Xhamster", "Pornhub", "Xnxx", "Xvideos"]
    for c in cat.values():
        assert c["labels"] and c["description"] and c["synonyms"], c["key"]


def test_prompt_contains_concepts():
    p = dictionary.render_for_prompt()
    assert "app_concepts:" in p and "adult_content" in p and "'Xhamster'" in p


def test_find_values_points_to_the_concept(monkeypatch):
    from ipdr.db import QueryResult

    monkeypatch.setattr(agent.db, "cached_query", lambda *a, **k: QueryResult([], [], 0.1, 0, 0))
    out = agent._tool_find_values("app_label", "adult", {"last_full_day": "2026-11-09"})
    assert "adult_content" in out and "Xhamster" in out


def test_giving_up_on_a_concept_question_is_sent_back(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE, GOOD_SQL

    gave_up = plan(sql="", status="cannot_answer", message="The term 'adult' was not found in app_label.")
    fixed = GOOD_SQL.replace("AND client_location = 'Karachi Pakistan'",
                             "AND (app_label IN " + concepts.sql_list("adult_content") + " OR match(requested_server, 'xhamster'))")
    s = FakeSession([
        Turn("", [ToolCall("a", "submit_answer", gave_up)], "tool_use"),
        Turn("", [ToolCall("b", "submit_answer", plan(sql=fixed))], "tool_use"),
    ])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    steps = []
    ans = agent.answer("what is the app adult uses most on 5 nov", [], LLMConfig(provider="anthropic", model="m"), RANGE, steps.append)
    assert ans.result is not None
    feedback = s.tool_results[0][0][1]
    assert "adult_content" in feedback and "Xhamster" in feedback
    assert "Reconsidering the meaning of the question" in steps


def test_a_second_cannot_answer_is_accepted(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE

    gave_up = plan(sql="", status="cannot_answer", message="No age data.")
    s = FakeSession([Turn("", [ToolCall("a", "submit_answer", gave_up)], "tool_use"),
                     Turn("", [ToolCall("b", "submit_answer", gave_up)], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    ans = agent.answer("adult users by age group", [], LLMConfig(provider="anthropic", model="m"), RANGE, lambda t: None)
    assert ans.result is None and ans.plan["status"] == "cannot_answer"


def test_explore_app_group_filter():
    b = explore.build(ExploreQuery(start="2026-11-09T00:00", end="2026-11-10T00:00",
                                   filters=[FilterIn(field="app_group", op="ne", value="ads_tracking")]))
    assert "NOT has({p0:Array(String)}, app_label)" in b.sql and "Vungle" in b.params["p0"]
    with pytest.raises(explore.ExploreError, match="Choose an app group"):
        explore.build(ExploreQuery(start="2026-11-09T00:00", end="2026-11-10T00:00",
                                   filters=[FilterIn(field="app_group", op="eq", value="nope")]))
    assert "app_group" not in explore.build(ExploreQuery(start="2026-11-09T00:00", end="2026-11-10T00:00",
                                                         columns=["app_group", "app_label"])).sql.split("FROM")[0]


def test_second_refusal_on_a_concept_question_is_answered_by_the_system(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan
    from ipdr.db import QueryResult

    RNG = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    refuse = plan(sql="", status="cannot_answer", message="The data does not have specific app names for adult content.")
    s = FakeSession([Turn("", [ToolCall("a", "submit_answer", refuse)], "tool_use"),
                     Turn("", [ToolCall("b", "submit_answer", refuse)], "tool_use")])

    class DB(FakeDB):
        def cached_query(self, sql, max_rows=None, params=None, ttl=600):
            self.queries.append(sql)
            return QueryResult([{"name": "app", "type": "String"}, {"name": "users", "type": "UInt64"}],
                               [["Xhamster", 101780], ["Pornhub", 21797]], 0.3, 1000, 0)

    fake = DB()
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", fake)
    steps = []
    ans = agent.answer("gandi videos kon zyada dekhta hai lahore me", [], LLMConfig(provider="ollama", model="m"), RNG, steps.append)
    assert ans.result is not None and ans.result.rows[0][0] == "Xhamster"
    assert "'Xhamster'" in fake.queries[-1] and "client_location = 'Lahore Pakistan'" in fake.queries[-1]
    assert "2026-11-03" in fake.queries[-1] and "2026-11-10" in fake.queries[-1]
    assert ans.plan["understood"] == "Which adult content apps were used the most in Lahore from 3 Nov to 9 Nov 2026?"
    assert any("standard ranking" in st for st in steps)
    assert "SELECT" in s.tool_results[0][0][1] and "'Pornhub'" in s.tool_results[0][0][1]


def test_city_detection():
    assert concepts.city_in("gandi videos lahore me") == "Lahore"
    assert concepts.city_in("tiktok khi last wk") == "Karachi"
    assert concepts.city_in("karachi vs lahore") is None
    assert concepts.city_in("top apps") is None


def test_incomplete_concept_list_is_flagged_once():
    from ipdr.agent import concept_incomplete
    msg = concept_incomplete("social media usage in karachi", "WHERE app_label IN ('Facebook', 'Instagram')")
    assert msg and "TikTok" in msg and "social media" in msg
    assert concept_incomplete("adult apps", "WHERE (app_label IN " + concepts.sql_list("adult_content") + " OR match(requested_server, 'x'))") is None
    assert concept_incomplete("top apps in lahore", "WHERE app_label IN ('Facebook')") is None


def test_missing_attribute_is_not_pushed_and_is_stated(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan, RANGE

    refuse = plan(sql="", status="cannot_answer", message="There is no age information in this data.")
    s = FakeSession([Turn("", [ToolCall("a", "submit_answer", refuse)], "tool_use")])
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", FakeDB())
    ans = agent.answer("adult users by age", [], LLMConfig(provider="ollama", model="m"), RANGE, lambda t: None)
    assert ans.result is None and ans.plan["status"] == "cannot_answer"
    assert s.tool_results == []


def test_answer_that_skips_a_missing_attribute_says_so():
    from ipdr.agent import note_missing
    out = note_missing({"explanation": "Counts users per adult app.", "assumptions": []}, "adult users by age group")
    assert out["assumptions"][-1]["label"] == "Not in the data" and "age" in out["assumptions"][-1]["value"]
    assert out["explanation"].startswith("The data has no age information")
    assert note_missing({"explanation": "e", "assumptions": []}, "women using tiktok")["assumptions"][0]["value"].startswith("gender")
    assert note_missing({"explanation": "e", "assumptions": []}, "how many people use whatsapp")["assumptions"] == []


def test_count_question_fallback_gives_one_total(monkeypatch):
    from tests.test_agent_loop import FakeDB, FakeSession, plan
    from ipdr.db import QueryResult

    RNG = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    refuse = plan(sql="", status="clarify", message="No values of client_location match ISB.")
    s = FakeSession([Turn("", [ToolCall(i, "submit_answer", refuse)], "tool_use") for i in "abc"])

    class DB(FakeDB):
        def cached_query(self, sql, max_rows=None, params=None, ttl=600):
            self.queries.append(sql)
            return QueryResult([{"name": "app_group", "type": "String"}, {"name": "users", "type": "UInt64"}],
                               [["tiktok", 25000]], 0.3, 1000, 0)

    fake = DB()
    monkeypatch.setattr(agent.llm, "open_session", lambda *a, **k: s)
    monkeypatch.setattr(agent, "db", fake)
    steps = []
    ans = agent.answer("how many ppl used tiktok in isb last 3 days", [], LLMConfig(provider="ollama", model="m"), RNG, steps.append)
    sql = fake.queries[-1]
    assert "GROUP BY" not in sql and "client_location = 'Islamabad Pakistan'" in sql and "2026-11-07" in sql
    assert ans.plan["understood"] == "How many people used TikTok in Islamabad from 7 Nov to 9 Nov 2026?"
    assert any("standard count" in st for st in steps)
    assert "Islamabad Pakistan" in s.tool_results[0][0][1]


@pytest.mark.parametrize("q,total", [("how many ppl used tiktok in isb", True), ("kitne log vpn use karte hain", True),
                                     ("which vpn is most used", False), ("how many users per social media app", False),
                                     ("top dating apps", False)])
def test_wants_total(q, total):
    assert concepts.wants_total(q) is total


def test_entertainment_is_an_umbrella_concept():
    c = concepts.match("best app for entertainment in this month")[0]
    assert c["key"] == "entertainment"
    for part in ("Netflix", "Spotify", "Roblox", "Bigo"):
        assert part in c["labels"]
    assert len(c["labels"]) == len(set(c["labels"]))


def test_sql_that_ignores_the_concept_is_sent_back():
    from ipdr.agent import concept_incomplete
    bad = ("SELECT server_asn AS app FROM t WHERE start_time >= 1793000000 "
           "AND server_asn IN ('Blizzard Entertainment, Inc', 'NForce Entertainment B.V.')")
    msg = concept_incomplete("best app for entertainment in this month", bad)
    assert msg and "does not use that group" in msg and "'Netflix'" in msg
    assert concept_incomplete("tiktok users in lahore", "WHERE app_label LIKE 'TikTok%'") is None
    assert concept_incomplete("top apps in lahore", bad) is None


def test_unlabelled_concept_sites_are_named_not_excluded():
    from ipdr.agent import generic_in_ranking
    from ipdr.db import QueryResult
    sql = ("SELECT app_label AS app FROM t WHERE (app_label IN ('Xhamster') OR match(requested_server, 'pornhub')) "
           "GROUP BY app")
    res = QueryResult([{"name": "app", "type": "String"}], [["Xhamster"], ["SSL"]], 0.1, 1, 0)
    msg = generic_in_ranking("what is the app adult uses most", sql, res)
    assert msg and "Do not exclude" in msg and "'Other adult content sites'" in msg
    plain = generic_in_ranking("top apps in karachi", "SELECT app_label AS app FROM t GROUP BY app", res)
    assert plain and "NOT IN" in plain


@pytest.mark.parametrize("question,keys", [
    ("gandi videos kon zyada dekhta hai lahore me", ["adult_content"]),
    ("adult videos", ["adult_content"]),
    ("video streaming and music apps", ["video_streaming", "music"]),
    ("social media and gaming in karachi", ["social_media", "gaming"]),
])
def test_a_word_inside_a_longer_phrase_is_not_a_second_concept(question, keys):
    assert [c["key"] for c in concepts.match(question)] == keys


def test_unlabelled_sites_of_a_concept_are_required():
    from ipdr.agent import concept_incomplete
    only_labels = "WHERE app_label IN ('Bet365') GROUP BY app"
    msg = concept_incomplete("betting apps users", only_labels)
    assert msg and "requested_server" in msg and "'Other gambling sites'" in msg
    with_sites = "WHERE (app_label IN ('Bet365') OR match(requested_server, 'x')) GROUP BY app"
    assert concept_incomplete("betting apps users", with_sites) is None
    assert concept_incomplete("top dating apps", "WHERE app_label IN " + concepts.sql_list("dating")) is None
