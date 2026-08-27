/**
 * The assistant's plan as a checklist the user confirms ONCE.
 *
 * Built from the onboarding register (numbered rows separated by hairlines)
 * so it reads as a document, not a dialog: every step is a row with a
 * checkbox, the model it pulls or assigns, a size, and a badge that says how
 * proven the model is. Unchecking a row leaves it out of the confirmation;
 * the one primary action sends "Execute steps: … (proposal v1, hash …)" and
 * the caller remembers the confirmed steps so the runner's approval cards
 * for exactly those steps are answered without another click.
 *
 * A `brain_switch` never runs by itself (the brain provider is the user's
 * lock): it is a separate card whose button calls the ordinary switch route
 * only when the user clicks it.
 */
import { Check, Download, FlaskConical, Mic, Settings2, Terminal, Wand2 } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/agentic/controls";
import { Panel, StatusDot } from "@/components/extensions/primitives";
import { Register, StepFooter, StepSection } from "@/components/onboarding/primitives";
import { switchBrainProvider } from "@/hooks/useProviders";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import {
  confirmationMessage,
  proposalHash,
  type Proposal,
  type ProposalStep,
  type ProposalStepKind,
  type ProvenLabel,
} from "./assistantProposal";

const STEP_ICON: Record<ProposalStepKind, ReactNode> = {
  install_ollama: <Terminal className="h-4 w-4" />,
  pull: <Download className="h-4 w-4" />,
  set_role: <Wand2 className="h-4 w-4" />,
  set_options: <Settings2 className="h-4 w-4" />,
  apply_voice_stack: <Mic className="h-4 w-4" />,
  test: <FlaskConical className="h-4 w-4" />,
};

function ProvenBadge({ proven }: { proven: ProvenLabel | null }) {
  const t = useT();
  if (!proven) return null;
  const tone = proven === "proven" ? "ok" : proven === "stale" ? "warn" : "busy";
  return (
    <span data-testid={`proposal-badge-${proven}`} className="shrink-0">
      <StatusDot tone={tone} label={t(`local_models.assistant.badge_${proven}`)} />
    </span>
  );
}

function formatSize(gb: number | undefined): string | null {
  if (gb === undefined || !Number.isFinite(gb) || gb <= 0) return null;
  return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1)} GB`;
}

export function SetupProposalCard({
  proposal,
  onConfirm,
  busy,
  confirmedHash,
}: {
  proposal: Proposal;
  /** Sends the confirmation message and remembers the checked steps. */
  onConfirm: (steps: ProposalStep[], message: string) => void | Promise<void>;
  /** A turn is running — the plan cannot be confirmed twice. */
  busy?: boolean;
  /** The hash of an already-confirmed plan: the card shows the receipt instead of the button. */
  confirmedHash?: string | null;
}) {
  const t = useT();
  const hash = useMemo(() => proposalHash(proposal), [proposal]);
  const [excluded, setExcluded] = useState<ReadonlySet<string>>(() => new Set());
  const [sending, setSending] = useState(false);
  const confirmed = confirmedHash === hash;

  const toggle = useCallback((id: string) => {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const chosen = proposal.steps.filter((s) => !excluded.has(s.id));

  const confirm = useCallback(async () => {
    if (chosen.length === 0 || sending) return;
    setSending(true);
    try {
      await onConfirm(chosen, confirmationMessage(proposal, chosen));
    } finally {
      setSending(false);
    }
  }, [chosen, onConfirm, proposal, sending]);

  return (
    <Panel className="p-4">
      <div data-testid="setup-proposal" data-hash={hash} className="space-y-6">
        <StepSection label={t("local_models.assistant.proposal_title")}>
          <p className="text-sm text-muted-foreground">
            {t("local_models.assistant.proposal_hint")}
          </p>
          <Register
            items={proposal.steps.map((step) => {
              const off = excluded.has(step.id);
              const size = formatSize(step.size_gb);
              return {
                key: step.id,
                icon: STEP_ICON[step.kind],
                children: (
                  <label
                    className={cn(
                      "flex cursor-pointer items-start gap-3",
                      off && "text-muted-foreground line-through decoration-border",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 shrink-0 accent-primary"
                      checked={!off && !confirmed}
                      disabled={confirmed}
                      onChange={() => toggle(step.id)}
                      aria-label={step.label}
                      data-testid={`proposal-step-${step.id}`}
                    />
                    <span className="flex min-w-0 flex-1 flex-col gap-1">
                      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <span className="font-medium text-foreground">{step.label}</span>
                        <ProvenBadge proven={step.proven} />
                      </span>
                      {(step.model || size || step.fit) && (
                        <span className="flex flex-wrap gap-x-3 font-mono text-[12px] text-muted-foreground">
                          {step.model && <span>{step.model}</span>}
                          {size && <span>{size}</span>}
                          {step.fit && <span>{step.fit}</span>}
                        </span>
                      )}
                    </span>
                  </label>
                ),
              };
            })}
          />
          {proposal.notes.length > 0 && (
            <ul className="space-y-1 text-sm text-muted-foreground">
              {proposal.notes.map((note, i) => (
                <li key={i}>{note}</li>
              ))}
            </ul>
          )}
        </StepSection>

        {confirmed ? (
          <p
            className="flex items-center gap-2 text-sm text-muted-foreground"
            data-testid="proposal-confirmed"
          >
            <Check className="h-4 w-4 text-primary" />
            {fill(t("local_models.assistant.proposal_confirmed"), { hash })}
          </p>
        ) : (
          <StepFooter
            onBack={null}
            hidePrimaryArrow
            primary={{
              label:
                chosen.length === proposal.steps.length
                  ? t("local_models.assistant.confirm_all")
                  : fill(t("local_models.assistant.confirm_some"), { count: chosen.length }),
              onClick: () => void confirm(),
              disabled: chosen.length === 0 || Boolean(busy),
              busy: sending,
              testId: "proposal-confirm",
            }}
          />
        )}
      </div>
    </Panel>
  );
}

/**
 * "Make Ollama the active brain" — the one thing the assistant may only
 * propose. The switch happens on this click and nowhere else.
 */
export function BrainSwitchCard({
  provider,
  why,
  serverLabel,
}: {
  provider: string;
  why: string;
  /** What the section calls the local server ("Ollama"). */
  serverLabel: string;
}) {
  const t = useT();
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState<string>("");

  const run = useCallback(async () => {
    setState("busy");
    setError("");
    try {
      await switchBrainProvider(provider);
      setState("done");
    } catch (err) {
      setState("error");
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [provider]);

  return (
    <Panel className="p-4">
      <div data-testid="brain-switch-card" className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="font-medium text-foreground">
            {fill(t("local_models.assistant.brain_switch_title"), { server: serverLabel })}
          </p>
          {why && <p className="text-sm text-muted-foreground">{why}</p>}
          {state === "done" && (
            <StatusDot
              tone="ok"
              label={fill(t("local_models.assistant.brain_switch_done"), { server: serverLabel })}
            />
          )}
          {state === "error" && <StatusDot tone="error" label={error} />}
        </div>
        {state !== "done" && (
          <Button
            variant="primary"
            onClick={() => void run()}
            disabled={state === "busy"}
            data-testid="brain-switch-run"
          >
            {t("local_models.assistant.brain_switch_action")}
          </Button>
        )}
      </div>
    </Panel>
  );
}
