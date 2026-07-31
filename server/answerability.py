"""
server/answerability.py — Refuse questions the schema cannot answer.

The failure this prevents
-------------------------
Asked "break down sales by customer age group" against a schema with no age
column, an LLM does not refuse. It produces confident, runnable SQL for a
*different* question — typically the shape it has seen most often — and the
user receives plausible numbers that do not answer what they asked. That is
worse than an error: an error is visible, a wrong answer is not.

The correct response is "this database has no age data, here is what it does
have about customers". That is a deterministic schema lookup, not a judgement
call, so it belongs in code rather than in a prompt.

How it works
------------
Extract the noun phrases a question is asking to slice or measure by, then
check whether any real column plausibly corresponds. A question is only
refused when a requested concept has NO reasonable match anywhere in the
schema — the bar is deliberately high, because wrongly refusing an answerable
question is worse than occasionally allowing a bad one through.

Matching is intentionally generous: exact, normalised, substring, and a small
synonym map for vocabulary that differs between business language and
warehouse column names ("revenue" -> SalesAmount, "profit" -> derived).
"""

from __future__ import annotations

import re

from config.observability import get_logger

log = get_logger(__name__)

__all__ = ["check_answerable", "Unanswerable"]

#: Concepts a question may ask to slice by. Each maps to the tokens that would
#: satisfy it. If none of the tokens appear in any column name, the schema
#: genuinely lacks that concept.
_CONCEPTS: dict[str, tuple[str, ...]] = {
    "age": ("age", "birth", "dob", "yearofbirth"),
    "age group": ("age", "birth", "dob"),
    "payment method": ("payment", "paymenttype", "tender", "cardtype"),
    "payment": ("payment", "tender", "card"),
    "return": ("return", "refund", "rma", "creditmemo"),
    "returns": ("return", "refund", "rma", "creditmemo"),
    "store": ("store", "outlet", "branch", "shop", "location"),
    "supplier": ("supplier", "vendor"),
    "employee": ("employee", "staff", "salesperson", "rep"),
    "campaign": ("campaign", "promotion", "marketing"),
    "rating": ("rating", "review", "score", "satisfaction"),
    "inventory": ("inventory", "stock", "onhand"),
    "warehouse": ("warehouse", "depot", "distribution"),
    "shipping method": ("shipmethod", "shipping", "carrier", "freight"),
    "email": ("email", "mail"),
    "phone": ("phone", "telephone", "mobile"),
    "loyalty": ("loyalty", "points", "rewards", "tier"),
    "subscription": ("subscription", "plan", "membership"),
    "churn": ("churn", "cancelled", "attrition", "lapsed"),
    "weather": ("weather", "temperature", "rainfall"),
}


class Unanswerable(Exception):
    """Raised when the schema provably cannot answer the question."""

    def __init__(self, concept: str, message: str, available: list[str]):
        super().__init__(message)
        self.concept = concept
        self.message = message
        self.available = available


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _schema_tokens(schema_text: str) -> set[str]:
    """Every column and table name in the schema, normalised."""
    tokens: set[str] = set()
    for m in re.finditer(r"^\s*-\s*(\w+)\s*\(", schema_text, re.MULTILINE):
        tokens.add(_normalise(m.group(1)))
    for m in re.finditer(r"^Table:\s*\S*?\.?(\w+)\s*$", schema_text, re.MULTILINE):
        tokens.add(_normalise(m.group(1)))
    return tokens


def _concept_present(tokens: tuple[str, ...], schema_tokens: set[str]) -> bool:
    """True if any schema name plausibly carries one of these tokens."""
    for token in tokens:
        t = _normalise(token)
        if not t:
            continue
        if any(t in name for name in schema_tokens):
            return True
    return False


def _customer_attributes(schema_text: str) -> list[str]:
    """Descriptive (non-key, non-numeric-id) columns on customer-like tables."""
    out: list[str] = []
    current: str | None = None
    for line in schema_text.splitlines():
        m = re.match(r"^Table:\s*(\S+)", line)
        if m:
            current = m.group(1)
            continue
        if not current or "customer" not in current.lower():
            continue
        c = re.match(r"^\s*-\s*(\w+)\s*\(([^)]+)\)", line)
        if c:
            name, dtype = c.group(1), c.group(2).lower()
            if name.lower().endswith("key") or "[PK]" in line:
                continue
            if any(x in dtype for x in ("char", "date", "bit")):
                out.append(name)
    return out[:12]


def check_answerable(question: str, schema_text: str) -> None:
    """Raise :class:`Unanswerable` if the question needs data that is absent.

    Deliberately conservative: only fires when a recognised concept is
    explicitly requested AND nothing in the schema plausibly provides it.
    Anything ambiguous is allowed through — a wrong refusal is more damaging
    than a wrong answer, because it blocks work the user could have done.
    """
    if not question or not schema_text:
        return

    q = question.lower()
    tokens = _schema_tokens(schema_text)
    if not tokens:
        return                      # no schema parsed — do not block

    # Longest concepts first so "payment method" wins over "payment".
    for concept in sorted(_CONCEPTS, key=len, reverse=True):
        if concept not in q:
            continue
        if _concept_present(_CONCEPTS[concept], tokens):
            return                  # schema has it — proceed

        available = _customer_attributes(schema_text) if "customer" in q or \
            concept in ("age", "age group", "loyalty", "churn") else []

        msg = (
            f"This database has no {concept} data — no table or column in the "
            f"schema records it, so the question cannot be answered from it."
        )
        if available:
            msg += ("\n\nCustomer attributes that ARE available: "
                    + ", ".join(available) + ".")

        log.info("question refused as unanswerable",
                 extra={"concept": concept, "question": question[:120]})
        raise Unanswerable(concept, msg, available)
