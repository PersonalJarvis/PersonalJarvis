import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Eye,
  FilePenLine,
  ShieldAlert,
  SquareTerminal,
  TriangleAlert,
  X,
  XCircle,
} from "lucide-react";
import { useCommandActivityStore } from "@/store/commandActivity";
import type {
  CommandActivityEntry,
  CommandImpactLevel,
} from "@/lib/commandActivity";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * Live shell-command activity — the first place the app SHOWS a command run.
 *
 * Every subprocess spawns windowless on purpose, so until now the only sign
 * that Jarvis touched the machine was the spoken result. These cards make
 * the act visible while it happens: what runs (mono, exactly as executed),
 * what it would do (impact badge from the safety classifier — reads /
 * changes / deletes), and how it ended (duration, error, or the safety
 * layer's block). Trust through visibility — the UI half of the shell
 * explain layer.
 *
 * Sits above the JarvisDock (bottom-right); toasts keep the top-right.
 */

const MAX_VISIBLE = 4;

const LEVEL_ICON: Record<CommandImpactLevel, typeof Eye> = {
  read: Eye,
  modify: FilePenLine,
  destructive: TriangleAlert,
  unknown: SquareTerminal,
};

/** Accent per impact level — calm for reads, warm for writes, hot for
 * deletes; the same temperature scale the toasts already speak. */
const LEVEL_STYLE: Record<
  CommandImpactLevel,
  { chip: string; text: string; bar: string }
> = {
  read: {
    chip: "border-sky-400/30 bg-sky-400/10 text-sky-400",
    text: "text-sky-400",
    bar: "text-sky-400",
  },
  modify: {
    chip: "border-amber-500/30 bg-amber-500/10 text-amber-500",
    text: "text-amber-500",
    bar: "text-amber-500",
  },
  destructive: {
    chip: "border-destructive/40 bg-destructive/10 text-destructive",
    text: "text-destructive",
    bar: "text-destructive",
  },
  unknown: {
    chip: "border-border bg-muted/40 text-muted-foreground",
    text: "text-muted-foreground",
    bar: "text-muted-foreground",
  },
};

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

export function CommandActivityLayer() {
  const entries = useCommandActivityStore((s) => s.entries);
  const prune = useCommandActivityStore((s) => s.prune);

  // One shared 1 s heartbeat: advances the live timers AND expires settled
  // cards. Only runs while there is something on screen.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (entries.length === 0) return;
    const timer = window.setInterval(() => {
      const ts = Date.now();
      setNow(ts);
      prune(ts);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [entries.length, prune]);

  if (entries.length === 0) return null;

  const visible = entries.slice(-MAX_VISIBLE);
  const overflow = entries.length - visible.length;

  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-16 right-4 z-50 flex w-[360px] flex-col items-end gap-2"
    >
      {overflow > 0 && (
        <span className="rounded-full border border-border bg-card/95 px-2 py-0.5 text-[10px] font-medium tabular-nums text-muted-foreground backdrop-blur">
          +{overflow}
        </span>
      )}
      {visible.map((entry) => (
        <CommandCard key={entry.id} entry={entry} now={now} />
      ))}
    </div>
  );
}

function CommandCard({
  entry,
  now,
}: {
  entry: CommandActivityEntry;
  now: number;
}) {
  const t = useT();
  const dismiss = useCommandActivityStore((s) => s.dismiss);
  const style = LEVEL_STYLE[entry.level];
  const LevelIcon = LEVEL_ICON[entry.level];
  const running = entry.status === "running";

  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto relative w-full overflow-hidden rounded-lg border bg-card/95 backdrop-blur",
        "animate-in slide-in-from-bottom-3 fade-in duration-300",
        entry.status === "failed" || entry.status === "blocked"
          ? "border-destructive/40"
          : "border-border",
      )}
    >
      <div className="flex items-start gap-2.5 px-3 py-2.5">
        <span
          className={cn(
            "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border",
            style.chip,
          )}
          title={entry.words || undefined}
        >
          <LevelIcon className="h-3.5 w-3.5" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <span
              className={cn(
                "text-[11px] font-semibold tracking-wide",
                style.text,
              )}
            >
              {t(`command_activity.level_${entry.level}`)}
            </span>
            <StatusBadge entry={entry} now={now} />
          </div>

          {entry.command && (
            <div
              className="mt-1 flex items-center gap-1.5 font-mono text-[11px] leading-relaxed text-foreground/90"
              title={entry.command}
            >
              <span className={cn("shrink-0 font-bold", style.text)}>❯</span>
              <span className="truncate">{entry.command}</span>
            </div>
          )}

          {entry.detail && entry.status !== "done" && (
            <div className="mt-1 break-words text-[10px] leading-relaxed text-muted-foreground">
              {entry.detail}
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={() => dismiss(entry.id)}
          className="shrink-0 text-muted-foreground/60 transition-colors hover:text-foreground"
          aria-label={t("command_activity.dismiss")}
        >
          <X className="h-3 w-3" />
        </button>
      </div>

      {/* Indeterminate sweep while running — 2 px of the impact color. */}
      {running && (
        <div
          className={cn(
            "command-activity-progress h-0.5 w-full opacity-80",
            style.bar,
          )}
        />
      )}
    </div>
  );
}

function StatusBadge({
  entry,
  now,
}: {
  entry: CommandActivityEntry;
  now: number;
}) {
  const t = useT();
  switch (entry.status) {
    case "running": {
      const elapsed = Math.max(0, Math.round((now - entry.startedTs) / 1000));
      return (
        <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
          {elapsed > 0 ? `${elapsed} s` : t("command_activity.running")}
        </span>
      );
    }
    case "done":
      return (
        <span className="flex shrink-0 items-center gap-1 text-[10px] tabular-nums text-primary">
          <CheckCircle2 className="h-3 w-3" />
          {entry.durationMs ? formatDuration(entry.durationMs) : t("command_activity.done")}
        </span>
      );
    case "failed":
      return (
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-destructive">
          <XCircle className="h-3 w-3" />
          {t("command_activity.failed")}
        </span>
      );
    case "blocked":
      return (
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-destructive">
          <ShieldAlert className="h-3 w-3" />
          {t("command_activity.blocked")}
        </span>
      );
  }
}
