/**
 * Small shared pieces of the Computers section: the status light, the vital
 * meters, the copy field and the number formatting. Theme tokens only — hue
 * appears on status alone (success / warning / destructive), everything else
 * is ink on the neutral ladder, so light and dark mode need no second path.
 */
import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { Computer, HealthStatus } from "@/lib/computersApi";

export type Tone = "ok" | "warn" | "error" | "off" | "busy";

export function statusTone(status: HealthStatus): Tone {
  switch (status) {
    case "online":
      return "ok";
    case "provisioning":
      return "busy";
    case "auth_failed":
    case "host_key_changed":
      return "warn";
    case "offline":
    case "error":
      return "error";
    default:
      return "off";
  }
}

/** A computer needs the user's hand (not just "is off"). */
export function needsAttention(computer: Computer): boolean {
  const s = computer.health.status;
  return s === "auth_failed" || s === "host_key_changed" || s === "error" || s === "offline";
}

const TONE_DOT: Record<Tone, string> = {
  ok: "bg-success",
  busy: "bg-info",
  warn: "bg-warning",
  error: "bg-destructive",
  off: "bg-foreground-faint",
};

/** The status light: a dot with a soft halo while it is live or moving. */
export function StatusLight({ tone, className }: { tone: Tone; className?: string }) {
  const live = tone === "ok" || tone === "busy";
  return (
    <span className={cn("relative inline-flex h-2.5 w-2.5 shrink-0", className)} aria-hidden>
      {live && (
        <span
          className={cn(
            "absolute inset-0 rounded-full opacity-40 motion-safe:animate-ping",
            TONE_DOT[tone],
            tone === "ok" && "[animation-duration:2.4s]",
          )}
        />
      )}
      <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full", TONE_DOT[tone])} />
    </span>
  );
}

/** Colour of a fill level: ink until it gets tight, then status hues. */
function fillClass(pct: number): string {
  if (pct >= 90) return "bg-destructive";
  if (pct >= 75) return "bg-warning";
  return "bg-foreground-secondary";
}

/** One vital as a thin bar: label left, value right, the level beneath. */
export function Meter({
  label,
  pct,
  value,
  compact = false,
}: {
  label: string;
  /** 0-100; null draws an empty track and a dash. */
  pct: number | null;
  value?: ReactNode;
  compact?: boolean;
}) {
  const clamped = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className="min-w-0" role="group" aria-label={label}>
      <div
        className={cn(
          "flex items-baseline justify-between gap-2",
          compact ? "text-xs" : "text-sm",
        )}
      >
        <span className="truncate text-muted-foreground">{label}</span>
        <span className="shrink-0 tabular-nums text-foreground-secondary">
          {value ?? (pct === null ? "—" : `${Math.round(clamped)} %`)}
        </span>
      </div>
      <div
        className={cn(
          "mt-1.5 overflow-hidden rounded-full bg-secondary",
          compact ? "h-1" : "h-1.5",
        )}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct === null ? undefined : Math.round(clamped)}
        aria-label={label}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700 ease-out",
            fillClass(clamped),
          )}
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  );
}

/** Load average as a share of the cores it can use. */
export function loadPct(computer: Computer): number | null {
  const load = computer.health.load_1m;
  const cores = computer.facts?.cpu_count;
  if (load === null || !cores) return null;
  return (100 * load) / cores;
}

export function formatUptime(seconds: number | null, t: (k: string) => string): string {
  if (seconds === null) return "—";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days} ${t("computers.unit_days")} ${hours} ${t("computers.unit_hours")}`;
  if (hours > 0) return `${hours} ${t("computers.unit_hours")} ${minutes} ${t("computers.unit_minutes")}`;
  return `${minutes} ${t("computers.unit_minutes")}`;
}

export function formatMemory(mb: number | null | undefined): string | null {
  if (!mb) return null;
  return mb >= 1024 ? `${Math.round((mb / 1024) * 10) / 10} GB` : `${mb} MB`;
}

export function formatAgo(epochS: number | null, t: (k: string) => string): string {
  if (!epochS) return t("computers.never_checked");
  const diff = Math.max(0, Date.now() / 1000 - epochS);
  if (diff < 45) return t("computers.just_now");
  if (diff < 3600) return `${Math.round(diff / 60)} ${t("computers.unit_minutes")}`;
  if (diff < 86400) return `${Math.round(diff / 3600)} ${t("computers.unit_hours")}`;
  return `${Math.round(diff / 86400)} ${t("computers.unit_days")}`;
}

/** Monospace value with a copy button — the public key, a fingerprint. */
export function CopyField({
  value,
  label,
  copyLabel,
  copiedLabel,
  multiline = false,
}: {
  value: string;
  label: string;
  copyLabel: string;
  copiedLabel: string;
  multiline?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="min-w-0">
      <div className="mb-1.5 text-sm text-muted-foreground">{label}</div>
      <div className="flex items-stretch gap-2">
        <code
          className={cn(
            "min-w-0 flex-1 rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-xs text-foreground-secondary",
            multiline ? "break-all" : "truncate",
          )}
          title={value}
        >
          {value}
        </code>
        <button
          type="button"
          onClick={() => {
            void robustCopy(value).then((ok) => {
              if (!ok) return;
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1600);
            });
          }}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border-strong px-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={copyLabel}
        >
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
          {copied ? copiedLabel : copyLabel}
        </button>
      </div>
    </div>
  );
}

/** A labelled form field in the section's quiet style. */
export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block min-w-0", className)}>
      <span className="mb-1.5 block text-sm font-medium text-foreground-secondary">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "h-9 w-full rounded-md border border-border-strong bg-input px-3 text-base text-foreground placeholder:text-foreground-faint transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-ring";
