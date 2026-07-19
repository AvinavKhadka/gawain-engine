"""
prepare_data.py — Generate SQL training pairs for fine-tuning Gawain

Modes:
  python train/prepare_data.py                  # Generate from built-in templates
  python train/prepare_data.py --from-history   # Also pull from app query history
  python train/prepare_data.py --validate       # Validate all SQLs against live DB

Output: train/data/examples.jsonl   (append new pairs, skip duplicates)
        train/data/train.txt         (llama.cpp training format)

Run from project root with venv active.
"""

import argparse
import json
import os
import re
import sqlite3
import sys

# ── Ensure project root is on path ───────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

OUTPUT_DIR    = os.path.join(ROOT, "train", "data")
EXAMPLES_FILE = os.path.join(OUTPUT_DIR, "examples.jsonl")
TRAIN_FILE    = os.path.join(OUTPUT_DIR, "train.txt")
HISTORY_DB    = os.path.join(ROOT, "storage", "history.db")

# ── Llama-3 instruction template ──────────────────────────────────────────────
TEMPLATE = "<s>[INST] {question} [/INST] ```sql\n{sql}\n``` </s>"

# ── Built-in seed pairs ───────────────────────────────────────────────────────
SEED_PAIRS = [
    {
        "question": "Show total revenue by year",
        "sql": """SELECT
    dd.CalendarYear                      AS Year,
    SUM(fis.SalesAmount)                 AS Revenue,
    COUNT(DISTINCT fis.SalesOrderNumber) AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY dd.CalendarYear
ORDER BY dd.CalendarYear""",
    },
    {
        "question": "Top 10 customers by total spend",
        "sql": """SELECT TOP 10
    dc.FirstName + ' ' + dc.LastName      AS Customer,
    dg.EnglishCountryRegionName           AS Country,
    SUM(fis.SalesAmount)                  AS TotalRevenue,
    COUNT(DISTINCT fis.SalesOrderNumber)  AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimCustomer  dc ON fis.CustomerKey = dc.CustomerKey
JOIN dbo.DimGeography dg ON dc.GeographyKey = dg.GeographyKey
GROUP BY dc.FirstName, dc.LastName, dg.EnglishCountryRegionName
ORDER BY TotalRevenue DESC""",
    },
    {
        "question": "Gross profit margin by product category",
        "sql": """SELECT
    dpc.EnglishProductCategoryName                                                           AS Category,
    SUM(fis.SalesAmount)                                                                     AS Revenue,
    SUM(fis.SalesAmount - fis.TotalProductCost)                                              AS GrossProfit,
    CAST(SUM(fis.SalesAmount - fis.TotalProductCost) * 100.0
         / NULLIF(SUM(fis.SalesAmount), 0) AS DECIMAL(5,1))                                 AS MarginPct
FROM dbo.FactInternetSales fis
JOIN dbo.DimProduct            dp  ON fis.ProductKey           = dp.ProductKey
JOIN dbo.DimProductSubcategory dps ON dp.ProductSubcategoryKey = dps.ProductSubcategoryKey
JOIN dbo.DimProductCategory    dpc ON dps.ProductCategoryKey   = dpc.ProductCategoryKey
GROUP BY dpc.EnglishProductCategoryName
ORDER BY Revenue DESC""",
    },
    {
        "question": "Monthly revenue trend for 2013",
        "sql": """SELECT
    dd.MonthNumberOfYear                        AS Month,
    LEFT(dd.EnglishMonthName, 3)                AS MonthName,
    SUM(fis.SalesAmount)                        AS Revenue,
    COUNT(DISTINCT fis.SalesOrderNumber)        AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
WHERE dd.CalendarYear = 2013
GROUP BY dd.MonthNumberOfYear, dd.EnglishMonthName
ORDER BY dd.MonthNumberOfYear""",
    },
    {
        "question": "Compare 2012 vs 2013 revenue by product category",
        "sql": """SELECT
    dpc.EnglishProductCategoryName AS Category,
    SUM(CASE WHEN dd.CalendarYear = 2012 THEN fis.SalesAmount ELSE 0 END) AS Revenue_2012,
    SUM(CASE WHEN dd.CalendarYear = 2013 THEN fis.SalesAmount ELSE 0 END) AS Revenue_2013,
    CAST(
        (SUM(CASE WHEN dd.CalendarYear = 2013 THEN fis.SalesAmount ELSE 0 END)
         - SUM(CASE WHEN dd.CalendarYear = 2012 THEN fis.SalesAmount ELSE 0 END))
        * 100.0 / NULLIF(SUM(CASE WHEN dd.CalendarYear = 2012 THEN fis.SalesAmount ELSE 0 END), 0)
    AS DECIMAL(5,1)) AS ChangePct
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate               dd  ON fis.OrderDateKey         = dd.DateKey
JOIN dbo.DimProduct            dp  ON fis.ProductKey           = dp.ProductKey
JOIN dbo.DimProductSubcategory dps ON dp.ProductSubcategoryKey = dps.ProductSubcategoryKey
JOIN dbo.DimProductCategory    dpc ON dps.ProductCategoryKey   = dpc.ProductCategoryKey
WHERE dd.CalendarYear IN (2012, 2013)
GROUP BY dpc.EnglishProductCategoryName
ORDER BY Revenue_2013 DESC""",
    },
    {
        "question": "Revenue by sales territory region and year",
        "sql": """SELECT
    dst.SalesTerritoryGroup              AS Region,
    dd.CalendarYear                      AS Year,
    SUM(fis.SalesAmount)                 AS Revenue,
    COUNT(DISTINCT fis.SalesOrderNumber) AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate           dd  ON fis.OrderDateKey      = dd.DateKey
JOIN dbo.DimSalesTerritory dst ON fis.SalesTerritoryKey = dst.SalesTerritoryKey
GROUP BY dst.SalesTerritoryGroup, dd.CalendarYear
ORDER BY dst.SalesTerritoryGroup, dd.CalendarYear""",
    },
    {
        "question": "Top 10 best-selling products by revenue",
        "sql": """SELECT TOP 10
    dp.EnglishProductName                AS Product,
    dps.EnglishProductSubcategoryName    AS Subcategory,
    dpc.EnglishProductCategoryName       AS Category,
    SUM(fis.SalesAmount)                 AS Revenue,
    SUM(fis.OrderQuantity)               AS UnitsSold
FROM dbo.FactInternetSales fis
JOIN dbo.DimProduct            dp  ON fis.ProductKey           = dp.ProductKey
JOIN dbo.DimProductSubcategory dps ON dp.ProductSubcategoryKey = dps.ProductSubcategoryKey
JOIN dbo.DimProductCategory    dpc ON dps.ProductCategoryKey   = dpc.ProductCategoryKey
GROUP BY dp.EnglishProductName, dps.EnglishProductSubcategoryName, dpc.EnglishProductCategoryName
ORDER BY Revenue DESC""",
    },
    {
        "question": "Customer segment breakdown by lifetime spend",
        "sql": """SELECT
    CASE
        WHEN TotalSpend >= 5000 THEN 'High Value (>$5K)'
        WHEN TotalSpend >= 1000 THEN 'Mid Value ($1K-$5K)'
        ELSE                         'Standard (<$1K)'
    END                AS Segment,
    COUNT(*)           AS Customers,
    SUM(TotalSpend)    AS Revenue,
    AVG(TotalSpend)    AS AvgSpend
FROM (
    SELECT CustomerKey, SUM(SalesAmount) AS TotalSpend
    FROM dbo.FactInternetSales
    GROUP BY CustomerKey
) t
GROUP BY
    CASE
        WHEN TotalSpend >= 5000 THEN 'High Value (>$5K)'
        WHEN TotalSpend >= 1000 THEN 'Mid Value ($1K-$5K)'
        ELSE                         'Standard (<$1K)'
    END
ORDER BY Revenue DESC""",
    },
    {
        "question": "Quarterly revenue trend from 2010 to 2014",
        "sql": """SELECT
    dd.CalendarYear                      AS Year,
    dd.CalendarQuarter                   AS Quarter,
    SUM(fis.SalesAmount)                 AS Revenue,
    SUM(fis.SalesAmount - fis.TotalProductCost) AS GrossProfit,
    COUNT(DISTINCT fis.SalesOrderNumber) AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY dd.CalendarYear, dd.CalendarQuarter
ORDER BY dd.CalendarYear, dd.CalendarQuarter""",
    },
    {
        "question": "Top 5 subcategories by revenue in 2013",
        "sql": """SELECT TOP 5
    dps.EnglishProductSubcategoryName    AS Subcategory,
    dpc.EnglishProductCategoryName       AS Category,
    SUM(fis.SalesAmount)                 AS Revenue,
    SUM(fis.OrderQuantity)               AS UnitsSold
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate               dd  ON fis.OrderDateKey         = dd.DateKey
JOIN dbo.DimProduct            dp  ON fis.ProductKey           = dp.ProductKey
JOIN dbo.DimProductSubcategory dps ON dp.ProductSubcategoryKey = dps.ProductSubcategoryKey
JOIN dbo.DimProductCategory    dpc ON dps.ProductCategoryKey   = dpc.ProductCategoryKey
WHERE dd.CalendarYear = 2013
GROUP BY dps.EnglishProductSubcategoryName, dpc.EnglishProductCategoryName
ORDER BY Revenue DESC""",
    },
    {
        "question": "Average order value by year",
        "sql": """SELECT
    dd.CalendarYear                                AS Year,
    COUNT(DISTINCT fis.SalesOrderNumber)           AS Orders,
    SUM(fis.SalesAmount)                           AS TotalRevenue,
    SUM(fis.SalesAmount) / COUNT(DISTINCT fis.SalesOrderNumber) AS AvgOrderValue
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY dd.CalendarYear
ORDER BY dd.CalendarYear""",
    },
    {
        "question": "Revenue by country",
        "sql": """SELECT
    dst.SalesTerritoryCountry            AS Country,
    dst.SalesTerritoryGroup              AS Region,
    SUM(fis.SalesAmount)                 AS Revenue,
    COUNT(DISTINCT fis.CustomerKey)      AS UniqueCustomers,
    COUNT(DISTINCT fis.SalesOrderNumber) AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimSalesTerritory dst ON fis.SalesTerritoryKey = dst.SalesTerritoryKey
GROUP BY dst.SalesTerritoryCountry, dst.SalesTerritoryGroup
ORDER BY Revenue DESC""",
    },
    {
        "question": "Which promotions generated the most revenue",
        "sql": """SELECT TOP 20
    dp.EnglishPromotionName              AS Promotion,
    dp.EnglishPromotionType              AS Type,
    SUM(fis.SalesAmount)                 AS Revenue,
    SUM(fis.DiscountAmount)              AS TotalDiscount,
    COUNT(DISTINCT fis.SalesOrderNumber) AS Orders
FROM dbo.FactInternetSales fis
JOIN dbo.DimPromotion dp ON fis.PromotionKey = dp.PromotionKey
GROUP BY dp.EnglishPromotionName, dp.EnglishPromotionType
ORDER BY Revenue DESC""",
    },
    {
        "question": "How many unique customers bought in each year",
        "sql": """SELECT
    dd.CalendarYear                 AS Year,
    COUNT(DISTINCT fis.CustomerKey) AS UniqueCustomers,
    COUNT(DISTINCT fis.SalesOrderNumber) AS Orders,
    SUM(fis.SalesAmount)            AS Revenue
FROM dbo.FactInternetSales fis
JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
GROUP BY dd.CalendarYear
ORDER BY dd.CalendarYear""",
    },
    {
        "question": "Revenue per unit sold by product category",
        "sql": """SELECT
    dpc.EnglishProductCategoryName               AS Category,
    SUM(fis.SalesAmount)                         AS Revenue,
    SUM(fis.OrderQuantity)                       AS UnitsSold,
    SUM(fis.SalesAmount) / SUM(fis.OrderQuantity) AS RevenuePerUnit
FROM dbo.FactInternetSales fis
JOIN dbo.DimProduct            dp  ON fis.ProductKey           = dp.ProductKey
JOIN dbo.DimProductSubcategory dps ON dp.ProductSubcategoryKey = dps.ProductSubcategoryKey
JOIN dbo.DimProductCategory    dpc ON dps.ProductCategoryKey   = dpc.ProductCategoryKey
GROUP BY dpc.EnglishProductCategoryName
ORDER BY Revenue DESC""",
    },
]


# ── Helper functions ──────────────────────────────────────────────────────────

def load_existing(path: str) -> set[str]:
    """Return set of questions already in the output file."""
    if not os.path.exists(path):
        return set()
    questions = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    questions.add(json.loads(line)["question"].lower().strip())
                except (json.JSONDecodeError, KeyError):
                    pass
    return questions


def append_pair(path: str, question: str, sql: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"question": question, "sql": sql.strip()}, ensure_ascii=False) + "\n")


def validate_sql_live(sql: str) -> tuple[bool, str]:
    """Run SET NOEXEC ON against the live DB. Returns (ok, error_msg)."""
    try:
        import pyodbc
        from core.config import get_connection_string
        conn = pyodbc.connect(get_connection_string(), timeout=10)
        cur = conn.cursor()
        cur.execute("SET NOEXEC ON")
        try:
            cur.execute(sql)
            ok, msg = True, ""
        except pyodbc.Error as e:
            ok, msg = False, str(e)
        cur.execute("SET NOEXEC OFF")
        conn.close()
        return ok, msg
    except Exception as e:
        return False, f"Connection error: {e}"


def load_from_history() -> list[dict]:
    """Pull question+sql pairs from the app's SQLite history DB."""
    if not os.path.exists(HISTORY_DB):
        print(f"  History DB not found at {HISTORY_DB} — skipping.")
        return []
    conn = sqlite3.connect(HISTORY_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT question, sql FROM history WHERE sql IS NOT NULL AND sql != '' ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [{"question": r["question"], "sql": r["sql"]} for r in rows]


def convert_to_training_format(examples_path: str, train_path: str):
    """Convert examples.jsonl → llama.cpp train.txt"""
    if not os.path.exists(examples_path):
        print(f"  No examples file found at {examples_path}")
        return 0

    count = 0
    os.makedirs(os.path.dirname(train_path), exist_ok=True)
    with open(examples_path, encoding="utf-8") as fin, \
         open(train_path, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                fout.write(
                    TEMPLATE.format(
                        question=obj["question"],
                        sql=obj["sql"].strip(),
                    ) + "\n"
                )
                count += 1
            except (json.JSONDecodeError, KeyError):
                continue
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate SQL training data for Gawain")
    parser.add_argument("--from-history", action="store_true",
                        help="Also pull from app query history DB")
    parser.add_argument("--validate", action="store_true",
                        help="Validate all SQL pairs against the live database")
    args = parser.parse_args()

    existing = load_existing(EXAMPLES_FILE)
    added = 0
    skipped = 0

    # ── Seed pairs ─────────────────────────────────────────────────────────
    print(f"Processing {len(SEED_PAIRS)} built-in seed pairs...")
    for pair in SEED_PAIRS:
        q = pair["question"].lower().strip()
        if q in existing:
            skipped += 1
            continue
        append_pair(EXAMPLES_FILE, pair["question"], pair["sql"])
        existing.add(q)
        added += 1

    # ── History pairs ───────────────────────────────────────────────────────
    if args.from_history:
        hist_pairs = load_from_history()
        print(f"Processing {len(hist_pairs)} history pairs...")
        for pair in hist_pairs:
            q = pair["question"].lower().strip()
            if q in existing or not pair.get("sql"):
                skipped += 1
                continue
            append_pair(EXAMPLES_FILE, pair["question"], pair["sql"])
            existing.add(q)
            added += 1

    print(f"\nResult: {added} new pairs added, {skipped} duplicates skipped.")
    print(f"Total examples: {len(existing)}")
    print(f"Output: {EXAMPLES_FILE}")

    # ── Validation ──────────────────────────────────────────────────────────
    if args.validate:
        print("\nValidating SQL against live database...")
        valid = invalid = 0
        with open(EXAMPLES_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ok, err = validate_sql_live(obj["sql"])
                    if ok:
                        valid += 1
                    else:
                        invalid += 1
                        print(f"  INVALID: {obj['question'][:60]}")
                        print(f"           {err[:120]}")
                except (json.JSONDecodeError, KeyError):
                    continue
        print(f"\nValidation: {valid} valid, {invalid} invalid")

    # ── Convert to llama.cpp training format ───────────────────────────────
    count = convert_to_training_format(EXAMPLES_FILE, TRAIN_FILE)
    print(f"\nConverted {count} pairs -> {TRAIN_FILE}")
    print("\nNext steps:")
    print("  Ollama:     ollama create gawain-sql -f train/Modelfile")
    print("  Fine-tune:  train\\finetune.bat   (Windows)")
    print("              ./train/finetune.sh   (Linux/Mac)")


if __name__ == "__main__":
    main()
