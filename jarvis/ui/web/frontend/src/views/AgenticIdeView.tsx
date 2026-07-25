/**
 * Agentic IDE — the "code with Jarvis" section.
 *
 * Two states in one view:
 *
 * * no workspace open → a four-step wizard: folder, how many terminals, which
 *   agent + call-sign per terminal, then start,
 * * workspace open → the terminal grid (see AgenticGrid).
 *
 * The wizard order is deliberate and matches how the decision actually gets
 * made: you know the folder first, the number of panes second, and only then
 * care which agent sits in which pane. Every step is reversible and nothing is
 * started until the last one.
 *
 * i18n note: the section label and view header go through the locale files; the
 * panel copy inside is still English-only source awaiting its i18n keys.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  Download,
  FolderOpen,
  Loader2,
  Rocket,
  Sparkles,
  Terminal,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { AgenticGrid } from "@/components/agentic/AgenticGrid";
import { FolderPicker } from "@/components/agentic/FolderPicker";
import {
  endIdeSession,
  fetchIdeAgents,
  fetchIdeState,
  setFocusMode,
  startIdeSession,
  type AgentStatus,
  type AgentsResponse,
  type SessionState,
} from "@/lib/agenticIdeApi";

type Step = 0 | 1 | 2 | 3;

interface PlannedTerminal {
  agent: string;
  name: string;
}

const COUNT_CHOICES = [1, 2, 3, 4, 6, 8] as const;

/**
 * Terminal plan for ``count`` panes, preserving whatever the user already chose
 * for the panes that still exist.
 *
 * A pure function called from the click handlers on purpose. The first version
 * of this was a `useEffect` that synchronised `planned` against `count` — and
 * one of its dependencies was `meta?.suggested_names ?? []`, a fresh array on
 * every render. Setting state from an effect whose dependency is recreated each
 * render is an infinite loop; it froze the tab and only showed up because a test
 * ran out of heap. Deriving on the event that actually changes the plan has no
 * such failure mode.
 */
function buildPlan(
  count: number,
  previous: PlannedTerminal[],
  agent: string,
  names: string[],
): PlannedTerminal[] {
  return Array.from({ length: count }, (_, i) => {
    const kept = previous[i];
    if (kept) return kept;
    return { agent, name: names[i] ?? `T${i + 1}` };
  });
}

export function AgenticIdeView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [loading, setLoading] = useState(true);
  const [meta, setMeta] = useState<AgentsResponse | null>(null);
  const [session, setSession] = useState<SessionState | null>(null);
  const [focusMode, setFocus] = useState(false);
  const [busy, setBusy] = useState(false);

  const [step, setStep] = useState<Step>(0);
  const [folder, setFolder] = useState<string | null>(null);
  const [count, setCount] = useState(2);
  const [planned, setPlanned] = useState<PlannedTerminal[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [state, agents] = await Promise.all([fetchIdeState(), fetchIdeAgents()]);
      setMeta(agents);
      setSession(state.session);
      setFocus(Boolean(state.session?.focus_mode));
    } catch {
      /* backend still warming or headless — keep whatever we had */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Memoised so these arrays keep a stable identity across renders — see the
  // note on buildPlan for what an unstable one costs.
  const agents = useMemo(() => meta?.agents ?? [], [meta]);
  const suggested = useMemo(() => meta?.suggested_names ?? [], [meta]);
  const installed = useMemo(() => agents.filter((a) => a.installed), [agents]);
  const defaultAgent = installed[0]?.name ?? "claude";

  const chooseCount = (n: number) => {
    setCount(n);
    setPlanned((prev) => buildPlan(n, prev, defaultAgent, suggested));
  };

  const replayRecent = (recent: {
    path: string;
    terminals: number;
    agents: Record<string, number>;
  }) => {
    const total = Math.max(1, Math.min(recent.terminals, meta?.max_terminals ?? 12));
    setCount(total);
    const entries = Object.entries(recent.agents ?? {}).filter(([, n]) => n > 0);
    if (entries.length === 0) {
      setPlanned(buildPlan(total, [], defaultAgent, suggested));
      return;
    }
    // Expand the remembered split back into one pane per terminal, grouped the
    // way it was, then re-apply the call-sign pool in grid order.
    const expanded: string[] = [];
    for (const [agent, n] of entries) {
      for (let i = 0; i < n && expanded.length < total; i += 1) expanded.push(agent);
    }
    while (expanded.length < total) expanded.push(defaultAgent);
    setPlanned(
      expanded.map((agent, index) => ({
        agent,
        name: suggested[index] ?? `T${index + 1}`,
      })),
    );
  };

  const goToStep = (next: Step) => {
    // Entering the agent step is the moment the plan has to exist and the
    // suggested call-signs have certainly arrived.
    if (next === 2) {
      setPlanned((prev) => buildPlan(count, prev, defaultAgent, suggested));
    }
    setStep(next);
  };

  const start = async () => {
    if (!folder) return;
    setBusy(true);
    try {
      const created = await startIdeSession(folder, planned);
      setSession(created);
      setFocus(created.focus_mode);
      pushToast(
        "success",
        `Workspace open — ${created.terminals.map((x) => x.name).join(", ")}`,
      );
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    setBusy(true);
    try {
      await endIdeSession();
      setSession(null);
      setFocus(false);
      setStep(0);
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleFocus = async (enabled: boolean) => {
    try {
      setFocus(await setFocusMode(enabled));
    } catch (e) {
      pushToast("error", (e as Error).message);
    }
  };

  // ------------------------------------------------------------ running mode
  if (session) {
    return (
      <AgenticGrid
        session={session}
        focusMode={focusMode}
        onToggleFocus={(v) => void toggleFocus(v)}
        onClose={() => void close()}
        busy={busy}
      />
    );
  }

  // ------------------------------------------------------------------ wizard
  const canAdvance =
    (step === 0 && Boolean(folder)) ||
    (step === 1 && count > 0) ||
    (step === 2 && planned.every((p) => p.name.trim() && p.agent));

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Sparkles className="h-4 w-4 text-primary" />}
        title={t("nav.agentic_ide") === "nav.agentic_ide" ? "Agentic IDE" : t("nav.agentic_ide")}
        subtitle="Open a folder, run coding agents in named terminals, and talk to them."
      />

      <div className="flex-1 overflow-y-auto scrollbar-jarvis">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-6">
          <StepRail step={step} />

          {loading && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Checking this machine…
            </div>
          )}

          {meta && !meta.terminal_available && (
            <Callout tone="error">
              This machine has no usable terminal backend, so agent panes cannot
              run here. On Windows that means <code>pywinpty</code>, on macOS and
              Linux <code>ptyprocess</code> — both ship with the desktop extra.
            </Callout>
          )}

          {meta && installed.length === 0 && (
            <Callout tone="warn">
              Neither Claude Code nor Codex was found on this machine’s PATH.
              Install one from the CLIs page, then come back — the wizard picks it
              up automatically.
              <button
                type="button"
                className="btn-ghost mt-2"
                onClick={() => setActiveSection("clis")}
              >
                <Download className="h-4 w-4" />
                Open CLIs
              </button>
            </Callout>
          )}

          {step === 0 && (
            <Section
              title="Which folder should the agents work in?"
              hint="Pick the project root. Everything the agents do happens inside it."
            >
              <FolderPicker
                selected={folder}
                onSelect={setFolder}
                onSelectRecent={replayRecent}
              />
            </Section>
          )}

          {step === 1 && (
            <Section
              title="How many terminals?"
              hint="Each terminal runs its own agent, side by side in a grid."
            >
              <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
                {COUNT_CHOICES.filter((n) => n <= (meta?.max_terminals ?? 12)).map((n) => (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={count === n}
                    onClick={() => chooseCount(n)}
                    className={cn(
                      "flex aspect-square flex-col items-center justify-center gap-2 rounded-xl border p-3 transition-colors",
                      count === n
                        ? "border-primary/50 bg-primary/5"
                        : "border-border bg-card/60 hover:border-primary/30",
                    )}
                  >
                    <DotGrid n={n} active={count === n} />
                    <span
                      className={cn(
                        "text-sm font-semibold",
                        count === n ? "text-primary" : "text-foreground",
                      )}
                    >
                      {n}
                    </span>
                  </button>
                ))}
              </div>
            </Section>
          )}

          {step === 2 && (
            <Section
              title="Who runs where?"
              hint="Each terminal gets a call-sign so you can talk about it by name — “what is Mika doing?”."
            >
              <ul className="space-y-2">
                {planned.map((pane, index) => (
                  <li
                    key={index}
                    className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card/60 p-3"
                  >
                    <span className="w-6 shrink-0 text-center font-mono text-xs text-muted-foreground">
                      {index + 1}
                    </span>
                    <input
                      value={pane.name}
                      onChange={(e) =>
                        setPlanned((prev) =>
                          prev.map((p, i) =>
                            i === index ? { ...p, name: e.target.value } : p,
                          ),
                        )
                      }
                      aria-label={`Call-sign for terminal ${index + 1}`}
                      className="w-32 shrink-0 rounded-lg border border-border bg-background/60 px-3 py-1.5 text-sm outline-none focus:border-primary/50"
                      spellCheck={false}
                    />
                    <div className="flex flex-wrap items-center gap-2">
                      {agents.map((agent) => (
                        <AgentChoice
                          key={agent.name}
                          agent={agent}
                          selected={pane.agent === agent.name}
                          onSelect={() =>
                            setPlanned((prev) =>
                              prev.map((p, i) =>
                                i === index ? { ...p, agent: agent.name } : p,
                              ),
                            )
                          }
                        />
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {step === 3 && (
            <Section title="Ready to open" hint="Nothing has started yet.">
              <div className="space-y-4 rounded-xl border border-border bg-card/60 p-5">
                <div className="flex items-center gap-2 text-sm">
                  <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
                  <code className="min-w-0 truncate font-mono text-xs">{folder}</code>
                </div>
                <ul className="flex flex-wrap gap-2">
                  {planned.map((pane, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-2 rounded-lg bg-primary/10 px-3 py-1.5 text-sm text-primary"
                    >
                      <Terminal className="h-3.5 w-3.5" />
                      <span className="font-medium">{pane.name}</span>
                      <span className="text-primary/70">
                        {agents.find((a) => a.name === pane.agent)?.display_name ?? pane.agent}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-muted-foreground">
                  Once the panes are up you can turn on coding mode, which lets
                  Jarvis answer inside this workspace — and switch it off again at
                  any time.
                </p>
              </div>
            </Section>
          )}

          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              className={cn("btn-ghost", step === 0 && "invisible")}
              onClick={() => goToStep((step > 0 ? step - 1 : step) as Step)}
            >
              <ArrowLeft className="h-4 w-4" />
              Back
            </button>
            {step < 3 ? (
              <button
                type="button"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!canAdvance}
                onClick={() => goToStep((step + 1) as Step)}
              >
                Next
                <ArrowRight className="h-4 w-4" />
              </button>
            ) : (
              <button
                type="button"
                className="btn-primary disabled:cursor-not-allowed disabled:opacity-40"
                disabled={busy || !folder || planned.length === 0}
                onClick={() => void start()}
              >
                {busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Rocket className="h-4 w-4" />
                )}
                {busy ? "Opening…" : "Open workspace"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function StepRail({ step }: { step: Step }) {
  const labels = ["Folder", "Terminals", "Agents", "Start"];
  return (
    <ol className="flex items-center gap-2">
      {labels.map((label, index) => (
        <li key={label} className="flex flex-1 items-center gap-2">
          <span
            className={cn(
              "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
              index < step
                ? "bg-primary/20 text-primary"
                : index === step
                  ? "bg-primary text-background"
                  : "bg-muted text-muted-foreground",
            )}
          >
            {index < step ? <Check className="h-3.5 w-3.5" /> : index + 1}
          </span>
          <span
            className={cn(
              "truncate text-xs",
              index === step ? "text-foreground" : "text-muted-foreground",
            )}
          >
            {label}
          </span>
          {index < labels.length - 1 && (
            <span className="hidden h-px flex-1 bg-border sm:block" />
          )}
        </li>
      ))}
    </ol>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold">{title}</h3>
        <p className="text-sm text-muted-foreground">{hint}</p>
      </div>
      {children}
    </div>
  );
}

function AgentChoice({
  agent,
  selected,
  onSelect,
}: {
  agent: AgentStatus;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={!agent.installed}
      aria-pressed={selected}
      title={agent.installed ? agent.version ?? undefined : "Not installed on this machine"}
      className={cn(
        "flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition-colors",
        selected
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:border-primary/40",
        !agent.installed && "cursor-not-allowed opacity-40",
      )}
    >
      <Terminal className="h-3.5 w-3.5" />
      {agent.display_name}
    </button>
  );
}

function DotGrid({ n, active }: { n: number; active: boolean }) {
  const cols = n <= 1 ? 1 : n <= 2 ? 2 : n <= 6 ? 3 : 4;
  return (
    <div
      className="grid gap-1"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {Array.from({ length: n }).map((_, i) => (
        <span
          key={i}
          className={cn(
            "h-2 w-2 rounded-[3px]",
            active ? "bg-primary" : "bg-muted-foreground/40",
          )}
        />
      ))}
    </div>
  );
}

function Callout({
  tone,
  children,
}: {
  tone: "error" | "warn";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 rounded-lg border px-4 py-3 text-sm",
        tone === "error"
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-amber-500/40 bg-amber-500/10 text-amber-200",
      )}
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex flex-col items-start">{children}</div>
    </div>
  );
}
