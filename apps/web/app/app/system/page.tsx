"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import {
  KpiTile,
  PageHeader,
  Skeleton,
  StatusChip,
} from "@/components/data/primitives";
import { API_URL, api } from "@/lib/api-client";
import { formatNumber } from "@/lib/format";

const LatencyChart = dynamic(() => import("@/components/charts/latency-chart").then((m) => m.LatencyChart), {
  ssr: false,
  loading: () => <Skeleton className="h-72" />,
});

const ms = (v: number | null | undefined) => (v == null ? "n/a" : `${v} ms`);
const bytes = (b: number | null | undefined) => {
  if (b == null) return "n/a";
  const units = ["B", "KB", "MB", "GB"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
};

/** System: live API latency, throughput, errors, queue, database, cache and versions. Every value is read live. */
export default function SystemPage() {
  const q = useQuery({
    queryKey: ["system-metrics"],
    refetchInterval: 10_000,
    queryFn: async () => {
      const { data } = await api.GET("/api/v1/system/metrics");
      if (!data) throw new Error("metrics failed");
      return data;
    },
  });
  const m = q.data;
  const mlflow = m?.mlflow_url ?? "";

  return (
    <div className="space-y-6">
      <PageHeader
        title="System"
        description={`Backend health over the last ${m?.window_minutes ?? 30} minutes, refreshed every 10 seconds.`}
      />
      {q.isLoading || !m ? (
        <Skeleton className="h-64" />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiTile
              label="API latency p50 / p95"
              value={`${m.p50_ms ?? "n/a"} / ${m.p95_ms ?? "n/a"}`}
              hint="ms"
              source="request timings recorded by the API middleware (Redis), last 30 minutes"
            />
            <KpiTile
              label="Requests per minute"
              value={String(m.requests_per_min)}
              hint={`${formatNumber(m.requests_total)} in the window`}
              source="count of requests recorded by the API middleware / window minutes"
            />
            <KpiTile
              label="Error rate (5xx)"
              value={`${(m.error_rate * 100).toFixed(2)}%`}
              source="share of responses with status >= 500 in the window"
            />
            <KpiTile
              label="Queue depth"
              value={m.queue_depth == null ? "n/a" : String(m.queue_depth)}
              hint={`${m.jobs_running} running · ${m.jobs_queued} queued (yours)`}
              source="ARQ queue length in Redis (all organisations); running/queued counts are this organisation's jobs table"
            />
            <KpiTile
              label="Database size"
              value={bytes(m.db_size_bytes)}
              hint={m.db_version ?? m.db_engine}
              source="pg_database_size(current_database()) on PostgreSQL"
            />
            <KpiTile
              label="Cache hit rate"
              value={
                m.cache_hit_rate == null
                  ? "n/a"
                  : `${(m.cache_hit_rate * 100).toFixed(1)}%`
              }
              hint="Redis, server-wide"
              source="Redis INFO keyspace_hits / (hits + misses) since the Redis server started"
            />
            <KpiTile
              label="Worker"
              value={m.worker === "up" ? "up" : "down"}
              hint={m.redis_version ? `Redis ${m.redis_version}` : undefined}
              source="worker heartbeat key in Redis (refreshed every 10 s)"
            />
            <KpiTile
              label="API version"
              value={m.api_version}
              hint={`build ${m.git_sha}`}
              source="Settings.app_version and GIT_SHA of the running API"
            />
          </div>

          <section className="card p-5" aria-label="Latency and throughput">
            <h2 className="font-medium">Latency and throughput</h2>
            <p className="mb-2 text-xs text-muted">
              Per minute: p50 and p95 response time (lines) and request count
              (bars).
            </p>
            {m.series.length ? (
              <LatencyChart points={m.series} />
            ) : (
              <p className="text-sm text-muted">
                No requests recorded in this window yet.
              </p>
            )}
          </section>

          <div className="grid gap-6 lg:grid-cols-2">
            <section className="card overflow-auto" aria-label="Slowest routes">
              <h2 className="border-b border-border bg-surface-2 px-4 py-2.5 text-sm font-medium">
                Slowest routes (p95)
              </h2>
              {m.slowest.length === 0 ? (
                <p className="p-4 text-sm text-muted">
                  Needs at least 3 requests per route.
                </p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-muted">
                    <tr>
                      <th scope="col" className="px-3 py-2 font-medium">
                        Route
                      </th>
                      <th
                        scope="col"
                        className="px-3 py-2 text-right font-medium"
                      >
                        Requests
                      </th>
                      <th
                        scope="col"
                        className="px-3 py-2 text-right font-medium"
                      >
                        p95
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.slowest.map((r) => (
                      <tr key={r.route} className="border-t border-border">
                        <td className="px-3 py-2 font-mono text-xs">
                          {r.route}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {r.requests}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {ms(r.p95_ms)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
            <section
              className="card overflow-auto"
              aria-label="Service versions"
            >
              <h2 className="border-b border-border bg-surface-2 px-4 py-2.5 text-sm font-medium">
                Service versions
              </h2>
              <table className="w-full text-sm">
                <tbody>
                  {[
                    ["Database", m.db_version ?? m.db_engine],
                    ["Redis", m.redis_version ?? "n/a"],
                    ...Object.entries(m.versions),
                  ].map(([k, v]) => (
                    <tr
                      key={k}
                      className="border-t border-border first:border-t-0"
                    >
                      <th
                        scope="row"
                        className="px-3 py-2 text-left font-normal text-muted"
                      >
                        {k}
                      </th>
                      <td className="px-3 py-2 tabular-nums">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </div>

          <section
            className="card flex flex-wrap items-center gap-3 p-5"
            aria-label="Links"
          >
            <h2 className="mr-2 font-medium">Tools</h2>
            <a
              className="btn btn-secondary btn-sm"
              href={`${API_URL}${m.docs_url}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              API docs (Swagger)
            </a>
            {mlflow.startsWith("http") && (
              <a
                className="btn btn-secondary btn-sm"
                href={mlflow}
                target="_blank"
                rel="noopener noreferrer"
              >
                MLflow
              </a>
            )}
            <StatusChip tone={m.worker === "up" ? "ok" : "bad"}>
              worker {m.worker}
            </StatusChip>
          </section>
        </>
      )}
    </div>
  );
}
