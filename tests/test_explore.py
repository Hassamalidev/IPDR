import pytest
from fastapi.testclient import TestClient

from ipdr import explore, main, sql_guard
from ipdr.db import QueryResult
from ipdr.explore import ExploreError, ExploreQuery, FilterIn, build

RANGE = dict(start="2026-11-09T10:00", end="2026-11-09T11:00")


def q(**kw):
    return ExploreQuery(**{**RANGE, **kw})


def test_records_query_shape_and_params():
    b = build(q(filters=[FilterIn(field="app_label", op="eq", value="TikTok"),
                         FilterIn(field="s2c_byte_num", op="gt", value="10,000")],
                columns=["start_time", "app_label", "s2c_byte_num"], limit=50, offset=100))
    assert "toDateTime(start_time, 'Asia/Karachi') AS start_time" in b.sql
    assert "app_label = {p0:String}" in b.sql and "s2c_byte_num > {p1:Float64}" in b.sql
    assert b.params["p0"] == "TikTok" and b.params["p1"] == "10000"
    assert b.sql.rstrip().endswith("LIMIT 50 OFFSET 100")
    assert "app_label = 'TikTok'" in b.display_sql and "s2c_byte_num > 10000" in b.display_sql


def test_display_sql_passes_the_chat_guard():
    b = build(q(filters=[FilterIn(field="loc", op="contains", value="model town")]))
    sql_guard.check(b.display_sql)


def test_values_never_become_sql():
    evil = "x' OR 1=1; DROP TABLE wms_dist.ntc_ssl_log --"
    b = build(q(filters=[FilterIn(field="requested_server", op="eq", value=evil)]))
    assert evil not in b.sql and b.params["p0"] == evil
    assert "\\'" in b.display_sql


def test_unknown_or_injected_field_rejected():
    with pytest.raises(ExploreError, match="Unknown field"):
        build(q(filters=[FilterIn(field="app_label = '' OR 1=1 --", op="eq", value="x")]))
    b = build(q(columns=["app_label", "1; DROP TABLE x", "user_agent"]))
    assert "DROP" not in b.sql and "user_agent" not in b.sql


def test_time_rules():
    with pytest.raises(ExploreError, match="after the start"):
        build(ExploreQuery(start="2026-11-09T11:00", end="2026-11-09T10:00"))
    build(ExploreQuery(start="2025-01-01T00:00", end="2026-11-09T00:00"))
    with pytest.raises(ExploreError, match="not valid"):
        build(ExploreQuery(start="yesterday", end="2026-11-09T00:00"))
    with pytest.raises(ExploreError, match="time range"):
        build(q(filters=[FilterIn(field="end_time", op="gt", value="1")]))


def test_msisdn_normalised():
    b = build(q(filters=[FilterIn(field="subscriber_id", op="eq", value="0325-3047295")]))
    assert b.params["p0"] == "['923253047295', '03253047295']"
    b = build(q(filters=[FilterIn(field="subscriber_id", op="in", value="+92 325 3047295, 03001234567")]))
    assert b.params["p0"] == "['923253047295', '03253047295', '923001234567', '03001234567']"
    b = build(q(filters=[FilterIn(field="subscriber_id", op="eq", value="AJR37134768")]))
    assert b.params["p0"] == "AJR37134768"


def test_in_between_empty():
    b = build(q(filters=[FilterIn(field="server_port", op="in", value="80, 443"),
                         FilterIn(field="duration", op="between", value="5000, 100"),
                         FilterIn(field="requested_server", op="empty")]))
    assert b.params["p0"] == "[80, 443]"
    assert b.params["p1a"] == "100" and b.params["p1b"] == "5000"
    assert "requested_server = ''" in b.sql
    with pytest.raises(ExploreError, match="two numbers"):
        build(q(filters=[FilterIn(field="duration", op="between", value="5")]))
    with pytest.raises(ExploreError, match="not a number"):
        build(q(filters=[FilterIn(field="duration", op="gt", value="lots")]))
    with pytest.raises(ExploreError, match="Enter a value"):
        build(q(filters=[FilterIn(field="app_label", op="eq", value="")]))
    with pytest.raises(ExploreError, match="not a valid condition"):
        build(q(filters=[FilterIn(field="app_label", op="gt", value="x")]))


def test_summary_and_count():
    b = build(q(mode="summary", group_by=["day", "app_label"], measures=["users", "volume_gb", "nope"]))
    assert "toDate(toDateTime(start_time, 'Asia/Karachi')) AS day" in b.sql
    assert "GROUP BY day, app_label" in b.sql and "ORDER BY day ASC" in b.sql
    assert "uniqExactIf(subscriber_id, position(subscriber_id, '_') = 0) AS users" in b.sql and "nope" not in b.sql
    b = build(q(mode="summary", group_by=["app_label"], measures=["connections"]))
    assert "ORDER BY connections DESC" in b.sql
    with pytest.raises(ExploreError, match="Cannot group"):
        build(q(mode="summary", group_by=["start_time"]))
    c = build(q(filters=[FilterIn(field="app_label", op="eq", value="x")]), count=True)
    assert c.sql.startswith("SELECT count() AS records") and "LIMIT" not in c.sql


def test_export_limit_ignores_paging():
    b = build(q(limit=50, offset=500), export_limit=200000)
    assert b.sql.rstrip().endswith("LIMIT 200000")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "data_range", lambda: {"first": "2026-10-11 09:41:00", "last": "2026-11-10 09:40:59",
                                                     "last_full_day": "2026-11-09"})
    with TestClient(main.app) as c:
        yield c


def test_api_fields_and_run(client, monkeypatch):
    f = client.get("/api/explore/fields").json()
    names = {x["name"] for x in f["fields"]}
    assert "app_label" in names and "user_agent" not in names
    seen = {}

    def fake(sql, max_rows=None, params=None, ttl=0):
        seen.update(sql=sql, params=params)
        return QueryResult([{"name": "app_label", "type": "String"}, {"name": "connections", "type": "UInt64"}],
                           [["TikTok", 5], ["IMO", 2]], 0.2, 1000, 0)

    monkeypatch.setattr(main.db, "cached_query", fake)
    r = client.post("/api/explore/run", json={**RANGE, "mode": "summary", "group_by": ["app_label"], "measures": ["connections"]})
    assert r.status_code == 200
    out = r.json()
    assert out["result"]["row_count"] == 2 and out["charts"]["options"][0]["type"] == "bar"
    bad = client.post("/api/explore/run", json={**RANGE, "filters": [{"field": "nope", "op": "eq", "value": "x"}]})
    assert bad.status_code == 400 and "Unknown field" in bad.json()["detail"]


def test_api_views(client):
    v = client.post("/api/views", json={"name": "Karachi TikTok", "config": {"filters": [{"field": "app_label", "op": "eq", "value": "TikTok"}]}}).json()
    assert any(x["id"] == v["id"] for x in client.get("/api/views").json())
    client.delete(f"/api/views/{v['id']}")
    assert all(x["id"] != v["id"] for x in client.get("/api/views").json())


def test_api_export_rejects_bad_query(client):
    r = client.get("/api/explore/export", params={"q": '{"start": "x", "end": "y"}'})
    assert r.status_code == 400


def test_identified_only_and_hide_background():
    b = build(q(identified_only=True, hide_background=True))
    assert "position(subscriber_id, '_') = 0" in b.sql
    assert "NOT has({bg:Array(String)}, app_label)" in b.sql and "SSL" in b.params["bg"]
    plain = build(q())
    assert "position(subscriber_id" not in plain.sql and "bg" not in plain.params


def test_records_carry_hidden_keys_but_exports_and_display_do_not():
    b = build(q())
    assert b.key_cols == 2 and "AS __t, log_id AS __id" in b.sql
    assert "__t" not in b.display_sql
    sql_guard.check(b.display_sql)
    e = build(q(), export_limit=1000)
    assert e.key_cols == 0 and "__t" not in e.sql
    s = build(q(mode="summary", group_by=["app_label"]))
    assert s.key_cols == 0


def test_record_sql_is_parameterised():
    sql, params = explore.record_sql("2026-11-10 09:40:59", "7000'; DROP TABLE x")
    assert "log_id = {id:String}" in sql and "DROP" not in sql and params["id"] == "7000'; DROP TABLE x"
    assert params["t"] == "2026-11-10 09:40:59"
    with pytest.raises(ExploreError):
        explore.record_sql("not a time", "1")


@pytest.mark.parametrize("text,expected", [
    ("0325-3047295", [("subscriber_id", "in", "923253047295, 03253047295")]),
    ("tiktok karachi", [("client_location", "eq", "Karachi Pakistan"), ("app_group", "eq", "tiktok")]),
    ("snapchat in lahore", [("client_location", "eq", "Lahore Pakistan"), ("app_label", "in", "SnapChat, SnapChat_VoIP")]),
    ("adult sites isb", [("client_location", "eq", "Islamabad Pakistan"), ("app_group", "eq", "adult_content")]),
    ("100.67.126.11", [("client_ip", "eq", "100.67.126.11")]),
    ("142.251.15.1", [("server_ip", "eq", "142.251.15.1")]),
    ("facebook.com", [("requested_server", "contains", "facebook.com")]),
    ("100.67.196.194_54808", [("subscriber_id", "eq", "100.67.196.194_54808")]),
    ("410041234567890", [("imsi", "eq", "410041234567890")]),
    ("jazz", [("client_asn", "in", "PMCL LDI IP TRANSIT, IX Peering for Mobilink and Link Direct International.")]),
    ("nayatel", [("client_asn", "eq", "Nayatel Pvt Ltd")]),
    ("jazz sim", [("mnc", "in", "01, 07")]),
])
def test_quick_lookup(text, expected):
    out = explore.interpret(text)
    assert [(f["field"], f["op"], f["value"]) for f in out["filters"]] == expected
    assert out["unknown"] == []


def test_quick_lookup_reports_what_it_could_not_place():
    out = explore.interpret("xyzzy karachi")
    assert out["unknown"] == ["xyzzy"] and out["filters"][0]["value"] == "Karachi Pakistan"
    assert explore.interpret("qwerty")["filters"] == []


def test_lookup_filters_are_valid_explore_filters():
    for text in ["0325-3047295 tiktok", "zong karachi", "facebook.com", "adult sites isb", "snapchat"]:
        filters = [FilterIn(**f) for f in explore.interpret(text)["filters"]]
        build(q(filters=filters))


def test_api_run_splits_off_record_keys_and_opens_a_record(client, monkeypatch):
    def fake(sql, max_rows=None, params=None, ttl=0):
        if "__t" in sql:
            return QueryResult([{"name": "app_label", "type": "String"}, {"name": "__t", "type": "DateTime"},
                                {"name": "__id", "type": "String"}],
                               [["TikTok", "2026-11-09 10:00:01", "71"], ["IMO", "2026-11-09 10:00:02", "72"]], 0.1, 100, 0)
        return QueryResult([{"name": "log_id", "type": "String"}, {"name": "duration", "type": "UInt64"}],
                           [["71", 1500]], 0.1, 10, 0)

    monkeypatch.setattr(main.db, "cached_query", fake)
    out = client.post("/api/explore/run", json={**RANGE, "columns": ["app_label"]}).json()
    assert [c["name"] for c in out["result"]["columns"]] == ["app_label"]
    assert out["result"]["rows"] == [["TikTok"], ["IMO"]]
    assert out["keys"] == [["2026-11-09 10:00:01", "71"], ["2026-11-09 10:00:02", "72"]]
    rec = client.post("/api/explore/record", json={"t": "2026-11-09 10:00:01", "id": "71"}).json()
    assert {"name": "duration", "type": "UInt64", "value": 1500} == {k: rec["fields"][1][k] for k in ("name", "type", "value")}
    assert client.get("/api/explore/interpret", params={"q": "tiktok karachi"}).json()["understood"]
