/**
 * "Which Jarvis am I looking at?" — the run's recorded configuration.
 *
 * A forensic report without it is unreadable: the same sentence takes a
 * completely different path in realtime vs. pipeline mode, on another provider,
 * or started by hotkey instead of the wake word. Every value is read back from
 * what the run RECORDED, never from the host's current config — a run inspected
 * tomorrow must show the setup it actually ran under.
 */
import { useT } from "@/i18n";

import type { RunEnvironment } from "./types";

export function EnvironmentPanel({ env }: { env: RunEnvironment }) {
  const t = useT();
  const rows: Array<[string, string]> = [];
  const push = (label: string, value: string | number | null | undefined): void => {
    if (value === null || value === undefined || value === "") return;
    rows.push([label, String(value)]);
  };

  push(t("run_inspector.env.mode"), env.voice_mode);
  push(t("run_inspector.env.surface"), env.surface);
  push(
    t("run_inspector.env.started_by"),
    env.wake_keyword ? `${env.wake_source} · "${env.wake_keyword}"` : env.wake_source,
  );
  push(t("run_inspector.env.ended_by"), env.hangup_reason);
  push(t("run_inspector.env.language"), env.language);
  push(t("run_inspector.env.providers"), env.providers.join(", "));
  push(t("run_inspector.env.models"), env.models.join(", "));
  push(t("run_inspector.env.tiers"), env.tiers.join(", "));
  push(t("run_inspector.env.voices"), env.voices.join(", "));
  if (env.input_sample_rate || env.output_sample_rate) {
    push(
      t("run_inspector.env.audio"),
      `${env.input_sample_rate ?? "?"} Hz in · ${env.output_sample_rate ?? "?"} Hz out`,
    );
  }

  if (rows.length === 0) {
    return (
      <p className="text-base text-muted-foreground">{t("run_inspector.env.empty")}</p>
    );
  }

  return (
    // Label/value pairs, not a table: every value is a short recorded fact, so
    // the label sits in its own fixed column and the value keeps the monospace
    // spelling it was written with.
    <dl
      className="grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-x-4 gap-y-2.5"
      data-testid="environment-panel"
    >
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="truncate text-sm text-muted-foreground">{label}</dt>
          <dd className="min-w-0 break-words font-mono text-sm text-foreground [overflow-wrap:anywhere]">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
