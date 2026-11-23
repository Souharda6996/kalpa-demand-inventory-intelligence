"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api-client";

function Pill({ label, up }: { label: string; up: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full border border-border px-3 py-1 text-sm ${
        up ? "text-success" : "text-danger"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${up ? "bg-success" : "bg-danger"}`} />
      {label}
    </span>
  );
}

export default function StatusPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/health");
      if (error || !data) throw new Error("health request failed");
      return data;
    },
    refetchInterval: 5000,
  });

  return (
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold">System status</h1>
      {isLoading && <p className="mt-4 text-muted">Checking...</p>}
      <div className="mt-6 flex flex-wrap gap-3">
        <Pill label="API" up={!isError && !!data} />
        <Pill label="Database" up={data?.db === "up"} />
        <Pill label="Redis" up={data?.redis === "up"} />
        <Pill label="Worker" up={data?.worker === "up"} />
      </div>
      {data && (
        <p className="mt-4 text-sm text-muted">
          v{data.version} · {data.git_sha}
        </p>
      )}
    </main>
  );
}
