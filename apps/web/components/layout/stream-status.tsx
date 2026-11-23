"use client";

import { useStreamStatus } from "@/components/layout/event-stream";

/** Connection light for the live stream; text is always present so colour is never the only signal. */
export function StreamStatusDot() {
  const status = useStreamStatus();
  const live = status === "live";
  return (
    <span
      role="status"
      aria-live="polite"
      title={live ? "Live updates connected" : "Live updates are reconnecting"}
      className="hidden items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted sm:inline-flex"
    >
      <span
        className={`h-2 w-2 rounded-full ${live ? "bg-success" : "bg-warning"}`}
        aria-hidden
      />
      {live ? "Live" : status === "connecting" ? "Connecting" : "Reconnecting"}
    </span>
  );
}
