import Link from "next/link";
import type { ReactNode } from "react";
import { Logo } from "@/components/ui/icons";

export function AuthCard({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <main className="hero-bg relative grid min-h-screen place-items-center overflow-hidden p-4">
      <div className="grid-fade absolute inset-0" aria-hidden />
      <div className="card rise relative w-full max-w-[400px] p-7 shadow-[var(--shadow-lg)]">
        <Link href="/" className="inline-flex items-center gap-2.5">
          <Logo size={30} />
          <span className="text-lg font-semibold tracking-tight">Kalpa</span>
        </Link>
        <h1 className="mt-7 text-[22px] font-semibold">{title}</h1>
        <p className="mt-1 text-sm text-muted">{subtitle}</p>
        <div className="mt-6">{children}</div>
      </div>
    </main>
  );
}

export const inputCls = "input";
export const buttonCls = "btn btn-primary w-full";

export function Field({ label, error, children }: { label: string; error?: string; children: ReactNode }) {
  return (
    <label className="mb-4 block text-sm">
      <span className="mb-1.5 block font-medium">{label}</span>
      {children}
      {error && (
        <span role="alert" className="mt-1 block text-danger">
          {error}
        </span>
      )}
    </label>
  );
}
