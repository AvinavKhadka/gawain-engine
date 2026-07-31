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
from server.sql_dialect import preprocess_sql, register_repair_hook
from config.settings import (
    get_connection_string, DB_TABLE_FILTER, DB_DATABASE,
    CURRENCY_HINTS, PERCENT_HINTS,
    TIME_HINTS, CAT_HINTS, CHART_COLORS,
)

log = get_logger(__name__)


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

    lines = [f"=== {DB_DATABASE} Schema ===", ""]

    # The date boundary is a fact about the DATA, not the schema. Without it an
    # LLM reaches for GETDATE() and silently produces nonsense — ages computed
    # against today, "last 30 days" filters matching nothing.
    coverage = _probe_date_coverage(cursor, tables)
    if coverage:
        lines += [
            f"DATA COVERAGE: {coverage}",
            "  The data ENDS on the later date above. Never use GETDATE(),",
            "  CURRENT_TIMESTAMP or SYSDATETIME() — they resolve to today and",
            "  will produce wrong results. Anchor any relative period ('recent',",
            "  'last N days', age calculations) to the end of the coverage range.",
            "",
        ]

    lines += ["KEY RELATIONSHIPS:"]
    for fk_table, fk_col, pk_table, pk_col in fk_rows:
        lines.append(f"  {fk_table}.{fk_col} -> {pk_table}.{pk_col}")
    lines.append("")

    for table_name, cols in tables.items():
        lines.append(f"Table: {table_name}")
        lines.extend(cols)
        lines.append("")

    return "\n".join(lines)


def _probe_date_coverage(cursor, tables: dict) -> str | None:
    """Find the real date range in the data by querying it.

    INFORMATION_SCHEMA can say a column is a DATE; it cannot say the data stops
    in 2014. That boundary is a fact about the *contents*, so it has to be
    measured rather than declared — which keeps it correct for any database
    instead of hardcoding one dataset's dates into the prompt.

    Returns None on any failure: a missing hint degrades the prompt slightly,
    but it must never break schema discovery.
    """
    candidates: list[tuple[str, str]] = []
    for table_name, cols in tables.items():
        for col_line in cols:
            m = re.match(
                r"\s*-\s*(\w+)\s*\((date|datetime|datetime2|smalldatetime)\)",
                col_line, re.IGNORECASE,
            )
            if m:
                candidates.append((table_name, m.group(1)))

    if not candidates:
        return None

    # Prefer fact tables: a date dimension lists every calendar date it was
    # generated with, including years holding no transactions, which would
    # overstate the true coverage.
    candidates.sort(key=lambda tc: (0 if "fact" in tc[0].lower() else 1, tc[0]))

    for table_name, col in candidates[:3]:
        try:
            cursor.execute(
                f"SELECT MIN([{col}]), MAX([{col}]) FROM {table_name} "  # noqa: S608
                f"WHERE [{col}] IS NOT NULL"
            )
            row = cursor.fetchone()
            if row and row[0] and row[1]:
                lo, hi = row
                if hasattr(lo, "strftime"):
                    return f"{lo:%Y-%m-%d} to {hi:%Y-%m-%d}"
                return f"{lo} to {hi}"
        except Exception:
            log.debug("date probe failed for %s.%s", table_name, col, exc_info=True)
            continue
    return None


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


def _learned_repair(sql: str) -> str:
    """Column mappings observed from repairs that actually worked here."""
    from server.failures import apply_learned_fixes
    fixed, _ = apply_learned_fixes(sql)
    return fixed


def install_repair_hooks() -> None:
    """Register this module's repair passes with the dialect sanitiser.

    Called at import, and exposed so tests can reinstate the hooks after
    clearing them — re-importing would not, since the module is cached.
    Order matters: a mapping confirmed empirically beats a similarity guess.
    """
    register_repair_hook(_learned_repair)
    register_repair_hook(repair_columns)


install_repair_hooks()


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
