/**
 * The Local models setup assistant — a plain chat that shows its work.
 *
 * Three buttons start a canned turn on the backend (`POST …/assistant/run`
 * with a mode): "Help me set up" plans one complete setup, "Test" runs the
 * end-to-end check, "Something is broken" diagnoses. The conversation itself
 * is an ordinary agent-chat session on the `local-models` surface, so
 * streaming, approvals and cancel are the shared ones; only the store instance
 * is this panel's own (`assistantStore.ts`).
 *
 * Three deliberate departures from the coding-agent chat:
 *
 * 1. **The conversation scrolls inside itself.** The panel is one screen with
 *    the composer at the bottom, so opening the helper never turns the section
 *    into a page thousands of pixels tall that buries the roles below it.
 * 2. **Only the current run is shown.** Every click on a mode button starts a
 *    new run; the ones before collapse behind one "earlier messages" line
 *    instead of stacking five identical setups on top of each other.
 * 3. **Steps read as sentences** (`AssistantSteps`), not as tool names — this
 *    reader wants to follow what is being done to their machine.
 *
 * When a turn ends with a ```jarvis-proposal block, the plan is shown as a
 * checklist (`SetupProposalCard`). ONE Confirm sends the "Execute steps: …"
 * message and remembers the steps; every `approval_required` the runner raises
 * for exactly those steps is answered "allow" here, on the client, so the user
 * clicks once. The ToolExecutor still asks (AP-3) — the panel only answers what
 * was already confirmed; anything else keeps its own inline question.
 *
 * Honest states, never toasts: a 409 from `/run` means the Tool Model (or its
 * Agents-tier fallback) is not usable and the sentence links to API Keys; a 404
 * means the backend predates the assistant.
 */
import { AlertTriangle, FlaskConical, History, Send, Sparkles, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TextBlock, TimelineItem, TurnItem } from "@/components/agentchat/reduce";
import { SoftButton, StatusDot } from "@/components/extensions/primitives";
import { fill, useT } from "@/i18n";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { cn } from "@/lib/utils";

import {
  AssistantApiError,
  assistantTimestamp,
  getAssistantHealth,
  getAssistantSession,
  runAssistant,
  type AssistantHealth,
  type AssistantMode,
} from "./assistantApi";
import { AssistantSteps } from "./AssistantSteps";
import {
  matchesConfirmedStep,
  proposalFromText,
  proposalHash,
  stripProposalBlocks,
  type Proposal,
  type ProposalStep,
} from "./assistantProposal";
import { useLocalModelsAssistantStore } from "./assistantStore";
import { BrainSwitchCard, SetupProposalCard } from "./SetupProposalCard";

/** A request from the section: start this mode; `token` makes repeats distinct. */
export interface AssistantRequest {
  mode: AssistantMode;
  token: number;
}

/** The steps confirmed per session survive a close/reopen of the panel. */
const confirmedBySession = new Map<string, { hash: string; steps: ProposalStep[] }>();

function turnText(turn: TurnItem): string {
  return turn.blocks
    .filter((b): b is TextBlock => b.kind === "text")
    .map((b) => b.text)
    .join("\n");
}

/** The text of the last finished assistant turn — where a proposal lives. */
function lastAssistantText(items: readonly TimelineItem[]): { text: string; turnId: string } | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type !== "turn") continue;
    // A turn still streaming has no plan yet; the previous finished one keeps
    // its card instead of the card blinking out for the length of a follow-up.
    if (item.status !== "done") continue;
    return { text: turnText(item), turnId: item.id };
  }
  return null;
}

function isRunning(items: readonly TimelineItem[]): boolean {
  const last = items[items.length - 1];
  return Boolean(last && last.type === "turn" && last.status === "running");
}

/**
 * Where the current run begins.
 *
 * Anchored on the turn id `/run` answered with, never on a count: the socket
 * snapshot of an existing conversation can land AFTER the click, and an index
 * captured before it would put the whole history inside "the current run" —
 * which is exactly how five identical setups ended up stacked on one screen.
 * The user message that started the turn belongs to the run, hence the step
 * back. Without an id (the panel just opened), the last user message starts it.
 */
function runStartIndex(items: readonly TimelineItem[], turnId: string | null): number {
  if (turnId) {
    const at = items.findIndex((it) => it.type === "turn" && it.id === turnId);
    if (at > 0 && items[at - 1].type === "user") return at - 1;
    if (at >= 0) return at;
  }
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === "user") return i;
  }
  return 0;
}

export function AssistantPanel({
  providerId,
  serverLabel,
  request,
  onOpenApiKeys,
  onOpenServerLog,
}: {
  providerId: string;
  /** What the section calls the local server ("Ollama"). */
  serverLabel: string;
  /** The mode the section asked for (Help me set up / Something is not working). */
  request: AssistantRequest | null;
  onOpenApiKeys: () => void;
  /** The Server tab with the log open — one link for the reader who wants it raw. */
  onOpenServerLog?: () => void;
}) {
  const t = useT();
  const items = useLocalModelsAssistantStore((s) => s.timeline.items);
  const pendingApprovals = useLocalModelsAssistantStore((s) => s.timeline.pendingApprovals);
  const activeSessionId = useLocalModelsAssistantStore((s) => s.activeSessionId);
  const busy = useLocalModelsAssistantStore((s) => s.busy);
  const lastError = useLocalModelsAssistantStore((s) => s.lastError);
  const openSession = useLocalModelsAssistantStore((s) => s.openSession);
  const send = useLocalModelsAssistantStore((s) => s.send);
  const cancel = useLocalModelsAssistantStore((s) => s.cancel);
  const decide = useLocalModelsAssistantStore((s) => s.decide);
  const disconnect = useLocalModelsAssistantStore((s) => s.disconnect);

  const [health, setHealth] = useState<AssistantHealth | null>(null);
  const [starting, setStarting] = useState<AssistantMode | null>(null);
  /** The 409 sentence: no usable Tool Model or Agents-tier fallback. */
  const [blocked, setBlocked] = useState<string | null>(null);
  /** 404 from the assistant routes: the running backend predates this panel. */
  const [backendMissing, setBackendMissing] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirmed, setConfirmed] = useState<{ hash: string; steps: ProposalStep[] } | null>(null);
  /** The turn `/run` started; everything before it is "earlier". */
  const [runTurnId, setRunTurnId] = useState<string | null>(null);
  const [showEarlier, setShowEarlier] = useState(false);
  const answered = useRef<Set<string>>(new Set());
  const scroller = useRef<HTMLDivElement | null>(null);

  const running = isRunning(items);
  const start = runStartIndex(items, runTurnId);
  const current = items.slice(Math.min(start, items.length));
  const earlier = items.slice(0, Math.min(start, items.length));
  const shown = showEarlier ? items : current;

  // The one session per install: open it (and its socket) when the panel mounts.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const s = await getAssistantSession(providerId);
        if (cancelled) return;
        if (s.session_id) openSession(s.session_id);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof AssistantApiError && err.status === 404) setBackendMissing(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [providerId, openSession]);

  useEffect(() => () => disconnect(), [disconnect]);

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await getAssistantHealth(providerId));
    } catch (err) {
      if (err instanceof AssistantApiError && err.status === 404) setBackendMissing(true);
      // No health record yet is a normal state (monitor not run); nothing to show.
    }
  }, [providerId]);

  useEffect(() => {
    void loadHealth();
  }, [loadHealth]);

  useEffect(() => {
    setConfirmed(activeSessionId ? (confirmedBySession.get(activeSessionId) ?? null) : null);
    answered.current = new Set();
  }, [activeSessionId]);

  // Follow the conversation while it writes, the way a chat does.
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items, shown.length]);

  const run = useCallback(
    async (mode: AssistantMode) => {
      setStarting(mode);
      setBlocked(null);
      setRunError(null);
      setShowEarlier(false);
      try {
        const res = await runAssistant(providerId, mode);
        // Everything before this turn becomes "earlier": a second setup run is
        // a fresh start, not a fifth copy stacked under the fourth.
        setRunTurnId(res.turn_id);
        if (res.session_id !== activeSessionId) openSession(res.session_id);
      } catch (err) {
        if (err instanceof AssistantApiError && err.status === 409) setBlocked(err.message);
        else if (err instanceof AssistantApiError && err.status === 404) setBackendMissing(true);
        else setRunError(err instanceof Error ? err.message : String(err));
      } finally {
        setStarting(null);
      }
    },
    [providerId, activeSessionId, openSession],
  );

  // The section's button click is the request; run it once per token.
  const lastToken = useRef<number | null>(null);
  useEffect(() => {
    if (!request || request.token === lastToken.current) return;
    lastToken.current = request.token;
    void run(request.mode);
  }, [request, run]);

  useEffect(() => {
    if (!running) void loadHealth();
  }, [running, loadHealth]);

  // The plan in the last finished turn of the current run, if any.
  const proposal: { value: Proposal; turnId: string } | null = useMemo(() => {
    const last = lastAssistantText(current);
    if (!last) return null;
    const value = proposalFromText(last.text);
    return value ? { value, turnId: last.turnId } : null;
  }, [current]);

  const onConfirm = useCallback(
    async (steps: ProposalStep[], message: string) => {
      if (!proposal || !activeSessionId) return;
      const record = { hash: proposalHash(proposal.value), steps };
      confirmedBySession.set(activeSessionId, record);
      setConfirmed(record);
      await send(message);
    },
    [proposal, activeSessionId, send],
  );

  // One click covers the runner's questions about exactly the confirmed steps.
  useEffect(() => {
    if (!confirmed) return;
    for (const approval of pendingApprovals) {
      if (answered.current.has(approval.approvalId)) continue;
      if (!matchesConfirmedStep({ name: approval.name, input: approval.input }, confirmed.steps)) continue;
      answered.current.add(approval.approvalId);
      void decide(approval.approvalId, "allow");
    }
  }, [pendingApprovals, confirmed, decide]);

  const onDecide = useCallback(
    (approvalId: string, decision: ApprovalDecision) => {
      answered.current.add(approvalId);
      void decide(approvalId, decision);
    },
    [decide],
  );

  const submit = useCallback(async () => {
    const text = draft.trim();
    if (!text || !activeSessionId || busy || running) return;
    setDraft("");
    await send(text);
  }, [draft, activeSessionId, busy, running, send]);

  const checkedAt = assistantTimestamp(health?.checked_at);
  const healthTone =
    health?.status === "ok" ? "ok" : health?.status === "unknown" || !health ? "off" : "warn";
  const needsFix = health?.status === "error" || health?.status === "needs_setup";

  // One line that says what the helper is doing right now.
  const statusLine = running
    ? t("local_models.assistant.status_working")
    : starting
      ? t("local_models.assistant.status_starting")
      : proposal
        ? t("local_models.assistant.status_plan_ready")
        : items.length > 0
          ? t("local_models.assistant.status_idle")
          : t("local_models.assistant.status_empty");

  const busyNow = starting !== null || running;

  return (
    <div
      className="flex flex-col gap-3 rounded-xl border border-border bg-card/60"
      data-testid="local-models-assistant"
    >
      {/* Header: what it is, what it is doing, and the three things it can start. */}
      <div className="flex flex-col gap-3 border-b border-border/70 px-4 pt-4 pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-[15px] font-semibold leading-tight">
              {t("local_models.assistant.title")}
            </h3>
            <p className="mt-0.5 text-sm text-muted-foreground" data-testid="assistant-status">
              {statusLine}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <SoftButton primary onClick={() => void run("setup")} disabled={busyNow}>
              <Sparkles className="h-3.5 w-3.5" />
              {t("local_models.assistant.action_setup")}
            </SoftButton>
            <SoftButton onClick={() => void run("test")} disabled={busyNow}>
              <FlaskConical className="h-3.5 w-3.5" />
              {t("local_models.assistant.action_test")}
            </SoftButton>
            <SoftButton onClick={() => void run("diagnose")} disabled={busyNow}>
              <AlertTriangle className="h-3.5 w-3.5" />
              {t("local_models.assistant.action_diagnose")}
            </SoftButton>
            {running && (
              <SoftButton onClick={() => void cancel()} ariaLabel={t("local_models.assistant.cancel")}>
                <Square className="h-3.5 w-3.5" />
                {t("local_models.assistant.cancel")}
              </SoftButton>
            )}
          </div>
        </div>

        {health && checkedAt && (
          <p className="flex flex-wrap items-center gap-3 text-[13px]" data-testid="assistant-health">
            <StatusDot
              tone={healthTone}
              label={fill(t("local_models.assistant.last_check"), {
                when: checkedAt.toLocaleString(),
                what: health.reason || t(`local_models.assistant.health_${health.status}`),
              })}
            />
            {needsFix && (
              <button
                type="button"
                className="font-medium text-primary underline-offset-4 hover:underline"
                onClick={() => void run("diagnose")}
                data-testid="assistant-health-fix"
              >
                {t("local_models.assistant.fix")}
              </button>
            )}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-3 px-4 pb-4">
        {blocked && (
          <div
            className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
            data-testid="assistant-blocked"
          >
            <span>{blocked}</span>
            <SoftButton onClick={onOpenApiKeys}>{t("local_models.assistant.open_api_keys")}</SoftButton>
          </div>
        )}

        {backendMissing && (
          <p className="text-sm text-muted-foreground" data-testid="assistant-backend-missing">
            {t("local_models.assistant.backend_missing")}
          </p>
        )}

        {(runError || lastError) && (
          <p className="text-sm text-destructive" data-testid="assistant-error">
            {runError ?? lastError}
          </p>
        )}

        {items.length === 0 && !blocked && !backendMissing && (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border px-4 py-8 text-center">
            <p className="max-w-[46ch] text-sm text-muted-foreground">
              {t("local_models.assistant.empty")}
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {["ask_1", "ask_2", "ask_3"].map((key) => {
                const text = t(`local_models.assistant.${key}`);
                return (
                  <button
                    key={key}
                    type="button"
                    disabled={!activeSessionId || busyNow}
                    onClick={() => void send(text)}
                    className="rounded-full border border-border px-3 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    {text}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {items.length > 0 && (
          <div
            ref={scroller}
            className="flex max-h-[52vh] min-h-[9rem] flex-col gap-5 overflow-y-auto pr-1"
            data-testid="assistant-timeline"
          >
            {earlier.length > 0 && (
              <button
                type="button"
                onClick={() => setShowEarlier((v) => !v)}
                className="mx-auto inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-[12px] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                data-testid="assistant-earlier"
              >
                <History className="h-3 w-3" />
                {showEarlier
                  ? t("local_models.assistant.earlier_hide")
                  : fill(t("local_models.assistant.earlier_show"), { n: String(earlier.length) })}
              </button>
            )}

            {shown.map((item) => {
              if (item.type === "user") {
                return (
                  <div key={item.id} className="flex justify-end">
                    <p
                      className="max-w-[72%] rounded-2xl rounded-br-md bg-muted px-3.5 py-2 text-[13.5px] leading-relaxed text-foreground"
                      data-testid="assistant-you"
                    >
                      {item.text}
                    </p>
                  </div>
                );
              }
              if (item.type === "error") {
                return (
                  <p key={item.id} className="text-sm text-destructive">
                    {item.text}
                  </p>
                );
              }
              const answer = stripProposalBlocks(turnText(item));
              const live = item.status === "running";
              return (
                <div key={item.id} className="flex flex-col gap-2.5">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        live ? "animate-pulse bg-primary" : "bg-muted-foreground/40",
                      )}
                    />
                    <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                      {t("local_models.assistant.title")}
                    </span>
                    {item.model && (
                      <span className="font-mono text-[10.5px] text-muted-foreground/70">
                        {item.model}
                      </span>
                    )}
                  </div>
                  <AssistantSteps blocks={item.blocks} live={live} onDecide={onDecide} />
                  {answer && (
                    <div
                      className="flex max-w-[68ch] flex-col gap-2.5 text-[14px] leading-[1.65] text-foreground"
                      data-testid="assistant-answer"
                    >
                      {answer.split(/\n{2,}/).map((para, k) => (
                        <p key={k} className="whitespace-pre-wrap">
                          {para}
                        </p>
                      ))}
                    </div>
                  )}
                  {item.status === "error" && item.error && (
                    <p className="text-sm text-destructive">{item.error}</p>
                  )}
                  {proposal?.turnId === item.id && (
                    <>
                      <SetupProposalCard
                        proposal={proposal.value}
                        onConfirm={onConfirm}
                        busy={running || busy}
                        confirmedHash={confirmed?.hash ?? null}
                      />
                      {proposal.value.brain_switch && !running && (
                        <BrainSwitchCard
                          provider={proposal.value.brain_switch.provider}
                          why={proposal.value.brain_switch.why}
                          serverLabel={serverLabel}
                        />
                      )}
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <form
          className={cn(
            "flex items-end gap-2 rounded-xl border border-border bg-background px-2 py-1.5",
            "focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/20",
            !activeSessionId && "opacity-60",
          )}
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            rows={1}
            disabled={!activeSessionId}
            placeholder={
              activeSessionId
                ? t("local_models.assistant.composer_placeholder")
                : t("local_models.assistant.composer_locked")
            }
            aria-label={t("local_models.assistant.composer_placeholder")}
            data-testid="assistant-composer"
            className={cn(
              "max-h-40 min-h-[2rem] flex-1 resize-none bg-transparent px-1.5 py-1.5 text-[13.5px] leading-relaxed text-foreground",
              "placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed",
            )}
          />
          <button
            type="submit"
            aria-label={t("local_models.assistant.send")}
            disabled={!activeSessionId || !draft.trim() || busy || running}
            className={cn(
              "mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground",
              "transition-opacity hover:opacity-90 disabled:opacity-40",
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            )}
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </form>

        {onOpenServerLog && (
          <button
            type="button"
            onClick={onOpenServerLog}
            className="self-start text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            data-testid="assistant-open-server-log"
          >
            {t("local_models.assistant.open_server_log")}
          </button>
        )}
      </div>
    </div>
  );
}
