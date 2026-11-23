"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { FanSeries } from "@/components/charts/fan-chart";
import { API_URL } from "@/lib/api-client";

// ECharts is heavy: load it only in the browser, after the page is interactive (keeps the landing LCP fast).
const FanChart = dynamic(() => import("@/components/charts/fan-chart").then((m) => m.FanChart), { ssr: false });

type Demo = {
  location: string;
  product: string;
  model: string;
  history: { date: string; units: number }[];
  points: { date: string; p10: number; p50: number; p90: number }[];
  note: string;
};

/** Live fan chart from the synthetic demo tenant via the public read-only endpoint; renders nothing if unavailable. */
export function HeroChart() {
  const [demo, setDemo] = useState<Demo | null>(null);

  useEffect(() => {
    const ctl = new AbortController();
    const id = window.setTimeout(() => {
      fetch(`${API_URL}/api/v1/public/demo-forecast`, { signal: ctl.signal })
        .then((r) => (r.ok ? (r.json() as Promise<Demo>) : null))
        .then(setDemo)
        .catch(() => setDemo(null));
    }, 300);
    return () => {
      window.clearTimeout(id);
      ctl.abort();
    };
  }, []);

  if (!demo || demo.points.length === 0) return null;
  const last = demo.history.at(-1)?.date ?? "";
  const dates = [...new Set([...demo.history.map((h) => h.date), ...demo.points.map((p) => p.date)])].sort();
  const hist = new Map(demo.history.map((h) => [h.date, h.units]));
  const pts = new Map(demo.points.map((p) => [p.date, p]));
  const series: FanSeries = {
    dates,
    todayIndex: Math.max(dates.findIndex((d) => d > last), 0),
    actual: dates.map((d) => hist.get(d) ?? null),
    p10: dates.map((d) => pts.get(d)?.p10 ?? null),
    p50: dates.map((d) => pts.get(d)?.p50 ?? null),
    p90: dates.map((d) => pts.get(d)?.p90 ?? null),
  };
  return (
    <figure className="card relative mx-auto w-full max-w-5xl overflow-hidden shadow-[var(--shadow-lg)]" aria-label="Live demo forecast">
      <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-4 py-2.5" aria-hidden>
        <span className="h-2.5 w-2.5 rounded-full bg-border-strong" />
        <span className="h-2.5 w-2.5 rounded-full bg-border-strong" />
        <span className="h-2.5 w-2.5 rounded-full bg-border-strong" />
        <span className="ml-3 text-xs text-muted">Forecast Explorer · live demo</span>
      </div>
      <div className="p-5">
        <figcaption className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
          <span className="font-medium text-[var(--text)]">
            {demo.product} · {demo.location}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-primary" /> actual
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-accent-ai" /> forecast + P10-P90
          </span>
          <span className="ml-auto text-xs">{demo.note}</span>
        </figcaption>
        <FanChart series={series} height={300} />
      </div>
    </figure>
  );
}
