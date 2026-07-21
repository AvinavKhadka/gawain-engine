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
  return iso.replace(" ", " // ");
}

export function DriverPanel({ meta, loading, rebuilding, error, onRebuild, onClose }: Props) {
  return (
    <div className="panel-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="driver-panel">
        <div className="panel-header">
          <span className="panel-title">⬢ ARASAKA // DRIVER SUBSTRATE</span>
          <span className={`badge ${meta ? "ok" : "err"}`}>
            {meta ? "EXTRACT: ACTIVE" : "EXTRACT: NULL"}
          </span>
          <button className="panel-close" onClick={onClose}>✕ CLOSE</button>
        </div>

        <div className="driver-body">
          <p className="driver-intro">
            ARASAKA_DRIVER_ANALYSIS // TACTICAL SUBSTRATE<br />
            Local DuckDB extract of star schema. Powers period attribution,
            key-influencer ranking, changepoint detection algorithms.
            Rebuild protocol required after source data mutation.<br />
            <span style={{color:"var(--red)", fontSize:"0.66rem", letterSpacing:"0.14em"}}>アラサカドライバー分析 // SEC_CLEARANCE 4 REQUIRED</span>
          </p>

          {loading && <div className="panel-loading">▶ ANALYZING SUBSTRATE INTEGRITY…</div>}

          {error && <div className="driver-error">⟁ SUBSTRATE_FAULT // {error}</div>}

          {!loading && !meta && (
            <div className="panel-empty">
              NO EXTRACT ESTABLISHED IN LOCAL ENCLAVE.<br />
              EXECUTE <b>REBUILD PROTOCOL</b> TO PULL FACT + DIMENSIONS FROM SQL SERVER INTO SECURE STORE.<br />
              EST_TIME: 45-90s // REQUIRES DB LINK
            </div>
          )}

          {meta && (
            <>
              <div className="driver-stats">
                <div className="driver-stat">
                  <span className="driver-stat-val">{meta.rows.toLocaleString()}</span>
                  <span className="driver-stat-lbl">records</span>
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
                <span className="driver-meta-key">FACT_TABLE</span>
                <span className="driver-meta-val">{meta.fact.toUpperCase()}</span>
              </div>
              <div className="driver-meta-row">
                <span className="driver-meta-key">DATE_RANGE</span>
                <span className="driver-meta-val">{meta.date_min} → {meta.date_max}</span>
              </div>
              <div className="driver-meta-row">
                <span className="driver-meta-key">BUILT_AT</span>
                <span className="driver-meta-val">
                  {fmtBuilt(meta.built_at)} // {meta.elapsed_sec}s ELAPSED
                </span>
              </div>

              <div className="panel-section-label">MEASURE_MANIFEST // {meta.measures.length}</div>
              <div className="driver-chips">
                {meta.measures.map((m) => (
                  <span key={m} className="driver-chip driver-chip-measure">{m.toUpperCase()}</span>
                ))}
              </div>

              <div className="panel-section-label">
                DIMENSION_FIELDS // {meta.dims.length} ACTIVE
              </div>
              <div className="driver-chips">
                {meta.dims.map((d) => (
                  <span key={d} className="driver-chip">{d.toUpperCase()}</span>
                ))}
              </div>

              {meta.dropped_high_cardinality.length > 0 && (
                <>
                  <div className="panel-section-label">
                    DROPPED // HIGH_CARDINALITY [{meta.dropped_high_cardinality.length}]
                  </div>
                  <div className="driver-chips">
                    {meta.dropped_high_cardinality.map((d) => (
                      <span key={d} className="driver-chip driver-chip-dropped">{d.toUpperCase()}</span>
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
            {rebuilding ? "◉ REBUILDING SUBSTRATE… // HOLD POSITION" : "⟳ INITIATE REBUILD PROTOCOL // ARASAKA_SECURE"}
          </button>
        </div>
      </div>
    </div>
  );
}
