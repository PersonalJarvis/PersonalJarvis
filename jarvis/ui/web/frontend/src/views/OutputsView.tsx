import { useMemo, useState, type DragEvent } from "react";
import {
  FolderOpen,
  ExternalLink,
  Github,
  Loader2,
  ListChecks,
  FileQuestion,
  FileText,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { PlanStepList } from "@/components/PlanStepList";
import { HoldToAbortButton } from "@/components/HoldToAbortButton";
import { RerunButton } from "@/components/RerunButton";
import {
  useOutputsList,
  usePlanForOutput,
  useArtifactsForOutput,
  useArtifactFile,
  useCancelMission,
  useOutputsCapabilities,
  useOpeners,
  usePreferredOpener,
  useSetPreferredOpener,
  artifactOpenUrl,
  revealArtifact,
  openArtifactWith,
  type OutputSummary,
  type ArtifactSummary,
} from "@/hooks/useOutputs";
import { OpenWithDialog } from "@/components/OpenWithDialog";
import { useT } from "@/i18n";
import { applyMissionDragImage } from "@/lib/missionDragImage";
import { useMissionDrag } from "@/store/missionDrag";

const STATUS_BADGE: Record<string, string> = {
  success: "border-emerald-400/40 bg-emerald-400/10 text-emerald-400",
  error: "border-destructive/40 bg-destructive/10 text-destructive",
  running: "border-primary/40 bg-primary/10 text-primary",
  // Deliberate user abort — amber, not the destructive red of a failure.
  cancelled: "border-amber-400/40 bg-amber-400/10 text-amber-400",
  unknown: "border-border bg-secondary/40 text-muted-foreground",
};

/** Tiny ping dot inside the RUNNING badge — inherits the badge colour. */
function PulseDot() {
  return (
    <span className="relative flex h-1.5 w-1.5" aria-hidden="true">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
    </span>
  );
}

const URL_REGEX = /(https?:\/\/[^\s)]+[^\s.,;:!?)])/g;

/** MIME type carrying a mission reference between a card and the Jarvis dock.
 *  Must match `MISSION_DND_MIME` in `components/JarvisDock.tsx`. */
export const MISSION_DND_MIME = "application/x-jarvis-mission";

/** The subset of an Outputs card the drag carries to the dock/server. */
export type OutputsDragMeta = Pick<
  OutputSummary,
  "slug" | "utterance" | "status" | "summary" | "error" | "mission_id"
>;

/** Serialise the fields the dock/server need from a dragged Outputs card. */
export function buildMissionDragPayload(meta: OutputsDragMeta): string {
  return JSON.stringify({
    slug: meta.slug,
    utterance: meta.utterance ?? "",
    status: meta.status ?? "unknown",
    summary: meta.summary ?? "",
    error: meta.error ?? "",
    mission_id: meta.mission_id ?? null,
  });
}

/**
 * Begin dragging an Outputs card toward the Jarvis dock. Writes the payload,
 * swaps the giant native drag ghost for a compact branded chip, and flags the
 * drag globally so the dock blooms into a big, forgiving target.
 */
export function startMissionDrag(e: DragEvent, meta: OutputsDragMeta): void {
  e.dataTransfer.setData(MISSION_DND_MIME, buildMissionDragPayload(meta));
  e.dataTransfer.effectAllowed = "copy";
  applyMissionDragImage(e.dataTransfer, meta.utterance || meta.slug);
  useMissionDrag.getState().begin();
}

export function OutputsView() {
  const t = useT();
  const { data, isLoading, error } = useOutputsList();
  const sessions = useMemo(
    () => (data ?? []).slice(0, 20),
    [data],
  );
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);

  const selected = useMemo(
    () => sessions.find((s) => s.slug === selectedSlug) ?? null,
    [sessions, selectedSlug],
  );

  // Auto-select: erster Eintrag, sobald Daten da sind und nichts ausgewaehlt ist.
  const effectiveSlug =
    selectedSlug ?? (sessions.length > 0 ? sessions[0].slug : null);
  const effectiveSelected =
    selected ?? (sessions.length > 0 ? sessions[0] : null);

  const openOnDesktop = async (slug: string) => {
    try {
      await fetch(`/api/outputs/${slug}/open`, { method: "POST" });
    } catch {
      // ignoriert — Toast waere nice-to-have, aber der Call ist best-effort.
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<FolderOpen className="h-4 w-4 text-primary" />}
        title={t("outputs_view.title")}
        subtitle={t("outputs_view.subtitle")}
      />

      <div className="flex flex-1 min-h-0">
        <aside className="flex w-96 shrink-0 flex-col border-r border-border">
          {isLoading ? (
            <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : error ? (
            <div className="p-4 text-xs text-destructive">
              Outputs nicht geladen: {String(error)}
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-muted-foreground">
              Noch keine Sub-Agent-Sessions. Starte einen OpenClaw-Task.
            </div>
          ) : (
            <ScrollArea className="flex-1">
              <ul className="flex flex-col gap-1 px-3 py-3">
                {sessions.map((s) => (
                  <li key={s.slug}>
                    <SessionRow
                      meta={s}
                      isSelected={effectiveSlug === s.slug}
                      onSelect={() => setSelectedSlug(s.slug)}
                      onOpenDesktop={() => openOnDesktop(s.slug)}
                    />
                  </li>
                ))}
              </ul>
            </ScrollArea>
          )}
        </aside>

        <section className="flex min-w-0 flex-1 flex-col">
          {effectiveSelected ? (
            <SessionDetail meta={effectiveSelected} />
          ) : (
            <div className="flex flex-1 items-center justify-center text-xs text-muted-foreground">
              Waehle eine Session.
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function SessionRow({
  meta,
  isSelected,
  onSelect,
  onOpenDesktop,
}: {
  meta: OutputSummary;
  isSelected: boolean;
  onSelect: () => void;
  onOpenDesktop: () => void;
}) {
  const t = useT();
  const cancel = useCancelMission();
  const statusKey = meta.status ?? "unknown";
  const badgeClass = STATUS_BADGE[statusKey] ?? STATUS_BADGE.unknown;
  const ts = meta.completed_at ?? meta.started_at;
  const tsLabel = ts ? new Date(ts * 1000).toLocaleString() : "--";
  const canAbort = statusKey === "running" && !!meta.mission_id;

  return (
    <button
      type="button"
      draggable
      onDragStart={(e) => startMissionDrag(e, meta)}
      onDragEnd={() => useMissionDrag.getState().end()}
      onClick={onSelect}
      className={cn(
        "w-full cursor-grab rounded-lg border p-3 text-left transition-colors hover:border-primary/40 active:cursor-grabbing",
        isSelected
          ? "border-primary/40 bg-primary/10"
          : "border-border bg-card/40",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-mono text-[10px] text-muted-foreground">
            {meta.slug}
          </div>
          {meta.utterance && (
            <div className="mt-0.5 break-words text-sm">{meta.utterance}</div>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="flex items-center gap-1.5">
            {canAbort && (
              <HoldToAbortButton
                size="sm"
                pending={cancel.isPending}
                onConfirm={() => {
                  if (meta.mission_id) cancel.mutate(meta.mission_id);
                }}
                label={
                  cancel.isPending
                    ? t("outputs_view.aborting")
                    : t("outputs_view.abort_hold")
                }
              />
            )}
            {statusKey === "cancelled" && meta.mission_id && (
              <RerunButton
                missionId={meta.mission_id}
                action="continue"
                size="sm"
              />
            )}
            {statusKey === "error" && meta.mission_id && (
              <RerunButton
                missionId={meta.mission_id}
                action="restart"
                size="sm"
              />
            )}
            <span
              className={cn(
                "flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                badgeClass,
              )}
            >
              {statusKey === "running" && <PulseDot />}
              {statusKey}
            </span>
          </span>
          {meta.github_url && (
            <a
              href={meta.github_url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              title="GitHub"
              className="text-muted-foreground hover:text-primary"
            >
              <Github className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>

      <div className="mt-1.5 flex items-center gap-3 text-[10px] text-muted-foreground">
        <span>{tsLabel}</span>
        {typeof meta.duration_s === "number" && (
          <span>{meta.duration_s.toFixed(1)}s</span>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOpenDesktop();
          }}
          className="ml-auto flex items-center gap-1 hover:text-primary"
          title={t("common.open_in_explorer")}
        >
          <ExternalLink className="h-3 w-3" />
          Desktop
        </button>
      </div>

      {meta.summary && (
        <div className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">
          {meta.summary}
        </div>
      )}
    </button>
  );
}

function SessionDetail({ meta }: { meta: OutputSummary }) {
  const t = useT();
  const cancel = useCancelMission();
  const plan = usePlanForOutput(meta.slug);
  const statusKey = meta.status ?? "unknown";
  const badgeClass = STATUS_BADGE[statusKey] ?? STATUS_BADGE.unknown;
  const canAbort = statusKey === "running" && !!meta.mission_id;
  const hasPlan =
    !plan.isLoading && plan.data && plan.data.plan !== null;
  const isSingleShot =
    !plan.isLoading && plan.data !== undefined && plan.data.plan === null;

  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-5 px-6 py-5">
        <header className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold text-foreground">
              {meta.utterance ?? meta.slug}
            </h3>
            <span
              className={cn(
                "flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                badgeClass,
              )}
            >
              {statusKey === "running" && <PulseDot />}
              {statusKey}
            </span>
            {typeof meta.duration_s === "number" && (
              <span className="text-xs text-muted-foreground">
                {meta.duration_s.toFixed(1)}s
              </span>
            )}
            {canAbort && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-destructive/30 bg-destructive/5 py-0.5 pl-1 pr-2.5">
                <HoldToAbortButton
                  size="md"
                  pending={cancel.isPending}
                  onConfirm={() => {
                    if (meta.mission_id) cancel.mutate(meta.mission_id);
                  }}
                  label={
                    cancel.isPending
                      ? t("outputs_view.aborting")
                      : t("outputs_view.abort_label")
                  }
                />
                <span className="text-[11px] text-muted-foreground">
                  {cancel.isPending
                    ? t("outputs_view.aborting")
                    : t("outputs_view.abort_hold")}
                </span>
              </span>
            )}
            {statusKey === "cancelled" && meta.mission_id && (
              <RerunButton
                missionId={meta.mission_id}
                action="continue"
                size="md"
              />
            )}
            {statusKey === "error" && meta.mission_id && (
              <RerunButton
                missionId={meta.mission_id}
                action="restart"
                size="md"
              />
            )}
            {meta.github_url && (
              <a
                href={meta.github_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded border border-border bg-secondary/40 px-2 py-0.5 text-[11px] text-muted-foreground hover:text-primary"
              >
                <Github className="h-3 w-3" />
                GitHub
              </a>
            )}
          </div>
          <div className="font-mono text-[11px] text-muted-foreground">
            {meta.slug}
          </div>
        </header>

        {meta.summary && (
          <section className="rounded-xl border border-border bg-card/40 p-4">
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Summary
            </div>
            <p className="text-sm leading-relaxed text-foreground/90">
              <LinkifiedText text={meta.summary} />
            </p>
          </section>
        )}

        {meta.error && (
          <section className="rounded-xl border border-destructive/30 bg-destructive/5 p-4">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-destructive">
              Fehler
            </div>
            <pre className="whitespace-pre-wrap text-xs text-destructive/90">
              {meta.error}
            </pre>
          </section>
        )}

        <ArtifactsSection slug={meta.slug} />

        <section className="rounded-xl border border-border bg-card/40 p-4">
          <div className="mb-3 flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-primary" />
            <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Plan
            </span>
            {hasPlan && plan.data?.plan && (
              <span className="text-[11px] text-muted-foreground">
                {plan.data.plan.total_steps} Steps - Status{" "}
                {plan.data.plan.status}
              </span>
            )}
          </div>

          {plan.isLoading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              Lade Plan...
            </div>
          ) : plan.isError ? (
            <div className="text-xs text-destructive">
              Plan konnte nicht geladen werden: {String(plan.error)}
            </div>
          ) : isSingleShot ? (
            <div className="flex items-center gap-2 rounded-lg border border-border bg-background/50 p-3 text-xs text-muted-foreground">
              <FileQuestion className="h-4 w-4" />
              Single-Shot-Run — kein strukturierter Plan fuer diese Session.
            </div>
          ) : hasPlan && plan.data ? (
            <>
              {plan.data.plan?.vision && (
                <p className="mb-3 border-l-2 border-primary/40 pl-3 text-xs italic text-muted-foreground">
                  {plan.data.plan.vision}
                </p>
              )}
              <PlanStepList steps={plan.data.steps} />
            </>
          ) : null}
        </section>
      </div>
    </ScrollArea>
  );
}

// Pure plumbing the worker subprocess emits — never a user deliverable.
// Hiding it keeps the list to the actual files the worker created (under
// artifacts/files/) plus the captured diff, so a non-coder sees their result
// instead of stream logs (2026-05-29).
function isPlumbingArtifact(path: string): boolean {
  return (
    path.endsWith("stream.jsonl") ||
    path.endsWith("stderr.log") ||
    path.endsWith(".jarvis-mcp.json") ||
    path === "reflections.md"
  );
}

function ArtifactsSection({ slug }: { slug: string }) {
  const q = useArtifactsForOutput(slug);
  const caps = useOutputsCapabilities();
  const nativeActions = caps.data?.native_file_actions ?? false;
  const allFiles = q.data?.files ?? [];
  // Show genuine deliverables + the captured diff; hide stream/stderr/mcp noise.
  const files = allFiles.filter((f) => !isPlumbingArtifact(f.path));

  return (
    <section className="rounded-xl border border-border bg-card/40 p-4">
      <div className="mb-3 flex items-center gap-2">
        <FileText className="h-4 w-4 text-primary" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Ergebnisse
        </span>
        {!q.isLoading && (
          <span className="text-[11px] text-muted-foreground">
            {files.length} {files.length === 1 ? "Datei" : "Dateien"}
          </span>
        )}
      </div>

      {q.isLoading ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          Lade Artefakte...
        </div>
      ) : q.isError ? (
        <div className="text-xs text-destructive">
          Artefakte nicht geladen: {String(q.error)}
        </div>
      ) : files.length === 0 ? (
        <div className="text-xs text-muted-foreground">
          Diese Session hat keine gespeicherten Dateien.
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {files.map((f) => (
            <ArtifactRow
              key={f.path}
              slug={slug}
              file={f}
              nativeActions={nativeActions}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function ArtifactRow({
  slug,
  file,
  nativeActions,
}: {
  slug: string;
  file: ArtifactSummary;
  nativeActions: boolean;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const [chooserOpen, setChooserOpen] = useState(false);
  const full = useArtifactFile(slug, expanded ? file.path : null);
  const openUrl = artifactOpenUrl(slug, file.path);
  const openers = useOpeners();
  const preferred = usePreferredOpener();
  const setPreferred = useSetPreferredOpener();

  // Open the file with a specific app (desktop). Remembering persists the
  // choice so the next "Open" click skips the chooser.
  const pickOpener = (opener: string, remember: boolean) => {
    void openArtifactWith(slug, file.path, opener).catch(() => {});
    if (remember) setPreferred.mutate(opener);
    setChooserOpen(false);
  };

  const handleOpen = () => {
    if (!nativeActions) {
      // Headless VPS: the UI is already a real browser tab — open the render
      // URL there (no local apps to launch).
      if (openUrl) window.open(openUrl, "_blank", "noopener,noreferrer");
      return;
    }
    const pref = preferred.data ?? "";
    if (pref) {
      void openArtifactWith(slug, file.path, pref).catch(() => {});
    } else {
      setChooserOpen(true); // first time: ask which app
    }
  };

  const sizeLabel =
    file.size < 1024
      ? `${file.size} B`
      : file.size < 1024 * 1024
        ? `${(file.size / 1024).toFixed(1)} KiB`
        : `${(file.size / (1024 * 1024)).toFixed(1)} MiB`;

  const previewText = expanded
    ? full.data?.text ?? file.preview ?? ""
    : file.preview;

  return (
    <li className="rounded-lg border border-border/60 bg-background/40">
      <div className="flex w-full items-center gap-1 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left hover:bg-secondary/30"
        >
          {expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
          )}
          <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
            {file.path}
          </span>
        </button>
        <span className="shrink-0 text-[10px] text-muted-foreground">
          {sizeLabel}
        </span>
        <div className="flex shrink-0 items-center gap-0.5">
          {(nativeActions || openUrl) && (
            <button
              type="button"
              title={t("outputs_view.open_action")}
              onClick={handleOpen}
              className="rounded p-1 hover:bg-secondary/40"
            >
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
          {nativeActions && (
            <button
              type="button"
              title={t("outputs_view.open_with_change")}
              onClick={() => setChooserOpen(true)}
              className="rounded p-1 hover:bg-secondary/40"
            >
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
          {nativeActions && (
            <button
              type="button"
              title={t("outputs_view.reveal_in_folder")}
              onClick={() =>
                void revealArtifact(slug, file.path).catch(() => {})
              }
              className="rounded p-1 hover:bg-secondary/40"
            >
              <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
            </button>
          )}
        </div>
      </div>

      {chooserOpen && (
        <OpenWithDialog
          openers={openers.data ?? []}
          onPick={pickOpener}
          onClose={() => setChooserOpen(false)}
        />
      )}

      {expanded && (
        <div className="border-t border-border/40 px-3 py-2">
          {!file.is_text ? (
            <div className="text-[11px] text-muted-foreground">
              {t("outputs_view.binary_file_hint")}
            </div>
          ) : full.isLoading ? (
            <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {t("outputs_view.loading_file")}
            </div>
          ) : full.isError ? (
            <div className="text-[11px] text-destructive">
              {t("common.error")}: {String(full.error)}
            </div>
          ) : (
            <>
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-[11px] font-mono text-foreground/90">
                {previewText || ""}
              </pre>
              {full.data?.truncated && (
                <div className="mt-1 text-[10px] text-muted-foreground">
                  Datei gekürzt auf 1 MiB — Rest im Datei-Manager anzeigen.
                </div>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Linkifiziert URLs im Text. Regex ist bewusst simpel — fuer saubere
 * Markdown-Unterstuetzung muesste ein Parser her, aber die Summary ist
 * Plain-Text mit gelegentlichen Links.
 */
function LinkifiedText({ text }: { text: string }) {
  const parts = useMemo(() => {
    const out: Array<{ type: "text" | "url"; value: string }> = [];
    let last = 0;
    URL_REGEX.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = URL_REGEX.exec(text)) !== null) {
      if (match.index > last) {
        out.push({ type: "text", value: text.slice(last, match.index) });
      }
      out.push({ type: "url", value: match[0] });
      last = match.index + match[0].length;
    }
    if (last < text.length) {
      out.push({ type: "text", value: text.slice(last) });
    }
    return out;
  }, [text]);

  return (
    <>
      {parts.map((p, i) =>
        p.type === "url" ? (
          <a
            key={i}
            href={p.value}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline-offset-2 hover:underline"
          >
            {p.value}
          </a>
        ) : (
          <span key={i}>{p.value}</span>
        ),
      )}
    </>
  );
}
