"use client";

import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { NAV } from "./nav";

export function CommandPalette({ onSignOut }: { onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };
  const toggleTheme = () => {
    const el = document.documentElement;
    el.dataset.theme = el.dataset.theme === "dark" ? "light" : "dark";
    setOpen(false);
  };

  return (
    <Command.Dialog open={open} onOpenChange={setOpen} label="Command palette" overlayClassName="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm" className="fixed left-1/2 top-24 z-50 w-[min(92vw,34rem)] -translate-x-1/2 card p-2 shadow-[var(--shadow-lg)]">
      <Command.Input placeholder="Type a command or page..." className="w-full border-b border-border bg-transparent px-3 py-3 text-sm outline-none placeholder:text-muted" />
      <Command.List className="max-h-72 overflow-auto">
        <Command.Empty className="p-3 text-sm text-muted">No results.</Command.Empty>
        <Command.Group heading="Go to" className="text-xs text-muted">
          {NAV.filter((n) => n.ready).map((n) => (
            <Command.Item key={n.href} value={n.label} onSelect={() => go(n.href)} className="cursor-pointer rounded-[8px] px-3 py-2 text-sm text-[var(--text)] data-[selected=true]:bg-primary-soft data-[selected=true]:text-primary">
              {n.label}
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="Actions" className="text-xs text-muted">
          <Command.Item value="Toggle theme" onSelect={toggleTheme} className="cursor-pointer rounded-[8px] px-3 py-2 text-sm text-[var(--text)] data-[selected=true]:bg-primary-soft data-[selected=true]:text-primary">
            Toggle light / dark theme
          </Command.Item>
          <Command.Item value="Sign out" onSelect={onSignOut} className="cursor-pointer rounded-[8px] px-3 py-2 text-sm text-[var(--text)] data-[selected=true]:bg-primary-soft data-[selected=true]:text-primary">
            Sign out
          </Command.Item>
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
