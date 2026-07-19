import type { HistoryEntry } from "../types";

interface Props {
  items: HistoryEntry[];
  loading: boolean;
  onRerun: (question: string) => void;
  onToggleFavorite: (id: number) => void;
  onDelete: (id: number) => void;
  onClose: () => void;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso + "Z").getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function HistoryPanel({ items, loading, onRerun, onToggleFavorite, onDelete, onClose }: Props) {
  const favorites = items.filter((x) => x.favorited);
  const recent    = items.filter((x) => !x.favorited);

  return (
    <div className="panel-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="history-panel">
        <div className="panel-header">
          <span className="panel-title">Query History</span>
          <button className="panel-close" onClick={onClose}>✕</button>
        </div>

        {loading && <div className="panel-loading">Loading…</div>}

        {favorites.length > 0 && (
          <section>
            <div className="panel-section-label">⭐ Favorites</div>
            {favorites.map((item) => (
              <HistoryRow
                key={item.id}
                item={item}
                onRerun={onRerun}
                onToggleFavorite={onToggleFavorite}
                onDelete={onDelete}
              />
            ))}
          </section>
        )}

        <section>
          <div className="panel-section-label">Recent</div>
          {recent.length === 0 && !loading && (
            <div className="panel-empty">No queries yet.</div>
          )}
          {recent.map((item) => (
            <HistoryRow
              key={item.id}
              item={item}
              onRerun={onRerun}
              onToggleFavorite={onToggleFavorite}
              onDelete={onDelete}
            />
          ))}
        </section>
      </div>
    </div>
  );
}

function HistoryRow({ item, onRerun, onToggleFavorite, onDelete }: {
  item: HistoryEntry;
  onRerun: (q: string) => void;
  onToggleFavorite: (id: number) => void;
  onDelete: (id: number) => void;
}) {
  return (
    <div className="history-row">
      <div className="history-question" onClick={() => onRerun(item.question)} title="Re-run this question">
        {item.question}
      </div>
      <div className="history-meta">
        <span className="history-time">{timeAgo(item.created_at)}</span>
        {item.row_count > 0 && <span className="history-rows">{item.row_count.toLocaleString()} rows</span>}
      </div>
      <div className="history-row-actions">
        <button
          className={`hist-btn${item.favorited ? " favorited" : ""}`}
          onClick={() => onToggleFavorite(item.id)}
          title={item.favorited ? "Remove from favorites" : "Add to favorites"}
        >
          {item.favorited ? "⭐" : "☆"}
        </button>
        <button className="hist-btn" onClick={() => onDelete(item.id)} title="Delete">🗑</button>
      </div>
    </div>
  );
}
