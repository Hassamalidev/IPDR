from __future__ import annotations

import re

from .config import settings
from .llm import Tool

SQL_INSTRUCTIONS = """\
You are the query engine of an analysis tool for a telecom data team. People ask questions about internet \
usage records (IPDR) of mobile and fixed-line subscribers in their own words. You understand each question, turn it into one ClickHouse \
SELECT that answers it exactly, and submit it. People see your result as a table, so the SQL \
must be right.

Questions arrive in any shape: misspelt, very short, run together, in English, Urdu, Roman Urdu or a mix \
("karachi me sb se zyada kon si app", "tiktok khi last wk", "how many ppl use yt at nite"), with \
abbreviations, local terms, or details missing. Never expect a tidy question.

Work in this order
1. Understand the question first. Work out what the person wants to know: the measure (users, \
connections, data volume...), the things being compared or listed, the place, the period, and any filter. \
Fix spelling, expand short forms, translate Urdu or Roman Urdu, and resolve local terms with team_terms in \
the data dictionary. Turn relative dates ("last week", "yesterday") into real dates from the context. \
Fill anything missing with the defaults in the data dictionary. Write the result as one clear, complete \
English question: this is the "understood" field, and everything else must answer exactly that question. \
Keep every detail the person gave (app, ISP, place, site, number, period, measure): dropping one, such as the ISP \
in "top whatsapp nayatel users from islamabad", answers a different question.
2. Understand words by their meaning, not their spelling. A word that names a KIND of app, service or \
content (adult, porn, social media, VPN, dating, games, AI, music, calls, shopping, gambling...) is a concept: \
filter with that concept's full app_label list from app_concepts in the data dictionary. Never search app_label \
for such a word; app names rarely contain the word for their kind. When a word could mean something this data \
does not hold (age, gender, income, device model), use the meaning the data can answer, and state it as an \
assumption: "adult" means adult-content apps, because there is no age data. Use "cannot_answer" only when no \
sensible meaning can be answered from this data, and then say what can be answered instead.
3. If the message holds several questions, answer the main one and say in the explanation which part you \
answered, so the person can ask the rest separately.
4. Check every name before using it. Names listed under <resolved> in the message are already looked up: use \
those exact values and filters directly, without find_values. ISP and operator names (PTCL, Nayatel, Zong...) \
filter client_asn by isp_names. Look up any other place, app, ISP, organisation or website with find_values, and use the exact stored values (WhatsApp is stored as 'WhatsAPP'). One app often \
has several labels; include all of them. Look up several names in parallel when you can.
5. Write the SQL by the query rules in the data dictionary. Answer exactly what was asked: no extra groups or \
rows the question did not ask for (such as an 'Other' bucket), and the complete app_label list when you use a \
concept. When the measure is not stated or could be read several ways ("usage", "popular", "most", "kitna"), \
return users, connections and volume_gb together so every reading is answered, ordered by the team's default.
6. Decide what one row of the answer is. "Top 10 WhatsApp users", "who used VPN", "heavy PTCL users" ask for \
PEOPLE: one row per identified subscriber (metrics.top_subscribers). "Top apps", "which city" ask for things \
ranked by a measure. "Address / ISP / IMSI of this user" asks for stored details of given subscribers.
7. Call submit_answer as soon as the SQL is written. The system runs it and sends back any error, empty result \
or problem it finds, so you do not need to test it first. Use run_sql only when you need to see data before \
you can write the final SQL. Never put the answer in plain text.

For follow-up questions ("and Lahore?", "same for last month", "their addresses", "this user's ISP"), use \
the conversation so far: keep the earlier measure, filters and period unless the person changes them, and adapt \
the MOST RECENT earlier SQL rather than starting over. "This user", "these numbers", "their", "inka" mean the \
rows of the most recent answer; their exact values are given under <resolved>. The "understood" field then states the full question, not just the follow-up.

Reference queries are earlier questions the team confirmed were answered correctly. They show proven \
patterns for this data. Use a pattern when it fits, but answer the question actually asked, which is \
usually different.

Status
- "ok": you have SQL that answers the question.
- "clarify": only when no sensible default exists and a wrong guess would change the answer, for example \
a place name you cannot find, or a name that matches several different places equally well. Ask one short \
question and list the options you found.
- "cannot_answer": the data does not hold what is needed (device models, roaming and SSL certificate details \
are empty columns), or the question is not about this data. In plain words, say what is missing and what the \
data can answer instead, as a question the user could send next. No SQL is run for it, so do not mention "this \
query" or results.

Answer fields
- understood: the question as you understood it, as one clear and complete English sentence with real \
dates and names, e.g. "Which apps had the most identified users in Karachi from 31 Oct to 9 Nov 2026?". \
Fill it for every status, including "clarify" and "cannot_answer".
- title: a short heading for the result, e.g. "Most used apps, Karachi, 31 Oct to 9 Nov".
- sql: the final SQL, laid out on several lines. Empty unless status is "ok".
- explanation: one or two sentences for a reader who does not know SQL, saying what is counted and how.
- assumptions: every choice the user did not state, as label and value: Period, Area (and which location \
source), Measure, Excluded, Coverage. One or two words per label, short values. Only state what the SQL really \
does: "Excluded" only when the SQL excludes something, "Coverage" only when it uses the partial columns \
(loc, geo, cid, lac, mcc, mnc), "Area" only when it filters a place.
- chart: the best chart for the result columns. type: "bar" for rankings and comparisons, "line" for change \
over time, "area" for one total over time, "pie" for the share of a whole across a few categories, "treemap" \
for shares across many, "stacked_bar" for additive totals split by a second category, "heatmap" for two \
categories (or weekday and hour) against one measure, "calendar" for a measure per day, "clock" for a measure by \
hour of day, "sankey" for how one category flows into another, "combo" for two measures on one axis, "radar" \
for a few items compared on 3+ measures, "scatter" for two measures, "map" when the result has lon and lat, \
"table" when no chart helps. Shares, stacking, pie, treemap and sankey never use user counts (one user is in \
several groups). If the user names a chart type, use it. x: category or time column. y: measure column. \
series: breakdown column or "".
- message: the question to ask for "clarify", the reason for "cannot_answer", otherwise "".

Write every text field for analysts: short factual sentences, no emojis, no exclamation marks, no filler.\
"""

CONCLUSION_INSTRUCTIONS = """\
You write the conclusion under a query result for analysts at a telecom data team. They have the table \
and chart in front of them; your job is to say what the result shows. Call submit_conclusion with it.

Rules
- Use only the numbers in the result and the summary given to you, or simple arithmetic on them (shares, \
differences, ratios, rankings). Never invent or estimate a figure that is not derivable from them.
- Round sensibly (1.84M, 23%, 532 GB) and name the measure and the period.
- A share or percentage must say what it is a share of. When the result is a top-N list, a share of the \
list total is "of the top N shown", never "of all usage" or "of the city".
- headline: the direct answer to the question, in one sentence.
- findings: two to five points, one sentence each, most important first. Prefer comparisons and patterns \
(who leads by how much, peaks, gaps, outliers) over repeating single rows.
- caveats: only those that change how the result should be read (partial location coverage, excluded \
traffic, an incomplete day, a result cut off at the row limit). Empty list if none apply.
- Do not guess causes. If a cause is worth mentioning, mark it as a possibility.
- Plain, short sentences. No emojis, no exclamation marks, no filler such as "Overall".\
"""


def sql_system_prompt(dictionary_text: str) -> str:
    return f"{SQL_INSTRUCTIONS}\n\n<data_dictionary>\n{dictionary_text}</data_dictionary>"


def _turn_text(i: int, msg_user: dict | None, msg_ai: dict | None) -> list[str]:
    lines = []
    if msg_user:
        q = msg_user["content"] if msg_user["kind"] == "question" else f"(ran their own SQL)\n{msg_user['content']}"
        lines.append(f"Turn {i}. User: {q}")
    if not msg_ai:
        if msg_user:
            lines.append("(No answer was given to this question: it was interrupted before it finished.)")
        return lines
    d = msg_ai.get("data") or {}
    if d.get("understood"):
        lines.append(f"Understood as: {d['understood']}")
    if msg_ai["kind"] == "result":
        lines.append(f"Answer: {d.get('title', '')}")
        if d.get("assumptions"):
            lines.append("Assumptions: " + "; ".join(f"{a['label']}: {a['value']}" for a in d["assumptions"]))
        lines.append(f"SQL:\n{d.get('sql', '')}")
        res = d.get("result") or {}
        cols = [c["name"] for c in res.get("columns", [])]
        rows = res.get("rows", [])[:5]
        if cols:
            lines.append(f"Result ({res.get('row_count', len(rows))} rows), first rows: {', '.join(cols)}")
            lines += ["  " + ", ".join(str(v) for v in r) for r in rows]
        if d.get("conclusion"):
            lines.append(f"Conclusion shown: {d['conclusion'].get('headline', '')}")
    elif msg_ai["kind"] in ("clarify", "cannot_answer"):
        lines.append(f"Assistant replied: {msg_ai['content']}")
    elif msg_ai["kind"] == "error":
        lines.append("(That question failed with an error.)")
    return lines


def periods(data_range: dict) -> dict:
    from datetime import date, timedelta

    try:
        d = date.fromisoformat(data_range["last_full_day"])
        first = date.fromisoformat(data_range["first"][:10])
    except (KeyError, ValueError, TypeError):
        return {}
    one = timedelta(days=1)
    month_start = d.replace(day=1)
    prev_month_end = month_start - one
    week_start = d - timedelta(days=d.weekday())
    sunday = d - timedelta(days=(d.weekday() - 6) % 7)
    spans = {
        "day": ("today / yesterday / last day (all mean the last full day of data)", d, d),
        "day_before": ("the day before that (day before yesterday)", d - one, d - one),
        "3d": ("last 3 days", d - 2 * one, d),
        "7d": ("last week / last 7 days", d - 6 * one, d),
        "this_week": ("this week (Monday to last full day)", week_start, d),
        "prev_week": ("previous calendar week (Mon-Sun)", week_start - 7 * one, week_start - one),
        "10d": ("last 10 days", d - 9 * one, d),
        "14d": ("last 14 days / last two weeks", d - 13 * one, d),
        "30d": ("last 30 days", d - 29 * one, d),
        "this_month": ("this month so far", month_start, d),
        "prev_month": ("last month / previous calendar month (as far as the data goes)", prev_month_end.replace(day=1),
                       prev_month_end),
        "weekend": ("last weekend (Saturday and Sunday)", sunday - one, sunday),
        "all": ("all data", first, d),
    }
    return {k: (label, max(s, first), e) for k, (label, s, e) in spans.items()}


def date_helpers(data_range: dict) -> list[str]:
    from datetime import timedelta

    ps = periods(data_range)
    if not ps:
        return []
    lines = ["Ready-made periods (copy these exactly; they already account for the data not being live):"]
    for label, s, e in ps.values():
        lines.append(f"- {label}: {s.isoformat()} to {e.isoformat()} -> start_time >= "
                     f"toUnixTimestamp('{s} 00:00:00', 'Asia/Karachi') AND start_time < "
                     f"toUnixTimestamp('{e + timedelta(days=1)} 00:00:00', 'Asia/Karachi')")
    return lines


SQL_DATE = re.compile(r"'(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)'")


def without_dates(sql: str) -> str:
    n = [0]

    def sub(_m):
        n[0] += 1
        return "'<start>'" if n[0] % 2 else "'<end>'"
    return SQL_DATE.sub(sub, sql)


_EXAMPLE_VALUES = [
    (re.compile(r"(?<![\w.])(?<!NOT )(app_label)\s+IN\s*\(\s*'[^)]*\)", re.I), r"\1 IN (<the app's or kind's labels>)"),
    (re.compile(r"(?<![\w.])(app_label)\s*=\s*'[^']*'", re.I), r"\1 = '<label>'"),
    (re.compile(r"(?<![\w.])(app_label)\s+LIKE\s+'[^']*'", re.I), r"\1 IN (<the app's labels>)"),
    (re.compile(r"(?<![\w.])(?<!NOT )(client_location)\s+IN\s*\(\s*'[^)]*\)", re.I), r"\1 IN ('<City> Pakistan', ...)"),
    (re.compile(r"(?<![\w.])(client_location)\s*=\s*'[^']*'", re.I), r"\1 = '<City> Pakistan'"),
    (re.compile(r"(?<![\w.])(?<!NOT )(client_asn)\s+IN\s*\(\s*'[^)]*\)", re.I), r"\1 IN (<the ISP's stored names>)"),
    (re.compile(r"(?<![\w.])(client_asn)\s*=\s*'[^']*'", re.I), r"\1 = '<ISP stored name>'"),
    (re.compile(r"(?<![\w.])(subscriber_id)\s+IN\s*\(\s*'[^)]*\)", re.I), r"\1 IN (<the subscriber ids>)"),
    (re.compile(r"(?<![\w.])(requested_server)\s*=\s*'[^']*'", re.I), r"\1 = '<hostname>'"),
    (re.compile(r"(?<![\w.])(server_asn)\s+IN\s*\(\s*'[^)]*\)", re.I), r"\1 IN (<the server owner's names>)"),
    (re.compile(r"endsWith\(\s*server_location\s*,\s*'[^']*'\s*\)", re.I), "endsWith(server_location, '<Country>')"),
    (re.compile(r"(?<![\w.])(client_ip|imei|imsi)\s*=\s*'[^']*'", re.I), r"\1 = '<value>'"),
    (re.compile(r"countIf\(\s*app_label\s+(IN\s*\([^)]*\)|LIKE\s+'[^']*')\s*\)", re.I), "countIf(app_label IN (<labels of A or B>))"),
    (re.compile(r"(loc\s+I?LIKE\s+)'%[^%']+%'", re.I), r"\1'%<Area>%'"),
]


def template_example(question: str, sql: str) -> tuple[str, str]:
    from . import catalog, concepts
    q = question
    for name in sorted(concepts.apps_in(question), key=len, reverse=True):
        q = re.sub(rf"(?i)\b{re.escape(name)}\b", "<app>", q)
    for c in concepts.match(question):
        for w in sorted(c["synonyms"] + [c["key"].replace("_", " ")], key=len, reverse=True):
            q = re.sub(rf"(?i)(?<![a-z_]){re.escape(w)}(?![a-z_])", "<kind of app>", q)
    for e in catalog.entities(question):
        placeholder = {"city": "<city>", "isp": "<ISP>", "website": "<hostname>", "subscriber": "<subscriber>",
                       "area": "<area>", "server_country": "<country>", "server_org": "<server owner>",
                       "ip": "<IP>"}.get(e.kind)
        if placeholder:
            for word in {e.name, e.name.split(",")[0]}:
                q = re.sub(rf"(?i)(?<![\w]){re.escape(word)}(?![\w])", placeholder, q)
    s = sql
    for rx, rep in _EXAMPLE_VALUES:
        s = rx.sub(rep, s)
    q = re.sub(r"(<app>|<kind of app>)(\s*(,|and|or)\s*\1)+", r"\1s", q)
    return q, s


_PHONE = re.compile(r"(?<!\d)(?:\+?92|0092|92|0)?[ -]?(3\d{2})[ -]?(\d{3})[ -]?(\d{4})(?!\d)")


def phones(text: str) -> list[tuple[str, str]]:
    return [(m.group(0).strip(" -"), "92" + "".join(m.groups())) for m in _PHONE.finditer(text or "")]


def phone_forms(stored: str) -> list[str]:
    return [stored, "0" + stored[2:]] if stored.startswith("92") and len(stored) == 12 else [stored]


def phone_sql(numbers: list[tuple[str, str]]) -> str:
    ids = [f for _, stored in numbers for f in phone_forms(stored)]
    return "subscriber_id IN (" + ", ".join(f"'{i}'" for i in dict.fromkeys(ids)) + ")"


def operators(text: str) -> list[tuple[str, str]]:
    from . import concepts
    sim = concepts.wants_sim_operator(text)
    return [(c["name"], concepts.isp_filter(c, sim)) for c in concepts.isps_in(text)]


def operator_note(text: str) -> str:
    found = operators(text)
    if not found:
        return ""
    parts = "; ".join(f"{n} is {f}" for n, f in found)
    note = (f"ISPs and operators: {parts}. client_asn is the subscriber's network provider (ISP), filled on about 98% "
            "of records. For a breakdown by ISP, group by client_asn.")
    if any("mnc" in f for _, f in found):
        note += (" mnc is the SIM operator code, filled only for mobile numbers (about 30% of records): add that as a "
                 "Coverage assumption.")
    return note


_REF_NOUNS = (r"users?|subscribers?|customers?|numbers?|msisdns?|persons?|people|accounts?|ips?|sites?|websites?|"
              r"domains?|servers?|hosts?|urls?|devices?|phones?|handsets?|imsis?|imeis?|ones?|guys?|log|logon|bande?|"
              r"apps?|applications?|cit(?:y|ies)|isps?|hours?|times?|days?|dates?")
_REF = re.compile(
    rf"(?<![a-z])(this|that|these|those|same|above|previous|said)\s+"
    rf"(?:(?:\d+|one|two|three|five|ten|top|first|last|\w+)\s+){{0,2}}?({_REF_NOUNS})(?![a-z])"
    rf"|(?<![a-z])(is|iss|in|inn|un|unn|us|uss|ye|yeh|wo|woh)\s+({_REF_NOUNS})(?=\s+(?:ka|ke|ki|ko|ne|se|mein|me|"
    r"kaun|kon|kya)(?![a-z]))"
    r"|(?<![a-z])(their|theirs|them|they|his|her|hers|its|he|she|him|inka|inki|inke|unka|unki|unke|iska|iski|iske|"
    r"uska|uski|uske|inhon|unhon|inho|unho|wahan|wahaan|udhar|wahin"
    r"|(?<!is )(?<!are )(?<!was )(?<!were )(?<!be )there(?!\s+(?:is|are|was|were|been|any)\b))(?![a-z])")
_SINGULAR = {"this", "that", "is", "iss", "us", "uss", "ye", "yeh", "wo", "woh", "his", "her", "hers", "its", "he", "she",
             "him", "iska", "iski", "iske", "uska", "uski", "uske"}
_REF_KINDS = [
    (re.compile(r"(hours?|times?)$"), ["__hour"]),
    (re.compile(r"(days?|dates?)$"), ["__day"]),
    (re.compile(r"(apps?|applications?)$"), ["app_label"]),
    (re.compile(r"(city|cities)$"), ["client_location"]),
    (re.compile(r"isps?$"), ["client_asn"]),
    (re.compile(r"ips?$"), ["client_ip", "server_ip"]),
    (re.compile(r"(sites?|websites?|domains?|hosts?|urls?|servers?)$"), ["requested_server", "server_ip"]),
    (re.compile(r"(devices?|handsets?|imeis?)$"), ["imei", "subscriber_id"]),
    (re.compile(r"imsis?$"), ["imsi", "subscriber_id"]),
]
_ID_COLUMNS = ["subscriber_id", "requested_server", "client_ip", "server_ip", "imsi", "imei", "app_label",
               "client_location", "client_asn"]
_ALIAS = r"(?:\b(?:any|anyLast|min|max|argMax|argMin)\(\s*)?\b{col}\b[^,]*?\s+AS\s+([A-Za-z_]\w*)"


def _id_columns(sql: str, columns: list[str]) -> dict[str, str]:
    out = {}
    select = re.split(r"(?i)\bFROM\b", sql or "", maxsplit=1)[0]
    for col in _ID_COLUMNS:
        if col in columns:
            out[col] = col
        for alias in re.findall(_ALIAS.format(col=col), select, re.I):
            if alias in columns and alias not in out:
                out[alias] = col
    for name, col in (("city", "client_location"), ("app", "app_label"), ("isp", "client_asn"),
                      ("website", "requested_server"), ("domain", "requested_server"), ("site", "requested_server")):
        if name in columns and name not in out and col in (select or ""):
            out[name] = col
    return out


def _columns_by_values(cols: list[str], rows: list[list]) -> dict[str, str]:
    from . import catalog, concepts
    labels = {lab for labs in concepts.app_names().values() for lab in labs} | \
             {lab for c in concepts.catalogue().values() for lab in c["labels"]} | set(concepts.app_families())
    data = catalog.load()
    cities = {v for v, _ in data.get("client_location", [])} | {v.rsplit(" Pakistan", 1)[0] for v, _ in data.get("client_location", [])}
    isps = {v for v, _ in data.get("client_asn", [])} | {c["name"] for c in concepts.isp_catalogue().values()}
    out = {}
    for i, c in enumerate(cols):
        vals = [r[i] for r in rows[:20] if isinstance(r[i], str) and r[i]]
        if not vals:
            continue
        for known, col in ((labels, "app_label"), (cities, "client_location"), (isps, "client_asn")):
            if sum(v in known for v in vals) >= max(1, int(len(vals) * 0.8)):
                out[c] = col
                break
    return out


_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
             "fifth": 4, "5th": 4, "sixth": 5, "6th": 5, "seventh": 6, "7th": 6, "eighth": 7, "8th": 7, "ninth": 8,
             "9th": 8, "tenth": 9, "10th": 9, "last": -1, "pehla": 0, "pehle": 0, "doosra": 1, "dusra": 1, "teesra": 2}
_ORDINAL_REF = re.compile(
    rf"(?<![a-z])(?:the\s+)?({'|'.join(_ORDINALS)}|number\s+\d{{1,2}}|#\s*\d{{1,2}})\s+({_REF_NOUNS})(?![a-z])")


_AT_NUMBER = re.compile(r"(?<![a-z])(?:at|is|was|on)\s+(?:number|no\.?|#)\s*(\d{1,2})(?!\d)")
_FIRST_N = re.compile(rf"(?<![a-z])the\s+first\s+(two|three|four|five|\d{{1,2}})(?:\s+({_REF_NOUNS}))?(?![a-z0-9])")
_TOP_ONE = re.compile(r"(?<![a-z])the\s+(?:top|top most|topmost|number one|no\. ?1|#1)\s+(one|user|guy|person|subscriber|"
                      r"number|account|customer|row|entry)(?![a-z])")
_COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5}


def _ordinal(question: str):
    q = (question or "").lower()
    m = _ORDINAL_REF.search(q)
    if m:
        word = m.group(1)
        digits = re.findall(r"\d+", word)
        return (int(digits[0]) - 1 if digits else _ORDINALS[word]), m.group(2)
    if (a := _AT_NUMBER.search(q)):
        return int(a.group(1)) - 1, "one"
    if (f := _FIRST_N.search(q)):
        n = _COUNT_WORDS.get(f.group(1)) or int(f.group(1))
        return slice(0, n), f.group(2) or "ones"
    if (t := _TOP_ONE.search(q)):
        from . import catalog, concepts
        if not catalog.entities(question) and not concepts.apps_in(question) and not concepts.match(question):
            return 0, t.group(1) if t.group(1) != "one" else "one"
    return None


def refers_back(question: str) -> bool:
    return bool(_REF.search((question or "").lower())) or _ordinal(question) is not None


def references(question: str, turns: list[dict] | None) -> list[dict]:
    m = _REF.search((question or "").lower())
    pick = _ordinal(question)
    if (not m and pick is None) or not turns:
        return []
    noun = (m.group(2) or m.group(4) or "") if m else pick[1]
    wanted = next((cols for rx, cols in _REF_KINDS if noun and rx.match(noun)), None)
    if wanted is None and noun and noun not in ("one", "ones"):
        wanted = ["subscriber_id"]
    word = (m.group(1) or m.group(3) or m.group(5) or "") if m else ""
    if word in ("he", "him", "his", "she", "her", "hers") and wanted is None:
        wanted = ["subscriber_id"]
    single = word in _SINGULAR and not (noun.endswith("s") or noun in ("people", "log", "logon"))
    if word in ("there", "wahan", "wahaan", "udhar", "wahin"):
        wanted, single = ["client_location"], True
    for msg in reversed(turns[-8:]):
        if msg.get("role") != "assistant" or msg.get("kind") != "result":
            continue
        d = msg.get("data") or {}
        res = d.get("result") or {}
        cols = [c["name"] for c in res.get("columns", [])]
        rows = res.get("rows") or []
        found = _id_columns(d.get("sql", ""), cols)
        if rows:
            found.update({c: col for c, col in _columns_by_values(cols, rows).items() if c not in found})
        for i, c in enumerate(cols):
            low = c.lower()
            if "hour" in low and all(isinstance(r[i], int) or str(r[i]).isdigit() for r in rows[:5]):
                found.setdefault(c, "__hour")
            elif low in ("day", "date", "d") or (rows and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(rows[0][i]))):
                found.setdefault(c, "__day")
        wants_person = wanted is None or "subscriber_id" in wanted
        if wants_person and (not found or not rows or (wanted and "subscriber_id" not in found.values())):
            ids = re.search(r"subscriber_id\s*(?:IN\s*\(([^)]*)\)|=\s*'([^']+)')", d.get("sql", ""), re.I)
            if ids:
                values = re.findall(r"'([^']+)'", ids.group(1) or f"'{ids.group(2)}'")
                if values:
                    return [{"column": "subscriber_id", "values": values, "turn": d.get("title", ""),
                             "single": len(values) <= 2, "of": len(values)}]
        if not found or not rows:
            continue
        order = [stored for stored in (wanted or [found[c] for c in cols if c in found
                                                  and found[c] not in ("__hour", "__day")])]
        for stored in order:
            name = next((c for c in cols if found.get(c) == stored), None)
            if name is None:
                continue
            i = cols.index(name)
            values = [str(r[i]) for r in rows if r[i] not in (None, "")
                      and not (stored == "subscriber_id" and "_" in str(r[i]))]
            values = list(dict.fromkeys(values))
            if pick is not None and isinstance(pick[0], slice):
                values = values[pick[0]]
                single = len(values) == 1
            elif pick is not None:
                values = [values[pick[0]]] if -len(values) <= pick[0] < len(values) else []
                single = True
            values = values[: 1 if single else 50]
            if values:
                return [{"column": stored, "values": values, "turn": d.get("title", ""), "single": single,
                         "of": len(rows)}]
    return []


def _stored_values(column: str, values: list[str]) -> list[str]:
    if column == "client_location":
        return [v if " " in v.strip() and v.split()[-1][:1].isupper() and v.endswith(("Pakistan", "Afghanistan",
                "States", "Uzbekistan", "Tajikistan", "Kong", "Singapore", "Oman", "India")) else f"{v} Pakistan"
                for v in values]
    if column == "client_asn":
        from . import concepts
        brands = {c["name"].lower(): c["asn"] for c in concepts.isp_catalogue().values() if c["asn"]}
        out = []
        for v in values:
            out += [x for x in brands.get(v.lower(), [v]) if x not in out]
        return out
    if column == "app_label":
        from . import concepts
        cat, names, shown = concepts.catalogue(), concepts.app_names(), concepts.app_families()
        out = []
        for v in values:
            if v in shown:
                out += [x for x in shown[v] if x not in out]
                continue
            family = next((cat[n]["labels"] if n in cat else names[n] for n in names
                           if v in (cat[n]["labels"] if n in cat else names[n])), [v])
            out += [x for x in family if x not in out]
        return out
    return values


def reference_lines(refs: list[dict], profile: bool = False) -> list[str]:
    out = []
    for r in refs:
        if r["column"] in ("__hour", "__day"):
            vals = r["values"]
            cond = (f"toHour(toDateTime(start_time, 'Asia/Karachi')) IN ({', '.join(vals)})" if r["column"] == "__hour"
                    else "toDate(toDateTime(start_time, 'Asia/Karachi')) IN (" + ", ".join(f"'{v[:10]}'" for v in vals) + ")")
            out.append(f"The question refers to the {'hour' if r['column'] == '__hour' else 'day'} from the earlier answer "
                       f"\"{r['turn']}\" ({', '.join(vals)}): add {cond} to the WHERE clause and keep the earlier period "
                       "and filters. Do not ask the user which one.")
            continue
        stored = _stored_values(r["column"], r["values"])
        vals = ", ".join("'" + v.replace("'", "\\'") + "'" for v in stored)
        which = ("the first row of" if r["single"] and r["of"] > 1 else "every row of" if len(r["values"]) > 1 else "")
        what = f"{which or 'the'} {r['column']} value{'s' if len(r['values']) > 1 else ''}"
        if r["column"] in ("app_label", "client_location", "client_asn"):
            out.append(f"The question refers to {what} from the earlier answer \"{r['turn']}\" "
                       f"({', '.join(r['values'])}): filter with {r['column']} IN ({vals}). Answer the new question "
                       "for exactly that, keeping the earlier period and place unless the question changes them. Do not "
                       "ask the user which one.")
            continue
        keep = ("keep the earlier period only (an address, ISP, IMSI or IMEI does not depend on the earlier answer's "
                "app or website filters, so drop those)" if profile else "keep the earlier period and filters")
        out.append(f"The question refers to {what} from the earlier answer \"{r['turn']}\": {r['column']} IN ({vals}). "
                   f"Filter on exactly these values, {keep}, and return one row per {r['column']} in the earlier "
                   f"order. Do not ask the user for them.")
    return out


_ACK = {"good", "great", "nice", "ok", "okay", "okk", "k", "kk", "cool", "thanks", "thank", "you", "u", "thx", "ty",
        "tysm", "shukriya", "shukria", "shukran", "jazakallah", "perfect", "awesome", "excellent", "amazing", "well",
        "done", "job", "work", "got", "it", "understood", "fine", "alright", "sure", "theek", "thik", "hai", "he", "h",
        "acha", "achha", "accha", "sahi", "zabardast", "wah", "wow", "lol", "haha", "hahaha", "very", "so", "much",
        "bohat", "bahut", "boht", "bhai", "yes", "yeah", "yep", "no", "nope", "correct", "right", "makes", "sense",
        "that's", "thats", "this", "that", "is", "was", "the", "answer", "a", "lot", "love", "it's", "its", "superb"}
_DOUBT_START = {"is", "are", "was", "were", "why", "how", "what", "which", "who", "when", "where", "do", "does", "did",
                "can", "could", "should", "sure", "really", "kya", "kyun", "kyon", "again"}
_HELLO = {"hi", "there", "hello", "hey", "salam", "salaam", "assalam", "assalamualaikum", "asalam", "o", "alaikum", "aoa",
          "morning", "evening", "afternoon", "good"}
_BYE = {"bye", "goodbye", "allah", "hafiz", "khuda", "see", "you", "later", "tc", "take", "care", "good", "night"}
_HELP = ("help", "what can you do", "how to use", "how do i use", "who are you", "what are you", "kya kar sakte",
         "what can i ask", "examples")

SMALL_TALK_REPLIES = {
    "thanks": "Glad that helps. Ask the next question whenever you are ready, or use Draw chart or Write conclusion on "
              "the answer above.",
    "hello": "Hello. Ask about app usage, places, times, servers or subscribers in your own words, for example "
             "\"top apps in Karachi last week\" or \"kitne log TikTok use karte hain Lahore me\".",
    "bye": "Goodbye. Your conversations are saved in the list on the left.",
    "later": "Sure. Ask whenever you are ready, in your own words, for example \"top apps in Karachi last week\".",
    "help": "Ask in your own words, in English, Urdu or Roman Urdu. For example: \"most used apps in Lahore yesterday\", "
            "\"how many people used VPN last week\", \"peak hour for YouTube\", \"what did 0325-3047295 do on 5 Nov\", "
            "\"WhatsApp vs IMO in Karachi and Lahore\". Each answer shows how it was understood, the period and the "
            "measure, and you can draw a chart or get a written conclusion. To browse raw records without the AI, "
            "use Explore.",
}


_QUESTION_WORDS = {"how", "what", "which", "who", "whom", "whose", "when", "where", "why", "kitne", "kitna", "kitni",
                   "kon", "kaun", "konsi", "kaunsi", "kya", "kab", "kahan", "kahaan", "kis", "kisne", "kaise"}
_DATA_WORDS = {"app", "apps", "application", "applications", "user", "users", "people", "ppl", "log", "logon",
               "subscriber", "subscribers", "customer", "customers", "data", "traffic", "usage", "use", "used", "using",
               "volume", "gb", "mb", "tb", "connection", "connections", "record", "records", "session", "sessions",
               "hour", "hours", "day", "days", "daily", "week", "weekly", "month", "monthly", "peak", "top", "most",
               "least", "count", "total", "number", "compare", "comparison", "vs", "versus", "trend", "list", "rank",
               "city", "cities", "area", "areas", "site", "sites", "server", "servers", "ip", "port", "operator",
               "network", "internet", "phone", "mobile", "msisdn", "imsi", "imei", "blocked", "block", "duration",
               "today", "yesterday", "kal", "aaj", "last", "average", "avg", "per", "map", "location", "cell", "tower",
               "upload", "download", "uplink", "downlink", "share", "percent", "percentage", "growth", "zyada", "ziada",
               "kam", "sab", "sb", "show", "find", "search", "website", "websites", "domain", "url", "video", "videos",
               "streaming", "watch", "dekhta", "dekhte", "chalti", "chalta", "night", "morning", "evening", "raat",
               "subah", "shaam", "minute", "minutes", "second", "seconds", "bytes", "category", "categories", "label"}
_TALK_PRONOUNS = {"i", "i'll", "i'm", "im", "we", "we'll", "you", "you're", "me", "my", "main", "mein", "mai", "hum",
                  "ham", "aap", "ap", "tum", "mujhe", "humein", "let's", "lets"}
_TALK_WORDS = {"later", "wait", "hold", "on", "nevermind", "never", "mind", "hmm", "hmmm", "umm", "one", "sec", "moment",
               "brb", "busy", "baad", "abhi", "nahi", "theek", "acha", "sorry", "oops", "nothing", "nope", "nah", "ok",
               "okay", "fine", "sure", "cool", "think", "check", "see", "will", "ask", "tell", "come", "back", "soon",
               "tomorrow", "free", "maybe", "not", "now", "just", "a", "the", "it", "that", "this", "and", "to"}


def has_data_signal(message: str) -> bool:
    text = (message or "").lower()
    words = set(re.findall(r"[a-z']+", text))
    if words & (_QUESTION_WORDS | _DATA_WORDS) or re.search(r"\d", text) or "?" in text:
        return True
    from . import concepts
    return bool(concepts.cities_in(text) or concepts.apps_in(text) or concepts.match(text) or operators(text))


def is_conversation(message: str) -> bool:
    words = re.findall(r"[a-z']+", (message or "").lower())
    if not words or len(words) > 12 or has_data_signal(message):
        return False
    return bool(set(words) & _TALK_PRONOUNS) or set(words) <= _TALK_WORDS


def small_talk(message: str, turns: list[dict] | None = None) -> str | None:
    if turns and turns[-1].get("role") == "assistant" and turns[-1].get("kind") == "clarify":
        return None
    text = (message or "").lower().strip()
    words = re.findall(r"[a-z']+", text)
    if not words:
        return "thanks" if text and len(text) <= 12 and not any(ch.isdigit() for ch in text) else None
    if len(words) > 8:
        return None
    joined = " ".join(words)
    if any(joined == h or (joined.startswith(h + " ") and not has_data_signal(joined[len(h):])) for h in _HELP):
        return "help"
    if words[0] in _DOUBT_START or "?" in text:
        return None
    if set(words) <= _HELLO and words != ["good"]:
        return "hello"
    if set(words) <= _BYE and not set(words) <= {"good", "see", "you"}:
        return "bye"
    if set(words) <= _ACK:
        return "thanks"
    if is_conversation(message):
        return "later"
    return None


_UI_CHART = {"chart", "charts", "graph", "graphs", "plot", "diagram", "visual", "visualise", "visualize", "bar", "pie"}
_CHART_TYPE_WORDS = {
    "pie": "pie", "donut": "pie", "doughnut": "pie", "treemap": "treemap", "tree": "treemap", "pareto": "pareto",
    "bar": "bar", "bars": "bar", "horizontal": "bar", "column": "column", "columns": "column", "vertical": "column",
    "line": "line", "lines": "line", "trend": "line", "area": "area", "stacked": "stacked_bar",
    "percent": "percent_bar", "percentage": "percent_bar", "heatmap": "heatmap", "heat": "heatmap",
    "calendar": "calendar", "clock": "clock", "polar": "clock", "radial": "clock", "sankey": "sankey",
    "flow": "sankey", "sunburst": "sunburst", "combo": "combo", "combined": "combo", "dual": "combo",
    "radar": "radar", "spider": "radar", "scatter": "scatter", "bubble": "bubble", "bubbles": "bubble", "map": "map",
}
_CHART_ONLY = {"pie", "donut", "doughnut", "treemap", "pareto", "heatmap", "sankey", "sunburst", "radar", "scatter"}
CHART_HINTS = ["bar", "column", "line", "area", "stacked_bar", "stacked_area", "percent_bar", "pie", "treemap",
               "pareto", "heatmap", "calendar", "clock", "sankey", "sunburst", "combo", "radar", "scatter", "bubble",
               "map", "table"]


def chart_type_in(words: list[str]) -> str:
    found = [_CHART_TYPE_WORDS[w] for w in words if w in _CHART_TYPE_WORDS]
    if "stacked_bar" in found and "area" in found:
        return "stacked_area"
    if "percent_bar" in found:
        return "percent_bar"
    specific = [f for f in found if f not in ("bar",)]
    return (specific or found or [""])[0]
_UI_CONCLUSION = {"conclusion", "conclusions", "conclude", "summary", "summarise", "summarize", "insight", "insights",
                  "analysis", "explain", "nateeja", "khulasa"}
_UI_EXPORT = {"csv", "excel", "export", "download"}
_UI_NEG = {"no", "not", "don't", "dont", "without", "skip", "hide", "remove", "stop", "nahi", "nhi", "mat", "na",
           "never", "needed", "dont't", "close"}
_UI_FILLER = {"need", "do", "draw", "make", "show", "give", "create", "write", "add", "generate", "please", "pls",
              "plz", "kindly", "can", "could", "you", "the", "a", "an", "it", "this", "that", "for", "of", "and", "or",
              "to", "me", "i", "want", "also", "karo", "kar", "kardo", "do", "bana", "banao", "dikhao", "likho", "too",
              "as", "well", "with", "answer", "result", "results", "above", "last", "previous", "just", "only", "is",
              "are", "there", "any", "now", "again", "its", "it's", "chahiye", "hai", "ki", "ka", "ke", "koi", "zaroorat",
              "zarurat", "table", "one", "be", "will", "would", "like", "into", "in", "data", "file", "save"}


def app_command(message: str, turns: list[dict] | None = None) -> dict | None:
    words = re.findall(r"[a-z'+]+", (message or "").lower())
    if not words or len(words) > 14:
        return None
    ui = _UI_CHART | _UI_CONCLUSION | _UI_EXPORT
    if not any(w in ui or w in _CHART_ONLY for w in words) or any(
            w not in ui | _UI_NEG | _UI_FILLER | set(_CHART_TYPE_WORDS) for w in words):
        return None
    chart = any(w in _UI_CHART or w in _CHART_ONLY for w in words)
    kind = chart_type_in(words) if chart else ""
    concl = any(w in _UI_CONCLUSION for w in words)
    export = any(w in _UI_EXPORT for w in words)
    negative = any(w in _UI_NEG for w in words)
    target = next((m["id"] for m in reversed(turns or []) if m.get("role") == "assistant" and m.get("kind") == "result"
                   and m.get("id")), None)
    if negative:
        actions = (["hide_chart"] if chart else []) + (["hide_conclusion"] if concl else [])
        if target and any(w in ("hide", "remove", "close") for w in words):
            what = " and ".join({"hide_chart": "the chart", "hide_conclusion": "the conclusion"}[a] for a in actions)
            return {"actions": actions, "target": target, "reply": f"Done: {what} hidden for the answer above."}
        reply = ("Understood. Charts and conclusions are never added on their own: they appear only when you press "
                 "Draw chart or Write conclusion under an answer, and Hide chart or Hide conclusion removes them. "
                 "Ask your next question whenever you are ready.")
        return {"actions": actions if target else [], "target": target, "reply": reply}
    actions = ([f"chart:{kind}" if kind else "chart"] if chart else []) + (["conclusion"] if concl else []) + (["export"] if export else [])
    if not target:
        return {"actions": [], "target": None,
                "reply": "There is no answer here yet to do that for. Ask a question first: every answer has Draw "
                         "chart, Write conclusion and a CSV download."}
    drawing, note = "drawing the chart", ""
    if kind:
        msg = next((m for m in reversed(turns or []) if m.get("id") == target), {})
        options = ((msg.get("data") or {}).get("charts") or {}).get("options") or []
        fit = next((o for o in options if o.get("type") == kind), None)
        name = CHART_NAMES.get(kind, "chart")
        if fit or not options:
            drawing = f"drawing it as a {name}"
        else:
            drawing = "drawing the best fitting chart"
            shares = kind in ("pie", "treemap", "pareto", "stacked_bar", "stacked_area", "percent_bar", "sankey", "sunburst")
            users_only = options and not any(o.get("additive", True) for o in options if o.get("type") != "figure")
            why = (" User counts cannot be shown as parts of a whole, because one user is counted in several groups."
                   if shares and users_only else "")
            note = (f" A {name} does not fit this result.{why} Every chart that fits is listed under Chart type: "
                    + ", ".join(dict.fromkeys(CHART_NAMES.get(o.get('type'), o.get('type')) for o in options)) + ".")
    what = " and ".join({"conclusion": "writing the conclusion", "export": "downloading the table"}.get(a, drawing)
                        for a in actions)
    return {"actions": actions, "target": target, "reply": f"Done: {what} for the answer above.{note}"}


CHART_NAMES = {
    "pie": "donut (pie) chart", "treemap": "treemap", "pareto": "Pareto chart", "bar": "bar chart",
    "column": "column chart", "line": "line chart", "area": "area chart", "stacked_area": "stacked area chart",
    "stacked_bar": "stacked bar chart", "percent_bar": "100% bar chart", "heatmap": "heatmap",
    "calendar": "calendar chart", "clock": "24-hour clock chart", "sankey": "flow (sankey) chart",
    "sunburst": "sunburst chart", "combo": "bars-and-line chart", "radar": "radar chart", "scatter": "scatter chart",
    "bubble": "bubble chart", "map": "map",
}


_RETRY = re.compile(
    r"^(please |pls |plz |kindly )?(continue|carry on|go on|proceed|retry|try again|again|redo|re-?run|resume|"
    r"run (it|that|this) again|answer (it|that|this)|(continue|answer|do|run|finish|complete) (with )?(the |my )?"
    r"(above|last|previous|same|earlier) (query|question|one|request)|same (query|question)( again)?|"
    r"dobara|dubara|phir se|phirse|jari rakho|continue karo|aage chalo)(?![a-z])")


def is_retry(question: str) -> bool:
    q = " ".join((question or "").lower().split()).rstrip("?.! ")
    return len(q.split()) <= 7 and bool(_RETRY.search(q))


def retry_target(question: str, turns: list[dict]) -> str | None:
    if not is_retry(question):
        return None
    last, answered = None, False
    for m in turns:
        if m["role"] == "user":
            if m["kind"] == "question" and not is_retry(m["content"]) and not small_talk(m["content"]):
                last, answered = m["content"], False
        elif m["kind"] == "result":
            answered = True
    return last if last and not answered else None


def sql_user_message(question: str, data_range: dict, today: str, turns: list[dict], examples: list[dict],
                     resolved: list[str] | None = None) -> str:
    lines = [
        "<context>",
        f"Data available from {data_range.get('first', '?')[:16]} to {data_range.get('last', '?')[:16]} (Pakistan time).",
        f"Last complete day: {data_range.get('last_full_day', '?')}. Today's calendar date: {today}.",
        *date_helpers(data_range),
        "</context>",
    ]
    if turns:
        lines.append("<conversation_so_far>")
        pending_user = None
        i = 0
        for m in turns:
            if m["role"] == "user":
                if pending_user:
                    i += 1
                    lines += _turn_text(i, pending_user, None)
                pending_user = m
            else:
                i += 1
                lines += _turn_text(i, pending_user, m)
                pending_user = None
        if pending_user:
            i += 1
            lines += _turn_text(i, pending_user, None)
        lines.append("</conversation_so_far>")
        lines.append(
            "<follow_up_rule>If the new question only makes sense together with the conversation (short questions "
            "such as 'aur lahore me?', 'and yesterday?', 'only TikTok', 'now as users'), it is the previous question "
            "with that one change. Start from the previous SQL above and change only what the new question changes; "
            "keep everything else (measure, period, filters).</follow_up_rule>")
    if examples:
        lines.append("<reference_queries>")
        lines.append("Patterns that worked for other questions. Their dates and names are placeholders: take dates, "
                     "apps, places, ISPs and ids ONLY from THIS question and <resolved>; never copy a filter the question "
                     "does not ask for. Take dates from the ready-made periods for THIS question.")
        for e in examples:
            tq, tsql = template_example(e["question"], without_dates(e["sql"].strip()))
            lines.append(f"Question pattern: {tq}")
            if e.get("notes"):
                lines.append(f"Notes: {template_example(e['notes'], '')[0]}")
            lines.append(f"SQL pattern:\n{tsql}")
            lines.append("")
        lines.append("</reference_queries>")
    lines.append(f"<question>{question}</question>")
    if (found := phones(question)):
        lines.append("<identifiers>A mobile number is stored in subscriber_id either in international form without "
                     "'+' or in national form with a leading 0: " +
                     "; ".join(f"{raw} is {phone_sql([(raw, stored)])}" for raw, stored in found) +
                     ". Filter with that IN list for the question's period.</identifiers>")
    if resolved:
        lines.append("<resolved>Already looked up for this question (exact stored values; no find_values needed):")
        lines += [f"- {r}" for r in resolved]
        lines.append("</resolved>")
    return "\n".join(lines)


ASSUMPTION = {
    "type": "object",
    "properties": {"label": {"type": "string"}, "value": {"type": "string"}},
    "required": ["label", "value"],
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "understood": {"type": "string"},
        "status": {"type": "string", "enum": ["ok", "clarify", "cannot_answer"]},
        "title": {"type": "string"},
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
        "assumptions": {"type": "array", "items": ASSUMPTION},
        "chart": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": CHART_HINTS},
                "x": {"type": "string"},
                "y": {"type": "string"},
                "series": {"type": "string"},
            },
            "required": ["type", "x", "y", "series"],
            "additionalProperties": False,
        },
        "message": {"type": "string"},
    },
    "required": ["understood", "status", "title", "sql", "explanation", "assumptions", "chart", "message"],
    "additionalProperties": False,
}

CONCLUSION_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "findings", "caveats"],
    "additionalProperties": False,
}


def sql_tools(searchable_columns: list[str]) -> list[Tool]:
    return [
        Tool(
            name="find_values",
            description=(
                "Look up the exact stored values of a text column that contain a search text (case-insensitive), "
                "with how many rows each has on the last complete day. Use it for every place, app, organisation "
                "or website name before using it in SQL. An empty search returns the most common values."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "column": {"type": "string", "enum": searchable_columns},
                    "search": {"type": "string", "description": "Text to look for, e.g. 'karachi', 'tiktok', 'model town'."},
                },
                "required": ["column", "search"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="run_sql",
            description=(
                "Run a read-only ClickHouse SELECT and get back the column types, the row count and the first 15 "
                f"rows, or the error. Only {settings.full_table} can be queried, every query needs a time filter on "
                "start_time, and there is no SETTINGS or FORMAT clause. Use it to test the final SQL and for small checks."
            ),
            parameters={
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
                "additionalProperties": False,
            },
        ),
        Tool(
            name="submit_answer",
            description="Submit the final answer. Call it exactly once. The system runs the SQL and replies if anything is wrong.",
            parameters=PLAN_SCHEMA,
        ),
        Tool(
            name="confirm_answer",
            description="Only when asked to check a submitted answer: confirms it is correct as it is. Takes no arguments.",
            parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        ),
    ]


CONCLUSION_TOOLS = [
    Tool(name="submit_conclusion", description="Submit the written conclusion.", parameters=CONCLUSION_SCHEMA),
]
