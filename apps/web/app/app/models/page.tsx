"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import {
  EmptyState,
  PageHeader,
  Skeleton,
  StatusChip,
} from "@/components/data/primitives";
import { useLiveEvents } from "@/components/layout/event-stream";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import { formatMoney } from "@/lib/format";

type Drift = components["schemas"]["DriftOut"];
const NAMES: Record<string, string> = {
  seasonal_naive: "Seasonal Naive",
  auto_ets: "AutoETS",
  croston: "Croston (intermittent)",
  tft: "Temporal Fusion Transformer",
  lightgbm_tweedie: "LightGBM (Tweedie)",
};
const TONE = {
  ok: "ok",
  warn: "warn",
  alert: "bad",
  unknown: "neutral",
} as const;
const pct = (v: number | null | undefined, d = 1) =>
  v == null ? "n/a" : `${(v * 100).toFixed(d)}%`;
const num = (v: number | null | undefined) =>
  v == null ? "n/a" : v.toFixed(3);

function PsiBar({ value, alert }: { value: number; alert: number }) {
  const scale = Math.max(0.4, value * 1.15, alert * 2);
  return (
    <div className="relative h-2 w-40 rounded-full bg-surface-2" aria-hidden>
      <div
        className={`h-2 rounded-full ${value > alert ? "bg-danger" : value > 0.1 ? "bg-warning" : "bg-success"}`}
        style={{ width: `${Math.min(100, (value / scale) * 100)}%` }}
      />
      <span
        className="absolute -top-0.5 h-3 w-0.5 bg-[var(--text)]"
        style={{ left: `${(alert / scale) * 100}%` }}
        title={`alert at ${alert}`}
      />
    </div>
  );
}

function DriftCard({
  d,
  canRun,
  onCheck,
  checking,
}: {
  d: Drift;
  canRun: boolean;
  onCheck: () => void;
  checking: boolean;
}) {
  const alertAt = d.thresholds.psi_alert ?? 0.2;
  return (
    <section className="card p-5" aria-label="Drift monitor">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-medium">Drift monitor</h2>
        <StatusChip tone={TONE[d.status]}>
          {d.status === "unknown" ? "not enough data" : d.status}
        </StatusChip>
        {d.model && (
          <span className="text-xs text-muted">
            {NAMES[d.model] ?? d.model}
          </span>
        )}
        {canRun && (
          <button
            className="btn btn-secondary btn-sm ml-auto"
            onClick={onCheck}
            disabled={checking}
          >
            {checking ? "Checking..." : "Check drift now"}
          </button>
        )}
      </div>
      {d.message && <p className="mt-3 text-sm text-muted">{d.message}</p>}
      {d.alerts.length > 0 && (
        <ul
          className="mt-3 space-y-1 text-sm text-danger"
          aria-label="Drift alerts"
        >
          {d.alerts.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
      <div className="mt-4 grid gap-6 md:grid-cols-2">
        <div>
          <h3 className="text-sm font-medium">Input drift (PSI)</h3>
          <p className="text-xs text-muted">
            Training window vs the {d.recent_days} new complete days. Alert
            above {alertAt}.
          </p>
          {d.psi.length === 0 ? (
            <p className="mt-3 text-sm text-muted">Not computed yet.</p>
          ) : (
            <ul className="mt-3 space-y-3">
              {d.psi.map((p) => (
                <li key={p.feature} className="text-sm">
                  <div className="flex justify-between gap-2">
                    <span>{p.feature}</span>
                    <span className="flex items-center gap-2 tabular-nums">
                      {p.psi.toFixed(3)}{" "}
                      <StatusChip tone={TONE[p.status]}>{p.status}</StatusChip>
                    </span>
                  </div>
                  <PsiBar value={p.psi} alert={alertAt} />
                  <div className="text-xs text-muted">
                    {p.baseline_n.toLocaleString("en-IN")} baseline rows vs{" "}
                    {p.recent_n.toLocaleString("en-IN")} new rows
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3 className="text-sm font-medium">Accuracy drift (WAPE)</h3>
          <p className="text-xs text-muted">
            Live: the forecast against what actually sold. Alert when it is{" "}
            {pct(d.thresholds.wape_degradation, 0)} worse than the backtest.
          </p>
          <dl className="mt-3 grid grid-cols-3 gap-3 text-sm">
            <div>
              <dt className="text-muted">Backtest</dt>
              <dd className="font-medium tabular-nums">
                {pct(d.wape.backtest)}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Live</dt>
              <dd className="font-medium tabular-nums">{pct(d.wape.live)}</dd>
            </div>
            <div>
              <dt className="text-muted">Ratio</dt>
              <dd className="font-medium tabular-nums">
                {d.wape.ratio == null ? "n/a" : `${d.wape.ratio.toFixed(2)}x`}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-muted">
            {d.complete_through
              ? `Complete days up to ${d.complete_through}`
              : "No complete day after the training data yet"}
            {d.data_end ? ` · model trained on data to ${d.data_end}` : ""}
          </p>
        </div>
      </div>
      <p className="mt-4 text-xs text-muted">
        {d.schedule}.
        {d.last_retrain
          ? ` Last retrain: ${d.last_retrain.status}${d.last_retrain.reason ? ` (${d.last_retrain.reason})` : ""}, ${new Date(d.last_retrain.created_at).toLocaleString("en-IN")}.`
          : ""}
      </p>
    </section>
  );
}

/** Models: registry (versions, metrics, champion history, MLflow links) and the drift monitor with retraining. */
export default function ModelsPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const router = useRouter();
  const [checking, setChecking] = useState(false);
  const [retraining, setRetraining] = useState(false);
  const canRun = !!me?.permissions.includes("upload_data");
  const registry = useQuery({
    queryKey: ["registry"],
    queryFn: async () => (await api.GET("/api/v1/models/registry")).data,
  });
  const drift = useQuery({
    queryKey: ["drift"],
    refetchInterval: 20_000,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/models/drift");
      if (!data) throw new Error("drift failed");
      return data;
    },
  });
  useLiveEvents((m) => {
    if (
      m.type === "job" &&
      (m.job_type === "retrain" || m.job_type === "backtest") &&
      m.status === "succeeded"
    ) {
      void qc.invalidateQueries({ queryKey: ["registry"] });
      void qc.invalidateQueries({ queryKey: ["drift"] });
    }
    if (m.type === "alert" && m.kind === "drift")
      void qc.invalidateQueries({ queryKey: ["drift"] });
  });

  async function retrain() {
    setRetraining(true);
    const { data, error } = await api.POST("/api/v1/models/retrain");
    setRetraining(false);
    if (!data) return toast.error(apiMessage(error));
    toast.success("Retrain queued", {
      action: { label: "Open Jobs", onClick: () => router.push("/app/jobs") },
    });
  }
  async function check() {
    setChecking(true);
    const { data, error } = await api.POST("/api/v1/models/drift/check");
    setChecking(false);
    if (!data) return toast.error(apiMessage(error));
    void qc.invalidateQueries({ queryKey: ["drift"] });
    if (data.alerted)
      toast(
        data.retrain_job_id
          ? "Drift detected: retrain started"
          : "Drift detected",
      );
    else
      toast.message(
        data.drift.status === "alert"
          ? "Drift already reported for this model version"
          : "Drift check done",
      );
  }

  const reg = registry.data;
  return (
    <div className="space-y-6">
      <PageHeader
        title="Models"
        description="Every trained version with its metrics, the champion history, and whether the champion still fits the data."
        actions={
          canRun ? (
            <button
              className="btn btn-primary"
              onClick={retrain}
              disabled={retraining || !reg?.models.length}
            >
              {retraining ? "Queuing..." : "Retrain now"}
            </button>
          ) : null
        }
      />
      {drift.isLoading ? (
        <Skeleton className="h-56" />
      ) : drift.data ? (
        <DriftCard
          d={drift.data}
          canRun={canRun}
          onCheck={check}
          checking={checking}
        />
      ) : null}

      {registry.isLoading ? (
        <Skeleton className="h-64" />
      ) : !reg?.models.length ? (
        <EmptyState
          icon="cpu"
          title="No models yet"
          hint="Run a backtest from Forecasts to train and register models."
          action={
            <Link href="/app/forecasts" className="btn btn-primary">
              Go to Forecasts
            </Link>
          }
        />
      ) : (
        <section aria-label="Model registry" className="space-y-4">
          <h2 className="font-medium">Registry</h2>
          {reg.models.map((m) => (
            <div key={m.model_name} className="card overflow-auto">
              <h3 className="border-b border-border bg-surface-2 px-4 py-2.5 text-sm font-medium">
                {NAMES[m.model_name] ?? m.model_name}
              </h3>
              <table className="w-full text-sm">
                <thead className="text-left text-muted">
                  <tr>
                    {[
                      "Version",
                      "Trained",
                      "WAPE",
                      "MASE",
                      "Bias",
                      "Coverage @80",
                      "Cost",
                      "Fill rate",
                      "MLflow",
                    ].map((h) => (
                      <th key={h} scope="col" className="px-3 py-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {m.versions.map((v) => (
                    <tr key={v.id} className="border-t border-border">
                      <td className="px-3 py-2.5">
                        <span className="tabular-nums">{v.version}</span>{" "}
                        {v.is_champion && (
                          <StatusChip tone="ok">champion</StatusChip>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-muted">
                        {new Date(v.created_at).toLocaleString("en-IN")}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {pct(v.metrics.wape)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {num(v.metrics.mase)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {pct(v.metrics.bias)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {pct(v.metrics.coverage80)}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {v.metrics.cost == null
                          ? "n/a"
                          : formatMoney(Math.round(v.metrics.cost))}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums">
                        {pct(v.metrics.fill_rate)}
                      </td>
                      <td className="px-3 py-2.5">
                        {v.mlflow_url ? (
                          <a
                            href={v.mlflow_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary-text underline"
                          >
                            Open run
                          </a>
                        ) : (
                          <span className="text-muted">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <div className="card p-5">
            <h3 className="font-medium">Champion history</h3>
            {reg.champion_history.length === 0 ? (
              <p className="mt-2 text-sm text-muted">
                No model has been promoted yet. Promote one from Model Arena.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-border text-sm">
                {reg.champion_history.map((e) => (
                  <li
                    key={`${e.at}-${e.run_id}`}
                    className="flex flex-wrap gap-x-3 py-2"
                  >
                    <span className="tabular-nums text-muted">
                      {new Date(e.at).toLocaleString("en-IN")}
                    </span>
                    <span>
                      {NAMES[e.model_name] ?? e.model_name}{" "}
                      <span className="text-muted">{e.version}</span>
                    </span>
                    <span className="text-muted">
                      promoted by {e.by ?? "unknown"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-3 text-xs text-muted">
              Tracking store:{" "}
              <span className="tabular-nums">{reg.mlflow_ui}</span>.{" "}
              {reg.schedule}.
            </p>
          </div>
        </section>
      )}
    </div>
  );
}
