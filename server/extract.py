"""
server/extract.py — Extract a star schema from SQL Server into a local DuckDB
denormalized "wide" table, the substrate for driver analysis.

Why a local store?
    Driver analysis (period attribution, key-influencer ML, changepoint slicing)
    runs many GROUP BYs and full-column scans over the same data. Doing that
    repeatedly against SQL Server is slow and chatty. We extract once into a
    single denormalized table — one row per fact row, with every dimension
    attribute flattened alongside the measures and a parsed `date` column — then
    all analysis runs locally in DuckDB / pandas.

Star discovery is automatic:
    • dimensions = every table the fact has a FK to (deduped),
    • attributes = each dimension's short, low-cardinality text/categorical cols,
    • measures   = config.settings.STAR_MEASURES,
    • date       = fact.STAR_DATE_KEY -> STAR_DATE_DIM.STAR_DATE_COL.

Run it:
    python -m server.extract              # full rebuild
    python -m server.extract --limit 50000
"""

from __future__ import annotations

import json
import os
import time

import duckdb
import pandas as pd

from config.settings import (
    DUCKDB_PATH, STAR_FACT, STAR_DATE_KEY, STAR_DATE_DIM, STAR_DATE_PK,
    STAR_DATE_COL, STAR_MEASURES, MAX_DIM_CARDINALITY, EXTRACT_ROW_LIMIT,
)
from server.database import (
    get_connection, get_foreign_keys, get_dimension_attributes,
)

_META_PATH = os.path.splitext(DUCKDB_PATH)[0] + "_meta.json"


def _short(table: str) -> str:
    """'dbo.DimProduct' -> 'Product' (drop schema + 'Dim' prefix)."""
    name = table.split(".")[-1]
    return name[3:] if name.lower().startswith("dim") else name


def discover_star() -> dict:
    """Resolve the fact's dimensions and attribute columns from FK metadata.

    Returns a plan dict:
        {
          "fact": "dbo.FactInternetSales",
          "measures": ["SalesAmount", ...],
          "dims": [
            {"table": "dbo.DimProduct", "alias": "d0", "prefix": "Product",
             "fk_col": "ProductKey", "pk_col": "ProductKey",
             "attrs": ["EnglishProductName", "Color", ...]},
            ...
          ],
        }
    """
    fks = get_foreign_keys()
    fact_fks = [f for f in fks if f["fk_table"].lower() == STAR_FACT.lower()]

    dims: list[dict] = []
    seen_tables: set[str] = set()
    for fk in fact_fks:
        pk_table = fk["pk_table"]
        # The date dimension is handled specially (one canonical date column).
        if pk_table.lower() == STAR_DATE_DIM.lower():
            continue
        if pk_table.lower() in seen_tables:
            continue
        attrs = get_dimension_attributes(pk_table)
        if not attrs:
            continue
        seen_tables.add(pk_table.lower())
        dims.append({
            "table": pk_table,
            "alias": f"d{len(dims)}",
            "prefix": _short(pk_table),
            "fk_col": fk["fk_col"],
            "pk_col": fk["pk_col"],
            "attrs": attrs,
        })

    return {"fact": STAR_FACT, "measures": list(STAR_MEASURES), "dims": dims}


def build_extract_sql(plan: dict, limit: int | None) -> str:
    """Build the denormalized SELECT joining the fact to all discovered dims."""
    top = f"TOP {limit} " if limit else ""
    select: list[str] = [
        f"CONVERT(date, dt.[{STAR_DATE_COL}]) AS [date]",
    ]
    for m in plan["measures"]:
        select.append(f"f.[{m}] AS [{m}]")

    for dim in plan["dims"]:
        for col in dim["attrs"]:
            alias = f"{dim['prefix']}_{col}"
            select.append(f"{dim['alias']}.[{col}] AS [{alias}]")

    joins = [
        f"JOIN {STAR_DATE_DIM} dt "
        f"ON f.[{STAR_DATE_KEY}] = dt.[{STAR_DATE_PK}]"
    ]
    for dim in plan["dims"]:
        joins.append(
            f"LEFT JOIN {dim['table']} {dim['alias']} "
            f"ON f.[{dim['fk_col']}] = {dim['alias']}.[{dim['pk_col']}]"
        )

    return (
        f"SELECT {top}\n    " + ",\n    ".join(select) + "\n"
        f"FROM {plan['fact']} f\n" + "\n".join(joins)
    )


def extract_to_duckdb(limit: int | None = EXTRACT_ROW_LIMIT, verbose: bool = True) -> dict:
    """Run the extract and persist it to DuckDB. Returns a metadata summary."""
    t0 = time.time()
    plan = discover_star()
    if not plan["dims"]:
        raise RuntimeError(
            f"No dimensions discovered for fact {STAR_FACT}. Check FK metadata "
            f"and STAR_FACT in config/settings.py."
        )

    sql = build_extract_sql(plan, limit)
    if verbose:
        print(f"[extract] fact={STAR_FACT}  dims={len(plan['dims'])}  "
              f"measures={plan['measures']}")
        print(f"[extract] pulling rows from SQL Server"
              f"{f' (TOP {limit})' if limit else ''}...")

    conn = get_connection()
    df = pd.read_sql(sql, conn)
    conn.close()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    # Keep measures numeric; coerce the rest, then prune attributes that are too
    # high-cardinality to be useful drivers (free-text names, near-unique codes).
    measures = [m for m in plan["measures"] if m in df.columns]
    for m in measures:
        df[m] = pd.to_numeric(df[m], errors="coerce")

    attr_cols = [c for c in df.columns if c not in (["date"] + measures)]
    kept_dims: list[str] = []
    dropped: list[str] = []
    for c in attr_cols:
        nuniq = df[c].nunique(dropna=True)
        if 1 < nuniq <= MAX_DIM_CARDINALITY:
            df[c] = df[c].astype("string").fillna("(unknown)")
            kept_dims.append(c)
        else:
            dropped.append(c)
    df = df.drop(columns=dropped)

    os.makedirs(os.path.dirname(DUCKDB_PATH), exist_ok=True)
    con = duckdb.connect(DUCKDB_PATH)
    con.execute("DROP TABLE IF EXISTS analytics")
    con.register("df_in", df)
    con.execute("CREATE TABLE analytics AS SELECT * FROM df_in")
    con.unregister("df_in")
    con.close()

    meta = {
        "fact": plan["fact"],
        "date_col": "date",
        "measures": measures,
        "dims": kept_dims,
        "dropped_high_cardinality": dropped,
        "rows": int(len(df)),
        "date_min": df["date"].min().strftime("%Y-%m-%d"),
        "date_max": df["date"].max().strftime("%Y-%m-%d"),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    if verbose:
        print(f"[extract] {meta['rows']:,} rows  "
              f"{len(kept_dims)} dim attrs  {len(measures)} measures  "
              f"({meta['date_min']} -> {meta['date_max']})  in {meta['elapsed_sec']}s")
        if dropped:
            print(f"[extract] dropped {len(dropped)} high-cardinality cols: "
                  f"{', '.join(dropped[:8])}{'...' if len(dropped) > 8 else ''}")
        print(f"[extract] wrote {DUCKDB_PATH}")
    return meta


def load_meta() -> dict | None:
    """Return the metadata of the current extract, or None if not built yet."""
    if not os.path.exists(_META_PATH):
        return None
    with open(_META_PATH, encoding="utf-8") as f:
        return json.load(f)


def connect() -> duckdb.DuckDBPyConnection:
    """Open a read-only connection to the analytics store."""
    if not os.path.exists(DUCKDB_PATH):
        raise RuntimeError(
            "Analytics store not built. Run:  python -m server.extract"
        )
    return duckdb.connect(DUCKDB_PATH, read_only=True)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Extract star schema to DuckDB.")
    ap.add_argument("--limit", type=int, default=EXTRACT_ROW_LIMIT or 0,
                    help="Max fact rows to pull (0 = all).")
    args = ap.parse_args()
    extract_to_duckdb(limit=args.limit or None)
