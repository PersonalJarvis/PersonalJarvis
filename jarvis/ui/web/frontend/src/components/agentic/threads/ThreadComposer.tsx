import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowUp, Hammer, ListChecks, Loader2, MessageCircleQuestion, ShieldAlert, Square, X } from "lucide-react";
import { ChatAttachmentStrip } from "@/components/agentchat/ChatAttachmentStrip";
import { ChatMarkdown } from "@/components/agentchat/ChatMarkdown";
import { ComposerTypeahead } from "@/components/agentchat/ComposerTypeahead";
import { DictationButton } from "@/components/agentchat/DictationButton";
import { runningTurn, type QuestionState, type Timeline, type ToolBlock, type TurnItem } from "@/components/agentchat/reduce";
import { useChatAttachments } from "@/components/agentchat/useChatAttachments";
import { useComposerDictation } from "@/components/agentchat/useComposerDictation";
import { useComposerTypeahead } from "@/components/agentchat/useComposerTypeahead";
import { useT } from "@/i18n";
import type { ChatAttachment, PlanDecision } from "@/lib/agentChatApi";
import { joinProviderOptions, type ComposerDraft, type ProviderOption } from "@/store/agentChat";
import { cn } from "@/lib/utils";
import { AccessPicker, AgentModelPicker, EffortPicker } from "./ThreadPickers";
import { rememberSeat, rememberedSeat, threadAgents, useThreadChatStore } from "./threadModel";

/** A message typed while the agent was still working, sent when it is free. */
interface QueuedMessage {
  id: number;
  text: string;
  attachments: ChatAttachment[];
}

/** Draft text per thread, so switching threads never loses a half-written message. */
const drafts = new Map<string, string>();
/** Queued follow-ups per thread, so switching threads never drops one. */
const queues = new Map<string, QueuedMessage[]>();

/** The open question card of the newest turn, if any. */
function openQuestion(timeline: Timeline): { block: ToolBlock; question: QuestionState } | null {
  for (let i = timeline.items.length - 1; i >= 0; i--) {
    const item = timeline.items[i];
    if (item.type !== "turn") continue;
    for (const block of item.blocks) {
      if (block.kind === "tool" && block.question && !block.question.closed) return { block, question: block.question };
    }
    return null;
  }
  return null;
}

const PLAN_PROSE = cn(
  "prose prose-neutral max-w-none text-sm leading-6 text-foreground dark:prose-invert dark:text-foreground [overflow-wrap:anywhere]",
  "prose-p:my-1.5 prose-p:text-foreground prose-li:text-foreground prose-headings:text-foreground-strong prose-strong:text-foreground-strong",
  // A plan is read inside a small card: its headings stay at reading size.
  "prose-headings:mb-1 prose-headings:mt-3 prose-h1:text-base prose-h2:text-sm prose-h3:text-sm",
  "[&>div>:first-child]:mt-0 [&>div>:last-child]:mb-0",
);

function ApprovalPanel({ timeline, onDecide }: { timeline: Timeline; onDecide: (id: string, decision: "allow" | "allow_always" | "deny") => void }) {
  const pending = timeline.pendingApprovals[0];
  if (!pending) return null;
  const input = pending.input && typeof pending.input === "object" ? pending.input as Record<string, unknown> : {};
  // Claude Code's finished plan asks here too, and the plan itself is what gets approved.
  const isPlan = pending.name === "ExitPlanMode";
  const plan = isPlan ? String(input.plan ?? "").trim() : "";
  const detail = isPlan ? "" : String(input.command ?? input.file_path ?? input.path ?? input.url ?? "");
  return <div data-testid="thread-approval" className="border-b border-border px-4 py-3">
    <div className="flex items-start gap-2.5">
      {isPlan
        ? <ListChecks aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        : <ShieldAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warning" />}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground-strong">{isPlan ? "Plan ready" : "Approval needed"}{timeline.pendingApprovals.length > 1 ? ` (${timeline.pendingApprovals.length})` : ""}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{isPlan ? "Build it, or keep planning and say what to change." : pending.summary || pending.name}</p>
        {plan && <div data-testid="thread-approval-plan" className={cn("mt-2 max-h-72 overflow-auto rounded-md bg-secondary px-3 py-2 scrollbar-jarvis", PLAN_PROSE)}><ChatMarkdown text={plan} /></div>}
        {detail && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded-md bg-secondary px-2.5 py-1.5 font-mono text-xs text-foreground scrollbar-jarvis">{detail}</pre>}
      </div>
    </div>
    <div className="mt-3 flex flex-wrap justify-end gap-2">
      <button type="button" onClick={() => onDecide(pending.approvalId, "deny")}
        className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{isPlan ? "Keep planning" : "Decline"}</button>
      {!isPlan && <button type="button" onClick={() => onDecide(pending.approvalId, "allow_always")}
        className="rounded-lg border border-border px-3 py-1.5 text-sm text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Always allow</button>}
      <button type="button" data-testid="thread-approve" onClick={() => onDecide(pending.approvalId, "allow")}
        className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{isPlan ? "Build it" : "Approve"}</button>
    </div>
  </div>;
}

/** The newest turn, when it is the timeline's last item and its plan card still waits. */
function waitingPlan(timeline: Timeline): TurnItem | null {
  const last = timeline.items[timeline.items.length - 1];
  return last && last.type === "turn" && last.plan && last.plan.decision === null ? last : null;
}

/**
 * Any coding agent's finished plan (jarvis/agent_chat/turn_prompts.py): build
 * it in the runner's build mode, or keep planning and type what to change.
 */
function PlanPanel({ timeline, provider }: { timeline: Timeline; provider: ProviderOption | null }) {
  const turn = waitingPlan(timeline);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  if (!turn?.plan) return null;
  const buildMode = turn.plan.buildMode;
  const mode = provider?.permission_modes?.find((row) => row.id === buildMode);
  const decide = async (decision: PlanDecision) => {
    setSending(true);
    setError("");
    try {
      await useThreadChatStore.getState().resolvePlan(turn.id, decision);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };
  return <div data-testid="thread-plan" className="border-b border-border px-4 py-3">
    <div className="flex items-start gap-2.5">
      <ListChecks aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground-strong">Plan ready</p>
        <p className="mt-0.5 text-sm text-muted-foreground">
          {mode ? `Build it switches access to ${mode.label} and starts building.` : "Build it starts building."} Or keep planning and type what to change.
        </p>
        {error && <p role="alert" className="mt-1 text-xs text-destructive">{error}</p>}
      </div>
    </div>
    <div className="mt-3 flex flex-wrap justify-end gap-2">
      <button type="button" disabled={sending} onClick={() => void decide("keep")}
        className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Keep planning</button>
      <button type="button" data-testid="thread-plan-build" disabled={sending} onClick={() => void decide("build")}
        className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-accent-foreground hover:opacity-90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <Hammer aria-hidden className="h-3.5 w-3.5" />Build it
      </button>
    </div>
  </div>;
}

function QuestionPanel({ timeline }: { timeline: Timeline }) {
  const open = openQuestion(timeline);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  if (!open) return null;
  const { question } = open;
  const index = question.answers.findIndex((answer) => answer === null);
  if (index < 0) return null;
  const item = question.questions[index];
  const answer = async (payload: { optionIndex: number } | { text: string }) => {
    setSending(true);
    setError("");
    try {
      await useThreadChatStore.getState().answerQuestion(question.questionId, index, payload);
      setText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };
  return <div data-testid="thread-question" className="border-b border-border px-4 py-3">
    <div className="flex items-start gap-2.5">
      <MessageCircleQuestion aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
      <div className="min-w-0 flex-1">
        <p className="text-xs text-muted-foreground">{question.asker || "The agent"} asks{question.questions.length > 1 ? ` · ${index + 1} of ${question.questions.length}` : ""}</p>
        <p className="mt-0.5 text-sm font-medium text-foreground-strong">{item.question}</p>
        <div className="mt-2 flex flex-col gap-1">
          {item.options.map((option, optionIndex) => <button key={optionIndex} type="button" disabled={sending}
            onClick={() => void answer({ optionIndex })}
            className="flex w-full items-start gap-2 rounded-lg border border-border px-3 py-2 text-left text-sm hover:bg-secondary disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <span className="min-w-0 flex-1"><span className="text-foreground">{option.label}</span>
              {option.description && <span className="block text-xs text-muted-foreground">{option.description}</span>}</span>
            {optionIndex === 0 && <span className="shrink-0 text-xs text-accent">Recommended</span>}
          </button>)}
        </div>
        <form className="mt-2 flex gap-2" onSubmit={(event) => { event.preventDefault(); if (text.trim()) void answer({ text: text.trim() }); }}>
          <input value={text} onChange={(event) => setText(event.target.value)} placeholder="Or type your own answer" disabled={sending}
            className="min-w-0 flex-1 rounded-lg border border-input bg-background px-3 py-1.5 text-sm text-foreground outline-none focus:ring-2 focus:ring-ring" />
          <button type="button" disabled={sending} onClick={() => void useThreadChatStore.getState().skipQuestion(question.questionId).catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))}
            className="rounded-lg px-3 py-1.5 text-sm text-muted-foreground hover:bg-secondary">Skip</button>
        </form>
        {error && <p role="alert" className="mt-1 text-xs text-destructive">{error}</p>}
      </div>
    </div>
  </div>;
}

/**
 * The thread's composer: a rounded card with the text box, the files that
 * will go with the message, and one row of picks — agent and model, effort,
 * access — beside the dictation mic and the send button. Files still come in
 * by paste or drop onto the card. An approval or a
 * question the agent is waiting on opens at the top of the card, where the
 * person's eyes already are.
 *
 * `prepareDraft` runs before a NEW thread's first message and says which
 * folder the thread starts in (its project, or a fresh worktree of it);
 * returning null cancels the send.
 */
export function ThreadComposer({
  threadKey,
  prepareDraft,
  placeholder = "Ask for changes, send follow-ups, or attach images",
  autoFocusNonce,
  strip,
}: {
  /** Which thread the box is typing for — the session id, or `draft:<project>`. */
  threadKey: string;
  prepareDraft: () => Promise<string | null>;
  placeholder?: string;
  autoFocusNonce: number;
  /** The strip that hangs under the card — where the agent works and on which branch. */
  strip?: ReactNode;
}) {
  const draft = useThreadChatStore((state) => state.draft);
  const timeline = useThreadChatStore((state) => state.timeline);
  const activeSessionId = useThreadChatStore((state) => state.activeSessionId);
  const activeSession = useThreadChatStore((state) => state.activeSession);
  const catalog = useThreadChatStore((state) => state.catalog);
  const connections = useThreadChatStore((state) => state.connections);
  const liveModels = useThreadChatStore((state) => state.liveModels);
  const busy = useThreadChatStore((state) => state.busy);
  const lastError = useThreadChatStore((state) => state.lastError);
  const [value, setValueState] = useState(() => drafts.get(threadKey) ?? "");
  const [queue, setQueueState] = useState<QueuedMessage[]>(() => queues.get(threadKey) ?? []);
  // After Stop the queue waits: the person halted the agent, it must not start the next job.
  const [paused, setPaused] = useState(false);
  const queueKey = useRef(threadKey);
  queueKey.current = threadKey;
  const setQueue = useCallback((update: (current: QueuedMessage[]) => QueuedMessage[]) => {
    setQueueState((current) => {
      const next = update(current);
      if (next.length) queues.set(queueKey.current, next);
      else queues.delete(queueKey.current);
      return next;
    });
  }, []);
  const [problem, setProblem] = useState("");
  const [starting, setStarting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const cardRef = useRef<HTMLDivElement | null>(null);
  const queueId = useRef(0);
  const t = useT();

  const providers = useMemo(
    () => threadAgents(catalog ? joinProviderOptions(catalog.providers, connections) : []),
    [catalog, connections],
  );
  const provider = providers.find((option) => option.id === draft.provider) ?? null;
  const running = runningTurn(timeline) !== null || Boolean(activeSession?.running);
  const started = timeline.items.length > 0;

  // A coding thread only runs on a coding agent: an API row left in the draft
  // from elsewhere moves to the first connected CLI.
  useEffect(() => {
    if (activeSessionId || providers.length === 0) return;
    if (providers.some((option) => option.id === draft.provider && option.connected)) return;
    const seat = rememberedSeat();
    const remembered = seat && providers.find((option) => option.id === seat.provider && option.connected);
    if (remembered && seat) { void useThreadChatStore.getState().setDraft(seat); return; }
    const first = providers.find((option) => option.connected) ?? providers[0];
    if (first && first.id !== draft.provider) void useThreadChatStore.getState().setDraft({ provider: first.id });
  }, [activeSessionId, providers, draft.provider]);

  const setValue = useCallback((next: string) => {
    setValueState(next);
    drafts.set(threadKey, next);
  }, [threadKey]);

  // The dictated words append to whatever the box holds when they land.
  const setDictated = useCallback((next: string | ((current: string) => string)) => {
    setValue(typeof next === "function" ? next(drafts.get(threadKey) ?? "") : next);
  }, [setValue, threadKey]);
  const dictation = useComposerDictation(setDictated, () => void submit());

  // Another thread: its own half-written text, its own queue.
  useEffect(() => {
    setValueState(drafts.get(threadKey) ?? "");
    setQueueState(queues.get(threadKey) ?? []);
    setPaused(false);
    setProblem("");
  }, [threadKey]);

  useEffect(() => {
    if (autoFocusNonce > 0) textareaRef.current?.focus();
  }, [autoFocusNonce]);

  // Grow with the text, up to a third of the window.
  useEffect(() => {
    const box = textareaRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, Math.max(160, window.innerHeight / 3))}px`;
  }, [value]);

  const files = useChatAttachments(
    { sessionId: activeSessionId, cwd: draft.cwd, provider: draft.provider, surface: "agent" },
    (message) => setProblem(message),
    { owner: useThreadChatStore, key: threadKey },
  );
  const triggers = useMemo(() => provider?.typeahead ?? [], [provider]);
  const typeahead = useComposerTypeahead(textareaRef, value, setValue, {
    surface: "agent",
    provider: draft.provider,
    cwd: draft.cwd,
    triggers,
  });

  const dispatch = useCallback(async (text: string, attachments: ChatAttachment[]) => {
    const store = useThreadChatStore.getState();
    if (!store.activeSessionId) {
      setStarting(true);
      try {
        const cwd = await prepareDraft();
        if (cwd === null) return false;
        if (cwd !== store.draft.cwd) await store.setDraft({ cwd });
      } catch (error) {
        setProblem(error instanceof Error ? error.message : String(error));
        return false;
      } finally {
        setStarting(false);
      }
    }
    await useThreadChatStore.getState().send(text, attachments);
    return !useThreadChatStore.getState().lastError;
  }, [prepareDraft]);

  // A queued message goes out the moment the agent is free again.
  useEffect(() => {
    if (paused || running || busy || starting || queue.length === 0 || !activeSessionId) return;
    const [next] = queue;
    const key = threadKey;
    setQueue((current) => current.filter((row) => row.id !== next.id));
    void dispatch(next.text, next.attachments).then((sent) => {
      if (sent) return;
      // Not delivered: back to the front of ITS thread's line, and wait for the person.
      queues.set(key, [next, ...(queues.get(key) ?? [])]);
      if (queueKey.current === key) {
        setQueueState(queues.get(key) ?? []);
        setPaused(true);
      }
    });
  }, [paused, running, busy, starting, queue, activeSessionId, dispatch, setQueue, threadKey]);

  const submit = async () => {
    // Send during a dictation ends it first; the message goes once the words land.
    if (dictation.dictating) {
      dictation.stopAndSend();
      return;
    }
    const text = value.trim();
    if (!text && files.attachments.length === 0) return;
    if (files.analyzing > 0 || starting) return;
    if (!provider || !provider.connected) {
      setProblem("Choose a connected coding agent first.");
      return;
    }
    const attachments = files.attachments;
    setValue("");
    files.clear();
    setProblem("");
    setPaused(false);
    if (running || busy) {
      setQueue((current) => [...current, { id: ++queueId.current, text, attachments }]);
      return;
    }
    const sent = await dispatch(text, attachments);
    if (!sent && !useThreadChatStore.getState().activeSessionId) {
      // Nothing was created: give the words back instead of losing them.
      setValue(text);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (typeahead.onKeyDown(event)) return;
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  };

  // A pick the person made is the seat every new thread starts on.
  const pick = async (patch: Partial<Pick<ComposerDraft, "provider" | "model" | "effort" | "permissionMode">>) => {
    const store = useThreadChatStore.getState();
    await store.setDraft(patch);
    rememberSeat(useThreadChatStore.getState().draft);
  };

  const decide = (approvalId: string, decision: "allow" | "allow_always" | "deny") => {
    void useThreadChatStore.getState().decide(approvalId, decision);
  };

  const canSend = (value.trim().length > 0 || files.attachments.length > 0 || dictation.dictating) && files.analyzing === 0 && !starting;
  const error = problem || lastError || "";

  return <div className="mx-auto w-full max-w-3xl">
    {queue.length > 0 && <div className="mb-2 flex flex-col gap-1 px-2" data-testid="thread-queue">
      {paused && !running && <div className="flex items-center justify-between gap-2 px-1 text-xs text-muted-foreground">
        <span>Queue paused after the agent stopped.</span>
        <button type="button" onClick={() => setPaused(false)} className="rounded px-1.5 py-0.5 text-foreground hover:bg-secondary">Send next</button>
      </div>}
      {queue.map((entry) => <div key={entry.id} className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-sm">
        <span className="shrink-0 text-xs text-muted-foreground">Queued</span>
        <span className="min-w-0 flex-1 truncate text-foreground">{entry.text || `${entry.attachments.length} file(s)`}</span>
        <button type="button" aria-label="Remove queued message" onClick={() => setQueue((current) => current.filter((row) => row.id !== entry.id))}
          className="rounded p-0.5 text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
      </div>)}
    </div>}
    {error && <p role="alert" className="mb-2 px-4 text-xs text-destructive">{error}</p>}
    <div ref={cardRef} {...files.dragHandlers}
      className={cn(
        "relative z-10 overflow-hidden rounded-3xl border border-border bg-card shadow-[0_8px_24px_-12px_rgb(var(--scrim-rgb)/0.5)] transition-colors focus-within:border-border-strong",
        files.dragging && "border-accent ring-2 ring-accent/40",
      )}>
      <ApprovalPanel timeline={timeline} onDecide={decide} />
      <QuestionPanel timeline={timeline} />
      {!running && <PlanPanel timeline={timeline} provider={provider} />}
      {(files.attachments.length > 0 || files.analyzing > 0) && <div className="px-3 pt-3 sm:px-4">
        <ChatAttachmentStrip attachments={files.attachments} analyzing={files.analyzing}
          onRemove={files.remove} previews={files.previews} look="thumbnail" />
      </div>}
      <textarea ref={textareaRef} value={value} rows={2} data-testid="thread-composer-input"
        aria-label="Message the coding agent"
        placeholder={running ? "Queue a follow-up for when the agent is done" : placeholder}
        onChange={(event) => { setValue(event.target.value); typeahead.refresh(); }}
        onKeyDown={onKeyDown}
        onKeyUp={() => typeahead.refresh()}
        onClick={() => typeahead.refresh()}
        onBlur={() => typeahead.blur()}
        onPaste={files.onPaste}
        className="block max-h-[40vh] min-h-[84px] w-full resize-none bg-transparent px-4 pb-2 pt-4 text-base leading-6 text-foreground outline-none placeholder:text-faint-foreground sm:px-5" />
      <div className="flex min-w-0 items-center justify-between gap-2 px-3 pb-3 sm:px-4 sm:pb-4">
        <div className="-ms-1 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]">
        <AgentModelPicker providers={providers} liveModels={liveModels} draft={draft}
          lockedProvider={started && activeSessionId ? draft.provider : null}
          onPick={(nextProvider, model) => void pick(nextProvider !== draft.provider ? { provider: nextProvider, model } : { model })} />
        <EffortPicker provider={provider} draft={draft} liveModels={liveModels} separated
          onPick={(effort) => void pick({ effort })} />
        <AccessPicker provider={provider} draft={draft} separated
          onPick={(permissionMode) => void pick({ permissionMode })} />
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <DictationButton dictating={dictation.dictating} onToggle={dictation.toggle}
            startLabel={t("chats_view.dictation_start")} stopLabel={t("chats_view.dictation_stop")} shape="round" />
          {running && !canSend
            ? <button type="button" aria-label="Stop the agent" title="Stop" data-testid="thread-stop"
              onClick={() => { setPaused(true); void useThreadChatStore.getState().cancel(); }}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-background transition-transform duration-150 hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <Square className="h-3 w-3 fill-current" />
            </button>
            : <button type="button" aria-label={running ? "Queue message" : "Send message"} title={running ? "Queue for when the agent is done" : "Send"}
              data-testid="thread-send" disabled={!canSend} onClick={() => void submit()}
              className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-accent-foreground shadow-[0_1px_2px_rgb(var(--accent-rgb)/0.3)] transition-[opacity,transform] duration-150 hover:scale-105 hover:opacity-95 disabled:opacity-40 disabled:hover:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {starting || busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
            </button>}
        </div>
      </div>
    </div>
    {strip}
    <ComposerTypeahead anchorRef={cardRef} open={typeahead.open} trigger={typeahead.token?.trigger ?? null}
      items={typeahead.items} loading={typeahead.loading} activeIndex={typeahead.activeIndex}
      onHover={typeahead.setActiveIndex} onPick={typeahead.pick} />
  </div>;
}
