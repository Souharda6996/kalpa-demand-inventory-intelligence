"use client";

import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { StatusChip } from "@/components/data/primitives";
import { Icon } from "@/components/ui/icons";
import { api } from "@/lib/api-client";
import { apiMessage } from "@/lib/errors";

type Turn = { q: string; a?: string; mode?: "llm" | "rule-based" | "refused"; sources?: string[]; error?: string };
const SUGGESTIONS = ["Which model is the most accurate?", "What is at risk of stock-out?", "What should I reorder?", "Compare the models"];
const MODE_LABEL = { llm: "AI, numbers verified", "rule-based": "answered from your data", refused: "out of scope" } as const;

/** Read-only copilot. Works with no API key (rule-based over the same tools); every answer names its source. */
export function CopilotDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const status = useQuery({ queryKey: ["copilot-status"], enabled: open, queryFn: async () => (await api.GET("/api/v1/copilot/status")).data });

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") return onClose();
      if (e.key !== "Tab" || !panel.current) return; // keep focus inside the dialog
      const f = panel.current.querySelectorAll<HTMLElement>("button, input, [href], [tabindex]:not([tabindex='-1'])");
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);

  async function ask(question: string) {
    const q = question.trim();
    if (q.length < 3 || busy) return;
    setBusy(true);
    setText("");
    setTurns((t) => [...t, { q }]);
    let turn: Turn = { q, error: "Could not reach the server. Try again." };
    try {
      const { data, error } = await api.POST("/api/v1/copilot/chat", { body: { question: q } });
      turn = data ? { q, a: data.answer, mode: data.mode, sources: data.sources } : { q, error: apiMessage(error) };
    } catch {
      /* network failure or a response without CORS headers: keep the friendly message above */
    } finally {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? turn : x)));
      setBusy(false);
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    void ask(text);
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50" role="presentation">
      <button type="button" aria-label="Close copilot" className="absolute inset-0 bg-black/40" onClick={onClose} tabIndex={-1} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Copilot"
        className="absolute inset-y-0 right-0 flex w-full max-w-md flex-col border-l border-border bg-surface shadow-xl"
      >
        <header className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Icon name="sparkles" size={18} />
          <h2 className="font-medium">Copilot</h2>
          {status.data && <StatusChip tone="neutral">{status.data.mode === "llm" ? "AI mode" : "rule-based mode"}</StatusChip>}
          <button type="button" onClick={onClose} className="btn btn-ghost btn-sm ml-auto">
            Close
          </button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto p-4 text-sm" aria-live="polite">
          {turns.length === 0 && (
            <div>
              <p className="text-muted">
                Ask about your forecasts, stock-out risk, replenishment or model comparison. Answers come only from your organisation&apos;s data and name their
                source.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" className="btn btn-secondary btn-sm" onClick={() => void ask(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className="space-y-2">
              <p className="ml-8 rounded-[10px] bg-primary-soft px-3 py-2 text-primary-text">{t.q}</p>
              {t.a ? (
                <div className="rounded-[10px] border border-border px-3 py-2">
                  <p>{t.a}</p>
                  <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                    {t.mode && <StatusChip tone={t.mode === "refused" ? "warn" : "neutral"}>{MODE_LABEL[t.mode]}</StatusChip>}
                    {t.sources?.map((s) => (
                      <span key={s}>source: {s}</span>
                    ))}
                  </p>
                </div>
              ) : t.error ? (
                <p className="text-danger">{t.error}</p>
              ) : (
                <p className="text-muted">Thinking...</p>
              )}
            </div>
          ))}
        </div>
        <form onSubmit={submit} className="flex gap-2 border-t border-border p-3">
          <input
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={500}
            aria-label="Ask the copilot"
            placeholder="e.g. Why is Kaju Katli at Koramangala going up?"
            className="input"
          />
          <button type="submit" className="btn btn-primary" disabled={busy || text.trim().length < 3}>
            Ask
          </button>
        </form>
      </div>
    </div>
  );
}
