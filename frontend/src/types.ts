export interface ColDef {
  field: string;
  headerName: string;
  type: "string" | "number";
  format: "currency" | "integer" | "percent" | null;
}

export interface GridData {
  columns: ColDef[];
  rows: Record<string, unknown>[];
  total: number;
  _title?: string;
}

export interface ChartDataset {
  label: string;
  data: number[] | { x: number; y: number }[];
  color: string;
  segmentColors?: string[];
}

export interface ChartData {
  type: "line" | "bar" | "doughnut" | "scatter" | "stacked_bar";
  title: string;
  labels: string[];
  datasets: ChartDataset[];
  xLabel?: string;
  yLabel?: string;
}

export interface KpiCard {
  label: string;
  value: string;
}

export type EventType =
  | "session" | "step" | "sql" | "kpi" | "grid"
  | "chart"  | "token" | "error" | "done";

export interface StreamEvent {
  type: EventType;
  content: string | GridData | ChartData | KpiCard[];
}

export type Block =
  | { kind: "step";  text: string }
  | { kind: "sql";   sql: string }
  | { kind: "kpi";   cards: KpiCard[] }
  | { kind: "grid";  data: GridData }
  | { kind: "chart"; data: ChartData }
  | { kind: "answer"; text: string }
  | { kind: "error"; text: string };

export interface Message {
  id: number;
  role: "user" | "assistant";
  blocks: Block[];
  streaming: boolean;
}

export interface PinnedItem {
  id: string;
  kind: "grid" | "chart";
  title: string;
  data: GridData | ChartData;
  pinnedAt: string;
}

export interface HistoryEntry {
  id: number;
  session_id: string;
  question: string;
  sql: string;
  row_count: number;
  favorited: number;
  created_at: string;
}
