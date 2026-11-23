"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState, Skeleton, StatusChip } from "@/components/data/primitives";
import { buttonCls, Field, inputCls } from "@/components/layout/auth-card";
import { api } from "@/lib/api-client";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";

const ROLES = ["owner", "planner", "store_manager", "viewer"] as const;
type Role = (typeof ROLES)[number];

function OrgSection() {
  const qc = useQueryClient();
  const { data: org } = useQuery({
    queryKey: ["org"],
    queryFn: async () => (await api.GET("/api/v1/org")).data,
  });
  const [name, setName] = useState<string | null>(null);
  if (!org) return <Skeleton className="h-24" />;
  return (
    <section className="card p-4">
      <h2 className="font-medium">Organisation</h2>
      <Field label="Name">
        <input className={inputCls} value={name ?? org.name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <p className="mb-3 text-sm text-muted">
        Currency {org.currency} · timezone {org.timezone}
      </p>
      <button
        className={`${buttonCls} max-w-40`}
        disabled={name === null || !name.trim()}
        onClick={async () => {
          const { error } = await api.PATCH("/api/v1/org", { body: { name: name ?? undefined } });
          if (error) toast.error(apiMessage(error));
          else {
            toast.success("Saved");
            qc.invalidateQueries({ queryKey: ["org"] });
            qc.invalidateQueries({ queryKey: ["me"] });
          }
        }}
      >
        Save
      </button>
    </section>
  );
}

function CostSection() {
  const qc = useQueryClient();
  const { data: org } = useQuery({ queryKey: ["org"], queryFn: async () => (await api.GET("/api/v1/org")).data });
  const [v, setV] = useState<{ h: string; p: string; r: string } | null>(null);
  if (!org) return <Skeleton className="h-32" />;
  const cur = org.cost_params ?? {};
  const form = v ?? { h: String(cur.holding_pct_year ?? 0.25), p: String(cur.stockout_penalty_ratio ?? 0.5), r: String(cur.review_days ?? 7) };
  return (
    <section className="card p-4" aria-labelledby="cost-h">
      <h2 id="cost-h" className="font-medium">
        Cost parameters
      </h2>
      <p className="mt-1 text-sm text-muted">Drive the service level, order quantities and the Arena cost replay. Defaults are assumptions, not facts about your business.</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <Field label="Holding cost (share of unit cost per year)">
          <input type="number" step="0.01" min={0} max={2} className={inputCls} value={form.h} onChange={(e) => setV({ ...form, h: e.target.value })} />
        </Field>
        <Field label="Stock-out penalty (x unit cost per lost unit)">
          <input type="number" step="0.1" min={0} max={20} className={inputCls} value={form.p} onChange={(e) => setV({ ...form, p: e.target.value })} />
        </Field>
        <Field label="Review period (days)">
          <input type="number" min={1} max={28} className={inputCls} value={form.r} onChange={(e) => setV({ ...form, r: e.target.value })} />
        </Field>
      </div>
      <button
        className={`${buttonCls} max-w-40`}
        disabled={v === null}
        onClick={async () => {
          const { error } = await api.PATCH("/api/v1/org", {
            body: { cost_params: { holding_pct_year: Number(form.h), stockout_penalty_ratio: Number(form.p), review_days: Number(form.r) } },
          });
          if (error) toast.error(apiMessage(error));
          else {
            toast.success("Cost parameters saved");
            setV(null);
            qc.invalidateQueries({ queryKey: ["org"] });
            qc.invalidateQueries({ queryKey: ["recommendations"] });
          }
        }}
      >
        Save
      </button>
    </section>
  );
}

function MembersSection() {
  const qc = useQueryClient();
  const members = useQuery({
    queryKey: ["members"],
    queryFn: async () => (await api.GET("/api/v1/org/members")).data ?? [],
  });
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "viewer" as Role });
  const refresh = () => qc.invalidateQueries({ queryKey: ["members"] });

  async function add() {
    const { error } = await api.POST("/api/v1/org/members", { body: { ...form } });
    if (error) toast.error(apiMessage(error));
    else {
      toast.success("Member added");
      setForm({ email: "", name: "", password: "", role: "viewer" });
      refresh();
    }
  }

  return (
    <section className="card p-4">
      <h2 className="font-medium">Members &amp; roles</h2>
      {members.isLoading ? (
        <Skeleton className="mt-3 h-24" />
      ) : (
        <ul className="mt-3 divide-y divide-border text-sm">
          {members.data?.map((m) => (
            <li key={m.membership_id} className="flex flex-wrap items-center gap-3 py-2">
              <span className="min-w-48 flex-1">
                {m.name} <span className="text-muted">· {m.email}</span>
              </span>
              <select
                aria-label={`Role for ${m.email}`}
                value={m.role}
                className="rounded-card border border-border bg-bg px-2 py-1"
                onChange={async (e) => {
                  const { error } = await api.PATCH("/api/v1/org/members/{membership_id}", {
                    params: { path: { membership_id: m.membership_id } },
                    body: { role: e.target.value as Role },
                  });
                  if (error) toast.error(apiMessage(error));
                  refresh();
                }}
              >
                {ROLES.map((r) => (
                  <option key={r}>{r}</option>
                ))}
              </select>
              <button
                className="text-danger underline"
                onClick={async () => {
                  const { error } = await api.DELETE("/api/v1/org/members/{membership_id}", {
                    params: { path: { membership_id: m.membership_id } },
                  });
                  if (error) toast.error(apiMessage(error));
                  refresh();
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <h3 className="mb-2 mt-5 text-sm font-medium">Add a member</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Email">
          <input type="email" className={inputCls} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </Field>
        <Field label="Name">
          <input className={inputCls} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </Field>
        <Field label="Temporary password (10+ characters)">
          <input type="password" className={inputCls} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </Field>
        <Field label="Role">
          <select className={inputCls} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
            {ROLES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </Field>
      </div>
      <button className={`${buttonCls} max-w-40`} disabled={!form.email || !form.name || form.password.length < 10} onClick={add}>
        Add member
      </button>
    </section>
  );
}

function InvitesSection() {
  const qc = useQueryClient();
  const invites = useQuery({ queryKey: ["invites"], queryFn: async () => (await api.GET("/api/v1/org/invites")).data ?? [] });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [link, setLink] = useState<string | null>(null);
  return (
    <section className="card p-4">
      <h2 className="font-medium">Invitations</h2>
      <p className="mt-1 text-sm text-muted">Creates a one-time link (valid 7 days). No email is sent: copy the link and share it.</p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Invitee email">
          <input type="email" className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Role">
          <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </Field>
        <button
          className={`${buttonCls} mb-4 max-w-36`}
          disabled={!email}
          onClick={async () => {
            const { data, error } = await api.POST("/api/v1/org/invites", { body: { email, role } });
            if (!data) return toast.error(apiMessage(error));
            setLink(`${window.location.origin}${data.accept_path}`);
            setEmail("");
            qc.invalidateQueries({ queryKey: ["invites"] });
          }}
        >
          Create invite
        </button>
      </div>
      {link && (
        <p role="status" className="break-all rounded-card border border-border p-2 text-sm">
          Invite link (shown once): <code>{link}</code>
        </p>
      )}
      <ul className="mt-3 text-sm">
        {invites.data?.map((i) => (
          <li key={i.id} className="flex items-center gap-3 border-b border-border py-2">
            <span className="flex-1">
              {i.email} · {i.role}
            </span>
            <StatusChip tone={i.accepted ? "ok" : "neutral"}>{i.accepted ? "accepted" : "pending"}</StatusChip>
            {!i.accepted && (
              <button
                className="text-danger underline"
                onClick={async () => {
                  await api.DELETE("/api/v1/org/invites/{invite_id}", { params: { path: { invite_id: i.id } } });
                  qc.invalidateQueries({ queryKey: ["invites"] });
                }}
              >
                Revoke
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SuppliersSection() {
  const qc = useQueryClient();
  const sup = useQuery({
    queryKey: ["suppliers"],
    queryFn: async () => (await api.GET("/api/v1/suppliers", { params: { query: { page_size: 200 } } })).data?.items ?? [],
  });
  const [name, setName] = useState("");
  const [lead, setLead] = useState("7");
  return (
    <section className="card p-4">
      <h2 className="font-medium">Suppliers</h2>
      {sup.data && sup.data.length === 0 && (
        <div className="mt-3">
          <EmptyState title="No suppliers yet" />
        </div>
      )}
      <ul className="mt-3 text-sm">
        {sup.data?.map((s) => (
          <li key={s.id} className="flex items-center gap-3 border-b border-border py-2">
            <span className="flex-1">{s.name}</span>
            <StatusChip tone="neutral">{s.default_lead_time_days} d lead time</StatusChip>
          </li>
        ))}
      </ul>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <Field label="Supplier name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Lead time (days)">
          <input type="number" min={0} className={inputCls} value={lead} onChange={(e) => setLead(e.target.value)} />
        </Field>
        <button
          className={`${buttonCls} mb-4 max-w-32`}
          disabled={!name.trim()}
          onClick={async () => {
            const { error } = await api.POST("/api/v1/suppliers", { body: { name, default_lead_time_days: Number(lead), reliability_score: 0.95 } });
            if (error) toast.error(apiMessage(error));
            else {
              setName("");
              qc.invalidateQueries({ queryKey: ["suppliers"] });
            }
          }}
        >
          Add
        </button>
      </div>
    </section>
  );
}

export default function SettingsPage() {
  const { data: me } = useMe();
  const isOwner = me?.permissions.includes("manage_org");
  const canEdit = me?.permissions.includes("edit_master_data");
  return (
    <div className="max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      {isOwner ? (
        <>
          <OrgSection />
          <CostSection />
          <MembersSection />
          <InvitesSection />
        </>
      ) : (
        <p className="text-sm text-muted">Organisation and member settings are managed by owners.</p>
      )}
      {canEdit ? <SuppliersSection /> : null}
    </div>
  );
}
