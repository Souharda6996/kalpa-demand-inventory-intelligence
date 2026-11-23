"use client";

import { useSyncExternalStore } from "react";
import { Toaster } from "sonner";

const themeKey = () =>
  document.documentElement.dataset.theme === "dark" ? "dark" : "light";
const subscribe = (cb: () => void) => {
  const o = new MutationObserver(cb);
  o.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => o.disconnect();
};

/** Toasts follow the app theme (Sonner defaults to light). No richColors: its tinted backgrounds failed WCAG contrast in dark mode. */
export function ThemedToaster() {
  const theme = useSyncExternalStore(
    subscribe,
    themeKey,
    () => "light" as const,
  );
  return (
    <Toaster
      closeButton
      theme={theme}
      toastOptions={{
        classNames: {
          // our audited button colours instead of Sonner's defaults, which fail WCAG contrast on the action button
          actionButton: "!bg-primary !text-primary-fg !font-medium",
          cancelButton: "!bg-surface-2 !text-[var(--text)]",
        },
      }}
    />
  );
}
