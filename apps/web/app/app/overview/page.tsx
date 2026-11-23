"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { useAnimatedNumber } from "@/components/data/animated-number";
import {
  EmptyState,
  KpiTile,
  PageHeader,
  Skeleton,
  StatusChip,
} from "@/components/data/primitives";
import { useLiveState } from "@/components/layout/event-stream";
import { api } from "@/lib/api-client";
import { createPoForAlert } from "@/lib/alerts";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import { formatMoney, formatNumber } from "@/lib/format";
import type { AlertEvent, SimState, WatchRow } from "@/lib/live";

const DEMAND = [
  { label: "As forecast (x1.0)", value: 1.0 },
  { label: "Quiet (x0.6)", value: 0.6 },
  { label: "Busy (x1.5)", value: 1.5 },
  { label: "Forced drift (x2.5)", value: 2.5 },
];
const DAY_LENGTH = [
  { label: "1 min per day", value: 60 },
  { label: "2 min per day", value: 120 },
  { label: "4 min per day", value: 240 },
];
const time = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour12: false });

function Simulator({ sim, canRun }: { sim: SimState; canRun: boolean }) {
  const [multiplier, setMultiplier] = useState(1.0);
  const [daySeconds, setDaySeconds] = useState(60);
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    const { error } = await api.POST("/api/v1/simulator/start", {
      body: { multiplier, day_seconds: daySeconds, minutes: 60 },
    });
    if (error) {
      setBusy(false);
      return toast.error(apiMessage(error));
    }
    toast.success("Simulator starting...");
    setTimeout(() => setBusy(false), 8000); // the worker picks it up within a couple of seconds
  }
  async function stop() {
    setBusy(true);
    const { error } = await api.POST("/api/v1/simulator/stop");
    if (error) toast.error(apiMessage(error));
    setTimeout(() => setBusy(false), 2500);
  }

  return (
    <section className="card p-5" aria-label="Live store simulator">
      <div className="flex items-center gap-2">
        <h2 className="font-medium">Live store simulator</h2>
        <StatusChip tone={sim.running ? "ok" : "neutral"}>
          {sim.running ? "running" : "stopped"}
        </StatusChip>
      </div>
      <p className="mt-1 text-xs text-muted">
        Sales follow the forecast and go through the real checkout, on a
        simulated calendar that starts the day after your data ends.
      </p>
      {!sim.available ? (
        <p className="mt-4 text-sm text-muted">
          Only the demo tenant can run the simulator. Load the demo data from
          Data or onboarding.
        </p>
      ) : (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-muted">Business day</dt>
              <dd className="font-medium tabular-nums">
                {sim.sim_date ? `${sim.sim_date} · ${sim.clock ?? ""}` : "n/a"}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Checkouts</dt>
              <dd className="font-medium tabular-nums">
                {formatNumber(sim.checkouts)}
              </dd>
            </div>
            <div>
              <dt className="text-muted">Units sold</dt>
              <dd className="font-medium tabular-nums">
                {formatNumber(sim.units)}
              </dd>
            </div>
            <div>
              <dt
                className="text-muted"
                title="Demand that found an empty shelf: the sale is refused and stock never goes negative"
              >
                Lost units
              </dt>
              <dd className="font-medium tabular-nums">
                {formatNumber(sim.lost_units)}
              </dd>
            </div>
          </dl>
          {canRun ? (
            <div className="mt-4 space-y-3">
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                <label className="text-xs text-muted">
                  Demand
                  <select
                    className="input mt-1"
                    value={multiplier}
                    disabled={sim.running}
                    onChange={(e) => setMultiplier(Number(e.target.value))}
                  >
                    {DEMAND.map((d) => (
                      <option key={d.value} value={d.value}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-xs text-muted">
                  Day length
                  <select
                    className="input mt-1"
                    value={daySeconds}
                    disabled={sim.running}
                    onChange={(e) => setDaySeconds(Number(e.target.value))}
                  >
                    {DAY_LENGTH.map((d) => (
                      <option key={d.value} value={d.value}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {sim.running ? (
                <button
                  className="btn btn-secondary w-full"
                  onClick={stop}
                  disabled={busy}
                >
                  Stop simulator
                </button>
              ) : (
                <button
                  className="btn btn-primary w-full"
                  onClick={start}
                  disabled={busy}
                >
                  {busy ? "Starting..." : "Start simulator"}
                </button>
              )}
            </div>
          ) : (
            <p className="mt-4 text-xs text-muted">
              Your role can watch the simulator but not start it.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function StockWatch({ rows }: { rows: WatchRow[] }) {
  return (
    <section className="card p-5" aria-label="Stock watch">
      <h2 className="font-medium">Stock watch</h2>
      <p className="mt-1 text-xs text-muted">
        Series closest to (or below) their reorder point. The marker is the
        reorder point.
      </p>
      {rows.length === 0 ? (
        <p className="mt-4 text-sm text-muted">
          Reorder points appear after a backtest (they come from the forecast
          and supplier lead times).
        </p>
      ) : (
        <ul className="mt-3 space-y-3">
          {rows.map((r) => {
            const low = r.on_hand <= r.reorder_point;
            const width = Math.min(
              100,
              (r.on_hand / Math.max(1, r.reorder_point * 2)) * 100,
            );
            return (
              <li key={`${r.location_id}|${r.product_id}`} className="text-sm">
                <div className="flex justify-between gap-2">
                  <span
                    className="truncate"
                    title={`${r.product} at ${r.location}`}
                  >
                    {r.product}{" "}
                    <span className="text-muted">· {r.location}</span>
                  </span>
                  <span
                    className={`shrink-0 tabular-nums ${low ? "font-medium text-danger" : "text-muted"}`}
                  >
                    {r.on_hand} / {r.reorder_point}
                  </span>
                </div>
                <div
                  className="relative mt-1 h-2 rounded-full bg-surface-2"
                  aria-hidden
                >
                  <div
                    className={`h-2 rounded-full transition-[width] duration-500 ${low ? "bg-danger" : "bg-primary"}`}
                    style={{ width: `${width}%` }}
                  />
                  <span className="absolute -top-0.5 left-1/2 h-3 w-0.5 bg-[var(--text)]" />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Alerts({
  alerts,
  canOrder,
}: {
  alerts: AlertEvent[];
  canOrder: boolean;
}) {
  const [done, setDone] = useState<Record<string, boolean>>({});
  async function order(a: AlertEvent) {
    const r = await createPoForAlert(a);
    if (r.ok) {
      setDone((d) => ({ ...d, [a.id]: true }));
      toast.success(`Draft PO created: ${r.qty} units from ${r.supplier}`);
    } else toast.error(r.message);
  }
  return (
    <section className="card p-5" aria-label="Alerts">
      <h2 className="font-medium">Alerts</h2>
      {alerts.length === 0 ? (
        <p className="mt-3 text-sm text-muted">
          Nothing needs attention. Alerts appear when stock crosses a reorder
          point or a model drifts.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-border">
          {alerts.slice(0, 8).map((a) => (
            <li key={a.id} className="flex items-center gap-3 py-2 text-sm">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">
                  {a.kind === "drift"
                    ? (a.title ?? "Model drift detected")
                    : `${a.product} is below its reorder point`}
                </div>
                <div className="truncate text-xs text-muted">
                  {a.kind === "drift"
                    ? a.detail
                    : `${a.location}: ${a.on_hand} on hand, reorder point ${a.reorder_point}`}
                </div>
              </div>
              {a.kind === "reorder" && canOrder && (
                <button
                  className="btn btn-secondary btn-sm"
                  disabled={done[a.id]}
                  onClick={() => void order(a)}
                >
                  {done[a.id] ? "PO drafted" : "Create PO"}
                </button>
              )}
              {a.kind === "drift" && (
                <Link href="/app/models" className="btn btn-secondary btn-sm">
                  Open
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Command Center: live revenue / activity / stock watch / alerts over SSE, plus the simulator controls. */
export default function OverviewPage() {
  const { data: me } = useMe();
  const live = useLiveState();
  const stats = useQuery({
    queryKey: ["stats"],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/data/stats");
      if (!data) throw new Error("stats failed");
      return data;
    },
  });
  const s = stats.data;
  const l = live.data;
  const revenue = useAnimatedNumber(l?.revenue ?? 0);
  const units = useAnimatedNumber(l?.units ?? 0);
  const canRun = !!me?.permissions.includes("upload_data");
  const canOrder = !!me?.permissions.includes("create_po");

  return (
    <div>
      <PageHeader
        title="Command Center"
        description={`Welcome${me ? `, ${me.name}` : ""}. Live operations and the state of your data.`}
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {live.isLoading || stats.isLoading ? (
          Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-[124px]" />
          ))
        ) : (
          <>
            <div data-testid="kpi-revenue">
              <KpiTile
                label={`Revenue · ${l?.date ?? "today"}`}
                value={formatMoney(Math.round(revenue))}
                hint="ticks with every sale"
                source="sum of sales_daily.revenue (before GST) for the current business day, plus live checkout events"
              />
            </div>
            <div data-testid="kpi-units">
              <KpiTile
                label={`Units sold · ${l?.date ?? "today"}`}
                value={formatNumber(Math.round(units))}
                source="sum of sales_daily.units for the current business day, plus live checkout events"
              />
            </div>
            {s && (
              <KpiTile
                label="Series"
                value={formatNumber(s.series)}
                source="count of distinct (location, product) in sales_daily"
              />
            )}
            {s && (
              <KpiTile
                label="Data-quality score"
                value={s.quality_score != null ? String(s.quality_score) : "-"}
                source="latest data_quality_reports.score"
              />
            )}
          </>
        )}
      </div>

      {l ? (
        <div className="mt-6 grid gap-6 lg:grid-cols-3">
          <Simulator sim={l.sim} canRun={canRun} />
          <section className="card p-5" aria-label="Live activity">
            <h2 className="font-medium">Live activity</h2>
            {l.feed.length === 0 ? (
              <p className="mt-3 text-sm text-muted">
                No sales yet. Start the simulator (demo tenant) or sell
                something in POS.
              </p>
            ) : (
              <ul
                role="list"
                aria-label="Recent sales"
                tabIndex={0}
                className="mt-3 max-h-[360px] divide-y divide-border overflow-y-auto text-sm"
              >
                {l.feed.slice(0, 14).map((f) => (
                  <li key={f.id} className="flex items-center gap-2 py-2">
                    <span className="w-16 shrink-0 tabular-nums text-xs text-muted">
                      {time(f.ts)}
                    </span>
                    <span className="min-w-0 flex-1 truncate">
                      {f.location} · {f.units} unit{f.units === 1 ? "" : "s"}
                    </span>
                    {f.simulated && <StatusChip tone="neutral">sim</StatusChip>}
                    <span className="shrink-0 tabular-nums">
                      {formatMoney(Math.round(f.total))}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <StockWatch rows={l.watch} />
          <div className="lg:col-span-3">
            <Alerts alerts={l.alerts} canOrder={canOrder} />
          </div>
        </div>
      ) : live.isError ? (
        <div className="mt-6">
          <EmptyState
            title="Could not load live state"
            hint="The API may be restarting. This page reconnects by itself."
          />
        </div>
      ) : null}
    </div>
  );
}
