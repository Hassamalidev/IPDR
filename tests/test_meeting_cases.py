import pytest

from ipdr import agent, context
from ipdr.db import QueryResult
from ipdr.llm import Turn
from tests.test_agent_loop import CFG, RANGE, FakeSession, call, plan

WEEK = ("start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') AND "
        "start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")
WHATSAPP = "app_label IN ('WhatsAPP', 'WhatsAPP_Web', 'WhatsApp_Login', 'WhatsApp_VoIP')"

MEETING_NAYATEL = (f"SELECT subscriber_id AS user_id, uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS users "
                   f"FROM wms_dist.ntc_ssl_log WHERE {WEEK} AND client_location = 'Islamabad Pakistan' AND {WHATSAPP} "
                   f"GROUP BY subscriber_id ORDER BY users DESC LIMIT 10")
MEETING_PTCL = (f"SELECT subscriber_id AS user, round(sum(c2s_byte_num + s2c_byte_num) / 1e9, 2) AS volume_gb "
                f"FROM wms_dist.ntc_ssl_log WHERE {WEEK} AND mnc = '01' GROUP BY subscriber_id ORDER BY volume_gb DESC LIMIT 10")
MEETING_BLOCKED = (f"SELECT requested_server AS website, COUNT(*) AS blocked_connections FROM wms_dist.ntc_ssl_log "
                   f"WHERE {WEEK} AND block_type > 0 GROUP BY website ORDER BY blocked_connections DESC LIMIT 10")
VK_TOP = (f"SELECT subscriber_id, COUNT(*) AS connections FROM wms_dist.ntc_ssl_log WHERE {WEEK} "
          f"AND requested_server = 'api.vk.ru' GROUP BY subscriber_id ORDER BY connections DESC LIMIT 1")
RIGHT_NAYATEL = (f"SELECT subscriber_id, round(sum(c2s_byte_num + s2c_byte_num) / 1e9, 3) AS volume_gb, count() AS "
                 f"connections FROM wms_dist.ntc_ssl_log WHERE {WEEK} AND position(subscriber_id, '_') = 0 AND "
                 f"client_location = 'Islamabad Pakistan' AND client_asn IN ('Nayatel Pvt Ltd') AND {WHATSAPP} "
                 f"GROUP BY subscriber_id ORDER BY volume_gb DESC LIMIT 10")
PROFILE = (f"SELECT subscriber_id, any(loc) AS address, any(client_asn) AS isp FROM wms_dist.ntc_ssl_log WHERE {WEEK} "
           f"AND subscriber_id IN ('923869624946') GROUP BY subscriber_id")


def turns_after(title, sql, columns, rows, question="earlier question"):
    return [{"role": "user", "kind": "question", "content": question},
            {"role": "assistant", "kind": "result", "id": "a1", "content": "",
             "data": {"title": title, "sql": sql, "understood": title,
                      "result": {"columns": [{"name": c, "type": "String"} for c in columns], "rows": rows,
                                 "row_count": len(rows)}}}]


VK_TURNS = turns_after("Top user who accessed api.vk.ru", VK_TOP, ["subscriber_id", "connections"],
                       [["923869624946", 13]], "show me the top user who accessed api.vk.ru")
TOP10_TURNS = turns_after("Top 10 WhatsApp users in Islamabad", RIGHT_NAYATEL, ["subscriber_id", "volume_gb", "connections"],
                          [["923500081495", 0.09, 60], ["729578wqxgk", 0.08, 50], ["03717487713", 0.08, 44]])


@pytest.mark.parametrize("question, isp", [
    ("show me top 10 whatsapp nayatel users from islamabad", "Nayatel"),
    ("show me the top ten ptcl users by traffic volumne", "PTCL"),
    ("PTCL ke top users", "PTCL"),
    ("evo users in karachi", "PTCL"),
    ("storm fiber customers lahore", "StormFiber"),
    ("stormfiber pe sab se zyada youtube", "StormFiber"),
    ("zong users count", "Zong"),
    ("how many jazz users yesterday", "Jazz"),
    ("transworld traffic", "Transworld"),
    ("naya tel isb", "Nayatel"),
])
def test_isp_names_resolve_to_client_asn(question, isp):
    found = dict(context.operators(question))
    assert isp in found and found[isp].startswith("client_asn IN (")
    assert any(line.startswith(f"{isp} (ISP") for line in agent.resolved_names(question, RANGE))


def test_jazzcash_is_an_app_not_jazz():
    assert context.operators("jazzcash app users") == []


def test_meeting_ptcl_answer_is_sent_back():
    msg = agent.isp_missing("show me the top ten ptcl users by traffic volumne", MEETING_PTCL)
    assert msg and "Pakistan Telecommunication Company Limited" in msg


def test_meeting_nayatel_answer_is_sent_back():
    q = "show me top 10 whatsapp nayatel users from islamabad"
    assert "Nayatel" in agent.isp_missing(q, MEETING_NAYATEL)
    assert agent.isp_missing(q, RIGHT_NAYATEL) is None


@pytest.mark.parametrize("question", [
    "show me top 10 whatsapp nayatel users from islamabad", "top ten ptcl users by traffic volumne",
    "heavy users of tiktok in lahore", "who used nordvpn yesterday", "which users accessed api.vk.ru",
    "kon se users sab se zyada youtube chalate hain", "top 5 subscribers of zong", "list of users using telegram",
    "top 10 numbers by data in karachi", "biggest consumers on storm fiber", "kis ne sab se zyada facebook use kiya",
    "top 10 heavy downloaders", "top 10 whatsapp users by connections",
])
def test_questions_about_people(question):
    assert agent.wants_subscriber_list(question)


@pytest.mark.parametrize("question", [
    "top 10 apps by users", "how many users use whatsapp", "top cities by users", "top apps in karachi",
    "most popular app among ptcl users", "how many nayatel users in isb", "users of whatsapp per city",
])
def test_questions_about_things(question):
    assert not agent.wants_subscriber_list(question)


def test_user_count_per_subscriber_is_sent_back():
    msg = agent.subscriber_shape("show me top 10 whatsapp nayatel users from islamabad", MEETING_NAYATEL)
    assert msg and "always 1" in msg


def test_unidentified_rows_in_a_people_list_are_sent_back():
    msg = agent.subscriber_shape("show me the top user who accessed api.vk.ru", VK_TOP)
    assert msg and "position(subscriber_id, '_') = 0" in msg


def test_people_question_answered_with_a_count_is_sent_back():
    count = f"SELECT uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS users FROM wms_dist.ntc_ssl_log WHERE {WEEK}"
    assert "one row per subscriber" in agent.subscriber_shape("top 10 tiktok users in lahore", count)


def test_right_people_list_passes():
    assert agent.subscriber_shape("show me top 10 whatsapp nayatel users from islamabad", RIGHT_NAYATEL) is None


def test_people_list_measure_follows_the_question():
    assert "ORDER BY sum(c2s_byte_num + s2c_byte_num) DESC" in agent.shape_hints("top 10 whatsapp users")[0]
    assert "ORDER BY connections" in agent.shape_hints("top 10 whatsapp users by connections")[0]


@pytest.mark.parametrize("question", [
    "show me the address and isp name of this user", "is user ka address kya hai", "inka isp batao",
    "what is the imei of this number", "where does this user live", "this guy's location",
])
def test_reference_to_one_user(question):
    refs = context.references(question, VK_TURNS)
    assert refs and refs[0]["column"] == "subscriber_id" and refs[0]["values"] == ["923869624946"]


@pytest.mark.parametrize("question", [
    "show me their addresses", "also show me their corresponding traffic volume", "these users ki ISP",
    "un users ka address", "what apps do these numbers use",
])
def test_reference_to_every_row(question):
    refs = context.references(question, TOP10_TURNS)
    assert refs and refs[0]["values"] == ["923500081495", "729578wqxgk", "03717487713"]


def test_no_reference_without_earlier_rows():
    assert context.references("top apps in karachi", TOP10_TURNS) == []
    assert context.references("show me their addresses", []) == []


def test_unidentified_ids_are_never_referenced():
    turns = turns_after("x", VK_TOP, ["subscriber_id", "connections"], [["100.65.1.2_3456", 1], ["AJR37134768", 1]])
    assert context.references("their addresses", turns)[0]["values"] == ["AJR37134768"]


def test_reference_through_an_alias():
    turns = turns_after("x", MEETING_PTCL, ["user", "volume_gb"], [["923001112223", 1.2]])
    assert context.references("show me this user's address", turns)[0]["values"] == ["923001112223"]


def test_meeting_follow_up_answer_is_sent_back():
    msg = agent.reference_dropped("show me the address and isp name of this user", MEETING_BLOCKED, VK_TURNS)
    assert msg and "'923869624946'" in msg
    assert agent.reference_dropped("show me the address and isp name of this user", PROFILE, VK_TURNS) is None


def test_follow_up_keeps_the_earlier_period():
    assert agent.question_period("show me their addresses", RANGE) is None


def test_meeting_follow_up_end_to_end(monkeypatch):
    session = FakeSession([
        Turn(text="", tool_calls=[call("submit_answer", plan(sql="", status="clarify",
                                                    message="Please provide the subscriber ID."))], stop="tool_use"),
        Turn(text="", tool_calls=[call("submit_answer", plan(sql=MEETING_BLOCKED))], stop="tool_use"),
        Turn(text="", tool_calls=[call("submit_answer", plan(sql=PROFILE, understood="Address and ISP of 923869624946"))],
             stop="tool_use"),
    ])
    sent = {}

    def open_session(cfg, system, tools, user):
        sent["user"] = user
        return session

    class DB:
        def cached_query(self, sql, max_rows=None, params=None, ttl=600):
            return QueryResult([{"name": "subscriber_id", "type": "String"}, {"name": "address", "type": "String"},
                                {"name": "isp", "type": "String"}],
                               [["923869624946", "House 1344, Street 2, Sector C, Multan Pakistan", "PMCL LDI IP TRANSIT"]],
                               0.1, 1000, 0)

    monkeypatch.setattr(agent.llm, "open_session", open_session)
    monkeypatch.setattr(agent, "db", DB())
    ans = agent.answer("show me the address and isp name of this user", VK_TURNS, CFG, RANGE, lambda s: None)
    assert "subscriber_id IN ('923869624946')" in sent["user"]
    assert ans.plan["status"] == "ok" and "923869624946" in ans.sql
    notes = [text for results in session.tool_results for _, text, _ in results]
    assert any("without asking the user" in n for n in notes)
    assert any("rows of an earlier answer" in n for n in notes)


def test_phone_number_matches_both_stored_forms():
    assert context.phone_sql(context.phones("what did 0335-3016943 do")) == \
        "subscriber_id IN ('923353016943', '03353016943')"
