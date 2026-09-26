import { useState, type FormEvent } from "react";
import { useT } from "@/i18n";
import type { SocietyQuestRow } from "@/lib/societyApi";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import { TaskOutputLinks } from "@/components/agentchat/TaskOutputLinks";
import type { SocietyAgent } from "../data";
import { useCancelQuest, usePostQuest, useRetryQuest, useSocietyQuests } from "../world/questsData";

function taskStatus(row: SocietyQuestRow, t: (key: string) => string): string {
  if (row.state === "done" && row.result?.status === "reported") return t("society.tasks.reported");
  if (row.state === "failed" && row.result?.status === "partial") return t("society.tasks.partial");
  if (row.state === "failed" && row.result?.status === "blocked") return t("society.tasks.blocked");
  return t(`society.world.quest_state.${row.state}`);
}

function TaskCard({ row, agentName, onOpenAgent }: {
  row: SocietyQuestRow;
  agentName: string;
  onOpenAgent: (agentId: string) => void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const retry = useRetryQuest();
  const cancel = useCancelQuest();
  const active = ["open", "assigned", "running"].includes(row.state);
  const summary = row.result?.done || row.result?.live || row.result?.progress?.at(-1) || "";
  return <li className="rounded-lg border border-border bg-card text-foreground" data-testid={`team-task-${row.quest_id}`}>
    <button type="button" className="flex w-full min-w-0 items-start justify-between gap-3 px-3 py-2 text-left"
      onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{row.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{summary || agentName || t("society.tasks.choosing")}</span>
      </span>
      <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-xs text-foreground">{taskStatus(row, t)}</span>
    </button>
    {expanded && <div className="space-y-2 border-t border-border px-3 py-3 text-sm">
      {agentName && <p className="text-xs text-muted-foreground">{t("society.tasks.owner")}: {agentName}</p>}
      {summary && <ChatMarkdown text={summary} />}
      <TaskOutputLinks output={row.result?.output} evidence={row.result?.evidence} />
      {row.state === "done" && row.result?.status === "reported" && <p className="text-xs text-muted-foreground">{t("society.tasks.unverified")}</p>}
      {(row.result?.open?.length ?? 0) > 0 && <div role="status" className="rounded-md bg-secondary px-3 py-2 text-xs">
        <p className="font-medium">{t("society.tasks.needs_attention")}</p>
        <ul className="mt-1 list-disc pl-4">{row.result.open?.map((item) => <li key={item}>{item}</li>)}</ul>
      </div>}
      <div className="flex flex-wrap gap-2">
        {row.agent_id && <button type="button" className="rounded-md border border-border px-2 py-1 text-xs hover:bg-secondary" onClick={() => onOpenAgent(row.agent_id!)}>{t("society.tasks.open_agent")}</button>}
        {row.state === "failed" && <button type="button" disabled={retry.isPending} className="rounded-md border border-border px-2 py-1 text-xs hover:bg-secondary disabled:opacity-50" onClick={() => retry.mutate([row.quest_id])}>{t("society.world.quest_retry")}</button>}
        {active && <button type="button" disabled={cancel.isPending} className="rounded-md border border-border px-2 py-1 text-xs hover:bg-secondary disabled:opacity-50" onClick={() => cancel.mutate([row.quest_id])}>{t("society.world.quest_cancel")}</button>}
      </div>
      {(retry.isError || cancel.isError) && <p role="alert" className="text-xs text-destructive">{String(retry.error ?? cancel.error)}</p>}
    </div>}
  </li>;
}

export function TeamTasks({ agents, onOpenAgent }: { agents: SocietyAgent[]; onOpenAgent: (agentId: string) => void }) {
  const t = useT();
  const [draft, setDraft] = useState("");
  const [showAll, setShowAll] = useState(false);
  const quests = useSocietyQuests();
  const post = usePostQuest();
  const names = new Map(agents.map((agent) => [agent.agentId, agent.name]));
  const rows = quests.data ?? [];
  const visible = showAll ? rows : rows.slice(0, 3);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || post.isPending) return;
    post.mutate([text, ""], { onSuccess: () => setDraft("") });
  };
  return <section className="shrink-0 border-b border-border bg-background px-4 py-3" aria-label={t("society.tasks.title")} data-testid="team-tasks">
    <form onSubmit={submit} className="mx-auto flex max-w-4xl items-end gap-2">
      <label className="min-w-0 flex-1">
        <span className="mb-1 block text-sm font-medium text-foreground">{t("society.tasks.title")}</span>
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={2}
          onKeyDown={(event) => { if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }}
          placeholder={t("society.tasks.placeholder")}
          className="w-full resize-y rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
      </label>
      <button type="submit" disabled={!draft.trim() || post.isPending} className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{t("society.tasks.start")}</button>
    </form>
    {post.isError && <p role="alert" className="mx-auto mt-1 max-w-4xl text-xs text-destructive">{String(post.error)}</p>}
    {quests.isError && <p role="alert" className="mx-auto mt-1 max-w-4xl text-xs text-destructive">{String(quests.error)}</p>}
    {visible.length > 0 && <div className="mx-auto mt-3 max-w-4xl">
      <ul className="grid max-h-56 gap-2 overflow-y-auto md:grid-cols-2">{visible.map((row) => <TaskCard key={row.quest_id} row={row} agentName={names.get(row.agent_id ?? "") ?? ""} onOpenAgent={onOpenAgent} />)}</ul>
      {rows.length > 3 && <button type="button" className="mt-2 text-xs text-muted-foreground underline hover:text-foreground" onClick={() => setShowAll((value) => !value)}>{t(showAll ? "society.tasks.show_less" : "society.tasks.show_all")}</button>}
    </div>}
  </section>;
}
