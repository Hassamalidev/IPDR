from __future__ import annotations

import math
import re

from . import store
from .dictionary import base

STOP = set("""a an the of in on at for to from by with and or is are was were be been what which who whom how
many much show me give list tell please this that these those it its do does did can could would should i we you
there their them all any per each than then into about over under between during last first top most""".split())

SYNONYMS = {
    "app": "app", "apps": "app", "application": "app", "applications": "app",
    "user": "user", "users": "user", "subscriber": "user", "subscribers": "user", "people": "user",
    "traffic": "volume", "data": "volume", "volume": "volume", "usage": "volume", "bytes": "volume",
    "gb": "volume", "mb": "volume", "bandwidth": "volume",
    "daily": "day", "day": "day", "days": "day", "date": "day",
    "hourly": "hour", "hour": "hour", "hours": "hour", "time": "hour",
    "area": "place", "city": "place", "cities": "place", "location": "place", "locations": "place",
    "map": "map", "site": "map", "sites": "map", "cell": "map", "cells": "map",
    "compare": "compare", "comparison": "compare", "versus": "compare", "vs": "compare",
    "used": "use", "use": "use", "using": "use", "popular": "use",
    "number": "msisdn", "msisdn": "msisdn", "phone": "msisdn",
}


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        if re.fullmatch(r"0?3\d{9}|92\d{10}", w):
            w = "msisdn"
        elif w.isdigit():
            continue
        w = SYNONYMS.get(w, w)
        if len(w) > 4 and w.endswith("s") and w not in SYNONYMS.values():
            w = w[:-1]
        out.append(w)
    return out


_SHAPE_WORDS = [
    (re.compile(r"(?<![a-z])(hour|hours|hourly|peak|busiest|time of day|kis waqt|night|raat|morning|subah)(?![a-z])"), "#byhour"),
    (re.compile(r"(?<![a-z])(daily|per day|each day|trend|roz|over time|day by day)(?![a-z])"), "#byday"),
    (re.compile(r"(?<![a-z])(share|percent|percentage|hissa|%)"), "#share"),
    (re.compile(r"(?<![a-z])(compare|comparison|vs|versus|week before|previous week|growth|change)(?![a-z])"), "#compare"),
    (re.compile(r"(?<![a-z])(blocked|block|banned|band)(?![a-z])"), "#blocked"),
    (re.compile(r"(?<![a-z])(websites?|sites?|domains?|urls?|hostnames?)(?![a-z])"), "#website"),
    (re.compile(r"(?<![a-z])(upload|uploaders?|uplink|download|downloaders?|downlink)(?![a-z])"), "#direction"),
    (re.compile(r"(?<![a-z])(how long|time spent|duration|kitni der)(?![a-z])"), "#duration"),
    (re.compile(r"(?<![a-z])(timeline|activity|what did|kya kiya)(?![a-z])"), "#timeline"),
    (re.compile(r"(?<![a-z])(imei|handsets?|devices?|sim swap)(?![a-z])"), "#device"),
    (re.compile(r"(?<![a-z])(both|and also|as well as)(?![a-z])"), "#both"),
]


def shape(text: str) -> set[str]:
    from . import agent, catalog, concepts

    q = (text or "").lower()
    tags = {f"#{e.kind}" for e in catalog.entities(text)}
    if concepts.apps_in(q):
        tags.add("#app")
    if concepts.match(q):
        tags.add("#kindofapp")
    if agent.wants_subscriber_list(text):
        tags.add("#peoplelist")
    if agent.wants_profile(text):
        tags.add("#profile")
    tags |= {tag for rx, tag in _SHAPE_WORDS if rx.search(q)}
    return tags


def seed_builtin_examples() -> None:
    store.replace_builtin_examples(
        [(ex["question"], ex["sql"], ex.get("notes", "")) for ex in base().get("examples", [])])


def similar(question: str, k: int = 4) -> list[dict]:
    examples = store.list_examples()
    if not examples:
        return []
    docs = [set(tokens(e["question"] + " " + e.get("notes", ""))) | shape(e["question"]) for e in examples]
    n = len(docs)
    df: dict[str, int] = {}
    for d in docs:
        for t in d:
            df[t] = df.get(t, 0) + 1
    q = set(tokens(question)) | shape(question)
    scored = []
    for e, d in zip(examples, docs):
        overlap = q & d
        if not overlap:
            continue
        score = sum(math.log(1 + n / df[t]) for t in overlap) / math.sqrt(len(d) or 1)
        scored.append((score, e))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [e for _, e in scored[:k]]
