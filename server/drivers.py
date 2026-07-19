"""
server/drivers.py — Driver analysis over the local DuckDB extract.

Answers the question "which dimension field drives changes in the fact?" using
three complementary techniques (none of which is text-RAG — driver analysis is a
statistical/attribution problem, not a semantic-retrieval one):

  1. period_attribution()  — Period-over-period contribution decomposition.
       "Revenue fell $1.2M; Region=Southwest accounts for -$800K (66%)."
       Pure arithmetic, fully explainable, no model.

  2. key_influencers()     — Global feature importance.
       Fits a gradient-boosted tree (measure ~ all dim attributes) and ranks
       columns by permutation importance. "Across all history, which fields
       explain variance in the measure?"

  3. changepoint_drivers() — Time-series break detection + slicing.
       Finds where the measure's trend broke (ruptures), then attributes the
       shift across dimension members for that specific break.

Each returns a dict: {method, measure, summary, headline, findings, frame}
where `summary` is LLM-ready prose, `findings` is structured, and `frame` is a
pandas DataFrame for grid/chart display.
"""

from __future__ import annotations

import pandas as pd

from server.extract import connect, load_meta


# ── Data access ────────────────────────────────────────────────────────────────

def _load() -> tuple[pd.DataFrame, dict]:
    meta = load_meta()
    if meta is None:
        raise RuntimeError("Analytics store not built. Run: python -m server.extract")
    con = connect()
    df = con.execute("SELECT * FROM analytics").fetchdf()
    con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df, meta


def _resolve_measure(meta: dict, measure: str | None) -> str:
    measures = meta["measures"]
    if not measures:
        raise RuntimeError("Extract has no measures.")
    if measure:
        for m in measures:
            if m.lower() == measure.lower():
                return m
        # loose contains-match (e.g. "revenue" -> "SalesAmount" won't match, but
        # "sales" -> "SalesAmount" will); fall back to first measure otherwise.
        for m in measures:
            if measure.lower() in m.lower() or m.lower() in measure.lower():
                return m
    return measures[0]


def _pick_granularity(df: pd.DataFrame) -> str:
    span = (df["date"].max() - df["date"].min()).days
    if span > 720:
        return "M"
    if span > 120:
        return "W"
    return "D"


def _period_key(dates: pd.Series, gran: str) -> pd.Series:
    p = dates.dt.to_period(gran)
    return p.astype(str)


def _fmt(x: float) -> str:
    a = abs(x)
    if a >= 1_000_000:
        return f"{x/1_000_000:.2f}M"
    if a >= 1_000:
        return f"{x/1_000:.1f}K"
    return f"{x:,.0f}"


def _changepoints(signal, max_cp: int = 3, min_size: int = 2, min_gain_frac: float = 0.05):
    """Detect mean-shift changepoints via recursive binary segmentation.

    Self-contained (no native deps): repeatedly finds the split that most reduces
    within-segment sum-of-squared-error, accepting it only if the reduction is a
    meaningful fraction of the segment's variance. Returns sorted indices.
    """
    import numpy as np

    sig = np.asarray(signal, dtype=float)

    def best_split(seg: np.ndarray):
        n = len(seg)
        if n < 2 * min_size:
            return None, 0.0
        csum = np.cumsum(seg)
        total = csum[-1]
        base = float(((seg - seg.mean()) ** 2).sum())
        best_i, best_gain = None, 0.0
        for i in range(min_size, n - min_size + 1):
            lmean = csum[i - 1] / i
            rmean = (total - csum[i - 1]) / (n - i)
            sse = float(((seg[:i] - lmean) ** 2).sum()
                        + ((seg[i:] - rmean) ** 2).sum())
            gain = base - sse
            if gain > best_gain:
                best_gain, best_i = gain, i
        return best_i, (best_gain / base if base else 0.0)

    # segments as (start, end) half-open intervals; recurse on the best gains
    found: list[int] = []
    segments = [(0, len(sig))]
    while segments and len(found) < max_cp:
        # pick the segment with the largest available gain
        best = None  # (gain, abs_index, start, end, local_i)
        for (s, e) in segments:
            li, frac = best_split(sig[s:e])
            if li is not None and frac >= min_gain_frac:
                if best is None or frac > best[0]:
                    best = (frac, s + li, s, e, li)
        if best is None:
            break
        _, abs_i, s, e, li = best
        found.append(abs_i)
        segments.remove((s, e))
        segments.extend([(s, abs_i), (abs_i, e)])

    return sorted(found)


# ── Core: contribution decomposition between two row-masks ──────────────────────

def _attribute_between(
    df: pd.DataFrame, measure: str, dims: list[str],
    mask_a: pd.Series, mask_b: pd.Series, top_n: int = 12,
) -> tuple[pd.DataFrame, float, float]:
    """Decompose the change in `measure` from period A (mask_a) to B (mask_b)
    across every (dimension, member) pair. Each member's contribution is its
    measure total in B minus in A; these sum to the overall delta within a dim.

    Returns (findings_df, total_a, total_b) where findings_df is ranked by the
    absolute contribution and tagged with its share of the overall change.
    """
    a = df[mask_a]
    b = df[mask_b]
    total_a = float(a[measure].sum())
    total_b = float(b[measure].sum())
    total_delta = total_b - total_a

    records = []
    for dim in dims:
        ga = a.groupby(dim, observed=True)[measure].sum()
        gb = b.groupby(dim, observed=True)[measure].sum()
        members = ga.index.union(gb.index)
        for m in members:
            va = float(ga.get(m, 0.0))
            vb = float(gb.get(m, 0.0))
            delta = vb - va
            if va == 0 and vb == 0:
                continue
            records.append({
                "dimension": dim,
                "member": str(m),
                "value_a": va,
                "value_b": vb,
                "delta": delta,
                "pct_of_change": (delta / total_delta * 100.0) if total_delta else 0.0,
            })

    findings = pd.DataFrame.from_records(records)
    if not findings.empty:
        findings = findings.reindex(
            findings["delta"].abs().sort_values(ascending=False).index
        ).head(top_n).reset_index(drop=True)
    return findings, total_a, total_b


# ── 1. Period-over-period attribution ───────────────────────────────────────────

def period_attribution(
    measure: str | None = None,
    period_a: str | None = None,
    period_b: str | None = None,
    gran: str | None = None,
    top_n: int = 12,
) -> dict:
    """Compare two periods and rank the dimension members driving the change.

    With no periods given, compares the two most recent complete periods at an
    auto-chosen granularity (month / week / day based on the data span).
    """
    df, meta = _load()
    measure = _resolve_measure(meta, measure)
    dims = meta["dims"]
    gran = gran or _pick_granularity(df)

    df = df.copy()
    df["__period"] = _period_key(df["date"], gran)
    periods = sorted(df["__period"].unique())
    if len(periods) < 2:
        raise RuntimeError("Need at least two periods to compare.")

    pa = period_a or periods[-2]
    pb = period_b or periods[-1]
    mask_a = df["__period"] == pa
    mask_b = df["__period"] == pb

    findings, ta, tb = _attribute_between(df, measure, dims, mask_a, mask_b, top_n)
    delta = tb - ta
    pct = (delta / ta * 100.0) if ta else 0.0
    direction = "increased" if delta >= 0 else "decreased"

    lines = [f"  - {r.dimension}={r.member}: {_fmt(r.delta)} "
             f"({r.pct_of_change:+.0f}% of change)"
             for r in findings.head(6).itertuples()]
    summary = (
        f"{measure} {direction} from {_fmt(ta)} in {pa} to {_fmt(tb)} in {pb} "
        f"({pct:+.1f}%, a change of {_fmt(delta)}). "
        f"The dimension members contributing most to this change:\n"
        + "\n".join(lines)
    )

    return {
        "method": "period_attribution",
        "measure": measure,
        "summary": summary,
        "headline": {
            "period_a": pa, "period_b": pb, "granularity": gran,
            "total_a": ta, "total_b": tb, "delta": delta, "pct_change": pct,
        },
        "findings": findings.to_dict(orient="records"),
        "frame": findings.rename(columns={
            "value_a": f"{measure} ({pa})",
            "value_b": f"{measure} ({pb})",
            "delta": "Change",
            "pct_of_change": "% of Change",
        }),
    }


# ── 2. Key influencers (global feature importance) ──────────────────────────────

def key_influencers(measure: str | None = None, top_n: int = 12,
                    max_rows: int = 80_000) -> dict:
    """Rank dimension attributes by how much they explain variance in the measure.

    Fits a HistGradientBoostingRegressor (measure ~ dim attributes, treated as
    native categoricals) and scores columns by permutation importance on a
    held-out split. Importances are normalized to a 0-100% share.
    """
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import OrdinalEncoder

    df, meta = _load()
    measure = _resolve_measure(meta, measure)
    dims = meta["dims"]
    if not dims:
        raise RuntimeError("No dimension attributes available to rank.")

    data = df[dims + [measure]].dropna(subset=[measure])
    if len(data) > max_rows:
        data = data.sample(max_rows, random_state=0)

    X_raw = data[dims].astype("string").fillna("(unknown)")
    y = data[measure].astype(float).values

    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    X = enc.fit_transform(X_raw)

    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.25, random_state=0)
    model = HistGradientBoostingRegressor(
        max_iter=200, learning_rate=0.08, max_depth=6,
        categorical_features=list(range(X.shape[1])), random_state=0,
    )
    model.fit(X_tr, y_tr)
    r2 = float(model.score(X_te, y_te))

    perm = permutation_importance(
        model, X_te, y_te, n_repeats=5, random_state=0, scoring="r2",
    )
    imp = perm.importances_mean
    imp = np.clip(imp, 0, None)
    total = imp.sum()
    shares = (imp / total * 100.0) if total > 0 else imp

    rank = (
        pd.DataFrame({"dimension": dims, "importance": shares,
                      "raw_importance": perm.importances_mean})
        .sort_values("importance", ascending=False)
        .head(top_n).reset_index(drop=True)
    )

    lines = [f"  - {r.dimension}: {r.importance:.0f}%"
             for r in rank.head(6).itertuples() if r.importance > 0]
    summary = (
        f"Ranking which dimension fields most explain variance in {measure} "
        f"(model holdout R²={r2:.2f}). Most influential fields:\n"
        + "\n".join(lines)
        + ("\n(Low R² means dimension attributes alone weakly predict the "
           "measure — treat rankings as relative.)" if r2 < 0.2 else "")
    )

    return {
        "method": "key_influencers",
        "measure": measure,
        "summary": summary,
        "headline": {"r2": r2, "n_rows": int(len(data)), "n_features": len(dims)},
        "findings": rank.to_dict(orient="records"),
        "frame": rank[["dimension", "importance"]].rename(
            columns={"dimension": "Dimension Field", "importance": "Influence %"}),
    }


# ── 3. Changepoint drivers ───────────────────────────────────────────────────────

def changepoint_drivers(measure: str | None = None, gran: str | None = None,
                        top_n: int = 12) -> dict:
    """Detect the largest break in the measure's time series, then attribute that
    shift across dimension members (before-window vs after-window)."""
    import numpy as np

    df, meta = _load()
    measure = _resolve_measure(meta, measure)
    dims = meta["dims"]
    gran = gran or _pick_granularity(df)

    df = df.copy()
    df["__period"] = _period_key(df["date"], gran)
    series = df.groupby("__period", observed=True)[measure].sum().sort_index()
    periods = list(series.index)
    if len(periods) < 6:
        raise RuntimeError("Need at least 6 periods for changepoint detection.")

    signal = series.values.astype(float)
    bkps = _changepoints(signal, max_cp=3, min_size=2)

    if not bkps:
        # fall back to the single largest period-to-period jump
        diffs = np.abs(np.diff(signal))
        bkps = [int(np.argmax(diffs)) + 1]

    # pick the breakpoint with the largest mean shift around it
    def shift_at(b: int) -> float:
        return abs(signal[b:].mean() - signal[:b].mean())

    bkp = max(bkps, key=shift_at)
    cp_period = periods[bkp]
    before_periods = set(periods[:bkp])
    after_periods = set(periods[bkp:])
    mask_before = df["__period"].isin(before_periods)
    mask_after = df["__period"].isin(after_periods)

    # compare average-per-period level before vs after so windows of unequal
    # length are comparable
    n_before, n_after = len(before_periods), len(after_periods)
    findings_raw, _, _ = _attribute_between(
        df.assign(**{measure: df[measure]}), measure, dims, mask_before, mask_after,
        top_n=top_n * 3,
    )
    # recompute deltas on a per-period-average basis
    if not findings_raw.empty:
        findings_raw["value_a"] = findings_raw["value_a"] / max(n_before, 1)
        findings_raw["value_b"] = findings_raw["value_b"] / max(n_after, 1)
        findings_raw["delta"] = findings_raw["value_b"] - findings_raw["value_a"]
        tot = findings_raw["delta"].abs().sum() or 1.0
        findings_raw["pct_of_change"] = findings_raw["delta"] / tot * 100.0
        findings = findings_raw.reindex(
            findings_raw["delta"].abs().sort_values(ascending=False).index
        ).head(top_n).reset_index(drop=True)
    else:
        findings = findings_raw

    before_avg = signal[:bkp].mean()
    after_avg = signal[bkp:].mean()
    direction = "upward" if after_avg >= before_avg else "downward"
    lines = [f"  - {r.dimension}={r.member}: {_fmt(r.delta)}/period "
             f"({r.pct_of_change:+.0f}%)"
             for r in findings.head(6).itertuples()]
    summary = (
        f"The {measure} series broke {direction} around {cp_period} "
        f"(avg {_fmt(before_avg)}/period before → {_fmt(after_avg)}/period after). "
        f"Dimension members whose own shift aligns with this break:\n"
        + "\n".join(lines)
    )

    trend = pd.DataFrame({"Period": periods, measure: signal})

    return {
        "method": "changepoint_drivers",
        "measure": measure,
        "summary": summary,
        "headline": {
            "changepoint": cp_period, "granularity": gran,
            "before_avg": float(before_avg), "after_avg": float(after_avg),
            "all_breakpoints": [periods[b] for b in bkps],
        },
        "findings": findings.to_dict(orient="records"),
        "frame": findings.rename(columns={
            "value_a": "Avg Before", "value_b": "Avg After",
            "delta": "Shift/Period", "pct_of_change": "% of Shift"}),
        "trend_frame": trend,
    }


# ── Dispatcher ───────────────────────────────────────────────────────────────────

_METHODS = {
    "period_attribution": period_attribution,
    "key_influencers": key_influencers,
    "changepoint_drivers": changepoint_drivers,
}


def run(method: str, measure: str | None = None, **kwargs) -> dict:
    if method not in _METHODS:
        raise ValueError(f"Unknown method '{method}'. Options: {list(_METHODS)}")
    return _METHODS[method](measure=measure, **kwargs)


if __name__ == "__main__":
    import argparse, json

    ap = argparse.ArgumentParser(description="Run a driver analysis.")
    ap.add_argument("method", choices=list(_METHODS))
    ap.add_argument("--measure", default=None)
    args = ap.parse_args()

    result = run(args.method, measure=args.measure)
    print("\n" + result["summary"] + "\n")
    print(result["frame"].to_string(index=False))
