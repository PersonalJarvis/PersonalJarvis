/**
 * CLI Test Hub — drive any connected CLI through a plain-language instruction.
 *
 * The user types what they want ("liste meine Google-Cloud-Projekte"), Jarvis
 * picks the matching `cli_<name>` tool, runs a real command through the safety
 * gate, and this view renders the full evidence trail: the chosen tool, the
 * exact command, the resolved risk tier, exit code, stdout/stderr, duration,
 * and Jarvis's natural-language summary.
 *
 * Backend contract:
 *   POST /api/clis/test-run  (see useCliTestRun / the design spec)
 *   GET  /api/clis           (connected-CLI list, via useClisList)
 *
 * UI strings route through the in-house i18n system (English source, with de/es
 * translations); all code-level identifiers stay English per project policy.
 */
import { useMemo, useState } from "react";
import {
  Wand2,
  Play,
  Loader2,
  Terminal,
  ArrowRight,
  ShieldAlert,
  ShieldCheck,
  Shield,
  Ban,
  ChevronRight,
  ExternalLink,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  useCliTestRun,
  useClisList,
  type CliSummary,
  type RiskTier,
  type TestRunResponse,
} from "@/hooks/useClis";

// ---------------------------------------------------------------------------
// Risk-tier visual language (brand severity coding — gold/charcoal + strokes)
// ---------------------------------------------------------------------------

interface RiskStyle {
  label: string;
  /** Tailwind classes for the badge chrome. */
  badge: string;
  /** 3px left-stroke colour for the result panel (brand severity coding). */
  stroke: string;
  Icon: React.ComponentType<{ className?: string }>;
}

const RISK_STYLES: Record<RiskTier, RiskStyle> = {
  safe: {
    label: "safe",
    badge: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
    stroke: "border-l-emerald-500/70",
    Icon: ShieldCheck,
  },
  monitor: {
    // Monitor = brand gold: runs, but observed/logged.
    label: "monitor",
    badge: "border-primary/40 bg-primary/10 text-primary",
    stroke: "border-l-primary/70",
    Icon: Shield,
  },
  ask: {
    label: "ask",
    badge: "border-amber-500/40 bg-amber-500/10 text-amber-400",
    stroke: "border-l-amber-500/70",
    Icon: ShieldAlert,
  },
  block: {
    label: "block",
    badge: "border-destructive/50 bg-destructive/10 text-destructive",
    stroke: "border-l-destructive/80",
    Icon: Ban,
  },
};

function RiskBadge({ tier }: { tier: RiskTier | null }) {
  const t = useT();
  if (!tier || !(tier in RISK_STYLES)) {
    return (
      <span
        data-testid="risk-badge"
        data-risk="unknown"
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-0.5 text-[11px] font-semibold text-muted-foreground"
      >
        {t("cli_test_hub_view.no_risk_tier")}
      </span>
    );
  }
  const style = RISK_STYLES[tier];
  const Icon = style.Icon;
  return (
    <span
      data-testid="risk-badge"
      data-risk={tier}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wider",
        style.badge,
      )}
    >
      <Icon className="h-3 w-3" />
      {style.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function CliTestHubView() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const { data, isLoading: listLoading, error: listError } = useClisList();
  const testRun = useCliTestRun();

  const [instruction, setInstruction] = useState("");
  const [cliHint, setCliHint] = useState<string>("");

  const connected = useMemo<CliSummary[]>(
    () => (data?.clis ?? []).filter((c) => c.status === "connected"),
    [data],
  );

  const canRun = instruction.trim().length > 0 && !testRun.isPending;

  const handleRun = () => {
    const trimmed = instruction.trim();
    if (!trimmed || testRun.isPending) return;
    testRun.mutate({
      instruction: trimmed,
      cli_hint: cliHint || undefined,
    });
  };

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Wand2 className="h-4 w-4 text-primary" />}
        title="CLI Test Hub"
        subtitle={`${t("cli_test_hub_view.subtitle_tell")} ${assistantName} ${t("cli_test_hub_view.subtitle_rest")}`}
      />

      <ScrollArea className="flex-1">
        <div className="mx-auto w-full max-w-3xl space-y-5 p-6">
          <ConnectedClisPanel
            clis={connected}
            isLoading={listLoading}
            error={listError as Error | null}
          />

          <PromptPanel
            instruction={instruction}
            onInstructionChange={setInstruction}
            cliHint={cliHint}
            onCliHintChange={setCliHint}
            connectedClis={connected}
            canRun={canRun}
            isPending={testRun.isPending}
            onRun={handleRun}
          />

          {testRun.isPending && <ResultSkeleton />}

          {!testRun.isPending && testRun.error && (
            <RequestErrorPanel error={testRun.error as Error} />
          )}

          {!testRun.isPending && testRun.data && (
            <ResultPanel result={testRun.data} />
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connected-CLIs panel
// ---------------------------------------------------------------------------

function ConnectedClisPanel({
  clis,
  isLoading,
  error,
}: {
  clis: CliSummary[];
  isLoading: boolean;
  error: Error | null;
}) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const assistantName = useEventStore((s) => s.assistantName);

  return (
    <section className="rounded-xl border border-border bg-card/40 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
          {t("cli_test_hub_view.connected_clis")}
        </h3>
        {clis.length > 0 && (
          <span className="rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">
            {clis.length} {t("cli_test_hub_view.available")}
          </span>
        )}
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t("cli_test_hub_view.loading_connected")}
        </div>
      )}

      {!isLoading && error && (
        <div
          data-testid="clis-load-error"
          className="rounded-md border border-destructive/40 border-l-[3px] border-l-destructive bg-destructive/10 p-3 text-xs text-destructive"
        >
          {t("cli_test_hub_view.list_load_failed")}: {error.message}
        </div>
      )}

      {!isLoading && !error && clis.length === 0 && (
        <div
          data-testid="clis-empty"
          className="flex flex-col items-start gap-3 rounded-md border border-border border-l-[3px] border-l-primary/50 bg-background/40 p-4"
        >
          <div className="flex items-center gap-2 text-sm font-medium">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            {t("cli_test_hub_view.no_cli_connected")}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {assistantName} {t("cli_test_hub_view.no_cli_help")}
          </p>
          <Button
            size="sm"
            variant="ghost"
            className="border border-primary/30 text-primary hover:bg-primary/10"
            onClick={() => setActiveSection("clis")}
          >
            {t("cli_test_hub_view.go_to_clis")}
            <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {!isLoading && !error && clis.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" data-testid="clis-chips">
          {clis.map((cli) => (
            <li
              key={cli.name}
              className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-card/60 px-2.5 py-1 text-xs"
              title={cli.description}
            >
              <span
                className="h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_6px_rgba(255,214,10,0.6)]"
                aria-hidden
              />
              <span className="font-medium">{cli.name}</span>
              {cli.version && (
                <span className="font-mono text-[10px] text-muted-foreground/70">
                  {cli.version}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Prompt panel
// ---------------------------------------------------------------------------

function PromptPanel({
  instruction,
  onInstructionChange,
  cliHint,
  onCliHintChange,
  connectedClis,
  canRun,
  isPending,
  onRun,
}: {
  instruction: string;
  onInstructionChange: (v: string) => void;
  cliHint: string;
  onCliHintChange: (v: string) => void;
  connectedClis: CliSummary[];
  canRun: boolean;
  isPending: boolean;
  onRun: () => void;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  // Ctrl/Cmd+Enter submits — a textarea swallows plain Enter for multi-line.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && canRun) {
      e.preventDefault();
      onRun();
    }
  };

  return (
    <section className="rounded-xl border border-border bg-card/40 p-4">
      <label htmlFor="cli-test-instruction" className="block">
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground/70">
          {t("cli_test_hub_view.instruction_label")}
        </span>
        <textarea
          id="cli-test-instruction"
          value={instruction}
          onChange={(e) => onInstructionChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isPending}
          rows={3}
          aria-label={`${t("cli_test_hub_view.instruction_to")} ${assistantName}`}
          placeholder={`${t("cli_test_hub_view.subtitle_tell")} ${assistantName}, ${t("cli_test_hub_view.placeholder_rest")}`}
          className="w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm leading-relaxed focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60"
        />
      </label>

      <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
        <label htmlFor="cli-test-hint" className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-muted-foreground/70">
            {t("cli_test_hub_view.cli_hint_label")}
          </span>
          <select
            id="cli-test-hint"
            value={cliHint}
            onChange={(e) => onCliHintChange(e.target.value)}
            disabled={isPending}
            aria-label={t("cli_test_hub_view.cli_hint_aria")}
            className="min-w-[180px] rounded-md border border-input bg-background px-3 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-60"
          >
            <option value="">{assistantName} {t("cli_test_hub_view.let_decide")}</option>
            {connectedClis.map((cli) => (
              <option key={cli.name} value={cli.name}>
                {cli.name}
              </option>
            ))}
          </select>
        </label>

        <div className="flex items-center gap-3">
          <span className="hidden text-[10px] text-muted-foreground/60 sm:inline">
            {t("cli_test_hub_view.ctrl_cmd_enter")}
          </span>
          <Button
            type="button"
            className="btn-primary"
            disabled={!canRun}
            onClick={onRun}
            aria-label={t("cli_test_hub_view.run_instruction")}
          >
            {isPending ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span className="ml-1.5">{t("cli_test_hub_view.running")}</span>
              </>
            ) : (
              <>
                <Play className="h-3.5 w-3.5" />
                <span className="ml-1.5">{t("cli_test_hub_view.run")}</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton (these runs can take several seconds)
// ---------------------------------------------------------------------------

function ResultSkeleton() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <section
      data-testid="result-skeleton"
      aria-busy="true"
      className="space-y-3 rounded-xl border border-border border-l-[3px] border-l-primary/40 bg-card/40 p-5"
    >
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin text-primary" />
        {assistantName} {t("cli_test_hub_view.picking_tool")}
      </div>
      <div className="h-3 w-2/3 animate-jarvis-pulse rounded bg-muted-foreground/15" />
      <div className="h-3 w-1/2 animate-jarvis-pulse rounded bg-muted-foreground/15" />
      <div className="h-20 w-full animate-jarvis-pulse rounded bg-muted-foreground/10" />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Request-level error (network / 5xx — the mutation itself rejected)
// ---------------------------------------------------------------------------

function RequestErrorPanel({ error }: { error: Error }) {
  const t = useT();
  return (
    <section
      data-testid="request-error"
      className="rounded-xl border border-destructive/40 border-l-[3px] border-l-destructive bg-destructive/10 p-5"
    >
      <h3 className="mb-1 text-sm font-semibold text-destructive">
        {t("cli_test_hub_view.request_failed")}
      </h3>
      <p className="break-words text-xs text-destructive/90">{error.message}</p>
      <p className="mt-2 text-[11px] text-muted-foreground">
        {t("cli_test_hub_view.backend_check_prefix")}
        <code className="mx-1 font-mono">/api/clis/test-run</code>{" "}
        {t("cli_test_hub_view.backend_check_suffix")}
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Result panel
// ---------------------------------------------------------------------------

function ResultPanel({ result }: { result: TestRunResponse }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const failed = result.ok === false || Boolean(result.error);
  // Severity stroke for the whole panel: failures dominate, otherwise the
  // resolved risk tier drives the colour; default to gold (brand accent).
  const stroke = failed
    ? "border-l-destructive/80"
    : result.risk_tier && result.risk_tier in RISK_STYLES
      ? RISK_STYLES[result.risk_tier].stroke
      : "border-l-primary/60";

  const hasSteps = result.steps && result.steps.length > 1;

  return (
    <section
      data-testid="result-panel"
      className={cn(
        "space-y-4 rounded-xl border border-border border-l-[3px] bg-card/50 p-5",
        stroke,
      )}
    >
      {/* Summary — the prominent, human-readable headline. */}
      <div>
        <div className="mb-1 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground/70">
          <Wand2 className="h-3 w-3 text-primary" />
          {assistantName} {t("cli_test_hub_view.says")}
        </div>
        <p
          data-testid="result-summary"
          className="font-display text-base leading-relaxed text-foreground"
        >
          {result.summary || (failed ? t("cli_test_hub_view.command_failed") : "—")}
        </p>
      </div>

      {/* Meta row: tool, risk tier, exit code, duration. */}
      <div className="flex flex-wrap items-center gap-2">
        <MetaChip label="Tool">
          <span data-testid="result-tool" className="font-mono">
            {result.tool_called ?? "—"}
          </span>
        </MetaChip>
        <RiskBadge tier={result.risk_tier} />
        <ExitCodeBadge code={result.exit_code} />
        {typeof result.duration_ms === "number" && (
          <MetaChip label={t("cli_test_hub_view.duration")}>
            <span data-testid="result-duration" className="tabular-nums">
              {result.duration_ms} ms
            </span>
          </MetaChip>
        )}
      </div>

      {/* The exact command. */}
      {result.command && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            {t("cli_test_hub_view.command_label")}
          </div>
          <pre
            data-testid="result-command"
            className="overflow-x-auto rounded-md border border-border bg-background px-3 py-2 font-mono text-xs text-foreground scrollbar-jarvis"
          >
            <code>{result.command}</code>
          </pre>
        </div>
      )}

      {/* Inline error (the run produced a structured error string). */}
      {result.error && (
        <div
          data-testid="result-error"
          className="rounded-md border border-destructive/40 border-l-[3px] border-l-destructive bg-destructive/10 px-3 py-2 text-xs text-destructive"
        >
          {result.error}
        </div>
      )}

      {/* stdout / stderr — scrollable monospace, only when present. */}
      {result.stdout && (
        <OutputBlock
          title="stdout"
          content={result.stdout}
          testId="result-stdout"
          tone="default"
        />
      )}
      {result.stderr && (
        <OutputBlock
          title="stderr"
          content={result.stderr}
          testId="result-stderr"
          tone="error"
        />
      )}

      {/* Multi-step plan (only render when >1 step). */}
      {hasSteps && (
        <div>
          <div className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            {t("cli_test_hub_view.steps")} ({result.steps.length})
          </div>
          <ol data-testid="result-steps" className="space-y-1.5">
            {result.steps.map((step, idx) => (
              <li
                key={`${step.tool}-${idx}`}
                className="flex items-start gap-2 rounded-md border border-border bg-background/40 px-3 py-2"
              >
                <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[10px] font-semibold text-primary tabular-nums">
                  {idx + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span className="font-mono">{step.tool}</span>
                    <ChevronRight className="h-3 w-3" />
                    <StepExitCode code={step.exit_code} />
                  </div>
                  <code className="mt-0.5 block break-all font-mono text-[11px] text-foreground">
                    {step.command}
                  </code>
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Empty result hint — no tool resolved at all. */}
      {!result.tool_called && !result.command && !result.error && (
        <div className="flex items-center gap-2 rounded-md border border-border border-l-[3px] border-l-primary/40 bg-background/40 px-3 py-2 text-xs text-muted-foreground">
          <ExternalLink className="h-3.5 w-3.5" />
          {assistantName} {t("cli_test_hub_view.no_tool_found")}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Atoms
// ---------------------------------------------------------------------------

function MetaChip({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/40 px-2.5 py-0.5 text-[11px]">
      <span className="text-muted-foreground/70">{label}</span>
      <span className="text-foreground">{children}</span>
    </span>
  );
}

function ExitCodeBadge({ code }: { code: number | null }) {
  if (code === null || code === undefined) {
    return (
      <span
        data-testid="result-exit-code"
        data-exit="null"
        className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/40 px-2.5 py-0.5 text-[11px] text-muted-foreground"
      >
        <span className="text-muted-foreground/70">Exit</span>
        <span>—</span>
      </span>
    );
  }
  const ok = code === 0;
  return (
    <span
      data-testid="result-exit-code"
      data-exit={String(code)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-0.5 text-[11px] font-semibold tabular-nums",
        ok
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
          : "border-destructive/50 bg-destructive/10 text-destructive",
      )}
    >
      <span className="font-normal text-muted-foreground/70">Exit</span>
      {code}
    </span>
  );
}

function StepExitCode({ code }: { code: number | null }) {
  if (code === null || code === undefined) {
    return <span className="text-muted-foreground/60">exit —</span>;
  }
  return (
    <span
      className={cn(
        "tabular-nums",
        code === 0 ? "text-emerald-400" : "text-destructive",
      )}
    >
      exit {code}
    </span>
  );
}

function OutputBlock({
  title,
  content,
  testId,
  tone,
}: {
  title: string;
  content: string;
  testId: string;
  tone: "default" | "error";
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
        {title}
      </div>
      <ScrollArea className="max-h-60 rounded-md border border-border bg-background">
        <pre
          data-testid={testId}
          className={cn(
            "whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed",
            tone === "error" ? "text-destructive/90" : "text-foreground/90",
          )}
        >
          {content}
        </pre>
      </ScrollArea>
    </div>
  );
}
