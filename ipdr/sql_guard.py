from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from .config import settings


class GuardError(ValueError):
    pass


FORBIDDEN_WORDS = [
    "INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE", "TRUNCATE", "RENAME", "ATTACH",
    "DETACH", "OPTIMIZE", "SYSTEM", "KILL", "GRANT", "REVOKE", "SET", "SETTINGS", "OUTFILE",
    "FORMAT", "EXCHANGE", "UNDROP", "BACKUP", "RESTORE",
]
TABLE_FUNCTIONS = [
    "url", "urlCluster", "file", "remote", "remoteSecure", "s3", "s3Cluster", "gcs", "hdfs", "mysql",
    "postgresql", "jdbc", "odbc", "mongodb", "redis", "sqlite", "executable", "input", "cluster",
    "clusterAllReplicas", "azureBlobStorage", "merge", "dictionary", "iceberg", "deltaLake", "hudi",
    "view", "viewIfPermitted", "loop", "fuzzJSON", "generateRandom",
]
_STRING_RE = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_WORD_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_WORDS) + r")\b", re.I)
_TABLE_FN_RE = re.compile(r"\b(" + "|".join(TABLE_FUNCTIONS) + r")\s*\(", re.I)


_STRING_OR_COMMENT = re.compile(r"('(?:[^'\\]|\\.|'')*')|(--[^\n]*|/\*.*?\*/)", re.S)


def _strip(sql: str) -> str:
    return _STRING_OR_COMMENT.sub(lambda m: "''" if m.group(1) is not None else " ", sql)


def check(sql: str, *, max_rows: int | None = None) -> str:
    max_rows = max_rows or settings.max_result_rows
    sql = sql.strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise GuardError("The query is empty.")

    bare = _strip(sql)
    if ";" in bare:
        raise GuardError("Only one statement can run at a time.")
    if not re.match(r"^\s*(SELECT|WITH)\b", bare, re.I):
        raise GuardError("Only SELECT queries are allowed.")
    if re.search(r"\bsystem\s*\.", bare, re.I) or re.search(r"\binformation_schema\b", bare, re.I):
        raise GuardError("System tables cannot be queried.")
    m = _WORD_RE.search(bare)
    if m:
        raise GuardError(f"'{m.group(1).upper()}' is not allowed. Only plain SELECT queries can run.")
    m = _TABLE_FN_RE.search(bare)
    if m:
        raise GuardError(f"The table function {m.group(1)}() is not allowed.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="clickhouse") if s is not None]
    except sqlglot.errors.ParseError:
        raise GuardError("This query could not be checked, so it was not run. Write it as one plain SELECT (no "
                         "'LIMIT n BY', 'SELECT TOP', 'APPLY' or other special syntax).") from None
    if len(statements) != 1:
        raise GuardError("Only one statement can run at a time.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise GuardError("Only SELECT queries are allowed.")
    _check_tables(tree)
    _check_time_filter(tree)
    return _apply_limit(sql, tree, max_rows)


def _check_tables(tree: exp.Expression) -> None:
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    allowed = settings.table.lower()
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise GuardError("Table functions are not allowed.")
        name = t.name.lower()
        db_name = (t.db or "").lower()
        if name in cte_names and not db_name:
            continue
        if name != allowed or db_name not in ("", settings.ch_database.lower()):
            shown = f"{t.db}.{t.name}" if t.db else t.name
            raise GuardError(f"Only {settings.full_table} can be queried, not {shown}.")


_TIME_MSG = (
    "Add a time range on start_time, e.g. WHERE start_time >= toUnixTimestamp('2026-11-01 00:00:00', "
    "'Asia/Karachi') AND start_time < toUnixTimestamp('2026-11-08 00:00:00', 'Asia/Karachi'). "
    "The start of the range must be joined with AND (not inside an OR) and be a real date; "
    "otherwise the query reads the whole table. EVERY SELECT that reads the table needs its own range, including "
    "subqueries such as imei IN (SELECT imei FROM ... WHERE <the same start_time range> AND ...)."
)


def _check_time_filter(tree: exp.Expression) -> None:
    allowed = settings.table.lower()
    def direct_table(node) -> bool:
        return node is not None and isinstance(node.this, exp.Table) and node.this.name.lower() == allowed

    for select in tree.find_all(exp.Select):
        from_ = select.args.get("from") or select.args.get("from_")
        joins = select.args.get("joins") or []
        reads_table = direct_table(from_) or any(direct_table(j) for j in joins)
        if not reads_table:
            continue
        conjuncts: list[exp.Expression] = []
        for part in (select.args.get("where"), select.args.get("prewhere")):
            if part is not None:
                conjuncts += _and_parts(part.this)
        if not any(_is_lower_bound(c) for c in conjuncts):
            raise GuardError(_TIME_MSG)


_EARLIEST_REAL_BOUND = 1_577_836_800


def _and_parts(node: exp.Expression) -> list[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _and_parts(node.left) + _and_parts(node.right)
    return [node]


def _is_start_time(node) -> bool:
    while isinstance(node, exp.Paren):
        node = node.this
    return isinstance(node, exp.Column) and node.name.lower() == "start_time"


def _real_bound(value) -> bool:
    t = _seconds(value)
    return t is not None and t >= _EARLIEST_REAL_BOUND


def _seconds(node):
    import time
    from datetime import datetime, timezone

    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Literal):
        if node.is_number:
            try:
                return float(node.this)
            except ValueError:
                return None
        return _date_text(node.this)
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        a, b = _seconds(node.left), _seconds(node.right)
        if a is None or b is None:
            return None
        if isinstance(node, exp.Add):
            return a + b
        if isinstance(node, exp.Sub):
            return a - b
        if isinstance(node, exp.Mul):
            return a * b
        return a / b if b else None
    if isinstance(node, exp.Neg):
        v = _seconds(node.this)
        return -v if v is not None else None
    if isinstance(node, (exp.CurrentTimestamp, exp.CurrentDate)):
        return time.time()
    name = (node.sql_name() if isinstance(node, exp.Func) else "").upper()
    fname = (node.name if isinstance(node, exp.Anonymous) else "").lower()
    if fname in ("now", "now64", "today"):
        return time.time()
    if fname in ("tounixtimestamp", "todatetime", "todatetime64", "parsedatetimebesteffort", "touint32", "toint64",
                 "touint64", "toint32") or name in ("TIMESTRTOUNIX", "STRTOUNIX", "TIMESTRTOTIME", "CAST", "UNIXTOTIME",
                                                    "TIMETOUNIX", "STRTOTIME", "TSORDSTOTIME"):
        args = node.expressions if isinstance(node, exp.Anonymous) else [node.this]
        return _seconds(args[0]) if args else None
    if isinstance(node, exp.Func) and node.args.get("this") is not None and name in ("TIMESTRTOUNIX",):
        return _seconds(node.this)
    return None


def _date_text(text: str):
    from datetime import datetime, timezone
    m = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}(?::\d{2})?))?\s*", text or "")
    if not m:
        return float(text) if re.fullmatch(r"\s*\d+(\.\d+)?\s*", text or "") else None
    try:
        dt = datetime.fromisoformat(m.group(1) + (" " + m.group(2) if m.group(2) else ""))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _is_lower_bound(cond: exp.Expression) -> bool:
    if isinstance(cond, (exp.GTE, exp.GT)) and _is_start_time(cond.left):
        return _real_bound(cond.right)
    if isinstance(cond, (exp.LTE, exp.LT)) and _is_start_time(cond.right):
        return _real_bound(cond.left)
    if isinstance(cond, exp.Between) and _is_start_time(cond.this):
        return _real_bound(cond.args.get("low"))
    return False


def _apply_limit(sql: str, tree: exp.Query, max_rows: int) -> str:
    limit = tree.args.get("limit")
    if limit is None:
        return f"{sql}\nLIMIT {max_rows}"
    value = limit.args.get("expression")
    try:
        n = int(value.name) if value is not None else None
    except (TypeError, ValueError):
        n = None
    if n is None or n > max_rows:
        return f"SELECT * FROM (\n{sql}\n)\nLIMIT {max_rows}"
    return sql
