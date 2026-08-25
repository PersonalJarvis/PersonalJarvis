/**
 * The Local models setup assistant — a guided chat inside the section.
 *
 * Three buttons start a canned turn on the backend (`POST …/assistant/run`
 * with a mode): "Help me set up" plans one complete setup, "Test" runs the
 * end-to-end check, "Something is broken" diagnoses. The conversation itself
 * is an ordinary agent-chat session on the `local-models` surface, so the
 * timeline, streaming, approval cards and cancel are the shared ones; only
 * the store instance is this panel's own (`assistantStore.ts`).
 *
 * When the assistant ends a turn with a ```jarvis-proposal block, the plan
 * is shown as a checklist (`SetupProposalCard`). ONE Confirm sends the
 * "Execute steps: …" message and remembers the steps; every
 * `approval_required` the runner raises for exactly those steps is answered
 * "allow" here, on the client, so the user clicks once. The ToolExecutor still
 * asks (AP-3) — the panel only answers what was already confirmed; anything
 * else keeps its ordinary card.
 *
 * Honest states, never toasts: a 409 from `/run` means the Tool Model (or its Agents-tier fallback)
 * is not usable and the sentence links to API Keys; a 404 means the backend
 * predates the assistant. The header carries the monitor's last check with a
 * "Fix" that opens diagnose mode.
 */
import { AlertTriangle, FlaskConical, Send, Sparkles, Square, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentTimeline } from "@/components/agentchat/AgentTimeline";
import type { TextBlock, TurnItem } from "@/components/agentchat/reduce";
import { Panel, SoftButton, StatusDot } from "@/components/extensions/primitives";
import { fill, useT } from "@/i18n";
import type { ApprovalDecision } from "@/lib/agentChatApi";
import { useEventStore } from "@/store/events";
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

/** The text of the last finished assistant turn — where a proposal lives. */
function lastAssistantText(items: readonly { type: string }[]): { text: string; turnId: string } | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type !== "turn") continue;
    const turn = item as TurnItem;
    // A turn still streaming has no plan yet; the previous finished one keeps
    // its card instead of the card blinking out for the length of a follow-up.
    if (turn.status !== "done") continue;
    const text = turn.blocks
      .filter((b): b is TextBlock => b.kind === "text")
      .map((b) => b.text)
      .join("\n");
    return { text, turnId: turn.id };
  }
  return null;
}

function isRunning(items: readonly { type: string }[]): boolean {
  const last = items[items.length - 1];
  return Boolean(last && last.type === "turn" && (last as TurnItem).status === "running");
}

export function AssistantPanel({
  providerId,
  serverLabel,
  request,
  onOpenApiKeys,
  onOpenServerLog,
  onClose,
}: {
  providerId: string;
  /** What the section calls the local server ("Ollama"). */
  serverLabel: string;
  /** The mode the section asked for (Help me set up / Something is not working). */
  request: AssistantRequest | null;
  onOpenApiKeys: () => void;
  /** The old "Something is not working" path: the Server tab with the log open. */
  onOpenServerLog?: () => void;
  onClose?: () => void;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
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
  const answered = useRef<Set<string>>(new Set());

  const running = isRunning(items);

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

  const run = useCallback(
    async (mode: AssistantMode) => {
      setStarting(mode);
      setBlocked(null);
      setRunError(null);
      try {
        const res = await runAssistant(providerId, mode);
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

  // The chat shows the words, the card shows the plan: the raw JSON block is
  // stripped from every finished turn's text before the timeline sees it.
  const displayItems = useMemo(
    () =>
      items.map((item) => {
        if (item.type !== "turn") return item;
        const turn = item as TurnItem;
        if (!turn.blocks.some((b) => b.kind === "text" && /```jarvis-proposal/.test((b as TextBlock).text))) {
          return item;
        }
        return {
          ...turn,
          blocks: turn.blocks.map((b) =>
            b.kind === "text" ? { ...b, text: stripProposalBlocks((b as TextBlock).text) } : b,
          ),
        };
      }),
    [items],
  );

  // The plan in the last finished turn, if any.
  const proposal: { value: Proposal; turnId: string } | null = useMemo(() => {
    const last = lastAssistantText(items);
    if (!last) return null;
    const value = proposalFromText(last.text);
    return value ? { value, turnId: last.turnId } : null;
  }, [items]);

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

  const providerLabel = useCallback((id: string) => id, []);

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

  return (
    <Panel className="p-4">
      <div className="space-y-4" data-testid="local-models-assistant">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <SoftButton
              primary
              onClick={() => void run("setup")}
              disabled={starting !== null || running}
            >
              <Sparkles className="h-3.5 w-3.5" />
              {t("local_models.assistant.action_setup")}
            </SoftButton>
            <SoftButton onClick={() => void run("test")} disabled={starting !== null || running}>
              <FlaskConical className="h-3.5 w-3.5" />
              {t("local_models.assistant.action_test")}
            </SoftButton>
            <SoftButton onClick={() => void run("diagnose")} disabled={starting !== null || running}>
              <AlertTriangle className="h-3.5 w-3.5" />
              {t("local_models.assistant.action_diagnose")}
            </SoftButton>
          </div>
          <div className="flex items-center gap-2">
            {running && (
              <SoftButton onClick={() => void cancel()} ariaLabel={t("local_models.assistant.cancel")}>
                <Square className="h-3.5 w-3.5" />
                {t("local_models.assistant.cancel")}
              </SoftButton>
            )}
            {onClose && (
              <SoftButton onClick={onClose} ariaLabel={t("local_models.assistant.close")}>
                <X className="h-3.5 w-3.5" />
              </SoftButton>
            )}
          </div>
        </div>

        {health && checkedAt && (
          <p className="flex flex-wrap items-center gap-3 text-sm" data-testid="assistant-health">
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
                className="text-sm font-medium text-primary underline-offset-4 hover:underline"
                onClick={() => void run("diagnose")}
                data-testid="assistant-health-fix"
              >
                {t("local_models.assistant.fix")}
              </button>
            )}
          </p>
        )}

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
          <p className="text-sm text-muted-foreground">
            {starting ? t("local_models.assistant.starting") : t("local_models.assistant.empty")}
          </p>
        )}

        {items.length > 0 && (
          <div className="flex max-h-[60vh] flex-col gap-5 overflow-y-auto pr-1" data-testid="assistant-timeline">
            <AgentTimeline
              items={displayItems}
              assistantName={assistantName}
              providerLabel={providerLabel}
              onDecide={onDecide}
            />
          </div>
        )}

        {proposal && (
          <SetupProposalCard
            key={proposal.turnId}
            proposal={proposal.value}
            onConfirm={onConfirm}
            busy={running || busy}
            confirmedHash={confirmed?.hash ?? null}
          />
        )}

        {proposal?.value.brain_switch && !running && (
          <BrainSwitchCard
            provider={proposal.value.brain_switch.provider}
            why={proposal.value.brain_switch.why}
            serverLabel={serverLabel}
          />
        )}

        <form
          className="flex items-end gap-2"
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
            rows={2}
            disabled={!activeSessionId}
            placeholder={
              activeSessionId
                ? t("local_models.assistant.composer_placeholder")
                : t("local_models.assistant.composer_locked")
            }
            aria-label={t("local_models.assistant.composer_placeholder")}
            data-testid="assistant-composer"
            className={cn(
              "min-h-[2.5rem] flex-1 resize-none rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground",
              "placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40",
              "disabled:cursor-not-allowed disabled:opacity-60",
            )}
          />
          <SoftButton
            primary
            ariaLabel={t("local_models.assistant.send")}
            onClick={() => void submit()}
            disabled={!activeSessionId || !draft.trim() || busy || running}
          >
            <Send className="h-3.5 w-3.5" />
          </SoftButton>
        </form>

        {onOpenServerLog && (
          <button
            type="button"
            onClick={onOpenServerLog}
            className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            data-testid="assistant-open-server-log"
          >
            {t("local_models.assistant.open_server_log")}
          </button>
        )}
      </div>
    </Panel>
  );
}
