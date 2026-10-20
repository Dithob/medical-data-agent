"""Bounded SQLite SELECT execution. Business connections never permit writes."""

import re
import sqlite3
import time
from pathlib import Path
from .config import BIZ_DB, SQL_MAX_ROWS, SQL_TIMEOUT_SECONDS, AS_OF_DATE

TABLES = {
    "fact_sales",
    "fact_purchase",
    "fact_inventory",
    "dim_drug",
    "dim_store",
    "dim_supplier",
}
FUNCTIONS = {
    "sum",
    "count",
    "avg",
    "min",
    "max",
    "round",
    "abs",
    "nullif",
    "coalesce",
    "substr",
    "substring",
    "lower",
    "upper",
    "length",
    "trim",
    "date",
    "strftime",
    "julianday",
    "like",
    "ifnull",
    "as_of_date",
}


def is_readonly(sql):
    # Strip literals/comments for syntax screening; SQLite authorizer is the actual boundary.
    clean = re.sub(
        r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/", " ", sql
    ).strip()
    clean = clean[:-1].strip() if clean.endswith(";") else clean
    if not re.match(r"^(select|with)\b", clean, re.I) or ";" in clean:
        return False, "只允许单条 SELECT 查询"
    if re.search(
        r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|reindex)\b",
        clean,
        re.I,
    ):
        return False, "不允许写入、DDL 或数据库管理操作"
    return True, ""


def execute_sql(
    sql, params=(), cancel=None, max_rows=SQL_MAX_ROWS, timeout=SQL_TIMEOUT_SECONDS
):
    allowed, reason = is_readonly(sql)
    if not allowed:
        raise ValueError(reason)
    conn = sqlite3.connect(Path(BIZ_DB).resolve().as_uri() + "?mode=ro", uri=True)
    deadline = time.monotonic() + timeout
    conn.execute("PRAGMA query_only=ON")
    conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
    conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000)
    conn.create_function(
        "as_of_date", 0, lambda: AS_OF_DATE.isoformat(), deterministic=True
    )

    def authorize(action, arg1, arg2, database, source):
        if action == sqlite3.SQLITE_READ:
            return (
                sqlite3.SQLITE_OK
                if arg1 in TABLES
                and (database == "main" or (database is None and arg2 == ""))
                else sqlite3.SQLITE_DENY
            )
        if action == sqlite3.SQLITE_FUNCTION:
            return (
                sqlite3.SQLITE_OK
                if (arg2 or "").lower() in FUNCTIONS
                else sqlite3.SQLITE_DENY
            )
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorize)
    conn.set_progress_handler(
        lambda: int(time.monotonic() > deadline or bool(cancel and cancel.is_set())),
        1000,
    )
    try:
        if cancel and cancel.is_set():
            raise InterruptedError("任务已取消")
        cur = conn.execute(sql, params)
        rows = [list(row) for row in cur.fetchmany(max_rows + 1)]
        if cancel and cancel.is_set():
            raise InterruptedError("任务已取消")
        return {
            "columns": [col[0] for col in cur.description],
            "data": rows[:max_rows],
            "returnedRows": min(len(rows), max_rows),
            "hasMore": len(rows) > max_rows,
            "truncated": len(rows) > max_rows,
        }
    except sqlite3.OperationalError as exc:
        if cancel and cancel.is_set():
            raise InterruptedError("任务已取消") from exc
        if "interrupted" in str(exc):
            raise TimeoutError("查询超过执行时间限制") from exc
        raise
    finally:
        conn.close()
