from ipdr import agent, catalog, concepts, context


def kinds(q):
    return [(e.kind, e.name) for e in catalog.entities(q)]


def test_in_total_is_not_a_server_owner():
    q = "How many packets were sent from servers to subscribers (downstream) yesterday in total?"
    assert not [k for k in kinds(q) if k[0] == "server_org"]


def test_connect_to_is_not_an_organisation():
    q = "Which places in the United States host the servers users connect to most? Top 5"
    ks = kinds(q)
    assert ("server_country", "United States") in ks
    assert not [k for k in ks if k[0] in ("server_org", "isp", "place")]


def test_mobile_country_code_is_not_about_servers():
    q = "How many connections yesterday came from SIM cards registered outside Pakistan (mobile country code other than 410)?"
    assert not [k for k in kinds(q) if k[0] in ("server_org", "server_country")]


def test_servers_in_pakistan_still_found():
    assert ("server_country", "Pakistan") in kinds("top 5 servers in pakistan by data")
    assert not [k for k in kinds("servers outside pakistan") if k[0] == "server_country"]


def test_real_organisations_still_found():
    assert ("server_org", "Amazon.com, Inc.") in kinds("traffic to amazon servers")
    assert ("isp", "Connect") in kinds("connect isp users in lahore")


def test_foreign_subscriber_place_without_server_talk():
    assert ("place", "United States") in kinds("connections from the united states yesterday")


def test_like_underscore_is_a_wildcard():
    assert agent.fix_user_count("SELECT x FROM t WHERE subscriber_id NOT LIKE '%_%'") == \
        "SELECT x FROM t WHERE position(subscriber_id, '_') = 0"
    assert "position(subscriber_id, '_') > 0" in agent.fix_user_count("SELECT x FROM t WHERE subscriber_id LIKE '%_%'")


def test_count_subscriber_id_counts_people():
    out = agent.fix_user_count("SELECT count(subscriber_id) AS n FROM t WHERE position(subscriber_id, '_') = 0")
    assert "uniqExact(subscriber_id) AS n" in out
    assert agent.fix_user_count("SELECT count(subscriber_id) AS connections FROM t") is None


def test_isp_ranking_grouped_by_brand():
    sql = ("SELECT client_asn AS ISP, count() AS connections FROM wms_dist.ntc_ssl_log WHERE start_time >= 1 "
           "AND client_asn != '' GROUP BY client_asn ORDER BY connections DESC LIMIT 10")
    out = agent.fix_isp_groups(sql)
    assert "'PTCL'" in out and "AS ISP" in out and "GROUP BY ISP" in out
    assert "client_asn != ''" in out
    by_alias = agent.fix_isp_groups("SELECT client_asn AS isp, count() c FROM t GROUP BY isp ORDER BY c DESC")
    assert "'PTCL'" in by_alias and by_alias.endswith("GROUP BY isp ORDER BY c DESC")


def test_isp_grouping_leaves_profiles_and_subqueries():
    assert agent.fix_isp_groups("SELECT subscriber_id, any(client_asn) FROM t GROUP BY subscriber_id") is None
    assert agent.fix_isp_groups("SELECT isp, n FROM (SELECT client_asn AS isp, count() n FROM t GROUP BY isp)") is None


def test_app_ranking_grouped_by_app():
    sql = ("SELECT app_label AS app, count() AS connections FROM t WHERE start_time >= 1 "
           "AND client_location = 'Multan Pakistan' GROUP BY app ORDER BY connections DESC LIMIT 5")
    out = agent.fix_app_groups(sql, "top 5 apps by connections in multan")
    assert "'YouTube'" in out and "'Youtube_QUIC_Video'" in out and out.endswith("GROUP BY app ORDER BY connections DESC LIMIT 5")


def test_app_grouping_keeps_one_apps_parts():
    one = "SELECT app_label, count() c FROM t WHERE app_label IN ('Youtube_QUIC_Video', 'Youtube_SSL_Video') GROUP BY app_label"
    assert agent.fix_app_groups(one, "youtube traffic") is None
    assert agent.fix_app_groups("SELECT app_label, count() c FROM t GROUP BY app_label", "traffic by app label") is None


def test_grouped_app_name_resolves_for_follow_ups():
    assert context._stored_values("app_label", ["YouTube"])[:2] == ["Youtube_QUIC_Video", "Youtube_SSL_Video"]


def test_sumif_with_only_a_condition_is_countif():
    out = agent.repair_measures("SELECT round(100 * sumIf(block_type > 0) / sum(block_type), 1) AS s, "
                                "sumIf(c2s_byte_num, x > 1) AS b FROM t WHERE a")
    assert "countIf(block_type > 0) / count()" in out and "sumIf(c2s_byte_num, x > 1)" in out


def test_named_subscriber_keeps_unknown_city():
    out = agent.repair_measures("SELECT any(loc) FROM t WHERE start_time >= 1 AND subscriber_id = 'JNQ29740014' "
                                "AND client_location NOT IN ('Pakistan', 'Punjab Pakistan', '') GROUP BY subscriber_id")
    assert "client_location" not in out


def fc(q, sql):
    r = agent.field_checks(q, sql, set())
    return r[0] if r else None


_P = ("start_time >= toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi') "
      "AND start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")


def test_total_asked_per_user_given():
    sql = (f"SELECT round(sumIf(duration, position(subscriber_id, '_') = 0) / uniqExactIf(subscriber_id, "
           f"position(subscriber_id, '_') = 0) / 60000, 2) AS minutes_per_user FROM t WHERE {_P}")
    assert fc("How many hours in total did people spend on Netflix yesterday?", sql) == "per_user_unasked"
    assert fc("average hours per user on netflix", sql) is None


def test_latitude_is_the_second_part():
    sql = f"SELECT uniqExact(subscriber_id) FROM t WHERE {_P} AND toFloat64OrNull(splitByChar(',', geo)[1]) > 35"
    assert fc("How many subscribers have an address north of latitude 35 degrees?", sql) == "geo_axis"
    assert fc("How many subscribers have an address north of latitude 35 degrees?", sql.replace("[1]", "[2]")) is None


def test_ipv6_is_the_subscribers_address():
    sql = f"SELECT uniqExact(subscriber_id) FROM t WHERE {_P} AND server_ip LIKE '2001:%'"
    assert fc("How many subscribers connected over IPv6 yesterday?", sql) == "ip_version"
    assert fc("How many subscribers connected over IPv6 yesterday?",
              f"SELECT 1 FROM t WHERE {_P} AND position(client_ip, ':') > 0") is None


def test_same_phone_means_imei():
    sql = f"SELECT subscriber_id FROM t WHERE {_P} AND subscriber_id IN ('923148030180', '03148030180')"
    assert fc("Which other numbers used the same phone as 923148030180 last week?", sql) == "same_handset"


def test_period_needs_an_end():
    sql = ("SELECT count() FROM t WHERE start_time >= toUnixTimestamp('2026-11-08 00:00:00', 'Asia/Karachi') "
           "AND end_time >= toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi')")
    assert fc("How many connections that started on 8 November were still running after midnight?", sql) == "open_period"
    assert fc("how many connections since 8 november", sql) is None


def test_share_of_connections_not_volume():
    sql = (f"SELECT l4_protocol, round(100 * sum(c2s_byte_num + s2c_byte_num) / sum(sum(c2s_byte_num + s2c_byte_num)) "
           f"OVER (), 1) AS share_pct FROM t WHERE {_P} GROUP BY l4_protocol")
    assert fc("What share of yesterday's connections used UDP?", sql) == "share_measure"


def test_share_of_own_traffic():
    sql = (f"SELECT client_location, round(100 * sum(c2s_byte_num) / sum(sum(c2s_byte_num)) OVER (), 2) AS share_pct "
           f"FROM t WHERE {_P} GROUP BY client_location")
    assert fc("Which cities send the largest share of their traffic upstream?", sql) == "own_share"


def test_named_ports():
    sql = f"SELECT uniqExact(subscriber_id) FROM t WHERE {_P} AND server_port = 5228"
    assert fc("How many people had Apple push notification connections on 9 November?", sql) == "port_5223"
    assert fc("Which cities had the most email (IMAP over SSL, port 993) data?", f"SELECT 1 FROM t WHERE {_P} "
              "AND app_label IN ('IMAP_SSL')") == "port_993"
    assert agent.ports_in("connections on source port 5000") == [("client_port", 5000, "port 5000")]


def test_province_only_records():
    sql = (f"SELECT client_location, count() FROM t WHERE {_P} AND client_location NOT IN ('Pakistan', "
           "'Punjab Pakistan', 'Sindh Pakistan', '') GROUP BY client_location")
    assert fc("For records where only the province is known, how many connections per province?", sql) == "province_only"


def test_where_connections_come_from():
    sql = (f"SELECT server_location, count() FROM t WHERE {_P} AND NOT endsWith(server_location, 'Pakistan') "
           "GROUP BY server_location")
    assert fc("Which foreign places outside Pakistan did connections come from yesterday?", sql) == "from_place"


def test_account_codes():
    sql = f"SELECT uniqExact(subscriber_id) FROM t WHERE {_P} AND position(subscriber_id, '_') = 0 AND subscriber_id NOT LIKE '923%'"
    assert fc("How many identified subscribers are account codes rather than phone numbers?", sql) == "account_codes"


def test_category_named():
    sql = f"SELECT client_location FROM t WHERE {_P} AND app_label IN ('Netflix') GROUP BY client_location"
    assert fc("Which cities used the most Streaming-category data last week?", sql) == "category"


def test_how_many_subscribers_with_countif():
    sql = "SELECT countIf(position(loc, 'House') > 0) AS h FROM t WHERE x"
    assert agent.rows_counted_as_people("How many subscribers have a house address?", sql)


def test_device_word_alone_is_answerable():
    assert agent.missing_in("how many connections used a privileged source port on the subscriber's device") == []
    assert agent.missing_in("which devices are shared by several numbers") == []
    assert agent.missing_in("which phone models were used most in lahore") == ["device model"]
    assert agent.missing_in("how many iphone users") == ["device model"]
    assert agent.missing_in("samsung cloud traffic") == []


def test_zoom_is_an_app_in_context():
    assert concepts.apps_in("Who spent the most time on Zoom last week?") == ["zoom"]
    assert concepts.apps_in("zoom in on lahore") == []
    assert concepts.apps_in("weak signal in karachi") == []


def test_find_values_on_an_imsi_says_how_to_filter():
    out = agent._tool_find_values("subscriber_id", "410060926190472", {"last_full_day": "2026-11-09"})
    assert "imsi = '410060926190472'" in out


def test_understood_keeps_a_second_date_the_sql_uses():
    sql = ("SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= toUnixTimestamp('2026-11-08 00:00:00', "
           "'Asia/Karachi') AND start_time < toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi') "
           "AND end_time >= toUnixTimestamp('2026-11-09 00:00:00', 'Asia/Karachi')")
    q = "How many connections that started on 8 November were still running after midnight, i.e. ended on 9 November or later?"
    assert agent.align_period_text({"understood": q, "title": "", "assumptions": []}, sql)["understood"] == q


_PREV = [{"role": "user", "kind": "question", "content": "top 5 tiktok users in karachi"},
         {"role": "assistant", "kind": "result", "data": {
             "title": "x", "sql": "SELECT subscriber_id, count() AS c FROM wms_dist.ntc_ssl_log WHERE start_time >= 1 "
                                  "GROUP BY subscriber_id ORDER BY c DESC LIMIT 5",
             "result": {"columns": [{"name": "subscriber_id"}, {"name": "c"}],
                        "rows": [["923001112223", 5], ["03001234567", 4], ["AJR37134768", 3], ["923009998887", 2],
                                 ["fiber12345678", 1]]}}}]


def _refs(q):
    return [(r["column"], r["values"]) for r in context.references(q, _PREV)]


def test_row_positions_in_follow_ups():
    assert _refs("which isp is the top one on") == [("subscriber_id", ["923001112223"])]
    assert _refs("what is the imei of the top user") == [("subscriber_id", ["923001112223"])]
    assert _refs("what did the first two do yesterday") == [("subscriber_id", ["923001112223", "03001234567"])]
    assert _refs("the guy at number 4 - his address?") == [("subscriber_id", ["923009998887"])]


def test_top_user_of_something_named_is_a_new_question():
    assert _refs("show me the top user who accessed api.vk.ru") == []
    assert _refs("the top user of youtube") == []
    assert _refs("show me the top 5") == []


def test_dotted_city_short_forms():
    assert concepts.cities_in("top apps in D.G. Khan") == ["Dera Ghazi Khan"]
    assert concepts.cities_in("users in lahore.") == ["Lahore"]


def test_more_period_phrasings():
    rng = {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:00", "last_full_day": "2026-11-09"}

    def days(q):
        p = agent.question_period(q, rng)
        return (str(p[1]), str(p[2])) if p else None
    assert days("top apps last 24 hours") == ("2026-11-09", "2026-11-09")
    assert days("top apps last fortnight") == ("2026-10-27", "2026-11-09")
    assert days("top apps in the whole data") == ("2026-10-11", "2026-11-09")
    assert days("top apps previous week") == ("2026-11-03", "2026-11-09")
    assert days("top apps last month") == ("2026-10-11", "2026-10-31")
    assert days("top apps in october") == ("2026-10-11", "2026-10-31")
    assert days("top apps over the weekend") == ("2026-11-07", "2026-11-08")
    assert days("weekday vs weekend usage") is None


def test_total_time_keeps_unidentified_records():
    sql = (f"SELECT round(sum(duration) / 3.6e6, 2) AS total_hours FROM t WHERE {_P} "
           "AND app_label IN ('Netflix', 'Netflix_NonHTTP_Video') AND position(subscriber_id, '_') = 0")
    assert agent.identified_filter_on_traffic("How many hours in total did people spend on Netflix yesterday?", sql)
    assert agent.identified_filter_on_traffic("How much time do people spend on Netflix per day on average?", sql) is None


def test_who_questions_are_lists_with_the_right_measure():
    assert agent.wants_subscriber_list("who spent the most time on tiktok")
    assert agent._list_measure("who spent the most time on tiktok")[0] == "hours"
    assert agent._list_measure("who made the most whatsapp calls")[0] == "connections"
    assert agent.wants_subscriber_list("who was online the longest")
    assert not agent.wants_subscriber_list("who has ip 100.64.55.35")


def test_foreign_places_keep_the_outside_condition():
    sql = (f"SELECT client_location, count() AS c FROM t WHERE {_P} AND client_location NOT IN ('Pakistan', '') "
           "GROUP BY client_location ORDER BY c DESC")
    assert fc("Which foreign places outside Pakistan did connections come from yesterday?", sql) == "foreign_place"
    ok = sql.replace("NOT IN ('Pakistan', '')", "!= '' AND NOT endsWith(client_location, 'Pakistan')")
    assert fc("Which foreign places outside Pakistan did connections come from yesterday?", ok) is None


def test_subquery_gets_the_outer_period():
    p = ("start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') "
         "AND start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")
    sql = (f"SELECT client_location FROM wms_dist.ntc_ssl_log WHERE {p} AND geo = (SELECT any(geo) FROM "
           "wms_dist.ntc_ssl_log WHERE client_location = 'Peshawar Pakistan' OR x = 1) AND imei IN (SELECT imei FROM "
           "wms_dist.ntc_ssl_log GROUP BY imei HAVING count() > 1) GROUP BY client_location")
    out = agent.repair_measures(sql)
    assert out.count("start_time >=") == 3
    assert f"WHERE {p} AND (client_location = 'Peshawar Pakistan' OR x = 1))" in out
    from ipdr import sql_guard
    sql_guard.check(out)


def test_server_city_is_server_location():
    ks = kinds("What are the coordinates of the servers located in Mumbai, India?")
    assert ("server_place", "Mumbai India") in ks and not [k for k in ks if k[0] == "place"]
    msg = agent.entity_missing("What are the coordinates of the servers located in Mumbai, India?",
                               "SELECT server_geo FROM t WHERE x AND client_location = 'Mumbai India'")
    assert msg and "server_location = 'Mumbai India'" in msg
    assert agent.entity_missing("What are the coordinates of the servers located in Mumbai, India?",
                                "SELECT server_geo FROM t WHERE x AND server_location = 'Mumbai India'") is None


def test_named_category_is_not_the_kind_of_app():
    assert [c["key"] for c in concepts.match("Which cities used the most Streaming-category data?")] == []


def test_share_of_connections_not_time():
    sql = f"SELECT round(100 * sumIf(duration, l4_protocol = 'UDP') / sum(duration), 2) AS p FROM t WHERE {_P}"
    assert fc("What share of yesterday's connections used UDP?", sql) == "share_measure"


def test_meta_understood_detected():
    assert agent._META_UNDERSTOOD.search("The question specifically asks about video streaming apps. The SQL should filter")
    assert not agent._META_UNDERSTOOD.search("Top 5 cities by Streaming data from 3 Nov to 9 Nov 2026")


def test_exclusions_are_not_pushed_as_filters():
    q = "Leaving out Google and Facebook, whose servers got the most data yesterday? top 5"
    sql = "SELECT server_asn FROM t WHERE server_asn NOT IN ('Google LLC', 'Facebook, Inc.') GROUP BY server_asn"
    assert agent.excluded_words(q) == {"google", "facebook"}
    assert agent.app_ignored(q, sql) is None and agent.concept_incomplete(q, sql) is None
    assert agent.app_ignored("how much tiktok data in lahore", "SELECT sum(c2s_byte_num) FROM t WHERE x")


def test_country_short_names_and_people_words():
    assert ("server_country", "United States") in kinds("what % of Netflix data came off servers in the US?")
    assert not [k for k in kinds("show us the top apps") if k[0] == "server_country"]
    assert agent.wants_subscriber_list("where do the 5 heaviest Spotify listeners live?")
    assert agent.wants_subscriber_list("did anyone go over a gig yesterday? who?")
