"use client";

import { LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import { useMemo, useSyncExternalStore } from "react";

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, CanvasRenderer]);

export type FanSeries = {
  dates: string[];
  actual: (number | null)[]; // history (indigo)
  p10: (number | null)[]; // forecast band lower
  p50: (number | null)[]; // forecast median (saffron)
  p90: (number | null)[]; // forecast band upper
  todayIndex: number; // index of the forecast start (dashed "today" marker)
};

function themeKey() {
  return document.documentElement.dataset.theme ?? "light";
}
function subscribe(cb: () => void) {
  const o = new MutationObserver(cb);
  o.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => o.disconnect();
}
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Actual (indigo) + P50 (saffron) + P10-P90 uncertainty band + dashed "today" marker. Colours follow the theme. */
export function FanChart({
  series,
  height = 320,
  unit = "units",
  showToday = true,
}: {
  series: FanSeries;
  height?: number;
  unit?: string;
  showToday?: boolean; // false when the whole chart is forecast (no "today" boundary to mark)
}) {
  const theme = useSyncExternalStore(subscribe, themeKey, () => "light");
  const option = useMemo(() => {
    void theme; // recompute colours when the theme flips
    const primary = css("--primary");
    const ai = css("--accent-ai");
    const band = css("--band");
    const text = css("--text-muted");
    const border = css("--border");
    const span = series.p90.map((hi, i) => (hi == null || series.p10[i] == null ? null : hi - (series.p10[i] as number)));
    return {
      animation: false,
      grid: { left: 48, right: 16, top: 16, bottom: 28 },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: series.dates, axisLabel: { color: text }, axisLine: { lineStyle: { color: border } } },
      yAxis: { type: "value", name: unit, axisLabel: { color: text }, splitLine: { lineStyle: { color: border } } },
      series: [
        { name: "P10", type: "line", data: series.p10, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: true } },
        { name: "P10-P90 band", type: "line", data: span, stack: "band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: band } },
        { name: "Actual", type: "line", data: series.actual, symbol: "none", lineStyle: { color: primary, width: 2 }, itemStyle: { color: primary } },
        {
          name: "Forecast (P50)",
          type: "line",
          data: series.p50,
          symbol: "none",
          lineStyle: { color: ai, width: 2 },
          itemStyle: { color: ai },
          ...(showToday
            ? {
                markLine: {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: text },
                  label: { formatter: "today", color: text },
                  data: [{ xAxis: series.dates[series.todayIndex] ?? 0 }],
                },
              }
            : {}),
        },
      ],
    };
  }, [series, theme, unit, showToday]);

  const rows = series.dates.map((d, i) => ({ d, a: series.actual[i], lo: series.p10[i], mid: series.p50[i], hi: series.p90[i] })).filter((r) => r.a != null || r.mid != null);
  const cell = (v: number | null | undefined) => (v == null ? "-" : Math.round(v * 10) / 10);
  return (
    <div>
      <ReactEChartsCore echarts={echarts} option={option} style={{ height }} notMerge lazyUpdate />
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">View as table</summary>
        <div className="mt-2 max-h-64 overflow-auto" role="region" aria-label="Forecast data table" tabIndex={0}>
          <table className="w-full text-sm">
            <caption className="sr-only">
              Actual and forecast {unit} per day, with the P10 to P90 range
            </caption>
            <thead>
              <tr>
                {["Date", "Actual", "P10", "P50 (forecast)", "P90"].map((h) => (
                  <th key={h} scope="col" className="px-2 py-1 text-left font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.d} className="border-t border-border tabular-nums">
                  <td className="px-2 py-1">{r.d}</td>
                  <td className="px-2 py-1">{cell(r.a)}</td>
                  <td className="px-2 py-1">{cell(r.lo)}</td>
                  <td className="px-2 py-1">{cell(r.mid)}</td>
                  <td className="px-2 py-1">{cell(r.hi)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
