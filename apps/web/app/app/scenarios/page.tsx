"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import dynamic from "next/dynamic";
import type { FanSeries } from "@/components/charts/fan-chart";
import { EmptyState, KpiTile, PageHeader, Skeleton, StatusChip } from "@/components/data/primitives";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import { formatMoney, formatNumber } from "@/lib/format";

const FanChart = dynamic(() => import("@/components/charts/fan-chart").then((m) => m.FanChart), {
  ssr: false,
  loading: () => <Skeleton className="h-60" />,
});

type Result = components["schemas"]["Result"];
type Changes = components["schemas"]["Changes"];

function signed(n: number, fmt: (v: number) => string) {
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}${fmt(Math.abs(n))}`;
}

function Slider({ label, value, min, max, unit, onChange, hint }: { label: string; value: number; min: number; max: number; unit: string; onChange: (v: number) => void; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1.5 flex justify-between font-medium">
        {label}
        <span className="tabular-nums text-primary-text">
          {value > 0 && min < 0 ? "+" : ""}
          {value}
          {unit}
        </span>
      </span>
      <input type="range" min={min} max={max} step={1} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[var(--primary)]" />
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

function toFan(r: Result, side: "baseline" | "scenario"): FanSeries | null {
  if (!r.fan) return null;
  const f = r.fan[side];
  return { dates: r.fan.dates, todayIndex: 0, actual: r.fan.dates.map(() => null), p10: f.p10!, p50: f.p50!, p90: f.p90! };
}

export default function ScenarioStudio() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [loc, setLoc] = useState("");
  const [category, setCategory] = useState("");
  const [product, setProduct] = useState("");
  const [price, setPrice] = useState(0);
  const [promo, setPromo] = useState(0);
  const [festival, setFestival] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [missing, setMissing] = useState(false);
  const [name, setName] = useState("");

  const locations = useQuery({
    queryKey: ["scn-locs"],
    queryFn: async () => ((await api.GET("/api/v1/locations", { params: { query: { page_size: 200 } } })).data?.items ?? []).filter((l) => l.type === "store"),
  });
  const products = useQuery({ queryKey: ["scn-prods"], queryFn: async () => (await api.GET("/api/v1/products", { params: { query: { page_size: 200 } } })).data?.items ?? [] });
  const saved = useQuery({ queryKey: ["scenarios"], queryFn: async () => (await api.GET("/api/v1/scenarios")).data ?? [] });
  const locationId = loc || locations.data?.[0]?.id || "";
  const categories = useMemo(() => [...new Set((products.data ?? []).map((p) => p.category))].sort(), [products.data]);
  const inCat = (products.data ?? []).filter((p) => !category || p.category === category);
  const canEdit = me?.permissions.includes("edit_scenarios");

  const body = () => {
    const changes: Changes = { price_change_pct: price, promo_depth_pct: promo, add_festival: festival || null };
    return { scope: { location_id: locationId, category: category || null, product_ids: product ? [product] : null }, changes };
  };

  async function run() {
    setBusy(true);
    setMissing(false);
    const { data, error, response } = await api.POST("/api/v1/scenarios/run", { body: body() });
    setBusy(false);
    if (response.status === 404) return setMissing(true);
    if (!data) return toast.error(apiMessage(error));
    setResult(data);
  }

  async function save() {
    const { error } = await api.POST("/api/v1/scenarios", { body: { ...body(), name } });
    if (error) return toast.error(apiMessage(error));
    toast.success("Scenario saved");
    setName("");
    qc.invalidateQueries({ queryKey: ["scenarios"] });
  }

  if (locations.isLoading) return <Skeleton className="h-64" />;
  const base = result ? toFan(result, "baseline") : null;
  const scn = result ? toFan(result, "scenario") : null;
  const pctUnits = result ? (result.delta_units / Math.max(1, result.days.reduce((a, d) => a + d.baseline, 0))) * 100 : 0;

  return (
    <div>
      <PageHeader
        title="Scenario Studio"
        description="Change what the model already knows about the future (price, promotion depth, festivals) and see the forecast move. It re-infers with the saved LightGBM model, so nothing is a hand-set multiplier."
      />
      <div className="grid gap-6 xl:grid-cols-[340px_1fr]">
        <section className="card space-y-5 p-5" aria-label="Scenario controls">
          <div className="grid gap-3">
            <label className="text-sm">
              <span className="mb-1.5 block font-medium">Store</span>
              <select className="input" value={locationId} onChange={(e) => setLoc(e.target.value)}>
                {locations.data?.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1.5 block font-medium">Category</span>
              <select className="input" value={category} onChange={(e) => { setCategory(e.target.value); setProduct(""); }}>
                <option value="">All categories</option>
                {categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              <span className="mb-1.5 block font-medium">Product (optional)</span>
              <select className="input" value={product} onChange={(e) => setProduct(e.target.value)}>
                <option value="">All in scope</option>
                {inCat.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.sku} · {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <Slider label="Price change" value={price} min={-30} max={30} unit="%" onChange={setPrice} />
          <Slider label="Promotion depth" value={promo} min={0} max={40} unit="%" onChange={setPromo} hint="A promotion lowers the price and switches the promo flag on." />
          <label className="block text-sm">
            <span className="mb-1.5 block font-medium">Add a festival on</span>
            <input type="date" className="input" value={festival} onChange={(e) => setFestival(e.target.value)} />
          </label>
          <div className="rounded-[10px] bg-surface-2 p-3 text-xs text-muted">
            <strong className="text-[var(--text)]">Weather shift</strong>: not available. Weather is not an input of the current model, so Kalpa does not pretend to model it.
          </div>
          <button className="btn btn-primary w-full" disabled={busy || !canEdit || !locationId} onClick={run}>
            {busy ? "Running..." : "Run scenario"}
          </button>
          {!canEdit && <p className="text-xs text-muted">Your role cannot run scenarios.</p>}
        </section>

        <div className="space-y-6">
          {missing && (
            <EmptyState
              icon="flask"
              title="No saved model to re-infer with"
              hint="Scenarios use the LightGBM model saved by a backtest. Run the baseline backtest (it includes LightGBM) first."
              action={
                <Link href="/app/forecasts" className="btn btn-primary">
                  Go to Forecasts
                </Link>
              }
            />
          )}
          {!result && !missing && <EmptyState icon="flask" title="Pick a scope and run a scenario" hint="Try a 15% promotion on one category and compare against the baseline." />}
          {result && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <KpiTile kind="forecast" label="Change in units (28 d)" value={signed(result.delta_units, (v) => formatNumber(Math.round(v)))} hint={`${signed(pctUnits, (v) => v.toFixed(1))}%`} source="scenario P50 sum minus baseline P50 sum, from the saved LightGBM model" />
                <KpiTile kind="forecast" label="Change in revenue" value={result.delta_revenue == null ? "n/a" : signed(result.delta_revenue, (v) => formatMoney(Math.round(v)))} source="units x last observed price (scaled by the price/promo change)" />
                <KpiTile kind="forecast" label="Change in margin" value={result.delta_margin == null ? "n/a" : signed(result.delta_margin, (v) => formatMoney(Math.round(v)))} source="units x (price - supplier unit cost); needs supplier terms" />
                <KpiTile kind="forecast" label="Series at stock-out risk" value={`${result.at_risk_scenario}`} hint={`baseline ${result.at_risk_baseline}`} source="series whose stock + on-order is below forecast demand over the lead time" />
              </div>
              {base && scn ? (
                <div className="grid gap-4 lg:grid-cols-2">
                  <div className="card p-4">
                    <h2 className="mb-1 text-sm font-medium">Baseline</h2>
                    <FanChart series={base} height={240} showToday={false} />
                  </div>
                  <div className="card p-4">
                    <h2 className="mb-1 text-sm font-medium">Scenario</h2>
                    <FanChart series={scn} height={240} showToday={false} />
                  </div>
                </div>
              ) : (
                <div className="card p-4">
                  <h2 className="mb-1 text-sm font-medium">Daily forecast (P50, summed over {result.series} series)</h2>
                  <FanChart
                    height={260}
                    showToday={false}
                    series={{
                      dates: result.days.map((d) => d.date),
                      todayIndex: 0,
                      actual: result.days.map((d) => d.baseline),
                      p10: result.days.map(() => null),
                      p50: result.days.map((d) => d.scenario),
                      p90: result.days.map(() => null),
                    }}
                  />
                  <p className="mt-1 text-xs text-muted">Indigo = baseline, saffron = scenario. Uncertainty bands appear when one product is selected: quantiles of a sum are not the sum of quantiles.</p>
                </div>
              )}
              {result.notes.length > 0 && <p className="text-xs text-muted">{result.notes.join(" ")}</p>}
              {canEdit && (
                <div className="flex gap-2">
                  <input className="input max-w-xs" placeholder="Name this scenario" aria-label="Scenario name" value={name} onChange={(e) => setName(e.target.value)} />
                  <button className="btn btn-secondary" disabled={!name.trim()} onClick={save}>
                    Save scenario
                  </button>
                </div>
              )}
            </>
          )}
          {saved.data && saved.data.length > 0 && (
            <section aria-label="Saved scenarios">
              <h2 className="mb-2 font-medium">Saved scenarios</h2>
              <ul className="card divide-y divide-border text-sm">
                {saved.data.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5">
                    <span className="font-medium">{s.name}</span>
                    <StatusChip tone="neutral">price {String((s.changes as { price_change_pct?: number } | null)?.price_change_pct ?? 0)}%</StatusChip>
                    <StatusChip tone="neutral">promo {String((s.changes as { promo_depth_pct?: number } | null)?.promo_depth_pct ?? 0)}%</StatusChip>
                    <span className="tabular-nums text-muted">units {String((s.result as { delta_units?: number } | null)?.delta_units ?? "n/a")}</span>
                    <button
                      className="ml-auto text-danger underline"
                      onClick={async () => {
                        await api.DELETE("/api/v1/scenarios/{scenario_id}", { params: { path: { scenario_id: s.id } } });
                        qc.invalidateQueries({ queryKey: ["scenarios"] });
                      }}
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
