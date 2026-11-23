"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import type { ReceiptData } from "@/components/data/receipt-pdf";
import { EmptyState, Skeleton } from "@/components/data/primitives";
import { buttonCls, inputCls } from "@/components/layout/auth-card";
import { api } from "@/lib/api-client";
import type { components } from "@/lib/api-schema";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import { formatMoney } from "@/lib/format";

type Product = components["schemas"]["ProductOut"];
type Line = { product: Product; qty: number; price: string; discount: string; gst: string };

async function fetchProducts(): Promise<Product[]> {
  const out: Product[] = [];
  for (let page = 1; ; page++) {
    const { data } = await api.GET("/api/v1/products", { params: { query: { page, page_size: 200 } } });
    if (!data) throw new Error("products failed");
    out.push(...data.items);
    if (out.length >= data.total || data.items.length === 0) return out;
  }
}

export default function PosPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const searchRef = useRef<HTMLInputElement>(null);
  const [pickedLocation, setPickedLocation] = useState("");
  const [q, setQ] = useState("");
  const [cart, setCart] = useState<Line[]>([]);
  const [payment, setPayment] = useState<"cash" | "card" | "upi">("cash");
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<ReceiptData | null>(null);

  const locations = useQuery({
    queryKey: ["locations-all"],
    queryFn: async () => (await api.GET("/api/v1/locations", { params: { query: { page_size: 200, type: "store" } } })).data?.items ?? [],
  });
  const locationId = pickedLocation || locations.data?.[0]?.id || "";
  const products = useQuery({ queryKey: ["products-all"], queryFn: fetchProducts });
  const stock = useQuery({
    queryKey: ["stock", locationId],
    enabled: !!locationId,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/inventory", { params: { query: { page_size: 200, location_id: locationId } } });
      return new Map((data?.items ?? []).map((i) => [i.product_id, i.on_hand]));
    },
  });

  const matches = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    return (products.data ?? []).filter((p) => p.sku.toLowerCase().includes(t) || p.name.toLowerCase().includes(t)).slice(0, 8);
  }, [q, products.data]);

  const add = (p: Product) => {
    setCart((c) => {
      const i = c.findIndex((l) => l.product.id === p.id);
      if (i >= 0) return c.map((l, j) => (j === i ? { ...l, qty: l.qty + 1 } : l));
      return [...c, { product: p, qty: 1, price: "", discount: "0", gst: "5" }];
    });
    setQ("");
    searchRef.current?.focus();
  };

  const totals = useMemo(() => {
    let sub = 0;
    for (const l of cart) sub += l.qty * Number(l.price || 0) * (1 - Number(l.discount || 0) / 100) * (1 + Number(l.gst || 0) / 100);
    return sub;
  }, [cart]);

  const currency = me?.orgs ? "INR" : "INR";
  const invalid = cart.length === 0 || cart.some((l) => !(Number(l.price) > 0) || l.qty < 1);

  async function checkout() {
    if (invalid || busy || !locationId) return;
    setBusy(true);
    const { data, error } = await api.POST("/api/v1/pos/checkout", {
      params: { header: { "idempotency-key": crypto.randomUUID() } },
      body: {
        location_id: locationId,
        payment_method: payment,
        lines: cart.map((l) => ({
          product_id: l.product.id,
          qty: l.qty,
          unit_price: l.price,
          discount_pct: l.discount,
          gst_pct: l.gst,
        })) as never,
      },
    });
    setBusy(false);
    if (!data) return toast.error(apiMessage(error));
    const names = new Map(cart.map((l) => [l.product.id, l.product.name]));
    setReceipt({
      receipt_id: data.receipt_id,
      location: locations.data?.find((l) => l.id === locationId)?.name ?? "",
      payment_method: data.payment_method,
      created_at: data.created_at,
      lines: data.lines.map((l) => ({ ...l, name: names.get(l.product_id) ?? "" })),
      subtotal: data.subtotal,
      discount: data.discount,
      gst: data.gst,
      total: data.total,
      currency,
    });
    setCart([]);
    qc.invalidateQueries({ queryKey: ["stock"] });
    qc.invalidateQueries({ queryKey: ["inventory-rows"] });
    toast.success(`Sale complete: ${formatMoney(data.total, currency)}`);
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (e.key === "/" && tag !== "INPUT" && tag !== "SELECT") {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if (e.key === "F9") {
        e.preventDefault();
        void checkout();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function downloadPdf() {
    if (!receipt) return;
    const [{ pdf }, { ReceiptDoc }] = await Promise.all([import("@react-pdf/renderer"), import("@/components/data/receipt-pdf")]);
    const blob = await pdf(<ReceiptDoc r={receipt} />).toBlob();
    const url = URL.createObjectURL(blob);
    Object.assign(document.createElement("a"), { href: url, download: `receipt-${receipt.receipt_id.slice(0, 8)}.pdf` }).click();
    URL.revokeObjectURL(url);
  }

  if (locations.isLoading || products.isLoading) return <Skeleton className="h-64" />;
  if (!locations.data?.length) return <EmptyState title="No stores yet" hint="Add a store during onboarding or in Settings, then load products." />;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_420px]">
      <section>
        <h1 className="text-2xl font-semibold">POS</h1>
        <p className="mb-3 mt-1 text-sm text-muted">
          Press <kbd>/</kbd> to search, <kbd>Enter</kbd> to add the first match, <kbd>F9</kbd> to check out.
        </p>
        <div className="flex gap-3">
          <select aria-label="Store" className={`${inputCls} max-w-56`} value={locationId} onChange={(e) => setPickedLocation(e.target.value)}>
            {locations.data.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
          <input
            ref={searchRef}
            aria-label="Search products"
            placeholder="Search SKU or name..."
            className={inputCls}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && matches[0] && add(matches[0])}
          />
        </div>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {matches.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => add(p)} className="w-full card p-3 text-left text-sm hover:bg-surface-2">
                <div className="font-medium">{p.name}</div>
                <div className="text-muted">
                  {p.sku} · in stock: {stock.data?.get(p.id) ?? 0}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card p-4">
        <h2 className="font-medium">Cart</h2>
        {cart.length === 0 ? (
          <p className="mt-3 text-sm text-muted">Empty. Search a product to add it.</p>
        ) : (
          <ul className="mt-3 space-y-3 text-sm">
            {cart.map((l, i) => (
              <li key={l.product.id} className="rounded-card border border-border p-2">
                <div className="flex justify-between font-medium">
                  <span>{l.product.name}</span>
                  <button aria-label={`Remove ${l.product.name}`} className="text-danger" onClick={() => setCart(cart.filter((_, j) => j !== i))}>
                    ✕
                  </button>
                </div>
                <div className="mt-2 grid grid-cols-4 gap-2">
                  {(
                    [
                      ["Qty", "qty"],
                      ["Price", "price"],
                      ["Disc %", "discount"],
                      ["GST %", "gst"],
                    ] as const
                  ).map(([label, key]) => (
                    <label key={key} className="text-xs text-muted">
                      {label}
                      <input
                        inputMode="decimal"
                        aria-label={`${label} for ${l.product.name}`}
                        className="mt-0.5 w-full rounded border border-border bg-bg px-1.5 py-1 text-sm text-[var(--text)]"
                        value={String(l[key])}
                        onChange={(e) =>
                          setCart(cart.map((x, j) => (j === i ? { ...x, [key]: key === "qty" ? Math.max(1, Math.floor(Number(e.target.value) || 1)) : e.target.value } : x)))
                        }
                      />
                    </label>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="mt-4 flex items-center justify-between">
          <select aria-label="Payment method" className="input !w-auto !py-1.5 !text-[13px]" value={payment} onChange={(e) => setPayment(e.target.value as typeof payment)}>
            <option value="cash">Cash</option>
            <option value="card">Card</option>
            <option value="upi">UPI</option>
          </select>
          <span className="text-lg font-semibold tabular-nums">{formatMoney(totals, currency)}</span>
        </div>
        <button className={`${buttonCls} mt-3`} disabled={invalid || busy} onClick={checkout}>
          {busy ? "Processing..." : "Check out (F9)"}
        </button>
        <p className="mt-2 text-xs text-muted">Unit price is entered per line until a price master exists. Totals shown are indicative; the server computes the final receipt.</p>

        {receipt && (
          <div className="mt-4 rounded-card border border-success/40 p-3 text-sm" role="status">
            <div className="font-medium">Receipt #{receipt.receipt_id.slice(0, 8)}</div>
            <div className="tabular-nums">Total {formatMoney(receipt.total, currency)}</div>
            <button className="mt-2 text-primary underline" onClick={downloadPdf}>
              Download PDF
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
