"use client";

import { useSyncExternalStore } from "react";
import { Icon } from "@/components/ui/icons";

const KEY = "kalpa-theme";

function current(): "light" | "dark" {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function subscribe(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => obs.disconnect();
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, current, () => "light" as const);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      onClick={() => {
        document.documentElement.dataset.theme = next;
        try {
          localStorage.setItem(KEY, next);
        } catch {
          /* storage blocked: theme just won't persist */
        }
      }}
      className="btn btn-secondary btn-sm"
    >
      <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  );
}

/** Inline script (runs before paint) so the saved theme applies without a flash. */
export const themeInitScript = `try{var t=localStorage.getItem("${KEY}");if(!t){t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=t}catch(e){}`;
