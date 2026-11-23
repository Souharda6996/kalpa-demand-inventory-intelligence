import { describe, expect, it } from "vitest";
import {
  type AlertEvent,
  applyEvent,
  FEED_MAX,
  type Live,
  type SaleEvent,
  type SimEvent,
  WATCH_MAX,
} from "@/lib/live";

const sim0 = {
  available: true,
  running: false,
  day_index: 0,
  checkouts: 0,
  units: 0,
  revenue: 0,
  lost_units: 0,
  errors: 0,
  multiplier: 1,
  day_seconds: 240,
};
const base: Live = {
  date: "2026-01-01",
  revenue: 100,
  units: 10,
  feed: [],
  alerts: [],
  watch: [],
  sim: sim0,
};

const sale = (o: Partial<SaleEvent> = {}): SaleEvent => ({
  type: "sale",
  id: "s1",
  ts: "2026-01-01T10:00:00Z",
  location_id: "L1",
  location: "Koramangala",
  date: "2026-01-01",
  total: 105,
  revenue: 100,
  units: 4,
  simulated: false,
  stock: [],
  ...o,
});
const row = (
  sku: string,
  on_hand: number,
  reorder_point: number,
  loc = "L1",
) => ({
  location_id: loc,
  location: "Store",
  product_id: sku,
  product: sku,
  sku,
  on_hand,
  reorder_point,
});

describe("applyEvent", () => {
  it("adds a sale of the same business day to the ticking totals and the feed (newest first)", () => {
    const a = applyEvent(base, sale({ id: "a", revenue: 40, units: 2 }));
    const b = applyEvent(a, sale({ id: "b", revenue: 60, units: 3 }));
    expect([b.revenue, b.units, b.date]).toEqual([200, 15, "2026-01-01"]);
    expect(b.feed.map((f) => f.id)).toEqual(["b", "a"]);
  });

  it("starts the totals over when the simulated day moves on, and ignores a late sale of an older day", () => {
    const next = applyEvent(
      base,
      sale({ date: "2026-01-02", revenue: 30, units: 1 }),
    );
    expect([next.date, next.revenue, next.units]).toEqual([
      "2026-01-02",
      30,
      1,
    ]);
    const late = applyEvent(
      next,
      sale({ date: "2026-01-01", revenue: 999, units: 99 }),
    );
    expect([late.date, late.revenue, late.units]).toEqual([
      "2026-01-02",
      30,
      1,
    ]);
  });

  it("caps the feed", () => {
    let s = base;
    for (let i = 0; i < FEED_MAX + 5; i++)
      s = applyEvent(s, sale({ id: `e${i}` }));
    expect(s.feed).toHaveLength(FEED_MAX);
    expect(s.feed[0]?.id).toBe(`e${FEED_MAX + 4}`);
  });

  it("keeps the stock watch sorted by headroom, updates known rows and lets a newly low series in", () => {
    const start: Live = {
      ...base,
      watch: [row("A", 30, 10), row("B", 12, 10)],
    };
    const s = applyEvent(
      start,
      sale({
        stock: [
          {
            product_id: "B",
            product: "B",
            sku: "B",
            on_hand: 9,
            reorder_point: 10,
          },
          {
            product_id: "C",
            product: "C",
            sku: "C",
            on_hand: 5,
            reorder_point: 10,
          },
        ],
      }),
    );
    expect(s.watch.map((w) => [w.sku, w.on_hand])).toEqual([
      ["C", 5],
      ["B", 9],
      ["A", 30],
    ]); // headroom -5, -1, 20
    let full = start;
    for (let i = 0; i < WATCH_MAX + 3; i++)
      full = applyEvent(
        full,
        sale({
          stock: [
            {
              product_id: `P${i}`,
              product: `P${i}`,
              sku: `P${i}`,
              on_hand: 50 + i,
              reorder_point: 10,
            },
          ],
        }),
      );
    expect(full.watch).toHaveLength(WATCH_MAX);
    expect(full.watch[0]?.sku).toBe("B"); // the genuinely lowest headroom stays first
  });

  it("ignores stock lines without a reorder point (nothing to compare with)", () => {
    const s = applyEvent(
      base,
      sale({
        stock: [
          {
            product_id: "X",
            product: "X",
            sku: "X",
            on_hand: 1,
            reorder_point: null,
          },
        ],
      }),
    );
    expect(s.watch).toEqual([]);
  });

  it("records an alert once, newest first", () => {
    const a: AlertEvent = {
      type: "alert",
      id: "al1",
      ts: "t",
      kind: "reorder",
      product: "Jalebi",
      on_hand: 9,
      reorder_point: 10,
    };
    const s = applyEvent(applyEvent(base, a), a);
    expect(s.alerts).toHaveLength(1);
    expect(applyEvent(s, { ...a, id: "al2" }).alerts.map((x) => x.id)).toEqual([
      "al2",
      "al1",
    ]);
  });

  it("follows the simulator and resets the day's totals when its business day advances", () => {
    const run: SimEvent = {
      type: "sim",
      ...sim0,
      running: true,
      sim_date: "2026-01-02",
      clock: "08:00",
      checkouts: 3,
    };
    const s = applyEvent(base, run);
    expect([
      s.sim.running,
      s.sim.checkouts,
      s.sim.available,
      s.date,
      s.revenue,
      s.units,
    ]).toEqual([true, 3, true, "2026-01-02", 0, 0]);
    const stopped = applyEvent(s, { ...run, running: false });
    expect(stopped.sim.running).toBe(false);
    expect(stopped.date).toBe("2026-01-02"); // stopping does not reset the totals
  });

  it("follows the simulator even when its calendar is EARLIER than the real date (the bug that froze revenue at 0)", () => {
    const real: Live = { ...base, date: "2026-11-22", revenue: 0, units: 0 };
    const run: SimEvent = {
      type: "sim",
      ...sim0,
      running: true,
      sim_date: "2026-01-01",
      clock: "08:00",
    };
    const started = applyEvent(real, run);
    expect([started.date, started.revenue]).toEqual(["2026-01-01", 0]);
    const a = applyEvent(
      started,
      sale({ date: "2026-01-01", revenue: 40, units: 2, simulated: true }),
    );
    const b = applyEvent(
      a,
      sale({ date: "2026-01-01", revenue: 60, units: 3, simulated: true }),
    );
    expect([b.date, b.revenue, b.units]).toEqual(["2026-01-01", 100, 5]);
    // a simulated sale arriving before the first sim event is adopted too, but only while the simulator is running
    const early = applyEvent(
      { ...real, sim: { ...sim0, running: true } },
      sale({ date: "2026-01-01", revenue: 25, units: 1, simulated: true }),
    );
    expect([early.date, early.revenue]).toEqual(["2026-01-01", 25]);
    expect(
      applyEvent(
        real,
        sale({ date: "2026-01-01", revenue: 25, simulated: true }),
      ).revenue,
    ).toBe(0); // stopped: ignored
  });

  it("does not touch the state for job events", () => {
    expect(
      applyEvent(base, {
        type: "job",
        id: "j",
        job_type: "backtest",
        status: "running",
        progress: 0.5,
      }),
    ).toBe(base);
  });
});
