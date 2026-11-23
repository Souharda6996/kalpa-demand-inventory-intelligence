import type { components } from "./api-schema";

/** Live state as served by GET /live/state, with the loosely typed feed/alerts lists given real types. */
export type SimState = components["schemas"]["SimState"];
export type WatchRow = components["schemas"]["WatchRow"];

export type SaleEvent = {
  type: "sale";
  id: string;
  ts: string;
  location_id: string;
  location: string;
  date: string;
  total: number;
  revenue: number;
  units: number;
  simulated: boolean;
  stock: {
    product_id: string;
    product: string;
    sku: string;
    on_hand: number;
    reorder_point: number | null;
  }[];
};
export type AlertEvent = {
  type: "alert";
  id: string;
  ts: string;
  kind: "reorder" | "drift";
  location_id?: string;
  location?: string;
  product_id?: string;
  product?: string;
  sku?: string;
  on_hand?: number;
  reorder_point?: number;
  title?: string;
  detail?: string;
  simulated?: boolean;
};
export type JobEvent = {
  type: "job";
  id: string;
  job_type: string;
  status: string;
  progress: number;
  line?: string;
};
export type SimEvent = { type: "sim" } & SimState;
export type LiveEvent = SaleEvent | AlertEvent | JobEvent | SimEvent;

export type Live = {
  date: string;
  revenue: number;
  units: number;
  feed: SaleEvent[];
  alerts: AlertEvent[];
  watch: WatchRow[];
  sim: SimState;
};

export const FEED_MAX = 30;
export const ALERTS_MAX = 20;
export const WATCH_MAX = 8;

const headroom = (w: WatchRow) => w.on_hand - w.reorder_point;

/** Fold one live event into the state. Pure: the Command Center and the unit tests use the same function. */
export function applyEvent(s: Live, ev: LiveEvent): Live {
  switch (ev.type) {
    case "sale": {
      // totals belong to one business day; a newer day (the simulator moved on) starts from this sale
      const sameDay = ev.date === s.date;
      // the simulator's calendar can be EARLIER than the real date, so while it runs its days win
      const newer = ev.date > s.date || (s.sim.running && ev.simulated);
      const revenue = sameDay
        ? s.revenue + ev.revenue
        : newer
          ? ev.revenue
          : s.revenue;
      const units = sameDay ? s.units + ev.units : newer ? ev.units : s.units;
      let watch = [...s.watch];
      for (const st of ev.stock) {
        const i = watch.findIndex(
          (w) =>
            w.location_id === ev.location_id && w.product_id === st.product_id,
        );
        if (st.reorder_point == null) continue;
        const row: WatchRow = {
          location_id: ev.location_id,
          location: ev.location,
          product_id: st.product_id,
          product: st.product,
          sku: st.sku,
          on_hand: st.on_hand,
          reorder_point: st.reorder_point,
        };
        if (i >= 0) watch[i] = row;
        else watch.push(row);
      }
      watch = watch
        .sort((a, b) => headroom(a) - headroom(b) || a.sku.localeCompare(b.sku))
        .slice(0, WATCH_MAX);
      return {
        ...s,
        date: sameDay || !newer ? s.date : ev.date,
        revenue,
        units,
        watch,
        feed: [ev, ...s.feed].slice(0, FEED_MAX),
      };
    }
    case "alert":
      return s.alerts.some((a) => a.id === ev.id)
        ? s
        : { ...s, alerts: [ev, ...s.alerts].slice(0, ALERTS_MAX) };
    case "sim": {
      const sim: Partial<SimEvent> = { ...ev };
      delete sim.type; // the rest is exactly SimState
      const nextDay =
        ev.running && ev.sim_date != null && ev.sim_date !== s.date;
      return {
        ...s,
        sim: { ...s.sim, ...(sim as SimState), available: s.sim.available },
        ...(nextDay
          ? { date: ev.sim_date as string, revenue: 0, units: 0 }
          : {}),
      };
    }
    default:
      return s; // job events are consumed by the Jobs page
  }
}
