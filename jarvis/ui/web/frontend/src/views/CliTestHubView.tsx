/**
 * CLI Test Hub — drive any connected CLI through a plain-language instruction.
 *
 * The user types what they want ("list my Google Cloud projects"), Jarvis picks
 * the matching `cli_<name>` tool, runs a real command through the safety gate,
 * and this view renders the full evidence trail: the chosen tool, the exact
 * command, the resolved risk tier, exit code, stdout/stderr, duration, and
 * Jarvis's natural-language summary.
 *
 * Shape — a composer, then a log:
 * The instruction box is the first thing on the page and stays there; every run
 * lands underneath it, newest first, with the previous ones collapsed to one
 * line each. That is the difference between a form and a bench: comparing the
 * command Jarvis chose this time against the one it chose last time is the
 * whole point of a test hub, and the old single-slot layout threw the previous
 * answer away on every run. The log lives for the life of the view — it is a
 * scratchpad, not a record; the durable history is per-CLI under `Usage`.
 *
 * Backend contract:
 *   POST /api/clis/test-run  (see useCliTestRun / the design spec)
 *   GET  /api/clis           (connected-CLI list, via useClisList)
 *
 * UI strings route through the in-house i18n system (English source, with de/es
 * translations); all code-level identifiers stay English per project policy.
 */
import { useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Ban,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Terminal,
  Trash2,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { CliLogo } from "@/components/clis/CliLogo";
import {
  ActionMenu,
  IconButton,
  Panel,
  PanelHeader,
  SoftButton,
} from "@/components/extensions/primitives";
import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { translate, useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  useCliTestRun,
  useClisList,
  type CliSummary,
  type RiskTier,
  type TestRunResponse,
} from "@/hooks/useClis";

// ---------------------------------------------------------------------------
// Risk-tier visual language
// ---------------------------------------------------------------------------

interface RiskStyle {
  label: string;
  /** Tailwind classes for the badge chrome. */
  badge: string;
  Icon: React.ComponentType<{ className?: string }>;
}

const RISK_STYLES: Record<RiskTier, RiskStyle> = {
  safe: {
    label: "safe",
    badge: "border-emerald-500/40 bg-emerald-500/10 text-emerald-500",
    Icon: ShieldCheck,
  },
  monitor: {
    // Monitor = brand gold: runs, but observed/logged.
    label: "monitor",
    badge: "border-primary/40 bg-primary/10 text-primary",
    Icon: Shield,
  },
  ask: {
    label: "ask",
    badge: "border-foreground/40 bg-foreground/10 text-foreground",
    Icon: ShieldAlert,
  },
  block: {
    label: "block",
    badge: "border-destructive/50 bg-destructive/10 text-destructive",
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
        className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
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
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium",
        style.badge,
      )}
    >
      <Icon className="h-3 w-3" />
      {style.label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Run log
// ---------------------------------------------------------------------------

interface RunRecord {
  id: number;
  instruction: string;
  /** The CLI the user pinned, or "" when Jarvis chose freely. */
  hint: string;
  result: TestRunResponse | null;
  /** Set when the request itself failed (network / 5xx). */
  error: Error | null;
}

/**
 * The CLI a run actually used.
 *
 * The backend answers with the tool id (`cli_gcloud`), which is what a logo
 * lookup needs stripped. A run that resolved no tool falls back to whatever the
 * user pinned, so a failed run still shows which CLI was aimed at.
 */
function cliOfRun(run: RunRecord): string | null {
  const tool = run.result?.tool_called;
  if (tool && tool.startsWith("cli_")) return tool.slice(4);
  return run.hint || null;
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export function CliTestHubView() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const { data, isLoading: listLoading, error: listError, refetch, isFetching } = useClisList();
  const testRun = useCliTestRun();

  const [instruction, setInstruction] = useState("");
  const [cliHint, setCliHint] = useState<string>("");
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const nextId = useRef(1);

  const connected = useMemo<CliSummary[]>(
    () => (data?.clis ?? []).filter((c) => c.status === "connected"),
    [data],
  );

  const canRun = instruction.trim().length > 0 && !testRun.isPending;

  const record = (entry: Omit<RunRecord, "id">) => {
    const id = nextId.current++;
    setRuns((prev) => [{ ...entry, id }, ...prev]);
    // Newest run opens; the previous one folds away to its one-line summary.
    setExpanded(id);
  };

  const handleRun = () => {
    const trimmed = instruction.trim();
    if (!trimmed || testRun.isPending) return;
    const hint = cliHint;
    testRun.mutate(
      { instruction: trimmed, cli_hint: cliHint || undefined },
      {
        onSuccess: (result) =>
          record({ instruction: trimmed, hint, result, error: null }),
        onError: (err) =>
          record({ instruction: trimmed, hint, result: null, error: err as Error }),
      },
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto w-full max-w-4xl px-8 py-6">
          <PanelHeader
            title={t("cli_test_hub_view.title")}
            subtitle={`${t("cli_test_hub_view.subtitle_tell")} ${assistantName} ${t("cli_test_hub_view.subtitle_rest")}`}
            actions={
              <IconButton
                label={t("clis_view.reload")}
                onClick={() => void refetch()}
                busy={isFetching && !data}
              >
                <RefreshCw className="h-4 w-4" />
              </IconButton>
            }
          />

          <div className="mt-5 space-y-5">
            <Composer
              instruction={instruction}
              onInstructionChange={setInstruction}
              cliHint={cliHint}
              onCliHintChange={setCliHint}
              connectedClis={connected}
              canRun={canRun}
              isPending={testRun.isPending}
              onRun={handleRun}
            />

            {runs.length === 0 && !testRun.isPending && (
              <Starters
                connectedClis={connected}
                onPick={(cli, text) => {
                  setCliHint(cli);
                  setInstruction(text);
                }}
              />
            )}

            <ConnectedClisPanel
              clis={connected}
              isLoading={listLoading}
              error={listError as Error | null}
            />

            {testRun.isPending && <ResultSkeleton />}

            {runs.length > 0 && (
              <RunLog
                runs={runs}
                expanded={expanded}
                onToggle={(id) => setExpanded((cur) => (cur === id ? null : id))}
                onClear={() => {
                  setRuns([]);
                  setExpanded(null);
                }}
              />
            )}
          </div>
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer — the instruction box and everything that belongs to sending it
// ---------------------------------------------------------------------------

function Composer({
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
    <div
      className={cn(
        // One card, not a labelled form field: the border belongs to the whole
        // composer and lights up when the caret is anywhere inside it.
        "rounded-2xl border border-border bg-card/50 transition-colors",
        "focus-within:border-foreground/25",
      )}
    >
      <textarea
        id="cli-test-instruction"
        value={instruction}
        onChange={(e) => onInstructionChange(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={isPending}
        rows={3}
        aria-label={`${t("cli_test_hub_view.instruction_to")} ${assistantName}`}
        placeholder={`${t("cli_test_hub_view.subtitle_tell")} ${assistantName}, ${t("cli_test_hub_view.placeholder_rest")}`}
        className="w-full resize-none bg-transparent px-4 pb-2 pt-4 text-[15px] leading-relaxed placeholder:text-muted-foreground focus:outline-none disabled:opacity-60"
      />

      <div className="flex flex-wrap items-center gap-2 border-t border-border/70 px-3 py-2.5">
        <TargetPicker
          value={cliHint}
          onChange={onCliHintChange}
          connectedClis={connectedClis}
          disabled={isPending}
        />
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-[11px] text-muted-foreground sm:inline">
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
    </div>
  );
}

/**
 * Which CLI a run is aimed at.
 *
 * A dropdown of bare slugs ("gcloud", "gh", "wrangler") asks the reader to
 * translate binary names back into companies, so every entry carries its
 * vendor's mark. The first entry hands the choice back to Jarvis, which is the
 * default and the point of the hub.
 */
function TargetPicker({
  value,
  onChange,
  connectedClis,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  connectedClis: CliSummary[];
  disabled: boolean;
}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const selected = connectedClis.find((c) => c.name === value) ?? null;
  const anyLabel = `${assistantName} ${t("cli_test_hub_view.let_decide")}`;

  return (
    <ActionMenu
      align="start"
      label={t("cli_test_hub_view.cli_hint_aria")}
      actions={[
        {
          id: "__any",
          label: anyLabel,
          icon: value === "" ? <Check className="h-3.5 w-3.5" /> : <Sparkles className="h-3.5 w-3.5" />,
          onSelect: () => onChange(""),
        },
        ...connectedClis.map((cli, index) => ({
          id: cli.name,
          label: cli.display_name,
          separatorAbove: index === 0,
          icon: <CliLogo cliName={cli.name} category={cli.category} size="sm" className="h-4 w-4 rounded border-0 bg-transparent" />,
          onSelect: () => onChange(cli.name),
        })),
      ]}
      trigger={({ open, toggle }) => (
        <SoftButton
          onClick={toggle}
          disabled={disabled}
          ariaExpanded={open}
          ariaHasPopup="menu"
          ariaLabel={t("cli_test_hub_view.cli_hint_aria")}
        >
          {selected ? (
            <>
              <CliLogo
                cliName={selected.name}
                category={selected.category}
                size="sm"
                className="h-4 w-4 rounded border-0 bg-transparent"
              />
              {selected.display_name}
            </>
          ) : (
            <>
              <Sparkles className="h-3.5 w-3.5 text-muted-foreground" />
              {anyLabel}
            </>
          )}
          <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        </SoftButton>
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// Starters — what to type, for the people who have never used this page
// ---------------------------------------------------------------------------

/**
 * A ready-made instruction for a CLI, or null when none is written for it.
 *
 * The strings live in the locale files under `cli_test_hub_view.starters.<cli>`;
 * an unwritten one resolves to the key itself, which is the "no starter" signal.
 * Read-only commands on purpose — a suggestion the app puts in your mouth must
 * never be the one that deletes a cluster.
 */
function starterFor(cliName: string): string | null {
  const key = `cli_test_hub_view.starters.${cliName}`;
  const text = translate(key);
  return text === key ? null : text;
}

function Starters({
  connectedClis,
  onPick,
}: {
  connectedClis: CliSummary[];
  onPick: (cli: string, text: string) => void;
}) {
  const t = useT();
  const suggestions = useMemo(
    () =>
      connectedClis
        .map((cli) => ({ cli, text: starterFor(cli.name) }))
        .filter((s): s is { cli: CliSummary; text: string } => s.text !== null)
        .slice(0, 4),
    [connectedClis],
  );

  if (suggestions.length === 0) return null;

  return (
    <div>
      <div className="mb-2 text-xs text-muted-foreground">
        {t("cli_test_hub_view.try_one")}
      </div>
      <ul className="flex flex-wrap gap-2">
        {suggestions.map(({ cli, text }) => (
          <li key={cli.name}>
            <button
              type="button"
              onClick={() => onPick(cli.name, text)}
              className="inline-flex items-center gap-2 rounded-lg border border-border bg-card/40 px-3 py-1.5 text-left text-xs text-foreground/85 transition-colors hover:border-foreground/20 hover:bg-sheen/[0.06]"
            >
              <CliLogo
                cliName={cli.name}
                category={cli.category}
                size="sm"
                className="h-4 w-4 rounded border-0 bg-transparent"
              />
              {text}
            </button>
          </li>
        ))}
      </ul>
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

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t("cli_test_hub_view.loading_connected")}
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="clis-load-error"
        className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
      >
        {t("cli_test_hub_view.list_load_failed")}: {error.message}
      </div>
    );
  }

  if (clis.length === 0) {
    return (
      <div
        data-testid="clis-empty"
        className="rounded-xl border border-border bg-card/50 p-5"
      >
        <div className="flex flex-col items-start gap-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            {t("cli_test_hub_view.no_cli_connected")}
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">
            {assistantName} {t("cli_test_hub_view.no_cli_help")}
          </p>
          <SoftButton onClick={() => setActiveSection("clis")}>
            {t("cli_test_hub_view.go_to_clis")}
            <ArrowRight className="h-3.5 w-3.5" />
          </SoftButton>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 text-xs text-muted-foreground">
        {t("cli_test_hub_view.connected_clis")} · {clis.length}
      </div>
      <ul className="flex flex-wrap gap-1.5" data-testid="clis-chips">
        {clis.map((cli) => (
          <li
            key={cli.name}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card/40 py-1 pl-1.5 pr-2.5 text-xs"
            title={cli.description}
          >
            <CliLogo
              cliName={cli.name}
              category={cli.category}
              size="sm"
              className="h-5 w-5"
            />
            <span className="font-medium">{cli.name}</span>
            {cli.version && (
              <span className="font-mono text-[11px] text-muted-foreground">
                {cli.version}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton (these runs can take several seconds)
// ---------------------------------------------------------------------------

function ResultSkeleton() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div
      data-testid="result-skeleton"
      aria-busy="true"
      className="space-y-3 rounded-xl border border-border bg-card/50 p-5"
    >
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin text-primary" />
        {assistantName} {t("cli_test_hub_view.picking_tool")}
      </div>
      <div className="h-3 w-2/3 animate-jarvis-pulse rounded bg-muted-foreground/15" />
      <div className="h-3 w-1/2 animate-jarvis-pulse rounded bg-muted-foreground/15" />
      <div className="h-20 w-full animate-jarvis-pulse rounded bg-muted-foreground/10" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Run log
// ---------------------------------------------------------------------------

function RunLog({
  runs,
  expanded,
  onToggle,
  onClear,
}: {
  runs: RunRecord[];
  expanded: number | null;
  onToggle: (id: number) => void;
  onClear: () => void;
}) {
  const t = useT();
  return (
    <section>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="text-xs text-muted-foreground">
          {t("cli_test_hub_view.runs")} · {runs.length}
        </div>
        {runs.length > 1 && (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <Trash2 className="h-3.5 w-3.5" />
            {t("cli_test_hub_view.clear_runs")}
          </button>
        )}
      </div>
      <div className="space-y-2">
        {runs.map((run) => (
          <RunCard
            key={run.id}
            run={run}
            open={expanded === run.id}
            onToggle={() => onToggle(run.id)}
          />
        ))}
      </div>
    </section>
  );
}

function RunCard({
  run,
  open,
  onToggle,
}: {
  run: RunRecord;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useT();
  const cli = cliOfRun(run);
  const failed = Boolean(run.error) || run.result?.ok === false || Boolean(run.result?.error);

  return (
    <Panel
      className={cn(
        "overflow-hidden",
        failed ? "border-destructive/35" : open && "border-foreground/15",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-sheen/[0.04]"
      >
        {cli ? (
          <CliLogo cliName={cli} category="other" size="sm" />
        ) : (
          <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border/70 bg-background/60">
            <FlaskConical className="h-4 w-4 text-muted-foreground" />
          </span>
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{run.instruction}</span>
          <span className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
            {run.result?.command ?? run.error?.message ?? t("cli_test_hub_view.no_command")}
          </span>
        </span>
        {run.result && <ExitCodeBadge code={run.result.exit_code} compact={!open} />}
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
      </button>

      {open && (
        <div className="border-t border-border/70 px-4 py-4">
          {run.error ? (
            <RequestErrorPanel error={run.error} />
          ) : run.result ? (
            <ResultPanel result={run.result} />
          ) : null}
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Request-level error (network / 5xx — the mutation itself rejected)
// ---------------------------------------------------------------------------

function RequestErrorPanel({ error }: { error: Error }) {
  const t = useT();
  return (
    <div
      data-testid="request-error"
      className="rounded-lg border border-destructive/40 bg-destructive/10 p-4"
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result panel — the evidence trail for one run
// ---------------------------------------------------------------------------

function ResultPanel({ result }: { result: TestRunResponse }) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const failed = result.ok === false || Boolean(result.error);
  const hasSteps = result.steps && result.steps.length > 1;

  return (
    <div data-testid="result-panel" className="space-y-4">
      {/* Summary — the prominent, human-readable headline. */}
      <div>
        <div className="mb-1 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Sparkles className="h-3 w-3" />
          {assistantName} {t("cli_test_hub_view.says")}
        </div>
        <p
          data-testid="result-summary"
          className="text-[15px] leading-relaxed text-foreground"
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
        <ExitCodeBadge code={result.exit_code} testId="result-exit-code" />
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
        <CodeBlock
          title={t("cli_test_hub_view.command_label")}
          content={result.command}
          testId="result-command"
        />
      )}

      {/* Inline error (the run produced a structured error string). */}
      {result.error && (
        <div
          data-testid="result-error"
          className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
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
          <div className="mb-1.5 text-xs text-muted-foreground">
            {t("cli_test_hub_view.steps")} ({result.steps.length})
          </div>
          <ol data-testid="result-steps" className="space-y-1.5">
            {result.steps.map((step, idx) => (
              <li
                key={`${step.tool}-${idx}`}
                className="flex items-start gap-2.5 rounded-lg border border-border bg-background/40 px-3 py-2"
              >
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-sheen/[0.08] text-[11px] font-medium tabular-nums text-muted-foreground">
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
        <div className="rounded-lg border border-border bg-background/40 px-3 py-2 text-xs text-muted-foreground">
          {assistantName} {t("cli_test_hub_view.no_tool_found")}
        </div>
      )}
    </div>
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
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background/40 px-2 py-0.5 text-[11px]">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground">{children}</span>
    </span>
  );
}

/**
 * The exit code of a run.
 *
 * Appears twice per run — once folded into the log row, once in the evidence
 * block — so the test id travels with the evidence copy rather than being
 * stamped on both: a `data-testid` names one element, not a shape.
 */
function ExitCodeBadge({
  code,
  compact,
  testId,
}: {
  code: number | null;
  compact?: boolean;
  testId?: string;
}) {
  if (code === null || code === undefined) {
    return (
      <span
        data-testid={testId}
        data-exit="null"
        className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-background/40 px-2 py-0.5 text-[11px] text-muted-foreground"
      >
        {!compact && <span className="text-muted-foreground">Exit</span>}
        <span>—</span>
      </span>
    );
  }
  const ok = code === 0;
  return (
    <span
      data-testid={testId}
      data-exit={String(code)}
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium tabular-nums",
        ok
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-500"
          : "border-destructive/50 bg-destructive/10 text-destructive",
      )}
    >
      {!compact && <span className="font-normal text-muted-foreground">Exit</span>}
      {code}
    </span>
  );
}

function StepExitCode({ code }: { code: number | null }) {
  if (code === null || code === undefined) {
    return <span className="text-muted-foreground/70">exit —</span>;
  }
  return (
    <span
      className={cn(
        "tabular-nums",
        code === 0 ? "text-emerald-500" : "text-destructive",
      )}
    >
      exit {code}
    </span>
  );
}

/** A one-line command with a copy button — the thing people want to re-run. */
function CodeBlock({
  title,
  content,
  testId,
}: {
  title: string;
  content: string;
  testId: string;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{title}</span>
        <button
          type="button"
          aria-label={t("cli_test_hub_view.copy")}
          onClick={() => {
            void robustCopy(content).then((ok) => {
              if (!ok) return;
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            });
          }}
          className="inline-flex items-center gap-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? t("cli_test_hub_view.copied") : t("cli_test_hub_view.copy")}
        </button>
      </div>
      <pre
        data-testid={testId}
        className="overflow-x-auto rounded-lg border border-border bg-background px-3 py-2 font-mono text-xs text-foreground scrollbar-jarvis"
      >
        <code>{content}</code>
      </pre>
    </div>
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
      <div className="mb-1 text-xs text-muted-foreground">{title}</div>
      <ScrollArea className="max-h-60 rounded-lg border border-border bg-background">
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
