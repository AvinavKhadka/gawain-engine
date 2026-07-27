"""
server/database.py — SQL Server connection, schema discovery, query execution,
                     chart detection, KPI extraction, and grid formatting.
"""

import math
import re
import pyodbc
import pandas as pd
from tabulate import tabulate

from config.observability import get_logger
from config.settings import (
    get_connection_string, DB_TABLE_FILTER, DB_DATABASE,
    CURRENCY_HINTS, PERCENT_HINTS,
    TIME_HINTS, CAT_HINTS, CHART_COLORS,
)

log = get_logger(__name__)

# ── Pre-built trend queries ───────────────────────────────────────────────────

TREND_SQL_5DAYS = """
SELECT TOP 5
    CONVERT(varchar(10), dd.FullDateAlternateKey, 23) AS OrderDate,
    SUM(fis.SalesAmount)                              AS Revenue,
    COUNT(DISTINCT fis.SalesOrderNumber)              AS Orders,
    SUM(fis.OrderQuantity)                            AS Units
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY dd.FullDateAlternateKey
ORDER BY dd.FullDateAlternateKey DESC
"""

TREND_SQL_12MONTHS = """
SELECT TOP 12
    LEFT(dd.EnglishMonthName, 3) + ' ' + CAST(dd.CalendarYear AS varchar(4)) AS Period,
    dd.CalendarYear      AS Year,
    dd.MonthNumberOfYear AS MonthNum,
    SUM(fis.SalesAmount)                     AS Revenue,
    COUNT(DISTINCT fis.SalesOrderNumber)     AS Orders,
    SUM(fis.OrderQuantity)                   AS Units
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY dd.CalendarYear, dd.MonthNumberOfYear, dd.EnglishMonthName
ORDER BY dd.CalendarYear DESC, dd.MonthNumberOfYear DESC
"""


def get_connection():
    return pyodbc.connect(get_connection_string(), timeout=30)


# ── Schema discovery (works with any SQL Server database) ─────────────────────

def get_schema_context() -> str:
    """Auto-discover schema via INFORMATION_SCHEMA. Respects DB_TABLE_FILTER."""
    conn = get_connection()
    cursor = conn.cursor()

    filter_set: set[str] = set()
    if DB_TABLE_FILTER.strip():
        filter_set = {t.strip() for t in DB_TABLE_FILTER.split(",") if t.strip()}

    cursor.execute("""
        SELECT t.TABLE_SCHEMA, t.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
               CASE WHEN pk.COLUMN_NAME IS NOT NULL THEN ' [PK]' ELSE '' END AS KEY_FLAG
        FROM INFORMATION_SCHEMA.TABLES t
        JOIN INFORMATION_SCHEMA.COLUMNS c
            ON t.TABLE_NAME = c.TABLE_NAME AND t.TABLE_SCHEMA = c.TABLE_SCHEMA
        LEFT JOIN (
            SELECT ku.TABLE_SCHEMA, ku.TABLE_NAME, ku.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
                ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
                AND tc.TABLE_SCHEMA = ku.TABLE_SCHEMA
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ) pk ON c.TABLE_NAME = pk.TABLE_NAME
             AND c.TABLE_SCHEMA = pk.TABLE_SCHEMA
             AND c.COLUMN_NAME  = pk.COLUMN_NAME
        WHERE t.TABLE_TYPE = 'BASE TABLE'
        ORDER BY t.TABLE_SCHEMA, t.TABLE_NAME, c.ORDINAL_POSITION
    """)
    rows = cursor.fetchall()
    conn.close()

    tables: dict = {}
    for schema, table, col, dtype, key_flag in rows:
        if filter_set and table not in filter_set:
            continue
        if dtype in ("varbinary", "xml", "image"):
            continue
        if any(x in col for x in (
            "Spanish", "French", "Chinese", "Arabic",
            "Hebrew", "Thai", "German", "Japanese", "Turkish",
        )):
            continue
        tables.setdefault(f"{schema}.{table}", []).append(
            f"  - {col} ({dtype}){key_flag}"
        )

    # Auto-discover foreign key relationships
    fk_conn = get_connection()
    fk_cur = fk_conn.cursor()
    fk_cur.execute("""
        SELECT
            fk_tab.TABLE_SCHEMA + '.' + fk_tab.TABLE_NAME  AS FK_Table,
            fk_col.COLUMN_NAME                              AS FK_Column,
            pk_tab.TABLE_SCHEMA + '.' + pk_tab.TABLE_NAME  AS PK_Table,
            pk_col.COLUMN_NAME                              AS PK_Column
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS fk_tc
            ON rc.CONSTRAINT_NAME = fk_tc.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk_col
            ON rc.CONSTRAINT_NAME = fk_col.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS pk_tc
            ON rc.UNIQUE_CONSTRAINT_NAME = pk_tc.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE pk_col
            ON pk_tc.CONSTRAINT_NAME = pk_col.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.TABLES fk_tab
            ON fk_tc.TABLE_NAME = fk_tab.TABLE_NAME
            AND fk_tc.TABLE_SCHEMA = fk_tab.TABLE_SCHEMA
        JOIN INFORMATION_SCHEMA.TABLES pk_tab
            ON pk_tc.TABLE_NAME = pk_tab.TABLE_NAME
            AND pk_tc.TABLE_SCHEMA = pk_tab.TABLE_SCHEMA
    """)
    fk_rows = fk_cur.fetchall()
    fk_conn.close()

    lines = [f"=== {DB_DATABASE} Schema ===", "", "KEY RELATIONSHIPS:"]
    for fk_table, fk_col, pk_table, pk_col in fk_rows:
        lines.append(f"  {fk_table}.{fk_col} -> {pk_table}.{pk_col}")
    lines.append("")

    for table_name, cols in tables.items():
        lines.append(f"Table: {table_name}")
        lines.extend(cols)
        lines.append("")

    return "\n".join(lines)


def get_foreign_keys() -> list[dict]:
    """Return all FK relationships as [{fk_table, fk_col, pk_table, pk_col}].

    Tables are returned as 'schema.table'. Used by the driver engine to discover
    which dimensions hang off a fact table.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            fk_tab.TABLE_SCHEMA + '.' + fk_tab.TABLE_NAME AS FK_Table,
            fk_col.COLUMN_NAME                             AS FK_Column,
            pk_tab.TABLE_SCHEMA + '.' + pk_tab.TABLE_NAME AS PK_Table,
            pk_col.COLUMN_NAME                             AS PK_Column
        FROM INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS fk_tc
            ON rc.CONSTRAINT_NAME = fk_tc.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE fk_col
            ON rc.CONSTRAINT_NAME = fk_col.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.TABLE_CONSTRAINTS pk_tc
            ON rc.UNIQUE_CONSTRAINT_NAME = pk_tc.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE pk_col
            ON pk_tc.CONSTRAINT_NAME = pk_col.CONSTRAINT_NAME
        JOIN INFORMATION_SCHEMA.TABLES fk_tab
            ON fk_tc.TABLE_NAME = fk_tab.TABLE_NAME AND fk_tc.TABLE_SCHEMA = fk_tab.TABLE_SCHEMA
        JOIN INFORMATION_SCHEMA.TABLES pk_tab
            ON pk_tc.TABLE_NAME = pk_tab.TABLE_NAME AND pk_tc.TABLE_SCHEMA = pk_tab.TABLE_SCHEMA
    """)
    rows = cur.fetchall()
    conn.close()
    return [
        {"fk_table": r[0], "fk_col": r[1], "pk_table": r[2], "pk_col": r[3]}
        for r in rows
    ]


# Column names we never want as dimension attributes (keys, surrogate IDs, hashes).
_ATTR_EXCLUDE = ("key", "id", "guid", "hash", "alternatekey")
_LANG_EXCLUDE = (
    "Spanish", "French", "Chinese", "Arabic", "Hebrew",
    "Thai", "German", "Japanese", "Turkish",
)


def get_dimension_attributes(table: str) -> list[str]:
    """Return candidate categorical attribute columns for a dimension table.

    Keeps short text/categorical columns (varchar/nvarchar/char/bit/tinyint) and
    drops keys, surrogate IDs, and localized-translation columns. Cardinality is
    filtered later (post-load) against MAX_DIM_CARDINALITY.
    """
    schema, name = table.split(".", 1) if "." in table else ("dbo", table)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
    """, schema, name)
    rows = cur.fetchall()
    conn.close()

    keep: list[str] = []
    for col, dtype in rows:
        c = col.lower()
        if dtype not in ("varchar", "nvarchar", "char", "nchar", "bit", "tinyint"):
            continue
        if any(x in c for x in _ATTR_EXCLUDE):
            continue
        if any(x in col for x in _LANG_EXCLUDE):
            continue
        keep.append(col)
    return keep


# ── SQL pre-processing & validation ───────────────────────────────────────────

# Hallucinated → real column mappings (alias-agnostic, word-boundary safe)
_COL_FIXES: list[tuple[str, str]] = [
    (r"\b(\w+)\.ProductName\b",              r"\1.EnglishProductName"),
    (r"\b(\w+)\.CategoryName\b",             r"\1.EnglishProductCategoryName"),
    (r"\b(\w+)\.SubcategoryName\b",          r"\1.EnglishProductSubcategoryName"),
    (r"\b(\w+)\.CountryName\b",              r"\1.EnglishCountryRegionName"),
    (r"\b(\w+)\.RegionName\b",               r"\1.SalesTerritoryRegion"),
    (r"\b(\w+)\.CustomerSegmentName\b",        r"\1.EnglishOccupation"),
    (r"\b(\w+)\.EnglishCustomerSegmentName\b", r"\1.EnglishOccupation"),
    (r"\b(\w+)\.CustomerType\b",               r"\1.EnglishOccupation"),
    (r"\b(\w+)\.Segment\b",                    r"\1.EnglishOccupation"),
    (r"\b(\w+)\.TerritoryName\b",              r"\1.SalesTerritoryRegion"),
    # DimDate month/day names are prefixed "English…" in AdventureWorksDW.
    (r"\b(\w+)\.CalendarMonthName\b",          r"\1.EnglishMonthName"),
    (r"\b(\w+)\.MonthName\b",                  r"\1.EnglishMonthName"),
    (r"\b(\w+)\.CalendarDayName\b",            r"\1.EnglishDayNameOfWeek"),
    (r"\b(\w+)\.DayName\b",                    r"\1.EnglishDayNameOfWeek"),
    (r"\b(\w+)\.MonthNumber\b(?!OfYear)",      r"\1.MonthNumberOfYear"),
    # DimDate day/week ordinals are "…NumberOf…", never "Calendar…".
    (r"\b(\w+)\.CalendarDayOfWeek\b",          r"\1.DayNumberOfWeek"),
    (r"\b(\w+)\.DayOfWeek\b",                  r"\1.DayNumberOfWeek"),
    (r"\b(\w+)\.CalendarDayOfMonth\b",         r"\1.DayNumberOfMonth"),
    (r"\b(\w+)\.DayOfMonth\b",                 r"\1.DayNumberOfMonth"),
    (r"\b(\w+)\.CalendarDayOfYear\b",          r"\1.DayNumberOfYear"),
    (r"\b(\w+)\.DayOfYear\b",                  r"\1.DayNumberOfYear"),
    (r"\b(\w+)\.CalendarWeekOfYear\b",         r"\1.WeekNumberOfYear"),
    (r"\b(\w+)\.WeekOfYear\b",                 r"\1.WeekNumberOfYear"),
    (r"\b(\w+)\.WeekNumber\b(?!OfYear)",       r"\1.WeekNumberOfYear"),
    # The real calendar date lives in FullDateAlternateKey; DateKey is an
    # INTEGER YYYYMMDD surrogate and renders as 20131225 in a chart axis.
    (r"\b(\w+)\.FullDate\b(?!AlternateKey)",   r"\1.FullDateAlternateKey"),
    (r"\b(\w+)\.CalendarDate\b",               r"\1.FullDateAlternateKey"),
    (r"\b(\w+)\.OrderDate\b(?!Key)",           r"\1.FullDateAlternateKey"),
]


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


#: Process-wide schema cache. Owned here (rather than in routes.py) so the SQL
#: sanitiser can consult the real schema without a circular import or an extra
#: database round-trip. routes.get_schema() delegates to this.
_schema_cache: str | None = None

#: SchemaIndex built from _schema_cache, rebuilt when the schema changes.
_schema_index = None
_schema_index_src: str | None = None


def get_cached_schema(refresh: bool = False) -> str:
    """Return the schema context, fetching it once and caching it."""
    global _schema_cache
    if refresh:
        _schema_cache = None
    if _schema_cache is None:
        _schema_cache = get_schema_context()
    return _schema_cache


def clear_schema_cache() -> None:
    """Drop the cached schema (and derived index) — used by /api/schema/refresh."""
    global _schema_cache, _schema_index, _schema_index_src
    _schema_cache = None
    _schema_index = None
    _schema_index_src = None


def repair_columns(sql: str) -> str:
    """Resolve invalid alias.Column references against the live schema.

    Uses the cached schema text, so it costs no extra database round-trip.
    Returns the input unchanged if the schema is not available (e.g. the
    database is offline) — column repair is an optimisation, never a
    prerequisite for running a query.
    """
    global _schema_index, _schema_index_src

    from server.schema_index import SchemaIndex

    if _schema_cache is None:
        return sql          # never trigger a DB fetch from inside the sanitiser
    schema_text = _schema_cache

    if _schema_index is None or _schema_index_src != schema_text:
        _schema_index = SchemaIndex(schema_text)
        _schema_index_src = schema_text

    fixed, _ = _schema_index.fix_columns(sql)
    return fixed


def preprocess_sql(sql: str) -> str:
    """Normalise LLM output into executable T-SQL.

    Two layers, deliberately:

    1. **Dialect + known-ambiguous names** (below) — deterministic rules for
       things a schema lookup cannot decide, e.g. `CalendarDayOfWeek`, which
       matches both `DayNumberOfWeek` and `EnglishDayNameOfWeek` equally well.
    2. **Schema-aware resolution** (`SchemaIndex`) — every remaining
       `alias.Column` is checked against the columns that actually exist in
       the connected database. This is what generalises: it repairs invented
       names nobody has hit yet, and it works against any schema, not just
       AdventureWorksDW.

    Both are far cheaper than an LLM fix-up round-trip, which costs a full
    generation and frequently reproduces the same mistake.
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
        n = m.group(1)
        sql = re.sub(r"\s+TOP\s+\d+\s*;?\s*$", "", sql, flags=re.IGNORECASE)
        sql = _apply_top(sql, int(n))

    # ── Other dialect leakage ─────────────────────────────────────────────────
    # Postgres cast  x::int  ->  CAST(x AS int)
    sql = re.sub(r"\b(\w+(?:\.\w+)?)::(\w+)\b", r"CAST(\1 AS \2)", sql)
    # MySQL/Postgres functions with no T-SQL equivalent under the same name
    sql = re.sub(r"\bIFNULL\s*\(", "ISNULL(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNOW\s*\(\s*\)", "GETDATE()", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bCURRENT_DATE\b", "CAST(GETDATE() AS date)", sql, flags=re.IGNORECASE)

    # ── Fix aggregate alias syntax ────────────────────────────────────────────
    sql = re.sub(
        r"\w+\([^)]*\)\s*=\s*((?:SUM|AVG|MIN|MAX|COUNT)\([^)]+\))\s+AS\s+(\w+)",
        r"\1 AS \2", sql, flags=re.IGNORECASE,
    )

    # ── Auto-correct commonly hallucinated column names ───────────────────────
    # These handle cases a schema lookup cannot resolve unambiguously.
    for pattern, replacement in _COL_FIXES:
        sql = re.sub(pattern, replacement, sql, flags=re.IGNORECASE)

    # ── Schema-aware repair of anything still invalid ─────────────────────────
    # Best-effort: never let a repair failure break query execution.
    try:
        sql = repair_columns(sql)
    except Exception:
        log.debug("schema-aware column repair skipped", exc_info=True)

    return sql.strip()


def validate_sql(sql: str) -> str | None:
    """SET NOEXEC ON dry-run — returns error string or None if syntax is valid."""
    try:
        sql = preprocess_sql(sql)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SET NOEXEC ON")
        try:
            cursor.execute(sql)
            error = None
        except pyodbc.Error as e:
            error = str(e)
        cursor.execute("SET NOEXEC OFF")
        conn.close()
        return error
    except Exception as e:
        return str(e)


def execute_query(sql: str) -> tuple[pd.DataFrame | None, str]:
    try:
        sql = preprocess_sql(sql)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        cols = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        df = pd.DataFrame([list(r) for r in rows], columns=cols)
        for col in df.columns:
            if df[col].dtype == object:
                try:
                    df[col] = pd.to_numeric(df[col])
                except (ValueError, TypeError):
                    pass
        return df, ""
    except Exception as e:
        return None, str(e)


# ── Text helpers ───────────────────────────────────────────────────────────────

def dataframe_to_markdown(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "Query returned no results."
    trimmed = df.head(max_rows)
    note = f"\n_(showing {max_rows} of {len(df)} rows)_" if len(df) > max_rows else ""
    return tabulate(trimmed, headers="keys", tablefmt="github", showindex=False) + note


def result_brief(df: pd.DataFrame, max_rows: int = 3) -> str:
    """Short result summary used in conversation history context."""
    if df is None or df.empty:
        return "No results."
    cols = list(df.columns)
    sample = df.head(max_rows)
    rows_text = "; ".join(
        ", ".join(f"{c}={sample.iloc[i][c]}" for c in cols[:4])
        for i in range(min(len(sample), max_rows))
    )
    return f"Columns: {', '.join(cols[:6])} | Sample: {rows_text}"


# ── AG Grid helpers ────────────────────────────────────────────────────────────

def _col_format(col: str) -> str:
    c = col.lower()
    if any(h in c for h in PERCENT_HINTS):  return "percent"
    if any(h in c for h in CURRENCY_HINTS): return "currency"
    return "integer"


def df_to_grid_json(df: pd.DataFrame, max_rows: int = 500) -> dict:
    if df is None or df.empty:
        return {"columns": [], "rows": [], "total": 0}

    total   = len(df)
    trimmed = df.head(max_rows)

    columns = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            columns.append({"field": col, "headerName": col,
                             "type": "number", "format": _col_format(col)})
        else:
            columns.append({"field": col, "headerName": col,
                             "type": "string", "format": None})

    rows = []
    for _, row in trimmed.iterrows():
        d: dict = {}
        for col in df.columns:
            val = row[col]
            try:
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    d[col] = None
                elif hasattr(val, "item"):
                    d[col] = val.item()
                elif hasattr(val, "__float__") and not isinstance(val, (str, bool)):
                    d[col] = float(val)
                elif isinstance(val, (int, float, bool)):
                    d[col] = val
                else:
                    d[col] = str(val)
            except Exception:
                d[col] = str(val)
        rows.append(d)

    return {"columns": columns, "rows": rows, "total": total}


def extract_kpis(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    kpis = []
    for col in df.columns:
        if len(kpis) >= 4:
            break
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        c   = col.lower()
        fmt = _col_format(col)
        total = df[col].sum()
        if fmt == "currency":
            if abs(total) >= 1_000_000:
                val = f"{total / 1_000_000:.1f}M"
            elif abs(total) >= 1_000:
                val = f"{total / 1_000:,.0f}K"
            else:
                val = f"{total:,.0f}"
            kpis.append({"label": col, "value": val})
        elif any(h in c for h in {"orders", "count", "units", "customers", "quantity"}):
            kpis.append({"label": col, "value": f"{int(total):,}"})
    return kpis


# ── Chart auto-detection ───────────────────────────────────────────────────────

def _safe_float(val) -> float:
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return 0.0
        return round(float(val), 2)
    except Exception:
        return 0.0


#: Max slices before a doughnut becomes unreadable and a bar is clearer.
DOUGHNUT_MAX_SLICES = 8


def detect_chart(df: pd.DataFrame, title: str = "") -> dict | None:
    """Pick a chart type from the SHAPE of the result set.

    Precedence: scatter -> doughnut -> horizontal bar -> stacked bar ->
    line -> vertical bar. The type is inferred from columns, not requested by
    the LLM, so phrasing a question to return different columns is what
    changes the chart.
    """
    if df is None or df.empty or len(df) < 2:
        return None

    time_cols = [c for c in df.columns if any(h in c.lower() for h in TIME_HINTS)]
    cat_cols  = [c for c in df.columns
                 if any(h in c.lower() for h in CAT_HINTS) and c not in time_cols]
    num_cols  = [c for c in df.columns
                 if pd.api.types.is_numeric_dtype(df[c])
                 and c not in time_cols and c not in cat_cols]

    if not num_cols:
        return None

    # Scatter: 2+ numerics, no time, no categories
    if not time_cols and not cat_cols and len(num_cols) >= 2:
        x_col, y_col = num_cols[0], num_cols[1]
        points = [
            {"x": _safe_float(df.iloc[i][x_col]), "y": _safe_float(df.iloc[i][y_col])}
            for i in range(len(df))
        ]
        return {
            "type": "scatter", "title": title, "labels": [],
            "datasets": [{"label": f"{x_col} vs {y_col}", "data": points,
                          "color": CHART_COLORS[0]}],
            "xLabel": x_col, "yLabel": y_col,
        }

    if not time_cols and not cat_cols:
        return None

    # Part-to-whole: category-only, few slices, ONE metric.
    #
    # Multiple numerics deliberately fall through to a grouped bar: a doughnut
    # can only render one series, so drawing it would silently hide every other
    # measure the user asked for. DOUGHNUT_MAX_SLICES is generous enough for
    # AdventureWorksDW's 4 categories and most territory/country breakdowns.
    _cat_labels = ([str(v) for v in df[cat_cols[0]]] if cat_cols else [])
    _longest_label = max((len(x) for x in _cat_labels), default=0)

    if (not time_cols and cat_cols
            and len(df) <= DOUGHNUT_MAX_SLICES
            and len(num_cols) == 1
            and _longest_label <= 18                # long names need a bar's room
            and (df[num_cols[0]] >= 0).all()):     # negatives are meaningless as slices
        label_col  = cat_cols[0]
        num_col    = num_cols[0]
        labels     = [str(df.iloc[i][label_col]) for i in range(len(df))]
        vals       = [_safe_float(df.iloc[i][num_col]) for i in range(len(df))]
        seg_colors = [CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(df))]
        return {
            "type": "doughnut", "title": title, "labels": labels,
            "datasets": [{"label": num_col, "data": vals,
                          "color": CHART_COLORS[0], "segmentColors": seg_colors}],
        }

    # Horizontal bar: many categories, or long labels that would be unreadable
    # rotated 45° on an x-axis (product/subcategory names routinely are).
    if not time_cols and cat_cols:
        label_col  = cat_cols[0]
        labels     = [str(df.iloc[i][label_col]) for i in range(len(df))]
        longest    = max((len(x) for x in labels), default=0)
        if len(df) > DOUGHNUT_MAX_SLICES or longest > 18:
            datasets = [
                {"label": col,
                 "data": [_safe_float(df.iloc[j][col]) for j in range(len(df))],
                 "color": CHART_COLORS[i % len(CHART_COLORS)]}
                for i, col in enumerate(num_cols[:5])
            ]
            return {"type": "horizontal_bar", "title": title,
                    "labels": labels, "datasets": datasets}

    # Stacked bar: time + category + multiple numerics
    if time_cols and cat_cols and len(num_cols) >= 2:
        str_time   = [c for c in time_cols if df[c].dtype == object]
        label_cols = cat_cols[:1] + (str_time[:1] if str_time else time_cols[:1])
        labels     = [" ".join(str(df.iloc[i][c]) for c in label_cols) for i in range(len(df))]
        datasets   = [
            {"label": col,
             "data": [_safe_float(df.iloc[j][col]) for j in range(len(df))],
             "color": CHART_COLORS[i % len(CHART_COLORS)]}
            for i, col in enumerate(num_cols[:5])
        ]
        return {"type": "stacked_bar", "title": title, "labels": labels, "datasets": datasets}

    # Line (time only) or Bar (categories)
    if time_cols and cat_cols:
        chart_type = "bar"
        str_time   = [c for c in time_cols if df[c].dtype == object]
        label_cols = cat_cols[:1] + (str_time[:1] if str_time else time_cols[:1])
    elif time_cols:
        chart_type = "line"
        str_time   = [c for c in time_cols if df[c].dtype == object]
        label_cols = str_time[:1] if str_time else time_cols[:1]
    else:
        chart_type = "bar"
        label_cols = cat_cols[:1]

    labels = [" ".join(str(df.iloc[i][c]) for c in label_cols) for i in range(len(df))]

    if chart_type == "line":
        labels    = list(reversed(labels))
        idx_order = list(reversed(range(len(df))))
    else:
        idx_order = list(range(len(df)))

    datasets = [
        {"label": col,
         "data": [_safe_float(df.iloc[j][col]) for j in idx_order],
         "color": CHART_COLORS[i % len(CHART_COLORS)]}
        for i, col in enumerate(num_cols[:5])
    ]
    return {"type": chart_type, "title": title, "labels": labels, "datasets": datasets}
