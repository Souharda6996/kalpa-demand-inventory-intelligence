// @vitest-environment jsdom
import {
  cleanup,
  render,
  renderHook,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FanChart } from "@/components/charts/fan-chart";
import { useAnimatedNumber } from "@/components/data/animated-number";
import {
  EmptyState,
  KpiTile,
  Skeleton,
  StatusChip,
} from "@/components/data/primitives";

// the real chart needs a canvas, which jsdom does not have; the table fallback is what these tests are about
vi.mock("echarts-for-react/lib/core", () => ({
  default: () => <div data-testid="echart" />,
}));

afterEach(cleanup);

describe("StatusChip", () => {
  it("shows its text and uses a different colour class per tone", () => {
    const { rerender } = render(<StatusChip tone="ok">healthy</StatusChip>);
    const ok = screen.getByText("healthy").className;
    rerender(<StatusChip tone="bad">healthy</StatusChip>);
    expect(screen.getByText("healthy").className).not.toBe(ok);
    expect(ok).toContain("success");
  });
});

describe("KpiTile", () => {
  it("always says where its number came from (title and accessible label)", () => {
    render(
      <KpiTile
        label="Revenue"
        value="₹1,25,875"
        source="sum of sales_daily.revenue"
      />,
    );
    expect(screen.getByText("₹1,25,875")).toBeTruthy();
    expect(
      screen.getByTitle("Source: sum of sales_daily.revenue"),
    ).toBeTruthy();
    expect(
      screen.getByLabelText("Source: sum of sales_daily.revenue"),
    ).toBeTruthy();
  });

  it("draws a sparkline only when there are at least two points, and colours the delta by sign", () => {
    const { container, rerender } = render(
      <KpiTile label="A" value="1" source="s" spark={[1]} />,
    );
    expect(container.querySelector("svg")).toBeNull();
    rerender(
      <KpiTile
        label="A"
        value="1"
        source="s"
        spark={[1, 3, 2]}
        delta={{ value: "+4%", positive: true }}
      />,
    );
    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.getByText("+4%").className).toContain("text-success");
    rerender(
      <KpiTile
        label="A"
        value="1"
        source="s"
        delta={{ value: "-2%", positive: false }}
      />,
    );
    expect(screen.getByText("-2%").className).toContain("text-danger");
  });
});

describe("EmptyState and Skeleton", () => {
  it("explains what to do next, with the action", () => {
    render(
      <EmptyState
        title="No forecasts yet"
        hint="Run the backtest first."
        action={<button>Go</button>}
      />,
    );
    expect(screen.getByText("No forecasts yet")).toBeTruthy();
    expect(screen.getByText("Run the backtest first.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Go" })).toBeTruthy();
  });

  it("is hidden from assistive technology while loading", () => {
    const { container } = render(<Skeleton className="h-8" />);
    expect(container.firstElementChild?.getAttribute("aria-hidden")).toBe(
      "true",
    );
  });
});

describe("useAnimatedNumber", () => {
  const motion = (reduce: boolean) =>
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: reduce && q.includes("reduce"),
      addEventListener() {},
      removeEventListener() {},
      media: q,
    }));

  it("jumps straight to the new value when the user prefers reduced motion", () => {
    motion(true);
    const { result, rerender } = renderHook(({ v }) => useAnimatedNumber(v), {
      initialProps: { v: 100 },
    });
    expect(result.current).toBe(100);
    rerender({ v: 250 });
    expect(result.current).toBe(250);
  });

  it("starts from the first value and does not jump when animation is allowed", () => {
    motion(false);
    const { result, rerender } = renderHook(({ v }) => useAnimatedNumber(v), {
      initialProps: { v: 100 },
    });
    expect(result.current).toBe(100);
    rerender({ v: 250 });
    expect(result.current).toBeGreaterThanOrEqual(100); // eases towards 250; never starts below the old value
    expect(result.current).toBeLessThanOrEqual(250);
  });
});

describe("FanChart table fallback (a chart must never be the only way to read the numbers)", () => {
  const series = {
    dates: ["2026-01-01", "2026-01-02", "2026-01-03"],
    actual: [10, 12.34, null],
    p10: [null, 8, 9.04],
    p50: [null, 11.96, 12.04],
    p90: [null, 15, 15.55],
    todayIndex: 1,
  };

  it("lists every day with actual and the P10/P50/P90 values, rounded to one decimal", () => {
    render(<FanChart series={series} />);
    expect(screen.getByTestId("echart")).toBeTruthy();
    const region = screen.getByRole("region", { name: "Forecast data table" });
    const rows = within(region).getAllByRole("row");
    expect(rows).toHaveLength(4); // header + 3 days
    expect(
      within(rows[0]!)
        .getAllByRole("columnheader")
        .map((h) => h.textContent),
    ).toEqual(["Date", "Actual", "P10", "P50 (forecast)", "P90"]);
    expect(
      within(rows[2]!)
        .getAllByRole("cell")
        .map((c) => c.textContent),
    ).toEqual(["2026-01-02", "12.3", "8", "12", "15"]);
    expect(
      within(rows[3]!)
        .getAllByRole("cell")
        .map((c) => c.textContent),
    ).toEqual(["2026-01-03", "-", "9", "12", "15.6"]);
  });

  it("is keyboard reachable (a scrollable region needs a tab stop) and drops days without any value", () => {
    render(
      <FanChart
        series={{
          ...series,
          dates: [...series.dates, "2026-01-04"],
          actual: [...series.actual, null],
          p10: [...series.p10, null],
          p50: [...series.p50, null],
          p90: [...series.p90, null],
        }}
      />,
    );
    const region = screen.getByRole("region", { name: "Forecast data table" });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(within(region).getAllByRole("row")).toHaveLength(4); // the empty 4th day is not listed
  });
});
