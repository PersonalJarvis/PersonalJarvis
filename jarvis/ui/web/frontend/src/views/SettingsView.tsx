import { useEffect, useState } from "react";
import {
  Settings,
  Mic,
  Keyboard,
  Loader2,
  Languages,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { OverlayTaskbarGroup } from "@/views/settings/OverlayTaskbarGroup";
import { LanguagesGroup } from "@/views/settings/LanguagesGroup";
import { AppSettingsGroup } from "@/views/settings/AppSettingsGroup";
import { PermissionsPanel } from "@/views/settings/PermissionsPanel";
import { RealtimeVoiceGroup } from "@/views/settings/RealtimeVoiceGroup";
import { SilenceWindowGroup } from "@/views/settings/SilenceWindowGroup";
import { VolumeGroup } from "@/views/settings/VolumeGroup";
import { AudioDevicesGroup } from "@/views/settings/AudioDevicesGroup";
import { SystemPromptGroup } from "@/views/settings/SystemPromptGroup";
import { SettingsGroupBoundary } from "@/views/settings/SettingsGroupBoundary";
import { ScreenContextGroup } from "@/views/settings/ScreenContextGroup";
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

  const rows: SettingRow[] = [
    {
      icon: Settings,
      title: t("settings_view.rows.toasts_title"),
      description: t("settings_view.rows.toasts_description"),
      control: <Switch defaultChecked />,
    },
  ];

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Settings className="h-4 w-4 text-primary" />}
        title={t("settings_view.title")}
        subtitle={t("settings_view.subtitle")}
      />
      {/* Each group is fault-isolated. The panels are independent, each backed
          by its own route, so one of them throwing must cost the user that one
          panel — not the ability to change any setting at all. */}
      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <SettingsGroupBoundary group="languages">
          <LanguagesGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="app">
          <AppSettingsGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="permissions">
          <PermissionsPanel />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="screen-context">
          <ScreenContextGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="realtime-voice">
          <RealtimeVoiceGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="system-prompt">
          <SystemPromptGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="wake-word">
          <WakeWordPanel />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="silence-window">
          <SilenceWindowGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="volume">
          <VolumeGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="audio-devices">
          <AudioDevicesGroup />
        </SettingsGroupBoundary>
        <SettingsGroupBoundary group="keybinds">
          <KeybindsPanel />
        </SettingsGroupBoundary>

        <ul className="mt-2 space-y-2">
          {rows.map((r) => (
            <SettingRow key={r.title} row={r} />
          ))}
        </ul>

        <SettingsGroupBoundary group="overlay-taskbar">
          <OverlayTaskbarGroup />
        </SettingsGroupBoundary>
      </div>
    </div>
  );
}

function SettingRow({ row }: { row: SettingRow }) {
  const Icon = row.icon;
  return (
    <li className="card-outline flex items-center gap-4 p-4">
      <Icon className="h-4 w-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{row.title}</div>
        <p className="mt-0.5 text-xs text-muted-foreground">{row.description}</p>
      </div>
      {row.value && (
        <span className="font-mono text-xs text-muted-foreground">{row.value}</span>
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
 * Branded language dropdown (GTC black/yellow) — a native <select> cannot style
 * its option list (the OS renders it), which clashed with the theme. This is a
 * self-contained button + positioned listbox: dark card surface, primary-yellow
 * accent on the active/hover row, closes on outside-click and Escape. Shows a
 * placeholder when the value is not one of the offered concrete languages (e.g.
 * a fresh "auto" config), nudging the user to make an explicit choice.
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
      className="border-primary/40"
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
    <div className="rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Mic className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-sm font-semibold">
            {t("settings_view.wake_word.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.wake_word.description")}
          </p>

          <div className="mt-3 flex items-center justify-between gap-4">
            <span className="text-xs font-medium">
              {t("settings_view.wake_word.activation_title")}
            </span>
            <Switch
              checked={enabled}
              disabled={loading || togglingActivation}
              onCheckedChange={onToggleActivation}
            />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.wake_word.activation_hint")}
          </p>

          {error && (
            <p className="mt-3 text-xs text-destructive">{error}</p>
          )}

          {/* Phrase input — free text, no quick-picks */}
          <label className="mt-4 block text-xs font-medium text-muted-foreground">
            {t("settings_view.wake_word.phrase_label")}
          </label>
          <input
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            maxLength={64}
            placeholder={t("settings_view.wake_word.phrase_placeholder")}
            disabled={loading}
            className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          />

          {derivedName ? (
            <p className="mt-1.5 text-xs text-muted-foreground">
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
          <div className="mt-4 rounded-md border border-primary/50 bg-primary/5 p-3">
            <div className="flex items-center gap-2">
              <Languages className="h-4 w-4 shrink-0 text-primary" />
              <span className="text-xs font-semibold">
                {t("settings_view.wake_word.language_label")}
              </span>
            </div>
            <p className="mt-1.5 text-xs font-semibold text-primary">
              {t("settings_view.wake_word.language_callout_title")}
            </p>
            <div className="mt-2">
              <LanguageDropdown
                value={wakeLang}
                options={WAKE_LANGUAGES}
                placeholder={t("settings_view.wake_word.language_placeholder")}
                labelFor={(code) => t(`languages_view.options.${code}.label`)}
                onChange={(code) => void onPickWakeLanguage(code)}
                disabled={loading}
              />
            </div>
            <p className="mt-2 text-[11px] leading-snug text-muted-foreground">
              {t("settings_view.wake_word.language_hint")}
            </p>
          </div>

          {/* Engine select */}
          <label className="mt-4 block text-xs font-medium text-muted-foreground">
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
              <label className="mt-4 block text-xs font-medium text-muted-foreground">
                {t("settings_view.wake_word.custom_model_path_label")}
              </label>
              <input
                value={customModelPath}
                onChange={(e) => setCustomModelPath(e.target.value)}
                placeholder="C:\\Users\\...\\my_wakeword.onnx"
                disabled={loading}
                className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
              />
            </>
          )}

          {/* Any-phrase enablement: install the local speech pack in-app so
              an arbitrary wake word works, instead of silently degrading. */}
          {showNeedsWhisperHint && (
            <div className="mt-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-500">
              <p>{t("settings_view.wake_word.needs_whisper_hint")}</p>

              {installStatus.state === "idle" && (
                <Button
                  size="sm"
                  className="mt-2"
                  onClick={() => void install()}
                >
                  {t("settings_view.wake_word.enable_local_button")}
                </Button>
              )}

              {installStatus.state === "running" && (
                <p className="mt-2 flex items-center gap-2 text-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("settings_view.wake_word.enable_local_installing")}
                </p>
              )}

              {installStatus.state === "error" && (
                <div className="mt-2 text-destructive">
                  <p>{t("settings_view.wake_word.enable_local_error")}</p>
                  {installStatus.message && (
                    <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                      {installStatus.message}
                    </p>
                  )}
                  <Button
                    size="sm"
                    className="mt-2"
                    onClick={() => void install()}
                  >
                    {t("settings_view.wake_word.enable_local_retry")}
                  </Button>
                </div>
              )}

              {installStatus.state === "done" && (
                <div className="mt-2 text-foreground">
                  <p>{t("settings_view.wake_word.enable_local_done")}</p>
                </div>
              )}
            </div>
          )}

          {/* Save + Test buttons */}
          <div className="mt-4 flex items-center gap-3">
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
            <div
              className={`mt-3 rounded-md border p-3 text-xs ${
                selfTest.data.ok
                  ? "border-primary/40 bg-primary/10 text-foreground"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-500"
              }`}
            >
              <p>{selfTest.data.message}</p>
              {selfTest.data.hint && (
                <p className="mt-1 text-muted-foreground">{selfTest.data.hint}</p>
              )}
              <p className="mt-1 font-mono text-muted-foreground">
                engine: {selfTest.data.engine} · language: {selfTest.data.language}
                {selfTest.data.phrase_in_vocab === false ? " · not in vocabulary" : ""}
                {selfTest.data.mic_ok ? "" : " · mic quiet"}
              </p>
            </div>
          )}

          {/* Save result */}
          {result && (
            <div
              className={`mt-3 rounded-md border p-3 text-xs ${
                result.degraded
                  ? "border-amber-500/40 bg-amber-500/10 text-amber-500"
                  : "border-primary/40 bg-primary/10 text-foreground"
              }`}
            >
              <p>
                {result.degraded
                  ? t("settings_view.wake_word.degraded_warning")
                  : result.message}
              </p>
              <p className="mt-1 font-mono text-muted-foreground">
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
                <div className="mt-2">
                  {wakeModelDownload.state === "idle" && (
                    <Button
                      size="sm"
                      onClick={() => void onDownloadWakeModel()}
                    >
                      {t("settings_view.wake_word.download_model_button")}
                    </Button>
                  )}

                  {wakeModelDownload.state === "running" && (
                    <p className="flex items-center gap-2 text-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      {t("settings_view.wake_word.download_model_downloading")}
                    </p>
                  )}

                  {wakeModelDownload.state === "done" && (
                    <p className="text-foreground">
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
                        className="mt-2"
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
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Keyboard className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-sm font-semibold">
            {t("settings_view.keybinds.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.keybinds.description")}
          </p>
          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
          <div className="mt-4 space-y-3">
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
