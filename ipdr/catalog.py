from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)
CACHE = Path(settings.store_path).with_name("catalog.json")
MAX_AGE = 24 * 3600
_lock = threading.Lock()
_data: dict | None = None


@dataclass
class Entity:
    kind: str
    name: str
    sql: str
    values: list[str] = field(default_factory=list)
    sure: bool = True
    note: str = ""

    def line(self) -> str:
        label = {"city": "city", "region": "province / region", "area": "area inside addresses (loc)",
                 "road": "road inside cell-site addresses (loc)", "isp": "ISP / network provider",
                 "sim": "SIM operator code", "server_org": "organisation that owns the servers",
                 "server_country": "where the servers are", "website": "website / hostname",
                 "subscriber": "subscriber", "ip": "IP address", "imsi": "SIM (IMSI)", "imei": "handset (IMEI)",
                 "category": "app category", "place": "place"}.get(self.kind, self.kind)
        return f"{self.name} ({label}): {self.sql}" + (f". {self.note}" if self.note else "")


def _query_catalogue() -> dict:
    from .db import db
    last = db.data_range().get("last_full_day")
    if not last:
        raise RuntimeError("no data range")
    day = (f"start_time >= toUnixTimestamp('{last} 00:00:00', '{settings.timezone}') AND "
           f"start_time < toUnixTimestamp('{last} 00:00:00', '{settings.timezone}') + 86400")
    t = settings.full_table

    def col(expr: str, where: str = "1") -> list[list]:
        res = db.cached_query(f"SELECT {expr} AS v, count() AS n FROM {t} WHERE {day} AND {where} AND v != '' "
                              f"GROUP BY v ORDER BY n DESC LIMIT 2000", max_rows=2000, ttl=3600)
        return [[r[0], int(r[1])] for r in res.rows]

    return {
        "built": time.time(),
        "client_location": col("client_location"),
        "client_asn": col("client_asn"),
        "server_asn": col("server_asn"),
        "server_location": col("server_location"),
        "app_category": col("app_category"),
        "app_label": col("app_label"),
        "area": col("trim(splitByString(', ', loc)[3])", "loc != ''"),
        "road": col("trim(splitByString(', ', loc)[2])", "loc LIKE 'Cell site%'"),
    }


def load(refresh: bool = False) -> dict:
    global _data
    with _lock:
        if _data is not None and not refresh and time.time() - _data.get("built", 0) < MAX_AGE:
            return _data
        cached = None
        if CACHE.exists():
            try:
                cached = json.loads(CACHE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        if cached and not refresh and time.time() - cached.get("built", 0) < MAX_AGE:
            _data = cached
            _index.cache_clear()
            _vocabulary.cache_clear()
            return _data
        try:
            fresh = _query_catalogue()
            CACHE.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
            _data = fresh
        except Exception as e:
            log.warning("catalogue not refreshed: %s", e)
            _data = cached or {"built": 0}
        _index.cache_clear()
        _vocabulary.cache_clear()
        return _data


def warm() -> None:
    threading.Thread(target=load, daemon=True).start()


def set_data(data: dict) -> None:
    global _data
    with _lock:
        _data = {"built": time.time() + 10 ** 9, **data}
        _index.cache_clear()
        _vocabulary.cache_clear()


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


_ORG_FILLER = set("""pvt pvt. private ltd ltd. limited llc inc inc. co company corporation corp smc plc bv b v gmbh sa s a
the and of for pakistan pk networks network communications communication telecom telecommunication
telecommunications services service internet broadband solutions solution technologies technology systems
group international global holdings online isp as autonomous system number transit firm""".split())
_COMMON = set("""prime galaxy play win dream orbit net link cable fiber fibre fast line grand reliance multi khan
new national special super ocean pace best star smart digital world cloud data city town global media power
quick speed light mega max one first top net gate getlinks gate air air link wave wireless sky blue green red
apple google amazon microsoft facebook meta tiktok youtube whatsapp netflix akamai cloudflare fastly oracle
virginia washington georgia jordan chad china india turkey hong kong united states kingdom america
punjab sindh civil lines model town ward scheme sector phase block satellite market mall park garden station
college canal circular airport link road street house cell site""".split())
_SERVER_CUE = re.compile(r"(?<![a-z])(server|servers|hosted|hosting|host|asn|organisation|organization|owned|"
                         r"company|companies|cdn|datacenter|data center|destination|traffic to|going to|goes to|"
                         r"connecting to|connections to|from servers|located in|based in|country|countries)(?![a-z])")


_ISP_CUE = re.compile(r"(?<![a-z])(isp|isps|network|networks|provider|providers|broadband|internet|operator|"
                      r"operators|connection|wifi|fiber|fibre|ftth|customers|subscribers)(?![a-z])")
_BRANDS = {"apple", "google", "amazon", "microsoft", "facebook", "meta", "tiktok", "akamai", "cloudflare", "fastly",
           "oracle", "netflix", "tencent", "alibaba", "huawei", "yandex", "roblox"}
_NOT_ORG = {"servers", "server", "com", "net", "org", "host", "hosting", "cloud", "data", "center"}
_GENERIC = set("""total connect mobile content input level layer jump moving space storage switch third tier true tube
unity square perfect optimal packet reflected secured dedicated constant computing foundation commission university
universitaet wholesale universal liberty castle brothers daniel grass glide handy adjust elite falcon gamma globe
nebula orion phoenix rocket vector virgin orange frontier charter wired europe japan singapore saudi british spanish
italia serbia emirates afghan beijing shanghai shenzhen hangzhou qingdao tianjin jinan hebei sichuan zhejiang latitude
logon login opera proton trans telegram netflix tiktok yahoo absolute allied bright business closed delta development
dreams opened master masters professional route sharp single software standard ultra vision western velocity infinity
multan khyber kabul dubai faisal faysal petroleum tornado solace enigma atlantic pair dimension divertissements
informacines secretaria entidad servicos verein asociatia cyber limestone liquid gigabit""".split())


def _org_keys(value: str, brands: bool = False) -> list[str]:
    common = (_COMMON - _BRANDS) | _NOT_ORG if brands else _COMMON
    words = [w for w in norm(value).split() if w not in _ORG_FILLER and w not in _NOT_ORG]
    keys = []
    if words:
        full = " ".join(words)
        if len(full) >= 4 and full not in common and full not in _GENERIC:
            keys.append(full)
        if len(words) > 1 and len(words[0]) >= 5 and words[0] not in common and words[0] not in _GENERIC:
            keys.append(words[0])
    return keys


@lru_cache(maxsize=1)
def _index() -> dict:
    data = _data or {}
    places, regions, foreign = {}, {}, {}
    for v, _ in data.get("client_location", []):
        n = norm(v)
        if n == "pakistan":
            continue
        if n.endswith(" pakistan"):
            name = n[: -len(" pakistan")]
            if name in ("punjab", "sindh", "khyber pakhtunkhwa", "balochistan", "gilgit baltistan", "azad kashmir"):
                regions.setdefault(name, []).append(v)
            else:
                places.setdefault(name, []).append(v)
        else:
            foreign.setdefault(n, []).append(v)
    countries = {}
    for v, _ in data.get("server_location", []):
        n = norm(v)
        if " " not in n or n in ("united states", "united kingdom", "united arab emirates", "hong kong", "south africa",
                                 "saudi arabia", "south korea", "new zealand", "czech republic", "costa rica",
                                 "sri lanka", "puerto rico"):
            countries.setdefault(n, v)
    isp_keys, org_keys = {}, {}
    for v, _ in data.get("client_asn", []):
        for k in _org_keys(v):
            isp_keys.setdefault(k, []).append(v)
    for v, _ in data.get("server_asn", []):
        for k in _org_keys(v, brands=True):
            org_keys.setdefault(k, []).append(v)
    server_places = {norm(v): v for v, _ in data.get("server_location", []) if " " in norm(v)}
    return {"places": places, "regions": regions, "foreign": foreign, "countries": countries,
            "server_places": server_places,
            "isp": isp_keys, "org": org_keys,
            "areas": {norm(v): v for v, _ in data.get("area", []) if len(norm(v)) >= 4},
            "roads": {norm(v): v for v, _ in data.get("road", [])},
            "categories": {norm(v.replace("_", " ")): v for v, _ in data.get("app_category", [])}}


def _found(key: str, text: str) -> bool:
    return f" {key} " in text or (" " in key and f" {key.replace(' ', '')} " in text)


def _sql_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'" for v in values)


_DOMAIN = re.compile(r"(?<![\w.-])((?:[a-z0-9-]+\.)+(?:com|net|org|ru|io|co|me|tv|pk|info|app|cn|uk|us|de|xyz|live|chat|"
                     r"to|cc|ai|gg|ly|link|online|site|top|vip|club|biz|in|so|be|fm)(?:\.[a-z]{2})?)(?![\w-])", re.I)
_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9a-f:])([0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,7})(?![0-9a-f:])", re.I)
_IP_PORT = re.compile(r"(?<![\w.:])((?:\d{1,3}\.){3}\d{1,3}_\d{1,5})(?![\w])")
_ACCOUNT = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,4}\d{8}|[a-z]{2,5}\d{8}|\d{6}[a-z]{3,7})(?![A-Za-z0-9])")
_FIFTEEN = re.compile(r"(?<!\d)(\d{15})(?!\d)")


def _cgnat(ip: str) -> bool:
    a, b = (int(x) for x in ip.split(".")[:2])
    return a == 10 or (a == 100 and 64 <= b <= 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


@lru_cache(maxsize=1)
def _vocabulary() -> tuple[dict, frozenset]:
    from . import concepts, context
    ix = _index()
    targets = {}
    for name in list(ix["places"]) + list(ix["regions"]) + list(ix["foreign"]):
        for w in name.split():
            if len(w) >= 5:
                targets.setdefault(w, w)
    for c in concepts.isp_catalogue().values():
        for w in c["words"]:
            if " " not in w and len(w) >= 5:
                targets.setdefault(w, w)
    keep = frozenset(context._DATA_WORDS | context._QUESTION_WORDS | set(concepts.app_names())
                     | {w for c in concepts.catalogue().values() for s in c["synonyms"] for w in s.split()})
    return targets, keep


def correct_typos(text: str) -> str:
    import difflib

    targets, keep = _vocabulary()
    if not targets:
        return text
    out = []
    for w in text.split():
        if len(w) >= 5 and w not in targets and w not in keep and not w.isdigit():
            hit = difflib.get_close_matches(w, list(targets), n=1, cutoff=0.86)
            if hit:
                w = hit[0]
        out.append(w)
    return " ".join(out)


def entities(question: str) -> list[Entity]:
    from . import concepts, context

    raw = question or ""
    load()
    ix = _index()
    fixed = correct_typos(norm(raw))
    text = f" {fixed} "
    if fixed != norm(raw):
        raw = f"{raw} ({fixed})"
    out: list[Entity] = []
    taken: set[str] = set()

    def take(key: str) -> bool:
        if any(key in t.split(" ") or f" {key} " in f" {t} " for t in taken):
            return False
        taken.add(key)
        return True

    for raw_num, stored in context.phones(raw):
        forms = context.phone_forms(stored)
        out.append(Entity("subscriber", raw_num, context.phone_sql([(raw_num, stored)]), forms))
    for m in _IP_PORT.finditer(raw):
        out.append(Entity("subscriber", m.group(1), f"subscriber_id = '{m.group(1)}'", [m.group(1)],
                          note="an unidentified record (client IP and port), not a person"))
    for m in _ACCOUNT.finditer(raw):
        out.append(Entity("subscriber", m.group(1), f"subscriber_id = '{m.group(1)}'", [m.group(1)]))
    for m in _FIFTEEN.finditer(raw):
        n = m.group(1)
        kind = "imsi" if n.startswith("410") else "imei"
        out.append(Entity(kind, n, f"{kind} = '{n}'", [n]))
    for m in _IPV4.finditer(_IP_PORT.sub(" ", raw)):
        ip = m.group(1)
        if any(ip in e.name for e in out) or not all(0 <= int(x) <= 255 for x in ip.split(".")):
            continue
        col = "client_ip" if _cgnat(ip) else "server_ip"
        out.append(Entity("ip", ip, f"{col} = '{ip}'", [ip],
                          note="a subscriber-side (carrier NAT) address" if col == "client_ip" else "a server address"))
    for m in _IPV6.finditer(raw):
        ip = m.group(1)
        if ip.count(":") >= 2 and re.search(r"[0-9a-f]{2}", ip, re.I):
            out.append(Entity("ip", ip, f"(client_ip = '{ip}' OR server_ip = '{ip}')", [ip]))
    for m in _DOMAIN.finditer(raw):
        host = m.group(1).lower()
        out.append(Entity("website", host, f"requested_server = '{host}'", [host],
                          note=f"for every subdomain too: (requested_server = '{host}' OR "
                               f"endsWith(requested_server, '.{host}'))"))
        for part in norm(host).split():
            taken.add(part)

    sim = concepts.wants_sim_operator(raw)
    for c in concepts.isps_in(raw):
        flt = concepts.isp_filter(c, sim)
        kind = "sim" if "mnc" in flt else "isp"
        vals = c["mnc"] if kind == "sim" else c["asn"]
        out.append(Entity(kind, c["name"], flt, list(vals)))
        for w in c["words"]:
            if _found(w, text):
                taken.update(w.split())

    for key in sorted(ix["places"], key=len, reverse=True):
        if (f"{key} pakistan" in ix["server_places"] and re.search(
                rf" (servers?|hosted|hosting|located|based|datacenters?|data centers?)( \w+){{0,2}} (in|at|from) "
                rf"{re.escape(key)} ", text)):
            continue
        if _found(key, text) and take(key):
            vals = ix["places"][key]
            name = vals[0].rsplit(" Pakistan", 1)[0]
            out.append(Entity("city", name, f"client_location = {_sql_list(vals)}" if len(vals) == 1
                              else f"client_location IN ({_sql_list(vals)})", vals))
    for short, full in concepts._CITY_SHORT.items():
        if f" {short} " in text and not any(e.kind == "city" and e.name == full for e in out):
            v = f"{full} Pakistan"
            out.append(Entity("city", full, f"client_location = '{v}'", [v]))
    early_cue = re.sub(r" (?:mobile )?country codes? ", " ", text)
    servers_asked = bool(_SERVER_CUE.search(early_cue))
    for key, vals in sorted(ix["foreign"].items(), key=lambda kv: -len(kv[0])):
        city = key.rsplit(" ", 1)[0] if " " in key and key.split()[-1] not in ("states", "kingdom", "kong") else key
        for k in dict.fromkeys([key, city]):
            if servers_asked and k in ix["countries"]:
                continue
            if servers_asked and _found(k, text) and key in ix["server_places"]:
                if take(k):
                    sv = ix["server_places"][key]
                    out.append(Entity("server_place", sv, f"server_location = '{sv}'", [sv],
                                      note="where the servers are (server_location)"))
                break
            if len(k) >= 4 and _found(k, text) and take(k):
                out.append(Entity("place", vals[0], f"client_location = {_sql_list(vals)}", vals,
                                  note="a subscriber location outside Pakistan (client_location)"))
                break
    for key, vals in ix["regions"].items():
        if _found(key, text) and take(key):
            out.append(Entity("region", key.title(), f"client_location = {_sql_list(vals)}", vals, sure=False,
                              note="client_location holds the province itself only when the city is unknown; for the "
                                   "whole province add its cities"))
    for key, v in ix["areas"].items():
        if _found(key, text) and take(key):
            out.append(Entity("area", v, f"loc ILIKE '%{v}%'", [v],
                              note="area names repeat across cities: add the city too, or group by city"))
    for key, v in ix["roads"].items():
        if _found(key, text) and take(key):
            out.append(Entity("road", v, f"loc ILIKE '%{v}%'", [v]))

    isp_talk = bool(_ISP_CUE.search(text))
    for key in sorted(ix["isp"], key=len, reverse=True):
        if (" " in key or isp_talk) and key not in ix["places"] and _found(key, text) and take(key):
            vals = ix["isp"][key]
            if any(e.kind == "isp" and set(vals) & set(e.values) for e in out):
                continue
            out.append(Entity("isp", " ".join(w.capitalize() for w in key.split()), f"client_asn IN ({_sql_list(vals)})",
                              vals, sure=" " in key))

    cue_text = re.sub(r" (?:mobile )?country codes? ", " ", text)
    server_talk = bool(_SERVER_CUE.search(cue_text))
    servers_named = bool(re.search(r"(?<![a-z])(server|servers|hosted|hosting|datacenter|data center)(?![a-z])", text))
    for key, v in ix["countries"].items():
        k = key
        if k == "pakistan" and (not servers_named or re.search(r" (outside|abroad|foreign|not in|other than) (of )?"
                                                               r"pakistan ", text)):
            continue
        hit = _found(k, text) or _found(_DEMONYM.get(k, "#"), text) or any(_found(a, text) for a in _COUNTRY_ALIAS.get(k, ()) if a not in ("us", "u s") or servers_named)
        if hit and take(k) and not any(e.kind in ("city", "isp") and k in norm(e.name) for e in out):
            out.append(Entity("server_country", v, f"endsWith(server_location, '{v}')", [v], sure=server_talk,
                              note="" if server_talk else "if the question means where the servers are"))
    for key, v in sorted(ix["server_places"].items(), key=lambda kv: -len(kv[0])):
        city = " ".join(key.split()[:-2] if key.endswith(("united states", "united kingdom", "hong kong")) else key.split()[:-1])
        if len(city) >= 4 and re.search(rf" (servers?|hosted|hosting|located|based|datacenters?|data centers?)( \w+){{0,2}} "
                                        rf"(in|at|from) {re.escape(city)} ", text) and take(city):
            out.append(Entity("server_place", v, f"server_location = '{v}'", [v],
                              note="where the servers are (server_location)"))
    if server_talk:
        app_words = {w for name in concepts.apps_in(raw) for w in name.split()} | \
                    {w for c in concepts.match(raw) for w in c["key"].split("_")}
        for key in sorted(ix["org"], key=len, reverse=True):
            if key in app_words or any(w in app_words for w in key.split()):
                continue
            if _found(key, text) and take(key):
                vals = ix["org"][key]
                out.append(Entity("server_org", vals[0], f"server_asn IN ({_sql_list(vals[:10])})", vals[:10]))
    for key, v in ix["categories"].items():
        if len(key) > 3 and _found(key, text) and take(key):
            out.append(Entity("category", v, f"app_category = '{v}'", [v], sure=False))
    return out


def values_like(column: str, search: str, limit: int = 15) -> list[str]:
    key = {"area": "area", "road": "road"}.get(column, column)
    want = norm(search)
    if not want:
        return []
    data = load()
    return [v for v, _ in data.get(key, []) if want in norm(v)][:limit]


_COUNTRY_ALIAS = {"united states": ("us", "usa", "u s", "u s a", "america", "the states"), "united kingdom": ("uk", "u k", "britain",
                  "england"), "united arab emirates": ("uae", "u a e", "emirates", "dubai"), "russia": ("russian federation",),
                  "netherlands": ("holland",), "south korea": ("korea",)}
_DEMONYM = {"russia": "russian", "china": "chinese", "india": "indian", "germany": "german", "france": "french",
            "united states": "american", "united kingdom": "british", "singapore": "singaporean", "italy": "italian",
            "ireland": "irish", "netherlands": "dutch", "japan": "japanese", "turkey": "turkish", "iran": "iranian",
            "afghanistan": "afghan", "saudi arabia": "saudi", "united arab emirates": "uae", "israel": "israeli"}


def prompt_block() -> str:
    data = load()
    if not data.get("client_location"):
        return ""
    ix = _index()
    top = [v for v, _ in data.get("client_location", [])[:40]]
    return "\n".join([
        "stored_values:  # from the data; other values: find_values",
        f"  client_location ({len(data.get('client_location', []))} values, '<City> Pakistan'; 'Pakistan' = city "
        f"unknown; 'Punjab Pakistan', 'Sindh Pakistan', 'Khyber Pakhtunkhwa Pakistan' = province only; a few foreign "
        f"places): {', '.join(top)} ...",
        f"  loc areas (3rd part of every address): {', '.join(ix['areas'].values())}",
        f"  loc roads (2nd part of cell-site addresses): {', '.join(ix['roads'].values())}",
        f"  server countries (end of server_location): {', '.join(list(ix['countries'].values())[:30])} ...",
        f"  server_asn (top): {', '.join(v for v, _ in data.get('server_asn', [])[:15])} ...",
    ])
