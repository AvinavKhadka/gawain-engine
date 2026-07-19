import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from server.llm import generate_sql, extract_sql, is_trend_question
from server.database import get_schema_context, execute_query, df_to_grid_json, detect_chart, TREND_SQL_12MONTHS, TREND_SQL_5DAYS

schema = get_schema_context()
question = "Show me sales by product category per year"
print("is_trend_question:", is_trend_question(question))

print("Generating SQL...")
sql = extract_sql(generate_sql(question, schema))

print("\nExecuting...")
df, err = execute_query(sql)
if err:
    print("SQL error:", err[:300])
    sys.exit(1)

print("Column dtypes:", dict(zip(df.columns, df.dtypes)))
grid = df_to_grid_json(df)
chart = detect_chart(df, "Sales by Category per Year")

print("\nGrid columns:", [c["field"] for c in grid["columns"]])
print("Grid formats:", [c["format"] for c in grid["columns"]])
print("Grid rows:", len(grid["rows"]), "| total:", grid["total"])
print("Chart type:", chart["type"] if chart else "None")
print("Chart labels:", chart["labels"][:6] if chart else [])
print("Datasets:", [d["label"] for d in chart["datasets"]] if chart else [])

print("\n--- 5-day trend ---")
df5, e5 = execute_query(TREND_SQL_5DAYS)
if e5:
    print("Error:", e5)
else:
    c5 = detect_chart(df5, "Last 5 Days")
    print("rows:", len(df5), "| chart:", c5["type"] if c5 else None)
    print("labels:", c5["labels"] if c5 else None)

print("\n--- 12-month trend ---")
df12, e12 = execute_query(TREND_SQL_12MONTHS)
if e12:
    print("Error:", e12)
else:
    c12 = detect_chart(df12, "Last 12 Months")
    print("rows:", len(df12), "| chart type:", c12["type"] if c12 else None)
    print("labels:", c12["labels"] if c12 else None)
