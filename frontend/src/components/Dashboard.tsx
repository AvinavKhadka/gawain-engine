import type { PinnedItem, GridData, ChartData } from "../types";
import { DataGrid } from "./DataGrid";
import { TrendChart } from "./TrendChart";

interface Props {
  items: PinnedItem[];
  onUnpin: (id: string) => void;
  onClose: () => void;
}

export function Dashboard({ items, onUnpin, onClose }: Props) {
  return (
    <div className="panel-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dashboard-panel">
        <div className="panel-header">
          <span className="panel-title">📊 Dashboard</span>
          <span className="panel-subtitle">{items.length} pinned item{items.length !== 1 ? "s" : ""}</span>
          <button className="panel-close" onClick={onClose}>✕</button>
        </div>

        {items.length === 0 && (
          <div className="panel-empty dashboard-empty">
            No pinned items yet.<br />
            Use the 📌 button on any chart or table to pin it here.
          </div>
        )}

        <div className="dashboard-grid">
          {items.map((item) => (
            <div key={item.id} className="dashboard-item">
              <div className="dashboard-item-header">
                <span className="dashboard-item-title">{item.title}</span>
                <button
                  className="dashboard-unpin-btn"
                  onClick={() => onUnpin(item.id)}
                  title="Remove from dashboard"
                >
                  ✕
                </button>
              </div>
              {item.kind === "chart" ? (
                <TrendChart data={item.data as ChartData} />
              ) : (
                <DataGrid data={item.data as GridData} />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
