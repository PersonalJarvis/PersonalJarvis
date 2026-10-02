/**
 * Local voice card body: setup progress, self-test result, model and voice
 * choice, expected latency, and an honest "not verified on this system".
 *
 * Talks only to `/api/providers/local-voice/*` (see `@/lib/localVoice`). It
 * polls while setup, start-up or a self-test is running and otherwise reads
 * the status once on mount and then slowly. Every colour is a theme token, so
 * the panel follows light and dark mode.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Check, Download, Loader2, Play, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { useT, useUiLanguage } from "@/i18n";
import {
  fetchLocalVoiceStatus,
  latencyFigure,
  localVoiceIsBusy,
  percentOf,
  runLocalVoiceSelftest,
  saveLocalVoiceSettings,
  stageKey,
  startLocalVoiceSetup,
  type LocalVoiceSelftest,
  type LocalVoiceStatus,
} from "@/lib/localVoice";
import { cn } from "@/lib/utils";

const BUSY_POLL_MS = 2500;
const IDLE_POLL_MS = 20_000;
/** The automatic model choice; never a real Ollama tag (tags contain ":"). */
const AUTO_MODEL = "__auto__";

export function LocalVoicePanel({ onChanged }: { onChanged: () => void }) {
  const t = useT();
  const locale = useUiLanguage();
  const [status, setStatus] = useState<LocalVoiceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const busy = localVoiceIsBusy(status);
  const wasBusy = useRef(false);

  const accept = useCallback(
    (next: LocalVoiceStatus) => {
      const nowBusy = localVoiceIsBusy(next);
      // A finished setup or self-test changes the card's readiness chip.
      const finished = wasBusy.current && !nowBusy;
      wasBusy.current = nowBusy;
      setStatus(next);
      if (finished) onChanged();
    },
    [onChanged],
  );

  const refresh = useCallback(async () => {
    try {
      accept(await fetchLocalVoiceStatus());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [accept]);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (!cancelled) await refresh();
    };
    void tick();
    const timer = window.setInterval(tick, busy ? BUSY_POLL_MS : IDLE_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [busy, refresh]);

  const act = async (action: () => Promise<LocalVoiceStatus>) => {
    setError(null);
    try {
      accept(await action());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const save = async (change: { voice?: string; llm_model?: string }) => {
    setSaving(true);
    await act(() => saveLocalVoiceSettings(change));
    setSaving(false);
    onChanged();
  };

  const modelOptions = useMemo<BrandedSelectOption[]>(() => {
    if (!status) return [];
    const auto: BrandedSelectOption = {
      value: AUTO_MODEL,
      label: t("apikeys_view.local_voice_llm_auto"),
      hint: status.llm_source === "config" ? undefined : status.llm_model,
    };
    const names = new Set(status.llm_choices);
    if (status.llm_source === "config") names.add(status.llm_model);
    return [auto, ...[...names].sort().map((name) => ({ value: name, label: name }))];
  }, [status, t]);

  const voiceOptions = useMemo<BrandedSelectOption[]>(
    () =>
      (status?.voices ?? []).map((voice) => ({
        value: voice,
        label: t(`apikeys_view.local_voice_voice_${voice}`),
      })),
    [status?.voices, t],
  );

  if (!status) {
    return (
      <div className="border-t border-border pt-3 text-xs text-muted-foreground" data-testid="local-voice-panel">
        {error ?? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-label={t("apikeys_view.local_voice_loading")} />}
      </div>
    );
  }

  const phaseLabel = t(`apikeys_view.local_voice_phase_${status.phase}`);
  const moving = status.phase === "installing" || status.phase === "starting";
  const stage = stageKey(status.stage);
  const stageLabel = stage ? t(`apikeys_view.local_voice_stage_${stage}`) : "";
  const showSetup = !status.setup.running && (!status.installed || Boolean(status.setup.error));
  const figure = latencyFigure(status.expected_latency, locale);

  return (
    <div className="space-y-3 border-t border-border pt-3 text-xs" data-testid="local-voice-panel">
      <div className="flex items-start gap-2" data-testid="local-voice-phase" data-phase={status.phase}>
        {status.phase === "ready" ? (
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : moving ? (
          <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
        ) : status.phase === "failed" ? (
          <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
        ) : (
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-foreground" />
        )}
        <div className="min-w-0 space-y-0.5">
          <p className="font-medium text-foreground">{phaseLabel}</p>
          {/* The backend's sentence explains a failure or a stop; "not set up"
              is already said by the translated phase label above. */}
          {status.reason && status.phase !== "installing" && status.phase !== "not_installed" && (
            <p className="text-muted-foreground">{status.reason}</p>
          )}
        </div>
      </div>

      {moving && (
        <div className="space-y-1" data-testid="local-voice-progress">
          <div className="flex justify-between gap-2 text-muted-foreground">
            <span className="truncate">{stageLabel || status.reason}</span>
            <span className="tabular-nums">{percentOf(status.progress)} %</span>
          </div>
          <div
            role="progressbar"
            aria-label={phaseLabel}
            aria-valuenow={percentOf(status.progress)}
            aria-valuemin={0}
            aria-valuemax={100}
            className="h-1.5 w-full overflow-hidden rounded-full bg-foreground/10"
          >
            <div
              className="h-full rounded-full bg-foreground/70 transition-all duration-500 motion-reduce:transition-none"
              style={{ width: `${Math.max(2, percentOf(status.progress))}%` }}
            />
          </div>
          {status.phase === "installing" && (
            <p className="text-muted-foreground">{t("apikeys_view.local_voice_setup_hint")}</p>
          )}
        </div>
      )}

      <dl className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2">
        <dt className="text-muted-foreground">{t("apikeys_view.local_voice_machine_label")}</dt>
        <dd className="text-foreground" data-testid="local-voice-machine">
          {t(`apikeys_view.local_voice_machine_${status.machine_class}`)}
        </dd>
        <dt className="text-muted-foreground">{t("apikeys_view.local_voice_latency_label")}</dt>
        <dd className="text-foreground" data-testid="local-voice-latency">
          {figure === null
            ? t("apikeys_view.local_voice_latency_selftest")
            : `${figure} s · ${t(`apikeys_view.local_voice_basis_${status.expected_latency.basis}`)}`}
        </dd>
        <dt className="text-muted-foreground">{t("apikeys_view.local_voice_llm_label")}</dt>
        <dd className="min-w-0">
          <BrandedSelect
            value={status.llm_source === "config" ? status.llm_model : AUTO_MODEL}
            options={modelOptions}
            onValueChange={(value) => void save({ llm_model: value === AUTO_MODEL ? "" : value })}
            ariaLabel={t("apikeys_view.local_voice_llm_label")}
            disabled={saving || status.setup.running}
            testId="local-voice-llm"
          />
        </dd>
        <dt className="text-muted-foreground">{t("apikeys_view.local_voice_voice_label")}</dt>
        <dd className="min-w-0">
          <BrandedSelect
            value={status.voice}
            options={voiceOptions}
            onValueChange={(value) => void save({ voice: value })}
            ariaLabel={t("apikeys_view.local_voice_voice_label")}
            disabled={saving || status.setup.running}
            testId="local-voice-voice"
          />
        </dd>
      </dl>

      {status.llm_error ? (
        <p className="text-muted-foreground">{status.llm_error}</p>
      ) : (
        status.llm_installed === false && (
          <p className="text-muted-foreground" data-testid="local-voice-llm-missing">
            {t("apikeys_view.local_voice_llm_missing")}
          </p>
        )
      )}

      {!status.os_verified && (
        <div className="flex items-start gap-2 text-muted-foreground" data-testid="local-voice-unverified">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t("apikeys_view.local_voice_unverified")}</span>
        </div>
      )}

      <SelftestSummary selftest={status.selftest} running={status.selftest_running} />

      {status.setup.warnings.map((warning) => (
        <p key={warning} className="text-muted-foreground">{warning}</p>
      ))}
      {status.setup.error && status.phase !== "failed" && (
        <p className="text-destructive">{status.setup.error}</p>
      )}
      {error && <p className="text-destructive">{error}</p>}

      <div className="flex flex-wrap gap-2">
        {showSetup && (
          <Button size="sm" variant="secondary" className="gap-2" onClick={() => void act(startLocalVoiceSetup)} data-testid="local-voice-setup">
            <Download className="h-3.5 w-3.5" />
            {status.setup.error
              ? t("apikeys_view.local_voice_setup_retry")
              : t("apikeys_view.local_voice_setup_cta")}
          </Button>
        )}
        {status.installed && !status.setup.running && (
          <Button
            size="sm"
            variant="secondary"
            className="gap-2"
            onClick={() => void act(runLocalVoiceSelftest)}
            disabled={status.selftest_running}
            data-testid="local-voice-selftest"
          >
            {status.selftest_running ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            {status.selftest_running
              ? t("apikeys_view.local_voice_selftest_running")
              : t("apikeys_view.local_voice_selftest_cta")}
          </Button>
        )}
      </div>
    </div>
  );
}

function SelftestSummary({
  selftest,
  running,
}: {
  selftest: LocalVoiceSelftest | null;
  running: boolean;
}) {
  const t = useT();
  if (running) return null;
  if (!selftest) {
    return <p className="text-muted-foreground">{t("apikeys_view.local_voice_selftest_none")}</p>;
  }
  const languages = Object.entries(selftest.languages);
  return (
    <div className="space-y-1" data-testid="local-voice-selftest-result" data-ok={selftest.ok}>
      <p className={cn("font-medium", selftest.ok ? "text-foreground" : "text-destructive")}>
        {selftest.ok
          ? t("apikeys_view.local_voice_selftest_passed")
          : t("apikeys_view.local_voice_selftest_failed")}
      </p>
      {selftest.reason && <p className="text-muted-foreground">{selftest.reason}</p>}
      {languages.length > 0 && (
        <ul className="space-y-0.5 text-muted-foreground">
          {languages.map(([language, result]) => (
            <li key={language} className="tabular-nums">
              {language.toUpperCase()} · {result.voice ?? "?"} ·{" "}
              {t("apikeys_view.local_voice_selftest_voice_ms").replace("{0}", String(result.synth_ms ?? "?"))}{" "}
              · {t("apikeys_view.local_voice_selftest_stt_ms").replace("{0}", String(result.stt_ms ?? "?"))}
            </li>
          ))}
        </ul>
      )}
      {selftest.llm && (
        <p className="tabular-nums text-muted-foreground">
          {t("apikeys_view.local_voice_selftest_llm_ms").replace("{0}", String(selftest.llm.ms ?? "?"))}
          {selftest.llm.error ? ` · ${selftest.llm.error}` : ""}
        </p>
      )}
      {selftest.stale && (
        <p className="text-muted-foreground" data-testid="local-voice-selftest-stale">
          {t("apikeys_view.local_voice_selftest_stale")}
        </p>
      )}
    </div>
  );
}
