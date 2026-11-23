"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Skeleton } from "@/components/data/primitives";
import { AuthCard, buttonCls, Field, inputCls } from "@/components/layout/auth-card";
import { api } from "@/lib/api-client";
import { apiMessage } from "@/lib/errors";

function InviteForm() {
  const token = useSearchParams().get("token") ?? "";
  const router = useRouter();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const info = useQuery({
    queryKey: ["invite", token],
    enabled: token.length > 0,
    retry: false,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/auth/invite-info", { params: { query: { token } } });
      if (!data) throw new Error("invalid");
      return data;
    },
  });

  async function accept() {
    setBusy(true);
    setErr(null);
    const { error, response } = await api.POST("/api/v1/auth/accept-invite", {
      body: { token, password, name: info.data?.existing_user ? null : name },
    });
    setBusy(false);
    if (!response.ok) return setErr(apiMessage(error, "Could not accept the invitation"));
    qc.clear();
    router.push("/app/overview");
  }

  if (!token || info.isError)
    return (
      <AuthCard title="Invitation not valid" subtitle="This link is invalid, expired or has already been used.">
        <p className="text-sm text-muted">Ask the organisation owner to send you a new invitation.</p>
      </AuthCard>
    );
  if (!info.data) return (
      <AuthCard title="Loading invitation" subtitle="One moment...">
        <Skeleton className="h-10 w-full" />
      </AuthCard>
    );

  const d = info.data;
  return (
    <AuthCard title={`Join ${d.org_name}`} subtitle={`You were invited as ${d.role} (${d.email}).`}>
      {!d.existing_user && (
        <Field label="Your name">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
        </Field>
      )}
      <Field label={d.existing_user ? "Your existing password" : "Choose a password (10+ characters)"}>
        <input type="password" className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={d.existing_user ? "current-password" : "new-password"} />
      </Field>
      {err && (
        <p role="alert" className="mb-4 text-sm text-danger">
          {err}
        </p>
      )}
      <button className={buttonCls} disabled={busy || password.length < 10 || (!d.existing_user && !name.trim())} onClick={accept}>
        {busy ? "Joining..." : "Accept invitation"}
      </button>
    </AuthCard>
  );
}

export default function InvitePage() {
  return (
    <Suspense fallback={null}>
      <InviteForm />
    </Suspense>
  );
}
