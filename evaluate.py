#!/usr/bin/env python3
"""
evaluate.py — Portability and correctness harness.

Runs a fixed question set against whatever database the engine is currently
pointed at, and records what happened to each. The output is designed to be
handed to a second LLM for judgement, because the failure mode that matters
most here is invisible to assertions:

    SQL that runs, returns plausible numbers, and answers a DIFFERENT question.

An earlier session produced exactly that — `JOIN DimProductCategory dpc ON
fis.ProductKey = dpc.ProductCategoryKey` is valid T-SQL joining a product key
to a category key. It executes, returns rows, and means nothing. No syntax
check, no row-count check and no exception will ever catch it.

Usage
-----
    python evaluate.py                      # run the default question set
    python evaluate.py --out results.json   # save for LLM review
    python evaluate.py --questions my.txt   # one question per line
    python evaluate.py --url http://localhost:8000

Then paste results.json into another model with the prompt printed at the end,
or use --print-review-prompt to get it on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

try:
    import httpx
except ImportError:                                  # pragma: no cover
    sys.exit("httpx is required:  pip install httpx")


#: Deliberately spans capability classes rather than being a list of things
#: known to work. Each targets a different mechanism, and several are expected
#: to fail on some schemas — that is the point.
DEFAULT_QUESTIONS: list[tuple[str, str]] = [
    ("Show total sales by year",
     "basic aggregation + date discovery"),
    ("Show the top 10 products by revenue",
     "join-path discovery, single hop"),
    ("Show total sales by product category",
     "multi-hop join through a hierarchy"),
    ("Show sales by customer city",
     "join across a different dimension branch"),
    ("Show monthly revenue for 2013 ordered chronologically",
     "date grain + ordering + chart shape"),
    ("Show total sales for each product category",
     "few categories -> should produce a doughnut"),
    ("What are sales by payment method?",
     "answerability: schema has no payment data"),
    ("Break down sales by customer age group",
     "answerability: derivable only if a birth date exists"),
    ("Show me net revenue after returns by territory",
     "answerability: schema has no returns data"),
    ("Show sales for the last 30 days",
     "relative date against data that ends in the past"),
    ("What are the key drivers of sales?",
     "driver engine — star-schema dependent"),
    ("Show the top 3 products within each category by revenue",
     "window function / structural complexity"),
]


def ask(url: str, question: str, timeout: float) -> dict[str, Any]:
    """Send one question and collapse the NDJSON stream into a summary."""
    out: dict[str, Any] = {
        "question": question,
        "sql": None,
        "error": None,
        "steps": [],
        "chart_type": None,
        "row_count": None,
        "kpis": [],
        "refused": False,
        "elapsed_s": None,
        "repair_attempts": 0,
    }
    started = time.time()
    try:
        with httpx.stream("POST", f"{url}/api/chat",
                          json={"question": question}, timeout=timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind, data = evt.get("type"), evt.get("data")
                if kind == "sql":
                    out["sql"] = data
                elif kind == "step":
                    out["steps"].append(data)
                    if isinstance(data, str) and "Fixing SQL" in data:
                        out["repair_attempts"] += 1
                elif kind == "error":
                    out["error"] = data
                elif kind == "chart" and isinstance(data, dict):
                    out["chart_type"] = data.get("type")
                elif kind == "grid" and isinstance(data, dict):
                    out["row_count"] = data.get("total")
                elif kind == "kpi":
                    out["kpis"] = data
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"

    out["elapsed_s"] = round(time.time() - started, 1)

    # A refusal is a deliberate, correct outcome — distinguish it from a crash.
    if out["error"] and not out["sql"]:
        msg = str(out["error"]).lower()
        if "no " in msg and ("data" in msg or "cannot be answered" in msg):
            out["refused"] = True
    return out


REVIEW_PROMPT = """\
You are reviewing a natural-language-to-SQL engine for CORRECTNESS, not style.

For each item you are given: the question asked, the schema the engine had
available, and the SQL it produced.

Judge each on four points and answer with a short verdict per point:

1. ANSWERS THE QUESTION — does this SQL return what was asked for? Flag any
   query that returns a different breakdown, extra dimensions the question did
   not request, or a substituted metric.

2. VALID COLUMNS — does every column exist in the provided schema?

3. SEMANTICALLY MEANINGFUL JOINS — is each join between keys that genuinely
   relate? Specifically flag joins that are syntactically valid but pair
   unrelated keys, e.g. joining a product key to a category key. These execute
   and return rows, so they are invisible without review.

4. WOULD IT MISLEAD — if a business user saw this result labelled with the
   original question, would they draw a wrong conclusion?

Then score: CORRECT / MISLEADING / BROKEN / CORRECTLY-REFUSED.

Finish with the three most important systemic weaknesses, ordered by how much
damage they would cause in production. Prefer categories of failure over
individual instances.
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--out", default="evaluation_results.json")
    p.add_argument("--questions", help="file with one question per line")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--print-review-prompt", action="store_true")
    args = p.parse_args()

    if args.print_review_prompt:
        print(REVIEW_PROMPT)
        return 0

    if args.questions:
        with open(args.questions, encoding="utf-8") as fh:
            questions = [(ln.strip(), "") for ln in fh if ln.strip()]
    else:
        questions = DEFAULT_QUESTIONS

    # Capture the schema so the reviewing model can verify column existence.
    schema = ""
    try:
        r = httpx.get(f"{args.url}/api/schema", timeout=60)
        schema = r.json().get("schema", "")
    except Exception as e:
        print(f"warning: could not fetch schema ({e})", file=sys.stderr)

    health: dict[str, Any] = {}
    try:
        health = httpx.get(f"{args.url}/api/health", timeout=30).json()
    except Exception:
        pass

    print(f"engine   : {args.url}")
    print(f"health   : {health}")
    print(f"questions: {len(questions)}\n")

    results = []
    for i, (q, tests) in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q}")
        res = ask(args.url, q, args.timeout)
        res["tests"] = tests
        results.append(res)

        if res["refused"]:
            verdict = "🛑 refused"
        elif res["error"]:
            verdict = "❌ error"
        elif res["sql"]:
            verdict = "✅ produced SQL"
        else:
            verdict = "⚠️  no SQL, no error"
        extra = f" · {res['repair_attempts']} repair(s)" if res["repair_attempts"] else ""
        print(f"          {verdict} · {res['elapsed_s']}s"
              f" · rows={res['row_count']} · chart={res['chart_type']}{extra}\n")

    payload = {
        "engine_url": args.url,
        "health": health,
        "schema": schema,
        "review_prompt": REVIEW_PROMPT,
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    produced = sum(1 for r in results if r["sql"])
    refused = sum(1 for r in results if r["refused"])
    errored = sum(1 for r in results if r["error"] and not r["refused"])

    print("─" * 60)
    print(f"produced SQL : {produced}/{len(results)}")
    print(f"refused      : {refused}")
    print(f"errored      : {errored}")
    print(f"\nwritten to {args.out}")
    print("\n⚠️  'produced SQL' is NOT 'correct'. Semantic errors run cleanly.")
    print("    Give the JSON plus review_prompt to a second LLM to find them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
