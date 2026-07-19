import { useRef, useEffect } from "react";
import {
  Chart,
  LineElement, BarElement, PointElement, ArcElement,
  LineController, BarController, DoughnutController, ScatterController,
  CategoryScale, LinearScale,
  Tooltip, Legend, Filler,
  type ChartOptions,
} from "chart.js";
import type { ChartData } from "../types";

Chart.register(
  LineElement, BarElement, PointElement, ArcElement,
  LineController, BarController, DoughnutController, ScatterController,
  CategoryScale, LinearScale,
  Tooltip, Legend, Filler
);

Chart.defaults.color       = "#446F8B";
Chart.defaults.borderColor = "#D0DDE8";
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

const SEGMENT_COLORS = [
  "#00AEEF", "#00A98F", "#0077B6", "#F59E0B",
  "#7DD3FC", "#4DB8D1", "#005BAA", "#FF6B6B",
];

interface Props {
  data: ChartData;
  onPin?: () => void;
}

const tickY = (v: number | string) => {
  const n = Number(v);
  if (Math.abs(n) >= 1_000_000) return "$" + (n / 1_000_000).toFixed(1) + "M";
  if (Math.abs(n) >= 1_000)     return "$" + (n / 1_000).toFixed(0) + "K";
  return n.toLocaleString("en-US");
};

export function TrendChart({ data, onPin }: Props) {
  const ref      = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chartRef.current?.destroy();

    const { type } = data;

    // ── Doughnut ────────────────────────────────────────────────────────
    if (type === "doughnut") {
      const ds = data.datasets[0];
      const bgColors  = (ds.segmentColors ?? data.labels.map((_, i) => SEGMENT_COLORS[i % SEGMENT_COLORS.length]))
                          .map((c) => c + "CC");
      const bdrColors = ds.segmentColors ?? data.labels.map((_, i) => SEGMENT_COLORS[i % SEGMENT_COLORS.length]);
      chartRef.current = new Chart(ref.current, {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{
            label: ds.label,
            data: ds.data as number[],
            backgroundColor: bgColors,
            borderColor: bdrColors,
            borderWidth: 2,
            hoverOffset: 6,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "right", labels: { font: { size: 11 }, boxWidth: 12, padding: 14, color: "#446F8B" } },
            tooltip: {
              backgroundColor: "#FFFFFF", borderColor: "#D0DDE8", borderWidth: 1,
              titleColor: "#002B5C", bodyColor: "#446F8B",
              callbacks: {
                label: (ctx) => {
                  const v = ctx.raw as number;
                  const total = (ctx.dataset.data as number[]).reduce((a, b) => a + b, 0);
                  const pct = ((v / total) * 100).toFixed(1);
                  return ` ${ctx.label}: ${Math.abs(v) >= 1000 ? "$" + Math.round(v).toLocaleString("en-US") : v}  (${pct}%)`;
                },
              },
            },
          },
        },
      });
      return () => { chartRef.current?.destroy(); };
    }

    // ── Scatter ─────────────────────────────────────────────────────────
    if (type === "scatter") {
      const ds = data.datasets[0];
      chartRef.current = new Chart(ref.current, {
        type: "scatter",
        data: {
          datasets: [{
            label: ds.label,
            data: ds.data as { x: number; y: number }[],
            backgroundColor: ds.color + "BB",
            borderColor: ds.color,
            borderWidth: 1.5,
            pointRadius: 5,
            pointHoverRadius: 7,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { labels: { font: { size: 11 }, boxWidth: 12, padding: 12, color: "#446F8B" } },
            tooltip: {
              backgroundColor: "#FFFFFF", borderColor: "#D0DDE8", borderWidth: 1,
              titleColor: "#002B5C", bodyColor: "#446F8B",
              callbacks: {
                label: (ctx) => {
                  const p = ctx.raw as { x: number; y: number };
                  return ` ${data.xLabel ?? "x"}: ${p.x}  ${data.yLabel ?? "y"}: ${p.y}`;
                },
              },
            },
          },
          scales: {
            x: {
              title: { display: !!data.xLabel, text: data.xLabel, color: "#446F8B" },
              ticks: { font: { size: 10 }, color: "#7FA8C0", callback: tickY },
              grid:  { color: "#EAF0F6" },
            },
            y: {
              title: { display: !!data.yLabel, text: data.yLabel, color: "#446F8B" },
              ticks: { font: { size: 10 }, color: "#7FA8C0", callback: tickY },
              grid:  { color: "#EAF0F6" },
            },
          },
        },
      });
      return () => { chartRef.current?.destroy(); };
    }

    // ── Stacked Bar ─────────────────────────────────────────────────────
    if (type === "stacked_bar") {
      chartRef.current = new Chart(ref.current, {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: data.datasets.map((ds) => ({
            label: ds.label,
            data: ds.data as number[],
            backgroundColor: ds.color + "BB",
            borderColor: ds.color,
            borderWidth: 1.5,
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { labels: { font: { size: 11 }, boxWidth: 12, padding: 12, color: "#446F8B" } },
            tooltip: {
              backgroundColor: "#FFFFFF", borderColor: "#D0DDE8", borderWidth: 1,
              titleColor: "#002B5C", bodyColor: "#446F8B",
            },
          },
          scales: {
            x: { stacked: true, ticks: { font: { size: 10 }, maxRotation: 45, color: "#7FA8C0" }, grid: { color: "#EAF0F6" } },
            y: { stacked: true, ticks: { font: { size: 10 }, color: "#7FA8C0", callback: tickY }, grid: { color: "#EAF0F6" } },
          },
        } as ChartOptions,
      });
      return () => { chartRef.current?.destroy(); };
    }

    // ── Bar / Line ───────────────────────────────────────────────────────
    const isLine = type === "line";
    const options: ChartOptions = {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { font: { size: 11 }, boxWidth: 12, padding: 12, color: "#446F8B" } },
        tooltip: {
          backgroundColor: "#FFFFFF", borderColor: "#D0DDE8", borderWidth: 1,
          titleColor: "#002B5C", bodyColor: "#446F8B",
          callbacks: {
            label: (ctx) => {
              const v = ctx.raw as number;
              if (Math.abs(v) >= 1000)
                return ` ${ctx.dataset.label}: ${Math.round(v).toLocaleString("en-US")}`;
              return ` ${ctx.dataset.label}: ${v}`;
            },
          },
        },
      },
      scales: {
        x: { ticks: { font: { size: 10 }, maxRotation: 45, color: "#7FA8C0" }, grid: { color: "#EAF0F6" } },
        y: { ticks: { font: { size: 10 }, color: "#7FA8C0", callback: tickY }, grid: { color: "#EAF0F6" } },
      },
    };

    chartRef.current = new Chart(ref.current, {
      type: isLine ? "line" : "bar",
      data: {
        labels: data.labels,
        datasets: data.datasets.map((ds) => ({
          label: ds.label,
          data: ds.data as number[],
          backgroundColor: isLine ? ds.color + "20" : ds.color + "BB",
          borderColor: ds.color,
          borderWidth: isLine ? 2 : 1.5,
          tension: 0.4,
          fill: isLine,
          pointRadius: data.labels.length > 40 ? 0 : 3,
          pointHoverRadius: 5,
          pointBackgroundColor: ds.color,
        })),
      },
      options,
    });

    return () => { chartRef.current?.destroy(); };
  }, [data]);

  const isDoughnut = data.type === "doughnut";
  return (
    <div className={`chart-wrapper${isDoughnut ? " doughnut" : ""}`}>
      <div className="chart-header-bar">
        {data.title && <div className="chart-title">{data.title}</div>}
        {onPin && (
          <button className="chart-pin-btn" onClick={onPin} title="Pin to Dashboard">📌</button>
        )}
      </div>
      <div className={`chart-canvas-wrap${isDoughnut ? " doughnut" : ""}`}>
        <canvas ref={ref} />
      </div>
    </div>
  );
}
