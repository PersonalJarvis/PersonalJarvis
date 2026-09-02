/**
 * The Quest Board panel — small and quiet, the way a game's quest list sits
 * at the edge of the screen: a one-line box to post a quest, then compact
 * cards with a thin progress bar. A running quest shows what its agent is
 * doing right now (the latest step and sentence from the turn); a waiting
 * one says whom it waits for and why; a finished one folds its handoff
 * behind a tap. Live: every backend change arrives as a push within a second.
 * App chrome, so theme tokens; only the state colours echo the monument.
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight, Plus, RotateCcw, X } from "lucide-react";

import { fill, useT } from "@/i18n";
import type { QuestState, SocietyQuestRow } from "@/lib/societyApi";
import { cn } from "@/lib/utils";

import { useSocietyRoster } from "../data";
import { ageOf, groupQuests, takerKind } from "./questBoard";
import { useCancelQuest, usePostQuest, useRetryQuest, useSocietyQuests } from "./questsData";

const STATE_COLOR: Record<QuestState, string> = {
  open: "#ffb703",
  assigned: "#ffb703",
  running: "#4cc9f0",
  done: "#06d6a0",
  failed: "#ff6f61",
  cancelled: "#8b8f9c",
};

function ProgressBar({ state }: { state: QuestState }) {
  const color = STATE_COLOR[state];
  const width = state === "done" ? "100%" : state === "failed" || state === "cancelled" ? "100%" : state === "running" ? "60%" : "18%";
  return (
    <div className="mt-1.5 h-[3px] w-full overflow-hidden rounded-full bg-foreground/10" aria-hidden>
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", state === "running" && "sw-quest-shimmer")}
        style={{ width, background: color, opacity: state === "cancelled" ? 0.5 : 1 }}
      />
    </div>
  );
}

function QuestCard({
  row,
  agentName,
  now,
  onOpenAgent,
}: {
  row: SocietyQuestRow;
  agentName: string | null;
  now: number;
  onOpenAgent?: (agentId: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const cancel = useCancelQuest();
  const retry = useRetryQuest();
  const agent = agentName ?? row.agent_id ?? "";
  const kind = takerKind(row);
  const waiting = row.state === "open" && row.result?.status === "waiting";
  const active = row.state === "open" || row.state === "assigned" || row.state === "running";
  const age = ageOf(row.done_ms ?? row.created_ms, now);
  const ageText =
    age.unit === "now" ? t("society.world.quest_age_now") : fill(t(`society.world.quest_age_${age.unit}`), { n: age.n });

  let line: string;
  if (row.state === "running") {
    const progress = row.result?.progress ?? [];
    line = row.result?.live || progress[progress.length - 1] || fill(t("society.world.quest_taken_by"), { agent });
  } else if (waiting) {
    line = fill(
      t(row.result?.blocker === "approval" ? "society.world.quest_wait_approval" : "society.world.quest_wait_busy"),
      { agent },
    );
  } else if (row.state === "failed") {
    line = row.result?.status === "vetoed" ? fill(t("society.world.quest_refused"), { reason: row.result.reason ?? "" }) : row.result?.done || t("society.world.quest_state.failed");
  } else if (row.state === "done") {
    line = row.result?.done || row.result?.text || t("society.world.quest_state.done");
  } else if (kind === "none") {
    line = t("society.world.quest_no_taker");
  } else {
    line = fill(t(kind === "forged" ? "society.world.quest_forged" : "society.world.quest_taken_by"), { agent });
  }

  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <li className="rounded-md border border-border/70 bg-background/70 px-2.5 py-2">
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-start gap-2 text-left" aria-expanded={open}>
        <span className="mt-[3px] inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: STATE_COLOR[row.state] }} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[12.5px] font-medium leading-4 text-foreground">{row.title}</span>
            <span className="shrink-0 text-[10.5px] tabular-nums text-muted-foreground">{ageText}</span>
          </span>
          <span className="mt-0.5 block truncate text-[11px] leading-4 text-muted-foreground">{line}</span>
          <ProgressBar state={row.state} />
        </span>
        <Chevron size={12} className="mt-[3px] shrink-0 text-muted-foreground" aria-hidden />
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 border-t border-border/70 pt-2 text-[11.5px] leading-4">
          <p className="whitespace-pre-wrap text-foreground">{row.text}</p>
          {agent && (
            <p className="text-muted-foreground">
              {fill(t(kind === "forged" ? "society.world.quest_forged" : "society.world.quest_taken_by"), { agent })}
            </p>
          )}
          {(row.result?.progress?.length ?? 0) > 0 && (
            <ul className="space-y-0.5 text-muted-foreground">
              {row.result.progress?.map((step, i) => (
                <li key={`${i}-${step}`} className="truncate">
                  · {step}
                </li>
              ))}
            </ul>
          )}
          {row.state === "done" && (row.result?.done || row.result?.text) && (
            <p className="whitespace-pre-wrap text-foreground">{row.result.done || row.result.text}</p>
          )}
          {row.result?.open && row.result.open.length > 0 && (
            <ul className="list-disc pl-4 text-foreground">
              {row.result.open.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-1.5 pt-0.5">
            {row.agent_id && onOpenAgent && (
              <button
                type="button"
                onClick={() => onOpenAgent(row.agent_id as string)}
                className="inline-flex h-6 items-center rounded-md bg-secondary px-2 text-[11px] font-medium text-foreground hover:bg-muted"
              >
                {fill(t("society.world.quest_open_agent"), { agent })}
              </button>
            )}
            {active && (
              <button
                type="button"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate([row.quest_id])}
                className="inline-flex h-6 items-center gap-1 rounded-md bg-secondary px-2 text-[11px] font-medium text-foreground hover:bg-muted disabled:opacity-50"
              >
                <X size={11} />
                {t("society.world.quest_cancel")}
              </button>
            )}
            {row.state === "failed" && (
              <button
                type="button"
                disabled={retry.isPending}
                onClick={() => retry.mutate([row.quest_id])}
                className="inline-flex h-6 items-center gap-1 rounded-md bg-secondary px-2 text-[11px] font-medium text-foreground hover:bg-muted disabled:opacity-50"
              >
                <RotateCcw size={11} />
                {t("society.world.quest_retry")}
              </button>
            )}
          </div>
          {(cancel.isError || retry.isError) && <p className="text-destructive">{String(cancel.error ?? retry.error)}</p>}
        </div>
      )}
    </li>
  );
}

export function QuestBoardDrawer({ onClose, onOpenAgent }: { onClose: () => void; onOpenAgent?: (agentId: string) => void }) {
  const t = useT();
  const quests = useSocietyQuests();
  const roster = useSocietyRoster();
  const post = usePostQuest();
  const [text, setText] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const names = useMemo(() => {
    const byId = new Map<string, string>();
    for (const a of roster.data?.agents ?? []) byId.set(a.agentId, a.name);
    return byId;
  }, [roster.data]);

  const groups = useMemo(() => groupQuests(quests.data ?? []), [quests.data]);
  const history = [...groups.done, ...groups.failed, ...groups.cancelled].sort(
    (a, b) => (b.done_ms ?? b.updated_ms) - (a.done_ms ?? a.updated_ms),
  );
  const total = quests.data?.length ?? 0;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const clean = text.trim();
    if (!clean || post.isPending) return;
    post.mutate([clean, ""], { onSuccess: () => setText("") });
  };

  const card = (row: SocietyQuestRow) => (
    <QuestCard key={row.quest_id} row={row} agentName={row.agent_id ? (names.get(row.agent_id) ?? null) : null} now={now} onOpenAgent={onOpenAgent} />
  );

  return (
    <aside
      className="absolute right-3 top-14 z-30 flex max-h-[min(70%,560px)] w-[272px] flex-col overflow-hidden rounded-lg border border-border bg-popover/95 text-foreground shadow-float backdrop-blur"
      role="dialog"
      aria-label={t("society.world.drawer_quests_title")}
    >
      <header className="flex items-center justify-between gap-2 px-3 pb-1.5 pt-2.5">
        <h2 className="font-display text-[13px] font-semibold tracking-tight">
          {t("society.world.drawer_quests_title")}
          {groups.active.length > 0 && <span className="ml-1.5 tabular-nums text-muted-foreground">{groups.active.length}</span>}
        </h2>
        <button type="button" onClick={onClose} aria-label={t("society.world.quest_close")} className="rounded-md p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground">
          <X size={14} />
        </button>
      </header>
      <form onSubmit={submit} className="px-3 pb-2">
        <label htmlFor="sw-quest-text" className="sr-only">
          {t("society.world.quest_post")}
        </label>
        <div className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 focus-within:ring-2 focus-within:ring-ring">
          <input
            id="sw-quest-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={t("society.world.quest_add_placeholder")}
            className="h-7 min-w-0 flex-1 bg-transparent text-[12px] text-foreground placeholder:text-muted-foreground focus:outline-none"
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={!text.trim() || post.isPending}
            aria-label={t("society.world.quest_post")}
            title={t("society.world.quest_post")}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
          >
            <Plus size={14} />
          </button>
        </div>
        {post.isError && <p className="mt-1 text-[11px] text-destructive">{String(post.error)}</p>}
      </form>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {quests.isLoading && <p className="text-[11.5px] text-muted-foreground">{t("society.world.quest_loading")}</p>}
        {quests.isError && <p className="text-[11.5px] text-destructive">{String(quests.error)}</p>}
        {quests.data && total === 0 && <p className="text-[11.5px] text-muted-foreground">{t("society.world.quest_empty")}</p>}
        {groups.active.length > 0 && <ul className="space-y-1.5">{groups.active.map(card)}</ul>}
        {history.length > 0 && (
          <div className={cn(groups.active.length > 0 && "mt-2")}>
            <button
              type="button"
              onClick={() => setShowHistory((v) => !v)}
              className="flex w-full items-center gap-1 py-1 text-[10.5px] font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground"
              aria-expanded={showHistory}
            >
              {showHistory ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
              {t("society.world.quest_group_history")}
              <span className="tabular-nums">{history.length}</span>
            </button>
            {showHistory && <ul className="space-y-1.5">{history.map(card)}</ul>}
          </div>
        )}
      </div>
    </aside>
  );
}
