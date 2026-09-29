import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import {
  fetchAppshotSettings,
  fetchLatestAppshot,
  forgetAppshots,
  formatAppshotHotkey,
  latestAppshotImageUrl,
  saveAppshotSettings,
  takeAppshot,
  type AppshotMeta,
  type AppshotSettings,
  type AppshotSettingsPatch,
} from "@/lib/appshotApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * Appshots — show the assistant the window you are working in.
 *
 * One page for the whole feature: the master switch (it is also the switch
 * for every other screen look, `[screen_context].enabled`), the global
 * shortcut, where a shortcut appshot goes, the sound and the flash, a try-it
 * button, and the last appshot so the user sees exactly what was handed over.
 * That picture lives in backend memory for `deck_preview_s` and is fetched
 * with `no-store`; this view keeps no copy.
 */

const IS_MAC = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform || "");
const PRESET_HOTKEYS = ["alt+alt", "ctrl+alt+a", ""] as const;
const TRY_DELAY_S = 3;

export function AppshotGlyph({ className }: { className?: string }) {
  // Four camera-frame corners around a pill: "this window, captured".
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden className={className}>
      <path
        d="M4 11V8a4 4 0 0 1 4-4h3M21 4h3a4 4 0 0 1 4 4v3M28 21v3a4 4 0 0 1-4 4h-3M11 28H8a4 4 0 0 1-4-4v-3"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <rect x="9.5" y="13" width="13" height="6" rx="3" fill="currentColor" />
    </svg>
  );
}

function Row({
  label,
  hint,
  control,
  children,
}: {
  label: string;
  hint?: string;
  control: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="px-5 py-4">
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0">
          <p className="text-base font-medium text-foreground">{label}</p>
          {hint && <p className="mt-0.5 text-sm text-muted-foreground">{hint}</p>}
        </div>
        <div className="shrink-0">{control}</div>
      </div>
      {children}
    </div>
  );
}

function DemoWindow({ className }: { className?: string }) {
  // A theme-token mock of an app window; no screenshot of anyone's screen.
  return (
    <div className={cn("flex flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm", className)}>
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        <span className="h-2 w-2 rounded-full bg-foreground/20" />
        <span className="h-2 w-2 rounded-full bg-foreground/20" />
        <span className="h-2 w-2 rounded-full bg-foreground/20" />
        <span className="ml-3 h-2 w-1/3 rounded-full bg-foreground/10" />
      </div>
      <div className="flex flex-1 gap-3 p-3">
        <div className="w-1/4 space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className="block h-2 rounded-full bg-foreground/10" />
          ))}
        </div>
        <div className="flex-1 space-y-2">
          <span className="block h-3 w-1/2 rounded-full bg-foreground/15" />
          {[0, 1, 2, 3, 4].map((i) => (
            <span key={i} className={cn("block h-2 rounded-full bg-foreground/10", i % 2 ? "w-4/5" : "w-full")} />
          ))}
          <span className="mt-3 block h-14 rounded-md bg-accent/15" />
        </div>
      </div>
    </div>
  );
}

function PreviewDemo() {
  const t = useT();
  return (
    <div className="flex h-full flex-col">
      <div className="relative aspect-[16/10] w-full overflow-hidden rounded-lg bg-secondary/60 p-5">
        <DemoWindow className="h-full w-full" />
        <div className="appshot-demo-card pointer-events-none absolute inset-5">
          <DemoWindow className="h-full w-full ring-4 ring-white/90" />
        </div>
        <div className="appshot-demo-flash pointer-events-none absolute inset-5 rounded-lg bg-white" />
      </div>
      <p className="mt-3 text-sm text-muted-foreground">{t("appshots.preview_empty")}</p>
    </div>
  );
}

function deliveredLabel(t: (key: string) => string, deliveredTo: string): string {
  switch (deliveredTo) {
    case "voice":
      return t("appshots.preview_sent_voice");
    case "message":
      return t("appshots.preview_sent_message");
    case "turn":
      return t("appshots.preview_sent_turn");
    case "none":
      return t("appshots.preview_sent_none");
    default:
      return "";
  }
}

function LatestPreview({ shot, onForget }: { shot: AppshotMeta; onForget: () => void }) {
  const t = useT();
  const time = new Date(shot.taken_at * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const where = shot.app_name || t("appshots.preview_front_window");
  const delivered = deliveredLabel(t, shot.delivered_to);
  return (
    <div className="flex h-full flex-col">
      <div className="flex aspect-[16/10] w-full items-center justify-center overflow-hidden rounded-lg bg-secondary/60 p-3">
        <img
          src={latestAppshotImageUrl(shot.id)}
          alt={t("appshots.preview_alt").replace("{0}", where)}
          className="max-h-full max-w-full rounded-md object-contain shadow-sm ring-1 ring-border"
        />
      </div>
      <div className="mt-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-base font-medium text-foreground">{where}</p>
          <p className="text-sm text-muted-foreground">
            {[time, `${shot.width} × ${shot.height}`, delivered].filter(Boolean).join(" · ")}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={onForget}>
          <Trash2 aria-hidden />
          {t("appshots.forget")}
        </Button>
      </div>
    </div>
  );
}

export function AppshotsView() {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const lastAppshotEvent = useEventStore(
    (s) => s.events.find((event) => event.name === "AppshotTaken")?.id ?? "",
  );
  const [settings, setSettings] = useState<AppshotSettings | null>(null);
  const [latest, setLatest] = useState<AppshotMeta | null>(null);
  const [saving, setSaving] = useState(false);
  const [countdown, setCountdown] = useState<number | null>(null);
  const countdownTimer = useRef<number | null>(null);

  useEffect(() => {
    let active = true;
    fetchAppshotSettings()
      .then((next) => active && setSettings(next))
      .catch((error: Error) => pushToast("error", error.message));
    return () => {
      active = false;
    };
  }, [pushToast]);

  // Refetch the last appshot whenever the backend reports a new one.
  useEffect(() => {
    let active = true;
    fetchLatestAppshot()
      .then((body) => active && setLatest(body.appshot))
      .catch(() => active && setLatest(null));
    return () => {
      active = false;
    };
  }, [lastAppshotEvent]);

  useEffect(
    () => () => {
      if (countdownTimer.current !== null) window.clearInterval(countdownTimer.current);
    },
    [],
  );

  const save = useCallback(
    async (patch: AppshotSettingsPatch) => {
      setSaving(true);
      setSettings((current) => (current ? { ...current, ...patch } : current));
      try {
        setSettings(await saveAppshotSettings(patch));
      } catch (error) {
        pushToast("error", t("appshots.save_error").replace("{0}", (error as Error).message));
        fetchAppshotSettings().then(setSettings).catch(() => undefined);
      } finally {
        setSaving(false);
      }
    },
    [pushToast, t],
  );

  const tryIt = useCallback(async () => {
    if (countdown !== null) return;
    setCountdown(TRY_DELAY_S);
    countdownTimer.current = window.setInterval(() => {
      setCountdown((value) => (value !== null && value > 1 ? value - 1 : value));
    }, 1000);
    try {
      const result = await takeAppshot(TRY_DELAY_S);
      if (result.ok) {
        setLatest(result.appshot);
        pushToast("success", t("appshots.try_done"));
      } else {
        pushToast("warning", t("appshots.toast_refused").replace("{0}", result.message));
      }
    } catch (error) {
      pushToast("error", (error as Error).message);
    } finally {
      if (countdownTimer.current !== null) window.clearInterval(countdownTimer.current);
      countdownTimer.current = null;
      setCountdown(null);
    }
  }, [countdown, pushToast, t]);

  const forget = useCallback(async () => {
    try {
      await forgetAppshots();
      setLatest(null);
    } catch (error) {
      pushToast("error", (error as Error).message);
    }
  }, [pushToast]);

  const hotkeyOptions = useMemo<BrandedSelectOption[]>(() => {
    const options: BrandedSelectOption[] = PRESET_HOTKEYS.map((value) => ({
      value: value || "off",
      label: value ? formatAppshotHotkey(value, IS_MAC) : t("appshots.shortcut_off"),
    }));
    const current = settings?.hotkey ?? "";
    if (current && !PRESET_HOTKEYS.includes(current as (typeof PRESET_HOTKEYS)[number])) {
      options.unshift({ value: current, label: formatAppshotHotkey(current, IS_MAC) });
    }
    return options;
  }, [settings?.hotkey, t]);

  const targetOptions = useMemo<BrandedSelectOption[]>(
    () => [
      { value: "auto", label: t("appshots.target_auto") },
      { value: "message", label: t("appshots.target_message") },
      { value: "voice", label: t("appshots.target_voice") },
    ],
    [t],
  );

  const shortcutHint = (() => {
    if (!settings) return "";
    if (!settings.hotkey) return t("appshots.shortcut_hint_off");
    if (settings.enabled && !settings.shortcut.armed && settings.shortcut.detail) {
      return t("appshots.shortcut_unavailable").replace("{0}", settings.shortcut.detail);
    }
    if (settings.hotkey === "alt+alt") {
      return IS_MAC ? t("appshots.shortcut_both_option_hint") : t("appshots.shortcut_both_alt_hint");
    }
    return t("appshots.shortcut_combo_hint").replace(
      "{0}",
      formatAppshotHotkey(settings.hotkey, IS_MAC),
    );
  })();

  const targetHint =
    settings?.target === "message"
      ? t("appshots.target_message_hint")
      : settings?.target === "voice"
        ? t("appshots.target_voice_hint")
        : t("appshots.target_auto_hint");

  const disabled = !settings || !settings.enabled;

  return (
    <div data-testid="appshots-view" className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis">
      <div className="w-full max-w-[1400px]">
        <PageHeader
          icon={<AppshotGlyph />}
          title={t("appshots.title")}
          description={t("appshots.subtitle")}
        />

        <div className="flex items-start gap-4 rounded-xl border border-border bg-card p-5">
          <AppshotGlyph className="mt-0.5 h-8 w-8 shrink-0 text-accent" />
          <div className="min-w-0">
            <p className="text-lg font-semibold text-foreground">{t("appshots.hero_title")}</p>
            <p className="mt-1 text-base text-muted-foreground">{t("appshots.hero_body")}</p>
          </div>
        </div>

        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <div className="divide-y divide-border self-start rounded-xl border border-border bg-card">
            {!settings ? (
              <div className="flex h-40 items-center justify-center" role="status" aria-busy="true">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
              </div>
            ) : (
              <>
                <Row
                  label={t("appshots.enabled_label")}
                  hint={t("appshots.enabled_hint")}
                  control={
                    <Switch
                      checked={settings.enabled}
                      disabled={saving}
                      aria-label={t("appshots.enabled_label")}
                      data-testid="appshots-enabled"
                      onCheckedChange={(enabled) => void save({ enabled })}
                    />
                  }
                />
                <Row
                  label={t("appshots.shortcut_label")}
                  hint={shortcutHint}
                  control={
                    <BrandedSelect
                      value={settings.hotkey || "off"}
                      options={hotkeyOptions}
                      ariaLabel={t("appshots.shortcut_label")}
                      disabled={disabled || saving}
                      testId="appshots-hotkey"
                      className="w-44"
                      onValueChange={(value) => void save({ hotkey: value === "off" ? "" : value })}
                    />
                  }
                />
                <Row
                  label={t("appshots.target_label")}
                  hint={targetHint}
                  control={
                    <BrandedSelect
                      value={settings.target}
                      options={targetOptions}
                      ariaLabel={t("appshots.target_label")}
                      disabled={disabled || saving}
                      testId="appshots-target"
                      className="w-44"
                      onValueChange={(value) =>
                        void save({ target: value as AppshotSettings["target"] })
                      }
                    />
                  }
                />
                <Row
                  label={t("appshots.sound_label")}
                  hint={settings.sound_effects_master ? undefined : t("appshots.sound_master_off")}
                  control={
                    <Switch
                      checked={settings.sound}
                      disabled={disabled || saving}
                      aria-label={t("appshots.sound_label")}
                      data-testid="appshots-sound"
                      onCheckedChange={(sound) => void save({ sound })}
                    />
                  }
                />
                <Row
                  label={t("appshots.effect_label")}
                  hint={
                    settings.readiness.effect
                      ? t("appshots.effect_hint")
                      : t("appshots.effect_unavailable").replace("{0}", settings.readiness.effect_detail)
                  }
                  control={
                    <Switch
                      checked={settings.effect}
                      disabled={disabled || saving}
                      aria-label={t("appshots.effect_label")}
                      data-testid="appshots-effect"
                      onCheckedChange={(effect) => void save({ effect })}
                    />
                  }
                />
                <Row
                  label={t("appshots.try_label")}
                  hint={
                    countdown !== null
                      ? t("appshots.try_counting").replace("{0}", String(countdown))
                      : t("appshots.try_hint")
                  }
                  control={
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      disabled={disabled || countdown !== null}
                      onClick={() => void tryIt()}
                      data-testid="appshots-try"
                    >
                      {countdown !== null && <Loader2 className="animate-spin" aria-hidden />}
                      {t("appshots.try_button")}
                    </Button>
                  }
                />
              </>
            )}
          </div>

          <div className="rounded-xl border border-border bg-card p-5">
            <p className="mb-3 text-base font-medium text-foreground">{t("appshots.preview_title")}</p>
            {latest ? <LatestPreview shot={latest} onForget={() => void forget()} /> : <PreviewDemo />}
          </div>
        </div>

        <p className="mt-5 text-sm text-muted-foreground">{t("appshots.voice_hint")}</p>
      </div>
    </div>
  );
}
