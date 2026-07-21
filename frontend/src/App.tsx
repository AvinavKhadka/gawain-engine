import { useEffect, useRef, useState, useCallback } from "react";
import { Header } from "./components/Header";
import { MessageBubble } from "./components/MessageBubble";
import { ChatInput } from "./components/ChatInput";
import { HistoryPanel } from "./components/HistoryPanel";
import { Dashboard } from "./components/Dashboard";
import { DriverPanel } from "./components/DriverPanel";
import { useChat } from "./hooks/useChat";
import { useHealth } from "./hooks/useHealth";
import { useHistory } from "./hooks/useHistory";
import { useDrivers } from "./hooks/useDrivers";
import type { Block, PinnedItem, GridData, ChartData } from "./types";
import "./App.css";

const WELCOME_BLOCKS: Block[] = [
  {
    kind: "answer",
    text: "**ARASAKA // GAWAIN ENGINE ONLINE.**\n\n**SECURE CORPORATE DATA INTELLIGENCE NODE ACTIVE.**\nConnected to **AdventureWorksDW2019** tactical substrate — bicycle & accessory distribution, 2010-2014, $29.4M ledger across 60K transactions.\n\n**[CORPORATE DIRECTIVE]:** System translates natural language interrogatives into T-SQL, validates, executes, and returns KPI readouts, grid manifestations, and strategic analysis.\n\n**TRY DEPLOYING:**\n- Why did net sales variance drop -8.2% in 2013?\n- Quarterly revenue trend 2010 → 2014 // isolate anomaly\n- Top 10 customer entities by total credit allocation\n- Gross margin delta by product category // Bikes v Accessories\n- Territory performance comparison: 2012 vs 2013 // attribution drivers\n\n> アラサカは永遠です. Data is eternal. Control the data. Control Night City.\n\n*All queries logged to Arasaka secure enclave. Clearance Level 4 required.*",
  },
];

const PINNED_STORAGE_KEY = "arasaka_pinned_v3";
const THEME_STORAGE_KEY = "arasaka_theme_v2";

type Theme = "light" | "dark";

function loadPinned(): PinnedItem[] {
  try {
    return JSON.parse(localStorage.getItem(PINNED_STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function loadTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Ignore storage failures and fall back to the default theme.
  }
  // Arasaka defaults to dark — NIGHT_OPS
  return "dark";
}

export default function App() {
  const { messages, busy, send, runSql, newChat } = useChat();
  const { status, refresh: refreshHealth } = useHealth();
  const { items: historyItems, loading: histLoading, refresh: refreshHistory,
          toggleFavorite, deleteEntry } = useHistory();
  const { meta: driverMeta, loading: driverLoading, rebuilding: driverRebuilding,
          error: driverError, refresh: refreshDrivers, rebuild: rebuildDrivers } = useDrivers();
  const messagesRef  = useRef<HTMLDivElement>(null);
  const bottomRef    = useRef<HTMLDivElement>(null);
  const isAtBottom   = useRef(true);

  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [suggestionsVisible, setSuggestionsVisible] = useState(true);
  const [showHistory,   setShowHistory]   = useState(false);
  const [showDashboard, setShowDashboard] = useState(false);
  const [showDrivers,   setShowDrivers]   = useState(false);
  const [pinned, setPinned] = useState<PinnedItem[]>(loadPinned);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  // Track whether the user has scrolled away from the bottom
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    const onScroll = () => {
      isAtBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Only auto-scroll when already at the bottom — never yank the user back
  useEffect(() => {
    if (isAtBottom.current) {
      bottomRef.current?.scrollIntoView({ behavior: "instant" } as ScrollIntoViewOptions);
    }
  }, [messages]);

  const savePinned = (items: PinnedItem[]) => {
    setPinned(items);
    localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(items));
  };

  const handlePin = useCallback((kind: "grid" | "chart", title: string, data: GridData | ChartData) => {
    const item: PinnedItem = {
      id: crypto.randomUUID(),
      kind,
      title: title.toUpperCase() + " // ARASAKA_TACTICAL",
      data,
      pinnedAt: new Date().toISOString(),
    };
    savePinned([...pinned, item]);
  }, [pinned]);

  const handleUnpin = useCallback((id: string) => {
    savePinned(pinned.filter((p) => p.id !== id));
  }, [pinned]);

  const handleSend = (q: string) => {
    setSuggestionsVisible(false);
    isAtBottom.current = true;
    send(q);
  };

  const handleNewChat = () => {
    newChat();
    setSuggestionsVisible(true);
  };

  const handleRerun = (question: string) => {
    setShowHistory(false);
    setSuggestionsVisible(false);
    send(question);
  };

  const handleRunSql = useCallback((sql: string) => {
    setSuggestionsVisible(false);
    runSql(sql);
  }, [runSql]);

  const refreshSchema = async () => {
    await fetch("/api/schema/refresh", { method: "POST" });
    refreshHealth();
  };

  const toggleHistory = () => {
    if (!showHistory) refreshHistory();
    setShowHistory((v) => !v);
  };

  const toggleDrivers = () => {
    if (!showDrivers) refreshDrivers();
    setShowDrivers((v) => !v);
  };

  return (
    <div className="app">
      <Header
        status={status}
        theme={theme}
        onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")}
        onRefreshSchema={refreshSchema}
        onNewChat={handleNewChat}
        onToggleHistory={toggleHistory}
        onToggleDashboard={() => setShowDashboard((v) => !v)}
        onToggleDrivers={toggleDrivers}
        driversReady={!!driverMeta}
        pinnedCount={pinned.length}
      />

      <div className="messages" ref={messagesRef}>
        <MessageBubble
          message={{ id: 0, role: "assistant", blocks: WELCOME_BLOCKS, streaming: false }}
          onRunSql={handleRunSql}
          onPin={handlePin}
        />
        {messages.map((msg, idx) => {
          const prevUser = idx > 0
            ? [...messages].slice(0, idx).reverse().find((m) => m.role === "user")
            : undefined;
          const question = prevUser?.blocks.find((b) => b.kind === "answer")
            ? (prevUser.blocks.find((b) => b.kind === "answer") as { kind: "answer"; text: string }).text
            : undefined;
          return (
            <MessageBubble
              key={msg.id}
              message={msg}
              question={question}
              onRunSql={handleRunSql}
              onPin={handlePin}
            />
          );
        })}
        <div ref={bottomRef} />
      </div>

      <ChatInput onSend={handleSend} disabled={busy} showSuggestions={suggestionsVisible} />

      {showHistory && (
        <HistoryPanel
          items={historyItems}
          loading={histLoading}
          onRerun={handleRerun}
          onToggleFavorite={toggleFavorite}
          onDelete={deleteEntry}
          onClose={() => setShowHistory(false)}
        />
      )}

      {showDashboard && (
        <Dashboard
          items={pinned}
          onUnpin={handleUnpin}
          onClose={() => setShowDashboard(false)}
        />
      )}

      {showDrivers && (
        <DriverPanel
          meta={driverMeta}
          loading={driverLoading}
          rebuilding={driverRebuilding}
          error={driverError}
          onRebuild={rebuildDrivers}
          onClose={() => setShowDrivers(false)}
        />
      )}
    </div>
  );
}
