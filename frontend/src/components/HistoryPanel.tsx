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
  if (m < 1)  return "NOW";
  if (m < 60) return `${m}M AGO`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}H AGO`;
  return `${Math.floor(h / 24)}D AGO`;
}

export function HistoryPanel({ items, loading, onRerun, onToggleFavorite, onDelete, onClose }: Props) {
  const favorites = items.filter((x) => x.favorited);
  const recent    = items.filter((x) => !x.favorited);

  return (
    <div className="panel-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="history-panel">
        <div className="panel-header">
          <span className="panel-title">◈ ARASAKA // OP LOG</span>
          <button className="panel-close" onClick={onClose}>✕ CLOSE</button>
        </div>

        {loading && <div className="panel-loading">▶ ESTABLISHING NEURAL LINK TO ARCHIVE...</div>}

        {favorites.length > 0 && (
          <section>
            <div className="panel-section-label">★ FAVORITE OPERATIONS // PRIORITY</div>
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
          <div className="panel-section-label">RECENT OPERATIONS // {recent.length} LOGS</div>
          {recent.length === 0 && !loading && (
            <div className="panel-empty">NO OPERATION LOGS FOUND IN SECURE ENCLAVE.</div>
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
      <div className="history-question" onClick={() => onRerun(item.question)} title="Re-deploy this interrogative">
        ▶ {item.question}
      </div>
      <div className="history-meta">
        <span className="history-time">TS: {timeAgo(item.created_at)}</span>
        {item.row_count > 0 && <span className="history-rows">{item.row_count.toLocaleString()} ROWS // SECURE</span>}
      </div>
      <div className="history-row-actions">
        <button
          className={`hist-btn${item.favorited ? " favorited" : ""}`}
          onClick={() => onToggleFavorite(item.id)}
          title={item.favorited ? "Remove from priority overwatch" : "Mark as priority"}
        >
          {item.favorited ? "★ PRIORITY" : "☆ MARK"}
        </button>
        <button className="hist-btn" onClick={() => onDelete(item.id)} title="Purge log">✕ PURGE</button>
      </div>
    </div>
  );
}
