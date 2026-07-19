import { useState } from "react";
import type { Message, Block, KpiCard, GridData, ChartData } from "../types";
import { DataGrid } from "./DataGrid";
import { TrendChart } from "./TrendChart";

interface Props {
  message: Message;
  question?: string;           // user's question for this AI response (used for training)
  onRunSql?: (sql: string) => void;
  onPin?: (kind: "grid" | "chart", title: string, data: GridData | ChartData) => void;
}

// ── KPI Cards ────────────────────────────────────────────────────────────────
function KpiCards({ cards }: { cards: KpiCard[] }) {
  return (
    <div className="kpi-grid">
      {cards.map((c, i) => (
        <div key={i} className="kpi-card">
          <div className="kpi-value">{c.value}</div>
          <div className="kpi-label">{c.label}</div>
        </div>
      ))}
    </div>
  );
}

// ── SQL block — collapsible + editable ───────────────────────────────────────
function SqlBlock({ sql, onRunSql, autoEdit = false }: {
  sql: string;
  onRunSql?: (sql: string) => void;
  autoEdit?: boolean;
}) {
  const [open, setOpen]       = useState(autoEdit);
  const [editing, setEditing] = useState(autoEdit);
  const [edited, setEdited]   = useState(sql);
  const [copied, setCopied]   = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(editing ? edited : sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleRun = () => {
    onRunSql?.(edited);
    setEditing(false);
  };

  const handleCancel = () => {
    setEdited(sql);
    setEditing(false);
  };

  return (
    <div className="sql-section">
      <button className="sql-toggle" onClick={() => setOpen((o) => !o)}>
        <span className={`sql-chevron${open ? " open" : ""}`}>▶</span>
        Generated SQL
      </button>
      {open && (
        <div className="sql-block">
          {editing ? (
            <textarea
              className="sql-editor"
              value={edited}
              onChange={(e) => setEdited(e.target.value)}
              rows={Math.max(4, edited.split("\n").length + 1)}
            />
          ) : (
            <pre>{sql}</pre>
          )}
          <div className="sql-actions">
            <button className="sql-copy" onClick={copy}>{copied ? "Copied!" : "Copy"}</button>
            {onRunSql && !editing && (
              <button className="sql-edit-btn" onClick={() => setEditing(true)}>Edit &amp; Run</button>
            )}
            {editing && (
              <>
                <button className="sql-run-btn" onClick={handleRun}>▶ Run</button>
                <button className="sql-cancel-btn" onClick={handleCancel}>Cancel</button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Answer text with basic markdown ─────────────────────────────────────────
function AnswerText({ text, streaming }: { text: string; streaming: boolean }) {
  const html = text
    .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/^#{1,3} (.+)$/gm, "<h3>$1</h3>")
    .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>[\s\S]*?<\/li>)+/g, (m) => `<ul>${m}</ul>`)
    .split(/\n\n+/)
    .map((p) => (p.trim() && !p.trim().startsWith("<") ? `<p>${p.replace(/\n/g, "<br>")}</p>` : p))
    .join("");

  return (
    <div className="answer-section">
      <div className="section-label">Analysis</div>
      <div className="answer-text" dangerouslySetInnerHTML={{ __html: html }} />
      {streaming && <span className="cursor" />}
    </div>
  );
}

// ── SQL block — collapsible + editable, auto-opens in edit mode on error ────
// (replaces the existing SqlBlock above — same component, added autoEdit prop)


type GroupedBlock =
  | { kind: "charts"; items: Extract<Block, { kind: "chart" }>[] }
  | { kind: "single"; block: Block };

function groupBlocks(blocks: Block[]): GroupedBlock[] {
  const groups: GroupedBlock[] = [];
  for (const block of blocks) {
    if (block.kind === "chart") {
      const last = groups[groups.length - 1];
      if (last?.kind === "charts" && last.items.length < 2) {
        last.items.push(block);
      } else {
        groups.push({ kind: "charts", items: [block] });
      }
    } else {
      groups.push({ kind: "single", block });
    }
  }
  return groups;
}

export function MessageBubble({ message, question, onRunSql, onPin }: Props) {
  const isUser  = message.role === "user";
  const grouped = groupBlocks(message.blocks);

  // If the message has an error, auto-open the SQL block in edit mode
  const hasError  = message.blocks.some((b) => b.kind === "error");
  // Extract SQL for the training pair (most recent sql block in this message)
  const sqlForTraining = (message.blocks.filter((b) => b.kind === "sql").pop() as
    { kind: "sql"; sql: string } | undefined)?.sql;

  return (
    <div className={`message ${isUser ? "user" : "assistant"}`}>
      <div className="avatar">{isUser ? "You" : "AI"}</div>
      <div className="bubble">
        {grouped.map((g, gi) => {
          if (g.kind === "charts") {
            const wrap = g.items.length > 1 ? "charts-grid" : undefined;
            return (
              <div key={gi} className={wrap}>
                {g.items.map((b, bi) => (
                  <TrendChart
                    key={bi}
                    data={b.data}
                    onPin={onPin ? () => onPin("chart", b.data.title || "Chart", b.data) : undefined}
                  />
                ))}
              </div>
            );
          }

          const block = g.block;
          const blockIdx = message.blocks.indexOf(block);

          switch (block.kind) {
            case "step":
              return (
                <div key={gi} className="step-indicator">
                  <div className="spinner" />
                  <span>{block.text}</span>
                </div>
              );

            case "sql":
              return <SqlBlock key={gi} sql={block.sql} onRunSql={onRunSql} autoEdit={hasError} />;

            case "kpi":
              return <KpiCards key={gi} cards={block.cards} />;

            case "grid":
              return (
                <DataGrid
                  key={gi}
                  data={block.data}
                  onPin={onPin ? () => onPin("grid", block.data._title || "Table", block.data) : undefined}
                  trainingPair={question && sqlForTraining
                    ? { question, sql: sqlForTraining }
                    : undefined}
                />
              );

            case "answer": {
              const isLast = message.streaming && blockIdx === message.blocks.length - 1;
              return <AnswerText key={gi} text={block.text} streaming={isLast} />;
            }

            case "error":
              return <div key={gi} className="error-block">{block.text}</div>;

            default:
              return null;
          }
        })}
      </div>
    </div>
  );
}
