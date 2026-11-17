from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import yaml

from . import concepts, store
from .config import settings
from .db import QueryError, db

SEMANTIC_PATH = Path(__file__).with_name("semantic.yaml")
MAX_DESCRIPTION = 800
MAX_NOTES = 12_000
MAX_NOTES_TOTAL = 20_000
MAX_TERMS = 300

HEADERS = {
    "name": ["field", "field_name", "fieldname", "column", "column_name", "columnname", "name", "attribute", "key", "variable"],
    "description": ["description", "desc", "meaning", "definition", "details", "detail", "comment", "comments", "remarks", "explanation"],
    "type": ["type", "data_type", "datatype", "dtype", "format"],
    "example": ["example", "examples", "sample", "sample_value", "example_value"],
    "values": ["values", "allowed_values", "possible_values", "codes", "value_list", "enum"],
    "notes": ["notes", "note", "rules", "caveats", "usage"],
}


class DictionaryError(ValueError):
    pass


@lru_cache(maxsize=1)
def base() -> dict:
    return yaml.safe_load(SEMANTIC_PATH.read_text(encoding="utf-8"))


_live = {"at": 0.0, "value": None}
_live_lock = threading.Lock()


LIVE_CACHE = Path(settings.store_path).with_name("live_columns.json")


def live_columns() -> list[dict]:
    with _live_lock:
        if _live["value"] is not None and time.time() - _live["at"] < 600:
            return _live["value"]
        if _live.get("failed_at") and time.time() - _live["failed_at"] < 60:
            return _offline_columns()
    try:
        res = db.query(f"DESCRIBE TABLE {settings.full_table}", max_rows=2000, timeout=20)
        names = [c["name"] for c in res.columns]
        cols = [{"name": r[names.index("name")], "type": r[names.index("type")]} for r in res.rows]
        with _live_lock:
            _live.update(at=time.time(), value=cols, failed_at=0)
        try:
            LIVE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            LIVE_CACHE.write_text(json.dumps(cols), encoding="utf-8")
        except OSError:
            pass
        return cols
    except QueryError:
        with _live_lock:
            _live["failed_at"] = time.time()
        return _offline_columns()


def _offline_columns() -> list[dict]:
    try:
        cols = json.loads(LIVE_CACHE.read_text(encoding="utf-8"))
        if cols and all("name" in c and "type" in c for c in cols):
            return cols
    except (OSError, ValueError):
        pass
    b = base()
    known = list((b.get("columns") or {}).keys()) + list((b.get("empty_columns") or {}).get("list", []))
    return [{"name": n, "type": ""} for n in dict.fromkeys(known)]


def _norm(h) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def _map_headers(headers: list) -> dict[str, int]:
    normed = [_norm(h) for h in headers]
    found: dict[str, int] = {}
    for key, aliases in HEADERS.items():
        for alias in aliases:
            if alias in normed and normed.index(alias) not in found.values():
                found[key] = normed.index(alias)
                break
    return found


def _rows_to_fields(rows: list[list]) -> list[dict]:
    rows = [r for r in rows if any(str(c or "").strip() for c in r)]
    if not rows:
        raise DictionaryError("The file is empty.")
    cols = _map_headers(rows[0])
    if "name" not in cols:
        known = {c["name"].lower() for c in live_columns()}
        first = str(rows[0][0] or "").strip().lower()
        if len(rows[0]) >= 2 and not _map_headers(rows[0]) and first in known:
            cols = {"name": 0, "description": 1}
            body = rows
        else:
            raise DictionaryError(
                "Could not find the column with field names. Name the columns 'field' and 'description' "
                "(the template on this page has the right layout).")
    else:
        body = rows[1:]
    if "description" not in cols:
        raise DictionaryError("Could not find a 'description' column.")
    fields = []
    for r in body:
        get = lambda k: str(r[cols[k]]).strip() if k in cols and cols[k] < len(r) and r[cols[k]] is not None else ""
        name = get("name")
        if not name:
            continue
        fields.append({k: get(k) for k in ("name", "description", "type", "example", "values", "notes")})
    if not fields:
        raise DictionaryError("No fields found under the header row.")
    return fields


def _from_mapping(data) -> tuple[list[dict], str]:
    notes = ""
    if isinstance(data, dict):
        for key in ("fields", "columns", "schema"):
            if key in data:
                rest = {k: v for k, v in data.items() if k != key}
                notes = yaml.safe_dump(rest, sort_keys=False, allow_unicode=True) if rest else ""
                data = data[key]
                break
        else:
            if all(isinstance(v, (str, int, float)) or v is None for v in data.values()):
                return [{"name": str(k), "description": str(v or "")} for k, v in data.items()], ""
            raise DictionaryError("Expected a list of fields, a 'fields' key, or a map of field name to description.")
    if isinstance(data, dict):
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                out.append({"name": str(k), **{f: str(v.get(f, "") or "") for f in ("description", "type", "example", "values", "notes")}})
            else:
                out.append({"name": str(k), "description": str(v or "")})
        return out, notes
    if isinstance(data, list):
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            m = _map_headers(list(item.keys()))
            keys = list(item.keys())
            if "name" not in m:
                continue
            rec = {f: str(item[keys[m[f]]] or "").strip() if f in m else "" for f in ("name", "description", "type", "example", "values", "notes")}
            if rec["name"]:
                out.append(rec)
        if not out:
            raise DictionaryError("No fields with a name were found in the list.")
        return out, notes
    raise DictionaryError("The file content is not a list or map of fields.")


_MD_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_LINE_FIELD = re.compile(r"^\s*[-*]?\s*`?([A-Za-z_][\w.]*)`?\s*[:–—-]\s+(.+)$")


def _from_text(text: str) -> tuple[list[dict], str]:
    lines = text.splitlines()
    table_rows = [[c.strip() for c in m.group(1).split("|")] for line in lines if (m := _MD_TABLE_ROW.match(line))]
    table_rows = [r for r in table_rows if not all(re.fullmatch(r":?-{2,}:?", c or "-") for c in r)]
    fields: list[dict] = []
    if len(table_rows) >= 2:
        try:
            fields = _rows_to_fields(table_rows)
        except DictionaryError:
            fields = []
    if not fields:
        known = {c["name"].lower() for c in live_columns()}
        for line in lines:
            m = _LINE_FIELD.match(line)
            if m and m.group(1).lower() in known:
                fields.append({"name": m.group(1), "description": m.group(2).strip()})
    return fields, text.strip()


def parse_upload(filename: str, raw: bytes) -> tuple[list[dict], str]:
    ext = Path(filename).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as e:
            raise DictionaryError("Excel support is not installed on the server (openpyxl). Save the sheet as CSV.") from e
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception as e:
            raise DictionaryError("This Excel file could not be opened.") from e
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        return _rows_to_fields(rows), ""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if ext in (".csv", ".tsv"):
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel_tab if ext == ".tsv" else csv.excel
        return _rows_to_fields(list(csv.reader(io.StringIO(text), dialect))), ""
    if ext == ".json":
        try:
            return _from_mapping(json.loads(text))
        except json.JSONDecodeError as e:
            raise DictionaryError(f"The JSON is not valid: {e.msg} (line {e.lineno}).") from e
    if ext in (".yaml", ".yml"):
        try:
            return _from_mapping(yaml.safe_load(text))
        except yaml.YAMLError as e:
            raise DictionaryError("The YAML is not valid.") from e
    if ext in (".md", ".markdown", ".txt", ""):
        return _from_text(text)
    raise DictionaryError("Use a CSV, Excel (.xlsx), JSON, YAML, Markdown or text file.")


def clean_fields(fields: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for f in fields:
        name = f.get("name", "").strip().strip("`")
        if not name:
            continue
        rec = {k: (f.get(k) or "").strip()[:MAX_DESCRIPTION] for k in ("description", "type", "example", "values", "notes")}
        out[name.lower()] = {"name": name, **rec}
    return list(out.values())


def validate(fields: list[dict]) -> dict:
    live = {c["name"].lower(): c["name"] for c in live_columns()}
    names = {f["name"].lower() for f in fields}
    matched = sorted(live[n] for n in names if n in live)
    unknown = sorted(f["name"] for f in fields if f["name"].lower() not in live)
    no_description = sorted(f["name"] for f in fields if not f["description"])
    return {
        "fields": len(fields),
        "matched": len(matched),
        "unknown": unknown,
        "no_description": no_description,
        "table_columns": len(live),
    }


def save_upload(filename: str, raw: bytes, name: str = "") -> dict:
    try:
        fields, notes = parse_upload(filename, raw)
    except DictionaryError:
        raise
    except Exception as e:
        raise DictionaryError("This file could not be read. Check that it is a text CSV, Excel, JSON, YAML or Markdown file.") from e
    fields = clean_fields(fields)
    if not fields and not notes:
        raise DictionaryError("Nothing usable was found in the file.")
    report = validate(fields)
    if notes and len(notes) > MAX_NOTES:
        report["notes_truncated"] = True
        notes = notes[:MAX_NOTES]
    return store.add_dictionary(name or Path(filename).stem, filename, fields, notes, report)


def effective() -> dict:
    b = base()
    empty = {n.lower() for n in (b.get("empty_columns") or {}).get("list", [])}
    builtin = {k.lower(): str(v) for k, v in (b.get("columns") or {}).items()}
    cols: dict[str, dict] = {}
    for c in live_columns():
        key = c["name"].lower()
        cols[key] = {
            "name": c["name"], "type": c["type"], "description": builtin.get(key, ""),
            "source": "built-in" if key in builtin else "", "empty": key in empty,
            "example": "", "values": "", "notes": "",
        }
    notes = []
    for d in store.list_dictionaries(active_only=True):
        for f in d["fields"]:
            key = f["name"].lower()
            if key not in cols:
                continue
            col = cols[key]
            if f.get("description"):
                col["description"] = f["description"]
                col["source"] = d["name"]
            for extra in ("example", "values", "notes"):
                if f.get(extra):
                    col[extra] = f[extra]
        if d["notes"]:
            notes.append({"name": d["name"], "text": d["notes"]})
    return {"columns": list(cols.values()), "notes": notes}


def render_for_prompt() -> str:
    b = base()
    eff = effective()
    parts = []
    for key in ("table", "engine_notes", "grain", "data_model", "question_guide", "time", "metrics", "location", "network", "value_lists",
                "query_rules"):
        if key in b:
            parts.append(yaml.safe_dump({key: b[key]}, sort_keys=False, allow_unicode=True, width=110).rstrip())

    lines = ["columns:  # name (type): meaning. Columns marked EMPTY never hold data; do not use them."]
    for c in eff["columns"]:
        typ = f" ({c['type']})" if c["type"] else ""
        if c["empty"]:
            lines.append(f"  - {c['name']}{typ}: EMPTY")
            continue
        desc = c["description"] or "(no description yet)"
        extra = "".join(f" {label}: {c[k]}." for k, label in (("values", "Values"), ("example", "Example"), ("notes", "Notes")) if c[k])
        lines.append(f"  - {c['name']}{typ}: {desc}{extra}")
    parts.append("\n".join(lines))

    budget = MAX_NOTES_TOTAL
    for n in reversed(eff["notes"]):
        if budget <= 0:
            break
        text = n["text"][:budget]
        budget -= len(text)
        parts.append(f"notes_from_{_norm(n['name'])}: |\n" + "\n".join("  " + line for line in text.splitlines()))

    parts.append(concepts.prompt_block())
    parts.append(concepts.isp_prompt_block())
    from . import catalog
    if (values := catalog.prompt_block()):
        parts.append(values)

    terms = store.list_terms()[:MAX_TERMS]
    if terms:
        lines = ["team_terms:  # What the team's words mean. These override the defaults above when they conflict."]
        lines += [f"  - {t['term']}: {t['meaning'][:300]}" for t in terms]
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def string_columns() -> list[str]:
    eff = effective()
    out = []
    for c in eff["columns"]:
        if c["empty"] or c["name"] in ("log_id",):
            continue
        if not c["type"] or "String" in c["type"]:
            out.append(c["name"])
    return out


def template_csv() -> str:
    eff = effective()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["field", "type", "description", "example", "values", "notes"])
    for c in eff["columns"]:
        w.writerow([c["name"], c["type"], "Always empty" if c["empty"] and not c["description"] else c["description"],
                    c["example"], c["values"], c["notes"]])
    return buf.getvalue()
