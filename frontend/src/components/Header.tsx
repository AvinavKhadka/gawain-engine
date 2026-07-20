import type { HealthStatus } from "../hooks/useHealth";

interface Props {
  status: HealthStatus | null;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onRefreshSchema: () => void;
  onNewChat: () => void;
  onToggleHistory: () => void;
  onToggleDashboard: () => void;
  onToggleDrivers: () => void;
  driversReady: boolean;
  pinnedCount: number;
}

function Badge({ ok, okLabel, errLabel }: { ok: boolean; okLabel: string; errLabel: string }) {
  return (
    <span className={`badge ${ok ? "ok" : "err"}`}>
      {ok ? okLabel : errLabel}
    </span>
  );
}

function BarclaysLogo() {
  return (
    <svg width="36" height="36" viewBox="0 0 36 36" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="18" cy="18" r="17" fill="#00AEEF" />
      <path d="M6 17 Q10 10 18 13 Q26 10 30 17 Q26 21 18 17 Q10 21 6 17Z"
            fill="white" opacity="0.95" />
      <ellipse cx="18" cy="20" rx="4.5" ry="5.5" fill="white" />
      <circle cx="18" cy="13.5" r="3" fill="white" />
    </svg>
  );
}

export function Header({ status, theme, onToggleTheme, onRefreshSchema, onNewChat, onToggleHistory, onToggleDashboard, onToggleDrivers, driversReady, pinnedCount }: Props) {
  return (
    <header>
      <BarclaysLogo />
      <div className="brand">
        <span className="brand-name">BARCLAYS</span>
        <span className="brand-sub">Gawain — Data Intelligence</span>
      </div>
      <div className="brand-divider" />
      <div className="status-bar">
        {status ? (
          <>
            <Badge ok={status.database} okLabel="DB Connected"  errLabel="DB Error" />
            <Badge ok={status.ollama}   okLabel="Ollama Ready"  errLabel="Ollama Offline" />
          </>
        ) : (
          <span className="badge loading">Connecting…</span>
        )}
        <button className="hdr-btn hdr-btn-theme" onClick={onToggleTheme}>
          {theme === "dark" ? "Light Mode" : "Dark Mode"}
        </button>
        <button className="hdr-btn" onClick={onRefreshSchema}>Refresh Schema</button>
        <button className="hdr-btn" onClick={onToggleHistory}>History</button>
        <button className="hdr-btn hdr-btn-drivers" onClick={onToggleDrivers}>
          Driver Data
          <span className={`driver-dot ${driversReady ? "on" : "off"}`} />
        </button>
        <button className="hdr-btn hdr-btn-dashboard" onClick={onToggleDashboard}>
          Dashboard{pinnedCount > 0 ? ` (${pinnedCount})` : ""}
        </button>
        <button className="hdr-btn hdr-btn-new" onClick={onNewChat}>+ New Chat</button>
      </div>
    </header>
  );
}
