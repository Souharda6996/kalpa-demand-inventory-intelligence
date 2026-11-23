"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/icons";
import { useLiveState } from "@/components/layout/event-stream";
import { api } from "@/lib/api-client";

const KEY = "kalpa-notifications-seen";
const readSeen = () => {
  try {
    return Number(localStorage.getItem(KEY) ?? 0);
  } catch {
    return 0;
  }
};

/** Bell: recent background-job outcomes (real rows from /jobs). Live alerts (stock-outs) arrive with the Phase 8 stream. */
export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(readSeen);
  const jobs = useQuery({
    queryKey: ["jobs-bell"],
    refetchInterval: 10_000,
    queryFn: async () =>
      (await api.GET("/api/v1/jobs", { params: { query: { limit: 10 } } }))
        .data ?? [],
  });
  const items = (jobs.data ?? []).filter(
    (j) => j.type !== "simulator" || j.status === "failed",
  );
  const alerts = (useLiveState().data?.alerts ?? []).slice(0, 8);
  const unread =
    items.filter(
      (j) =>
        (j.status === "succeeded" || j.status === "failed") &&
        Date.parse(j.created_at) > seen,
    ).length + alerts.filter((a) => Date.parse(a.ts) > seen).length;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) {
      const now = Date.now();
      setSeen(now);
      try {
        localStorage.setItem(KEY, String(now));
      } catch {
        /* storage blocked: unread count just resets on reload */
      }
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={unread ? `Notifications, ${unread} new` : "Notifications"}
        className="relative btn btn-secondary btn-sm"
      >
        <Icon name="bell" size={16} />
        {unread > 0 && (
          <span
            className="absolute -right-1.5 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] font-semibold text-primary-fg"
            aria-hidden
          >
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="region"
          aria-label="Notifications"
          className="absolute right-0 z-40 mt-2 w-80 max-w-[90vw] card p-2 shadow-[var(--shadow)]"
        >
          {alerts.length > 0 && (
            <section
              aria-label="Alerts"
              className="border-b border-border pb-1"
            >
              <h3 className="px-2 pt-1 text-xs font-medium uppercase tracking-wide text-muted">
                Alerts
              </h3>
              <ul className="divide-y divide-border text-sm">
                {alerts.map((a) => (
                  <li key={a.id} className="p-2">
                    <div className="font-medium">
                      {a.kind === "drift"
                        ? (a.title ?? "Model drift detected")
                        : `${a.product} is below its reorder point`}
                    </div>
                    <div className="text-xs text-muted">
                      {a.kind === "drift"
                        ? a.detail
                        : `${a.location}: ${a.on_hand} on hand, reorder point ${a.reorder_point}`}
                    </div>
                    <div className="text-xs text-muted">
                      {new Date(a.ts).toLocaleString("en-IN")}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {items.length === 0 && alerts.length === 0 ? (
            <p className="p-3 text-sm text-muted">No background jobs yet.</p>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {items.map((j) => (
                <li key={j.id} className="p-2">
                  <div className="flex justify-between">
                    <span className="font-medium">{j.type}</span>
                    <span
                      className={
                        j.status === "failed"
                          ? "text-danger"
                          : j.status === "succeeded"
                            ? "text-success"
                            : "text-muted"
                      }
                    >
                      {j.status}
                    </span>
                  </div>
                  <div className="text-xs text-muted">
                    {new Date(j.created_at).toLocaleString("en-IN")}
                  </div>
                  {j.error && (
                    <div className="mt-1 text-xs text-danger">
                      {j.error.slice(0, 120)}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
