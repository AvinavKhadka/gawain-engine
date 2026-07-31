"""server/routes.py — FastAPI router: chat, history, schema, run-sql, and training endpoints."""

import json
import os
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.database import (
    get_cached_schema, clear_schema_cache, execute_query, validate_sql,
    dataframe_to_markdown, df_to_grid_json, detect_chart, extract_kpis,
    result_brief,
)
from server.schema_retrieval import get_relevant_schema
from server.extract import load_meta, extract_to_duckdb
from server.drivers import run as run_driver
from server.failures import (record_failure, record_repair, failure_stats,
                             build_lessons, learned_column_fixes, load_repairs)
from server.answerability import check_answerable, Unanswerable
from config.observability import get_logger
import server.history as history_db
from server.llm import (
    generate_sql, fix_sql, extract_sql,
    stream_deep_analysis, stream_driver_analysis, check_ollama,
    is_deep_question, needs_planning, plan_query,
    is_driver_question, pick_driver_method,
)
from config.settings import (SESSION_MAX_TURNS, CHART_COLORS, OLLAMA_MODEL,
                             HISTORY_DB_PATH)

router = APIRouter()
log = get_logger(__name__)

# ── Schema cache ──────────────────────────────────────────────────────────────
def get_schema() -> str:
    """Schema context, cached in server.database so the SQL sanitiser shares it."""
    return get_cached_schema()


# ── In-memory session store: session_id -> last N conversation turns ──────────
_sessions: dict[str, list[dict]] = {}


def _get_history(session_id: str) -> list[dict]:
    return _sessions.get(session_id, [])


def _save_turn(session_id: str, question: str, sql: str, summary: str):
    turns = _sessions.get(session_id, [])
    turns.append({"question": question, "sql": sql, "summary": summary})
    _sessions[session_id] = turns[-SESSION_MAX_TURNS:]


# ── Request models ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None


class RunSqlRequest(BaseModel):
    sql: str
    session_id: str | None = None


class FavoriteRequest(BaseModel):
    id: int


# ── Driver-analysis helpers ───────────────────────────────────────────────────

# Map common business words to extracted measure names for measure guessing.
_MEASURE_WORDS = {
    "revenue": "sales", "sales": "sales", "income": "sales",
    "cost": "cost", "cogs": "cost",
    "quantity": "quantity", "units": "quantity", "volume": "quantity",
    "orders": "quantity",
}


def _guess_measure(question: str, measures: list[str]) -> str | None:
    """Pick which extracted measure the question is about, or None for default."""
    q = question.lower()
    # direct name match first
    for m in measures:
        if m.lower() in q:
            return m
    # word-based match (revenue -> SalesAmount, etc.)
    for word, frag in _MEASURE_WORDS.items():
        if word in q:
            for m in measures:
                if frag in m.lower():
                    return m
    return None


def _driver_chart(result: dict) -> dict | None:
    """Build a Chart.js bar config from a driver result's ranked findings."""
    frame = result.get("frame")
    if frame is None or frame.empty:
        return None
    method = result["method"]
    if method == "key_influencers":
        labels = [str(r["Dimension Field"]) for _, r in frame.iterrows()]
        data = [round(float(r["Influence %"]), 2) for _, r in frame.iterrows()]
        title = f"What explains {result['measure']} (influence %)"
        ylabel = "Influence %"
    else:
        labels = [f"{r['dimension']}={r['member']}" for _, r in frame.iterrows()]
        change_col = "Change" if "Change" in frame.columns else "Shift/Period"
        data = [round(float(r[change_col]), 2) for _, r in frame.iterrows()]
        title = f"Top drivers of {result['measure']} change"
        ylabel = change_col
    return {
        "type": "bar", "title": title, "labels": labels[:12],
        "datasets": [{"label": ylabel, "data": data[:12], "color": CHART_COLORS[0]}],
    }


# ── Health & schema ───────────────────────────────────────────────────────────

#: Last database error string reported by /api/health. Used to log a repeated
#: failure once instead of on every poll — monitors hit this endpoint on a
#: fixed interval, and an unreachable DB would otherwise flood the log with
#: identical lines. A changed error, or a recovery, always logs.
_last_db_error: str | None = None


@router.get("/api/health")
def health():
    global _last_db_error

    ollama_ok = check_ollama()
    db_ok = False
    db_error = None
    try:
        db_ok = bool(get_schema())
    except Exception as e:
        # The reason used to be discarded here, which is why a false flag was
        # undiagnosable. Log it — but only when it changes, so a polling
        # monitor cannot spam the log.
        db_error = f"{type(e).__name__}: {e}"
        if db_error != _last_db_error:
            log.warning("database health check failed", extra={"error": db_error})
            log.debug("database health check traceback", exc_info=True)
        else:
            log.debug("database health check still failing", extra={"error": db_error})

    if db_ok and _last_db_error is not None:
        log.info("database connection recovered")
    _last_db_error = db_error

    payload = {"ollama": ollama_ok, "database": db_ok}
    if db_error:
        payload["database_error"] = db_error
    return payload


@router.get("/api/schema")
def schema_endpoint():
    try:
        return {"schema": get_schema()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/schema/refresh")
def refresh_schema():
    clear_schema_cache()      # also drops the derived column index
    try:
        schema = get_schema()
        return {"status": "refreshed", "tables": schema.count("Table:")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/failures")
def failures_summary():
    """What is failing, what was learned from it, and what is auto-applied."""
    stats = failure_stats()
    stats["active_lessons"] = build_lessons()
    stats["learned_column_fixes"] = learned_column_fixes()
    stats["repairs_recorded"] = len(load_repairs())
    return stats


# ── Driver-analysis endpoints ─────────────────────────────────────────────────

@router.get("/api/drivers/status")
def drivers_status():
    """Return metadata of the current analytics extract (or null if not built)."""
    return {"meta": load_meta()}


@router.post("/api/drivers/rebuild")
def drivers_rebuild(limit: int | None = None):
    """Re-extract the star schema from SQL Server into the local DuckDB store."""
    try:
        meta = extract_to_duckdb(limit=limit, verbose=False)
        return {"status": "rebuilt", "meta": meta}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DriverRequest(BaseModel):
    method: str | None = None          # period_attribution | key_influencers | changepoint_drivers
    measure: str | None = None
    question: str | None = None        # used to auto-pick method + measure


@router.post("/api/drivers/run")
def drivers_run(req: DriverRequest):
    """Run a single driver analysis and return the structured result (no LLM)."""

    meta = load_meta()
    if meta is None:
        raise HTTPException(status_code=409,
                            detail="Analytics store not built. POST /api/drivers/rebuild first.")

    method = req.method or (pick_driver_method(req.question) if req.question else "period_attribution")
    measure = req.measure or (_guess_measure(req.question, meta["measures"]) if req.question else None)
    try:
        result = run_driver(method, measure=measure)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # frames aren't JSON-serializable; drop them, keep structured findings
    return {k: v for k, v in result.items() if not k.endswith("frame")}


# ── History endpoints ─────────────────────────────────────────────────────────

@router.get("/api/history")
def get_history(limit: int = 100):
    return {"items": history_db.get_all(limit)}


@router.post("/api/history/favorite")
def toggle_favorite(req: FavoriteRequest):
    is_fav = history_db.toggle_favorite(req.id)
    return {"id": req.id, "favorited": is_fav}


@router.delete("/api/history/{item_id}")
def delete_history(item_id: int):
    history_db.delete(item_id)
    return {"deleted": item_id}


# ── Training data ─────────────────────────────────────────────────────────────

# Lives in storage/ alongside failures.jsonl and repairs.jsonl — the only
# directory bind-mounted in docker-compose, so records survive a container
# recreate. (It previously wrote to train/, which was not mounted and so was
# silently discarded on every rebuild.)
_TRAIN_DATA = os.path.join(
    os.path.dirname(HISTORY_DB_PATH), "training_pairs.jsonl"
)


class TrainSaveRequest(BaseModel):
    question: str
    sql: str


@router.post("/api/train/save")
def save_training_pair(req: TrainSaveRequest):
    """Validate and append a corrected question-SQL pair to the training examples."""
    err = validate_sql(req.sql)
    if err:
        raise HTTPException(status_code=400, detail=f"SQL invalid: {err}")

    os.makedirs(os.path.dirname(_TRAIN_DATA), exist_ok=True)

    # Deduplicate by question
    existing: set[str] = set()
    if os.path.exists(_TRAIN_DATA):
        with open(_TRAIN_DATA, encoding="utf-8") as f:
            for line in f:
                try:
                    existing.add(json.loads(line)["question"].strip().lower())
                except Exception:
                    pass

    q = req.question.strip()
    if q.lower() in existing:
        return {"saved": False, "reason": "duplicate"}

    with open(_TRAIN_DATA, "a", encoding="utf-8") as f:
        f.write(json.dumps({"question": q, "sql": req.sql.strip()}) + "\n")

    return {"saved": True}


# ── Run arbitrary SQL (user-edited queries) ───────────────────────────────────

@router.post("/api/chat/run-sql")
def run_sql(req: RunSqlRequest):
    """Execute a user-supplied SQL query and stream grid + chart results."""

    def event_stream():
        def emit(type_: str, content=None) -> str:
            return json.dumps({"type": type_, "content": content or ""}) + "\n"

        yield emit("step", "Validating query...")
        validation_error = validate_sql(req.sql)
        if validation_error:
            yield emit("error", f"SQL validation failed: {validation_error}")
            yield emit("done")
            return

        yield emit("step", "Running custom query...")
        df, error = execute_query(req.sql)
        if error:
            yield emit("error", f"SQL error: {error}")
            yield emit("done")
            return

        kpis = extract_kpis(df)
        if kpis:
            yield emit("kpi", kpis)

        yield emit("grid", df_to_grid_json(df))
        chart = detect_chart(df, "Custom Query")
        if chart:
            yield emit("chart", chart)

        sid = req.session_id or ""
        history = _get_history(sid)
        schema = get_schema()
        results_md = dataframe_to_markdown(df)
        yield emit("step", "Analysing results...")
        try:
            all_results = [{"title": "Custom Query", "sql": req.sql, "results_md": results_md}]
            for token in stream_deep_analysis("Analyse this query result.", all_results, schema, history):
                yield emit("token", token)
        except Exception as e:
            yield emit("error", f"Analysis error: {e}")

        yield emit("done")

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


# ── Main chat endpoint ────────────────────────────────────────────────────────

@router.post("/api/chat")
def chat(req: ChatRequest):
    """
    Streams newline-delimited JSON events:
      session -> session_id string (first event)
      step    -> progress message
      sql     -> SQL string
      kpi     -> [{label, value}, ...]  headline KPI cards
      grid    -> {columns, rows, total, _title?}  AG Grid data
      chart   -> {type, title, labels, datasets}  Chart.js config
      token   -> streamed answer text token
      error   -> error string
      done    -> end of stream
    """

    def event_stream():
        def emit(type_: str, content=None) -> str:
            return json.dumps({"type": type_, "content": content or ""}) + "\n"

        # ── Session ────────────────────────────────────────────────────────
        sid = req.session_id or str(uuid.uuid4())
        history = _get_history(sid)
        yield emit("session", sid)

        # ── Driver-analysis fast path ──────────────────────────────────────
        # "Which dim field drives the fact?" is an attribution problem, not
        # text->SQL. Route it to the DuckDB driver engine when the extract
        # exists; otherwise fall through to the normal SQL flow.
        if is_driver_question(req.question):
            meta = load_meta()
            if meta is None:
                yield emit("step", "Driver store not built — answering with SQL "
                                   "instead. (Build it: python -m server.extract)")
            else:
                method = pick_driver_method(req.question)
                measure = _guess_measure(req.question, meta["measures"])
                yield emit("step", f"Running {method.replace('_', ' ')}...")
                try:
                    result = run_driver(method, measure=measure)
                except Exception as e:
                    yield emit("error", f"Driver analysis failed: {e}")
                    yield emit("done")
                    return

                title = result["method"].replace("_", " ").title()
                yield emit("grid", {**df_to_grid_json(result["frame"]), "_title": title})
                chart = _driver_chart(result)
                if chart:
                    yield emit("chart", chart)
                # changepoint analysis also ships the underlying trend line
                if "trend_frame" in result:
                    trend_chart = detect_chart(result["trend_frame"],
                                               f"{result['measure']} trend")
                    if trend_chart:
                        yield emit("chart", trend_chart)

                results_md = dataframe_to_markdown(result["frame"])
                yield emit("step", "Explaining drivers...")
                try:
                    for token in stream_driver_analysis(
                        req.question, result, results_md, history):
                        yield emit("token", token)
                except Exception as e:
                    yield emit("error", f"Analysis error: {e}")

                _save_turn(sid, req.question, f"[driver:{method}]", result["summary"][:300])
                try:
                    history_db.save(sid, req.question, f"[driver:{method}]",
                                    len(result["frame"]))
                except Exception:
                    pass
                yield emit("done")
                return

        # ── Schema ────────────────────────────────────────────────────────
        try:
            full_schema = get_schema()
        except Exception as e:
            yield emit("error", f"Cannot connect to database: {e}")
            yield emit("done")
            return

        # Refuse questions the schema provably cannot answer, rather than
        # letting the LLM produce confident SQL for a different question.
        try:
            check_answerable(req.question, full_schema)
        except Unanswerable as e:
            yield emit("error", e.message)
            yield emit("done")
            return

        schema = get_relevant_schema(full_schema, req.question)

        # ── Generate SQL ───────────────────────────────────────────────────
        yield emit("step", "Generating SQL query...")
        try:
            sql = extract_sql(generate_sql(req.question, schema, history))
        except Exception as e:
            yield emit("error", f"LLM error: {e}")
            yield emit("done")
            return

        if not sql:
            yield emit("error", "Could not extract SQL from the model response.")
            yield emit("done")
            return

        yield emit("sql", sql)

        # ── Pre-execution validation (up to 2 fix attempts) ───────────────
        yield emit("step", "Validating query...")
        val_error = validate_sql(sql)
        if val_error:
            broken_sql, first_error = sql, val_error
            for attempt in range(2):
                # Second attempt escalates to full schema so LLM sees all columns
                fix_schema = full_schema if attempt == 1 else schema
                yield emit("step", f"Fixing SQL (attempt {attempt + 1})...")
                try:
                    fixed_sql = extract_sql(fix_sql(sql, val_error, fix_schema))
                except Exception:
                    fixed_sql = None
                if fixed_sql and fixed_sql != sql:
                    sql = fixed_sql
                    yield emit("sql", sql)
                    val_error = validate_sql(sql)
                if not val_error:
                    # Repair worked — capture what actually fixed it.
                    record_repair(req.question, broken_sql, sql, first_error,
                                  stage="validation", attempts=attempt + 1,
                                  model=OLLAMA_MODEL)
                    break
            if val_error:
                record_failure(req.question, sql, val_error,
                               stage="validation", attempts=2,
                               model=OLLAMA_MODEL)
                yield emit("error", f"SQL validation error: {val_error}\n\n```sql\n{sql}\n```")
                yield emit("done")
                return

        # ── Execute (up to 2 fix attempts on runtime error) ───────────────
        yield emit("step", "Running query against database...")
        df, error = execute_query(sql)

        if error:
            broken_sql, first_error = sql, error
            for attempt in range(2):
                fix_schema = full_schema if attempt == 1 else schema
                yield emit("step", f"Fixing SQL error (attempt {attempt + 1})...")
                try:
                    fixed_sql = extract_sql(fix_sql(sql, error, fix_schema))
                except Exception:
                    fixed_sql = None
                if fixed_sql and fixed_sql != sql:
                    yield emit("sql", fixed_sql)
                    sql = fixed_sql
                    df, error = execute_query(sql)
                if not error:
                    record_repair(req.question, broken_sql, sql, first_error,
                                  stage="execution", attempts=attempt + 1,
                                  model=OLLAMA_MODEL)
                    break

        if error:
            record_failure(req.question, sql, error,
                           stage="execution", attempts=2,
                           model=OLLAMA_MODEL)
            yield emit("error", f"SQL error: {error}\n\n```sql\n{sql}\n```")
            yield emit("done")
            return

        # ── KPI + grid + chart ─────────────────────────────────────────────
        kpis = extract_kpis(df)
        if kpis:
            yield emit("kpi", kpis)

        yield emit("grid", df_to_grid_json(df))
        chart = detect_chart(df, title="Query Results")
        if chart:
            yield emit("chart", chart)

        # ── Deep analysis: dynamic multi-step planning ─────────────────────
        all_results = [{
            "title": "Main Query",
            "sql": sql,
            "results_md": dataframe_to_markdown(df),
        }]

        if is_deep_question(req.question):
            if needs_planning(req.question):
                yield emit("step", "Building multi-step analysis plan...")
                plan = plan_query(req.question, schema)
                extra_steps = [p for p in plan if p["question"].lower() != req.question.lower()]
                if extra_steps:
                    yield emit("step", f"Running {len(extra_steps)} supplemental queries...")
                    for step in extra_steps:
                        try:
                            step_sql = extract_sql(generate_sql(step["question"], schema, history))
                        except Exception:
                            continue
                        if not step_sql:
                            continue
                        step_df, step_err = execute_query(step_sql)
                        if step_err or step_df is None or step_df.empty:
                            continue
                        yield emit("grid", {**df_to_grid_json(step_df), "_title": step["title"]})
                        step_chart = detect_chart(step_df, title=step["title"])
                        if step_chart:
                            yield emit("chart", step_chart)
                        all_results.append({
                            "title": step["title"],
                            "sql": step_sql,
                            "results_md": dataframe_to_markdown(step_df),
                        })

        # ── Streaming LLM analysis ─────────────────────────────────────────
        yield emit("step", "Analysing findings...")
        try:
            for token in stream_deep_analysis(req.question, all_results, schema, history):
                yield emit("token", token)
        except Exception as e:
            yield emit("error", f"Analysis error: {e}")

        # ── Persist turn to session + history DB ───────────────────────────
        summary = result_brief(df)
        _save_turn(sid, req.question, sql, summary)
        try:
            history_db.save(sid, req.question, sql, len(df))
        except Exception:
            pass

        yield emit("done")

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
