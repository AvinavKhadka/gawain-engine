"""
server/sql_dialect.py — Turn LLM output into executable T-SQL.

A **leaf module**: it imports nothing from ``server`` at module scope, which is
what breaks the import cycle that previously ran
``database -> failures -> llm -> database`` and forced four deferred
``import`` statements to hide it.

Everything here is deterministic text manipulation. Each rule exists because a
model repeatedly produced that exact mistake, and fixing it in code costs
microseconds where an LLM repair round-trip costs a full generation and often
reproduces the same error.

Schema-aware repair (resolving invented column names against columns that
really exist) is deliberately *not* implemented here — that needs a live
database. Callers inject it via ``register_repair_hook``, so this module stays
dependency-free and unit-testable with no database at all.
"""

from __future__ import annotations

import re
from typing import Callable

__all__ = ["preprocess_sql", "register_repair_hook", "clear_repair_hooks"]

#: Callables applied, in order, after the dialect rules. Each takes SQL and
#: returns SQL. Registered by server.database at import time so that layering
#: is explicit rather than a hidden circular import.
_REPAIR_HOOKS: list[Callable[[str], str]] = []


def register_repair_hook(fn: Callable[[str], str]) -> None:
    """Add a repair pass applied after the dialect rules.

    Idempotent: registering the same callable twice is a no-op, so a module
    re-import or a test that re-installs hooks cannot double-apply them.
    """
    if fn not in _REPAIR_HOOKS:
        _REPAIR_HOOKS.append(fn)


def clear_repair_hooks() -> None:
    """Drop all hooks — used by tests to isolate the pure dialect layer."""
    _REPAIR_HOOKS.clear()


def _strip_trailing_limit(sql: str) -> tuple[str, int | None]:
    """Remove a trailing LIMIT/OFFSET clause, returning (sql, row_limit).

    LLMs trained mostly on MySQL/Postgres emit `LIMIT n` no matter how firmly
    the prompt says T-SQL. Rather than burning LLM round-trips re-asking, we
    strip it deterministically and hand the row count back so the caller can
    re-express it as `SELECT TOP n`.

    Handles:  LIMIT 20  |  LIMIT 20 OFFSET 5  |  LIMIT 5, 20 (MySQL)  |  FETCH NEXT
    """
    # MySQL two-arg form: LIMIT <offset>, <count>  -> the count is the row cap
    m = re.search(r"\bLIMIT\s+\d+\s*,\s*(\d+)\s*;?\s*$", sql, re.IGNORECASE)
    if m:
        return re.sub(r"\bLIMIT\s+\d+\s*,\s*\d+\s*;?\s*$", "", sql,
                      flags=re.IGNORECASE).rstrip(), int(m.group(1))

    # Standard: LIMIT <count> [OFFSET <n>]
    m = re.search(r"\bLIMIT\s+(\d+)(?:\s+OFFSET\s+\d+)?\s*;?\s*$", sql, re.IGNORECASE)
    if m:
        return re.sub(r"\bLIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*;?\s*$", "", sql,
                      flags=re.IGNORECASE).rstrip(), int(m.group(1))

    # Postgres/ANSI OFFSET..FETCH is valid T-SQL only with ORDER BY; if the
    # model emitted a bare FETCH FIRST without OFFSET, convert it too.
    m = re.search(r"\bFETCH\s+(?:FIRST|NEXT)\s+(\d+)\s+ROWS?\s+ONLY\s*;?\s*$",
                  sql, re.IGNORECASE)
    if m and not re.search(r"\bOFFSET\b", sql, re.IGNORECASE):
        return re.sub(r"\bFETCH\s+(?:FIRST|NEXT)\s+\d+\s+ROWS?\s+ONLY\s*;?\s*$", "",
                      sql, flags=re.IGNORECASE).rstrip(), int(m.group(1))

    return sql, None


def _result_select_pos(sql: str) -> int:
    """Index of the SELECT that produces the final result set.

    For a plain query that is the first SELECT. For a `WITH cte AS (...) SELECT`
    it is the SELECT *after* the CTE definitions — putting TOP inside the CTE
    would silently truncate the wrong result set, which is a data-correctness
    bug rather than a syntax error, so it must be handled explicitly.
    """
    if not re.match(r"\s*WITH\b", sql, re.IGNORECASE):
        m = re.search(r"\bSELECT\b", sql, re.IGNORECASE)
        return m.start() if m else -1

    # Walk the string tracking parenthesis depth; the first SELECT at depth 0
    # after the WITH block is the result-producing one.
    depth = 0
    for m in re.finditer(r"[()]|\bSELECT\b", sql, re.IGNORECASE):
        tok = m.group()
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0 and m.start() > 0:
            return m.start()
    return -1


def _apply_top(sql: str, n: int) -> str:
    """Inject `TOP n` into the result-producing SELECT, if not already capped.

    A TOP already present on that SELECT is respected — the model's explicit
    intent wins over a stripped LIMIT.
    """
    pos = _result_select_pos(sql)
    if pos < 0:
        return sql

    head, tail = sql[:pos], sql[pos:]

    # Already capped? leave it alone.
    if re.match(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\b", tail, re.IGNORECASE):
        return sql

    # T-SQL requires SELECT DISTINCT TOP n, not SELECT TOP n DISTINCT.
    if re.match(r"\bSELECT\s+DISTINCT\b", tail, re.IGNORECASE):
        tail = re.sub(r"\bSELECT\s+DISTINCT\b", f"SELECT DISTINCT TOP {n}", tail,
                      count=1, flags=re.IGNORECASE)
    else:
        tail = re.sub(r"\bSELECT\b", f"SELECT TOP {n}", tail,
                      count=1, flags=re.IGNORECASE)
    return head + tail


def preprocess_sql(sql: str) -> str:
    """Normalise LLM output into executable T-SQL.

    Three layers, in order of confidence:

    1. **Dialect** — LIMIT/OFFSET, backticks, ``::`` casts, MySQL/Postgres
       function names. Purely syntactic and always safe.
    2. **Learned repairs** (hook) — column mappings observed from repairs that
       actually worked against this database.
    3. **Schema resolution** (hook) — invented columns resolved against the
       columns that exist.

    A hook that raises is skipped: a repair failure must never stop a query
    that would otherwise run.
    """
    if not sql:
        return sql

    sql = sql.strip()

    # Strip markdown fences / stray backticks the extractor may have missed.
    sql = re.sub(r"^```(?:sql|tsql|mssql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"```\s*$", "", sql).strip()
    sql = sql.replace("`", "")          # MySQL identifier quoting is invalid in T-SQL
    sql = sql.rstrip(";").rstrip()

    # ── Dialect: LIMIT/OFFSET -> TOP n ────────────────────────────────────────
    sql, row_limit = _strip_trailing_limit(sql)
    if row_limit is not None:
        sql = _apply_top(sql, row_limit)

    # ── TOP N mistakenly placed at the end of the query ───────────────────────
    m = re.search(r"\s+TOP\s+(\d+)\s*;?\s*$", sql, re.IGNORECASE)
    if m:
        sql = re.sub(r"\s+TOP\s+\d+\s*;?\s*$", "", sql, flags=re.IGNORECASE)
        sql = _apply_top(sql, int(m.group(1)))

    # ── Other dialect leakage ─────────────────────────────────────────────────
    sql = re.sub(r"\b(\w+(?:\.\w+)?)::(\w+)\b", r"CAST(\1 AS \2)", sql)
    sql = re.sub(r"\bIFNULL\s*\(", "ISNULL(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNOW\s*\(\s*\)", "GETDATE()", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bCURRENT_DATE\b", "CAST(GETDATE() AS date)", sql, flags=re.IGNORECASE)

    # ── Aggregate alias syntax ────────────────────────────────────────────────
    sql = re.sub(
        r"\w+\([^)]*\)\s*=\s*((?:SUM|AVG|MIN|MAX|COUNT)\([^)]+\))\s+AS\s+(\w+)",
        r"\1 AS \2", sql, flags=re.IGNORECASE,
    )

    # ── Injected repair passes ────────────────────────────────────────────────
    for hook in _REPAIR_HOOKS:
        try:
            sql = hook(sql)
        except Exception:
            continue

    return sql.strip()
