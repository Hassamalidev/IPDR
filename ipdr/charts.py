from __future__ import annotations

import math
import re

_NUMERIC = re.compile(r"^(Nullable\()?(LowCardinality\()?(U?Int\d+|Float\d+|Decimal)")
_TIME = re.compile(r"^(Nullable\()?(LowCardinality\()?Date")
_ORDINAL_NAMES = {"hour_of_day", "hour", "weekday", "day_of_week", "minute", "month_num", "week_num", "dow"}
_LON = {"lon", "lng", "long", "longitude"}
_LAT = {"lat", "latitude"}


def pretty(name: str) -> str:
    units = {"gb": "GB", "mb": "MB", "kb": "KB", "s": "s", "ms": "ms", "pct": "%"}
    parts = name.split("_")
    unit = ""
    if len(parts) > 1 and parts[-1].lower() in units:
        unit = f" ({units[parts.pop().lower()]})"
    text = " ".join(parts).strip()
    return (text[:1].upper() + text[1:] + unit) if text else name


def classify(columns: list[dict]) -> dict:
    kinds = {"time": [], "ordinal": [], "category": [], "measure": [], "lon": None, "lat": None}
    for c in columns:
        name, typ = c["name"], c["type"]
        low = name.lower()
        if _TIME.match(typ):
            kinds["time"].append(name)
        elif _NUMERIC.match(typ):
            if low in _LON:
                kinds["lon"] = name
            elif low in _LAT:
                kinds["lat"] = name
            elif low in _ORDINAL_NAMES:
                kinds["ordinal"].append(name)
            else:
                kinds["measure"].append(name)
        else:
            kinds["category"].append(name)
    return kinds


def additive(name: str) -> bool:
    low = name.lower()
    return not any(w in low for w in ("user", "subscriber", "uniq", "distinct", "unique", "avg", "mean", "pct", "share", "rate"))


def _opt(type_, title, x="", y="", series="", value="", label="") -> dict:
    o = {"type": type_, "title": title, "x": x, "y": y, "series": series, "value": value, "label": label}
    measure = value or y
    o["additive"] = additive(measure) if measure else True
    return o


def suggest(columns: list[dict], rows: list[list], hint: dict | None = None) -> dict:
    if not rows:
        return {"options": [], "default": None}
    keep = [i for i, c in enumerate(columns)
            if not str(c.get("type", "")).startswith("Array") and not any(isinstance(r[i], (list, dict)) for r in rows)]
    if len(keep) < len(columns):
        columns = [columns[i] for i in keep]
        rows = [[r[i] for i in keep] for r in rows]
    k = classify(columns)
    M, C = k["measure"], k["category"]
    T = k["time"] + k["ordinal"]
    types = {c["name"]: c["type"] for c in columns}
    idx = {c["name"]: i for i, c in enumerate(columns)}

    def _summable(m: str) -> bool:
        vals = [r[idx[m]] for r in rows]
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if not additive(m) or len(nums) < len([v for v in vals if v is not None]):
            return False
        return all(math.isfinite(v) and v >= 0 for v in nums) and sum(nums) > 0
    parts = {m: _summable(m) for m in M}
    distinct = {c["name"]: len({r[i] for r in rows}) for i, c in enumerate(columns)}
    opts: list[dict] = []
    add = lambda *a, **kw: opts.append(_opt(*a, **kw))

    if len(rows) == 1 and len(M) == 1 and not T:
        add("figure", pretty(M[0]), y=M[0])

    if k["lon"] and k["lat"] and M:
        label = C[0] if C else ""
        for m in M:
            add("map", f"Map: {pretty(m)} by location", x=k["lon"], y=k["lat"], value=m, label=label)

    if T and M:
        t = T[0]
        tl = pretty(t).lower()
        is_ordinal = t in k["ordinal"]
        breakdown = next((c for c in C if 1 < distinct[c] <= 50), "")
        second_t = next((x for x in T[1:] if distinct[x] > 1), "")
        for m in M:
            pm = pretty(m)
            if second_t:
                wide, narrow = (t, second_t) if distinct[t] >= distinct[second_t] else (second_t, t)
                add("heatmap", f"Heatmap: {pm} by {pretty(wide).lower()} and {pretty(narrow).lower()}", x=wide, y=narrow, value=m)
                if distinct[narrow] <= 12:
                    add("line", f"Line: {pm} over {pretty(wide).lower()}, one line per {pretty(narrow).lower()}",
                        x=wide, y=m, series=narrow)
            if breakdown:
                bl = pretty(breakdown).lower()
                add("line", f"Line: {pm} over {tl}, by {bl}", x=t, y=m, series=breakdown)
                if parts[m]:
                    add("stacked_area", f"Stacked area: {pm} over {tl}, by {bl}", x=t, y=m, series=breakdown)
                    add("stacked_bar", f"Stacked columns: {pm} by {tl} and {bl}", x=t, y=m, series=breakdown)
                    add("percent_bar", f"100% columns: share of {pm.lower()} by {tl} and {bl}", x=t, y=m, series=breakdown)
                add("heatmap", f"Heatmap: {pm} by {tl} and {bl}", x=t, y=breakdown, value=m)
            elif not second_t:
                add("line", f"Line: {pm} over {tl}", x=t, y=m)
                add("area", f"Area: {pm} over {tl}", x=t, y=m)
                if is_ordinal or distinct[t] <= 45:
                    add("column", f"Columns: {pm} by {tl}", x=t, y=m)
                if re.match(r"^(Nullable\()?Date(32)?\)?$", types[t]) and distinct[t] >= 7:
                    add("calendar", f"Calendar: {pm} per day", x=t, y=m)
                if t.lower() in ("hour_of_day", "hour") and distinct[t] >= 6:
                    add("clock", f"24-hour clock: {pm} by hour", x=t, y=m)

    if not T and C and M and len(rows) > 1:
        cat = max(C, key=lambda c: distinct[c]) if len(C) > 1 else C[0]
        cl = pretty(cat).lower()
        second = next((c for c in C if c != cat and 1 < distinct[c] <= 50), "")
        n = distinct[cat]
        for m in M:
            pm = pretty(m)
            if second:
                sl = pretty(second).lower()
                add("bar", f"Grouped bars: {pm} by {cl} and {sl}", x=cat, y=m, series=second)
                if parts[m]:
                    add("stacked_bar", f"Stacked bars: {pm} by {cl}, split by {sl}", x=cat, y=m, series=second)
                    add("percent_bar", f"100% bars: share of {pm.lower()} in each {cl}, by {sl}", x=cat, y=m, series=second)
                add("heatmap", f"Heatmap: {pm} by {cl} and {sl}", x=cat, y=second, value=m)
                if parts[m]:
                    add("sankey", f"Flow: {pm} from {sl} to {cl}", x=cat, y=m, series=second)
                    add("sunburst", f"Sunburst: {pm} by {sl}, then {cl}", x=cat, y=m, series=second)
            else:
                add("bar", f"Bars: {pm} by {cl}", x=cat, y=m)
                if n <= 20:
                    add("column", f"Columns: {pm} by {cl}", x=cat, y=m)
                if parts[m]:
                    if n <= 30:
                        add("pie", f"Donut: share of {pm.lower()} by {cl}", x=cat, y=m)
                    if n >= 3:
                        add("treemap", f"Treemap: {pm} by {cl}", x=cat, y=m)
                    if n >= 4:
                        add("pareto", f"Pareto: {pm} by {cl}, with running share", x=cat, y=m)

    if len(M) >= 2 and len(rows) > 1:
        axis = T[0] if T else (C[0] if C and len(C) == 1 else "")
        if axis and len(rows) <= 60:
            add("combo", f"Bars and line: {pretty(M[0])} and {pretty(M[1])} by {pretty(axis).lower()}",
                x=axis, y=M[0], value=M[1])
    if len(M) >= 3 and not T and len(C) == 1 and 3 <= distinct[C[0]] <= 10 and len(rows) <= 10:
        o = _opt("radar", f"Radar: compare {pretty(C[0]).lower()} on {len(M)} measures", x=C[0])
        o["measures"] = M[:8]
        opts.append(o)
    if len(M) >= 2 and len(rows) > 2 and not (k["lon"] and k["lat"]):
        label = C[0] if C else (T[0] if T else "")
        add("scatter", f"Scatter: {pretty(M[0])} against {pretty(M[1])}", x=M[0], y=M[1], label=label)
        if len(M) >= 3:
            add("bubble", f"Bubbles: {pretty(M[0])} against {pretty(M[1])}, size = {pretty(M[2]).lower()}",
                x=M[0], y=M[1], value=M[2], label=label)

    return {"options": opts, "default": _match(opts, hint) if opts else None}


def _match(opts: list[dict], hint: dict | None) -> int:
    if not hint or hint.get("type") in (None, "", "table"):
        return 0
    want_type = {"bar": {"bar", "column"}, "column": {"column", "bar"}, "pie": {"pie", "treemap"},
                 "area": {"area", "stacked_area", "line"}, "stacked_area": {"stacked_area", "stacked_bar"},
                 "line": {"line", "area"}}.get(hint["type"], {hint["type"]})
    best, best_score = 0, -1
    for i, o in enumerate(opts):
        score = 0
        if o["type"] in want_type:
            score += 4
        if hint.get("y") and hint["y"] in (o["y"], o["value"]):
            score += 2
        if hint.get("x") and hint["x"] == o["x"]:
            score += 1
        if hint.get("series") and hint["series"] == o["series"]:
            score += 1
        if score > best_score:
            best, best_score = i, score
    return best
