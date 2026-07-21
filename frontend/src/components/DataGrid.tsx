import { useMemo, useCallback, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { ModuleRegistry, AllCommunityModule } from "ag-grid-community";
import type { ColDef as AGColDef, ValueFormatterParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import type { GridData } from "../types";

ModuleRegistry.registerModules([AllCommunityModule]);

interface TrainingPair { question: string; sql: string; }

interface Props {
  data: GridData;
  onPin?: () => void;
  trainingPair?: TrainingPair;
}

const fmt = (n: number, format: string | null): string => {
  if (format === "currency") return "¥" + Math.round(n).toLocaleString("en-US");
  if (format === "percent")  return n.toFixed(1) + "%";
  return Math.round(n).toLocaleString("en-US");
};

function exportCsv(data: GridData, title: string) {
  const { columns, rows } = data;
  const header = columns.map((c) => JSON.stringify(c.headerName)).join(",");
  const body = rows.map((row) =>
    columns.map((c) => {
      const v = row[c.field];
      if (v == null) return "";
      if (typeof v === "string") return JSON.stringify(v);
      return String(v);
    }).join(",")
  );
  const csv = [header, ...body].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `${title.replace(/\s+/g, "_") || "ARASAKA_EXPORT"}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function SaveTrainingBtn({ pair }: { pair: TrainingPair }) {
  const [state, setState] = useState<"idle" | "saving" | "saved" | "dup" | "err">("idle");

  const save = async () => {
    setState("saving");
    try {
      const r = await fetch("/api/train/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pair),
      });
      const data = await r.json();
      if (!r.ok) setState("err");
      else setState(data.saved ? "saved" : "dup");
    } catch {
      setState("err");
    }
  };

  const label =
    state === "saving" ? "UPLINKING…" :
    state === "saved"  ? "◉ SAVED_TO_CORE" :
    state === "dup"    ? "ALREADY_ARCHIVED" :
    state === "err"    ? "UPLINK_FAILED" :
                         "◈ TRAIN_CORE";

  return (
    <button
      className={`grid-action-btn train-btn${state === "saved" ? " train-saved" : ""}`}
      onClick={save}
      disabled={state !== "idle"}
      title="Archive as neural training pair"
    >
      {label}
    </button>
  );
}

export function DataGrid({ data, onPin, trainingPair }: Props) {
  const { columns, rows, total, _title } = data;
  const title = (_title || "DATA_MANIFEST").toUpperCase();

  const colDefs = useMemo<AGColDef[]>(() =>
    columns.map((col) => {
      const def: AGColDef = {
        field: col.field,
        headerName: col.headerName.toUpperCase(),
        sortable: true,
        filter: col.type === "number" ? "agNumberColumnFilter" : "agTextColumnFilter",
        resizable: true,
        minWidth: 110,
      };
      if (col.type === "number") {
        def.type = "numericColumn";
        def.valueFormatter = (p: ValueFormatterParams) =>
          p.value == null ? "" : fmt(Number(p.value), col.format);
        // right-align numeric cells — font is already JetBrains Mono via ag-theme
        def.cellStyle = { textAlign: "right" };
      }
      return def;
    }), [columns]);

  const rowHeight = 34;
  const headerH  = 38;
  const gridH    = Math.min(420, headerH + rows.length * rowHeight + 2);

  const handleExport = useCallback(() => exportCsv(data, title), [data, title]);

  return (
    <div className="grid-wrapper">
      <div className="grid-header-bar">
        <div className="grid-title">{title} // {rows.length} RECORDS</div>
        <div className="grid-actions">
          {trainingPair && <SaveTrainingBtn pair={trainingPair} />}
          {onPin && (
            <button className="grid-action-btn" onClick={onPin} title="Pin to tactical dashboard">
              ◈ PIN
            </button>
          )}
          <button className="grid-action-btn" onClick={handleExport} title="Export to CSV enclosure">
            ⬇ CSV
          </button>
        </div>
      </div>
      <div className="ag-theme-quartz" style={{ height: gridH, width: "100%" }}>
        <AgGridReact
          columnDefs={colDefs}
          rowData={rows}
          defaultColDef={{ flex: 1, minWidth: 90 }}
          pagination={rows.length > 50}
          paginationPageSize={50}
          animateRows={false}
          suppressMovableColumns={false}
        />
      </div>
      {total > rows.length && (
        <div className="grid-total-note">
          DISPLAYING {rows.length.toLocaleString()} OF {total.toLocaleString()} RECORDS // ARASAKA_SECURE
        </div>
      )}
    </div>
  );
}

// Robust exports — supports both named and default import styles
export default DataGrid;
