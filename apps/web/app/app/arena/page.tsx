"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState, PageHeader, Skeleton, StatusChip } from "@/components/data/primitives";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { formatMoney } from "@/lib/format";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";

const LABELS: Record<string, string> = {
  seasonal_naive: "Seasonal Naive",
  auto_ets: "AutoETS",
  croston: "Croston (intermittent)",
  lightgbm_tweedie: "LightGBM (Tweedie)",
  tft: "Temporal Fusion Transformer",
};
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null | undefined) => (v == null ? "n/a" : v.toFixed(3));

/** Coverage bars: how often the actual fell inside the P10-P90 band, against the 80 % target. */
function CoverageBar({ label, value, tone }: { label: string; value: number | null | undefined; tone: "raw" | "cal" }) {
  if (value == null) return <div className="text-xs text-muted">{label}: n/a</div>;
  const off = Math.abs(value - 0.8);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 text-muted">{label}</span>
      <div className="relative h-2.5 flex-1 rounded-full bg-surface-2" role="img" aria-label={`${label} coverage ${pct(value)} against an 80% target`}>
        <div className={`h-full rounded-full ${tone === "cal" ? "bg-primary" : "bg-accent-ai"}`} style={{ width: `${Math.min(100, value * 100)}%` }} />
        <div className="absolute inset-y-[-3px] w-[2px] bg-[var(--text)]" style={{ left: "80%" }} aria-hidden />
      </div>
      <span className={`w-14 text-right tabular-nums ${off <= 0.05 ? "text-success" : "text-muted"}`}>{pct(value)}</span>
    </div>
  );
}

type Run = components["schemas"]["RunOut"];

/** Decision-focused evaluation: each model's reorder policy replayed on the held-out fold, costed in rupees. */
function CostTab({ runs }: { runs: Run[] }) {
  const costed = runs.filter((r) => r.metrics?.cost != null).sort((a, b) => (a.metrics!.cost as number) - (b.metrics!.cost as number));
  if (!costed.length)
    return (
      <EmptyState
        icon="trophy"
        title="No cost replay yet"
        hint="The replay needs at least 2 backtest folds and supplier terms (lead time, MOQ, pack size) for the products."
      />
    );
  const cheapest = costed[0]!;
  const bestWape = runs[0]!;
  const a = (cheapest.config as { cost_assumptions?: Record<string, number> } | null)?.cost_assumptions;
  const same = cheapest.id === bestWape.id;
  const cheapestCost = cheapest.metrics!.cost as number;
  return (
    <section aria-label="Cost replay" className="space-y-4">
      <div className={`rounded-card border p-4 text-sm ${same ? "border-success/40 bg-success-soft" : "border-accent-ai bg-accent-soft"}`} role="status">
        {same ? (
          <>
            Here the most accurate model (<strong>{LABELS[bestWape.model_name] ?? bestWape.model_name}</strong>) is also the cheapest to run as a reorder policy.
          </>
        ) : (
          <>
            <strong>Best WAPE is not the cheapest policy.</strong> {LABELS[bestWape.model_name] ?? bestWape.model_name} is the most accurate, but{" "}
            <strong>{LABELS[cheapest.model_name] ?? cheapest.model_name}</strong> gives the lowest total cost when its forecasts drive reordering.
          </>
        )}
      </div>
      <div className="card overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-2 text-left">
            <tr>
              {["Model", "Total cost", "vs cheapest", "Fill rate", "Stock-out days", "Avg inventory (units)"].map((h) => (
                <th key={h} scope="col" className="px-3 py-2.5 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {costed.map((r) => {
              const c = r.metrics!.cost as number;
              return (
                <tr key={r.id} className="border-t border-border">
                  <td className="px-3 py-2.5 font-medium">{LABELS[r.model_name] ?? r.model_name}</td>
                  <td className="px-3 py-2.5 tabular-nums">{formatMoney(c)}</td>
                  <td className="px-3 py-2.5 tabular-nums">{r.id === cheapest.id ? "cheapest" : `+${(((c - cheapestCost) / cheapestCost) * 100).toFixed(1)}%`}</td>
                  <td className="px-3 py-2.5 tabular-nums">{pct(r.metrics?.fill_rate)}</td>
                  <td className="px-3 py-2.5 tabular-nums">{r.metrics?.stockout_days ?? "n/a"}</td>
                  <td className="px-3 py-2.5 tabular-nums">{r.metrics?.avg_inventory == null ? "n/a" : r.metrics.avg_inventory.toFixed(1)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">
        Replay of the last backtest fold (28 days): each model drives an order-up-to policy (safety stock from the previous fold&apos;s residuals), orders arrive after the
        supplier lead time, unmet demand is lost. Cost = holding + stock-out penalty
        {a ? ` (holding ${(a.holding_pct_year! * 100).toFixed(0)}% of unit cost per year, penalty ${a.stockout_penalty_ratio}x unit cost per lost unit, review every ${a.review_days} days)` : ""}.
        These are assumptions you can change in Settings.
      </p>
    </section>
  );
}

export default function ArenaPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const [tab, setTab] = useState<"accuracy" | "cost">("accuracy");
  const arena = useQuery({ queryKey: ["arena"], queryFn: async () => (await api.GET("/api/v1/models/arena")).data ?? [] });
  const canPromote = me?.permissions.includes("promote_model");
  const canRun = me?.permissions.includes("upload_data");

  async function promote(id: string) {
    const { error } = await api.POST("/api/v1/models/runs/{run_id}/promote", { params: { path: { run_id: id } } });
    if (error) return toast.error(apiMessage(error));
    toast.success("Champion updated");
    qc.invalidateQueries({ queryKey: ["arena"] });
  }

  async function runAll() {
    setRunning(true);
    const { data, error } = await api.POST("/api/v1/models/backtests", {
      body: { folds: 3, models: ["seasonal_naive", "auto_ets", "croston", "lightgbm_tweedie", "tft"] },
    });
    setRunning(false);
    if (!data) return toast.error(apiMessage(error));
    toast.message("Backtest queued. It runs in the background; the bell shows the result.");
  }

  return (
    <div>
      <PageHeader
        title="Model Arena"
        description="Every forecaster is backtested on the same rolling folds. Accuracy is not the whole story: the Cost tab replays each model&apos;s reorder policy and prices it."
        actions={
          canRun ? (
            <button className="btn btn-primary" disabled={running} onClick={runAll}>
              {running ? "Queuing..." : "Run all models incl. TFT"}
            </button>
          ) : undefined
        }
      />
      {arena.isLoading ? (
        <Skeleton className="h-64" />
      ) : !arena.data?.length ? (
        <EmptyState icon="trophy" title="No model runs yet" hint="Run the backtest to fill the leaderboard." />
      ) : (
        <>
          <div role="tablist" aria-label="Arena views" className="mb-4 inline-flex rounded-[10px] border border-border-strong bg-surface p-1">
            {(
              [
                ["accuracy", "Accuracy"],
                ["cost", "Cost (replay)"],
              ] as const
            ).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={`btn btn-sm ${tab === k ? "btn-primary" : "btn-ghost"}`}>
                {label}
              </button>
            ))}
          </div>
          {tab === "cost" && <CostTab runs={arena.data} />}
          <div className={`card overflow-auto ${tab === "cost" ? "hidden" : ""}`}>
            <table className="w-full text-sm">
              <thead className="bg-surface-2 text-left">
                <tr>
                  {["Model", "WAPE", "MASE", "Bias", "Pinball", "Coverage @80", "Calibrated @80", "Series", ""].map((h) => (
                    <th key={h} scope="col" className="px-3 py-2.5 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {arena.data.map((r, i) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="px-3 py-2.5 font-medium">
                      {LABELS[r.model_name] ?? r.model_name}{" "}
                      {i === 0 && <StatusChip tone="ok">best WAPE</StatusChip>} {r.is_champion && <StatusChip tone="warn">champion</StatusChip>}
                    </td>
                    <td className="px-3 py-2.5 tabular-nums">{pct(r.metrics?.wape)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{num(r.metrics?.mase)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{pct(r.metrics?.bias)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{num(r.metrics?.pinball)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{pct(r.metrics?.coverage80)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{pct(r.metrics?.coverage80_cal)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{r.metrics?.series_scored ?? "n/a"}</td>
                    <td className="px-3 py-2.5 text-right">
                      {canPromote && !r.is_champion && (
                        <button className="btn btn-secondary btn-sm" onClick={() => promote(r.id)}>
                          Promote
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <section className={`mt-8 ${tab === "cost" ? "hidden" : ""}`} aria-labelledby="cal-h">
            <h2 id="cal-h" className="mb-1 text-lg font-semibold">
              Interval calibration
            </h2>
            <p className="mb-4 max-w-3xl text-sm text-muted">
              A P10-P90 band should contain the actual 80 % of the time (the vertical line). Conformal calibration widens or narrows each band using the
              previous fold&apos;s errors. &quot;n/a&quot; means the model has no band (Croston) or only one fold was run (nothing earlier to calibrate on).
            </p>
            <div className="grid gap-4 md:grid-cols-2">
              {arena.data.map((r) => (
                <div key={r.id} className="card p-4">
                  <div className="mb-3 text-sm font-medium">{LABELS[r.model_name] ?? r.model_name}</div>
                  <div className="space-y-2">
                    <CoverageBar label="Raw" value={r.metrics?.coverage80} tone="raw" />
                    <CoverageBar label="Calibrated" value={r.metrics?.coverage80_cal} tone="cal" />
                  </div>
                </div>
              ))}
            </div>
          </section>
          <p className="mt-6 text-xs text-muted">Metrics are the mean over backtest folds and all scored series of this organisation; the run id is the source of each row.</p>
        </>
      )}
    </div>
  );
}
