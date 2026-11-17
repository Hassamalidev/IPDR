import pytest

from ipdr import agent, catalog, retrieval, store

WEEK = ("start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') AND "
        "start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")


def kinds(q):
    return [(e.kind, e.name) for e in catalog.entities(q)]


@pytest.mark.parametrize("question, expected", [
    ("show me top 10 whatsapp nayatel users from islamabad", [("isp", "Nayatel"), ("city", "Islamabad")]),
    ("users in sadiqabad", [("city", "S\u0101diq\u0101b\u0101d")]),
    ("khushab users", [("city", "Kh\u016bsh\u0101b")]),
    ("users from wah cantt", [("city", "Wah Cantt")]),
    ("gaming in multan", [("city", "Multan")]),
    ("model town lahore top apps", [("city", "Lahore"), ("area", "Model Town")]),
    ("mall road users", [("road", "Mall Road")]),
    ("fiber beam users", [("isp", "Fiber Beam")]),
    ("traffic to russian servers", [("server_country", "Russia")]),
    ("google servers traffic", [("server_org", "Google LLC")]),
    ("facebook servers in ireland", [("server_country", "Ireland")]),
    ("who opened api.vk.ru yesterday", [("website", "api.vk.ru")]),
    ("what did 0335-3016943 do", [("subscriber", "0335-3016943")]),
    ("100.65.228.1 activity", [("ip", "100.65.228.1")]),
    ("IMEI 990000008920979", [("imei", "990000008920979")]),
    ("imsi 410061861664459 location", [("imsi", "410061861664459")]),
    ("AJR37134768 address", [("subscriber", "AJR37134768")]),
    ("100.65.228.1_30349 details", [("subscriber", "100.65.228.1_30349")]),
    ("top apps in pakistan", []),
    ("top 10 apps by users", []),
    ("whatsapp usage", []),
])
def test_entities(question, expected):
    assert kinds(question) == expected


def test_public_ip_is_a_server():
    assert catalog.entities("who connects to 142.251.15.1")[0].sql == "server_ip = '142.251.15.1'"


def test_cgnat_ip_is_a_subscriber():
    assert catalog.entities("who had 100.64.55.35")[0].sql == "client_ip = '100.64.55.35'"


def test_unsure_names_are_not_required():
    assert [e.sure for e in catalog.entities("punjab users")] == [False]


def test_missing_name_is_sent_back():
    sql = f"SELECT count() FROM wms_dist.ntc_ssl_log WHERE {WEEK}"
    msg = agent.entity_missing("traffic to russian servers", sql)
    assert msg and "endsWith(server_location, 'Russia')" in msg
    assert agent.entity_missing("traffic to russian servers", sql + " AND endsWith(server_location, 'Russia')") is None
    assert "client_asn IN ('Fiber Beam Pvt Limited')" in agent.entity_missing("fiber beam users", sql)
    assert "'03353016943'" in agent.entity_missing("what did 0335-3016943 do", sql)
    assert agent.entity_missing("what did 0335-3016943 do", sql + " AND subscriber_id = '923353016943'") is None


def test_karachi_in_the_time_zone_is_not_a_filter():
    sql = f"SELECT app_label, count() FROM wms_dist.ntc_ssl_log WHERE {WEEK} GROUP BY app_label"
    assert agent.place_mismatch("top apps in karachi", sql)


@pytest.mark.parametrize("question, pattern", [
    ("top 10 nayatel users in lahore", "Nayatel users"),
    ("who visited telegram.org most", "accessed api.vk.ru"),
    ("show me 03001234567 activity yesterday", "923869624946"),
    ("servers in germany traffic", "servers in Russia"),
    ("who had ip 100.66.1.5", "IP 100.64.55.35"),
    ("address of this user", "address and ISP"),
])
def test_examples_found_by_shape(question, pattern):
    store.init()
    retrieval.seed_builtin_examples()
    assert any(pattern in e["question"] for e in retrieval.similar(question, k=3))


def test_find_values_ignores_accents():
    assert catalog.values_like("client_location", "sadiqabad") == ["S\u0101diq\u0101b\u0101d Pakistan"]


@pytest.mark.parametrize("question, measure", [
    ("top uploaders in lahore", "sum(c2s_byte_num)"), ("heavy downloaders", "sum(s2c_byte_num)"),
    ("top users by connections", "connections"), ("who accessed api.vk.ru most", "connections"),
    ("top whatsapp users", "sum(c2s_byte_num + s2c_byte_num)"),
])
def test_people_list_measure(question, measure):
    assert f"ORDER BY {measure} DESC" in agent.shape_hints(question)[0]


def test_list_columns_are_not_charted():
    from ipdr import charts
    cols = [{"name": "subscriber_id", "type": "String"}, {"name": "connections", "type": "UInt64"},
            {"name": "example_sites", "type": "Array(String)"}]
    out = charts.suggest(cols, [["9230", 5, ["a.com", "b.com"]], ["AJR1", 3, ["c.com"]]])
    assert out["options"] and all("example_sites" not in (o["x"], o["y"], o["series"]) for o in out["options"])


R = {"first": "2026-10-11 05:00:00", "last": "2026-11-10 09:40:00", "last_full_day": "2026-11-09"}


def test_urdu_city_and_ranking():
    from ipdr import urdu
    q = urdu.with_latin("\u0644\u0627\u06c1\u0648\u0631 \u0645\u06cc\u06ba \u0633\u0628 \u0633\u06d2 \u0632\u06cc\u0627\u062f\u06c1 \u0627\u0633\u062a\u0639\u0645\u0627\u0644 \u06c1\u0648\u0646\u06d2 \u0648\u0627\u0644\u06cc \u0627\u06cc\u067e\u0633")
    assert "lahore" in q and "most" in q and "apps" in q
    assert any(line.startswith("Lahore (city)") for line in agent.resolved_names(q, R))


def test_urdu_isp_top_users_are_people():
    from ipdr import urdu
    q = urdu.with_latin("\u0646\u06cc\u0627 \u0679\u06cc\u0644 \u06a9\u06d2 \u0679\u0627\u067e 10 \u0635\u0627\u0631\u0641\u06cc\u0646")
    assert agent.wants_subscriber_list(q)
    assert any("Nayatel Pvt Ltd" in line for line in agent.resolved_names(q, R))


def test_urdu_total_is_not_yesterday():
    from ipdr import urdu
    assert "yesterday" not in urdu.to_latin("\u06a9\u0644 \u0635\u0627\u0631\u0641\u06cc\u0646 \u06a9\u062a\u0646\u06d2 \u06c1\u06cc\u06ba")
    assert "yesterday" in urdu.to_latin("\u06a9\u0644 \u0627\u0633\u0644\u0627\u0645 \u0622\u0628\u0627\u062f \u0645\u06cc\u06ba")


def test_english_questions_are_unchanged():
    from ipdr import urdu
    assert urdu.with_latin("top apps in karachi") == "top apps in karachi"


VK_TOP = """SELECT subscriber_id,
       round(sum(c2s_byte_num + s2c_byte_num) / 1e9, 2) AS volume_gb,
       count() AS connections
FROM wms_dist.ntc_ssl_log
WHERE start_time >= 1 AND requested_server = 'api.vk.ru'
GROUP BY subscriber_id
ORDER BY volume_gb DESC
LIMIT 10"""


def test_order_uses_the_unrounded_value():
    fixed = agent.fix_rounded_order(VK_TOP)
    assert "ORDER BY (sum(c2s_byte_num + s2c_byte_num) / 1e9) DESC" in fixed
    assert "round(sum(c2s_byte_num + s2c_byte_num) / 1e9, 2) AS volume_gb" in fixed
    assert agent.fix_rounded_order("SELECT app, count() AS c FROM t GROUP BY app ORDER BY c DESC") is None
    window = "SELECT isp, round(100 * sum(b) / sum(sum(b)) OVER (), 1) AS share_pct FROM t GROUP BY isp ORDER BY share_pct DESC"
    assert agent.fix_rounded_order(window) is None


def test_a_column_of_zeros_is_sent_back():
    from ipdr.db import QueryResult
    cols = [{"name": "subscriber_id", "type": "String"}, {"name": "volume_gb", "type": "Float64"},
            {"name": "connections", "type": "UInt64"}]
    zeros = QueryResult(cols, [["a", 0.0, 13], ["b", 0.0, 12], ["c", 0.01, 9]], 0.1, 1, 0)
    assert "KB" in agent.rounded_to_zero(zeros)
    fine = QueryResult(cols, [["a", 1.2, 13], ["b", 0.9, 12], ["c", 0.0, 9]], 0.1, 1, 0)
    assert agent.rounded_to_zero(fine) is None


@pytest.mark.parametrize("code", ["AJR37134768", "BZ58568609", "GBQJ05629017", "acct56321665", "user11433910",
                                  "fiber45060543", "bb90958732", "292022gyxuk", "103046pkm", "390059sgddgnu"])
def test_every_account_code_style(code):
    assert kinds(f"address of {code}") == [("subscriber", code)]


APPS = ("SELECT app_label AS app, uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS users, count() AS connections "
        "FROM t WHERE x GROUP BY app ORDER BY users DESC, connections DESC LIMIT 20")
VK_BY_CONNECTIONS = ("SELECT subscriber_id, count() AS connections, round(sum(c2s_byte_num + s2c_byte_num) / 1e3, 1) AS volume_kb "
                     "FROM t WHERE x GROUP BY subscriber_id ORDER BY connections DESC LIMIT 20")


@pytest.mark.parametrize("question, sql, problem", [
    ("top 10 users of api.vk.ru as usage volume", VK_BY_CONNECTIONS, "data volume"),
    ("top 10 users of api.vk.ru as usage volume", VK_BY_CONNECTIONS, "LIMIT 10"),
    ("most used apps by data", APPS, "data volume"),
    ("which app has the most connections", APPS, "ORDER BY count()"),
    ("top ten apps", APPS, "LIMIT 10"),
])
def test_ranking_follows_the_measure_and_number_asked(question, sql, problem):
    assert problem in agent.measure_or_limit_mismatch(question, sql)


def test_default_ranking_is_left_alone():
    assert agent.measure_or_limit_mismatch("top apps in karachi", APPS) is None
    ok = VK_BY_CONNECTIONS.replace("ORDER BY connections DESC LIMIT 20", "ORDER BY sum(c2s_byte_num + s2c_byte_num) DESC LIMIT 10")
    assert agent.measure_or_limit_mismatch("top 10 users of api.vk.ru as usage volume", ok) is None


def test_values_below_one_ask_for_a_smaller_unit():
    from ipdr.db import QueryResult
    cols = [{"name": "subscriber_id", "type": "String"}, {"name": "volume_mb", "type": "Float64"}]
    small = QueryResult(cols, [["504516meat", 0.07], ["923126874001", 0.06], ["923860024909", 0.06]], 0.1, 1, 0)
    assert "KB" in agent.rounded_to_zero(small)
    fine = QueryResult(cols, [["a", 89.33], ["b", 0.4]], 0.1, 1, 0)
    assert agent.rounded_to_zero(fine) is None


def test_limit_one_becomes_top_ten():
    sql = "SELECT toHour(x) AS h, count() AS c FROM t WHERE y GROUP BY h ORDER BY c DESC LIMIT 1"
    assert agent.fix_limit_one(sql, "peak hour for youtube").endswith("LIMIT 10")
    assert agent.fix_limit_one(sql, "only the top one hour") is None
    assert agent.fix_limit_one("SELECT subscriber_id, any(loc) FROM t WHERE y GROUP BY subscriber_id LIMIT 1") is None


def test_empty_group_in_a_ranking_is_sent_back():
    from ipdr.db import QueryResult
    cols = [{"name": "website", "type": "String"}, {"name": "connections", "type": "UInt64"}]
    assert agent.empty_group_in_ranking(QueryResult(cols, [["", 900], ["api.vk.ru", 800]], 0.1, 1, 0))
    assert agent.empty_group_in_ranking(QueryResult(cols, [["telegram.org", 900], ["api.vk.ru", 800]], 0.1, 1, 0)) is None


def test_partial_days_are_explained():
    from ipdr.db import QueryResult
    cols = [{"name": "day", "type": "Date"}, {"name": "volume_gb", "type": "Float64"}]
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59", "last_full_day": "2026-11-09"}
    res = QueryResult(cols, [["2026-10-11", 10.0], ["2026-10-12", 13.0]], 0.1, 1, 0)
    assert "2026-10-11 (data starts at 09:41)" in agent.note_partial_days({"explanation": "x"}, res, rng)["explanation"]
    full = QueryResult(cols, [["2026-10-12", 13.0]], 0.1, 1, 0)
    assert agent.note_partial_days({"explanation": "x"}, full, rng)["explanation"] == "x"


def test_users_as_subject_is_not_a_measure():
    sql = "SELECT app_label AS app, count() AS connections FROM t WHERE x GROUP BY app ORDER BY connections DESC LIMIT 20"
    assert agent.measure_or_limit_mismatch("what apps do these users use most", sql) is None
    assert "users" in agent.measure_or_limit_mismatch("top apps by users", sql)


def test_no_city_question_back_when_no_place_named():
    assert agent._lazy_clarify("download volume by isp yesterday",
                               {"message": "Please confirm if you meant Karachi or another city.", "understood": "x"})


def test_a_failing_check_never_breaks_the_answer():
    def broken(*_):
        raise KeyError("users")
    assert agent._safe(broken, "q") is None


def _res(cols, rows):
    from ipdr.db import QueryResult
    return QueryResult([{"name": n, "type": t} for n, t in cols], rows, 0.1, 1, 0)


def test_total_answered_with_a_list_is_sent_back():
    r = _res([("app_label", "String"), ("volume_gb", "Float64")], [["SSL", 87.4], ["TikTok", 90.5]])
    assert agent.total_as_list("total data used by nayatel users last week", r)
    assert agent.total_as_list("total data by app for nayatel users", r) is None


def test_share_of_itself_is_sent_back():
    r = _res([("share_pct", "Float64")], [[100.0]])
    assert "sumIf" in agent.share_of_itself("zong ka karachi ke data me kitna hissa hai", "x", r)
    assert agent.share_of_itself("zong ka hissa", "x", _res([("share_pct", "Float64")], [[15.9]])) is None


def test_identified_filter_dropping_traffic_is_sent_back():
    sql = ("SELECT app_label AS app, round(sum(c2s_byte_num) / 1e6, 2) AS upload_mb FROM t WHERE start_time >= 1 "
           "AND position(subscriber_id, '_') = 0 GROUP BY app_label ORDER BY sum(c2s_byte_num) DESC LIMIT 5")
    assert agent.identified_filter_on_traffic("top 5 apps by upload in islamabad", sql)
    assert agent.identified_filter_on_traffic("average data per user in lahore", sql) is None
    assert agent.identified_filter_on_traffic("top uploaders in islamabad", sql) is None


def test_app_name_near_servers_stays_an_app():
    assert kinds("which countries host the servers tiktok uses most") == []


def test_isp_brand_split_is_sent_back():
    r = _res([("isp", "String"), ("users", "UInt64")],
             [["Pakistan Telecommunication Company Limited", 9], ["CMPak Limited", 8], ["Paknet Limited Merged into PTCL", 1]])
    assert "PTCL" in agent.isp_split_in_ranking(r)
    assert agent.isp_split_in_ranking(_res([("isp", "String"), ("users", "UInt64")], [["PTCL", 9], ["CMPak Limited", 8]])) is None


def test_ip_is_not_a_date():
    p = agent.question_period("who had ip 100.64.55.35", R)
    assert p and p[0].startswith("no period")
    assert agent.question_period("traffic to graph.facebook.com", R)[0].startswith("no period")
    assert agent.question_period("users on 5.11", R) is None


def test_people_with_a_condition_counted_not_listed():
    r = _res([("whatsapp_connections", "UInt64"), ("imo_connections", "UInt64")], [[42, 17], [47, 14]])
    assert "count() AS users FROM (" in agent.total_as_list("how many subscribers used both whatsapp and imo last week", r)


def test_imo_is_an_app_with_all_its_labels():
    from ipdr import concepts
    assert "imo" in concepts.apps_in("both whatsapp and imo")
    assert "IMO_VoIP" in agent.app_labels_incomplete("whatsapp and imo users", "SELECT 1 FROM t WHERE app_label = 'IMO'")
    assert agent.app_labels_incomplete("imo users", "SELECT 1 FROM t WHERE app_label IN ('IMO', 'IMO_VoIP')") is None
    assert concepts.apps_in("lol that was funny") == []


def test_raw_user_counts_are_rewritten():
    sql = "SELECT COUNT(DISTINCT subscriber_id) AS s FROM t WHERE start_time >= 1 AND client_location = 'Multan Pakistan'"
    assert "uniqExactIf(subscriber_id, position(subscriber_id, '_') = 0) AS s" in agent.fix_user_count(sql)
    ok = "SELECT uniq(subscriber_id) FROM t WHERE start_time >= 1 AND position(subscriber_id, '_') = 0"
    assert "uniqExact(subscriber_id)" in agent.fix_user_count(ok)
    assert agent.fix_user_count("SELECT uniqExactIf(subscriber_id, position(subscriber_id, '_') = 0) FROM t WHERE x") is None


def test_weekday_counts_back_from_the_data():
    assert str(agent.question_period("How much data did Islamabad use last Saturday, in GB?", R)[1]) == "2026-11-07"
    assert str(agent.question_period("itwar ko lahore me tiktok", R)[1]) == "2026-11-08"


def test_dates_outside_the_data_are_sent_back():
    late = ("WHERE start_time >= toUnixTimestamp('2026-11-12 00:00:00', 'Asia/Karachi') "
            "AND start_time < toUnixTimestamp('2026-11-13 00:00:00', 'Asia/Karachi')")
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:00", "last_full_day": "2026-11-09"}
    assert agent.period_outside_data(late, rng)


def test_imei_question_is_not_refused():
    assert agent.missing_in("Which numbers used the handset with IMEI 990000008920979 last week?") == []
    assert agent.missing_in("which device model is most used") == ["device model"]


def test_other_than_is_an_exclusion():
    assert not agent._ASKS_OTHER.search("which messaging apps other than whatsapp had the most users")


def test_not_using_is_decided_per_person():
    bad = "SELECT uniqIf(subscriber_id, position(subscriber_id, '_') = 0) FROM t WHERE x AND app_label NOT IN ('NordVPN')"
    assert agent.negation_as_row_filter("How many Karachi subscribers did NOT use any VPN last week?", bad)
    assert agent.negation_as_row_filter("top apps except social media", bad) is None


def test_this_app_follow_up():
    from ipdr import context
    turns = [{"role": "user", "kind": "question", "content": "top app in karachi"},
             {"role": "assistant", "kind": "result", "data": {
                 "title": "Top apps", "sql": "SELECT app_label AS app, count() AS c FROM t GROUP BY app",
                 "result": {"columns": [{"name": "app"}, {"name": "c"}], "rows": [["TikTok", 9], ["Facebook", 8]]}}}]
    line = context.reference_lines(context.references("is app ke Karachi mein top 5 users kaun hain?", turns))[0]
    assert "'TikTok_Upload'" in line and "one row per" not in line


def test_typos_and_foreign_places():
    assert kinds("avg youtube watch time per user in islambad yesterday") == [("city", "Islamabad")]
    assert kinds("nayatell users in rawalpindi") == [("isp", "Nayatel"), ("city", "Rawalpindi")]
    assert kinds("which subscribers connected from kabul") == [("place", "Kabul Afghanistan")]
    assert kinds("share of whatsapp") == []


def test_named_app_beats_its_kind():
    from ipdr import concepts
    assert concepts.match("PUBG connections at night") == []
    assert [c["key"] for c in concepts.match("pubg and other games")] == ["gaming"]


def test_app_calls_use_the_call_label():
    lines = agent.resolved_names("Daily WhatsApp call minutes in Karachi", R)
    assert any("app_label IN ('WhatsApp_VoIP')" in line for line in lines)
    assert agent.concept_incomplete("whatsapp calls in karachi", "SELECT 1 FROM t WHERE app_label IN ('WhatsApp_VoIP')") is None


def test_app_named_but_not_filtered():
    assert agent.app_ignored("TikTok ka download, upload se kitne guna zyada tha",
                             "SELECT app_label AS app, count() FROM t WHERE x GROUP BY app_label")


def test_per_user_average_uses_identified_traffic():
    sql = ("SELECT sum(s2c_byte_num) / uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS a FROM t "
           "WHERE start_time >= 1")
    assert agent.per_user_with_unidentified("average download per subscriber", sql)
    assert agent.per_user_with_unidentified("average download per subscriber",
                                            sql + " AND position(subscriber_id, '_') = 0") is None


def test_average_per_user_is_one_number():
    r = _res([("app", "String"), ("v", "Float64")], [["a", 1.0], ["b", 2.0]])
    assert agent.total_as_list("avg youtube watch time per user in islamabad yesterday", r)
    assert agent.total_as_list("average data per city", r) is None


def test_average_question_gets_an_average():
    totals = ("SELECT round(sum(c2s_byte_num + s2c_byte_num) / 1e6, 2) AS v, "
              "uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS u FROM t WHERE x")
    msg = agent.average_missing("What's the average Netflix data per user on Telenor last week, in MB?", totals)
    assert "AS mb_per_user" in msg
    assert "AS hours_per_user" in agent.average_formula("avg youtube watch time per user in islamabad, in hours")
    one = _res([("users", "UInt64")], [[24292]])
    assert agent.ranking_as_total("avg youtube watch time per user in islamabad yesterday", one) is None


def test_filter_copied_from_an_example_is_sent_back():
    from ipdr import concepts
    vpn = concepts.sql_list("vpn_proxy")
    sql = (f"SELECT count() FROM (SELECT subscriber_id FROM t WHERE x AND (app_label LIKE 'TikTok%' OR app_label IN {vpn}) "
           "GROUP BY subscriber_id)")
    assert "vpn" in agent.unasked_app_filter("TikTok download vs upload ratio last week", sql)
    assert agent.unasked_app_filter("users who use both tiktok and vpn", sql) is None
    assert agent.unasked_app_filter("top apps in karachi",
                                    "SELECT app_label FROM t WHERE x AND app_label NOT IN ('SSL', 'GoogleAPI')") is None


def _turns(q, sql, cols=None, rows=None):
    return [{"role": "user", "kind": "question", "content": q},
            {"role": "assistant", "kind": "result", "data": {"title": "x", "sql": sql, "result": {
                "columns": [{"name": c} for c in (cols or [])], "rows": rows or []}}}]


def test_follow_up_keeps_the_earlier_filters():
    t = _turns("how many of them were in karachi", "SELECT uniqIf(subscriber_id, position(subscriber_id, '_') = 0) FROM t "
               "WHERE start_time >= 1 AND app_label IN ('TikTok') AND client_location = 'Karachi Pakistan'")
    assert "drops its filters" in agent.follow_up_dropped_filters(
        "how much data did they use", "SELECT sum(c2s_byte_num) FROM t WHERE start_time >= 1", t)
    ny = _turns("nayatel data yesterday", "SELECT sum(c2s_byte_num) FROM t WHERE start_time >= 1 AND client_asn IN ('Nayatel Pvt Ltd')")
    assert "still keeps the old value" in agent.follow_up_dropped_filters(
        "now only zong", "SELECT sum(c2s_byte_num) FROM t WHERE start_time >= 1 AND client_asn IN ('Nayatel Pvt Ltd', 'CMPak Limited')", ny)
    assert agent.follow_up_dropped_filters("top apps in lahore last week", "SELECT app_label FROM t WHERE start_time >= 1 "
                                           "AND client_location = 'Lahore Pakistan'", t) is None


def test_most_used_without_a_measure_uses_the_default():
    vol = ("SELECT app_label AS vpn, round(sum(c2s_byte_num + s2c_byte_num) / 1e9, 2) AS volume_gb FROM t WHERE x "
           "GROUP BY app_label ORDER BY (sum(c2s_byte_num + s2c_byte_num) / 1e9) DESC LIMIT 5")
    assert agent.default_ranking_measure("which vpn is used the most", vol)
    assert agent.default_ranking_measure("show me the top 5", vol, _turns("which vpn is used the most", "x"))
    assert agent.default_ranking_measure("top 5 apps in karachi by data", vol) is None
    assert agent.default_ranking_measure("show me the top ten blocked websites", vol) is None


def test_brand_name_in_a_filter_becomes_stored_names():
    fixed = agent.fix_brand_values("SELECT 1 FROM t WHERE x AND client_asn IN ('PTCL') AND y")
    assert "'Paknet Limited Merged into PTCL'" in fixed and "'PTCL'" not in fixed


def test_pronouns_and_singular_references():
    from ipdr import context
    users = _turns("top 10 snapchat users", "SELECT subscriber_id, count() AS c FROM t GROUP BY subscriber_id",
                   ["subscriber_id", "c"], [["923001112223", 5], ["AJR37134768", 4]])
    assert context.references("which isp are they on", users)[0]["values"] == ["923001112223", "AJR37134768"]
    vpns = _turns("which vpn is used most", "SELECT app_label AS vpn, count() AS c FROM t GROUP BY app_label",
                  ["vpn", "c"], [["NordVPN", 9], ["ExpressVPN", 8]])
    assert context.references("show me its top 5 users", vpns)[0]["values"] == ["NordVPN"]
    sites = _turns("which websites did he visit", "SELECT requested_server, count() AS c FROM t WHERE x AND "
                   "subscriber_id IN ('923353016943', '03353016943') GROUP BY requested_server",
                   ["requested_server", "c"], [["dns.adguard.com", 9]])
    ref = context.references("what is his address", sites)[0]
    assert ref["column"] == "subscriber_id" and "03353016943" in ref["values"]
    assert "drop those" in context.reference_lines([ref], profile=True)[0]


def test_follow_up_after_an_address_lookup_keeps_the_real_filters():
    turns = (_turns("top 10 whatsapp nayatel users from islamabad",
                    "SELECT subscriber_id FROM t WHERE start_time >= 1 AND client_asn IN ('Nayatel Pvt Ltd') "
                    "AND app_label IN ('WhatsAPP') GROUP BY subscriber_id")
             + _turns("show me their addresses", "SELECT subscriber_id, any(loc) FROM t WHERE start_time >= 1 "
                      "AND subscriber_id IN ('923114427776') GROUP BY subscriber_id"))
    msg = agent.follow_up_dropped_filters("also show me their corresponding traffic volume",
                                          "SELECT subscriber_id, sum(c2s_byte_num) FROM t WHERE start_time >= 1 AND "
                                          "subscriber_id IN ('923114427776') AND client_asn IN ('Nayatel Pvt Ltd') "
                                          "GROUP BY subscriber_id", turns)
    assert msg and "WhatsAPP" in msg


def test_ip_lookup_is_not_told_to_drop_unidentified():
    sql = "SELECT subscriber_id, count() AS c FROM t WHERE x AND client_ip = '100.64.55.35' GROUP BY subscriber_id"
    one = _res([("subscriber_id", "String"), ("c", "UInt64")], [["923759003077", 1407]])
    assert agent.subscriber_shape("who had the ip 100.64.55.35", sql, one) is None
    assert agent._lazy_clarify("who had the ip 100.64.55.35",
                               {"message": "The list includes unidentified records. Please specify.", "understood": "x"})


def test_lookup_keeps_empty_values():
    r = _res([("subscriber_id", "String"), ("isp", "String")], [["fiber97469890", ""], ["923552168851", "PTCL"]])
    sql = "SELECT subscriber_id, any(client_asn) AS isp FROM t WHERE x AND subscriber_id IN ('a') GROUP BY subscriber_id"
    assert agent.empty_group_in_ranking(r, sql) is None


def test_per_person_rows_allowed_for_they():
    t = _turns("top 10 users by upload in karachi", "SELECT subscriber_id, sum(c2s_byte_num) AS u FROM t GROUP BY subscriber_id",
               ["subscriber_id", "u"], [["923672900246", 9], ["923273540119", 8]])
    r = _res([("subscriber_id", "String"), ("download_mb", "Float64")], [["923672900246", 9.0], ["923273540119", 8.0]])
    assert agent.total_as_list("how much did they download", r, t) is None


def test_short_follow_up_measure_is_checked():
    t = _turns("top 5 cities by vpn users", "x")
    sql = "SELECT client_location, uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS users FROM t GROUP BY client_location ORDER BY users DESC LIMIT 5"
    assert "data volume" in agent.measure_or_limit_mismatch("now by data", sql, t)


def test_column_recognised_by_its_values():
    from ipdr import context
    t = _turns("which vpn has the most users", "SELECT multiIf(app_label IN ('NordVPN', 'X'), app_label, 'Other') AS vpn, "
               "count() AS c FROM t GROUP BY vpn", ["vpn", "c"], [["NordVPN", 9], ["iCloudPrivateRelay", 8]])
    assert context.references("show me its top 5 users", t)[0]["values"] == ["NordVPN"]


def test_placeholder_values_are_sent_back():
    assert agent.placeholder_in_sql("SELECT 1 FROM t WHERE subscriber_id IN ('<resolved_subscriber_id>')")
    assert agent.placeholder_in_sql("SELECT 1 FROM t WHERE client_location = 'Lahore Pakistan'") is None


def test_roman_urdu_demonstratives_are_not_english_words():
    from ipdr import context
    assert not context.refers_back("who is behind ip 100.66.12.7")
    assert not context.refers_back("users in lahore")
    assert context.refers_back("is user ka address kya hai")


def test_spelling_variants_of_app_names():
    from ipdr import concepts
    for q, app in [("tik tok users", "tiktok"), ("whats app calls", "whatsapp"), ("youtub data", "youtube"),
                   ("faceboook users", "facebook"), ("netflx users", "netflix")]:
        assert app in concepts.apps_in(q), q
    assert concepts.apps_in("top apps in karachi") == []


def test_clock_times_are_not_periods():
    p = agent.question_period("traffic between 6pm and 9pm in karachi", R)
    assert p and p[0].startswith("no period")


def test_ordinal_references():
    from ipdr import context
    people = _turns("top 10 tiktok users", "SELECT subscriber_id, sum(x) AS v FROM t GROUP BY subscriber_id",
                    ["subscriber_id", "v"], [["923001112223", 9], ["AJR37134768", 8], ["03111304023", 7]])
    sites = _turns("top websites", "SELECT requested_server AS website, count() AS c FROM t GROUP BY website",
                   ["website", "c"], [["dns.adguard.com", 9], ["graph.facebook.com", 8]])
    assert context.references("show me the first user's address", people)[0]["values"] == ["923001112223"]
    assert context.references("the last user's isp", people)[0]["values"] == ["03111304023"]
    assert context.references("who visited the second one the most", sites)[0]["values"] == ["graph.facebook.com"]
    assert context.references("top apps in lahore", people) == []


def test_conclusion_figures_in_other_byte_units():
    cand = agent._derivable_numbers([{"name": "app", "type": "String"}, {"name": "volume_gb", "type": "Float64"}],
                                    [["Facebook", 5763.51], ["TikTok", 2683.29]])
    assert agent.unverified_numbers("Facebook used 5.76 TB of data.", cand) == []
    assert agent.unverified_numbers("Facebook used 9.1 TB.", cand) == ["9.1 TB"]


def test_questions_are_not_small_talk():
    from ipdr import context
    t = [{"role": "user", "kind": "question", "content": "top apps"}, {"role": "assistant", "kind": "result", "id": "m1", "data": {}}]
    for q in ["help me find the top users of nayatel", "is it correct", "are you sure", "again", "why is it so low"]:
        assert context.small_talk(q, t) is None, q
    for q, kind in [("hi there", "hello"), ("help", "help"), ("what can you do", "help"), ("thanks", "thanks"), ("bye", "bye")]:
        assert context.small_talk(q, t) == kind, q


def test_no_part_of_whole_chart_for_negative_or_zero_values():
    from ipdr import charts
    cols = [{"name": "app", "type": "String"}, {"name": "change", "type": "Float64"}]
    for rows in ([["a", -5.0], ["b", 3.0]], [["a", 0.0], ["b", 0.0]], [["a", float("nan")], ["b", 1.0]]):
        assert not {o["type"] for o in charts.suggest(cols, rows)["options"]} & {"pie", "treemap", "pareto"}
    ok = charts.suggest([{"name": "app", "type": "String"}, {"name": "volume_gb", "type": "Float64"}],
                        [["a", 5.0], ["b", 3.0], ["c", 1.0], ["d", 0.5]])
    assert "pie" in {o["type"] for o in ok["options"]}


def test_examples_are_shown_as_patterns_without_names():
    from ipdr import context, concepts
    vpn = concepts.sql_list("vpn_proxy")
    q, s = context.template_example("Users who used both TikTok and VPN apps last week",
                                    "SELECT subscriber_id FROM t WHERE x AND client_location = 'Lahore Pakistan' AND "
                                    f"(app_label LIKE 'TikTok%' OR app_label IN {vpn}) AND app_label NOT IN ('SSL') GROUP BY subscriber_id")
    assert "TikTok" not in q and "VPN" not in q and "<app>" in q
    assert "NordVPN" not in s and "Lahore" not in s and "TikTok%" not in s
    assert "NOT IN ('SSL')" in s


def test_there_means_the_earlier_place():
    from ipdr import context
    t = _turns("which city has the most nordvpn users", "SELECT replaceOne(client_location, ' Pakistan', '') AS city, "
               "count() AS users FROM t GROUP BY city", ["city", "users"], [["Islamabad", 9], ["Lahore", 8]])
    line = context.reference_lines(context.references("show me the top 5 nordvpn users there", t))[0]
    assert "'Islamabad Pakistan'" in line and "Lahore" not in line
    assert not context.refers_back("is there any traffic to russia")
    assert not context.refers_back("are there users on nayatel")


def test_understood_says_exactly_what_ran():
    w = ("WHERE start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') AND "
         "start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")
    plan = agent.align_period_text({"understood": "Show the top ten blocked websites last week.", "assumptions": []},
                                   f"SELECT a FROM t {w} GROUP BY a ORDER BY c DESC LIMIT 10")
    assert plan["understood"] == "Show the top 10 blocked websites from 3 Nov to 9 Nov 2026."
    plan = agent.align_period_text({"understood": "How many users used TikTok in Karachi?", "assumptions": []},
                                   f"SELECT count() FROM t {w}")
    assert plan["understood"].endswith("from 3 Nov to 9 Nov 2026?")


def test_that_hour_and_that_day():
    from ipdr import context
    t = _turns("busiest hour for youtube", "SELECT toHour(toDateTime(start_time, 'Asia/Karachi')) AS hour_of_day, count() AS c "
               "FROM t GROUP BY hour_of_day ORDER BY c DESC", ["hour_of_day", "c"], [[20, 9], [19, 8]])
    assert "IN (20)" in context.reference_lines(context.references("how many users were watching at that hour", t))[0]
    assert context.references("show me their addresses", t) == []
    d = _turns("daily data", "SELECT toDate(toDateTime(start_time, 'Asia/Karachi')) AS day, sum(x) AS v FROM t GROUP BY day",
               ["day", "v"], [["2026-11-06", 9], ["2026-11-04", 8]])
    assert "'2026-11-06'" in context.reference_lines(context.references("top apps on that day", d))[0]


DAY_SQL = ("WHERE start_time >= toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi') AND "
           "start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")


def test_how_many_people_is_not_a_row_count():
    assert agent.rows_counted_as_people("how many of them are on ptcl",
                                        "SELECT count(*) AS c FROM t WHERE x AND subscriber_id IN ('a')")
    assert agent.rows_counted_as_people("how many connections were blocked", "SELECT count() FROM t WHERE x") is None


def test_follow_up_keeps_the_earlier_period():
    t = _turns("which city had the most blocked connections yesterday",
               f"SELECT client_location, count() FROM t {DAY_SQL} GROUP BY client_location",
               ["client_location", "c"], [["Islamabad Pakistan", 9]])
    week = WEEK.replace("start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi')",
                        "start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi')")
    assert agent.follow_up_period_changed("show me the blocked websites in that city", f"SELECT a FROM t WHERE {week}", t)
    assert agent.follow_up_period_changed("show me the blocked websites in that city", f"SELECT a FROM t {DAY_SQL}", t) is None
    assert any("same period as the earlier answer" in line
               for line in agent.resolved_names("show me the blocked websites in that city", R, t))


def test_no_false_alarms_on_correct_answers():
    from ipdr import concepts
    assert agent.average_missing("which internet providers carried the most traffic (I mean data, not number of people)",
                                 "SELECT client_asn, sum(c2s_byte_num) FROM t WHERE x GROUP BY client_asn") is None
    sql = ("SELECT client_asn AS isp, uniqIf(subscriber_id, position(subscriber_id, '_') = 0) AS users FROM t WHERE x "
           "GROUP BY isp HAVING uniqIf(subscriber_id, position(subscriber_id, '_') = 0) >= 1000 ORDER BY users DESC")
    assert agent.subscriber_shape("which isp has the highest average download per subscriber", sql) is None
    sm = concepts.sql_list("social_media")
    assert agent.unasked_app_filter("top 10 people by time spent on social media",
                                    f"SELECT subscriber_id FROM t WHERE x AND app_label IN {sm} GROUP BY subscriber_id") is None
    assert agent.identified_filter_on_traffic(
        "how long do people spend on youtube per day in lahore",
        "SELECT round(sum(duration) / 3.6e6, 2) AS h FROM t WHERE x AND position(subscriber_id, '_') = 0") is None
    t = _turns("top 10 whatsapp nayatel users from islamabad", "SELECT subscriber_id FROM t WHERE x AND client_location = "
               "'Islamabad Pakistan' AND client_asn IN ('Nayatel Pvt Ltd') GROUP BY subscriber_id",
               ["subscriber_id", "v"], [["923114427776", 9]])
    assert agent.follow_up_dropped_filters("show me their addresses", "SELECT subscriber_id, any(loc) FROM t WHERE x AND "
                                           "subscriber_id IN ('923114427776') GROUP BY subscriber_id", t) is None


def test_reference_to_a_brand_accepts_its_stored_names():
    t = _turns("which isp has the most users in lahore", "SELECT multiIf(client_asn IN ('Pakistan Telecommunication Company "
               "Limited'), 'PTCL', client_asn) AS isp, count() AS c FROM t GROUP BY isp", ["isp", "c"], [["PTCL", 9], ["CMPak Limited", 8]])
    sql = ("SELECT subscriber_id FROM t WHERE x AND client_asn IN ('Pakistan Telecommunication Company Limited', "
           "'Pakistan Telecom Company Limited', 'Paknet Limited Merged into PTCL') GROUP BY subscriber_id")
    assert agent.reference_dropped("show me the top 10 users of that isp in lahore", sql, t) is None


def test_understood_rewrite_edge_cases():
    w = ("SELECT a FROM t WHERE start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') AND start_time < "
         "toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi') GROUP BY a ORDER BY c DESC LIMIT 10")
    f = lambda u, s=w: agent.align_period_text({"understood": u, "title": "", "assumptions": []}, s)["understood"]
    assert f("Top websites in the last 7 full days from 3 Nov to 9 Nov 2026?") == "Top 10 websites from 3 Nov to 9 Nov 2026?"
    assert f("Which apps are used the most in the last 7 full days (last week)?") == "Which apps are used the most from 3 Nov to 9 Nov 2026?"
    assert f("Show the top user who accessed api.vk.ru last week.") == "Show the top user who accessed api.vk.ru from 3 Nov to 9 Nov 2026."


def test_per_user_column_must_divide_by_users():
    bad = ("SELECT toDate(toDateTime(start_time, 'Asia/Karachi')) AS day, round(sumIf(duration, position(subscriber_id, "
           "'_') = 0) / 6e4, 2) AS minutes_per_user FROM t WHERE x GROUP BY day")
    assert "is a total" in agent.average_missing("how much time do people spend on snapchat per day in karachi", bad)
    ok = ("SELECT round(sumIf(duration, position(subscriber_id, '_') = 0) / uniqExactIf(subscriber_id, "
          "position(subscriber_id, '_') = 0) / 6e4, 2) AS minutes_per_user FROM t WHERE x")
    assert agent.average_missing("how much time do people spend on snapchat per day in karachi", ok) is None


def test_lookup_fallback_when_the_model_fails(monkeypatch):
    from ipdr.db import QueryResult
    monkeypatch.setattr(agent, "db", type("DB", (), {"cached_query": lambda self, sql, **k: QueryResult(
        [{"name": "subscriber_id", "type": "String"}, {"name": "city", "type": "String"}], [["561411zkd", "Islamabad Pakistan"]],
        0, 0, 0)})())
    t = _turns("show me the top 10 zong users", f"SELECT subscriber_id, count() AS c FROM t WHERE {WEEK} GROUP BY subscriber_id",
               ["subscriber_id", "c"], [["561411zkd", 9]])
    plan, sql, result = agent.profile_fallback("which cities are they from", t, R)
    assert "subscriber_id IN ('561411zkd')" in sql and result.rows
