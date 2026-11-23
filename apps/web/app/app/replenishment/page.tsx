"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { EmptyState, PageHeader, Skeleton, StatusChip } from "@/components/data/primitives";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import { formatMoney, formatNumber } from "@/lib/format";

type Rec = components["schemas"]["Recommendation"];
const key = (r: Rec) => `${r.location_id}|${r.product_id}`;
const PAGE = 40; // rows shown at a time; the list is already sorted by value at risk

export default function ReplenishmentPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [qty, setQty] = useState<Record<string, number>>({});
  const [supplier, setSupplier] = useState("");
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const canCreate = me?.permissions.includes("create_po");

  const recs = useQuery({
    queryKey: ["recommendations"],
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/v1/policy/recommendations");
      if (response.status === 404) return null;
      if (!data) throw new Error(apiMessage(error));
      return data;
    },
  });

  const all = useMemo(() => (recs.data?.items ?? []).filter((r) => !supplier || r.supplier_id === supplier), [recs.data, supplier]);
  const items = useMemo(() => all.slice(0, shown), [all, shown]);
  const suppliers = useMemo(() => [...new Map((recs.data?.items ?? []).map((r) => [r.supplier_id, r.supplier])).entries()], [recs.data]);
  const bySupplier = useMemo(() => {
    const m = new Map<string, Rec[]>();
    for (const r of items) m.set(r.supplier, [...(m.get(r.supplier) ?? []), r]);
    return [...m.entries()];
  }, [items]);

  const qtyOf = (r: Rec) => qty[key(r)] ?? r.recommended_qty;
  const invalid = (r: Rec) => qtyOf(r) % r.pack_size !== 0 || qtyOf(r) < r.moq;
  const chosen = items.filter((r) => selected.has(key(r)));

  async function createPos() {
    setBusy(true);
    const groups = new Map<string, Rec[]>();
    for (const r of chosen) groups.set(`${r.supplier_id}|${r.location_id}`, [...(groups.get(`${r.supplier_id}|${r.location_id}`) ?? []), r]);
    let made = 0;
    for (const [, rows] of groups) {
      const first = rows[0]!;
      const { error } = await api.POST("/api/v1/purchase-orders", {
        body: {
          supplier_id: first.supplier_id,
          location_id: first.location_id,
          lines: rows.map((r) => ({ product_id: r.product_id, qty: qtyOf(r), reason: { chips: r.reasons } })),
        },
      });
      if (error) toast.error(`${first.supplier} / ${first.location}: ${apiMessage(error)}`);
      else made++;
    }
    setBusy(false);
    if (made) {
      toast.success(`${made} purchase order${made > 1 ? "s" : ""} created as drafts`);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["pos"] });
    }
  }

  if (recs.isLoading) return <Skeleton className="h-64" />;
  if (recs.data === null)
    return (
      <div>
        <PageHeader title="Replenishment" />
        <EmptyState
          icon="truck"
          title="No forecasts yet"
          hint="Recommendations need a forecast. Run the backtest first, and make sure products have supplier terms (lead time, MOQ, pack size)."
          action={
            <Link href="/app/forecasts" className="btn btn-primary">
              Go to Forecasts
            </Link>
          }
        />
      </div>
    );
  if (recs.isError || !recs.data) return <EmptyState title="Could not load recommendations" />;

  return (
    <div>
      <PageHeader
        title="Replenishment"
        description={`What to order, from ${recs.data.is_champion ? "the champion model" : "the best-WAPE model"} (${recs.data.model.replaceAll("_", " ")}), as of ${recs.data.as_of}. Quantities are whole packs and respect each supplier's MOQ.`}
        actions={
          <>
            <select aria-label="Supplier filter" className="input !w-auto" value={supplier} onChange={(e) => {
                setSupplier(e.target.value);
                setShown(PAGE);
              }}>
              <option value="">All suppliers</option>
              {suppliers.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
            {canCreate && (
              <button className="btn btn-primary" disabled={!chosen.length || busy || chosen.some(invalid)} onClick={createPos}>
                {busy ? "Creating..." : `Create POs (${chosen.length})`}
              </button>
            )}
          </>
        }
      />
      {!items.length ? (
        <EmptyState icon="box" title="Nothing needs ordering" hint="Every stocked product is covered for its protection period at the target service level." />
      ) : (
        <div className="space-y-6">
          {bySupplier.map(([name, rows]) => (
            <section key={name} className="card overflow-hidden" aria-label={`Supplier ${name}`}>
              <div className="flex items-center gap-3 border-b border-border bg-surface-2 px-4 py-2.5">
                <h2 className="font-medium">{name}</h2>
                <StatusChip tone="neutral">
                  {rows.length} item{rows.length > 1 ? "s" : ""}
                </StatusChip>
                <span className="ml-auto text-sm text-muted">
                  Order value {formatMoney(rows.reduce((a, r) => a + qtyOf(r) * r.unit_cost, 0))}
                </span>
              </div>
              <div className="overflow-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-muted">
                    <tr>
                      <th className="w-10 px-3 py-2" scope="col">
                        <span className="sr-only">Select</span>
                      </th>
                      {["Product", "Store", "On hand", "Cover", "Stock-out", "Order qty", "At risk", "Why"].map((h) => (
                        <th key={h} scope="col" className="px-3 py-2 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={key(r)} className="border-t border-border align-top">
                        <td className="px-3 py-2.5">
                          <input
                            type="checkbox"
                            aria-label={`Select ${r.product} at ${r.location}`}
                            checked={selected.has(key(r))}
                            onChange={(e) => {
                              const n = new Set(selected);
                              if (e.target.checked) n.add(key(r));
                              else n.delete(key(r));
                              setSelected(n);
                            }}
                          />
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="font-medium">{r.product}</div>
                          <div className="text-xs text-muted">{r.sku}</div>
                        </td>
                        <td className="px-3 py-2.5">{r.location}</td>
                        <td className="px-3 py-2.5 tabular-nums">{formatNumber(r.on_hand)}</td>
                        <td className="px-3 py-2.5 tabular-nums">{r.days_of_cover == null ? "n/a" : `${r.days_of_cover} d`}</td>
                        <td className="px-3 py-2.5">{r.stockout_date ?? "none in 28 d"}</td>
                        <td className="px-3 py-2.5">
                          <input
                            type="number"
                            min={0}
                            step={r.pack_size}
                            aria-label={`Order quantity for ${r.product} at ${r.location}`}
                            aria-invalid={invalid(r)}
                            className={`input !w-24 !py-1 tabular-nums ${invalid(r) ? "!border-danger" : ""}`}
                            value={qtyOf(r)}
                            onChange={(e) => setQty({ ...qty, [key(r)]: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
                          />
                          <div className={`mt-1 text-xs ${invalid(r) ? "text-danger" : "text-muted"}`}>
                            packs of {r.pack_size}, MOQ {r.moq}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 tabular-nums">{formatMoney(r.value_at_risk)}</td>
                        <td className="px-3 py-2.5">
                          <div className="flex max-w-xs flex-wrap gap-1">
                            {r.reasons.slice(0, 2).map((t) => (
                              <StatusChip key={t} tone="neutral">
                                {t}
                              </StatusChip>
                            ))}
                            {r.reasons.length > 2 && (
                              <span title={r.reasons.slice(2).join(" · ")}>
                                <StatusChip tone="neutral">+{r.reasons.length - 2} more</StatusChip>
                              </span>
                            )}
                            {r.over_capacity && <StatusChip tone="bad">over shelf capacity</StatusChip>}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
          {all.length > items.length && (
            <div className="flex items-center justify-center gap-3">
              <span className="text-sm text-muted">
                Showing {items.length} of {all.length}, highest value at risk first
              </span>
              <button className="btn btn-secondary btn-sm" onClick={() => setShown(shown + PAGE)}>
                Show {Math.min(PAGE, all.length - items.length)} more
              </button>
            </div>
          )}
          <p className="text-xs text-muted">
            ₹ at risk = units the forecast says will be short during the supplier lead time, valued at cost. Service level comes from the newsvendor
            critical ratio using your cost parameters (Settings).
          </p>
        </div>
      )}
    </div>
  );
}
