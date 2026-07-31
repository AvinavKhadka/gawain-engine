"""
server/failures.py — Failure capture and prompt-level reinforcement.

Two jobs:

1. **Record** every query that could not be made to run, with enough context to
   be actionable: the question, the final SQL, the error, which stage it died
   at, how many repair attempts were burned, and what the deterministic
   sanitiser had already fixed.

2. **Reinforce** — turn those records into corrective guidance injected into
   the next prompt, so the model stops repeating a mistake *without* retraining.

Why prompt-level reinforcement rather than fine-tuning
------------------------------------------------------
Fine-tuning (train/finetune.sh) is the heavyweight option: it needs a GPU, a
few hundred examples, and produces a model you then have to maintain. It is
worth doing eventually, and the JSONL written here is deliberately in the same
shape as train/data/examples.jsonl so it can feed that pipeline later.

But the fast, free win is few-shot: when the same error signature recurs, add a
short "previously failed / do this instead" note to the prompt. That takes
effect on the very next question, costs no training, and is trivially
inspectable — you can read exactly what the model is being told.

Storage
-------
Records live under storage/, NOT train/, because only storage/ is bind-mounted
in docker-compose.yml. Writing to train/ inside the container would silently
lose every record on the next `docker compose up -d --force-recreate`.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from config.observability import get_logger
from config.settings import HISTORY_DB_PATH

log = get_logger(__name__)

__all__ = [
    "record_failure",
    "record_repair",
    "load_failures",
    "load_repairs",
    "failure_stats",
    "build_lessons",
    "learned_column_fixes",
    "apply_learned_fixes",
    "clear_lessons_cache",
    "FAILURES_PATH",
    "REPAIRS_PATH",
]

#: Alongside history.db in storage/ — the one directory that survives a
#: container recreate.
FAILURES_PATH = os.path.join(os.path.dirname(HISTORY_DB_PATH), "failures.jsonl")

#: Successful repairs: broken SQL -> SQL that actually ran. This is the
#: positive signal. A failure record only says "X is wrong"; a repair record
#: says "X is wrong AND Y is right", which is both stronger guidance for the
#: prompt and the exact shape fine-tuning wants.
REPAIRS_PATH = os.path.join(os.path.dirname(HISTORY_DB_PATH), "repairs.jsonl")

#: A learned column mapping is applied automatically once it has been observed
#: this many times with no contradicting evidence. Two independent confirmations
#: is enough to distinguish a real schema fact from a one-off coincidence.
AUTOFIX_THRESHOLD = 2

#: Cap the file so a pathological loop cannot fill the disk.
MAX_RECORDS = 2000

#: How many distinct lessons to inject into a prompt at most. Each costs
#: tokens, and a wall of corrections drowns out the schema.
MAX_LESSONS = 6

#: A lesson is only injected once an error signature has been seen this often.
#: One-off failures are noise; repeats are patterns.
LESSON_THRESHOLD = 2

_lock = threading.Lock()
_lessons_cache: tuple[int, list[str]] | None = None


# ──────────────────────────────────────────────────────────────────────────────
# Error signatures
# ──────────────────────────────────────────────────────────────────────────────

def error_signature(error: str) -> str:
    """Collapse a SQL error into a stable, groupable key.

    'Invalid column name OrderNumber' and 'Invalid column name Foo' are the
    same *class* of failure but different instances — we keep the identifier so
    a lesson can name it, while stripping volatile noise (line numbers, ODBC
    state codes) so repeats actually group together.

    >>> error_signature("[ODBC Driver 17] Invalid column name 'Foo'. (207)")
    'invalid_column:Foo'
    >>> error_signature("Incorrect syntax near 'LIMIT'. (102)")
    'syntax_near:LIMIT'
    """
    if not error:
        return "unknown"

    e = str(error)

    m = re.search(r"Invalid column name '([^']+)'", e, re.IGNORECASE)
    if m:
        return f"invalid_column:{m.group(1)}"

    m = re.search(r"Invalid object name '([^']+)'", e, re.IGNORECASE)
    if m:
        return f"invalid_object:{m.group(1)}"

    m = re.search(r"Incorrect syntax near '([^']+)'", e, re.IGNORECASE)
    if m:
        return f"syntax_near:{m.group(1)}"

    m = re.search(r"column '([^']+)' is invalid in the select list", e, re.IGNORECASE)
    if m:
        return f"not_grouped:{m.group(1)}"

    if "ambiguous column name" in e.lower():
        m = re.search(r"ambiguous column name '([^']+)'", e, re.IGNORECASE)
        return f"ambiguous_column:{m.group(1) if m else '?'}"

    m = re.search(r"multi-part identifier \"([^\"]+)\" could not be bound",
                  e, re.IGNORECASE)
    if m:
        return f"unbound_identifier:{m.group(1)}"

    if "timeout expired" in e.lower():
        return "connection_timeout"
    if "login failed" in e.lower():
        return "login_failed"

    # Fallback. Strip only the volatile parts — ODBC state codes, bracketed
    # driver banners and numeric error codes — but KEEP the message text.
    # (An earlier version also stripped everything inside single quotes, which
    # deleted the entire message, since ODBC wraps the whole thing in quotes.)
    cleaned = re.sub(r"\[[^\]]+\]", " ", e)          # [42000] [Microsoft] ...
    cleaned = re.sub(r"\(\d+\)", " ", cleaned)        # (4104)
    cleaned = re.sub(r"\('\w+',\s*", " ", cleaned)    # ('42000',
    cleaned = re.sub(r"[\"']", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,()")
    return f"other:{cleaned[:80]}" if cleaned else "unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Recording
# ──────────────────────────────────────────────────────────────────────────────

def record_failure(
    question: str,
    sql: str | None,
    error: str,
    *,
    stage: str,
    attempts: int = 0,
    repairs: list[str] | None = None,
    model: str | None = None,
) -> bool:
    """Append a failure record. Returns True if written.

    Never raises: a logging failure must not break the user's request. The
    caller is already on an error path — making that path fail harder helps
    nobody.
    """
    try:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": (question or "").strip(),
            "sql": (sql or "").strip(),
            "error": str(error)[:600],
            "signature": error_signature(error),
            "stage": stage,               # "validation" | "execution" | "generation"
            "attempts": attempts,
            "repairs": repairs or [],
            "model": model,
        }

        with _lock:
            os.makedirs(os.path.dirname(FAILURES_PATH), exist_ok=True)
            with open(FAILURES_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            _trim_if_needed()

        clear_lessons_cache()
        log.info(
            "failure recorded",
            extra={"stage": stage, "signature": record["signature"],
                   "attempts": attempts},
        )
        return True
    except Exception:
        log.debug("could not record failure", exc_info=True)
        return False


def _column_refs(sql: str) -> set[tuple[str, str]]:
    """Every (alias, column) pair in a statement."""
    return {
        (m.group(1), m.group(2))
        for m in re.finditer(r"\b([A-Za-z_]\w*)\.(\w+)\b", sql or "")
        if m.group(1).lower() not in ("dbo", "sys", "information_schema")
    }


def _expression_for(alias: str, column: str, good_sql: str) -> str | None:
    """Find what an invalid `alias.column` was replaced with in the fixed SQL.

    The single most common real repair is not a rename but a substitution with
    a derived expression — `fis.GrossProfit` becoming
    `fis.SalesAmount - fis.TotalProductCost`. Treating that as "no mapping"
    throws away the most valuable lesson available, so we recover it by
    aligning the aggregate wrappers around the reference.
    """
    # Look for AGG(<expr>) in the repaired SQL occupying the same position as
    # AGG(alias.column) in the broken one, matched via a shared output alias.
    pat = re.compile(
        r"(SUM|AVG|MIN|MAX|COUNT)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s+AS\s+(\w+)",
        re.IGNORECASE,
    )
    for _agg, expr, _out in pat.findall(good_sql):
        expr = expr.strip()
        if not expr or f"{alias}.{column}".lower() in expr.lower():
            continue
        refs = {c for _a, c in _column_refs(expr)}
        # Must be built only from real column references, and be a genuine
        # expression rather than a bare rename (which the caller handles).
        if refs and re.search(r"[-+*/]", expr):
            return expr
    return None


def diff_column_fix(bad_sql: str, good_sql: str) -> tuple[str, str] | None:
    """Infer what turned failing SQL into working SQL.

    Returns (wrong_column, replacement) where replacement is either another
    column name or a derived expression.

    Attribution requires EXACTLY ONE removed column reference. If the repair
    rewrote several things at once we cannot say which change mattered, so we
    learn nothing rather than learn something wrong — a bad auto-fix is far
    more damaging than a missing one, because it would corrupt queries that
    were previously correct.
    """
    before, after = _column_refs(bad_sql), _column_refs(good_sql)
    removed, added = before - after, after - before
    if len(removed) != 1:
        return None

    alias_b, col_b = removed.pop()

    # Case 1: straight rename — one column swapped for another.
    if len(added) == 1:
        alias_a, col_a = added.pop()
        if alias_b != alias_a or col_b.lower() == col_a.lower():
            return None
        return col_b, col_a

    # Case 2: replaced by a derived expression. `added` may be empty (the
    # expression reuses columns already present) or contain the columns the
    # expression is built from — either way there is exactly one thing removed,
    # so the substitution is still unambiguous.
    expr = _expression_for(alias_b, col_b, good_sql)
    if expr:
        return col_b, expr
    return None


def record_repair(
    question: str,
    bad_sql: str,
    good_sql: str,
    error: str,
    *,
    stage: str,
    attempts: int = 1,
    model: str | None = None,
) -> bool:
    """Record SQL that was broken, then repaired, then actually ran.

    This is the positive counterpart to record_failure(). Never raises.
    """
    try:
        mapping = diff_column_fix(bad_sql, good_sql)
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": (question or "").strip(),
            "bad_sql": (bad_sql or "").strip(),
            "good_sql": (good_sql or "").strip(),
            "error": str(error)[:600],
            "signature": error_signature(error),
            "stage": stage,
            "attempts": attempts,
            "model": model,
            # (wrong_column, right_column) when unambiguously attributable
            "column_fix": list(mapping) if mapping else None,
        }

        with _lock:
            os.makedirs(os.path.dirname(REPAIRS_PATH), exist_ok=True)
            with open(REPAIRS_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            _trim_file(REPAIRS_PATH)

        clear_lessons_cache()
        log.info("repair recorded",
                 extra={"stage": stage, "signature": record["signature"],
                        "column_fix": record["column_fix"]})
        return True
    except Exception:
        log.debug("could not record repair", exc_info=True)
        return False


def load_repairs(limit: int | None = None) -> list[dict]:
    """Read repair records, newest last."""
    return _load_jsonl(REPAIRS_PATH, limit)


def learned_column_fixes(threshold: int = AUTOFIX_THRESHOLD) -> dict[str, str]:
    """Column renames confirmed often enough to apply automatically.

    A mapping is only trusted when it has been observed `threshold` times AND
    the wrong column has never been repaired to a *different* right column.
    Conflicting evidence means the schema is ambiguous, so we defer to the LLM.
    """
    observed: dict[str, Counter] = {}
    for rec in load_repairs():
        fix = rec.get("column_fix")
        if not fix or len(fix) != 2:
            continue
        wrong, right = fix
        observed.setdefault(wrong.lower(), Counter())[right] += 1

    trusted: dict[str, str] = {}
    for wrong, targets in observed.items():
        if len(targets) > 1:
            continue                    # contradicting evidence — do not guess
        right, count = targets.most_common(1)[0]
        if count >= threshold:
            trusted[wrong] = right
    return trusted


def apply_learned_fixes(sql: str) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite column references using mappings learned from past repairs.

    This is what makes the system self-improving rather than rule-based: the
    mappings are discovered from what actually worked against this database,
    not hand-written by a developer.
    """
    if not sql:
        return sql, []

    fixes = learned_column_fixes()
    if not fixes:
        return sql, []

    applied: list[tuple[str, str]] = []

    def repl(m: re.Match[str]) -> str:
        alias, col = m.group(1), m.group(2)
        if alias.lower() in ("dbo", "sys", "information_schema"):
            return m.group(0)
        right = fixes.get(col.lower())
        if not right or right.lower() == col.lower():
            return m.group(0)

        if "." in right or re.search(r"[-+*/\s]", right):
            # A derived expression, e.g. "fis.SalesAmount - fis.TotalProductCost".
            # Parenthesise so it cannot change the precedence of surrounding
            # arithmetic: SUM(x) / y must not silently become SUM(a - b) / y
            # with different grouping.
            replacement = f"({right})"
        else:
            replacement = f"{alias}.{right}"

        applied.append((f"{alias}.{col}", replacement))
        return replacement

    out = re.sub(r"\b([A-Za-z_]\w*)\.(\w+)\b", repl, sql)
    if applied:
        log.info("applied learned column fixes", extra={"fixes": applied})
    return out, applied


def _load_jsonl(path: str, limit: int | None = None) -> list[dict]:
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:] if limit else out


def _trim_file(path: str) -> None:
    """Keep only the most recent MAX_RECORDS lines. Caller holds the lock."""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= MAX_RECORDS:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines[-MAX_RECORDS:])
    except Exception:
        log.debug("log trim skipped", exc_info=True)


def _trim_if_needed() -> None:
    """Keep only the most recent MAX_RECORDS lines. Caller holds the lock."""
    try:
        with open(FAILURES_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= MAX_RECORDS:
            return
        with open(FAILURES_PATH, "w", encoding="utf-8") as fh:
            fh.writelines(lines[-MAX_RECORDS:])
    except Exception:
        log.debug("failure-log trim skipped", exc_info=True)


def load_failures(limit: int | None = None) -> list[dict]:
    """Read failure records, newest last. Malformed lines are skipped."""
    if not os.path.exists(FAILURES_PATH):
        return []
    out: list[dict] = []
    try:
        with open(FAILURES_PATH, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out[-limit:] if limit else out


def failure_stats() -> dict[str, Any]:
    """Aggregate view — what is failing, how often, and where."""
    records = load_failures()
    sigs = Counter(r.get("signature", "unknown") for r in records)
    stages = Counter(r.get("stage", "unknown") for r in records)
    return {
        "total": len(records),
        "by_signature": sigs.most_common(20),
        "by_stage": dict(stages),
        "path": FAILURES_PATH,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Reinforcement — turn recorded failures into prompt guidance
# ──────────────────────────────────────────────────────────────────────────────

def _lesson_for(signature: str, count: int,
                corrections: dict[str, str] | None = None) -> str | None:
    """Human-readable corrective instruction for a recurring signature.

    Where a repair taught us the *right* column, the lesson names it. A
    positive instruction ("use Y") steers far better than a negative one
    ("don't use X"), which leaves the model to guess again.
    """
    kind, _, detail = signature.partition(":")
    corrections = corrections or {}

    if kind == "invalid_column":
        right = corrections.get(detail.lower())
        if right:
            return (f"`{detail}` DOES NOT EXIST — the correct column is "
                    f"`{right}` (learned from {count} previous failures).")
        return (f"The column `{detail}` DOES NOT EXIST (seen {count}x). "
                f"Check the schema above for the real name before using it.")
    if kind == "invalid_object":
        return (f"The table `{detail}` DOES NOT EXIST (seen {count}x). "
                f"Only use tables listed in the schema above.")
    if kind == "syntax_near":
        return (f"Syntax error near `{detail}` (seen {count}x). "
                f"This is Microsoft T-SQL, not MySQL or Postgres.")
    if kind == "not_grouped":
        return (f"`{detail}` was selected without being grouped or aggregated "
                f"(seen {count}x). Every non-aggregated SELECT column must "
                f"appear in GROUP BY.")
    if kind == "ambiguous_column":
        return (f"`{detail}` is ambiguous across joined tables (seen {count}x). "
                f"Always qualify columns with their table alias.")
    return None


def build_lessons(max_lessons: int = MAX_LESSONS,
                  threshold: int = LESSON_THRESHOLD) -> list[str]:
    """Corrective instructions for mistakes that have recurred.

    Cached against the failure-file size so this is effectively free per
    request; the cache is invalidated whenever a new failure is recorded.
    """
    global _lessons_cache

    try:
        size = os.path.getsize(FAILURES_PATH) if os.path.exists(FAILURES_PATH) else 0
    except OSError:
        size = 0

    if _lessons_cache is not None and _lessons_cache[0] == size:
        return _lessons_cache[1]

    counts = Counter(r.get("signature", "") for r in load_failures())
    corrections = {w: r for w, r in learned_column_fixes(threshold=1).items()}

    lessons: list[str] = []
    for sig, count in counts.most_common():
        if count < threshold:
            break                      # most_common is descending
        lesson = _lesson_for(sig, count, corrections)
        if lesson:
            lessons.append(lesson)
        if len(lessons) >= max_lessons:
            break

    _lessons_cache = (size, lessons)
    return lessons


def lessons_block() -> str:
    """Prompt fragment to inject, or '' when there is nothing to teach."""
    lessons = build_lessons()
    if not lessons:
        return ""
    body = "\n".join(f"  - {t}" for t in lessons)
    return (
        "\nLEARNED FROM PREVIOUS FAILURES ON THIS DATABASE — do not repeat:\n"
        f"{body}\n"
    )


def clear_lessons_cache() -> None:
    global _lessons_cache
    _lessons_cache = None
