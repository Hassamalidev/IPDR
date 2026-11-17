from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,            -- user | assistant
    kind TEXT NOT NULL,            -- question | sql | result | clarify | cannot_answer | error
    content TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS dictionaries (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    fields TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    report TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS examples (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    sql TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'user',   -- built-in | user
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS views (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terms (
    id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    meaning TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answer_cache (
    key TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    plan TEXT NOT NULL,
    created_at TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
"""

SEED_TERMS = [
    ("users, subscribers, people, customers", "Identified subscribers (the users measure). Not connections."),
    ("usage, traffic, data, consumption", "Data volume in GB (uplink + downlink), unless the question is clearly about how many people or how many connections."),
    ("most used, top, popular", "Ranked by users, then connections. Show the top 20 unless a number is given."),
    ("last week", "The last 7 full days of data."),
    ("today, yesterday", "Both mean the last full day of data (the data is not live). Use the ready-made period for it."),
    ("night", "00:00 to 06:00 Pakistan time."),
    ("Jazz, Mobilink", "ISP (client_asn): 'PMCL LDI IP TRANSIT' or 'IX Peering for Mobilink and Link Direct International.'. Jazz SIM (only when SIM is said): mnc 01 or 07."),
    ("Zong", "ISP (client_asn): 'CMPak Limited'. Zong SIM (only when SIM is said): mnc 04."),
    ("Telenor", "ISP (client_asn): 'Telenor Pakistan'. Telenor SIM (only when SIM is said): mnc 06."),
    ("Ufone", "SIM operator code mnc 03 (Ufone has no client_asn entry)."),
    ("KHI, LHR, ISB, RWP, FSD, MUX, PEW", "Karachi, Lahore, Islamabad, Rawalpindi, Faisalabad, Multan, Peshawar."),
    ("YT, insta, FB, WA", "YouTube, Instagram, Facebook, WhatsApp."),
] + [
    ("ISP, network, provider, operator, internet company", "client_asn (the subscriber's network provider). PTCL, Nayatel, StormFiber, Jazz, Zong, Telenor... are matched by the exact names in isp_names."),
    ("address, ghar, pata, location of a user", "loc: house address (fixed line) or serving cell site (mobile); geo holds its lon,lat."),
    ("top users, heavy users, who used", "A list of individual identified subscribers ranked by data volume, not a count of users."),
    ("blocked, block, banned, band", "block_type > 0 (1 domain/app block, 2 TCP drop by server IP, 3 UDP/QUIC block: likely meanings)."),
]
SCHEMA_VERSION = 3
_V3_OLD = {"Jazz, Mobilink": "Mobile network code (mnc) 01 or 07.", "Zong": "Mobile network code (mnc) 04.",
           "Telenor": "Mobile network code (mnc) 06.", "Ufone": "Mobile network code (mnc) 03."}
_V3_NEW = SEED_TERMS[-4:]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


_ready = False


def _open() -> sqlite3.Connection:
    Path(settings.store_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.store_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connect():
    if not _ready:
        init()
    conn = _open()
    try:
        with _lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


def init() -> None:
    global _ready
    conn = _open()
    try:
        with _lock:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 2:
                ts = now()
                conn.executemany("INSERT INTO terms (id, term, meaning, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                                 [(new_id(), t, m, ts, ts) for t, m in SEED_TERMS])
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version < 3:
                ts = now()
                meanings = dict(SEED_TERMS)
                for term, old in _V3_OLD.items():
                    conn.execute("UPDATE terms SET meaning = ?, updated_at = ? WHERE term = ? AND meaning = ?",
                                 (meanings[term], ts, term, old))
                have = {r[0] for r in conn.execute("SELECT term FROM terms")}
                conn.executemany("INSERT INTO terms (id, term, meaning, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                                 [(new_id(), t, m, ts, ts) for t, m in _V3_NEW if t not in have])
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        _ready = True
    finally:
        conn.close()


def create_conversation(title: str = "") -> dict:
    cid, ts = new_id(), now()
    with connect() as c:
        c.execute("INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)", (cid, title, ts, ts))
    return {"id": cid, "title": title, "created_at": ts, "updated_at": ts}


def list_conversations(search: str = "", limit: int = 200) -> list[dict]:
    sql = """
        SELECT c.id, c.title, c.created_at, c.updated_at,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user') AS questions
        FROM conversations c
    """
    args: list = []
    if search:
        sql += """ WHERE c.title LIKE ? OR EXISTS (
                     SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.content LIKE ?)"""
        like = f"%{search}%"
        args += [like, like]
    sql += " ORDER BY c.updated_at DESC LIMIT ?"
    args.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args)]


def get_conversation(cid: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
        if row is None:
            return None
        msgs = c.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid", (cid,)).fetchall()
    conv = dict(row)
    conv["messages"] = [_message(m) for m in msgs]
    return conv


def rename_conversation(cid: str, title: str) -> bool:
    with connect() as c:
        return c.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, cid)).rowcount > 0


def delete_conversation(cid: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM conversations WHERE id = ?", (cid,)).rowcount > 0


def touch_conversation(cid: str, title_if_empty: str = "") -> None:
    with connect() as c:
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), cid))
        if title_if_empty:
            c.execute("UPDATE conversations SET title = ? WHERE id = ? AND title = ''", (title_if_empty[:80], cid))


def _message(row: sqlite3.Row) -> dict:
    m = dict(row)
    m["data"] = json.loads(m["data"] or "{}")
    return m


def add_message(cid: str, role: str, kind: str, content: str = "", data: dict | None = None) -> dict:
    mid, ts = new_id(), now()
    payload = json.dumps(data or {}, ensure_ascii=False, default=str)
    with connect() as c:
        c.execute(
            "INSERT INTO messages (id, conversation_id, role, kind, content, data, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mid, cid, role, kind, content, payload, ts),
        )
    return {"id": mid, "conversation_id": cid, "role": role, "kind": kind, "content": content,
            "data": data or {}, "created_at": ts}


def get_message(mid: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
    return _message(row) if row else None


def update_message_data(mid: str, **fields) -> dict | None:
    msg = get_message(mid)
    if msg is None:
        return None
    msg["data"].update(fields)
    with connect() as c:
        c.execute("UPDATE messages SET data = ? WHERE id = ?", (json.dumps(msg["data"], ensure_ascii=False, default=str), mid))
    return msg


def recent_turns(cid: str, limit: int = 4) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (cid, limit * 2),
        ).fetchall()
    return [_message(r) for r in reversed(rows)]


def add_dictionary(name: str, filename: str, fields: list[dict], notes: str, report: dict) -> dict:
    did, ts = new_id(), now()
    with connect() as c:
        c.execute(
            "INSERT INTO dictionaries (id, name, filename, created_at, active, fields, notes, report) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (did, name, filename, ts, json.dumps(fields, ensure_ascii=False), notes, json.dumps(report, ensure_ascii=False)),
        )
    return get_dictionary(did)


def _dictionary(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["fields"] = json.loads(d["fields"] or "[]")
    d["report"] = json.loads(d["report"] or "{}")
    d["active"] = bool(d["active"])
    return d


def get_dictionary(did: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM dictionaries WHERE id = ?", (did,)).fetchone()
    return _dictionary(row) if row else None


def list_dictionaries(active_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM dictionaries"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY created_at"
    with connect() as c:
        return [_dictionary(r) for r in c.execute(sql)]


def set_dictionary_active(did: str, active: bool) -> bool:
    with connect() as c:
        return c.execute("UPDATE dictionaries SET active = ? WHERE id = ?", (1 if active else 0, did)).rowcount > 0


def delete_dictionary(did: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM dictionaries WHERE id = ?", (did,)).rowcount > 0


def add_example(question: str, sql: str, notes: str = "", source: str = "user") -> dict:
    eid, ts = new_id(), now()
    with connect() as c:
        c.execute("INSERT INTO examples (id, question, sql, notes, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                  (eid, question.strip(), sql.strip(), notes.strip(), source, ts))
    return {"id": eid, "question": question, "sql": sql, "notes": notes, "source": source, "created_at": ts}


def list_examples() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM examples ORDER BY created_at DESC")]


def delete_example(eid: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM examples WHERE id = ?", (eid,)).rowcount > 0


def replace_builtin_examples(items: list[tuple[str, str, str]]) -> None:
    ts = now()
    with connect() as c:
        c.execute("DELETE FROM examples WHERE source = 'built-in'")
        c.executemany("INSERT INTO examples (id, question, sql, notes, source, created_at) VALUES (?, ?, ?, ?, 'built-in', ?)",
                      [(new_id(), q.strip(), s.strip(), n.strip(), ts) for q, s, n in items])


def count_examples() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM examples").fetchone()[0]


def save_view(name: str, config: dict) -> dict:
    vid, ts = new_id(), now()
    with connect() as c:
        c.execute("INSERT INTO views (id, name, config, created_at) VALUES (?, ?, ?, ?)",
                  (vid, name.strip(), json.dumps(config, ensure_ascii=False), ts))
    return {"id": vid, "name": name.strip(), "config": config, "created_at": ts}


def list_views() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM views ORDER BY name COLLATE NOCASE").fetchall()
    return [{**dict(r), "config": json.loads(r["config"])} for r in rows]


def delete_view(vid: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM views WHERE id = ?", (vid,)).rowcount > 0


def list_terms() -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM terms ORDER BY term COLLATE NOCASE")]


def add_term(term: str, meaning: str) -> dict:
    tid, ts = new_id(), now()
    with connect() as c:
        c.execute("INSERT INTO terms (id, term, meaning, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                  (tid, term.strip(), meaning.strip(), ts, ts))
    return {"id": tid, "term": term.strip(), "meaning": meaning.strip(), "created_at": ts, "updated_at": ts}


def update_term(tid: str, term: str, meaning: str) -> bool:
    with connect() as c:
        return c.execute("UPDATE terms SET term = ?, meaning = ?, updated_at = ? WHERE id = ?",
                         (term.strip(), meaning.strip(), now(), tid)).rowcount > 0


def delete_term(tid: str) -> bool:
    with connect() as c:
        return c.execute("DELETE FROM terms WHERE id = ?", (tid,)).rowcount > 0


def delete_from(cid: str, mid: str) -> int:
    with connect() as c:
        row = c.execute("SELECT rowid FROM messages WHERE id = ? AND conversation_id = ?", (mid, cid)).fetchone()
        if row is None:
            return 0
        n = c.execute("DELETE FROM messages WHERE conversation_id = ? AND rowid >= ?", (cid, row[0])).rowcount
        left = c.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (cid,)).fetchone()[0]
        if not left:
            c.execute("UPDATE conversations SET title = '' WHERE id = ?", (cid,))
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), cid))
        return n


def cached_answer(key: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT plan FROM answer_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        c.execute("UPDATE answer_cache SET hits = hits + 1 WHERE key = ?", (key,))
    return json.loads(row["plan"])


def save_answer(key: str, question: str, plan: dict) -> None:
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO answer_cache (key, question, plan, created_at, hits) VALUES (?, ?, ?, ?, 0)",
                  (key, question, json.dumps(plan, ensure_ascii=False), now()))


def forget_answer(key: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM answer_cache WHERE key = ?", (key,))


def clear_answer_cache() -> int:
    with connect() as c:
        return c.execute("DELETE FROM answer_cache").rowcount
