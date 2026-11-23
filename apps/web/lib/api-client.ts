import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./api-schema";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`))
    ?.slice(name.length + 1);
}

/** Double-submit CSRF: echo the readable cookie into X-CSRF-Token on state-changing requests. */
const csrf: Middleware = {
  onRequest({ request }) {
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const token = readCookie("kalpa_csrf");
      if (token) request.headers.set("X-CSRF-Token", token);
    }
    return request;
  },
};

let inflight: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const before = readCookie("kalpa_csrf");
  const run = async () => {
    // Refresh tokens are one-time use. If another tab refreshed while we waited for the lock, the CSRF cookie has
    // changed and the session is already renewed: do not spend (and thereby invalidate) a second refresh token.
    if (before !== undefined && readCookie("kalpa_csrf") !== before)
      return true;
    const r = await fetch(`${API_URL}/api/v1/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": readCookie("kalpa_csrf") ?? "" },
    });
    return r.ok;
  };
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  return locks ? locks.request("kalpa-refresh", run) : run();
}

/** Renew the 15-minute access cookie with the refresh cookie. One refresh at a time per tab (and per browser via Web Locks). */
export function refreshSession(): Promise<boolean> {
  inflight ??= doRefresh().finally(() => {
    inflight = null;
  });
  return inflight;
}

const AUTH_PATH = /\/auth\/(login|signup|refresh|logout)/;

/** fetch that renews an expired access cookie once and retries the request, so long-lived pages do not drop to /login. */
async function kalpaFetch(input: Request): Promise<Response> {
  const retry = input.clone();
  const res = await fetch(input);
  if (res.status !== 401 || AUTH_PATH.test(input.url)) return res;
  if (!(await refreshSession())) return res;
  if (!["GET", "HEAD", "OPTIONS"].includes(retry.method))
    retry.headers.set("X-CSRF-Token", readCookie("kalpa_csrf") ?? "");
  return fetch(retry);
}

/** Typed client generated from the FastAPI OpenAPI schema (`pnpm gen:api`). Cookies are sent cross-origin. */
export const api = createClient<paths>({
  baseUrl: API_URL,
  credentials: "include",
  fetch: kalpaFetch,
});
api.use(csrf);
