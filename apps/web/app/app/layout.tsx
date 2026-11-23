"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, Suspense, useEffect, useState } from "react";
import { ErrorBoundary } from "@/components/data/error-boundary";
import { Skeleton, StatusChip } from "@/components/data/primitives";
import { CommandPalette } from "@/components/layout/command-palette";
import { CopilotDrawer } from "@/components/layout/copilot-drawer";
import { EventStreamProvider } from "@/components/layout/event-stream";
import { StreamStatusDot } from "@/components/layout/stream-status";
import { GlobalFilter } from "@/components/layout/global-filter";
import { NAV, NAV_GROUPS } from "@/components/layout/nav";
import { NotificationsBell } from "@/components/layout/notifications";
import { OrgSwitcher } from "@/components/layout/org-switcher";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Icon, Logo } from "@/components/ui/icons";
import { useLogout, useMe } from "@/lib/auth";

function initials(name: string) {
  return name
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function AppShell({ children }: { children: ReactNode }) {
  const { data: me, isLoading } = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const logout = useLogout();
  const [copilot, setCopilot] = useState(false);

  useEffect(() => {
    if (me === null) router.replace("/login");
  }, [me, router]);

  const signOut = async () => {
    await logout();
    router.replace("/login");
  };

  if (isLoading || !me) {
    return (
      <div className="p-8" aria-busy>
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-6 h-40 w-full" />
      </div>
    );
  }

  const openPalette = () => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
  const current = NAV.find((n) => pathname.startsWith(n.href));

  return (
    <EventStreamProvider>
    <div className="min-h-screen md:grid md:grid-cols-[248px_1fr]">
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-border bg-surface md:flex" aria-label="Primary">
        <Link href="/app/overview" className="flex items-center gap-2.5 px-5 pb-4 pt-5">
          <Logo />
          <span className="text-[17px] font-semibold tracking-tight">Kalpa</span>
        </Link>
        <button
          type="button"
          onClick={openPalette}
          className="mx-3 mb-3 flex items-center gap-2 rounded-[10px] border border-border-strong bg-bg px-3 py-2 text-sm text-muted transition-colors hover:bg-surface-2"
        >
          <Icon name="search" size={15} />
          <span className="flex-1 text-left">Search</span>
          <span className="kbd">Ctrl K</span>
        </button>
        <nav className="flex-1 overflow-y-auto px-3 pb-3">
          {NAV_GROUPS.map((g) => (
            <div key={g.title} className="mt-4 first:mt-0">
              <div className="px-3 pb-1.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted">{g.title}</div>
              <div className="flex flex-col gap-0.5">
                {g.items.map((n) => {
                  const active = pathname.startsWith(n.href);
                  return n.ready ? (
                    <Link
                      key={n.href}
                      href={n.href}
                      aria-current={active ? "page" : undefined}
                      className={`relative flex items-center gap-2.5 rounded-[9px] px-3 py-2 text-sm transition-colors ${
                        active ? "bg-primary-soft font-medium text-primary-text" : "text-[var(--text)] hover:bg-surface-2"
                      }`}
                    >
                      {active && <span className="absolute -left-3 top-1.5 h-6 w-[3px] rounded-r bg-primary" aria-hidden />}
                      <Icon name={n.icon} size={17} />
                      {n.label}
                    </Link>
                  ) : (
                    <span key={n.href} aria-disabled className="flex items-center gap-2.5 rounded-[9px] px-3 py-2 text-sm text-muted">
                      <Icon name={n.icon} size={17} />
                      <span className="flex-1">{n.label}</span>
                      <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium uppercase">soon</span>
                    </span>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2.5 rounded-[10px] p-1.5">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary-soft text-xs font-semibold text-primary-text" aria-hidden>
              {initials(me.name)}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{me.name}</div>
              <div className="truncate text-xs text-muted">{me.email}</div>
            </div>
            <button type="button" onClick={signOut} aria-label="Sign out" title="Sign out" className="btn btn-ghost btn-sm">
              <Icon name="logout" size={16} />
            </button>
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex flex-wrap items-center gap-3 border-b border-border bg-[color-mix(in_srgb,var(--surface)_82%,transparent)] px-4 py-2.5 backdrop-blur-md md:px-6">
          <nav className="flex w-full items-center gap-4 overflow-x-auto whitespace-nowrap pb-1 text-sm md:hidden" aria-label="Primary (mobile)">
            <Logo size={24} />
            {NAV.filter((n) => n.ready).map((n) => (
              <Link key={n.href} href={n.href} className={pathname.startsWith(n.href) ? "font-medium text-primary-text" : "text-muted"}>
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="hidden items-center gap-3 md:flex">
            <OrgSwitcher />
            <StatusChip tone="neutral">{me.role}</StatusChip>
            {current && <span className="text-sm text-muted">/ {current.label}</span>}
          </div>
          <div className="md:hidden">
            <OrgSwitcher />
          </div>
          <div className="flex w-full flex-wrap items-center gap-2 md:ml-auto md:w-auto md:flex-nowrap">
            <Suspense fallback={null}>
              <GlobalFilter />
            </Suspense>
            <button type="button" onClick={() => setCopilot(true)} className="btn btn-secondary btn-sm" aria-haspopup="dialog">
              <Icon name="sparkles" size={16} />
              <span>Copilot</span>
            </button>
            <StreamStatusDot />
            <NotificationsBell />
            <ThemeToggle />
            <button type="button" onClick={signOut} className="btn btn-secondary btn-sm md:hidden">
              Sign out
            </button>
          </div>
        </header>
        <main key={pathname} className="rise mx-auto max-w-[1400px] p-4 md:p-8">
          <ErrorBoundary label="This page">{children}</ErrorBoundary>
        </main>
      </div>
      <CommandPalette onSignOut={signOut} />
      <CopilotDrawer open={copilot} onClose={() => setCopilot(false)} />
    </div>
    </EventStreamProvider>
  );
}
