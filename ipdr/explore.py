from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from . import concepts, dictionary
from .config import settings

TZ = settings.timezone
TIME_COLS = {"start_time", "end_time", "recv_time"}
MAX_DAYS = None
MAX_PAGE = 1000
MAX_OFFSET = 100_000

DEFAULT_COLUMNS = ["start_time", "subscriber_id", "client_location", "app_label", "app_category",
                   "requested_server", "server_ip", "server_port", "c2s_byte_num", "s2c_byte_num", "duration"]

OPS = {
    "string": [("eq", "is"), ("ne", "is not"), ("contains", "contains"), ("not_contains", "does not contain"),
               ("starts", "starts with"), ("in", "is one of"), ("empty", "is empty"), ("not_empty", "is not empty")],
    "number": [("eq", "="), ("ne", "≠"), ("gt", ">"), ("gte", "≥"), ("lt", "<"), ("lte", "≤"),
               ("between", "between"), ("in", "is one of")],
    "group": [("eq", "is"), ("ne", "is not")],
}

APP_GROUP = "app_group"

MEASURES = {
    "connections": ("Connections", "count()"),
    "users": ("Users (identified)", "uniqExactIf(subscriber_id, position(subscriber_id, '_') = 0)"),
    "unidentified": ("Unidentified connections", "countIf(position(subscriber_id, '_') > 0)"),
    "volume_gb": ("Volume (GB)", "round(sum(c2s_byte_num + s2c_byte_num) / 1e9, 3)"),
    "uplink_gb": ("Uplink (GB)", "round(sum(c2s_byte_num) / 1e9, 3)"),
    "downlink_gb": ("Downlink (GB)", "round(sum(s2c_byte_num) / 1e9, 3)"),
    "avg_duration_s": ("Avg duration (s)", "round(avg(duration) / 1000, 2)"),
    "first_seen": ("First seen", f"toDateTime(min(start_time), '{TZ}')"),
    "last_seen": ("Last seen", f"toDateTime(max(start_time), '{TZ}')"),
}

TIME_GROUPS = {
    "day": ("Day", f"toDate(toDateTime(start_time, '{TZ}'))"),
    "hour": ("Hour", f"toStartOfHour(toDateTime(start_time, '{TZ}'))"),
    "hour_of_day": ("Hour of day", f"toHour(toDateTime(start_time, '{TZ}'))"),
}


class ExploreError(ValueError):
    pass


class FilterIn(BaseModel):
    field: str
    op: str
    value: str = ""


class ExploreQuery(BaseModel):
    start: str
    end: str
    filters: list[FilterIn] = []
    mode: Literal["records", "summary"] = "records"
    columns: list[str] = []
    sort: str = "start_time"
    desc: bool = True
    limit: int = Field(default=100, ge=1, le=MAX_PAGE)
    offset: int = Field(default=0, ge=0, le=MAX_OFFSET)
    group_by: list[str] = []
    measures: list[str] = ["connections", "users", "volume_gb"]
    identified_only: bool = False
    hide_background: bool = False


def background_labels() -> list[str]:
    vl = dictionary.base().get("value_lists", {}).get("generic_labels", {})
    return [str(x) for x in (vl.get("list", []) if isinstance(vl, dict) else [])]


def _kind(name: str, typ: str) -> str:
    if name in TIME_COLS:
        return "time"
    if re.match(r"^(Nullable\()?(LowCardinality\()?(U?Int\d+|Float\d+|Decimal)", typ or ""):
        return "number"
    return "string"


def fields() -> list[dict]:
    out = []
    for c in dictionary.effective()["columns"]:
        if c["empty"]:
            continue
        out.append({"name": c["name"], "type": c["type"], "kind": _kind(c["name"], c["type"]),
                    "description": c["description"]})
    out.insert(0, {"name": APP_GROUP, "type": "app group", "kind": "group",
                   "description": "What kind of app it is (adult content, VPN, social media, games...). Covers every app_label of that kind.",
                   "options": [{"value": c["key"], "label": c["key"].replace("_", " "), "description": c["description"]}
                               for c in concepts.catalogue().values()]})
    return out


def _field_map() -> dict[str, str]:
    return {f["name"]: f["kind"] for f in fields()}


_DT = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(:\d{2})?$")


def _dt(value: str, label: str) -> datetime:
    m = _DT.match((value or "").strip())
    if not m:
        raise ExploreError(f"The {label} time is not valid. Use the date and time pickers.")
    try:
        return datetime.fromisoformat(f"{m.group(1)} {m.group(2)}{m.group(3) or ':00'}")
    except ValueError:
        raise ExploreError(f"The {label} time {value.strip()!r} does not exist. Use the date and time pickers.") from None


def normalize_msisdn(v: str) -> str:
    if not re.fullmatch(r"[\d\s()+-]+", v.strip()):
        return v.strip()
    digits = re.sub(r"\D", "", v)
    if digits.startswith("0") and len(digits) == 11:
        return "92" + digits[1:]
    if digits.startswith("0092"):
        return digits[2:]
    if len(digits) == 10 and digits.startswith("3"):
        return "92" + digits
    return digits or v.strip()


def msisdn_forms(v: str) -> list[str]:
    return [v, "0" + v[2:]] if re.fullmatch(r"92\d{10}", v) else [v]


def _number_items(v: str) -> list[str]:
    if re.search(r"[;\n]", v):
        parts = re.split(r"[;\n]", v)
    else:
        parts = re.split(r",\s+|\s+|,(?!\d{3}(?!\d))", v)
    return [p.strip() for p in parts if p.strip()]


def _phone_like(v: str) -> bool:
    digits = re.sub(r"\D", "", v or "")
    return bool(re.fullmatch(r"[\d\s()+-]+", (v or "").strip())) and bool(re.match(r"(0092|92|0)?3", digits)) \
        and len(digits) >= 3


def _national_digits(v: str) -> str:
    digits = re.sub(r"\D", "", v or "")
    for prefix in ("0092", "92", "0"):
        if digits.startswith(prefix) and digits[len(prefix):].startswith("3"):
            return digits[len(prefix):]
    return digits


def _split(v: str, field: str = "") -> list[str]:
    v = v or ""
    quoted = re.findall(r"'((?:[^'\\]|\\.)*)'|\"([^\"]*)\"", v)
    if quoted:
        return [a or b for a, b in quoted if (a or b).strip()]
    if re.search(r"[;\n]", v):
        return [p.strip() for p in re.split(r"[;\n]", v) if p.strip()]
    pieces = [p.strip() for p in v.split(",") if p.strip()]
    if field in ("geo", "client_geo", "server_geo") and len(pieces) % 2 == 0 and \
            all(re.fullmatch(r"-?\d+(\.\d+)?", p) for p in pieces):
        return [f"{pieces[i]},{pieces[i + 1]}" for i in range(0, len(pieces), 2)]
    if field == "loc":
        out, cur = [], []
        for p in pieces:
            if re.match(r"(House|Cell site)\b", p, re.I) and cur:
                out.append(", ".join(cur))
                cur = []
            cur.append(p)
        return out + ([", ".join(cur)] if cur else [])
    if field in ("server_asn", "client_asn", "client_location", "server_location"):
        from . import catalog
        known = {str(x) for x, _ in (catalog.load().get(field) or [])}
        out, i = [], 0
        while i < len(pieces):
            for j in range(len(pieces), i, -1):
                joined = ", ".join(pieces[i:j])
                if joined in known or j == i + 1:
                    out.append(joined)
                    i = j
                    break
        return out
    return pieces


def _num(v: str, field: str) -> float:
    try:
        n = float(v.replace(",", "").strip())
    except ValueError as e:
        raise ExploreError(f"'{v}' is not a number (filter on {field}).") from e
    if not math.isfinite(n):
        raise ExploreError(f"'{v}' is not a usable number (filter on {field}).")
    return n


def literal(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(literal(x) for x in v) + "]"
    if isinstance(v, (int, float)):
        return repr(int(v)) if float(v).is_integer() else repr(v)
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _param_text(v) -> str:
    if isinstance(v, list):
        return literal(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


class Built:
    def __init__(self, sql: str, params: dict, kind_of: dict, key_cols: int = 0):
        self.sql = sql
        self.params = {k: _param_text(v) for k, v in params.items()}
        self._raw = params
        self._kind_of = kind_of
        self.key_cols = key_cols

    @property
    def display_sql(self) -> str:
        def sub(m):
            return literal(self._raw[m.group(1)])
        sql = re.sub(r",\n\s+toDateTime\(start_time, '[^']+'\) AS __t, log_id AS __id", "", self.sql)
        return re.sub(r"\{(\w+):[^}]+\}", sub, sql)


def _where(q: ExploreQuery, fmap: dict, params: dict) -> list[str]:
    start, end = _dt(q.start, "start"), _dt(q.end, "end")
    if end <= start:
        raise ExploreError("The end time must be after the start time.")
    if MAX_DAYS and end - start > timedelta(days=MAX_DAYS):
        raise ExploreError(f"Pick a range of at most {MAX_DAYS} days.")
    params["t_start"] = start.strftime("%Y-%m-%d %H:%M:%S")
    params["t_end"] = end.strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"start_time >= toUnixTimestamp({{t_start:String}}, '{TZ}')",
             f"start_time < toUnixTimestamp({{t_end:String}}, '{TZ}')"]
    if q.identified_only:
        parts.append("position(subscriber_id, '_') = 0")
    if q.hide_background and (bg := background_labels()):
        params["bg"] = bg
        parts.append("NOT has({bg:Array(String)}, app_label)")

    for i, f in enumerate(q.filters):
        kind = fmap.get(f.field)
        if kind is None:
            raise ExploreError(f"Unknown field: {f.field}.")
        if kind == "time":
            raise ExploreError(f"Filter {f.field} with the time range instead.")
        col, p, op = f.field, f"p{i}", f.op
        valid = {k for k, _ in OPS[kind]}
        if kind == "group":
            if op not in valid:
                raise ExploreError(f"'{op}' is not a valid condition for {col}.")
            labels = concepts.labels(f.value.strip())
            if not labels:
                raise ExploreError("Choose an app group from the list.")
            params[p] = labels
            parts.append(f"{'NOT ' if op == 'ne' else ''}has({{{p}:Array(String)}}, app_label)")
            continue
        if op not in valid:
            raise ExploreError(f"'{op}' is not a valid condition for {col}.")
        value = f.value.strip()
        if op not in ("empty", "not_empty") and not value:
            raise ExploreError(f"Enter a value for the filter on {col}.")

        if kind == "string":
            if col == "subscriber_id" and op in ("eq", "ne", "in"):
                forms = [f for x in _split(value) for f in msisdn_forms(normalize_msisdn(x))]
                if len(forms) > 1 and op in ("eq", "in"):
                    op, value = "in", ", ".join(dict.fromkeys(forms))
                elif len(forms) > 1:
                    params[p] = forms; parts.append(f"NOT has({{{p}:Array(String)}}, {col})")
                    continue
                else:
                    value = forms[0] if forms else value
            if op == "eq":
                params[p] = value; parts.append(f"{col} = {{{p}:String}}")
            elif op == "ne":
                params[p] = value; parts.append(f"{col} != {{{p}:String}}")
            elif col == "subscriber_id" and op in ("contains", "not_contains", "starts") and _phone_like(value):
                nsn = _national_digits(value)
                if op == "starts":
                    params[p + "a"], params[p + "b"] = "92" + nsn, "0" + nsn
                    parts.append(f"(startsWith({col}, {{{p}a:String}}) OR startsWith({col}, {{{p}b:String}}))")
                else:
                    params[p] = nsn
                    parts.append(f"position({col}, {{{p}:String}}) {'>' if op == 'contains' else '='} 0")
            elif op == "contains":
                params[p] = value; parts.append(f"positionCaseInsensitiveUTF8({col}, {{{p}:String}}) > 0")
            elif op == "not_contains":
                params[p] = value; parts.append(f"positionCaseInsensitiveUTF8({col}, {{{p}:String}}) = 0")
            elif op == "starts":
                params[p] = value; parts.append(f"startsWith({col}, {{{p}:String}})")
            elif op == "in":
                items = _split(value, col)
                if not items:
                    raise ExploreError(f"List at least one value for {col}, separated by commas.")
                params[p] = items; parts.append(f"has({{{p}:Array(String)}}, {col})")
            elif op == "empty":
                parts.append(f"{col} = ''")
            elif op == "not_empty":
                parts.append(f"{col} != ''")
        else:
            if op == "between":
                items = _number_items(re.sub(r"(?i)\s+(and|to)\s+|\s+-\s+|(?<=\d)-(?=\d)", ";", value))
                if len(items) != 2:
                    raise ExploreError(f"Enter two numbers for 'between' on {col}, e.g. 100, 5000.")
                lo, hi = sorted(_num(x, col) for x in items)
                params[p + "a"], params[p + "b"] = lo, hi
                parts.append(f"{col} BETWEEN {{{p}a:Float64}} AND {{{p}b:Float64}}")
            elif op == "in":
                params[p] = [_num(x, col) for x in _number_items(value)]
                parts.append(f"has({{{p}:Array(Float64)}}, toFloat64({col}))")
            else:
                sym = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
                params[p] = _num(value, col)
                parts.append(f"{col} {sym} {{{p}:Float64}}")
    return parts


def build(q: ExploreQuery, *, count: bool = False, export_limit: int | None = None) -> Built:
    fmap = _field_map()
    params: dict = {}
    where = "\n  AND ".join(_where(q, fmap, params))
    table = settings.full_table

    if count:
        return Built(f"SELECT count() AS records\nFROM {table}\nWHERE {where}", params, fmap)

    if q.mode == "records":
        cols = [c for c in (q.columns or DEFAULT_COLUMNS) if c in fmap and fmap[c] != "group"] or ["start_time"]
        select = ",\n       ".join(f"toDateTime({c}, '{TZ}') AS {c}" if fmap[c] == "time" else c for c in cols)
        sort = q.sort if q.sort in fmap and fmap[q.sort] != "group" else "start_time"
        limit = export_limit or q.limit
        offset = 0 if export_limit else q.offset
        keys = 0 if export_limit else 2
        if keys:
            select += f",\n       toDateTime(start_time, '{TZ}') AS __t, log_id AS __id"
        sql = (f"SELECT {select}\nFROM {table}\nWHERE {where}\n"
               f"ORDER BY {sort} {'DESC' if q.desc else 'ASC'}" + (", log_id" if sort != "log_id" else "") +
               f"\nLIMIT {int(limit)}" + (f" OFFSET {int(offset)}" if offset else ""))
        return Built(sql, params, fmap, key_cols=keys)

    groups = []
    for g in q.group_by[:3]:
        if g in TIME_GROUPS:
            groups.append((g, TIME_GROUPS[g][1]))
        elif g in fmap and fmap[g] not in ("time", "group"):
            groups.append((g, g))
        else:
            raise ExploreError(f"Cannot group by {g}.")
    measures = [m for m in q.measures if m in MEASURES] or ["connections"]
    select = [f"{expr} AS {name}" if expr != name else name for name, expr in groups]
    select += [f"{MEASURES[m][1]} AS {m}" for m in measures]
    sql = f"SELECT {', '.join(select)}\nFROM {table}\nWHERE {where}"
    if groups:
        sql += f"\nGROUP BY {', '.join(name for name, _ in groups)}"
        time_group = next((name for name, _ in groups if name in TIME_GROUPS), None)
        order = f"{time_group} ASC" if time_group else f"{measures[0]} DESC"
        sql += f"\nORDER BY {order}"
    sql += f"\nLIMIT {int(export_limit or q.limit)}"
    return Built(sql, params, fmap)


def values(field: str, search: str, day: str, db) -> list[dict]:
    fmap = _field_map()
    if fmap.get(field) != "string":
        return []
    sql = f"""
        SELECT {field} AS value, count() AS rows
        FROM {settings.full_table}
        WHERE start_time >= toUnixTimestamp({{d:String}}, '{TZ}')
          AND start_time <  toUnixTimestamp({{d:String}}, '{TZ}') + 86400
          AND {field} != '' AND positionCaseInsensitiveUTF8({field}, {{q:String}}) > 0
        GROUP BY value ORDER BY rows DESC LIMIT 30"""
    res = db.cached_query(sql, max_rows=30, params={"d": f"{day} 00:00:00", "q": search}, ttl=1800)
    return [{"value": v, "rows": n} for v, n in res.rows]


def record_sql(t: str, log_id: str) -> tuple[str, dict]:
    when = _dt(t, "record")
    cols = [f["name"] for f in fields() if f["kind"] != "group"]
    select = ", ".join(f"toDateTime({c}, '{TZ}') AS {c}" if c in TIME_COLS else c for c in cols)
    sql = (f"SELECT {select}\nFROM {settings.full_table}\n"
           f"WHERE start_time = toUnixTimestamp({{t:String}}, '{TZ}') AND log_id = {{id:String}}\nLIMIT 1")
    return sql, {"t": when.strftime("%Y-%m-%d %H:%M:%S"), "id": log_id}


_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_IP_PORT = re.compile(r"^[0-9a-f.:]+_\d+$", re.I)
_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.I)
_ACCOUNT_ID = re.compile(r"[A-Z]{2,4}\d{8}|[a-z]{2,5}\d{8}|\d{6}[a-z]{3,7}")
_FILLER = {"in", "on", "at", "of", "for", "the", "a", "an", "and", "or", "me", "mein", "ka", "ki", "ke", "se", "show",
           "find", "records", "record", "data", "traffic", "user", "users", "usage", "used", "use", "by", "from",
           "to", "with", "all", "apps", "app", "sites", "site", "number", "phone", "mobile", "ip", "address",
           "pakistan", "imsi", "imei", "sim", "subscriber", "website", "websites", "domain", "server", "city"}


def _private_ip(ip: str) -> bool:
    a, b = (int(x) for x in ip.split(".")[:2])
    return a == 10 or (a == 100 and 64 <= b <= 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def interpret(text: str) -> dict:
    from .context import phones

    rest = text or ""
    filters, said = [], []

    found = phones(rest)
    if found:
        ids = [f for _, stored in found for f in msisdn_forms(stored)]
        filters.append({"field": "subscriber_id", "op": "in", "value": ", ".join(ids)})
        said.append("subscriber " + ", ".join(stored for _, stored in found))
        for raw, _ in found:
            rest = rest.replace(raw, " ")

    words = []
    from . import catalog as _catalog
    for tok in re.split(r"[\s,;]+", rest):
        t = tok.strip().strip("'\"()")
        if not t:
            continue
        url = re.match(r"^(?:[a-z]+://)?(?:\*\.)?([a-z0-9.-]+\.[a-z]{2,})(?::\d+)?(?:/.*)?$", t, re.I)
        if url and ("/" in t or ":" in t or t.startswith("*.")) and not _IPV4.match(url.group(1)):
            t = url.group(1)
        if _ACCOUNT_ID.fullmatch(t):
            filters.append({"field": "subscriber_id", "op": "eq", "value": t}); said.append(f"subscriber {t}")
            continue
        if _IP_PORT.match(t):
            filters.append({"field": "subscriber_id", "op": "eq", "value": t}); said.append(f"unidentified subscriber {t}")
        elif _IPV4.match(t) and all(0 <= int(x) <= 255 for x in t.split(".")):
            col = "client_ip" if _private_ip(t) else "server_ip"
            filters.append({"field": col, "op": "eq", "value": t})
            said.append(f"{'subscriber' if col == 'client_ip' else 'server'} IP {t}")
        elif t.count(":") >= 2 and re.fullmatch(r"[0-9a-f:]+", t, re.I):
            filters.append({"field": "server_ip", "op": "eq", "value": t}); said.append(f"server IP {t}")
        elif re.fullmatch(r"\d{15}", t):
            col = "imsi" if t.startswith("410") else "imei"
            filters.append({"field": col, "op": "eq", "value": t}); said.append(f"{col.upper()} {t}")
        elif _DOMAIN.match(t) and not t.replace(".", "").isdigit():
            filters.append({"field": "requested_server", "op": "contains", "value": t.lower()})
            said.append(f"website containing {t.lower()}")
        else:
            words.append(t)

    plain = " ".join(words)
    used: set[str] = set()
    sim = concepts.wants_sim_operator(plain)
    for c in concepts.isps_in(plain):
        if c["mnc"] and (sim or not c["asn"]):
            filters.append({"field": "mnc", "op": "in" if len(c["mnc"]) > 1 else "eq", "value": ", ".join(c["mnc"])})
            said.append(f"{c['name']} SIM subscribers (records with an operator code)")
        else:
            filters.append({"field": "client_asn", "op": "in" if len(c["asn"]) > 1 else "eq", "value": ", ".join(c["asn"])})
            said.append(f"{c['name']} network (ISP)")
        used |= {w for w in c["words"] if f" {w} " in f" {plain.lower()} "} | ({"sim", "sims"} if sim else set())
    from . import catalog
    places: dict[str, list[str]] = {}
    for e in catalog.entities(plain):
        if e.kind in ("city", "region", "place"):
            places.setdefault("client_location", []).extend(e.values)
            said.append(e.name if e.kind != "region" else f"{e.name} (province, city unknown)")
        elif e.kind in ("area", "road"):
            filters.append({"field": "loc", "op": "contains", "value": e.values[0]}); said.append(f"{e.name} (in addresses)")
        else:
            continue
        used |= set(catalog.norm(e.name).split()) | set(catalog.norm(e.values[0]).split())
    if places.get("client_location"):
        vals = list(dict.fromkeys(places["client_location"]))
        filters.append({"field": "client_location", "op": "in" if len(vals) > 1 else "eq",
                        "value": "; ".join(vals) if any("," in v for v in vals) else ", ".join(vals)})
    for short, full in concepts._CITY_SHORT.items():
        if f" {short} " in f" {plain.lower()} ":
            used.add(short)

    catalogue = concepts.catalogue()
    calls = concepts.call_labels(plain)
    names = [n for n in concepts.apps_in(plain) if n not in used]
    for n in names:
        if n in calls:
            filters.append({"field": "app_label", "op": "in" if len(calls[n]) > 1 else "eq", "value": ", ".join(calls[n])})
            said.append(f"{n} calls ({', '.join(calls[n])})")
        elif n in catalogue:
            filters.append({"field": APP_GROUP, "op": "eq", "value": n}); said.append(f"{n} (all its traffic types)")
        else:
            labels = concepts.app_names()[n]
            filters.append({"field": "app_label", "op": "in" if len(labels) > 1 else "eq", "value": ", ".join(labels)})
            said.append(", ".join(labels))
        used.add(n)
    for i in range(len(words) - 1):
        pair = f"{words[i].lower()} {words[i + 1].lower()}"
        if concepts._app_spellings().get(pair) in names:
            used |= {words[i].lower(), words[i + 1].lower()}
    for w in words:
        if set(concepts.apps_in(w)) & set(names):
            used.add(w.lower())
    left = " ".join(w for w in words if w.lower() not in used)
    kinds = [c for c in concepts.match(left) if c["key"] not in used and c["key"] not in names
             and not (calls and c["key"] == "calls")]
    if kinds:
        c = kinds[0]
        filters.append({"field": APP_GROUP, "op": "eq", "value": c["key"]})
        said.append(f"{c['key'].replace('_', ' ')} apps")
        used |= set(c["synonyms"]) | {c["key"].replace("_", " ")}
    if calls:
        used |= {"call", "calls", "calling", "voice"}

    unknown = [w for w in words if w.lower() not in used and w.lower() not in _FILLER
               and not any(w.lower() in u.split() for u in used)
               and catalog.norm(catalog.correct_typos(catalog.norm(w))) not in used]
    return {"filters": filters, "understood": said, "unknown": unknown}
