import { useState, useCallback, useEffect } from "react";

/** Metadata describing the local DuckDB driver-analysis extract. */
export interface DriverMeta {
  fact: string;
  date_col: string;
  measures: string[];
  dims: string[];
  dropped_high_cardinality: string[];
  rows: number;
  date_min: string;
  date_max: string;
  built_at: string;
  elapsed_sec: number;
}

export function useDrivers() {
  const [meta, setMeta] = useState<DriverMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/drivers/status");
      const data = await r.json();
      setMeta(data.meta ?? null);
      setError(null);
    } catch {
      setMeta(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const rebuild = useCallback(async () => {
    setRebuilding(true);
    setError(null);
    try {
      const r = await fetch("/api/drivers/rebuild", { method: "POST" });
      if (!r.ok) {
        const detail = await r.json().catch(() => null);
        throw new Error(detail?.detail || `Rebuild failed (${r.status})`);
      }
      const data = await r.json();
      setMeta(data.meta ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rebuild failed");
    } finally {
      setRebuilding(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return { meta, loading, rebuilding, error, refresh, rebuild };
}
