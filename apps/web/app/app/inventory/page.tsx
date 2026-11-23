"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import dynamic from "next/dynamic";
import { DataTable } from "@/components/data/data-table";
import { EmptyState, PageHeader, Skeleton, StatusChip } from "@/components/data/primitives";
import { useGlobalFilter } from "@/components/layout/global-filter";
import { api } from "@/lib/api-client";
import { useMemo } from "react";
import { formatNumber } from "@/lib/format";

const HeatmapGrid = dynamic(() => import("@/components/charts/heatmap-grid").then((m) => m.HeatmapGrid), {
  ssr: false,
  loading: () => <Skeleton className="h-64" />,
});

type Row = { location_id: string; location: string; category: string; sku: string; product: string; on_hand: number; on_order: number };

type PageOf<T> = { items: T[]; total: number };

/** Fetch every page of a list endpoint (API caps page_size at 200). */
async function fetchAll<T>(get: (page: number) => Promise<PageOf<T> | undefined>): Promise<T[]> {
  const out: T[] = [];
  for (let page = 1; ; page++) {
    const res = await get(page);
    if (!res) throw new Error("request failed");
    out.push(...res.items);
    if (out.length >= res.total || res.items.length === 0) return out;
  }
}

async function loadRows(): Promise<Row[]> {
  const q = (page: number) => ({ params: { query: { page, page_size: 200 } } });
  const [inv, products, locations] = await Promise.all([
    fetchAll((p) => api.GET("/api/v1/inventory", q(p)).then((r) => r.data)),
    fetchAll((p) => api.GET("/api/v1/products", q(p)).then((r) => r.data)),
    fetchAll((p) => api.GET("/api/v1/locations", q(p)).then((r) => r.data)),
  ]);
  const prod = new Map(products.map((p) => [p.id, p]));
  const loc = new Map(locations.map((l) => [l.id, l.name]));
  return inv.map((i) => ({
    location_id: i.location_id,
    location: loc.get(i.location_id) ?? "?",
    category: prod.get(i.product_id)?.category ?? "?",
    sku: prod.get(i.product_id)?.sku ?? "?",
    product: prod.get(i.product_id)?.name ?? "?",
    on_hand: i.on_hand,
    on_order: i.on_order,
  }));
}

const columns: ColumnDef<Row>[] = [
  { accessorKey: "location", header: "Location" },
  { accessorKey: "sku", header: "SKU", size: 120 },
  { accessorKey: "product", header: "Product", size: 220 },
  { accessorKey: "on_hand", header: "On hand", size: 100, cell: (c) => <span className="tabular-nums">{formatNumber(c.getValue<number>())}</span> },
  { accessorKey: "on_order", header: "On order", size: 100, cell: (c) => <span className="tabular-nums">{formatNumber(c.getValue<number>())}</span> },
  {
    id: "status",
    header: "Status",
    size: 100,
    accessorFn: (r) => (r.on_hand === 0 ? "out" : "in stock"),
    cell: (c) => <StatusChip tone={c.getValue<string>() === "out" ? "bad" : "ok"}>{c.getValue<string>()}</StatusChip>,
  },
];

export default function InventoryPage() {
  const { data: all, isLoading, isError } = useQuery({ queryKey: ["inventory-rows"], queryFn: loadRows });
  const { store } = useGlobalFilter();
  const data = useMemo(() => (store ? all?.filter((r) => r.location_id === store) : all), [all, store]);
  const cells = useMemo(() => {
    const sums = new Map<string, number>();
    for (const r of all ?? []) sums.set(`${r.location}|${r.category}`, (sums.get(`${r.location}|${r.category}`) ?? 0) + r.on_hand);
    return [...sums].map(([k, value]) => ({ y: k.split("|")[0]!, x: k.split("|")[1]!, value }));
  }, [all]);
  return (
    <div>
      <PageHeader title="Inventory" description="On-hand and on-order by location. Reorder points and days of cover arrive with the policy engine." />
      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : isError ? (
        <EmptyState title="Could not load inventory" hint="Check that the API is running and you are signed in." />
      ) : data && data.length > 0 ? (
        <>
          <section className="mb-6 card p-4" aria-labelledby="heat">
            <h2 id="heat" className="mb-2 font-medium">
              On-hand units by store and category
            </h2>
            <HeatmapGrid cells={cells} xLabel="Category" yLabel="Store" unit="units" />
          </section>
          <DataTable data={data} columns={columns} filename="inventory" />
        </>
      ) : (
        <EmptyState title="No inventory yet" hint="Load the demo tenant (make seed-demo) or upload a CSV." />
      )}
    </div>
  );
}
