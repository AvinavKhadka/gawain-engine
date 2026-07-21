"""
config/settings.py — Single source of truth for all application configuration.

Every tunable value lives here. Edit this file (or the .env) to change behaviour.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Database ───────────────────────────────────────────────────────────────────
# SQL Server connection — edit DB_SERVER to match your instance name
DB_SERVER   = os.getenv("DB_SERVER",   r"IMPOSSIBLEISNOT\MSSQLSERVER2019")
DB_DATABASE = os.getenv("DB_DATABASE", "AdventureWorksDW2019")
DB_DRIVER   = os.getenv("DB_DRIVER",   "ODBC Driver 17 for SQL Server")
DB_USER     = os.getenv("DB_USER",     "")       # blank = Windows auth
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Optional comma-separated table whitelist (empty = auto-discover all tables)
# Example: "FactInternetSales,DimDate,DimProduct,DimCustomer"
DB_TABLE_FILTER = os.getenv("DB_TABLE_FILTER", "")

# ── Ollama ─────────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",    "llama3")

# LLM generation parameters
LLM_TEMPERATURE     = float(os.getenv("LLM_TEMPERATURE",  "0.1"))
LLM_TEMPERATURE_ANALYSIS = float(os.getenv("LLM_TEMPERATURE_ANALYSIS", "0.15"))
LLM_NUM_PREDICT     = int(os.getenv("LLM_NUM_PREDICT",   "1024"))
LLM_ANALYSIS_TOKENS = int(os.getenv("LLM_ANALYSIS_TOKENS", "2048"))
LLM_TIMEOUT_SQL     = float(os.getenv("LLM_TIMEOUT_SQL",    "180.0"))   # seconds
LLM_TIMEOUT_STREAM  = float(os.getenv("LLM_TIMEOUT_STREAM", "240.0"))

# ── Storage ────────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_DB_PATH = os.path.join(_ROOT, "storage", "history.db")
STATIC_DIR      = os.path.join(_ROOT, "static")
DUCKDB_PATH     = os.path.join(_ROOT, "storage", "analytics.duckdb")


# ── Driver-analysis engine (local DuckDB extract) ──────────────────────────────
# Defines the star schema the driver engine extracts and analyses. Dimensions and
# their attribute columns are auto-discovered from FK metadata; the values below
# only need editing to point at a different fact table / measures / date grain.
#
# All overridable via .env so the engine works against any SQL Server star schema.
STAR_FACT       = os.getenv("STAR_FACT",      "dbo.FactInternetSales")
STAR_DATE_KEY   = os.getenv("STAR_DATE_KEY",  "OrderDateKey")          # FK on fact -> date dim
STAR_DATE_DIM   = os.getenv("STAR_DATE_DIM",  "dbo.DimDate")
STAR_DATE_PK    = os.getenv("STAR_DATE_PK",   "DateKey")               # PK on date dim
STAR_DATE_COL   = os.getenv("STAR_DATE_COL",  "FullDateAlternateKey")  # the real date column
# Measures (fact numeric columns) the engine can attribute. Comma-separated.
STAR_MEASURES   = [m.strip() for m in os.getenv(
    "STAR_MEASURES", "SalesAmount,OrderQuantity,TotalProductCost").split(",") if m.strip()]
# A text/categorical column is treated as a usable dimension attribute only if its
# distinct-value count is at most this (keeps high-cardinality keys/names out).
MAX_DIM_CARDINALITY = int(os.getenv("MAX_DIM_CARDINALITY", "60"))
# Cap rows pulled into the extract (None = all). Useful on large facts.
EXTRACT_ROW_LIMIT   = int(os.getenv("EXTRACT_ROW_LIMIT", "0")) or None

# ── Session / history ──────────────────────────────────────────────────────────
SESSION_MAX_TURNS = 6    # how many prior Q&A turns to pass as LLM context

# ── SSAS (optional) ────────────────────────────────────────────────────────────
SSAS_CONNECTION_STRING = os.getenv("SSAS_CONNECTION_STRING", "")


# ── SQL column-format hints ────────────────────────────────────────────────────
# Used by database.py to auto-format grid columns and extract KPIs
CURRENCY_HINTS = {
    "amount", "sales", "cost", "price", "profit", "revenue", "quota",
    "freight", "tax", "rate", "cog", "gross", "net", "earn", "income",
    "extended", "standard", "unit", "dealer", "spend",
}
INTEGER_HINTS = {
    "qty", "quantity", "count", "orders", "units", "num",
    "key", "year", "month", "quarter", "customers",
}
PERCENT_HINTS = {"pct", "percent", "margin", "discount"}


# ── Chart detection hints ──────────────────────────────────────────────────────
TIME_HINTS = {"year", "month", "quarter", "date", "period", "week", "day"}
CAT_HINTS  = {
    "category", "territory", "region", "country", "subcategory",
    "type", "name", "group", "segment", "channel",
}

# Arasaka tactical palette for charts — red chrome, neon cyan, warning yellow
CHART_COLORS = [
    "#ff003c", "#00f0ff", "#fcee0a", "#ffffff", "#9d00ff",
    "#00ff88", "#ff6b00", "#0080ff", "#ff4d6d", "#47e5ff",
]


# ── LLM question-routing keywords ─────────────────────────────────────────────
# Determines which supplemental analysis paths are triggered
TREND_KEYWORDS = {
    "trend", "trends", "over time", "history", "historical",
    "monthly", "quarterly", "yearly", "annual",
    "last 5", "last five", "last 12", "last twelve",
    "recent", "growth", "decline", "progression",
    "by month", "by quarter", "by year",
}

DEEP_KEYWORDS = {
    "why", "cause", "reason", "explain", "understand", "analysis", "analyze",
    "analyse", "impact", "insight", "insights", "breakdown", "deep",
    "compare", "performance", "trend", "growth", "decline", "drop", "fell",
    "increase", "rose", "driver", "factor", "factors",
    "which product", "which region",
}

# Routes a question to the driver-analysis engine (DuckDB extract) instead of
# plain text->SQL. These ask "what dimension field drives/explains the fact".
DRIVER_KEYWORDS = {
    "driver", "drivers", "drives", "driving", "what causes", "what is causing",
    "what's causing", "attribution", "attribute", "contribut", "influence",
    "influencer", "influencers", "which dimension", "which field",
    "what changed", "what's changing", "biggest factor", "key factor",
    "key factors", "explain the change", "explain the drop", "explain the rise",
    "explain the increase", "explain the decline", "what explains",
    "most important", "what's behind", "behind the change",
}

# Sub-routing within the driver engine to a specific technique.
DRIVER_CHANGEPOINT_HINTS = {
    "when did", "changepoint", "change point", "inflection", "shift", "broke",
    "turning point", "started to", "began to", "when the",
}
DRIVER_INFLUENCER_HINTS = {
    "overall", "in general", "across all", "explain variance", "key influencer",
    "key influencers", "most important", "which fields matter", "feature importance",
    "generally", "historically",
}

# Triggers multi-step query planning (LLM breaks question into sub-queries)
PLAN_TRIGGERS = {
    " and ", " vs ", " versus ", " compared to ", "relationship between",
    "correlation", "combination", "both ", "across different",
}


# ── Database connection string ─────────────────────────────────────────────────
def get_connection_string() -> str:
    if DB_USER and DB_PASSWORD:
        return (
            f"DRIVER={{{DB_DRIVER}}};"
            f"SERVER={DB_SERVER};"
            f"DATABASE={DB_DATABASE};"
            f"UID={DB_USER};"
            f"PWD={DB_PASSWORD};"
        )
    return (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        "Trusted_Connection=yes;"
    )
