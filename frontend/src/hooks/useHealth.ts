import { useState, useEffect } from "react";

export interface HealthStatus {
  database: boolean;
  ollama: boolean;
}

export function useHealth() {
  const [status, setStatus] = useState<HealthStatus | null>(null);

  const check = async () => {
    try {
      const r = await fetch("/api/health");
      setStatus(await r.json());
    } catch {
      setStatus({ database: false, ollama: false });
    }
  };

  // Initial health check on mount — intentional setState in effect for data fetching
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { check(); }, []);

  return { status, refresh: check };
}
