"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { toast } from "sonner";
import {
  EmptyState,
  PageHeader,
  Skeleton,
  StatusChip,
} from "@/components/data/primitives";
import { useLiveEvents } from "@/components/layout/event-stream";
import { api } from "@/lib/api-client";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import type { components } from "@/lib/api-schema";

type Job = components["schemas"]["JobOut"];
const TONE = {
  queued: "neutral",
  running: "warn",
  succeeded: "ok",
  failed: "bad",
  cancelled: "neutral",
} as const;
const ACTIVE = ["queued", "running"];

function Progress({ job }: { job: Job }) {
  const pct = Math.round(job.progress * 100);
  const indeterminate = job.type === "simulator" && job.status === "running";
  return (
    <div className="flex items-center gap-2">
      <div
        role="progressbar"
        aria-label={`${job.type} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={indeterminate ? undefined : pct}
        className="h-2 w-28 overflow-hidden rounded-full bg-surface-2"
      >
        <div
          className={`h-2 rounded-full transition-[width] duration-500 ${job.status === "failed" ? "bg-danger" : job.status === "succeeded" ? "bg-success" : "bg-primary"} ${indeterminate ? "animate-pulse" : ""}`}
          style={{ width: indeterminate ? "100%" : `${pct}%` }}
        />
      </div>
      <span className="w-10 text-xs tabular-nums text-muted">
        {indeterminate ? "live" : `${pct}%`}
      </span>
    </div>
  );
}

function duration(j: Job): string {
  if (!j.started_at) return "waiting";
  const end = j.finished_at ? Date.parse(j.finished_at) : Date.now();
  const s = Math.max(0, Math.round((end - Date.parse(j.started_at)) / 1000));
  return s < 90 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
}

function LogTail({
  id,
  status,
  live,
}: {
  id: string;
  status: string;
  live: string[];
}) {
  const q = useQuery({
    queryKey: ["job", id, status],
    queryFn: async () =>
      (
        await api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: id } },
        })
      ).data,
  });
  const lines = (q.data?.log ?? "").split("\n").filter(Boolean);
  const shown = [
    ...lines,
    ...live.filter((l) => !lines.some((x) => x.endsWith(l))),
  ].slice(-14);
  return (
    <div
      role="region"
      aria-label="Job log"
      tabIndex={0}
      className="max-h-52 overflow-auto rounded-[10px] bg-surface-2 p-3 font-mono text-xs leading-5"
    >
      {q.isLoading
        ? "Loading log..."
        : shown.length
          ? shown.map((l, i) => <div key={`${i}-${l}`}>{l}</div>)
          : "No log lines yet."}
    </div>
  );
}

/** Jobs: queued / running / finished, with live progress (SSE), a log tail and cancel. */
export default function JobsPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [lines, setLines] = useState<Record<string, string[]>>({});
  const canCancel = !!me?.permissions.includes("upload_data");
  const jobs = useQuery({
    queryKey: ["jobs"],
    refetchInterval: 15_000, // safety net: the stream is the primary source
    queryFn: async () =>
      (await api.GET("/api/v1/jobs", { params: { query: { limit: 50 } } }))
        .data ?? [],
  });

  useLiveEvents((m) => {
    if (m.type === "reconnected")
      return void qc.invalidateQueries({ queryKey: ["jobs"] });
    if (m.type !== "job") return;
    let known = false;
    qc.setQueryData<Job[]>(["jobs"], (old) =>
      (old ?? []).map((j) => {
        if (j.id !== m.id) return j;
        known = true;
        return { ...j, status: m.status, progress: m.progress ?? j.progress };
      }),
    );
    if (!known) void qc.invalidateQueries({ queryKey: ["jobs"] }); // a job started elsewhere
    if (m.line)
      setLines((l) => ({
        ...l,
        [m.id]: [...(l[m.id] ?? []), m.line as string].slice(-30),
      }));
    if (!ACTIVE.includes(m.status))
      void qc.invalidateQueries({ queryKey: ["jobs"] }); // finished: pick up times / error text
  });

  async function cancel(j: Job) {
    const { error } = await api.POST("/api/v1/jobs/{job_id}/cancel", {
      params: { path: { job_id: j.id } },
    });
    if (error) return toast.error(apiMessage(error));
    toast.success(`${j.type} cancelled`);
    void qc.invalidateQueries({ queryKey: ["jobs"] });
  }

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Backtests, retrains and the live simulator, with progress as it happens. Cancelling stops a job at its next checkpoint."
      />
      {jobs.isLoading ? (
        <Skeleton className="h-64" />
      ) : !jobs.data?.length ? (
        <EmptyState
          icon="queue"
          title="No jobs yet"
          hint="Run a backtest from Forecasts, or start the simulator from the Command Center."
        />
      ) : (
        <div className="card overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-2 text-left">
              <tr>
                {["Job", "Status", "Progress", "Started", "Duration", ""].map(
                  (h, i) => (
                    <th key={i} scope="col" className="px-3 py-2 font-medium">
                      {h || <span className="sr-only">Actions</span>}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {jobs.data.map((j) => {
                const reason = (
                  j.payload as { reason?: string } | null | undefined
                )?.reason;
                return (
                  <Fragment key={j.id}>
                    <tr className="border-t border-border align-top">
                      <td className="px-3 py-2.5">
                        <div className="font-medium">{j.type}</div>
                        <div className="text-xs text-muted">
                          {j.id.slice(0, 8)}
                          {reason ? ` · ${reason}` : ""}
                        </div>
                      </td>
                      <td className="px-3 py-2.5">
                        <StatusChip
                          tone={
                            TONE[j.status as keyof typeof TONE] ?? "neutral"
                          }
                        >
                          {j.status}
                        </StatusChip>
                        {j.error && j.status !== "cancelled" && (
                          <div className="mt-1 max-w-56 text-xs text-danger">
                            {j.error.slice(0, 140)}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <Progress job={j} />
                      </td>
                      <td className="px-3 py-2.5 tabular-nums text-muted">
                        {j.started_at
                          ? new Date(j.started_at).toLocaleTimeString("en-IN", {
                              hour12: false,
                            })
                          : "-"}
                      </td>
                      <td className="px-3 py-2.5 tabular-nums text-muted">
                        {duration(j)}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <div className="flex justify-end gap-2">
                          <button
                            className="btn btn-secondary btn-sm"
                            aria-expanded={open === j.id}
                            onClick={() => setOpen(open === j.id ? null : j.id)}
                          >
                            {open === j.id ? "Hide log" : "Log"}
                          </button>
                          {canCancel && ACTIVE.includes(j.status) && (
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => void cancel(j)}
                              aria-label={`Cancel ${j.type} ${j.id.slice(0, 8)}`}
                            >
                              Cancel
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                    {open === j.id && (
                      <tr className="border-t border-border">
                        <td colSpan={6} className="px-3 pb-3">
                          <LogTail
                            id={j.id}
                            status={j.status}
                            live={lines[j.id] ?? []}
                          />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
