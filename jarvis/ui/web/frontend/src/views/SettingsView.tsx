import { useCallback, useEffect, useRef, useState } from "react";
import {
  Settings,
  Mic,
  Keyboard,
  Loader2,
  Languages,
} from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { OverlayTaskbarGroup } from "@/views/settings/OverlayTaskbarGroup";
import { LanguagesGroup } from "@/views/settings/LanguagesGroup";
import { MusicGroup } from "@/views/settings/MusicGroup";
import { AppSettingsGroup } from "@/views/settings/AppSettingsGroup";
import { PermissionsPanel } from "@/views/settings/PermissionsPanel";
import { RealtimeVoiceGroup } from "@/views/settings/RealtimeVoiceGroup";
import { SilenceWindowGroup } from "@/views/settings/SilenceWindowGroup";
import { VolumeGroup } from "@/views/settings/VolumeGroup";
import { AudioDevicesGroup } from "@/views/settings/AudioDevicesGroup";
import { SystemPromptGroup } from "@/views/settings/SystemPromptGroup";
import { SettingsGroupBoundary } from "@/views/settings/SettingsGroupBoundary";
import { ScreenContextGroup } from "@/views/settings/ScreenContextGroup";
import { settingsInputCls } from "@/views/settings/SettingsBlock";
import {
  useWakeWord,
  useLocalSpeechInstall,
  type WakeWordSaveResult,
} from "@/hooks/useWakeWord";
import { useKeybinds, type KeybindAction } from "@/hooks/useHotkey";
import { KeybindRow } from "@/views/settings/KeybindRow";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
import { WAKE_ENGINES, WAKE_ENGINE_I18N_KEY } from "@/constants/wakeEngines";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import { isAutopilotToastsEnabled, setAutopilotToastsEnabled } from "@/lib/autopilotToasts";

// The wake-word language pin — its OWN setting ([trigger.wake_word] language),
// deliberately independent of both the app display language and the general
// STT recognition language (maintainer mandate 2026-07-21: app in English +
// wake word spoken in German must be possible, with neither following the
// other). Mirrors jarvis/ui/web/settings_routes.py::_WAKE_LANGUAGES minus
// "auto".
type WakeLanguage = "en" | "de" | "es";

// Concrete spoken languages only — "auto" is deliberately NOT offered here: the
// wake word must be pinned to the language the user actually speaks (an
// ambiguous "auto" silently derives a default from other settings, the exact
// trap that left German speakers deaf). A user on "auto" sees a "choose your
// language" placeholder until they pick.
const WAKE_LANGUAGES: WakeLanguage[] = ["en", "de", "es"];

interface WakeSelfTestResult {
  ok: boolean;
  phrase: string;
  engine: string;
  language: string;
  wake_available: boolean;
  phrase_in_vocab: boolean | null;
  mic_ok: boolean;
  message: string;
  hint: string;
}

interface SettingRow {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  control?: React.ReactNode;
  value?: string;
}

export function SettingsView() {
  const t = useT();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [activeSection, setActiveSection] = useState<string>(SECTIONS[0].id);

  // The nav follows the scroll: the topmost group intersecting the upper
  // third of the column is the active one.
  useEffect(() => {
    const root = scrollRef.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    const visible = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).dataset.settingsSection ?? "";
          if (entry.isIntersecting) visible.set(id, entry.boundingClientRect.top);
          else visible.delete(id);
        }
        if (visible.size === 0) return;
        const [top] = [...visible.entries()].sort((a, b) => a[1] - b[1]);
        setActiveSection(top[0]);
      },
      { root, rootMargin: "0px 0px -66% 0px", threshold: 0 },
    );
    root.querySelectorAll<HTMLElement>("[data-settings-section]").forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, []);

  const jumpTo = useCallback((id: string) => {
    setActiveSection(id);
    document.getElementById(`settings-${id}`)?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-8">
        <PageHeader
          icon={<Settings />}
          title={t("settings_view.title")}
          description={t("settings_view.subtitle")}
          className="pb-2"
        />
      </div>
      {/* Two columns (v4): a sticky section nav on the left, the groups as
          cards stretching across the remaining width on the right. Each group is fault-isolated:
          one panel throwing costs that one panel, never the whole page. */}
      <div
        data-testid="settings-scroll"
        className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis"
        ref={scrollRef}
      >
        <div className="flex gap-10 px-8 pb-12 pt-4">
          <SettingsSectionNav sections={SECTIONS} active={activeSection} onPick={jumpTo} />
          <div className="min-w-0 flex-1 space-y-10">
            {SECTIONS.map((section) => (
              <section
                key={section.id}
                id={`settings-${section.id}`}
                data-settings-section={section.id}
                className="scroll-mt-4"
              >
                <SettingsGroupBoundary group={section.id}>
                  {section.render()}
                </SettingsGroupBoundary>
              </section>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** The groups in page order; the nav on the left reads the same list. */
const SECTIONS: readonly { id: string; labelKey: string; render: () => React.ReactNode }[] = [
  { id: "languages", labelKey: "settings_view.nav.languages", render: () => <LanguagesGroup /> },
  { id: "app", labelKey: "settings_view.nav.app", render: () => <AppSettingsGroup /> },
  { id: "permissions", labelKey: "settings_view.nav.permissions", render: () => <PermissionsPanel /> },
  { id: "screen-context", labelKey: "settings_view.nav.screen_context", render: () => <ScreenContextGroup /> },
  { id: "realtime-voice", labelKey: "settings_view.nav.realtime_voice", render: () => <RealtimeVoiceGroup /> },
  { id: "system-prompt", labelKey: "settings_view.nav.system_prompt", render: () => <SystemPromptGroup /> },
  { id: "wake-word", labelKey: "settings_view.nav.wake_word", render: () => <WakeWordPanel /> },
  { id: "silence-window", labelKey: "settings_view.nav.silence_window", render: () => <SilenceWindowGroup /> },
  { id: "volume", labelKey: "settings_view.nav.volume", render: () => <VolumeGroup /> },
  { id: "audio-devices", labelKey: "settings_view.nav.audio_devices", render: () => <AudioDevicesGroup /> },
  { id: "music", labelKey: "settings_view.nav.music", render: () => <MusicGroup /> },
  { id: "keybinds", labelKey: "settings_view.nav.keybinds", render: () => <KeybindsPanel /> },
  { id: "more", labelKey: "settings_view.nav.more", render: () => <MoreSettings /> },
  { id: "overlay-taskbar", labelKey: "settings_view.nav.overlay_taskbar", render: () => <OverlayTaskbarGroup /> },
];

function SettingsSectionNav({
  sections,
  active,
  onPick,
}: {
  sections: readonly { id: string; labelKey: string }[];
  active: string;
  onPick: (id: string) => void;
}) {
  const t = useT();
  return (
    <nav
      aria-label={t("settings_view.title")}
      data-testid="settings-section-nav"
      className="sticky top-0 hidden w-60 shrink-0 self-start lg:block"
    >
      <ul className="space-y-0.5">
        {sections.map((s) => {
          const isActive = s.id === active;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onPick(s.id)}
                aria-current={isActive ? "true" : undefined}
                className={cn(
                  "flex h-8 w-full items-center rounded-md px-3 text-left text-base transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "jarvis-nav-active bg-secondary font-medium text-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                {t(s.labelKey)}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function useMoreSettingRows(): SettingRow[] {
  const t = useT();
  const [autopilotToasts, setAutopilotToasts] = useState(isAutopilotToastsEnabled);
  return [
    {
      icon: Settings,
      title: t("settings_view.rows.toasts_title"),
      description: t("settings_view.rows.toasts_description"),
      control: (
        <Switch
          checked={autopilotToasts}
          aria-label={t("settings_view.rows.toasts_title")}
          onCheckedChange={(next) => {
            setAutopilotToasts(next);
            setAutopilotToastsEnabled(next);
          }}
        />
      ),
    },
  ];
}

function MoreSettings() {
  const rows = useMoreSettingRows();
  return (
    <ul className="space-y-3">
      {rows.map((r) => (
        <SettingRow key={r.title} row={r} />
      ))}
    </ul>
  );
}

function SettingRow({ row }: { row: SettingRow }) {
  const Icon = row.icon;
  return (
    <li className="flex items-center gap-3 rounded-lg border border-border bg-card p-block">
      {/* Secondary ink, never --primary: an icon that is brighter than the
          heading it labels inverts the ramp (rule 8). */}
      <Icon className="h-5 w-5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="text-title font-semibold text-foreground-strong">
          {row.title}
        </div>
        <p className="mt-1 text-meta text-muted-foreground">{row.description}</p>
      </div>
      {row.value && (
        <span className="font-mono text-meta tabular-nums text-muted-foreground">
          {row.value}
        </span>
      )}
      {row.control}
    </li>
  );
}

/**
 * Editable wake-word panel: free-text phrase input, engine select, optional
 * custom-model path, and a Save button that surfaces the backend's resolved
 * engine, message, and a restart hint. A degraded result is shown as a
 * warning; a phrase without local-Whisper gets an inline hint. There is no
 * user-facing speed/sensitivity control (removed 2026-07-10 mandate): every
 * wake path always runs at its calibrated-reliable maximum-speed value,
 * identically on every OS.
 *
 * No quick-pick chips: the user must type their own phrase. The onboarding gate
 * (WakeWordOnboardingGate) handles the mandatory first-run flow.
 */
/**
 * The wake-word language dropdown — a native <select> cannot style its option
 * list (the OS renders it), which clashed with the theme. Presentation is the
 * shared BrandedSelect's: a float surface for the list and the interaction
 * ladder for its rows. Nothing is passed in here, because a select that carries
 * a coloured hairline at one call site and not at the others is two controls.
 * Shows a placeholder when the value is not one of the offered concrete
 * languages (e.g. a fresh "auto" config), nudging an explicit choice.
 */
function LanguageDropdown({
  value,
  options,
  placeholder,
  labelFor,
  onChange,
  disabled,
}: {
  value: string;
  options: WakeLanguage[];
  placeholder: string;
  labelFor: (code: WakeLanguage) => string;
  onChange: (code: WakeLanguage) => void;
  disabled?: boolean;
}) {
  return (
    <BrandedSelect
      value={value}
      onValueChange={(code) => onChange(code as WakeLanguage)}
      ariaLabel={placeholder}
      placeholder={placeholder}
      disabled={disabled}
      options={options.map((code) => ({
        value: code,
        label: labelFor(code),
      }))}
    />
  );
}

function WakeWordPanel() {
  const t = useT();
  const { config, loading, error, saveWakeWord, refetch, setWakeLanguage, setWakeActivation } =
    useWakeWord();
  const pushToast = useEventStore((s) => s.pushToast);
  // In-app installer for the local speech pack (faster-whisper) that unlocks any
  // wake phrase. Refetch the wake config on success so the hint clears.
  const { status: installStatus, install } = useLocalSpeechInstall(refetch);

  // The wake-word language pin: a Vosk model is acoustically language-specific,
  // so the model must match the language the user speaks their wake word in.
  // Its OWN backend setting ([trigger.wake_word] language) — independent of the
  // app display language and the general STT recognition language, so switching
  // either never silently moves the wake model. Local state mirrors the config
  // for a snappy dropdown; the backend refetch (via jarvis:wake-word-changed)
  // keeps it truthful.
  const [wakeLang, setWakeLangLocal] = useState("auto");
  const [phrase, setPhrase] = useState("");
  const [engine, setEngine] = useState<string>("auto");
  const [customModelPath, setCustomModelPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<WakeWordSaveResult | null>(null);
  const [selfTest, setSelfTest] = useState<{
    state: "idle" | "running" | "done";
    data: WakeSelfTestResult | null;
  }>({ state: "idle", data: null });
  // The activation master switch (product rule 2026-07-04): on = always-on wake
  // word (needs a local model for the user's word), off = Call shortcut only.
  const [enabled, setEnabled] = useState(false);
  const [togglingActivation, setTogglingActivation] = useState(false);
  // In-app recovery for the degraded-wake-word scenario: downloads the Vosk
  // model that wake_phrase.py's degrade message points at (Settings -> Wake
  // word -> "Download wake model"). Mirrors the local-speech-install status
  // shape above but drives a different, one-shot backend route.
  const [wakeModelDownload, setWakeModelDownload] = useState<{
    state: "idle" | "running" | "done" | "error";
    message: string;
  }>({ state: "idle", message: "" });

  // Hydrate the form once the GET resolves (and whenever the config changes).
  useEffect(() => {
    if (!config) return;
    // Every field defaults. The panel derives `phrase.trim()` on the next
    // render, so a backend that omits the field (older build, degraded route)
    // would throw out of the render path and take the whole view with it —
    // the same version-skew failure the keybind rows hit.
    setPhrase(config.phrase ?? "");
    setEngine(config.engine || "auto");
    setCustomModelPath(config.custom_model_path ?? "");
    // ?? "auto" keeps the dropdown controlled even if an older backend omits it.
    setWakeLangLocal(config.language ?? "auto");
    // ?? false keeps the Switch controlled even if an older backend omits it.
    setEnabled(config.enabled ?? false);
  }, [config]);

  // Pin the wake language: optimistic local update, then persist + live-apply
  // via the backend. On failure, revert and surface the honest error.
  async function onPickWakeLanguage(code: WakeLanguage) {
    const previous = wakeLang;
    setWakeLangLocal(code);
    try {
      await setWakeLanguage(code);
    } catch (e) {
      setWakeLangLocal(previous);
      pushToast("error", (e as Error).message);
    }
  }

  async function onToggleActivation(next: boolean) {
    setTogglingActivation(true);
    setEnabled(next); // optimistic
    try {
      const activation = await setWakeActivation(next);
      pushToast(
        activation.restart_required ? "info" : "success",
        t(
          activation.restart_required
            ? "settings_view.wake_word.restart_required"
            : "settings_view.wake_word.activation_saved",
        ),
      );
    } catch (e) {
      setEnabled(!next); // revert on failure
      pushToast("error", (e as Error).message);
    } finally {
      setTogglingActivation(false);
    }
  }

  const localWhisperAvailable = config?.local_whisper_available ?? true;

  const trimmedPhrase = phrase.trim();
  // No local-Whisper extra + any non-empty phrase → the engine will degrade.
  const showNeedsWhisperHint = !localWhisperAvailable && trimmedPhrase.length > 0;
  const derivedName = deriveAssistantName(phrase);

  async function onSave() {
    if (!trimmedPhrase) return;
    setSaving(true);
    setResult(null);
    // A fresh save gets a fresh download control — stale done/error state from
    // a previous degrade shouldn't bleed into the next result.
    setWakeModelDownload({ state: "idle", message: "" });
    try {
      const res = await saveWakeWord({
        phrase: trimmedPhrase,
        engine,
        custom_model_path:
          engine === "custom_onnx" ? customModelPath.trim() : undefined,
        persist: true,
      });
      setResult(res);
      if (res.degraded) {
        pushToast("warning", res.message);
      } else {
        pushToast("success", t("settings_view.wake_word.saved"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // Recovers the "stt_match only, unreliable" degrade in-app: provisions the
  // per-language Vosk model via POST /api/settings/wake-word/download-model
  // (jarvis/ui/web/settings_routes.py), then re-runs Save so the plan
  // re-resolves to vosk_kws now that the model is present. Never throws — a
  // failed fetch or a still-absent model surfaces an honest retry message.
  async function onDownloadWakeModel() {
    setWakeModelDownload({ state: "running", message: "" });
    try {
      const res = await fetch("/api/settings/wake-word/download-model", {
        method: "POST",
      });
      const data: { ok?: boolean; present?: boolean; message?: string } = await res
        .json()
        .catch(() => ({}));
      const backendMessage = typeof data.message === "string" ? data.message : "";
      if (!res.ok) {
        setWakeModelDownload({
          state: "error",
          message: backendMessage || `HTTP ${res.status}`,
        });
        return;
      }
      if (data.present) {
        setWakeModelDownload({ state: "done", message: backendMessage });
        // Model is present now — re-save with the same phrase/engine so the
        // panel re-resolves to vosk_kws and drops out of the degraded state.
        await onSave();
      } else {
        setWakeModelDownload({
          state: "error",
          message: backendMessage || t("settings_view.wake_word.download_model_error"),
        });
      }
    } catch (e) {
      setWakeModelDownload({ state: "error", message: (e as Error).message });
    }
  }

  // Readiness check for the "Test wake word" button — asks the backend whether
  // the configured word will actually fire (right-language model armed, word in
  // vocabulary, mic delivering signal) without a second mic stream. Never throws.
  async function onSelfTest() {
    setSelfTest({ state: "running", data: null });
    try {
      const res = await fetch("/api/settings/wake-word/self-test", {
        method: "POST",
      });
      const data = (await res.json().catch(() => null)) as WakeSelfTestResult | null;
      setSelfTest({ state: "done", data });
    } catch (e) {
      setSelfTest({
        state: "done",
        data: {
          ok: false,
          phrase,
          engine,
          language: wakeLang,
          wake_available: false,
          phrase_in_vocab: null,
          mic_ok: false,
          message: (e as Error).message,
          hint: "",
        },
      });
    }
  }

  return (
    <div className="rounded-lg border border-border bg-card p-block">
      <div className="flex items-start gap-3">
        <Mic className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <h4 className="text-title font-semibold text-foreground-strong">
            {t("settings_view.wake_word.title")}
          </h4>
          <p className="mt-1 text-meta text-muted-foreground">
            {t("settings_view.wake_word.description")}
          </p>

          <div className="mt-block flex items-center justify-between gap-4">
            <span className="text-body text-foreground">
              {t("settings_view.wake_word.activation_title")}
            </span>
            <Switch
              checked={enabled}
              disabled={loading || togglingActivation}
              aria-label={t("settings_view.wake_word.activation_title")}
              onCheckedChange={onToggleActivation}
            />
          </div>
          <p className="mt-1 text-meta text-muted-foreground">
            {t("settings_view.wake_word.activation_hint")}
          </p>

          {error && (
            <p className="mt-stack text-meta text-destructive">{error}</p>
          )}

          {/* Phrase input — free text, no quick-picks */}
          <label className="mt-block block text-meta text-muted-foreground">
            {t("settings_view.wake_word.phrase_label")}
          </label>
          <input
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            maxLength={64}
            placeholder={t("settings_view.wake_word.phrase_placeholder")}
            disabled={loading}
            className={settingsInputCls + " mt-1.5 disabled:opacity-50"}
          />

          {derivedName ? (
            <p className="mt-1.5 text-meta text-muted-foreground">
              {t("settings_view.wake_word.derived_name").replace("{0}", derivedName)}
            </p>
          ) : null}

          {/* LANGUAGE — deliberately prominent + over-explained. Picking the
              wrong language here is the #1 cause of a silently dead wake word,
              and the trap is unintuitive: it is about the language the user
              SPEAKS (their accent/pronunciation), NOT the origin of the word
              ("Ruben" is heard by the German model because the user speaks it
              in German, not because the name is German). Bound to the wake
              word's OWN language pin ([trigger.wake_word] language) — the app
              display language and the STT recognition language stay untouched,
              and neither can move this choice. */}
          {/* A lift surface inside the card — the step up the ladder that says
              "this one matters", drawn in fill rather than in a tinted rim.
              It used to be --primary at 5 % behind a --primary hairline with a
              --primary heading: white ink on a white wash, which made a piece
              of guidance the loudest mark on the page and read as a status it
              is not. Emphasis is the surface and the ink ceiling now. */}
          <div className="mt-block rounded-md bg-secondary p-4">
            <div className="flex items-center gap-2">
              <Languages className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="text-title font-semibold text-foreground-strong">
                {t("settings_view.wake_word.language_label")}
              </span>
            </div>
            <p className="mt-1.5 text-body text-foreground">
              {t("settings_view.wake_word.language_callout_title")}
            </p>
            <div className="mt-stack">
              <LanguageDropdown
                value={wakeLang}
                options={WAKE_LANGUAGES}
                placeholder={t("settings_view.wake_word.language_placeholder")}
                labelFor={(code) => t(`languages_view.options.${code}.label`)}
                onChange={(code) => void onPickWakeLanguage(code)}
                disabled={loading}
              />
            </div>
            <p className="mt-stack text-meta text-muted-foreground">
              {t("settings_view.wake_word.language_hint")}
            </p>
          </div>

          {/* Engine select */}
          <label className="mt-block block text-meta text-muted-foreground">
            {t("settings_view.wake_word.engine_label")}
          </label>
          <BrandedSelect
            value={engine}
            onValueChange={setEngine}
            ariaLabel={t("settings_view.wake_word.engine_label")}
            disabled={loading}
            className="mt-1"
            options={WAKE_ENGINES.map((wakeEngine) => ({
              value: wakeEngine,
              label: t(WAKE_ENGINE_I18N_KEY[wakeEngine]),
            }))}
          />

          {/* Custom ONNX model path */}
          {engine === "custom_onnx" && (
            <>
              <label className="mt-block block text-meta text-muted-foreground">
                {t("settings_view.wake_word.custom_model_path_label")}
              </label>
              <input
                value={customModelPath}
                onChange={(e) => setCustomModelPath(e.target.value)}
                placeholder="C:\\Users\\...\\my_wakeword.onnx"
                disabled={loading}
                className={settingsInputCls + " mt-1.5 font-mono text-meta disabled:opacity-50"}
              />
            </>
          )}

          {/* Any-phrase enablement: install the local speech pack in-app so
              an arbitrary wake word works, instead of silently degrading. */}
          {showNeedsWhisperHint && (
            <div className="mt-stack rounded-md bg-secondary p-4 text-body text-foreground">
              <p className="text-warning">
                {t("settings_view.wake_word.needs_whisper_hint")}
              </p>

              {installStatus.state === "idle" && (
                <Button
                  size="sm"
                  className="mt-stack"
                  onClick={() => void install()}
                >
                  {t("settings_view.wake_word.enable_local_button")}
                </Button>
              )}

              {installStatus.state === "running" && (
                <p className="mt-stack flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("settings_view.wake_word.enable_local_installing")}
                </p>
              )}

              {installStatus.state === "error" && (
                <div className="mt-stack text-destructive">
                  <p>{t("settings_view.wake_word.enable_local_error")}</p>
                  {installStatus.message && (
                    <p className="mt-1 font-mono text-micro text-muted-foreground">
                      {installStatus.message}
                    </p>
                  )}
                  <Button
                    size="sm"
                    className="mt-stack"
                    onClick={() => void install()}
                  >
                    {t("settings_view.wake_word.enable_local_retry")}
                  </Button>
                </div>
              )}

              {installStatus.state === "done" && (
                <p className="mt-stack text-success">
                  {t("settings_view.wake_word.enable_local_done")}
                </p>
              )}
            </div>
          )}

          {/* Save + Test buttons */}
          <div className="mt-block flex items-center gap-3">
            <Button
              size="sm"
              onClick={onSave}
              disabled={saving || loading || !trimmedPhrase}
            >
              {saving
                ? t("settings_view.saving")
                : t("settings_view.wake_word.save")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void onSelfTest()}
              disabled={selfTest.state === "running" || loading || !trimmedPhrase}
            >
              {selfTest.state === "running" ? (
                <span className="flex items-center gap-2">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("settings_view.wake_word.self_test_running")}
                </span>
              ) : (
                t("settings_view.wake_word.self_test_button")
              )}
            </Button>
          </div>

          {/* Self-test result — honest readiness verdict (engine + language +
              vocabulary + mic), the fast way to see WHY a word won't wake. */}
          {selfTest.state === "done" && selfTest.data && (
            /* One surface for both verdicts, the verdict carried by the status
               ink on the sentence. A pass used to be a --primary wash and a
               fail a --foreground wash: two brightness washes standing in for
               a meaning that colour states directly, on a ground where neither
               was legible as a state at all. */
            <div className="mt-stack rounded-md bg-secondary p-4 text-body">
              <p
                className={
                  selfTest.data.ok ? "text-success" : "text-destructive"
                }
              >
                {selfTest.data.message}
              </p>
              {selfTest.data.hint && (
                <p className="mt-1 text-muted-foreground">{selfTest.data.hint}</p>
              )}
              <p className="mt-1 font-mono text-meta text-muted-foreground">
                engine: {selfTest.data.engine} · language: {selfTest.data.language}
                {selfTest.data.phrase_in_vocab === false ? " · not in vocabulary" : ""}
                {selfTest.data.mic_ok ? "" : " · mic quiet"}
              </p>
            </div>
          )}

          {/* Save result */}
          {result && (
            <div className="mt-stack rounded-md bg-secondary p-4 text-body">
              <p className={result.degraded ? "text-warning" : "text-success"}>
                {result.degraded
                  ? t("settings_view.wake_word.degraded_warning")
                  : result.message}
              </p>
              <p className="mt-1 font-mono text-meta text-muted-foreground">
                engine: {result.resolved_engine}
              </p>
              {result.degraded && result.message && (
                <p className="mt-1 text-muted-foreground">{result.message}</p>
              )}
              {result.restart_required && (
                <p className="mt-1 text-muted-foreground">
                  {t("settings_view.wake_word.restart_required")}
                </p>
              )}

              {/* In-app recovery for the degraded ("stt_match only") scenario:
                  wires the backend's own suggestion (Settings -> Wake word ->
                  "Download wake model") to a real button instead of a
                  CLI/API-only route. */}
              {result.degraded && (
                <div className="mt-stack">
                  {wakeModelDownload.state === "idle" && (
                    <Button
                      size="sm"
                      onClick={() => void onDownloadWakeModel()}
                    >
                      {t("settings_view.wake_word.download_model_button")}
                    </Button>
                  )}

                  {wakeModelDownload.state === "running" && (
                    <p className="flex items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      {t("settings_view.wake_word.download_model_downloading")}
                    </p>
                  )}

                  {wakeModelDownload.state === "done" && (
                    <p className="text-success">
                      {wakeModelDownload.message ||
                        t("settings_view.wake_word.download_model_done")}
                    </p>
                  )}

                  {wakeModelDownload.state === "error" && (
                    <div className="text-destructive">
                      <p>
                        {wakeModelDownload.message ||
                          t("settings_view.wake_word.download_model_error")}
                      </p>
                      <Button
                        size="sm"
                        className="mt-stack"
                        onClick={() => void onDownloadWakeModel()}
                      >
                        {t("settings_view.wake_word.download_model_retry")}
                      </Button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const _KEYBIND_ROWS: { action: KeybindAction; labelKey: string }[] = [
  { action: "call", labelKey: "settings_view.keybinds.call_label" },
  { action: "hangup", labelKey: "settings_view.keybinds.hangup_label" },
];

/**
 * Editable Call and Hangup keybinds, one row each — the two keys that start
 * and end a conversation. The user clicks Record and presses a combination, or
 * resets to default, then saves. The backend validator is the authority — an
 * unsafe combo or a collision with another action is rejected with a reason
 * shown as a toast. A successful save surfaces a restart-required hint.
 *
 * NO dictation row lives here. Dictation is a different act — it never reaches
 * the brain, it types into whatever window is in front, and it now has three
 * shortcuts of its own (hold, hands-free, paste again). Those belong together
 * on ONE surface, and that surface is the voice section's Shortcuts tab. This
 * panel is deliberately NOT synced with it: the two answer different questions,
 * and a row duplicated across both would let a user change the same key in two
 * places and see two different truths.
 *
 * The row component itself is shared, so the recorder, the live validation and
 * the collision check behave identically in both places — the collision check
 * in particular still spans EVERY action, dictation included, because the
 * backend keeps serving the whole set. Fewer rows here, never less data.
 */
export function KeybindsPanel() {
  const t = useT();
  const { config, loading, error, saveKeybind } = useKeybinds();

  return (
    <div className="rounded-lg border border-border bg-card p-block">
      <div className="flex items-start gap-3">
        <Keyboard className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <h4 className="text-title font-semibold text-foreground-strong">
            {t("settings_view.keybinds.title")}
          </h4>
          <p className="mt-1 text-meta text-muted-foreground">
            {t("settings_view.keybinds.description")}
          </p>
          {error && (
            <p className="mt-stack text-meta text-destructive">{error}</p>
          )}
          <div className="mt-block space-y-stack">
            {_KEYBIND_ROWS.map((row) => (
              <KeybindRow
                key={row.action}
                action={row.action}
                label={t(row.labelKey)}
                config={config}
                loading={loading}
                onSave={saveKeybind}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
