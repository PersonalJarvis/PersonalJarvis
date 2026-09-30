import { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { BeatId } from "../beats";
import type { BeatProps } from "../WelcomeFlow";
import { PrimaryAction, Rise, Status } from "../ui";

interface Autostart {
  enabled: boolean;
  supported: boolean;
}

/** The beats whose outcome the review reads back, in the order they ran. */
const REVIEWED: BeatId[] = ["brain", "agents", "permissions", "voice"];

/**
 * The review and the start. What each beat reported sits in one list — set
 * up, or honestly not — and the one action starts the assistant: the backend
 * writes the completion marker and restarts the app once, so everything
 * chosen here takes effect together. The tour of the real app follows that
 * restart.
 */
export function ReadyBeat({ onb, results, chosenName }: BeatProps) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autostart, setAutostart] = useState<Autostart | null>(null);
  const skipped = new Set(onb.state?.skipped_steps ?? []);

  // Start at login — only offered where the host supports it (a headless
  // Linux server does not); a failed probe hides it, Settings stays the way.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/settings/autostart");
        if (res.ok && !cancelled) setAutostart((await res.json()) as Autostart);
      } catch {
        // Probe is best-effort; without it the switch is simply not shown.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function toggleAutostart(enabled: boolean) {
    setAutostart((s) => (s ? { ...s, enabled } : s));
    try {
      await fetch("/api/settings/autostart", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
    } catch {
      // The optimistic value stays; Settings is where it can be fixed.
    }
  }

  async function start() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onb.complete();
    } catch {
      setError(t("first_run.ready.start_failed"));
      setBusy(false);
    }
  }

  const rows = REVIEWED.filter((beat) => beat in results || skipped.has(beat)).map((beat) => {
    const result = results[beat];
    return {
      beat,
      label: t(`first_run.ready.row_${beat}`),
      value: result?.summary ?? t("first_run.ready.not_set"),
      ok: Boolean(result?.summary),
      gap: result?.gap ?? (skipped.has(beat) && !result ? t(`first_run.ready.skipped_${beat}`) : null),
    };
  });
  const gaps = rows.filter((row) => row.gap);
  const voice = results.voice?.summary ?? null;

  return (
    <div className="space-y-5">
      {chosenName && voice && (
        <Rise index={0}>
          <p className="rounded-xl bg-accent-soft px-4 py-3 text-center text-base text-foreground" data-testid="onboarding-say-hello">
            {fill(t("first_run.ready.say"), { phrase: voice, assistant: chosenName })}
          </p>
        </Rise>
      )}

      <Rise index={1}>
        <dl className="overflow-hidden rounded-xl border border-border bg-background" data-testid="onboarding-review">
          {rows.map((row) => (
            <div key={row.beat} className="flex items-center justify-between gap-4 border-b border-border px-4 py-3 last:border-b-0">
              <dt className="text-sm text-muted-foreground">{row.label}</dt>
              <dd className={cn("flex min-w-0 items-center gap-2 text-right text-sm", row.ok ? "font-medium text-foreground" : "text-muted-foreground")}>
                <span className="truncate">{row.value}</span>
                <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", row.ok ? "bg-success" : "bg-border-strong")} />
              </dd>
            </div>
          ))}
          {autostart?.supported && (
            <div className="flex items-center justify-between gap-4 px-4 py-3">
              <div className="min-w-0">
                <span className="block text-sm text-foreground">{t("first_run.ready.autostart")}</span>
                <span className="block text-xs text-muted-foreground">{t("first_run.ready.autostart_hint")}</span>
              </div>
              <Switch
                checked={autostart.enabled}
                onCheckedChange={(v) => void toggleAutostart(v)}
                aria-label={t("first_run.ready.autostart")}
                data-testid="onboarding-autostart"
              />
            </div>
          )}
        </dl>
      </Rise>

      {gaps.length > 0 && (
        <Rise index={2}>
          <div data-testid="onboarding-gaps" className="space-y-1.5">
            {gaps.map((row) => (
              <Status key={row.beat} tone="muted">
                {row.gap}
              </Status>
            ))}
          </div>
        </Rise>
      )}

      <Rise index={3}>
        <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.ready.restart_note")}</p>
      </Rise>

      {error && <Status tone="error">{error}</Status>}

      <Rise index={4}>
        <PrimaryAction onClick={() => void start()} busy={busy} testId="onboarding-start">
          {busy
            ? t("first_run.ready.starting")
            : chosenName
              ? fill(t("first_run.ready.start_named"), { assistant: chosenName })
              : t("first_run.ready.start")}
        </PrimaryAction>
      </Rise>
    </div>
  );
}
