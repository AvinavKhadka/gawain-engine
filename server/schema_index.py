"""
server/schema_index.py — Schema-aware column resolution.

The problem this solves
-----------------------
An LLM writing T-SQL against a star schema reliably invents column names that
*sound* right: `fis.OrderNumber` instead of `SalesOrderNumber`,
`dd.CalendarMonthName` instead of `EnglishMonthName`, `dpc.CategoryName`
instead of `EnglishProductCategoryName`. Each one costs two fix-up round-trips
and usually fails anyway, because the retry re-reads the same schema text and
makes the same leap.

Hardcoding a regex per mistake does not scale: it only ever covers the exact
names someone already hit, and it silently breaks against any other database.

This module instead resolves every `alias.Column` reference against the columns
that *actually exist*, discovered at runtime from INFORMATION_SCHEMA. That makes
the correction:

  * **general** — works for names nobody has seen yet,
  * **portable** — no AdventureWorksDW assumptions,
  * **safe** — an unresolvable reference is left untouched for the LLM's
    normal fix-up path rather than being mangled into something wrong.

Matching strategy, in order of confidence:
  1. exact (case-insensitive)
  2. normalised equality — drop non-alphanumerics and known noise words
     ("english", "calendar", "number", "of", "name", "key"), so
     `CalendarMonthName` -> `month` matches `EnglishMonthName` -> `month`
  3. unique suffix/prefix containment (`OrderNumber` -> `SalesOrderNumber`)
  4. close string similarity above a strict threshold (typos)

Ambiguity is treated as failure: if two real columns match equally well, we
change nothing rather than guess.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from config.observability import get_logger

log = get_logger(__name__)

__all__ = ["SchemaIndex", "parse_schema_text"]

#: Tokens that carry no distinguishing meaning in warehouse column names.
_NOISE = ("english", "calendar", "number", "numberof", "of", "name", "full",
          "alternate", "the", "total")

_TABLE_RE = re.compile(r"^Table:\s*(\S+)", re.IGNORECASE)
_COL_RE = re.compile(r"^\s*-\s*(\w+)\s*\(")
#: alias.Column, but not a table-qualified name like dbo.FactInternetSales
_REF_RE = re.compile(r"\b([A-Za-z_]\w*)\.(\w+)\b")

_MIN_SIMILARITY = 0.86


def _normalise(name: str) -> str:
    """Reduce a column name to its distinguishing core."""
    out = re.sub(r"[^a-z0-9]", "", name.lower())
    for token in _NOISE:
        out = out.replace(token, "")
    return out


def parse_schema_text(schema_text: str) -> dict[str, list[str]]:
    """Extract {table_name: [columns]} from get_schema_context() output.

    Parsing the rendered text (rather than re-querying) keeps this free: the
    schema is already fetched, cached, and passed into every LLM call.
    """
    tables: dict[str, list[str]] = {}
    current: str | None = None
    for line in schema_text.splitlines():
        m = _TABLE_RE.match(line)
        if m:
            current = m.group(1)
            tables.setdefault(current, [])
            continue
        if current:
            c = _COL_RE.match(line)
            if c:
                tables[current].append(c.group(1))
            elif not line.strip():
                current = None
    return tables


class SchemaIndex:
    """Resolves `alias.Column` references against real schema columns."""

    def __init__(self, schema_text: str) -> None:
        self.tables = parse_schema_text(schema_text)

        #: every real column name, deduplicated, preserving original casing
        self._all: dict[str, str] = {}
        for cols in self.tables.values():
            for c in cols:
                self._all.setdefault(c.lower(), c)

        #: normalised form -> set of real names sharing it
        self._by_norm: dict[str, set[str]] = {}
        for real in self._all.values():
            self._by_norm.setdefault(_normalise(real), set()).add(real)

    # ── lookup ────────────────────────────────────────────────────────────────

    def exists(self, column: str) -> bool:
        return column.lower() in self._all

    def _candidates_for_alias(self, alias: str, sql: str) -> list[str] | None:
        """Columns of the table bound to `alias` in this statement, if known."""
        pat = re.compile(
            r"\b(?:FROM|JOIN)\s+((?:\w+\.)?\w+)\s+(?:AS\s+)?" + re.escape(alias) + r"\b",
            re.IGNORECASE,
        )
        m = pat.search(sql)
        if not m:
            return None
        target = m.group(1).lower()
        for table, cols in self.tables.items():
            tl = table.lower()
            if tl == target or tl.split(".")[-1] == target.split(".")[-1]:
                return cols
        return None

    def resolve(self, column: str, pool: list[str] | None = None) -> str | None:
        """Best real column for `column`, or None if uncertain.

        `pool` restricts the search to one table's columns — far safer, since
        it cannot pull a name from an unrelated table.
        """
        if pool is None:
            pool = list(self._all.values())
        if not pool:
            return None

        lower = column.lower()
        by_lower = {c.lower(): c for c in pool}

        # 1. exact
        if lower in by_lower:
            return by_lower[lower]

        # 2. normalised equality
        norm = _normalise(column)
        if norm:
            hits = {c for c in pool if _normalise(c) == norm}
            if len(hits) == 1:
                return hits.pop()
            if len(hits) > 1:
                return None                      # ambiguous -> do not guess

        # 3. unique containment (OrderNumber -> SalesOrderNumber)
        if len(lower) >= 4:
            hits = {c for c in pool
                    if c.lower().endswith(lower) or c.lower().startswith(lower)}
            if len(hits) == 1:
                return hits.pop()
            if len(hits) > 1:
                return None

        # 4. close similarity (typos) — strict, and must be unambiguous
        scored = sorted(
            ((SequenceMatcher(None, lower, c.lower()).ratio(), c) for c in pool),
            reverse=True,
        )
        if scored and scored[0][0] >= _MIN_SIMILARITY:
            if len(scored) == 1 or scored[0][0] - scored[1][0] > 0.03:
                return scored[0][1]
        return None

    # ── rewriting ─────────────────────────────────────────────────────────────

    def fix_columns(self, sql: str) -> tuple[str, list[tuple[str, str]]]:
        """Rewrite invalid `alias.Column` references to real columns.

        Returns (sql, corrections). Anything that cannot be resolved with
        confidence is left exactly as-is so the existing validate -> fix_sql
        loop still gets its chance.
        """
        if not sql or not self._all:
            return sql, []

        corrections: list[tuple[str, str]] = []
        alias_pools: dict[str, list[str] | None] = {}

        def repl(m: re.Match[str]) -> str:
            alias, col = m.group(1), m.group(2)

            # Skip table-qualified names (dbo.FactInternetSales) and real columns
            if alias.lower() in ("dbo", "sys", "information_schema"):
                return m.group(0)
            if self.exists(col):
                return m.group(0)

            if alias not in alias_pools:
                alias_pools[alias] = self._candidates_for_alias(alias, sql)
            pool = alias_pools[alias]

            fixed = self.resolve(col, pool)
            if fixed is None and pool is not None:
                fixed = self.resolve(col)        # widen to the whole schema
            if fixed and fixed.lower() != col.lower():
                corrections.append((f"{alias}.{col}", f"{alias}.{fixed}"))
                return f"{alias}.{fixed}"
            return m.group(0)

        out = _REF_RE.sub(repl, sql)
        if corrections:
            log.info("schema-aware column repair", extra={"fixes": corrections})
        return out, corrections
