"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import dynamic from "next/dynamic";
import type { FanSeries } from "@/components/charts/fan-chart";
import { ExplainPanel } from "@/components/data/explain-panel";
import { EmptyState, Skeleton, StatusChip } from "@/components/data/primitives";
import { buttonCls, inputCls } from "@/components/layout/auth-card";
import { api } from "@/lib/api-client";
import { useGlobalFilter } from "@/components/layout/global-filter";
import { useMe } from "@/lib/auth";
import type { components } from "@/lib/api-schema";
import { apiMessage } from "@/lib/errors";

const FanChart = dynamic(() => import("@/components/charts/fan-chart").then((m) => m.FanChart), {
  ssr: false,
  loading: () => <Skeleton className="h-80" />,
});

const LABELS: Record<string, string> = {
  seasonal_naive: "Seasonal Naive",
  auto_ets: "AutoETS",
  croston: "Croston (intermittent)",
  tft: "Temporal Fusion Transformer",
  lightgbm_tweedie: "LightGBM (Tweedie)",
};
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null | undefined) => (v == null ? "n/a" : v.toFixed(3));

async function listAll<T>(get: (page: number) => Promise<{ items: T[]; total: number } | undefined>): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; ; page++) {
    const r = await get(page);
    if (!r) throw new Error("request failed");
    out.push(...r.items);
    if (out.length >= r.total || r.items.length === 0) return out;
  }
}

export default function ForecastsPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [locPick, setLocPick] = useState("");
  const [prodPick, setProdPick] = useState("");
  const [modelPick, setModelPick] = useState("");
  const [running, setRunning] = useState(false);

  const q = (page: number) => ({ params: { query: { page, page_size: 200 } } });
  const locations = useQuery({ queryKey: ["locs"], queryFn: () => listAll<components["schemas"]["LocationOut"]>((p) => api.GET("/api/v1/locations", q(p)).then((r) => r.data)) });
  const products = useQuery({ queryKey: ["prods"], queryFn: () => listAll<components["schemas"]["ProductOut"]>((p) => api.GET("/api/v1/products", q(p)).then((r) => r.data)) });
  const arena = useQuery({ queryKey: ["arena"], queryFn: async () => (await api.GET("/api/v1/models/arena")).data ?? [] });

  const stores = locations.data?.filter((l) => l.type === "store");
  const { store: filterStore, days } = useGlobalFilter();
  const locationId = locPick || filterStore || stores?.[0]?.id || "";
  const productId = prodPick || products.data?.[0]?.id || "";
  const series = useQuery({
    queryKey: ["series", locationId, productId, days, arena.data?.map((a) => a.id).join(",")],
    enabled: !!locationId && !!productId,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/forecasts/series", { params: { query: { location_id: locationId, product_id: productId, days } } });
      if (!data) throw new Error("series failed");
      return data;
    },
  });

  const model = series.data?.models.find((m) => m.model_name === modelPick) ?? series.data?.models[0];

  const fan: FanSeries | null = useMemo(() => {
    const s = series.data;
    if (!s || !model || s.history.length === 0) return null;
    const lastHistory = s.history[s.history.length - 1]!.date;
    const at = new Map(model.points.map((p) => [p.date, p]));
    const actual = new Map(s.history.map((h) => [h.date, h.units]));
    const dates = [...new Set([...actual.keys(), ...at.keys()])].sort();
    const first = dates.findIndex((d) => at.has(d));
    return {
      dates,
      // "today" = the first day after the data ends (the true future); earlier band days are the backtest fold
      todayIndex: Math.max(dates.findIndex((d) => d > lastHistory), first, 0),
      actual: dates.map((d) => actual.get(d) ?? null),
      p10: dates.map((d) => at.get(d)?.p10 ?? null),
      p50: dates.map((d) => at.get(d)?.p50 ?? null),
      p90: dates.map((d) => at.get(d)?.p90 ?? null),
    };
  }, [series.data, model]);

  async function runBacktest() {
    setRunning(true);
    const { data: job, error } = await api.POST("/api/v1/models/backtests", { body: { folds: 3 } });
    if (!job) {
      setRunning(false);
      return toast.error(apiMessage(error));
    }
    let cur = job;
    const deadline = Date.now() + 45 * 60_000; // stop watching after 45 min; the job keeps its own status
    while ((cur.status === "queued" || cur.status === "running") && Date.now() < deadline) {
      toast.message(`Backtest ${cur.status}... ${Math.round(cur.progress * 100)}%`, { id: "bt" });
      await new Promise((r) => setTimeout(r, 2000));
      const next = await api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: cur.id } } });
      if (!next.data) break;
      cur = next.data;
    }
    setRunning(false);
    if (cur.status === "queued" || cur.status === "running") {
      toast.message("Still running in the background. Check the bell for the result.", { id: "bt" });
      return;
    }
    if (cur.status === "succeeded") {
      toast.success("Backtest finished", { id: "bt" });
      qc.invalidateQueries({ queryKey: ["arena"] });
      qc.invalidateQueries({ queryKey: ["series"] });
    } else toast.error(cur.error ?? "Backtest failed", { id: "bt" });
  }

  const canRun = me?.permissions.includes("upload_data");
  if (locations.isLoading || products.isLoading) return <Skeleton className="h-64" />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Forecast Explorer</h1>
        {canRun && (
          <button className={`${buttonCls} ml-auto max-w-56`} disabled={running} onClick={runBacktest}>
            {running ? "Running backtest..." : "Run baseline backtest"}
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <select aria-label="Store" className={`${inputCls} max-w-56`} value={locationId} onChange={(e) => setLocPick(e.target.value)}>
          {stores?.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </select>
        <select aria-label="Product" className={`${inputCls} max-w-72`} value={productId} onChange={(e) => setProdPick(e.target.value)}>
          {products.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.sku} · {p.name}
            </option>
          ))}
        </select>
        <select aria-label="Model" className={`${inputCls} max-w-56`} value={model?.model_name ?? ""} onChange={(e) => setModelPick(e.target.value)} disabled={!series.data?.models.length}>
          {series.data?.models.map((m) => (
            <option key={m.run_id} value={m.model_name}>
              {LABELS[m.model_name] ?? m.model_name}
            </option>
          ))}
        </select>
      </div>

      {series.isLoading ? (
        <Skeleton className="h-80" />
      ) : !series.data?.history.length ? (
        <EmptyState title="No sales history for this series" hint="Load demo data or upload a CSV first." />
      ) : !fan ? (
        <EmptyState
          title="No forecasts yet"
          hint={canRun ? "Run the baseline backtest to generate forecasts and metrics for every model." : "Ask an owner or planner to run the backtest."}
        />
      ) : (
        <div className="grid gap-6 xl:grid-cols-[1fr_300px]">
          <section className="card p-4">
            <p className="mb-2 text-sm text-muted">
              Indigo = actual sales · saffron = forecast median with P10-P90 band. The band left of the dashed line is the <strong>last backtest fold</strong> (compared with what really sold); right of it is the <strong>next 28 days</strong> (assumes no promotion and unchanged price).
            </p>
            <FanChart series={fan} />
          </section>
          <aside className="card p-4 text-sm" aria-label="Model metrics">
            <h2 className="font-medium">{LABELS[model?.model_name ?? ""] ?? model?.model_name}</h2>
            <dl className="mt-3 space-y-2">
              {(
                [
                  ["WAPE", pct(model?.metrics?.wape)],
                  ["MASE", num(model?.metrics?.mase)],
                  ["Bias", pct(model?.metrics?.bias)],
                  ["Pinball", num(model?.metrics?.pinball)],
                  ["Coverage @80", pct(model?.metrics?.coverage80)],
                ] as const
              ).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <dt className="text-muted">{k}</dt>
                  <dd className="tabular-nums">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-xs text-muted" title="Metrics are the mean over all backtest folds and all series in this organisation">
              source: run {model?.run_id.slice(0, 8)} · all series, mean over folds
            </p>
          </aside>
        </div>
      )}

      {fan && locationId && productId && <ExplainPanel locationId={locationId} productId={productId} model={model?.model_name} />}

      {arena.data && arena.data.length > 0 && (
        <section>
          <h2 className="mb-2 font-medium">Baseline leaderboard</h2>
          <div className="overflow-auto card">
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left">
                <tr>
                  {["Model", "WAPE", "MASE", "Bias", "Pinball", "Coverage @80", "Run"].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {arena.data.map((a, i) => (
                  <tr key={a.id} className="border-t border-border">
                    <td className="px-3 py-2">
                      {LABELS[a.model_name] ?? a.model_name} {i === 0 && <StatusChip tone="ok">best WAPE</StatusChip>}
                    </td>
                    <td className="px-3 py-2 tabular-nums">{pct(a.metrics?.wape)}</td>
                    <td className="px-3 py-2 tabular-nums">{num(a.metrics?.mase)}</td>
                    <td className="px-3 py-2 tabular-nums">{pct(a.metrics?.bias)}</td>
                    <td className="px-3 py-2 tabular-nums">{num(a.metrics?.pinball)}</td>
                    <td className="px-3 py-2 tabular-nums">{pct(a.metrics?.coverage80)}</td>
                    <td className="px-3 py-2 text-muted">{a.id.slice(0, 8)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-muted">Best WAPE is not necessarily the cheapest inventory policy: the Cost tab in Model Arena compares models by replayed ₹ cost and fill rate (needs a backtest with at least 2 folds).</p>
        </section>
      )}
    </div>
  );
}
