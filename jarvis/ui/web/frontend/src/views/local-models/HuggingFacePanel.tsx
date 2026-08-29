/**
 * Hugging Face — browse public GGUF repositories and pull one quantization.
 *
 * Off by default: the first thing on the panel is the switch that writes
 * `[brain.providers.ollama].hf_enabled`. While it is off the backend answers
 * every browse route with a 404 sentence, and the panel makes no outbound
 * request at all — an install that wants no Hugging Face traffic gets none.
 *
 * When on: a search field with three sort chips, a hairline ledger of
 * repositories (id, author, architecture, parameters, context, downloads,
 * updated) and, per expanded row, the `.gguf` files with quantization, size,
 * fit verdict and a Pull button. A pull posts `hf/pull`, then polls the
 * existing pull-status route until it finishes; progress shows inline on the
 * file row. The optional token is NOT collected here — the note points to the
 * API Keys screen — and private repositories get the SSH-key hint sentence.
 *
 * Props: `providerId` — the id of the pull-capable provider card (the panel is
 * gated on the capability by its caller, never on a provider name).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ExternalLink, KeyRound } from "lucide-react";

import {
  EmptyRow,
  InlineSearch,
  SegmentedFilter,
  SoftButton,
  StatusDot,
  formatShortDate,
} from "@/components/extensions/primitives";
import { Switch } from "@/components/ui/switch";
import {
  hfPullName,
  modelPullStatus,
  useHfEnabled,
  useHfFiles,
  useHfSearch,
  useInvalidateLocalModels,
  useSetHfEnabled,
  useStartHfPull,
  type HfFile,
  type HfRepo,
  type HfSort,
  type ModelPullProgress,
} from "@/hooks/useLocalModels";
import { useT, useUiLanguage } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

const HF_KEYS_URL = "https://huggingface.co/settings/keys";
const HF_REPO_URL = "https://huggingface.co/";
const SEARCH_DEBOUNCE_MS = 350;
const PULL_POLL_MS = 2500;

const EYEBROW = "text-[11px] uppercase tracking-[0.18em] text-muted-foreground";

/** "8.03B" / "27B" / "540M" — the way Hugging Face itself abbreviates. */
export function formatParams(total: number | null): string {
  if (total === null || !Number.isFinite(total) || total <= 0) return "—";
  if (total >= 1e12) return `${(total / 1e12).toFixed(1).replace(/\.0$/, "")}T`;
  if (total >= 1e9) return `${(total / 1e9).toFixed(1).replace(/\.0$/, "")}B`;
  if (total >= 1e6) return `${Math.round(total / 1e6)}M`;
  return String(total);
}

/** "128K" / "32K" / "4096" for the context window. */
export function formatContext(ctx: number | null): string {
  if (ctx === null || !Number.isFinite(ctx) || ctx <= 0) return "—";
  return ctx >= 1024 ? `${Math.round(ctx / 1024)}K` : String(ctx);
}

function formatCount(n: number, locale: string): string {
  try {
    return new Intl.NumberFormat(locale, {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(n);
  } catch {
    return String(n);
  }
}

function formatSize(gb: number | null, t: (k: string) => string): string {
  if (gb === null || !Number.isFinite(gb))
    return t("local_models.huggingface.size_unknown");
  return `${gb.toFixed(2)} GB`;
}

/** Split "user/repo" once; anything else is not a repository id. */
function splitRepoId(id: string): { user: string; repo: string } | null {
  const idx = id.indexOf("/");
  if (idx <= 0 || idx === id.length - 1) return null;
  return { user: id.slice(0, idx), repo: id.slice(idx + 1) };
}

type PullState = { progress: ModelPullProgress | null; error: string | null };

export function HuggingFacePanel({ providerId }: { providerId: string }) {
  const t = useT();
  const locale = useUiLanguage();
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const enabledQuery = useHfEnabled(providerId);
  const setEnabled = useSetHfEnabled(providerId);
  const enabled = enabledQuery.data?.enabled === true;

  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState<HfSort>("downloads");
  const [openRepo, setOpenRepo] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebounced(query.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [query]);

  const search = useHfSearch(providerId, debounced, sort, enabled);

  // One pull at a time per panel, keyed by the exact pull name so the progress
  // lands on the file row that started it.
  const [pulls, setPulls] = useState<Record<string, PullState>>({});
  const startPull = useStartHfPull(providerId);
  const invalidate = useInvalidateLocalModels(providerId);
  const pollTimers = useRef<Record<string, number>>({});

  useEffect(() => {
    const timers = pollTimers.current;
    return () => {
      Object.values(timers).forEach((id) => window.clearInterval(id));
    };
  }, []);

  const poll = useCallback(
    (name: string) => {
      if (pollTimers.current[name]) return;
      const timer = window.setInterval(async () => {
        try {
          const next = await modelPullStatus(providerId, name);
          setPulls((prev) => ({
            ...prev,
            [name]: { progress: next, error: null },
          }));
          if (next.state === "done" || next.state === "error") {
            window.clearInterval(timer);
            delete pollTimers.current[name];
            if (next.state === "done") invalidate();
          }
        } catch (err) {
          window.clearInterval(timer);
          delete pollTimers.current[name];
          setPulls((prev) => ({
            ...prev,
            [name]: {
              progress: null,
              error: err instanceof Error ? err.message : String(err),
            },
          }));
        }
      }, PULL_POLL_MS);
      pollTimers.current[name] = timer;
    },
    [providerId, invalidate],
  );

  const pull = useCallback(
    async (user: string, repo: string, quant: string | null) => {
      const name = hfPullName(user, repo, quant);
      setPulls((prev) => ({
        ...prev,
        [name]: { progress: null, error: null },
      }));
      try {
        const started = await startPull.mutateAsync({ user, repo, quant });
        setPulls((prev) => ({
          ...prev,
          [name]: { progress: started, error: null },
        }));
        if (started.state === "done") invalidate();
        else if (started.state === "running") poll(name);
      } catch (err) {
        setPulls((prev) => ({
          ...prev,
          [name]: {
            progress: null,
            error: err instanceof Error ? err.message : String(err),
          },
        }));
      }
    },
    [startPull, poll, invalidate],
  );

  const sortOptions = useMemo(
    () => [
      {
        id: "downloads" as const,
        label: t("local_models.huggingface.sort_downloads"),
      },
      {
        id: "lastModified" as const,
        label: t("local_models.huggingface.sort_newest"),
      },
      {
        id: "trendingScore" as const,
        label: t("local_models.huggingface.sort_trending"),
      },
    ],
    [t],
  );

  const toggleError =
    setEnabled.error instanceof Error ? setEnabled.error.message : null;
  const readError =
    enabledQuery.error instanceof Error ? enabledQuery.error.message : null;

  // The backend's own sentence (429, offline, 404 while off) beats a generic one.
  const searchError =
    search.data?.error ??
    (search.error instanceof Error ? search.error.message : null);
  const repos = search.data?.repos ?? [];

  return (
    <div className="space-y-4" data-testid="huggingface-panel">
      {/* Enable row */}
      <div className="flex items-start justify-between gap-4 rounded-xl border border-border bg-card/60 px-4 py-3">
        <div className="min-w-0">
          <div className={EYEBROW}>
            {t("local_models.huggingface.toggle_label")}
          </div>
          <p className="mt-1 text-sm text-foreground">
            {t("local_models.huggingface.toggle_sentence")}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("local_models.huggingface.toggle_hint")}
          </p>
          {(toggleError || readError) && (
            <p className="mt-1 text-xs text-destructive" role="alert">
              {toggleError ?? readError}
            </p>
          )}
        </div>
        <Switch
          checked={enabled}
          disabled={enabledQuery.isLoading || setEnabled.isPending}
          aria-label={t("local_models.huggingface.toggle_label")}
          data-testid="hf-enabled-switch"
          onCheckedChange={(next) => setEnabled.mutate(next)}
        />
      </div>

      {enabled && (
        <>
          {/* Search + sort */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-[220px] flex-1">
              <InlineSearch
                value={query}
                onChange={setQuery}
                placeholder={t("local_models.huggingface.search_placeholder")}
              />
            </div>
            <SegmentedFilter<HfSort>
              label={t("local_models.huggingface.sort_label")}
              value={sort}
              onChange={setSort}
              options={sortOptions}
            />
          </div>

          {/* Results */}
          {debounced.length === 0 ? (
            <EmptyRow>{t("local_models.huggingface.empty_query")}</EmptyRow>
          ) : searchError ? (
            <EmptyRow>
              <span role="alert">{searchError}</span>
            </EmptyRow>
          ) : search.isLoading ? (
            <EmptyRow>{t("local_models.huggingface.searching")}</EmptyRow>
          ) : repos.length === 0 ? (
            <EmptyRow>{t("local_models.huggingface.no_results")}</EmptyRow>
          ) : (
            <div
              className="divide-y divide-border/70 overflow-hidden rounded-xl border border-border bg-card/60"
              data-testid="hf-repo-list"
            >
              {repos.map((repo) => (
                <RepoRow
                  key={repo.id}
                  repo={repo}
                  open={openRepo === repo.id}
                  onToggle={() =>
                    setOpenRepo((cur) => (cur === repo.id ? null : repo.id))
                  }
                  providerId={providerId}
                  pulls={pulls}
                  onPull={pull}
                  locale={locale}
                  t={t}
                />
              ))}
            </div>
          )}

          {/* Token note + private-repo hint */}
          <div className="space-y-2 rounded-xl border border-border bg-card/60 px-4 py-3">
            <div className={EYEBROW}>
              {t("local_models.huggingface.notes_label")}
            </div>
            <p className="text-xs text-muted-foreground">
              {t("local_models.huggingface.token_note")}{" "}
              <button
                type="button"
                onClick={() => setActiveSection("apikeys")}
                className="inline-flex items-center gap-1 text-foreground/90 underline-offset-2 hover:underline"
              >
                <KeyRound className="h-3 w-3" />
                {t("local_models.huggingface.token_link")}
              </button>
            </p>
            <p className="text-xs text-muted-foreground">
              {t("local_models.huggingface.private_hint")}{" "}
              <button
                type="button"
                onClick={() => void openExternalUrl(HF_KEYS_URL)}
                className="inline-flex items-center gap-1 text-foreground/90 underline-offset-2 hover:underline"
              >
                <ExternalLink className="h-3 w-3" />
                hf.co/settings/keys
              </button>
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function RepoRow({
  repo,
  open,
  onToggle,
  providerId,
  pulls,
  onPull,
  locale,
  t,
}: {
  repo: HfRepo;
  open: boolean;
  onToggle: () => void;
  providerId: string;
  pulls: Record<string, PullState>;
  onPull: (user: string, repo: string, quant: string | null) => void;
  locale: string;
  t: (k: string) => string;
}) {
  const parts = splitRepoId(repo.id);
  return (
    <div data-testid={`hf-repo-${repo.id}`}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3.5 py-3 text-left transition-colors hover:bg-sheen/[0.05]"
      >
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-foreground">
            {repo.id}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground tabular-nums">
            <span>{repo.author || "—"}</span>
            <span>
              {repo.architecture || t("local_models.huggingface.arch_unknown")}
            </span>
            <span>
              {t("local_models.huggingface.col_params")}{" "}
              {formatParams(repo.total_params)}
            </span>
            <span>
              {t("local_models.huggingface.col_context")}{" "}
              {formatContext(repo.context_length)}
            </span>
          </div>
        </div>
        <div className="hidden shrink-0 text-right text-xs text-muted-foreground tabular-nums sm:block">
          <div>
            {formatCount(repo.downloads, locale)}{" "}
            {t("local_models.huggingface.col_downloads")}
          </div>
          <div>
            {t("local_models.huggingface.col_updated")}{" "}
            {formatShortDate(repo.last_modified || null, locale)}
          </div>
        </div>
      </button>
      {open && parts && (
        <FileList
          providerId={providerId}
          user={parts.user}
          repo={parts.repo}
          pulls={pulls}
          onPull={onPull}
          t={t}
        />
      )}
    </div>
  );
}

function FileList({
  providerId,
  user,
  repo,
  pulls,
  onPull,
  t,
}: {
  providerId: string;
  user: string;
  repo: string;
  pulls: Record<string, PullState>;
  onPull: (user: string, repo: string, quant: string | null) => void;
  t: (k: string) => string;
}) {
  const files = useHfFiles(providerId, user, repo);
  const error =
    files.data?.error ??
    (files.error instanceof Error ? files.error.message : null);
  const rows = files.data?.files ?? [];

  return (
    <div
      className="border-t border-border/70 bg-background/40 px-3.5 py-2"
      data-testid="hf-file-list"
    >
      {files.isLoading ? (
        <p className="py-2 text-xs text-muted-foreground">
          {t("local_models.huggingface.loading_files")}
        </p>
      ) : error ? (
        <p className="py-2 text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : rows.length === 0 ? (
        <p className="py-2 text-xs text-muted-foreground">
          {t("local_models.huggingface.no_files")}
        </p>
      ) : (
        <div className="divide-y divide-border/50">
          {rows.map((file) => (
            <FileRow
              key={file.filename}
              file={file}
              state={pulls[hfPullName(user, repo, file.quant)]}
              onPull={() => onPull(user, repo, file.quant)}
              t={t}
            />
          ))}
        </div>
      )}
      <p className="pt-2 text-[11px] text-muted-foreground">
        <button
          type="button"
          onClick={() => void openExternalUrl(`${HF_REPO_URL}${user}/${repo}`)}
          className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
        >
          <ExternalLink className="h-3 w-3" />
          {t("local_models.huggingface.open_repo")}
        </button>
      </p>
    </div>
  );
}

function FileRow({
  file,
  state,
  onPull,
  t,
}: {
  file: HfFile;
  state: PullState | undefined;
  onPull: () => void;
  t: (k: string) => string;
}) {
  const progress = state?.progress ?? null;
  const running =
    progress?.state === "running" ||
    (state !== undefined && progress === null && !state.error);
  const done = progress?.state === "done";
  const failed = progress?.state === "error" || Boolean(state?.error);
  const percent =
    typeof progress?.percent === "number"
      ? Math.max(0, Math.min(100, Math.round(progress.percent)))
      : null;

  return (
    <div
      className="flex items-center gap-3 py-2"
      data-testid={`hf-file-${file.filename}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-3 text-sm">
          <span className="font-medium tabular-nums text-foreground">
            {file.quant ?? t("local_models.huggingface.quant_default")}
          </span>
          <span className="text-xs text-muted-foreground tabular-nums">
            {formatSize(file.size_gb, t)}
          </span>
          <span
            className={cn(
              "text-xs",
              file.fit === "tight"
                ? "text-foreground"
                : file.fit === "comfortable"
                  ? "text-muted-foreground"
                  : "text-muted-foreground",
            )}
          >
            {t(`local_models.huggingface.fit_${file.fit}`)}
          </span>
        </div>
        <div className="truncate text-[11px] text-muted-foreground">
          {file.filename}
        </div>
        {file.fit === "tight" && file.fit_note && (
          <div className="text-[11px] text-foreground">
            {file.fit_note}
          </div>
        )}
        {running && (
          <div className="mt-1 flex items-center gap-2">
            <div className="h-1 w-40 overflow-hidden rounded-full bg-muted-foreground/20">
              <div
                className="h-full bg-foreground/70 transition-[width]"
                style={{ width: `${percent ?? 5}%` }}
              />
            </div>
            <span className="text-[11px] text-muted-foreground tabular-nums">
              {percent !== null
                ? `${percent}%`
                : t("local_models.huggingface.pull_starting")}
              {progress?.message ? ` · ${progress.message}` : ""}
            </span>
          </div>
        )}
        {done && (
          <div className="mt-1">
            <StatusDot
              tone="ok"
              label={
                progress?.message || t("local_models.huggingface.pull_done")
              }
            />
          </div>
        )}
        {failed && (
          <div className="mt-1 text-[11px] text-destructive" role="alert">
            {state?.error ??
              progress?.message ??
              t("local_models.huggingface.pull_failed")}
          </div>
        )}
      </div>
      <SoftButton
        onClick={onPull}
        disabled={running}
        primary={!done}
        ariaLabel={`${t("local_models.huggingface.pull")} ${file.quant ?? file.filename}`}
      >
        {done
          ? t("local_models.huggingface.pull_again")
          : running
            ? t("local_models.huggingface.pulling")
            : t("local_models.huggingface.pull")}
      </SoftButton>
    </div>
  );
}
