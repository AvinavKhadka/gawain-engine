import type { PinnedItem, GridData, ChartData } from "../types";
import { DataGrid } from "./DataGrid";
import { TrendChart } from "./TrendChart";
interface Props { items: PinnedItem[]; onUnpin: (id: string) => void; onClose: () => void; }
export function Dashboard({ items, onUnpin, onClose }: Props) {
  return (
    <div className="panel-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dashboard-panel">
        <div className="panel-header"><span className="panel-title">⬢ ARASAKA // TACTICAL DASHBOARD</span><span className="panel-subtitle">{items.length} ASSETS PINNED // CLEARANCE 4</span><button className="panel-close" onClick={onClose}>✕ CLOSE</button></div>
        {items.length === 0 && (<div className="panel-empty dashboard-empty">NO TACTICAL ASSETS PINNED.<br />DEPLOY ◈ PIN PROTOCOL ON ANY CHART OR DATA MANIFEST TO ESTABLISH OVERWATCH.<br /><br /><span style={{opacity:0.5, fontSize:"0.7rem", letterSpacing:"0.12em"}}>アラサカ戦術ダッシュボード // SECURE</span></div>)}
        <div className="dashboard-grid">
          {items.map((item) => (
            <div key={item.id} className="dashboard-item">
              <div className="dashboard-item-header"><span className="dashboard-item-title">{item.title}</span><button className="dashboard-unpin-btn" onClick={() => onUnpin(item.id)}>✕ UNPIN</button></div>
              {item.kind === "chart" ? (<TrendChart data={item.data as ChartData} />) : (<DataGrid data={item.data as GridData} />)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
export default Dashboard;