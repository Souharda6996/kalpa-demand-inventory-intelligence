/** Pull a human message out of the API's `{code, message, details}` error body. */
export function apiMessage(error: unknown, fallback = "Something went wrong"): string {
  const e = error as { message?: string; details?: unknown } | undefined;
  if (Array.isArray(e?.details) && e.details.length && typeof e.details[0] === "string") {
    return `${e?.message ?? fallback}: ${(e.details as string[]).slice(0, 3).join("; ")}`;
  }
  return e?.message ?? fallback;
}
