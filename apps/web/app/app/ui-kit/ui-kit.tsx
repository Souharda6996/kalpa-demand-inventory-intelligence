"use client";

import type { ColumnDef } from "@tanstack/react-table";
import { FanChart, type FanSeries } from "@/components/charts/fan-chart";
import { DataTable } from "@/components/data/data-table";
import { EmptyState, KpiTile, Skeleton, StatusChip } from "@/components/data/primitives";
import { formatMoney, formatNumber } from "@/lib/format";

// SAMPLE values for visual testing only (this page is dev-only and never shown with real tenant data).
const dates = Array.from({ length: 42 }, (_, i) => `D${i + 1}`);
const sample: FanSeries = {
  dates,
  todayIndex: 28,
  actual: dates.map((_, i) => (i <= 28 ? 100 + 20 * Math.sin(i / 3) : null)),
  p50: dates.map((_, i) => (i >= 28 ? 100 + 20 * Math.sin(i / 3) : null)),
  p10: dates.map((_, i) => (i >= 28 ? 80 + 20 * Math.sin(i / 3) - (i - 28) : null)),
  p90: dates.map((_, i) => (i >= 28 ? 120 + 20 * Math.sin(i / 3) + (i - 28) : null)),
};
type Row = { id: number; name: string; value: number };
const rows: Row[] = Array.from({ length: 500 }, (_, i) => ({ id: i + 1, name: `Sample row ${i + 1}`, value: (i * 7919) % 1000 }));
const cols: ColumnDef<Row>[] = [
  { accessorKey: "id", header: "#", size: 80 },
  { accessorKey: "name", header: "Name", size: 240 },
  { accessorKey: "value", header: "Value", size: 120 },
];

export function UiKit() {
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">UI kit (dev only, sample data)</h1>
      <section className="grid gap-4 sm:grid-cols-3">
        <KpiTile label="Actual (sample)" value={formatNumber(123456)} delta={{ value: "+4.2%", positive: true }} source="sample" spark={[3, 5, 4, 7, 6, 9]} />
        <KpiTile label="Forecast (sample)" kind="forecast" value={formatMoney(1234567)} delta={{ value: "-1.1%", positive: false }} source="sample" spark={[9, 7, 8, 5, 6, 3]} />
        <Skeleton className="h-[120px]" />
      </section>
      <section className="flex gap-2">
        <StatusChip tone="ok">ok</StatusChip>
        <StatusChip tone="warn">warn</StatusChip>
        <StatusChip tone="bad">bad</StatusChip>
        <StatusChip tone="neutral">neutral</StatusChip>
      </section>
      <section className="card p-4">
        <FanChart series={sample} />
      </section>
      <section>
        <p className="mb-2 text-sm text-muted">500 rows, virtualised:</p>
        <DataTable data={rows} columns={cols} filename="sample" />
      </section>
      <EmptyState title="Empty state" hint="Shown when a list has no rows." />
    </div>
  );
}
