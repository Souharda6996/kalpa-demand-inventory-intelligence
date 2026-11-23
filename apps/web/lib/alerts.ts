import { api } from "./api-client";
import type { AlertEvent } from "./live";
import { apiMessage } from "./errors";

export type PoResult =
  { ok: true; supplier: string; qty: number } | { ok: false; message: string };

/** Draft a purchase order for the item an alert is about, using the same recommendation the Replenishment page shows. */
export async function createPoForAlert(a: AlertEvent): Promise<PoResult> {
  if (!a.location_id || !a.product_id)
    return { ok: false, message: "This alert is not about a single item" };
  const { data, error } = await api.GET("/api/v1/policy/recommendations", {
    params: { query: { location_id: a.location_id, only_orders: true } },
  });
  if (!data) return { ok: false, message: apiMessage(error) };
  const rec = data.items.find((i) => i.product_id === a.product_id);
  if (!rec)
    return {
      ok: false,
      message: "No order is recommended for this item right now",
    };
  const res = await api.POST("/api/v1/purchase-orders", {
    body: {
      supplier_id: rec.supplier_id,
      location_id: rec.location_id,
      lines: [
        {
          product_id: rec.product_id,
          qty: rec.recommended_qty,
          reason: { chips: rec.reasons },
        },
      ],
    },
  });
  if (!res.data) return { ok: false, message: apiMessage(res.error) };
  return { ok: true, supplier: rec.supplier, qty: rec.recommended_qty };
}
