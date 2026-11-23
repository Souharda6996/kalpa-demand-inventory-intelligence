"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import { API_URL, api, refreshSession } from "@/lib/api-client";
import { createPoForAlert } from "@/lib/alerts";
import {
  type AlertEvent,
  applyEvent,
  type Live,
  type LiveEvent,
  type SaleEvent,
} from "@/lib/live";
import { useMe } from "@/lib/auth";

export type StreamStatus = "connecting" | "live" | "reconnecting";
export type StreamMessage = LiveEvent | { type: "reconnected" };
type Listener = (m: StreamMessage) => void;
type Ctx = { status: StreamStatus; subscribe: (l: Listener) => () => void };

const StreamCtx = createContext<Ctx | null>(null);
export const LIVE_KEY = ["live-state"] as const;
const EVENT_TYPES = ["sale", "alert", "job", "sim"] as const;
const TOAST_BURST = 3; // at most this many alert toasts per window, then one summary toast
const TOAST_WINDOW_MS = 10_000;

/** Subscribe to every live event (sale, alert, job, sim) and to "reconnected". */
export function useLiveEvents(handler: (m: StreamMessage) => void) {
  const ctx = useContext(StreamCtx);
  const ref = useRef(handler);
  useEffect(() => {
    ref.current = handler; // always call the latest handler without resubscribing
  });
  useEffect(() => ctx?.subscribe((m) => ref.current(m)), [ctx]);
}

export function useStreamStatus(): StreamStatus {
  return useContext(StreamCtx)?.status ?? "connecting";
}

/** The shared live state (today's totals, feed, alerts, stock watch, simulator). Written only by the provider. */
export function useLiveState() {
  return useQuery<Live>({
    queryKey: LIVE_KEY,
    staleTime: Infinity,
    queryFn: fetchLive,
  });
}

async function fetchLive(): Promise<Live> {
  const { data } = await api.GET("/api/v1/live/state");
  if (!data) throw new Error("live state failed");
  return {
    ...data,
    feed: data.feed as unknown as SaleEvent[],
    alerts: data.alerts as unknown as AlertEvent[],
  };
}

/**
 * One EventSource for the whole app shell. It reconnects by itself after a dropped connection (the server sends
 * `retry: 2000`); if the browser gives up (HTTP error, typically the 15-minute access cookie) it renews the session
 * and reopens with back-off. After every reconnect the live state is re-read, so nothing missed during the gap is lost.
 */
export function EventStreamProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const router = useRouter();
  const { data: me } = useMe();
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const listeners = useRef(new Set<Listener>());
  const burst = useRef<number[]>([]);
  const canOrder = !!me?.permissions.includes("create_po");

  useQuery<Live>({
    queryKey: LIVE_KEY,
    staleTime: Infinity,
    queryFn: fetchLive,
    enabled: !!me,
  });

  const toastAlert = useCallback(
    (a: AlertEvent) => {
      if (a.kind === "drift") {
        toast(a.title ?? "Model drift detected", {
          id: a.id,
          description: a.detail,
          action: {
            label: "Open Models",
            onClick: () => router.push("/app/models"),
          },
        });
        return;
      }
      const now = Date.now();
      burst.current = burst.current.filter((t) => now - t < TOAST_WINDOW_MS);
      if (burst.current.length >= TOAST_BURST) {
        toast("More items crossed their reorder point", {
          id: "alert-batch",
          description: "Several alerts arrived at once.",
          action: {
            label: "Review",
            onClick: () => router.push("/app/replenishment"),
          },
        });
        return;
      }
      burst.current.push(now);
      toast(`${a.product} is below its reorder point`, {
        id: a.id,
        duration: 12_000,
        description: `${a.location}: ${a.on_hand} on hand, reorder point ${a.reorder_point}`,
        action: canOrder
          ? {
              label: "Create PO",
              onClick: () => {
                void createPoForAlert(a).then((r) => {
                  if (r.ok)
                    toast.success(
                      `Draft PO created: ${r.qty} units from ${r.supplier}`,
                      {
                        action: {
                          label: "Open",
                          onClick: () => router.push("/app/purchase-orders"),
                        },
                      },
                    );
                  else toast.error(r.message);
                });
              },
            }
          : {
              label: "Replenishment",
              onClick: () => router.push("/app/replenishment"),
            },
      });
    },
    [canOrder, router],
  );

  useEffect(() => {
    if (!me) return;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let wasDown = false;
    let attempt = 0;
    const set = listeners.current;
    const emit = (m: StreamMessage) => set.forEach((l) => l(m));

    const open = () => {
      if (stopped) return;
      const source = new EventSource(`${API_URL}/api/v1/stream/events`, {
        withCredentials: true,
      });
      es = source;
      source.onopen = () => {
        attempt = 0;
        setStatus("live");
        if (wasDown) {
          wasDown = false;
          void qc.invalidateQueries({ queryKey: LIVE_KEY }); // catch up on what happened while disconnected
          emit({ type: "reconnected" });
        }
      };
      for (const t of EVENT_TYPES) {
        source.addEventListener(t, (e) => {
          try {
            emit(JSON.parse((e as MessageEvent<string>).data) as LiveEvent);
          } catch {
            /* a malformed frame is skipped, the stream stays up */
          }
        });
      }
      source.onerror = () => {
        wasDown = true;
        setStatus("reconnecting");
        if (source.readyState !== EventSource.CLOSED) return; // the browser is retrying by itself
        source.close();
        const delay = Math.min(30_000, 1500 * 2 ** attempt++);
        timer = setTimeout(() => void refreshSession().finally(open), delay); // expired cookie or a longer outage
      };
    };
    open();
    return () => {
      stopped = true;
      es?.close();
      clearTimeout(timer);
    };
  }, [me, qc]);

  useEffect(() => {
    const off = (m: StreamMessage) => {
      if (m.type === "reconnected") return;
      qc.setQueryData<Live>(LIVE_KEY, (old) =>
        old ? applyEvent(old, m) : old,
      );
      if (m.type === "alert") toastAlert(m);
    };
    const set = listeners.current;
    set.add(off);
    return () => void set.delete(off);
  }, [qc, toastAlert]);

  const value = useMemo<Ctx>(
    () => ({
      status,
      subscribe: (l) => {
        listeners.current.add(l);
        return () => void listeners.current.delete(l);
      },
    }),
    [status],
  );
  return <StreamCtx.Provider value={value}>{children}</StreamCtx.Provider>;
}
