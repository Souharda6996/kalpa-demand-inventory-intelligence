"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState, PageHeader, Skeleton, StatusChip } from "@/components/data/primitives";
import { api } from "@/lib/api-client";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";
import { formatMoney } from "@/lib/format";

const COLUMNS = [
  ["draft", "Draft"],
  ["approved", "Approved"],
  ["sent", "Sent"],
  ["partially_received", "Partially received"],
  ["received", "Received"],
  ["cancelled", "Cancelled"],
] as const;
const TONE = { draft: "neutral", approved: "warn", sent: "warn", partially_received: "warn", received: "ok", cancelled: "bad" } as const;

export default function PurchaseOrdersPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [recv, setRecv] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);

  const pos = useQuery({ queryKey: ["pos"], queryFn: async () => (await api.GET("/api/v1/purchase-orders")).data ?? [] });
  const open = pos.data?.find((p) => p.id === openId) ?? null;
  const can = (perm: string) => !!me?.permissions.includes(perm);

  async function act(verb: "approve" | "send" | "cancel" | "receive", body?: { lines: { line_id: string; qty: number }[] }) {
    if (!open) return;
    setBusy(true);
    const path = `/api/v1/purchase-orders/{po_id}/${verb}` as "/api/v1/purchase-orders/{po_id}/approve";
    const { error } = await api.POST(path, { params: { path: { po_id: open.id } }, ...(body ? { body } : {}) } as never);
    setBusy(false);
    if (error) return toast.error(apiMessage(error));
    toast.success(`Purchase order ${verb === "receive" ? "updated" : verb + (verb.endsWith("e") ? "d" : "ed")}`);
    setRecv({});
    qc.invalidateQueries({ queryKey: ["pos"] });
    qc.invalidateQueries({ queryKey: ["inventory-rows"] });
  }

  async function pdf() {
    if (!open) return;
    const [{ pdf: render }, { PoDoc }] = await Promise.all([import("@react-pdf/renderer"), import("@/components/data/po-pdf")]);
    const blob = await render(
      <PoDoc po={{ ...open, currency: "INR", lines: open.lines.map((l) => ({ sku: l.sku, product: l.product, qty: l.qty, unit_cost: l.unit_cost })) }} />,
    ).toBlob();
    const url = URL.createObjectURL(blob);
    Object.assign(document.createElement("a"), { href: url, download: `po-${open.id.slice(0, 8)}.pdf` }).click();
    URL.revokeObjectURL(url);
  }

  if (pos.isLoading) return <Skeleton className="h-64" />;

  return (
    <div>
      <PageHeader title="Purchase Orders" description="Draft, approve, send and receive. Sending raises on-order stock; receiving moves it to on-hand." />
      {!pos.data?.length ? (
        <EmptyState icon="layers" title="No purchase orders yet" hint="Create drafts from the Replenishment page." />
      ) : (
        <div className="grid gap-4 overflow-x-auto pb-2 lg:grid-cols-3 xl:grid-cols-6" role="list" aria-label="Purchase order board">
          {COLUMNS.map(([status, label]) => {
            const col = pos.data.filter((p) => p.status === status);
            return (
              <section key={status} role="listitem" aria-label={`${label} (${col.length})`} className="min-w-[200px]">
                <h2 className="mb-2 flex items-center gap-2 text-sm font-medium">
                  {label}
                  <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs tabular-nums text-muted">{col.length}</span>
                </h2>
                <div className="space-y-2">
                  {col.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setOpenId(p.id)}
                      className={`card card-hover w-full p-3 text-left text-sm ${openId === p.id ? "!border-primary" : ""}`}
                    >
                      <div className="font-medium">{p.supplier}</div>
                      <div className="text-xs text-muted">
                        {p.location} · {p.lines.length} line{p.lines.length > 1 ? "s" : ""}
                      </div>
                      <div className="mt-1 tabular-nums">{formatMoney(p.total_cost)}</div>
                    </button>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}

      {open && (
        <aside aria-label="Purchase order details" className="card mt-6 p-5">
          <div className="flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold">
              PO #{open.id.slice(0, 8)} · {open.supplier}
            </h2>
            <StatusChip tone={TONE[open.status as keyof typeof TONE]}>{open.status.replace("_", " ")}</StatusChip>
            <span className="text-sm text-muted">
              to {open.location} · expected {open.expected_date ?? "n/a"}
            </span>
            <button className="btn btn-ghost btn-sm ml-auto" onClick={() => setOpenId(null)}>
              Close
            </button>
          </div>
          <table className="mt-4 w-full text-sm">
            <thead className="text-left text-muted">
              <tr>
                {["Product", "Ordered", "Received", "Unit", "Why", open.status === "sent" || open.status === "partially_received" ? "Receive now" : ""].map((h, i) => (
                  <th key={i} scope="col" className="py-2 pr-3 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {open.lines.map((l) => (
                <tr key={l.id} className="border-t border-border">
                  <td className="py-2 pr-3">
                    {l.product} <span className="text-xs text-muted">{l.sku}</span>
                  </td>
                  <td className="py-2 pr-3 tabular-nums">{l.qty}</td>
                  <td className="py-2 pr-3 tabular-nums">{l.received_qty}</td>
                  <td className="py-2 pr-3 tabular-nums">{formatMoney(l.unit_cost)}</td>
                  <td className="py-2 pr-3 text-xs text-muted">{((l.reason as { chips?: string[] } | null)?.chips ?? []).join(" · ")}</td>
                  <td className="py-2">
                    {(open.status === "sent" || open.status === "partially_received") && l.qty > l.received_qty && (
                      <input
                        type="number"
                        min={0}
                        max={l.qty - l.received_qty}
                        aria-label={`Receive quantity for ${l.product}`}
                        className="input !w-24 !py-1"
                        value={recv[l.id] ?? 0}
                        onChange={(e) => setRecv({ ...recv, [l.id]: Math.min(l.qty - l.received_qty, Math.max(0, Number(e.target.value) || 0)) })}
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="mr-auto font-medium tabular-nums">Total {formatMoney(open.total_cost)}</span>
            {open.status === "draft" && can("approve_po") && (
              <button className="btn btn-primary" disabled={busy} onClick={() => act("approve")}>
                Approve
              </button>
            )}
            {open.status === "approved" && can("approve_po") && (
              <button className="btn btn-primary" disabled={busy} onClick={() => act("send")}>
                Mark as sent
              </button>
            )}
            {(open.status === "sent" || open.status === "partially_received") && can("receive_po") && (
              <>
                <button
                  className="btn btn-secondary"
                  onClick={() => setRecv(Object.fromEntries(open.lines.map((l) => [l.id, l.qty - l.received_qty])))}
                >
                  Fill all outstanding
                </button>
                <button
                  className="btn btn-primary"
                  disabled={busy || !Object.values(recv).some((q) => q > 0)}
                  onClick={() => act("receive", { lines: Object.entries(recv).filter(([, q]) => q > 0).map(([line_id, qty]) => ({ line_id, qty })) })}
                >
                  Receive
                </button>
              </>
            )}
            {["draft", "approved", "sent", "partially_received"].includes(open.status) && can("create_po") && (
              <button className="btn btn-secondary" disabled={busy} onClick={() => act("cancel")}>
                Cancel PO
              </button>
            )}
            <button className="btn btn-secondary" onClick={pdf}>
              Download PDF
            </button>
          </div>
        </aside>
      )}
    </div>
  );
}
