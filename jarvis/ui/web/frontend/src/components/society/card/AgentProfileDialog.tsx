import { useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, RefreshCw, Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { AgentSwatch } from "../AgentSwatch";
import type { SocietyAgent } from "../data";

const fieldClass = "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";

async function readJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

/** An independent profile surface; opening it never changes the active chat. */
export function AgentProfileDialog({ agent, sample, onClose }: {
  agent: SocietyAgent;
  sample: boolean;
  onClose: () => void;
}) {
  const t = useT();
  useLocaleChunk("society");
  const client = useQueryClient();
  const opener = useRef(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const lead = agent.tier === "lead";
  const [title, setTitle] = useState(agent.title);
  const [description, setDescription] = useState(agent.description);
  const [leadDraft, setLeadDraft] = useState<string | null>(null);
  const [saved, setSaved] = useState({ title: agent.title, description: agent.description });
  const [discard, setDiscard] = useState(false);
  const [tab, setTab] = useState("profile");
  const instructions = useQuery({
    queryKey: ["society", "profile-instructions", agent.agentId],
    enabled: lead && !sample,
    retry: false,
    queryFn: ({ signal }) => readJson<{ content: string; filename: string }>("/api/settings/agent-instructions", signal),
  });
  const content = lead ? (leadDraft ?? instructions.data?.content ?? "") : description;
  const dirty = lead ? leadDraft !== null && leadDraft !== (instructions.data?.content ?? "")
    : title !== saved.title || description !== saved.description;
  const save = useMutation({
    mutationFn: async () => {
      const response = await fetch(lead ? "/api/settings/agent-instructions" : `/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
        method: lead ? "PUT" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lead ? { content } : { title: title.trim(), description }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json() as Promise<{ content?: string; filename?: string }>;
    },
    onSuccess: (result) => {
      if (lead) {
        client.setQueryData(["society", "profile-instructions", agent.agentId], result);
        setLeadDraft(null);
      } else {
        setTitle(title.trim());
        setSaved({ title: title.trim(), description });
      }
      setDiscard(false);
      void client.invalidateQueries({ queryKey: ["society", "roster"] });
    },
  });
  const close = () => {
    if (save.isPending) return;
    if (dirty) setDiscard(true);
    else onClose();
  };

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) close(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/60 backdrop-blur-sm" />
        <Dialog.Content data-testid="agent-profile-dialog" onCloseAutoFocus={(event) => { event.preventDefault(); opener.current?.focus(); }} className="fixed left-1/2 top-1/2 z-50 flex h-[min(82dvh,780px)] w-[min(800px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-popover text-foreground shadow-float outline-none">
          <header className="flex shrink-0 items-center gap-4 border-b border-border p-6">
            <AgentSwatch agent={agent} size={56} />
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate text-xl font-semibold">{agent.name}</Dialog.Title>
              <Dialog.Description className="mt-1 truncate text-sm text-muted-foreground">{agent.title || t("society.profile_card.subtitle")}</Dialog.Description>
              <div className="mt-2 flex flex-wrap gap-2">
                <Badge variant="secondary">{t(`society.tier.${agent.tier}`)}</Badge>
                <Badge variant="outline">{t(`society.state.${agent.state}`)}</Badge>
                {sample && <Badge variant="outline">{t("society.sample_badge")}</Badge>}
              </div>
            </div>
            <button type="button" onClick={close} aria-label={t("society.card.close")} className="self-start rounded-lg p-2 text-muted-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><X size={18} aria-hidden /></button>
          </header>
          <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col">
            <TabsList className="mx-6 mt-4 w-fit shrink-0" aria-label={t("society.profile_card.subtitle")}>
              <TabsTrigger value="profile">{t("society.profile_card.profile")}</TabsTrigger>
              <TabsTrigger value="memory">{t("society.profile_card.memory")}</TabsTrigger>
            </TabsList>
            <TabsContent value="profile" className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5 text-sm">{t("society.profile_card.name")}<input className={fieldClass} value={agent.name} readOnly /></label>
                <label className="space-y-1.5 text-sm">{t("society.profile_card.role")}<input className={fieldClass} value={title} onChange={(event) => setTitle(event.target.value)} disabled={lead || sample || save.isPending} /></label>
              </div>
              <div className="mt-5">
                <label htmlFor="agent-profile-instructions" className="text-sm font-medium">{t("society.profile_card.instructions")}</label>
                <p className="mb-2 mt-1 text-xs leading-relaxed text-muted-foreground">{t(lead ? "society.card.instructions_hint" : "society.profile_card.instructions_hint")}</p>
                {instructions.isError ? <p role="alert" className="mb-2 text-sm text-destructive">{t("society.profile_card.load_error")} <Button variant="ghost" size="sm" onClick={() => void instructions.refetch()}>{t("society.profile_card.retry")}</Button></p> : null}
                <textarea id="agent-profile-instructions" className={cn(fieldClass, "min-h-[220px] resize-y leading-relaxed")} value={content} onChange={(event) => lead ? setLeadDraft(event.target.value) : setDescription(event.target.value)} disabled={sample || save.isPending || (lead && !instructions.data)} placeholder={t("society.card.no_description")} />
              </div>
              <dl className="mt-5 grid gap-4 rounded-xl border border-border p-4 text-sm sm:grid-cols-2">
                <div><dt className="text-xs text-muted-foreground">{t("society.profile_card.model")}</dt><dd className="mt-1 break-words">{lead || !agent.model ? t("society.card.default_brain") : `${agent.providerLabel || agent.provider} · ${agent.model}`}</dd></div>
                <div><dt className="text-xs text-muted-foreground">{t("society.profile_card.access")}</dt><dd className="mt-1">{t(`society.profile_card.access_${agent.grantMode}`)}</dd></div>
                <div><dt className="text-xs text-muted-foreground">{t("society.profile_card.id")}</dt><dd className="mt-1 break-all font-mono text-xs">{agent.agentId}</dd></div>
                <div><dt className="text-xs text-muted-foreground">{t("society.profile_card.location")}</dt><dd className="mt-1 break-all font-mono text-xs">{`society/${agent.agentId}/`}</dd></div>
              </dl>
            </TabsContent>
            <TabsContent value="memory" className="mt-3 min-h-0 flex-1 overflow-hidden"><AgentMemoryFiles agentId={agent.agentId} sample={sample} /></TabsContent>
          </Tabs>
          <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border px-6 py-4">
            {discard ? <>
              <span role="alert" className="mr-auto text-sm">{t("society.profile_card.unsaved")}</span>
              <Button variant="ghost" onClick={() => setDiscard(false)}>{t("society.profile_card.keep_editing")}</Button>
              <Button variant="secondary" onClick={onClose}>{t("society.profile_card.discard")}</Button>
            </> : <>
              {save.isError && <span role="alert" className="mr-auto text-sm text-destructive">{t("society.profile_card.save_error")}</span>}
              {save.isSuccess && !dirty && <span role="status" className="mr-auto text-sm text-muted-foreground">{t("society.profile_card.saved")}</span>}
              {sample && <span className="mr-auto text-xs text-muted-foreground">{t("society.profile_card.sample_hint")}</span>}
              <Button variant="ghost" onClick={close} disabled={save.isPending}>{t("society.card.close")}</Button>
              <Button onClick={() => save.mutate()} disabled={!dirty || sample || save.isPending}>{t(save.isPending ? "society.card.saving" : "society.card.save")}</Button>
            </>}
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

interface WikiFile { slug: string; title: string; mtime: number; size: number }
interface WikiTree { ok: boolean; folders: { name: string; files: WikiFile[] }[] }

/** Folder-qualified filenames avoid collisions between agents' memory.md files. */
export function agentMemoryFiles(tree: WikiTree, agentId: string) {
  const namespace = `society/${agentId}`;
  return tree.folders.filter((folder) => folder.name === namespace || folder.name.startsWith(`${namespace}/`))
    .flatMap((folder) => folder.files.map((file) => ({ ...file, path: `${folder.name}/${file.slug}.md` })))
    .sort((a, b) => Number(b.path === `${namespace}/memory.md`) - Number(a.path === `${namespace}/memory.md`) || b.mtime - a.mtime || a.path.localeCompare(b.path));
}

function AgentMemoryFiles({ agentId, sample }: { agentId: string; sample: boolean }) {
  const t = useT();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const tree = useQuery({
    queryKey: ["society", "profile-memory-tree"],
    enabled: !sample,
    retry: false,
    staleTime: 10_000,
    queryFn: async ({ signal }) => {
      const result = await readJson<WikiTree>("/api/wiki/tree", signal);
      if (!result.ok) throw new Error("Memory listing unavailable");
      return result;
    },
  });
  const files = tree.data ? agentMemoryFiles(tree.data, agentId) : [];
  const visible = files.filter((file) => `${file.path} ${file.title}`.toLowerCase().includes(search.toLowerCase()));
  const path = selected && files.some((file) => file.path === selected) ? selected : files[0]?.path;
  const file = useQuery({
    queryKey: ["society", "profile-memory-file", agentId, path],
    enabled: !sample && Boolean(path),
    retry: false,
    queryFn: ({ signal }) => readJson<{ path: string; content: string; updated_ms: number }>(`/api/society/memory/file?path=${encodeURIComponent(path!)}`, signal),
  });
  return <div className="flex h-full min-h-0 flex-col">
    <div className="mx-6 mb-3 flex items-center gap-2">
      <Search size={16} className="shrink-0 text-muted-foreground" aria-hidden />
      <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t("society.profile_card.search")} aria-label={t("society.profile_card.search")} className={fieldClass} />
      <Button size="sm" variant="ghost" disabled={sample || tree.isFetching || file.isFetching} aria-label={t("society.profile_card.refresh")} onClick={() => { void tree.refetch(); if (path) void file.refetch(); }}><RefreshCw size={16} aria-hidden /></Button>
    </div>
    {sample || tree.isError || tree.isLoading || files.length === 0 ? <div className="px-6 py-4 text-sm text-muted-foreground" role={tree.isError ? "alert" : "status"}>
      {t(sample ? "society.profile_card.sample_hint" : tree.isError ? "society.profile_card.load_error" : tree.isLoading ? "society.roster.loading" : "society.profile_card.empty")}
      {tree.isError && <Button size="sm" variant="ghost" onClick={() => void tree.refetch()}>{t("society.profile_card.retry")}</Button>}
    </div> : <div className="grid min-h-0 flex-1 grid-rows-[140px_minmax(0,1fr)] border-t border-border sm:grid-cols-[240px_minmax(0,1fr)] sm:grid-rows-1">
      <nav aria-label={t("society.profile_card.memory")} className="overflow-y-auto border-b border-border bg-card p-2 sm:border-b-0 sm:border-r">
        {visible.length === 0 && <p className="p-3 text-xs text-muted-foreground">{t("society.profile_card.no_results")}</p>}
        {visible.map((entry) => <button type="button" key={entry.path} aria-current={entry.path === path ? "true" : undefined} onClick={() => setSelected(entry.path)} className={cn("mb-1 flex w-full select-none items-start gap-2 rounded-lg p-3 text-left text-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", entry.path === path && "bg-secondary")}>
          <FileText size={16} className="mt-0.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0"><span className="block break-all font-medium">{entry.path.slice(`society/${agentId}/`.length)}</span><span className="mt-1 block truncate text-xs text-muted-foreground">{entry.title}</span></span>
        </button>)}
      </nav>
      <section aria-label={path} className="min-w-0 overflow-auto p-5">
        <div className="mb-4 border-b border-border pb-3"><p className="break-all font-mono text-xs">{path}</p><p className="mt-1 text-xs text-muted-foreground">{t("society.profile_card.read_only")}</p></div>
        {file.isLoading ? <p role="status" className="text-sm text-muted-foreground">{t("society.roster.loading")}</p> : file.isError ? <p role="alert" className="text-sm text-destructive">{t("society.profile_card.load_error")} <Button variant="ghost" size="sm" onClick={() => void file.refetch()}>{t("society.profile_card.retry")}</Button></p> : <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-6">{file.data?.content || t("society.profile_card.empty_file")}</pre>}
      </section>
    </div>}
  </div>;
}
