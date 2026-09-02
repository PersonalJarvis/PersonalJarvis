/**
 * What a click on the Quest Board opens: a composer to post a quest and the
 * board itself — what is being worked on, what waits, what is done — as a
 * drawer over the island (one-viewer doctrine: the world keeps living behind
 * it). The list is live: every change on the backend arrives as a push and
 * re-reads it within a second. The drawer is app chrome, so it wears the
 * theme tokens; only the state dots echo the monument's scroll colours.
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ExternalLink, RotateCcw, X } from "lucide-react";

import { fill, useT } from "@/i18n";
import type { QuestState, SocietyQuestRow } from "@/lib/societyApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

import { useSocietyRoster } from "../data";
import { ageOf, groupQuests, takerKind, type QuestGroup } from "./questBoard";
import { useCancelQuest, usePostQuest, useRetryQuest, useSocietyQuests } from "./questsData";

const STATE_DOT: Record<QuestState, string> = {
  open: "#c9c2b2",
  assigned: "#ffb703",
  running: "#4cc9f0",
  done: "#06d6a0",
  failed: "#ff6f61",
  cancelled: "#8b8f9c",
};

const GROUPS: ReadonlyArray<{ key: QuestGroup; labelKey: string }> = [
  { key: "active", labelKey: "quest_group_active" },
  { key: "done", labelKey: "quest_group_done" },
  { key: "failed", labelKey: "quest_group_failed" },
  { key: "cancelled", labelKey: "quest_group_cancelled" },
];

function useNow(tickMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), tickMs);
    return () => window.clearInterval(id);
  }, [tickMs]);
  return now;
}

function QuestRow({
  row,
  agentName,
  agentColor,
  now,
}: {
  row: SocietyQuestRow;
  agentName: string | null;
  agentColor: string | null;
  now: number;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const cancel = useCancelQuest();
  const retry = useRetryQuest();
  const age = ageOf(row.done_ms ?? row.created_ms, now);
  const ageText =
    age.unit === "now" ? t("society.world.quest_age_now") : fill(t(`society.world.quest_age_${age.unit}`), { n: age.n });
  const kind = takerKind(row);
  const takerText =
    kind === "none"
      ? t("society.world.quest_no_taker")
      : fill(t(kind === "forged" ? "society.world.quest_forged" : "society.world.quest_taken_by"), {
          agent: agentName ?? row.agent_id ?? "",
        });
  const active = row.state === "open" || row.state === "assigned" || row.state === "running";
  const retryable = row.state === "failed" || row.state === "open";
  const handoff = row.result?.done || row.result?.text || "";
  const refusal = row.result?.status === "vetoed" ? row.result.reason || row.result.text || "" : "";

  return (
    <li className="rounded-md border border-border bg-background/60">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2.5 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span
          className={cn("mt-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full", row.state === "running" && "animate-pulse")}
          style={{ background: STATE_DOT[row.state] }}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">{row.title}</span>
          <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
            {agentColor && <span className="inline-block h-2 w-2 rounded-sm" style={{ background: agentColor }} aria-hidden />}
            <span className="truncate">{takerText}</span>
            <span aria-hidden>·</span>
            <span className="shrink-0">{t(`society.world.quest_state.${row.state}`)}</span>
            <span aria-hidden>·</span>
            <span className="shrink-0 tabular-nums">{ageText}</span>
          </span>
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border px-3 py-2 text-sm">
          <p className="whitespace-pre-wrap text-foreground">{row.text}</p>
          {handoff && (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {t("society.world.quest_result_label")}
              </div>
              <p className="whitespace-pre-wrap text-foreground">{handoff}</p>
            </div>
          )}
          {row.result?.open && row.result.open.length > 0 && (
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {t("society.world.quest_open_label")}
              </div>
              <ul className="list-disc pl-4 text-foreground">
                {row.result.open.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          {refusal && <p className="text-destructive">{fill(t("society.world.quest_refused"), { reason: refusal })}</p>}
          <div className="flex gap-2 pt-1">
            {active && (
              <button
                type="button"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate([row.quest_id])}
                className="inline-flex h-7 items-center gap-1.5 rounded-md bg-secondary px-2.5 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
              >
                <X size={12} />
                {t("society.world.quest_cancel")}
              </button>
            )}
            {retryable && (
              <button
                type="button"
                disabled={retry.isPending}
                onClick={() => retry.mutate([row.quest_id])}
                className="inline-flex h-7 items-center gap-1.5 rounded-md bg-secondary px-2.5 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
              >
                <RotateCcw size={12} />
                {t("society.world.quest_retry")}
              </button>
            )}
          </div>
          {(cancel.isError || retry.isError) && (
            <p className="text-xs text-destructive">{String(cancel.error ?? retry.error)}</p>
          )}
        </div>
      )}
    </li>
  );
}

export function QuestBoardDrawer({ onClose }: { onClose: () => void }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const quests = useSocietyQuests();
  const roster = useSocietyRoster();
  const post = usePostQuest();
  const [text, setText] = useState("");
  const now = useNow();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const agents = useMemo(() => {
    const byId = new Map<string, { name: string; color: string }>();
    for (const a of roster.data?.agents ?? []) byId.set(a.agentId, { name: a.name, color: a.palette.accent });
    return byId;
  }, [roster.data]);

  const groups = useMemo(() => groupQuests(quests.data ?? []), [quests.data]);
  const total = quests.data?.length ?? 0;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const clean = text.trim();
    if (!clean || post.isPending) return;
    post.mutate([clean, ""], { onSuccess: () => setText("") });
  };

  return (
    <aside
      className="absolute inset-y-3 right-3 z-30 flex w-[360px] max-w-[85%] flex-col overflow-hidden rounded-lg border border-border bg-popover text-foreground shadow-float"
      role="dialog"
      aria-label={t("society.world.drawer_quests_title")}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold tracking-tight">{t("society.world.drawer_quests_title")}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("society.world.drawer_quests_hint")}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("society.world.quest_close")}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X size={16} />
        </button>
      </header>
      <form onSubmit={submit} className="border-b border-border px-4 py-3">
        <label htmlFor="sw-quest-text" className="sr-only">
          {t("society.world.quest_post")}
        </label>
        <textarea
          id="sw-quest-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") submit(e);
          }}
          rows={3}
          placeholder={t("society.world.quest_compose_placeholder")}
          className="w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span className="text-xs text-muted-foreground">{t("society.world.quest_compose_hint")}</span>
          <button
            type="submit"
            disabled={!text.trim() || post.isPending}
            className="inline-flex h-8 items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {post.isPending ? t("society.world.quest_posting") : t("society.world.quest_post")}
          </button>
        </div>
        {post.isError && <p className="mt-1.5 text-xs text-destructive">{String(post.error)}</p>}
      </form>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {quests.isLoading && <p className="text-sm text-muted-foreground">{t("society.world.quest_loading")}</p>}
        {quests.isError && <p className="text-sm text-destructive">{String(quests.error)}</p>}
        {quests.data && total === 0 && <p className="text-sm text-muted-foreground">{t("society.world.quest_empty")}</p>}
        {GROUPS.filter((g) => groups[g.key].length > 0).map((g) => (
          <section key={g.key} className="mb-4">
            <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {t(`society.world.${g.labelKey}`)}
              <span className="tabular-nums">{groups[g.key].length}</span>
            </h3>
            <ul className="space-y-1.5">
              {groups[g.key].map((row) => {
                const agent = row.agent_id ? agents.get(row.agent_id) : undefined;
                return (
                  <QuestRow
                    key={row.quest_id}
                    row={row}
                    agentName={agent?.name ?? null}
                    agentColor={agent?.color ?? null}
                    now={now}
                  />
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      <footer className="border-t border-border px-4 py-3">
        <button
          type="button"
          onClick={() => setActiveSection("agents")}
          className="inline-flex h-8 items-center gap-2 rounded-md bg-secondary px-3 text-sm font-medium text-foreground hover:bg-muted"
        >
          <ExternalLink size={14} />
          {t("society.world.quest_open_ledger")}
        </button>
      </footer>
    </aside>
  );
}
