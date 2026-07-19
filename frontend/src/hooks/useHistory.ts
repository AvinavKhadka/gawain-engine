import { useState, useCallback, useEffect } from "react";
import type { HistoryEntry } from "../types";

export function useHistory() {
  const [items, setItems]   = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/history");
      const data = await r.json();
      setItems(data.items ?? []);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const toggleFavorite = useCallback(async (id: number) => {
    await fetch("/api/history/favorite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    refresh();
  }, [refresh]);

  const deleteEntry = useCallback(async (id: number) => {
    await fetch(`/api/history/${id}`, { method: "DELETE" });
    refresh();
  }, [refresh]);

  return { items, loading, refresh, toggleFavorite, deleteEntry };
}
