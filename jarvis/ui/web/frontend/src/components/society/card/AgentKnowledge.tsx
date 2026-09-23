import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { MemoryMarkdown } from "../chat/MemoryDocument";

export interface AgentKnowledge {
  files: { path: string; name: string; kind: "memory" | "skills"; updated_ms: number; size: number }[];
  learned_instructions: string[];
  reviews: { pending: number; done: number };
  last_review: { state: "reviewing" | "pending" | "done"; updated_ms: number } | null;
  legacy?: boolean;
}

async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function useAgentKnowledge(agentId: string, sample: boolean) {
  return useQuery({
    queryKey: ["society", "agent-knowledge", agentId],
    enabled: !sample,
    retry: false,
    staleTime: 10_000,
    refetchInterval: import.meta.env.MODE === "test" ? false : () => 12_000 + Math.random() * 4_000,
    queryFn: async ({ signal }) => {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/knowledge`, { signal });
      if (response.status !== 404) {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<AgentKnowledge>;
      }
      // The WebView can load a new bundle before the Python process restarts.
      // Preserve the existing memory reader until the new route is available.
      const tree = await get<{ ok: boolean; folders: { name: string; files: { slug: string; mtime: number; size: number }[] }[] }>("/api/wiki/tree", signal);
      if (!tree.ok) throw new Error("Memory listing unavailable");
      const namespace = `society/${agentId}/`;
      const files = tree.folders.filter((folder) => `${folder.name}/`.startsWith(namespace)).flatMap((folder) => folder.files.map((file) => {
        const name = `${folder.name}/${file.slug}.md`.slice(namespace.length);
        return { path: `memory/${name}`, name, kind: "memory" as const, updated_ms: file.mtime * 1000, size: file.size };
      }));
      return { files, learned_instructions: [], reviews: { pending: 0, done: 0 }, last_review: null, legacy: true };
    },
  });
}

export function LearnedInstructions({ agentId, sample }: { agentId: string; sample: boolean }) {
  const t = useT();
  const query = useAgentKnowledge(agentId, sample);
  if (sample) return null;
  const data = query.data;
  const status = data?.last_review?.state === "reviewing" ? "reviewing"
    : data?.reviews.pending ? "pending" : data?.reviews.done ? "done" : "waiting";
  return <section className="mt-5 rounded-xl border border-border p-4" aria-label={t("society.profile_card.learned_instructions")}>
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-sm font-medium">{t("society.profile_card.learned_instructions")}</h3>
      <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => void query.refetch()} aria-label={t("society.profile_card.refresh")}><RefreshCw size={14} aria-hidden /></Button>
    </div>
    <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{t("society.profile_card.learned_hint")}</p>
    {query.isError ? <p role="alert" className="text-sm text-destructive">{t("society.profile_card.load_error")}</p>
      : query.isLoading ? <p className="text-xs text-muted-foreground">{t("society.profile_card.loading")}</p>
      : data?.legacy ? <p role="status" className="text-xs text-muted-foreground">{t("society.profile_card.runtime_pending")}</p>
      : <>
        {data?.learned_instructions.length ? <ul className="list-disc space-y-2 pl-4 text-sm">{data.learned_instructions.map((rule) => <li key={rule}>{rule}</li>)}</ul>
          : <p className="text-sm text-muted-foreground">{t("society.profile_card.no_instructions")}</p>}
        <p className="mt-3 text-xs text-muted-foreground" role="status">{t(`society.profile_card.review_${status}`)}{data?.reviews.pending ? ` (${data.reviews.pending})` : ""}</p>
        {data?.last_review && <p className="mt-1 text-xs text-muted-foreground">{t("society.profile_card.last_review")}: {new Date(data.last_review.updated_ms).toLocaleString()}</p>}
      </>}
  </section>;
}

export function AgentMemoryFiles({ agentId, sample }: { agentId: string; sample: boolean }) {
  const t = useT();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const query = useAgentKnowledge(agentId, sample);
  const files = query.data?.files ?? [];
  const visible = files.filter((file) => file.path.toLowerCase().includes(search.toLowerCase()));
  const current = files.find((file) => file.path === selected) ?? files[0];
  const file = useQuery({
    queryKey: ["society", "profile-knowledge-file", agentId, current?.path, current?.updated_ms, Boolean(query.data?.legacy)],
    enabled: !sample && Boolean(current),
    retry: false,
    queryFn: ({ signal }) => get<{ path: string; content: string; updated_ms: number }>(query.data?.legacy
      ? `/api/society/memory/file?path=${encodeURIComponent(`society/${agentId}/${current!.name}`)}`
      : `/api/society/agents/${encodeURIComponent(agentId)}/knowledge/file?path=${encodeURIComponent(current!.path)}`, signal),
  });
  return <div className="flex h-full min-h-0 flex-col">
    <p className="mx-6 mb-3 text-xs leading-relaxed text-muted-foreground">{t(query.data?.legacy ? "society.profile_card.runtime_pending" : "society.profile_card.knowledge_hint")}</p>
    <div className="mx-6 mb-3 flex items-center gap-2">
      <Search size={16} className="shrink-0 text-muted-foreground" aria-hidden />
      <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("society.profile_card.search")} aria-label={t("society.profile_card.search")} className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring" />
      <Button size="sm" variant="ghost" disabled={sample || query.isFetching || file.isFetching} aria-label={t("society.profile_card.refresh")} onClick={() => { void query.refetch(); if (current) void file.refetch(); }}><RefreshCw size={16} aria-hidden /></Button>
    </div>
    {sample || query.isError || query.isLoading || files.length === 0 ? <div className="px-6 py-4 text-sm text-muted-foreground" role={query.isError ? "alert" : "status"}>
      {t(sample ? "society.profile_card.sample_hint" : query.isError ? "society.profile_card.load_error" : query.isLoading ? "society.profile_card.loading" : "society.profile_card.empty")}
      {query.isError && <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>{t("society.profile_card.retry")}</Button>}
    </div> : <div className="grid min-h-0 flex-1 grid-rows-[140px_minmax(0,1fr)] border-t border-border sm:grid-cols-[240px_minmax(0,1fr)] sm:grid-rows-1">
      <nav aria-label={t("society.profile_card.memory")} className="overflow-y-auto border-b border-border bg-card p-2 sm:border-b-0 sm:border-r">
        {visible.length === 0 && <p className="p-3 text-xs text-muted-foreground">{t("society.profile_card.no_results")}</p>}
        {visible.map((entry) => <button type="button" key={entry.path} aria-current={entry.path === current?.path ? "true" : undefined} onClick={() => setSelected(entry.path)} className={cn("mb-1 flex w-full select-none items-start gap-2 rounded-lg p-3 text-left text-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", entry.path === current?.path && "bg-secondary")}>
          <FileText size={16} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0"><span className="block break-all font-medium">{entry.name}</span><span className="mt-1 block text-xs text-muted-foreground">{t(`society.profile_card.kind_${entry.kind}`)}</span></span>
        </button>)}
      </nav>
      <section aria-label={current?.path} className="min-w-0 overflow-auto p-5">
        <div className="mb-4 border-b border-border pb-3"><p className="break-all font-mono text-xs">{current?.path}</p><p className="mt-1 text-xs text-muted-foreground">{t("society.profile_card.read_only")}</p></div>
        {file.isLoading ? <p role="status" className="text-sm text-muted-foreground">{t("society.profile_card.loading")}</p> : file.isError ? <p role="alert" className="text-sm text-destructive">{t("society.profile_card.load_error")} <Button variant="ghost" size="sm" onClick={() => void file.refetch()}>{t("society.profile_card.retry")}</Button></p> : <MemoryMarkdown text={file.data?.content || t("society.profile_card.empty_file")} />}
      </section>
    </div>}
  </div>;
}
