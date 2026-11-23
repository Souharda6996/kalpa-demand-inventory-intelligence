"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { buttonCls, Field, inputCls } from "@/components/layout/auth-card";
import { api } from "@/lib/api-client";
import { useMe } from "@/lib/auth";
import { apiMessage } from "@/lib/errors";

const STEPS = ["Organisation", "First store", "Your data"] as const;

export default function OnboardingPage() {
  const { data: me } = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [typedOrgName, setOrgName] = useState<string | null>(null);
  const [currency, setCurrency] = useState<"INR" | "USD">("INR");
  const [storeName, setStoreName] = useState("");
  const [city, setCity] = useState("");

  const orgName = typedOrgName ?? me?.orgs.find((o) => o.id === me.org_id)?.name ?? "";

  useEffect(() => {
    if (me === null) router.replace("/login");
  }, [me, router]);

  async function run(fn: () => Promise<string | null>, next: () => void) {
    setBusy(true);
    const err = await fn();
    setBusy(false);
    if (err) toast.error(err);
    else next();
  }

  const saveOrg = () =>
    run(async () => {
      const { error } = await api.PATCH("/api/v1/org", { body: { name: orgName, currency, region: currency === "INR" ? "IN" : "US" } });
      return error ? apiMessage(error) : null;
    }, () => setStep(1));

  const saveStore = () =>
    run(async () => {
      const { error } = await api.POST("/api/v1/locations", { body: { name: storeName, type: "store", city: city || null } });
      return error ? apiMessage(error) : null;
    }, () => setStep(2));

  const loadDemo = () =>
    run(async () => {
      const { error } = await api.POST("/api/v1/data/demo");
      return error ? apiMessage(error) : null;
    }, () => {
      qc.invalidateQueries();
      toast.success("Demo data loaded");
      router.push("/app/overview");
    });

  return (
    <main className="mx-auto max-w-lg p-6">
      <h1 className="text-2xl font-semibold">Set up your workspace</h1>
      <ol className="mt-4 flex gap-2 text-sm" aria-label="Progress">
        {STEPS.map((s, i) => (
          <li key={s} aria-current={i === step ? "step" : undefined} className={`rounded-full border px-3 py-1 ${i === step ? "border-primary text-primary" : "border-border text-muted"}`}>
            {i + 1}. {s}
          </li>
        ))}
      </ol>
      <section className="mt-6 card p-5">
        {step === 0 && (
          <>
            <Field label="Organisation name">
              <input className={inputCls} value={orgName} onChange={(e) => setOrgName(e.target.value)} />
            </Field>
            <Field label="Currency / region">
              <select className={inputCls} value={currency} onChange={(e) => setCurrency(e.target.value as "INR" | "USD")}>
                <option value="INR">INR (₹) - India</option>
                <option value="USD">USD ($) - United States</option>
              </select>
            </Field>
            <button className={buttonCls} disabled={busy || !orgName.trim()} onClick={saveOrg}>
              Continue
            </button>
          </>
        )}
        {step === 1 && (
          <>
            <Field label="Store name">
              <input className={inputCls} value={storeName} onChange={(e) => setStoreName(e.target.value)} placeholder="e.g. Koramangala" />
            </Field>
            <Field label="City (optional)">
              <input className={inputCls} value={city} onChange={(e) => setCity(e.target.value)} />
            </Field>
            <div className="flex gap-3">
              <button className={buttonCls} disabled={busy || !storeName.trim()} onClick={saveStore}>
                Continue
              </button>
              <button className="w-full rounded-card border border-border px-3 py-2 text-sm" onClick={() => setStep(2)}>
                Skip
              </button>
            </div>
          </>
        )}
        {step === 2 && (
          <div className="space-y-3">
            <p className="text-sm text-muted">Start with sample data, or bring your own sales history.</p>
            <button className={buttonCls} disabled={busy} onClick={loadDemo}>
              {busy ? "Loading demo data (this can take a minute)..." : "Load demo data (Indian retail, synthetic)"}
            </button>
            <button className="w-full rounded-card border border-border px-3 py-2 text-sm" disabled={busy} onClick={() => router.push("/app/data")}>
              Upload a CSV
            </button>
          </div>
        )}
      </section>
    </main>
  );
}
