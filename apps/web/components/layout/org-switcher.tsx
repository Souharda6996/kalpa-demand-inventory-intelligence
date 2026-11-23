"use client";

import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import { useMe } from "@/lib/auth";

/** Org switcher: re-issues the session for another org the user belongs to, then refetches everything. */
export function OrgSwitcher() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  if (!me) return null;
  if (me.orgs.length < 2) return <span className="text-sm font-medium">{me.orgs[0]?.name}</span>;
  return (
    <select
      aria-label="Organisation"
      value={me.org_id}
      onChange={async (e) => {
        const { data } = await api.POST("/api/v1/auth/switch-org", { body: { org_id: e.target.value } });
        if (data) {
          qc.clear();
          qc.setQueryData(["me"], data);
        }
      }}
      className="input !w-auto !py-1.5 !text-[13px]"
    >
      {me.orgs.map((o) => (
        <option key={o.id} value={o.id}>
          {o.name}
        </option>
      ))}
    </select>
  );
}
