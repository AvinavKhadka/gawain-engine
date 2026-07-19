"""server/llm.py — Ollama LLM calls: SQL generation, analysis streaming, query planning."""

import httpx
import json
import re

from config.settings import (
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    LLM_TEMPERATURE, LLM_TEMPERATURE_ANALYSIS,
    LLM_NUM_PREDICT, LLM_ANALYSIS_TOKENS,
    LLM_TIMEOUT_SQL, LLM_TIMEOUT_STREAM,
    TREND_KEYWORDS, DEEP_KEYWORDS, PLAN_TRIGGERS,
    DRIVER_KEYWORDS, DRIVER_CHANGEPOINT_HINTS, DRIVER_INFLUENCER_HINTS,
)
from config.prompts import SYSTEM_PROMPT


def is_trend_question(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in TREND_KEYWORDS)


def is_deep_question(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in DEEP_KEYWORDS)


def needs_planning(question: str) -> bool:
    q = question.lower()
    return len(q.split()) > 10 and any(t in q for t in PLAN_TRIGGERS)


def is_driver_question(question: str) -> bool:
    """True if the question asks what dimension field drives/explains the fact."""
    q = question.lower()
    return any(kw in q for kw in DRIVER_KEYWORDS)


def pick_driver_method(question: str) -> str:
    """Route a driver question to one of the three engine techniques."""
    q = question.lower()
    if any(h in q for h in DRIVER_CHANGEPOINT_HINTS):
        return "changepoint_drivers"
    if any(h in q for h in DRIVER_INFLUENCER_HINTS):
        return "key_influencers"
    return "period_attribution"  # default: period-over-period attribution


def _format_history(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["[Conversation history — use for follow-up context]:"]
    for i, turn in enumerate(history[-4:], 1):
        lines.append(
            f"Turn {i}: Q: \"{turn['question']}\" | Result: {turn['summary']}"
        )
    return "\n".join(lines) + "\n\n"


def warmup():
    """Load the model into RAM at startup so the first user request doesn't cold-start."""
    try:
        httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": "hi", "stream": False,
                  "options": {"num_predict": 1}},
            timeout=LLM_TIMEOUT_SQL,
        )
    except Exception:
        pass  # warmup failure is non-fatal


def _generate(prompt: str, stream: bool = False, num_predict: int | None = None) -> httpx.Response:
    payload = {
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": LLM_TEMPERATURE,
            "num_predict": num_predict or LLM_NUM_PREDICT,
        },
    }
    # Retry once on transient 500 (e.g. Ollama OOM during model load)
    for attempt in range(2):
        r = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=LLM_TIMEOUT_SQL,
        )
        if r.status_code != 500 or attempt == 1:
            return r
    return r


def generate_sql(question: str, schema: str, history: list[dict] | None = None) -> str:
    history_ctx = _format_history(history or [])
    prompt = (
        f"{history_ctx}"
        f"{schema}\n\n"
        f"Write a T-SQL query to answer:\n{question}\n\n"
        "Requirements:\n"
        "- Return ONLY the SQL in a ```sql ... ``` block.\n"
        "- Use dbo.FactInternetSales as the primary fact table.\n"
        "- Join DimDate (alias dd) on OrderDateKey = dd.DateKey.\n"
        "- For trend/drop questions: break down by at least two dimensions "
        "(e.g., CalendarYear + Category, or Territory + Quarter).\n"
        "- Include GrossProfit = SalesAmount - TotalProductCost where relevant.\n"
        "- ORDER BY the primary metric DESC.\n"
        "- Use TOP 20 if it could return many rows."
    )
    response = _generate(prompt, stream=False)
    response.raise_for_status()
    return response.json()["response"]


def fix_sql(bad_sql: str, error_msg: str, schema: str) -> str:
    # For "Invalid column name" errors, name the bad column explicitly so the
    # LLM stops regenerating it and looks at the schema instead.
    bad_col_hint = ""
    col_match = re.search(r"Invalid column name '([^']+)'", error_msg)
    if col_match:
        bad_col = col_match.group(1)
        bad_col_hint = (
            f"\nCRITICAL: Column '{bad_col}' does NOT exist in the database. "
            "Do NOT use it. Look at the schema above and pick the correct column name "
            "from the table that is actually listed there.\n"
        )

    prompt = (
        f"{schema}\n\n"
        f"This T-SQL query failed:\n\n```sql\n{bad_sql}\n```\n\n"
        f"Error: {error_msg}\n{bad_col_hint}\n"
        "Fix it and return ONLY the corrected SQL in a ```sql ... ``` block.\n"
        "Rules:\n"
        "- Use ONLY column names that appear verbatim in the schema above\n"
        "- CalendarYear/CalendarQuarter/MonthNumberOfYear are in DimDate, not FactInternetSales\n"
        "- TOP N must appear right after SELECT, never at the end\n"
        "- GrossProfit must be computed inline: SUM(fis.SalesAmount - fis.TotalProductCost)\n"
        "- DimProductCategory joins via DimProductSubcategory, never directly from DimProduct\n"
        "CORRECT column names (replace any wrong ones):\n"
        "  dp.EnglishProductName             (NOT ProductName)\n"
        "  dpc.EnglishProductCategoryName    (NOT CategoryName)\n"
        "  dps.EnglishProductSubcategoryName (NOT SubcategoryName)\n"
        "  dc.FirstName + dc.LastName        (NOT CustomerName)\n"
        "  dc.EnglishOccupation              (NOT CustomerSegmentName, NOT EnglishCustomerSegmentName, NOT CustomerType)\n"
        "  dst.SalesTerritoryCountry         (NOT CountryName)\n"
        "  dst.SalesTerritoryRegion          (NOT RegionName)"
    )
    response = _generate(prompt, stream=False)
    response.raise_for_status()
    return response.json()["response"]


def extract_sql(llm_response: str) -> str | None:
    match = re.search(r"```sql\s*(.*?)```", llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    stripped = llm_response.strip()
    if stripped.upper().startswith(("SELECT", "WITH")):
        return stripped
    return None


def plan_query(question: str, schema: str) -> list[dict]:
    """Ask the LLM to break a complex question into 2-3 focused sub-queries.
    Returns list of {title, question}. Falls back to single step on failure."""
    prompt = (
        f"Question: \"{question}\"\n\n"
        "Break this BI question into 2-3 focused sub-questions that together "
        "fully answer it. Each sub-question should be answerable with a single SQL query.\n"
        'Return ONLY a JSON array: [{"title": "...", "question": "..."}]\n'
        "Max 3 items. If the question is simple, return 1 item."
    )
    try:
        response = _generate(prompt, stream=False, num_predict=512)
        response.raise_for_status()
        text = response.json()["response"]
        match = re.search(r"\[[\s\S]*?\]", text)
        if match:
            plan = json.loads(match.group())
            if isinstance(plan, list) and all("question" in p for p in plan):
                return [p for p in plan[:3] if p.get("question")]
    except Exception:
        pass
    return [{"title": "Main Query", "question": question}]


def stream_deep_analysis(
    question: str,
    query_results: list[dict],
    schema: str,
    history: list[dict] | None = None,
):
    """Stream a structured business analysis over one or more query results."""
    history_ctx = _format_history(history or [])

    if len(query_results) == 1:
        r = query_results[0]
        prompt = (
            f"{history_ctx}"
            f"The user asked: {question}\n\n"
            f"SQL:\n```sql\n{r['sql']}\n```\n\n"
            f"Results:\n{r['results_md']}\n\n"
            "Provide a data-driven analysis. Lead with the key finding. "
            "Cite specific numbers and percentages. "
            "Use bullet points for evidence and close with Recommendations."
        )
    else:
        sections = "\n\n---\n\n".join(
            f"**{r['title']}**\n{r['results_md']}" for r in query_results
        )
        prompt = (
            f"{history_ctx}"
            f"The user asked: **{question}**\n\n"
            f"Data from {len(query_results)} analytical queries:\n\n"
            f"{sections}\n\n"
            "Provide a structured BI response:\n"
            "1. **Summary** — headline finding in 1-2 sentences with key numbers\n"
            "2. **Root Cause / Key Driver** — what primarily explains this\n"
            "3. **Evidence** — 4-6 bullets with specific figures ($, %, YoY changes)\n"
            "4. **Recommendations** — 2-3 actionable next steps\n\n"
            "Be specific. Use actual numbers from the data above. Do not invent figures."
        )

    payload = {
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": LLM_TEMPERATURE_ANALYSIS,
            "num_predict": LLM_ANALYSIS_TOKENS,
        },
    }
    with httpx.stream(
        "POST",
        f"{OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=LLM_TIMEOUT_STREAM,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line:
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue


_DRIVER_LABELS = {
    "period_attribution": "period-over-period contribution analysis",
    "key_influencers": "key-influencer (feature importance) analysis",
    "changepoint_drivers": "time-series changepoint driver analysis",
}


def stream_driver_analysis(
    question: str, driver: dict, results_md: str, history: list[dict] | None = None,
):
    """Stream a business explanation grounded in a driver-engine result.

    The numbers are already computed by the engine; the LLM only interprets them —
    it must not invent figures.
    """
    history_ctx = _format_history(history or [])
    label = _DRIVER_LABELS.get(driver["method"], driver["method"])
    prompt = (
        f"{history_ctx}"
        f"The user asked: **{question}**\n\n"
        f"A {label} was run on the data for measure **{driver['measure']}**. "
        f"These results are computed facts — do not invent or alter any numbers.\n\n"
        f"Computed summary:\n{driver['summary']}\n\n"
        f"Ranked findings table:\n{results_md}\n\n"
        "Explain this for a business audience:\n"
        "1. **Headline** — what is driving the measure, in 1-2 sentences.\n"
        "2. **Top drivers** — 3-5 bullets naming the specific dimension fields/"
        "members and their contribution (cite the exact numbers above).\n"
        "3. **Interpretation** — what this implies and 1-2 recommended next checks.\n"
        "Use only the figures provided above."
    )
    payload = {
        "model": OLLAMA_MODEL,
        "system": SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": LLM_TEMPERATURE_ANALYSIS,
            "num_predict": LLM_ANALYSIS_TOKENS,
        },
    }
    with httpx.stream(
        "POST", f"{OLLAMA_BASE_URL}/api/generate", json=payload,
        timeout=LLM_TIMEOUT_STREAM,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line:
                try:
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue


def check_ollama() -> bool:
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False
