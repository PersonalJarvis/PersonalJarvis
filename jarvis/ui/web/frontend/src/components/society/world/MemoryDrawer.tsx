/**
 * What a click on the Memory House opens: the society's shared memory as a
 * drawer over the island — the reviewed team knowledge, every agent's memory
 * head, the review queue with Promote / Mark reviewed, and a recall search
 * that shows hits the way an agent sees them (scope and trust labels).
 * Data: /api/society/memory (docs/agent-society/memory-house.md §3.5).
 * App chrome, so it wears the theme tokens.
 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Search, Share2, X } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

interface MemoryOverview {
  shared: Array<{ path: string; title: string; author: string; updated_ms: number }>;
  agents: Array<{
    agent_id: string;
    name: string;
    title: string;
    memory_head: string;
    notes: number;
    checkpoint: string;
  }>;
  unreviewed: Array<{
    id: number;
    agent_id: string;
    path: string;
    origin: string;
    summary: string;
    created_ms: number;
  }>;
  vault_root: string;
}

interface Hit {
  path: string;
  title: string;
  scope: string;
  label: string;
  snippet: string;
}

const MEMORY_KEY = ["society", "memory"] as const;

async function getOverview(): Promise<MemoryOverview> {
  const res = await fetch("/api/society/memory");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as MemoryOverview;
}

async function post(url: string, body?: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

const SCOPE_COLOR: Record<string, string> = {
  own: "#7df9ff",
  shared: "#ffd166",
  user: "#b388ff",
  other: "#c9c2b2",
};

export function MemoryDrawer({ onClose }: { onClose: () => void }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const client = useQueryClient();
  const overview = useQuery({ queryKey: MEMORY_KEY, queryFn: getOverview, staleTime: 15_000 });
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: MEMORY_KEY });
    void client.invalidateQueries({ queryKey: ["society", "roster"] });
  };
  const promote = useMutation({
    mutationFn: (id: number) => post(`/api/society/memory/${id}/promote`),
    onSuccess: refresh,
  });
  const dismiss = useMutation({
    mutationFn: (id: number) => post(`/api/society/memory/${id}/dismiss`),
    onSuccess: refresh,
  });
  const recall = useMutation({
    mutationFn: (q: string) =>
      post("/api/society/memory/recall", { query: q, k: 8 }) as Promise<{ hits: Hit[] }>,
    onSuccess: (data) => setHits(data.hits),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const data = overview.data;

  return (
    <aside
      className="absolute inset-y-3 right-3 z-30 flex w-[380px] max-w-[85%] flex-col overflow-hidden rounded-lg border border-border bg-popover text-foreground shadow-float"
      role="dialog"
      aria-label={t("society.world.drawer_memory_title")}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold tracking-tight">{t("society.world.drawer_memory_title")}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("society.world.drawer_memory_hint")}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("society.world.drawer_close")}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X size={16} />
        </button>
      </header>

      <form
        className="flex items-center gap-2 border-b border-border px-4 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (query.trim()) recall.mutate(query.trim());
        }}
      >
        <Search size={14} className="shrink-0 text-muted-foreground" aria-hidden />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("society.world.memory_search_placeholder")}
          className="h-7 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
        />
        <button
          type="submit"
          disabled={recall.isPending || !query.trim()}
          className="h-7 rounded-md bg-secondary px-2 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
        >
          {t("society.world.memory_search_button")}
        </button>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {overview.isLoading && <p className="text-sm text-muted-foreground">{t("society.world.drawer_loading")}</p>}
        {overview.isError && <p className="text-sm text-destructive">{String(overview.error)}</p>}

        {hits && (
          <section className="mb-4">
            <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t("society.world.memory_hits")} <span className="tabular-nums">{hits.length}</span>
            </h3>
            {hits.length === 0 && <p className="text-sm text-muted-foreground">{t("society.world.memory_no_hits")}</p>}
            <ul className="space-y-1.5">
              {hits.map((h) => (
                <li key={h.path} className="rounded-md bg-secondary px-2 py-1.5 text-xs">
                  <div className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-sm" style={{ background: SCOPE_COLOR[h.scope] ?? "#c9c2b2" }} aria-hidden />
                    <span className="font-medium text-foreground">{h.title}</span>
                    <span className="ml-auto font-mono text-[10px] text-muted-foreground">{h.label}</span>
                  </div>
                  {h.snippet && <p className="mt-0.5 line-clamp-2 text-muted-foreground">{h.snippet}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}

        {data && (
          <>
            <section className="mb-4">
              <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: SCOPE_COLOR.shared }} aria-hidden />
                {t("society.world.memory_shared")}
                <span className="tabular-nums">{data.shared.length}</span>
              </h3>
              {data.shared.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("society.world.memory_empty_shared")}</p>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {data.shared.map((s) => (
                    <li key={s.path} className="rounded-md bg-secondary px-2 py-1 text-xs text-foreground" title={s.path}>
                      {s.title}
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="mb-4">
              <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: SCOPE_COLOR.own }} aria-hidden />
                {t("society.world.memory_agents")}
                <span className="tabular-nums">{data.agents.length}</span>
              </h3>
              <ul className="space-y-2">
                {data.agents.map((a) => (
                  <li key={a.agent_id} className="rounded-md border border-border px-2 py-1.5">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-medium text-foreground">{a.name}</span>
                      {a.title && <span className="truncate text-muted-foreground">{a.title}</span>}
                      <span className="ml-auto tabular-nums text-muted-foreground">
                        {t("society.world.memory_notes").replace("{count}", String(a.notes))}
                      </span>
                    </div>
                    <p className="mt-0.5 line-clamp-3 whitespace-pre-line text-xs text-muted-foreground">
                      {a.memory_head || t("society.world.memory_nothing_remembered")}
                    </p>
                  </li>
                ))}
              </ul>
            </section>

            <section className="mb-2">
              <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: SCOPE_COLOR.other }} aria-hidden />
                {t("society.world.memory_queue")}
                <span className="tabular-nums">{data.unreviewed.length}</span>
              </h3>
              {data.unreviewed.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("society.world.memory_empty_queue")}</p>
              ) : (
                <ul className="space-y-1.5">
                  {data.unreviewed.map((u) => (
                    <li key={u.id} className="rounded-md bg-secondary px-2 py-1.5 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-foreground">{u.agent_id}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">{u.origin}</span>
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-muted-foreground">{u.summary}</p>
                      <div className="mt-1.5 flex gap-1.5">
                        <button
                          type="button"
                          disabled={promote.isPending}
                          onClick={() => promote.mutate(u.id)}
                          className="inline-flex h-6 items-center gap-1 rounded-md bg-background px-2 text-[11px] font-medium text-foreground hover:bg-muted disabled:opacity-50"
                        >
                          <Share2 size={11} />
                          {t("society.world.memory_promote")}
                        </button>
                        <button
                          type="button"
                          disabled={dismiss.isPending}
                          onClick={() => dismiss.mutate(u.id)}
                          className="inline-flex h-6 items-center gap-1 rounded-md bg-background px-2 text-[11px] font-medium text-muted-foreground hover:bg-muted disabled:opacity-50"
                        >
                          <Check size={11} />
                          {t("society.world.memory_dismiss")}
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
      <footer className="border-t border-border px-4 py-3">
        <button
          type="button"
          onClick={() => setActiveSection("memory")}
          className="inline-flex h-8 items-center gap-2 rounded-md bg-secondary px-3 text-sm font-medium text-foreground hover:bg-muted"
        >
          <ExternalLink size={14} />
          {t("society.world.memory_open_wiki")}
        </button>
      </footer>
    </aside>
  );
}
