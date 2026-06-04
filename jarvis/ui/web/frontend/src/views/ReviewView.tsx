// ReviewView (Phase 8.5) — read-only Audit-UI für die Review-Pipeline.
//
// Plan-Referenz: §6.5. UI ist read-only — KEINE Mutation der Review-Daten.
// Drei Tabs: Recent Runs, Run Detail, Stats. Click-Through von Recent → Detail.
import { useEffect, useMemo, useState } from "react";
import { useT } from "@/i18n";

interface RunSummary {
  run_id: string;
  ts: string;
  iterations: number;
  final_status: string;
  cap_fired: boolean;
  total_latency_ms: number;
  total_tokens_in: number;
  total_tokens_out: number;
}

interface IterationDetail {
  iteration: number;
  worker_output_excerpt: string;
  worker_output_truncated: boolean;
  verdict: {
    status: string;
    summary: string;
    score: number;
    issues: Array<{
      severity: string;
      description: string;
      location: string | null;
      fix_hint: string | null;
    }>;
    rubric_results?: Array<{ name: string; passed: boolean; note: string | null }>;
  } | null;
  latency_ms: number;
}

interface RunDetail {
  run_id: string;
  ts: string;
  task: string;
  rubric_id: string;
  final_status: string;
  cap_fired: boolean;
  iterations_total: number;
  iterations_detail: IterationDetail[];
  final_artifact_path: string | null;
}

interface StatsResponse {
  window_days: number;
  runs_total: number;
  pass_rate: number;
  cap_fire_rate: number;
  median_iterations: number;
  median_latency_ms: number;
  median_tokens_per_run: number;
  pass_rate_by_rubric: Record<string, number>;
}

type TabId = "recent" | "detail" | "stats";

const STATUS_BADGE_STYLE: Record<string, string> = {
  pass: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  fail: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  cap_fired: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  precheck_fail: "bg-zinc-500/15 text-zinc-300 border-zinc-500/30",
  incomplete: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

function StatusBadge({ status, capFired }: { status: string; capFired?: boolean }) {
  const cls = STATUS_BADGE_STYLE[status] ?? STATUS_BADGE_STYLE.incomplete;
  const label = capFired ? "cap-fired" : status;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}
    >
      {label}
    </span>
  );
}

function formatTs(ts: string): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

function formatPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

// ----------------------------------------------------------------------
// Tab: Recent Runs
// ----------------------------------------------------------------------

function RecentRunsTab({
  runs,
  loading,
  error,
  onSelect,
}: {
  runs: RunSummary[];
  loading: boolean;
  error: string | null;
  onSelect: (runId: string) => void;
}) {
  if (loading) {
    return <p className="p-4 text-sm text-muted-foreground">Lade Runs …</p>;
  }
  if (error) {
    return <p className="p-4 text-sm text-rose-400">Fehler: {error}</p>;
  }
  if (runs.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">Noch keine Runs.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead className="border-b border-border/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left">Zeit</th>
            <th className="px-3 py-2 text-left">Run-ID</th>
            <th className="px-3 py-2 text-left">Status</th>
            <th className="px-3 py-2 text-right">Iter</th>
            <th className="px-3 py-2 text-right">Latenz</th>
            <th className="px-3 py-2 text-right">Tokens</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.run_id}
              className="border-b border-border/20 hover:bg-accent/30 cursor-pointer"
              onClick={() => onSelect(r.run_id)}
            >
              <td className="px-3 py-2 text-muted-foreground">{formatTs(r.ts)}</td>
              <td className="px-3 py-2 font-mono text-xs">{r.run_id.slice(0, 12)}…</td>
              <td className="px-3 py-2">
                <StatusBadge status={r.final_status} capFired={r.cap_fired} />
              </td>
              <td className="px-3 py-2 text-right">{r.iterations}</td>
              <td className="px-3 py-2 text-right">
                {(r.total_latency_ms / 1000).toFixed(1)}s
              </td>
              <td className="px-3 py-2 text-right">
                {r.total_tokens_in + r.total_tokens_out}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ----------------------------------------------------------------------
// Tab: Run Detail
// ----------------------------------------------------------------------

function RunDetailTab({
  selectedId,
  detail,
  loading,
  error,
  onBack,
}: {
  selectedId: string | null;
  detail: RunDetail | null;
  loading: boolean;
  error: string | null;
  onBack: () => void;
}) {
  if (!selectedId) {
    return (
      <p className="p-4 text-sm text-muted-foreground">
        Klicke einen Run im „Recent Runs"-Tab an, um Details zu sehen.
      </p>
    );
  }
  if (loading) {
    return <p className="p-4 text-sm text-muted-foreground">Lade Detail …</p>;
  }
  if (error) {
    return <p className="p-4 text-sm text-rose-400">Fehler: {error}</p>;
  }
  if (!detail) {
    return <p className="p-4 text-sm text-muted-foreground">Run nicht gefunden.</p>;
  }
  return (
    <div className="space-y-4 p-4">
      <button
        type="button"
        onClick={onBack}
        className="rounded-md border border-border bg-background px-2 py-1 text-xs hover:bg-accent"
      >
        ← Zurück
      </button>

      <div className="rounded-md border border-border bg-background/40 p-3">
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="font-mono text-sm">{detail.run_id}</span>
          <StatusBadge status={detail.final_status} capFired={detail.cap_fired} />
          <span className="text-xs text-muted-foreground">{formatTs(detail.ts)}</span>
          <span className="text-xs text-muted-foreground">
            Rubric: <code>{detail.rubric_id}</code>
          </span>
        </div>
        <p className="mt-2 whitespace-pre-wrap text-sm">{detail.task}</p>
      </div>

      {detail.final_artifact_path && (
        <div className="rounded-md border border-border/40 bg-background/20 p-3 text-xs text-muted-foreground">
          Endresultat: <code>{detail.final_artifact_path}</code>
        </div>
      )}

      {detail.iterations_detail.map((iter) => (
        <div
          key={iter.iteration}
          className="rounded-md border border-border bg-background/40 p-3"
        >
          <div className="flex items-baseline gap-3">
            <span className="text-sm font-semibold">Iteration {iter.iteration}</span>
            {iter.verdict ? (
              <StatusBadge status={iter.verdict.status} />
            ) : (
              <span className="text-xs text-muted-foreground">(no verdict)</span>
            )}
            {iter.verdict?.score !== undefined && (
              <span className="text-xs text-muted-foreground">
                score {iter.verdict.score.toFixed(2)}
              </span>
            )}
            <span className="ml-auto text-xs text-muted-foreground">
              {iter.latency_ms} ms
            </span>
          </div>
          {iter.verdict?.summary && (
            <p className="mt-2 text-sm">{iter.verdict.summary}</p>
          )}
          {iter.verdict?.issues && iter.verdict.issues.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs">
              {iter.verdict.issues.map((issue, idx) => (
                <li key={idx} className="rounded bg-background/40 p-2">
                  <span className="font-medium">[{issue.severity}]</span>{" "}
                  {issue.location ? <code>{issue.location}</code> : null}{" "}
                  {issue.description}
                  {issue.fix_hint && (
                    <div className="mt-1 text-muted-foreground">
                      Fix: {issue.fix_hint}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
          {iter.worker_output_excerpt && (
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                Worker-Output (Auszug
                {iter.worker_output_truncated ? ", gekürzt" : ""})
              </summary>
              <pre className="mt-2 max-h-48 overflow-auto rounded bg-background/60 p-2 text-[11px] text-foreground/80 whitespace-pre-wrap break-all">
                {iter.worker_output_excerpt}
              </pre>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

// ----------------------------------------------------------------------
// Tab: Stats
// ----------------------------------------------------------------------

function StatsTab({
  stats,
  loading,
  error,
  windowDays,
  setWindowDays,
}: {
  stats: StatsResponse | null;
  loading: boolean;
  error: string | null;
  windowDays: number;
  setWindowDays: (n: number) => void;
}) {
  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">Fenster:</span>
        {[1, 7, 30].map((n) => (
          <button
            key={n}
            type="button"
            onClick={() => setWindowDays(n)}
            className={`rounded-md border px-2 py-1 text-xs ${
              windowDays === n
                ? "border-primary bg-primary/15 text-primary"
                : "border-border bg-background hover:bg-accent"
            }`}
          >
            {n}d
          </button>
        ))}
      </div>
      {loading && <p className="text-sm text-muted-foreground">Lade Stats …</p>}
      {error && <p className="text-sm text-rose-400">Fehler: {error}</p>}
      {stats && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <Stat label="Runs gesamt" value={String(stats.runs_total)} />
            <Stat label="Pass-Rate" value={formatPct(stats.pass_rate)} />
            <Stat label="Cap-Fire-Rate" value={formatPct(stats.cap_fire_rate)} />
            <Stat
              label="Median Iter"
              value={stats.median_iterations.toFixed(1)}
            />
            <Stat
              label="Median Latenz"
              value={`${(stats.median_latency_ms / 1000).toFixed(1)}s`}
            />
            <Stat
              label="Median Tokens"
              value={Math.round(stats.median_tokens_per_run).toString()}
            />
          </div>
          {Object.keys(stats.pass_rate_by_rubric).length > 0 && (
            <div>
              <div className="mb-2 text-sm font-semibold">Pass-Rate pro Rubric</div>
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(stats.pass_rate_by_rubric).map(([rubric, rate]) => (
                    <tr key={rubric} className="border-b border-border/20">
                      <td className="py-1 font-mono text-xs">{rubric}</td>
                      <td className="py-1 text-right">{formatPct(rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-background/40 p-3">
      <div className="text-xs uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Main
// ----------------------------------------------------------------------

export function ReviewView() {
  const t = useT();
  const [tab, setTab] = useState<TabId>("recent");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState<boolean>(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState<boolean>(false);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [windowDays, setWindowDays] = useState<number>(7);

  // Load runs once on mount + when re-entering "recent"-Tab.
  useEffect(() => {
    if (tab !== "recent") return;
    setRunsLoading(true);
    setRunsError(null);
    fetch("/api/review/runs?limit=50")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: RunSummary[]) => setRuns(data))
      .catch((e: Error) => setRunsError(e.message))
      .finally(() => setRunsLoading(false));
  }, [tab]);

  // Load detail when selectedId changes.
  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    fetch(`/api/review/runs/${encodeURIComponent(selectedId)}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: RunDetail) => setDetail(data))
      .catch((e: Error) => setDetailError(e.message))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  // Load stats when on stats-tab + on windowDays-change.
  useEffect(() => {
    if (tab !== "stats") return;
    setStatsLoading(true);
    setStatsError(null);
    fetch(`/api/review/stats?window_days=${windowDays}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: StatsResponse) => setStats(data))
      .catch((e: Error) => setStatsError(e.message))
      .finally(() => setStatsLoading(false));
  }, [tab, windowDays]);

  const tabs = useMemo(
    () =>
      [
        { id: "recent" as const, label: "Recent Runs" },
        { id: "detail" as const, label: "Run Detail" },
        { id: "stats" as const, label: "Stats" },
      ],
    [],
  );

  return (
    <div className="flex h-full w-full flex-col">
      <header className="border-b border-border/40 px-4 py-3">
        <h1 className="text-lg font-semibold">{t("review_view.title")}</h1>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("review_view.subtitle")}
        </p>
      </header>

      <nav className="flex gap-1 border-b border-border/40 px-4 py-2">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setTab(t.id);
              if (t.id === "detail" && !selectedId && runs[0]) {
                setSelectedId(runs[0].run_id);
              }
            }}
            className={`rounded-md px-3 py-1.5 text-sm ${
              tab === t.id
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/40"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <section className="flex-1 overflow-auto">
        {tab === "recent" && (
          <RecentRunsTab
            runs={runs}
            loading={runsLoading}
            error={runsError}
            onSelect={(id) => {
              setSelectedId(id);
              setTab("detail");
            }}
          />
        )}
        {tab === "detail" && (
          <RunDetailTab
            selectedId={selectedId}
            detail={detail}
            loading={detailLoading}
            error={detailError}
            onBack={() => {
              setSelectedId(null);
              setTab("recent");
            }}
          />
        )}
        {tab === "stats" && (
          <StatsTab
            stats={stats}
            loading={statsLoading}
            error={statsError}
            windowDays={windowDays}
            setWindowDays={setWindowDays}
          />
        )}
      </section>
    </div>
  );
}
