"use client";

import { useQuery } from "@tanstack/react-query";
import { parseAsInteger, parseAsString, useQueryState } from "nuqs";
import { api } from "@/lib/api-client";

export const RANGES = [28, 90, 180, 365] as const;

/** Global store + history-range filter. State lives in the URL (?store=...&days=...), so every view is a shareable link. */
export function useGlobalFilter() {
  const [store, setStore] = useQueryState("store", parseAsString.withDefault(""));
  const [days, setDays] = useQueryState("days", parseAsInteger.withDefault(90));
  return { store, setStore, days, setDays };
}

export function GlobalFilter() {
  const { store, setStore, days, setDays } = useGlobalFilter();
  const stores = useQuery({
    queryKey: ["filter-stores"],
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/locations", { params: { query: { page_size: 200 } } });
      return (data?.items ?? []).filter((l) => l.type === "store");
    },
  });
  const cls = "input !w-auto !py-1.5 !text-[13px]";
  return (
    <div className="flex items-center gap-2" role="group" aria-label="Global filters">
      <select aria-label="Store filter" className={cls} value={store} onChange={(e) => setStore(e.target.value || null)}>
        <option value="">All stores</option>
        {stores.data?.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
      <select aria-label="History range" className={cls} value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {RANGES.map((r) => (
          <option key={r} value={r}>
            Last {r} days
          </option>
        ))}
      </select>
    </div>
  );
}
