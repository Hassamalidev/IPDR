import pytest

from ipdr.sql_guard import GuardError, check

T = "wms_dist.ntc_ssl_log"
TIME = "start_time >= toUnixTimestamp('2026-11-01 00:00:00', 'Asia/Karachi')"


def ok(sql: str) -> str:
    return check(sql, max_rows=100)


def bad(sql: str, match: str) -> None:
    with pytest.raises(GuardError, match=match):
        check(sql, max_rows=100)


def test_simple_select_gets_limit():
    out = ok(f"SELECT app_label, count() FROM {T} WHERE {TIME} GROUP BY app_label")
    assert out.rstrip().endswith("LIMIT 100")


def test_existing_small_limit_kept():
    sql = f"SELECT app_label FROM {T} WHERE {TIME} LIMIT 20"
    assert ok(sql) == sql


def test_large_limit_is_capped():
    out = ok(f"SELECT app_label FROM {T} WHERE {TIME} LIMIT 999999")
    assert out.startswith("SELECT * FROM (") and out.rstrip().endswith("LIMIT 100")


def test_trailing_semicolon_removed():
    assert not ok(f"SELECT 1 AS x FROM {T} WHERE {TIME} LIMIT 5;").endswith(";")


def test_table_without_database_allowed():
    ok(f"SELECT count() FROM ntc_ssl_log WHERE {TIME}")


def test_cte_and_subquery_allowed():
    ok(f"WITH a AS (SELECT app_label, count() AS n FROM {T} WHERE {TIME} GROUP BY app_label) "
       f"SELECT * FROM a ORDER BY n DESC LIMIT 10")
    ok(f"SELECT app, n FROM (SELECT app_label AS app, count() AS n FROM {T} WHERE {TIME} GROUP BY app) LIMIT 5")


def test_clickhouse_functions_parse():
    ok(f"""SELECT toDate(toDateTime(start_time, 'Asia/Karachi')) AS day,
               uniqIf(subscriber_id, app_label = 'TikTok') AS users,
               quantile(0.9)(duration) AS p90,
               multiIf(app_label LIKE 'TikTok%', 'TikTok', 'Other') AS app
        FROM {T} WHERE {TIME} AND loc ILIKE '%Model Town%' GROUP BY day, app ORDER BY day""")


def test_strings_with_keywords_are_fine():
    ok(f"SELECT count() FROM {T} WHERE {TIME} AND requested_server ILIKE '%drop table%' AND loc = 'Set; Street'")


@pytest.mark.parametrize("sql", [
    f"DROP TABLE {T}",
    f"INSERT INTO {T} SELECT * FROM {T}",
    f"ALTER TABLE {T} DELETE WHERE 1",
    f"TRUNCATE TABLE {T}",
    "SHOW TABLES",
    f"OPTIMIZE TABLE {T}",
])
def test_writes_rejected(sql):
    with pytest.raises(GuardError):
        ok(sql)


def test_two_statements_rejected():
    bad(f"SELECT 1 FROM {T} WHERE {TIME}; DROP TABLE {T}", "one statement")


def test_missing_time_filter_rejected():
    bad(f"SELECT count() FROM {T}", "time range")
    bad(f"SELECT count() FROM {T} WHERE app_label = 'TikTok'", "time range")


def test_time_filter_required_in_every_subquery():
    bad(f"SELECT * FROM (SELECT app_label FROM {T}) WHERE app_label = 'x'", "time range")


def test_other_tables_rejected():
    bad(f"SELECT * FROM system.users WHERE {TIME}", "System tables")
    bad(f"SELECT * FROM other_db.secret WHERE {TIME}", "Only")


def test_table_functions_rejected():
    bad(f"SELECT * FROM url('http://x', CSV) WHERE {TIME}", "url")
    bad(f"SELECT * FROM file('/etc/passwd') WHERE {TIME}", "file")


def test_settings_and_format_rejected():
    bad(f"SELECT 1 FROM {T} WHERE {TIME} SETTINGS max_execution_time = 9999", "SETTINGS")
    bad(f"SELECT 1 FROM {T} WHERE {TIME} FORMAT CSV", "FORMAT")
    bad(f"SELECT 1 FROM {T} WHERE {TIME} INTO OUTFILE 'x.csv'", "OUTFILE")


def test_empty_rejected():
    bad("  ;  ", "empty")


import pytest

from ipdr import sql_guard as _g

_W = ("start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') AND "
      "start_time < toUnixTimestamp('2026-11-10 00:00:00', 'Asia/Karachi')")


@pytest.mark.parametrize("sql", [
    "SELECT toString(number) AS start_time FROM (SELECT 1 AS number LIMIT 1) WHERE 'x' != '' UNION ALL "
    "SELECT name AS start_time FROM system.tables LIMIT 1 BY start_time LIMIT 5",
    "SELECT TOP 2 log_id, start_time FROM wms_dist.ntc_ssl_log",
    "SELECT 'start_time' AS x FROM wms_dist.ntc_ssl_log LIMIT 1 BY x LIMIT 1",
    "SELECT toString(number) AS start_time FROM (SELECT 1 AS number LIMIT 1) WHERE '--' != '' UNION ALL "
    "SELECT name AS start_time FROM system.clusters",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= 0+0",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= toUInt32('0')",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= '0'",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= -1",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= 0x0",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= now()-2e9",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= end_time-100",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= start_time",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= toUnixTimestamp('1970-01-02 00:00:00')",
])
def test_guard_holes_are_closed(sql):
    with pytest.raises(_g.GuardError):
        _g.check(sql)


@pytest.mark.parametrize("sql", [
    f"SELECT count() FROM wms_dist.ntc_ssl_log WHERE {_W}",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= now() - 86400 * 7",
    "SELECT count() FROM wms_dist.ntc_ssl_log WHERE start_time >= toUnixTimestamp('2026-11-03 00:00:00', 'Asia/Karachi') - 86400",
    f"SELECT count() FROM wms_dist.ntc_ssl_log WHERE {_W} AND requested_server NOT LIKE '%--%' "
    "AND requested_server != 'settings.crashlytics.com'",
])
def test_legitimate_queries_still_pass(sql):
    assert _g.check(sql)
