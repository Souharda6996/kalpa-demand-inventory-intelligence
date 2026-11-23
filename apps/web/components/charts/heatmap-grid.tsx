"use client";

import { HeatmapChart } from "echarts/charts";
import { GridComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import ReactEChartsCore from "echarts-for-react/lib/core";
import { useMemo, useSyncExternalStore } from "react";

echarts.use([HeatmapChart, GridComponent, TooltipComponent, VisualMapComponent, CanvasRenderer]);

export type HeatCell = { x: string; y: string; value: number };

const themeKey = () => document.documentElement.dataset.theme ?? "light";
const subscribe = (cb: () => void) => {
  const o = new MutationObserver(cb);
  o.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => o.disconnect();
};
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Rows x columns heatmap with an always-available data table (charts must not be the only way to read data). */
export function HeatmapGrid({ cells, xLabel, yLabel, unit }: { cells: HeatCell[]; xLabel: string; yLabel: string; unit: string }) {
  const theme = useSyncExternalStore(subscribe, themeKey, () => "light");
  const xs = useMemo(() => [...new Set(cells.map((c) => c.x))].sort(), [cells]);
  const ys = useMemo(() => [...new Set(cells.map((c) => c.y))].sort(), [cells]);
  const option = useMemo(() => {
    void theme;
    const max = Math.max(1, ...cells.map((c) => c.value));
    return {
      animation: false,
      grid: { left: 110, right: 16, top: 8, bottom: 78 },
      tooltip: { formatter: (p: { data: [number, number, number] }) => `${ys[p.data[1]]} / ${xs[p.data[0]]}: ${p.data[2].toLocaleString("en-IN")} ${unit}` },
      xAxis: { type: "category", data: xs, name: xLabel, axisLabel: { color: css("--text-muted"), interval: 0, rotate: 30 }, splitArea: { show: true } },
      yAxis: { type: "category", data: ys, name: yLabel, axisLabel: { color: css("--text-muted") }, splitArea: { show: true } },
      visualMap: { min: 0, max, orient: "horizontal", left: "center", bottom: 0, textStyle: { color: css("--text-muted") }, inRange: { color: [css("--surface-2"), css("--primary")] } },
      series: [{ type: "heatmap", data: cells.map((c) => [xs.indexOf(c.x), ys.indexOf(c.y), c.value]), label: { show: false } }],
    };
  }, [cells, xs, ys, theme, xLabel, yLabel, unit]);

  const at = new Map(cells.map((c) => [`${c.y}|${c.x}`, c.value]));
  return (
    <div>
      <ReactEChartsCore echarts={echarts} option={option} style={{ height: 40 * ys.length + 90 }} notMerge lazyUpdate />
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted">View as table</summary>
        <div className="mt-2 overflow-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">
              {yLabel} by {xLabel} ({unit})
            </caption>
            <thead>
              <tr>
                <th scope="col" className="px-2 py-1 text-left font-medium">
                  {yLabel}
                </th>
                {xs.map((x) => (
                  <th key={x} scope="col" className="px-2 py-1 text-right font-medium">
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ys.map((y) => (
                <tr key={y} className="border-t border-border">
                  <th scope="row" className="px-2 py-1 text-left font-medium">
                    {y}
                  </th>
                  {xs.map((x) => (
                    <td key={x} className="px-2 py-1 text-right tabular-nums">
                      {(at.get(`${y}|${x}`) ?? 0).toLocaleString("en-IN")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
