"""server/schema_retrieval.py — TF-IDF schema chunk retrieval.

Ranks schema table sections by relevance to the user's question so the LLM
receives a focused context window instead of the full schema.
"""

import re
import math
from collections import Counter


def _tokens(text: str) -> list[str]:
    return re.findall(r"\b[a-z][a-z0-9_]*\b", text.lower())


def get_relevant_schema(full_schema: str, question: str, top_k: int = 10) -> str:
    """Return the most relevant table sections for the given question."""
    lines = full_schema.split("\n")
    header: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    cur_name: str | None = None
    cur_lines: list[str] = []

    for line in lines:
        if line.startswith("Table:"):
            if cur_name is not None:
                sections.append((cur_name, cur_lines))
            cur_name = line
            cur_lines = [line]
        elif cur_name is not None:
            cur_lines.append(line)
        else:
            header.append(line)

    if cur_name is not None:
        sections.append((cur_name, cur_lines))

    if len(sections) <= top_k:
        return full_schema

    docs = ["\n".join(sl) for _, sl in sections]
    tf_list: list[Counter] = []
    df: Counter = Counter()
    for doc in docs:
        toks = Counter(_tokens(doc))
        tf_list.append(toks)
        for t in set(toks):
            df[t] += 1

    n = len(docs)
    q_toks = _tokens(question)

    def score(tf: Counter) -> float:
        s = 0.0
        for t in set(q_toks):
            if t in tf:
                idf = math.log((n + 1.0) / (df.get(t, 0) + 1)) + 1
                s += (tf[t] / max(sum(tf.values()), 1)) * idf
        return s

    # Fact tables always included regardless of score
    always  = {i for i, (name, _) in enumerate(sections) if "fact" in name.lower()}
    scores  = [score(tf) for tf in tf_list]
    ranked  = sorted(range(n), key=lambda i: scores[i], reverse=True)
    top_set = always | set(ranked[:top_k])

    chosen = "\n\n".join(
        "\n".join(sl) for i, (_, sl) in enumerate(sections) if i in top_set
    )
    return "\n".join(header) + "\n\n" + chosen
