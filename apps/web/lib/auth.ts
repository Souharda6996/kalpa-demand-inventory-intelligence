"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api-client";
import type { components } from "./api-schema";

export type Me = components["schemas"]["MeOut"];

/** Current session; `data === null` means signed out (401), `undefined` means still loading. */
export function useMe() {
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/me");
      if (response.status === 401) return null;
      if (!data) throw new Error("could not load session");
      return data;
    },
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return async () => {
    await api.POST("/api/v1/auth/logout");
    qc.setQueryData(["me"], null);
    qc.clear();
  };
}
