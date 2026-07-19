import type { DriverMeta } from "../hooks/useDrivers";

interface Props {
  meta: DriverMeta | null;
  loading: boolean;
  rebuilding: boolean;
  error: string | null;
  onRebuild: () => void;
  onClose: () => void;
}

function fmtBuilt(iso: string): string {
  // built_at is a local "YYYY-MM-DD HH:MM:SS" string from the backend
  return iso.replace(" ", " · ");
}

export function DriverPanel({ meta, loading, rebuilding, error, onRebuild, onClose }: Props) {
  return (
    <div className="panel-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="driver-panel">
        <div className="panel-header">
          <span className="panel-title">Driver Analysis</span>
          <span className={`badge ${meta ? "ok" : "err"}`}>
            {meta ? "Extract Ready" : "Not Built"}
          </span>
          <button className="panel-close" onClick={onClose}>✕</button>
        </div>

        <div className="driver-body">
          <p className="driver-intro">
            Driver analysis runs on a local DuckDB extract of your star schema —
            the substrate for period attribution, key-influencer ranking, and
            changepoint detection. Rebuild it after the underlying data changes.
          </p>

          {loading && <div className="panel-loading">Checking extract…</div>}

          {error && <div className="driver-error">⚠ {error}</div>}

          {!loading && !meta && (
            <div className="panel-empty">
              No extract has been built yet. Click <b>Rebuild Extract</b> to pull
              the fact and dimensions from SQL Server into the local store.
            </div>
          )}

          {meta && (
            <>
              <div className="driver-stats">
                <div className="driver-stat">
                  <span className="driver-stat-val">{meta.rows.toLocaleString()}</span>
                  <span className="driver-stat-lbl">rows</span>
                </div>
                <div className="driver-stat">
                  <span className="driver-stat-val">{meta.dims.length}</span>
                  <span className="driver-stat-lbl">dim fields</span>
                </div>
                <div className="driver-stat">
                  <span className="driver-stat-val">{meta.measures.length}</span>
                  <span className="driver-stat-lbl">measures</span>
                </div>
              </div>

              <div className="driver-meta-row">
                <span className="driver-meta-key">Fact</span>
                <span className="driver-meta-val">{meta.fact}</span>
              </div>
              <div className="driver-meta-row">
                <span className="driver-meta-key">Date range</span>
                <span className="driver-meta-val">{meta.date_min} → {meta.date_max}</span>
              </div>
              <div className="driver-meta-row">
                <span className="driver-meta-key">Built</span>
                <span className="driver-meta-val">
                  {fmtBuilt(meta.built_at)} ({meta.elapsed_sec}s)
                </span>
              </div>

              <div className="panel-section-label">Measures</div>
              <div className="driver-chips">
                {meta.measures.map((m) => (
                  <span key={m} className="driver-chip driver-chip-measure">{m}</span>
                ))}
              </div>

              <div className="panel-section-label">
                Dimension fields ({meta.dims.length})
              </div>
              <div className="driver-chips">
                {meta.dims.map((d) => (
                  <span key={d} className="driver-chip">{d}</span>
                ))}
              </div>

              {meta.dropped_high_cardinality.length > 0 && (
                <>
                  <div className="panel-section-label">
                    Dropped — too high-cardinality ({meta.dropped_high_cardinality.length})
                  </div>
                  <div className="driver-chips">
                    {meta.dropped_high_cardinality.map((d) => (
                      <span key={d} className="driver-chip driver-chip-dropped">{d}</span>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>

        <div className="driver-footer">
          <button
            className="driver-rebuild-btn"
            onClick={onRebuild}
            disabled={rebuilding}
          >
            {rebuilding ? "Rebuilding… (this can take a minute)" : "↻ Rebuild Extract"}
          </button>
        </div>
      </div>
    </div>
  );
}
