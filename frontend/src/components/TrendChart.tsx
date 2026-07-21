import { useRef, useEffect } from "react";
import {
  Chart as ChartJS,
  LineElement,
  BarElement,
  PointElement,
  ArcElement,
  LineController,
  BarController,
  DoughnutController,
  ScatterController,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import type { ChartData } from "../types";

ChartJS.register(
  LineElement,
  BarElement,
  PointElement,
  ArcElement,
  LineController,
  BarController,
  DoughnutController,
  ScatterController,
  CategoryScale,
  LinearScale,
  Tooltip,
  Legend,
  Filler
);

ChartJS.defaults.color = "#8b93b0";
ChartJS.defaults.borderColor = "#1e2236";
ChartJS.defaults.font.family = '"JetBrains Mono", monospace';

/* eslint-disable @typescript-eslint/no-explicit-any */

// ARASAKA palette — neon tactical
const SEGMENT_COLORS = [
  "#ff003c",
  "#00f0ff",
  "#fcee0a",
  "#ffffff",
  "#9d00ff",
  "#00ff88",
  "#ff6b00",
  "#0080ff",
];

interface Props {
  data: ChartData;
  onPin?: () => void;
}

const tickY = (v: number | string) => {
  const n = Number(v);
  if (Math.abs(n) >= 1_000_000) return "¥" + (n / 1_000_000).toFixed(1) + "M";
  if (Math.abs(n) >= 1_000) return "¥" + (n / 1_000).toFixed(0) + "K";
  return n.toLocaleString("en-US");
};

export function TrendChart({ data, onPin }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<any>(null);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current?.destroy();
    const { type } = data;
    const createChart = (config: any) => {
      chartRef.current = new (ChartJS as any)(ref.current as any, config);
    };

    if (type === "doughnut") {
      const ds = data.datasets[0];
      const bgColors = (ds.segmentColors ?? data.labels.map((_, i) => SEGMENT_COLORS[i % SEGMENT_COLORS.length])).map((c) => c + "CC");
      const bdrColors = ds.segmentColors ?? data.labels.map((_, i) => SEGMENT_COLORS[i % SEGMENT_COLORS.length]);
      createChart({
        type: "doughnut" as const,
        data: { labels: data.labels, datasets: [{ label: ds.label, data: ds.data as number[], backgroundColor: bgColors, borderColor: bdrColors, borderWidth: 1.5, hoverOffset: 8 }] },
        options: {
          responsive: true, maintainAspectRatio: false, cutout: "62%",
          plugins: {
            legend: { position: "right" as const, labels: { font: { size: 10, family: "JetBrains Mono" }, boxWidth: 10, padding: 12, color: "#8b93b0", usePointStyle: true } },
            tooltip: { backgroundColor: "#0d0f18", titleColor: "#e9ecf5", bodyColor: "#8b93b0", borderColor: "#ff003c", borderWidth: 1, callbacks: { label: (ctx: any) => { const v = ctx.raw as number; const total = (ctx.dataset.data as number[]).reduce((a: number, b: number) => a + b, 0); const pct = ((v / total) * 100).toFixed(1); return ` ${ctx.label}: ¥${Math.round(v).toLocaleString()}  (${pct}%)`; } } },
          },
        },
      } as any);
      return () => { chartRef.current?.destroy(); };
    }

    if (type === "scatter") {
      const ds = data.datasets[0];
      createChart({
        type: "scatter" as const,
        data: { datasets: [{ label: ds.label, data: ds.data as { x: number; y: number }[], backgroundColor: (ds.color as string) + "CC", borderColor: ds.color, borderWidth: 1.5, pointRadius: 4, pointHoverRadius: 7 }] },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { labels: { font: { size: 10, family: "JetBrains Mono" }, boxWidth: 10, padding: 10, color: "#8b93b0", usePointStyle: true } }, tooltip: { backgroundColor: "#0d0f18", borderColor: "#00f0ff", borderWidth: 1, titleColor: "#e9ecf5", bodyColor: "#8b93b0" } },
          scales: {
            x: { title: { display: !!data.xLabel, text: data.xLabel, color: "#8b93b0", font: { family: "Orbitron", size: 10 } }, ticks: { font: { size: 9, family: "JetBrains Mono" }, color: "#4f5776", callback: tickY as any }, grid: { color: "rgba(255,255,255,0.05)" } },
            y: { title: { display: !!data.yLabel, text: data.yLabel, color: "#8b93b0", font: { family: "Orbitron", size: 10 } }, ticks: { font: { size: 9, family: "JetBrains Mono" }, color: "#4f5776", callback: tickY as any }, grid: { color: "rgba(255,255,255,0.05)" } },
          },
        },
      } as any);
      return () => { chartRef.current?.destroy(); };
    }

    if (type === "stacked_bar") {
      createChart({
        type: "bar" as const,
        data: { labels: data.labels, datasets: data.datasets.map((ds: any, i: number) => ({ label: ds.label, data: ds.data as number[], backgroundColor: (ds.color as string) + "BB" || SEGMENT_COLORS[i % SEGMENT_COLORS.length] + "BB", borderColor: ds.color || SEGMENT_COLORS[i % SEGMENT_COLORS.length], borderWidth: 1 })) },
        options: {
          responsive: true, maintainAspectRatio: false, interaction: { mode: "index" as const, intersect: false },
          plugins: { legend: { labels: { font: { size: 10, family: "JetBrains Mono" }, boxWidth: 10, padding: 10, color: "#8b93b0", usePointStyle: true } }, tooltip: { backgroundColor: "#0d0f18", borderColor: "#1e2236", borderWidth: 1, titleColor: "#e9ecf5", bodyColor: "#8b93b0" } },
          scales: {
            x: { stacked: true, ticks: { font: { size: 9, family: "JetBrains Mono" }, maxRotation: 45, color: "#4f5776" }, grid: { color: "rgba(255,255,255,0.04)" } },
            y: { stacked: true, ticks: { font: { size: 9, family: "JetBrains Mono" }, color: "#4f5776", callback: tickY as any }, grid: { color: "rgba(255,255,255,0.05)" } },
          },
        },
      } as any);
      return () => { chartRef.current?.destroy(); };
    }

    const isLine = type === "line";
    createChart({
      type: isLine ? ("line" as const) : ("bar" as const),
      data: { labels: data.labels, datasets: data.datasets.map((ds: any, i: number) => ({ label: ds.label, data: ds.data as number[], backgroundColor: isLine ? (ds.color || SEGMENT_COLORS[i % SEGMENT_COLORS.length]) + "20" : (ds.color || SEGMENT_COLORS[i % SEGMENT_COLORS.length]) + "BB", borderColor: ds.color || SEGMENT_COLORS[i % SEGMENT_COLORS.length], borderWidth: isLine ? 2 : 1, tension: 0.35, fill: isLine, pointRadius: data.labels.length > 40 ? 0 : 2.5, pointHoverRadius: 5, pointBackgroundColor: ds.color || SEGMENT_COLORS[i % SEGMENT_COLORS.length] })) },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index" as const, intersect: false },
        plugins: {
          legend: { labels: { font: { size: 10, family: "JetBrains Mono" }, boxWidth: 10, padding: 10, color: "#8b93b0", usePointStyle: true } },
          tooltip: { backgroundColor: "#0d0f18", borderColor: "#1e2236", borderWidth: 1, titleColor: "#e9ecf5", bodyColor: "#8b93b0", callbacks: { label: (ctx: any) => { const v = ctx.raw as number; if (Math.abs(v) >= 1000) return ` ${ctx.dataset.label}: ¥${Math.round(v).toLocaleString()}`; return ` ${ctx.dataset.label}: ${v}`; } } },
        },
        scales: {
          x: { ticks: { font: { size: 9, family: "JetBrains Mono" }, maxRotation: 45, color: "#4f5776" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { ticks: { font: { size: 9, family: "JetBrains Mono" }, color: "#4f5776", callback: tickY as any }, grid: { color: "rgba(255,255,255,0.06)" } },
        },
      },
    } as any);
    return () => { chartRef.current?.destroy(); };
  }, [data]);

  const isDoughnut = data.type === "doughnut";
  return (
    <div className={`chart-wrapper${isDoughnut ? " doughnut" : ""}`}>
      <div className="chart-header-bar">
        {data.title && <div className="chart-title">{data.title.toUpperCase()} // TACTICAL_FEED</div>}
        {onPin && (<button className="chart-pin-btn" onClick={onPin}>◈ PIN</button>)}
      </div>
      <div className={`chart-canvas-wrap${isDoughnut ? " doughnut" : ""}`}><canvas ref={ref} /></div>
    </div>
  );
}
export default TrendChart;