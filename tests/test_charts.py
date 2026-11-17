from ipdr.charts import pretty, suggest


def cols(*pairs):
    return [{"name": n, "type": t} for n, t in pairs]


def types(result):
    return [o["type"] for o in result["options"]]


def test_ranking_gets_bars_and_scatter():
    c = cols(("app", "LowCardinality(String)"), ("users", "UInt64"), ("connections", "UInt64"), ("volume_gb", "Float64"))
    rows = [["TikTok", 10, 20, 1.5], ["Facebook", 8, 30, 2.0], ["WhatsAPP", 5, 9, 0.4]]
    r = suggest(c, rows, {"type": "bar", "x": "app", "y": "users", "series": ""})
    assert types(r).count("bar") == 3 and types(r)[0] == "bar"
    assert {"scatter", "bubble", "radar", "combo"} <= set(types(r))
    assert {o["y"] for o in r["options"] if o["type"] in ("pie", "treemap", "pareto")} == {"connections", "volume_gb"}
    assert r["options"][r["default"]]["y"] == "users"


def test_time_series_gets_line_first():
    c = cols(("day", "Date"), ("users", "UInt64"))
    r = suggest(c, [["2026-11-01", 5], ["2026-11-02", 6]], None)
    assert types(r) == ["line", "area", "column"]


def test_hour_of_day_is_ordered_axis():
    c = cols(("hour_of_day", "UInt8"), ("volume_gb", "Float64"))
    r = suggest(c, [[h, h * 1.5] for h in range(24)], {"type": "line", "x": "hour_of_day", "y": "volume_gb", "series": ""})
    assert r["options"][r["default"]]["type"] == "line"
    assert r["options"][r["default"]]["x"] == "hour_of_day"


def test_users_are_never_stacked():
    c = cols(("city", "String"), ("app", "String"), ("users", "UInt64"))
    rows = [["Karachi", "TikTok", 5], ["Karachi", "Facebook", 4], ["Lahore", "TikTok", 6], ["Lahore", "Facebook", 3]]
    t = types(suggest(c, rows, None))
    assert "stacked_bar" not in t and "heatmap" in t and "bar" in t


def test_volume_can_be_stacked():
    c = cols(("city", "String"), ("app", "String"), ("volume_gb", "Float64"))
    rows = [["Karachi", "TikTok", 5.0], ["Karachi", "Facebook", 4.0], ["Lahore", "TikTok", 6.0], ["Lahore", "Facebook", 3.0]]
    assert "stacked_bar" in types(suggest(c, rows, None))


def test_lon_lat_gives_map():
    c = cols(("site", "String"), ("lon", "Nullable(Float64)"), ("lat", "Nullable(Float64)"), ("users", "UInt64"))
    r = suggest(c, [["a", 73.0, 33.7, 5], ["b", 73.1, 33.8, 2]], {"type": "map", "x": "lon", "y": "lat", "series": ""})
    assert r["options"][r["default"]]["type"] == "map"
    assert "scatter" not in types(r)


def test_single_number_is_figure():
    r = suggest(cols(("users", "UInt64")), [[12345]], None)
    assert types(r) == ["figure"]


def test_text_only_result_has_no_chart():
    r = suggest(cols(("site", "String"),), [["a"], ["b"]], None)
    assert r["options"] == [] and r["default"] is None


def test_pretty_names():
    assert pretty("volume_gb") == "Volume (GB)"
    assert pretty("hour_of_day") == "Hour of day"
    assert pretty("users") == "Users"


def test_user_counts_get_no_share_charts():
    c = cols(("app", "String"), ("users", "UInt64"))
    t = types(suggest(c, [["a", 1], ["b", 2], ["c", 3], ["d", 4]], {"type": "pie", "x": "app", "y": "users", "series": ""}))
    assert t == ["bar", "column"]


def test_volume_by_app_gets_donut_treemap_pareto():
    c = cols(("app", "String"), ("volume_gb", "Float64"))
    r = suggest(c, [["a", 1.0], ["b", 2.0], ["c", 3.0], ["d", 4.0]], {"type": "pie", "x": "app", "y": "volume_gb", "series": ""})
    assert types(r) == ["bar", "column", "pie", "treemap", "pareto"]
    assert r["options"][r["default"]]["type"] == "pie"


def test_days_get_calendar_and_hours_get_clock():
    days = suggest(cols(("day", "Date"), ("connections", "UInt64")), [[f"2026-11-0{i}", i] for i in range(1, 9)], None)
    assert "calendar" in types(days)
    hours = suggest(cols(("hour_of_day", "UInt8"), ("volume_gb", "Float64")), [[h, 1.0] for h in range(24)], None)
    assert "clock" in types(hours) and "calendar" not in types(hours)


def test_weekday_by_hour_is_a_heatmap():
    c = cols(("weekday", "UInt8"), ("hour_of_day", "UInt8"), ("users", "UInt64"))
    r = suggest(c, [[w, h, 5] for w in range(1, 8) for h in range(24)], None)
    first = r["options"][0]
    assert first["type"] == "heatmap" and first["x"] == "hour_of_day" and first["y"] == "weekday"
    assert any(o["type"] == "line" and o["series"] == "weekday" for o in r["options"])


def test_two_categories_get_flow_and_share_charts():
    c = cols(("city", "String"), ("app", "String"), ("volume_gb", "Float64"))
    rows = [["K", "T", 1.0], ["K", "F", 2.0], ["L", "T", 3.0], ["L", "F", 1.0]]
    assert {"percent_bar", "sankey", "sunburst", "heatmap"} <= set(types(suggest(c, rows, None)))


def test_time_by_breakdown_volume_gets_stacked_area():
    c = cols(("day", "Date"), ("app", "String"), ("volume_gb", "Float64"))
    rows = [["2026-11-01", "T", 1.0], ["2026-11-01", "F", 2.0], ["2026-11-02", "T", 1.0], ["2026-11-02", "F", 2.0]]
    t = types(suggest(c, rows, None))
    assert {"line", "stacked_area", "stacked_bar", "percent_bar", "heatmap"} <= set(t)


def test_two_measures_over_time_get_combo():
    c = cols(("day", "Date"), ("connections", "UInt64"), ("volume_gb", "Float64"))
    r = suggest(c, [["2026-11-01", 1, 2.0], ["2026-11-02", 3, 1.0]], {"type": "combo", "x": "day", "y": "connections", "series": ""})
    o = r["options"][r["default"]]
    assert o["type"] == "combo" and o["y"] == "connections" and o["value"] == "volume_gb"
