"use client";

import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import { useMemo, useSyncExternalStore } from "react";

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

export type LatencyPoint = {
  minute: number;
  requests: number;
  p50_ms: number | null;
  p95_ms: number | null;
  errors: number;
};

const themeKey = () => document.documentElement.dataset.theme ?? "light";
const subscribe = (cb: () => void) => {
  const o = new MutationObserver(cb);
  o.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => o.disconnect();
};
const css = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const hhmm = (unix: number) =>
  new Date(unix * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

/** p50 / p95 latency per minute (lines, ms) with requests per minute (bars). A data table is always offered below. */
export function LatencyChart({ points }: { points: LatencyPoint[] }) {
  const theme = useSyncExternalStore(subscribe, themeKey, () => "light");
  const option = useMemo(() => {
    void theme;
    const text = css("--text-muted");
    const border = css("--border");
    const x = points.map((p) => hhmm(p.minute));
    return {
      animation: false,
      grid: { left: 52, right: 52, top: 32, bottom: 28 },
      tooltip: { trigger: "axis" },
      legend: { top: 0, textStyle: { color: text } },
      xAxis: {
        type: "category",
        data: x,
        axisLabel: { color: text },
        axisLine: { lineStyle: { color: border } },
      },
      yAxis: [
        {
          type: "value",
          name: "ms",
          axisLabel: { color: text },
          splitLine: { lineStyle: { color: border } },
        },
        {
          type: "value",
          name: "req/min",
          axisLabel: { color: text },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Requests",
          type: "bar",
          yAxisIndex: 1,
          data: points.map((p) => p.requests),
          itemStyle: { color: css("--surface-2"), borderColor: border },
        },
        {
          name: "p50",
          type: "line",
          data: points.map((p) => p.p50_ms),
          symbol: "circle",
          symbolSize: 4,
          lineStyle: { color: css("--primary"), width: 2 },
          itemStyle: { color: css("--primary") },
        },
        {
          name: "p95",
          type: "line",
          data: points.map((p) => p.p95_ms),
          symbol: "circle",
          symbolSize: 4,
          lineStyle: { color: css("--warning"), width: 2 },
          itemStyle: { color: css("--warning") },
        },
      ],
    };
  }, [points, theme]);

  return (
    <div>
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height: 280 }}
        notMerge
        lazyUpdate
      />
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">View as table</summary>
        <div
          className="mt-2 max-h-56 overflow-auto"
          role="region"
          aria-label="Latency by minute"
          tabIndex={0}
        >
          <table className="w-full text-sm">
            <caption className="sr-only">
              Requests and latency per minute
            </caption>
            <thead>
              <tr>
                {["Minute", "Requests", "p50 (ms)", "p95 (ms)", "5xx"].map(
                  (h) => (
                    <th
                      key={h}
                      scope="col"
                      className="px-2 py-1 text-left font-medium"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr
                  key={p.minute}
                  className="border-t border-border tabular-nums"
                >
                  <td className="px-2 py-1">{hhmm(p.minute)}</td>
                  <td className="px-2 py-1">{p.requests}</td>
                  <td className="px-2 py-1">{p.p50_ms ?? "-"}</td>
                  <td className="px-2 py-1">{p.p95_ms ?? "-"}</td>
                  <td className="px-2 py-1">{p.errors}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
