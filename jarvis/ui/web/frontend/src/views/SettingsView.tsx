import { useEffect, useState } from "react";
import {
  Settings,
  Mic,
  Keyboard,
  Shield,
  Folder,
  Power,
  Terminal,
  Bot,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { BackendConnectionSection } from "@/components/board/BackendConnectionSection";
import { setCodexBinaryPath, useProviders } from "@/hooks/useProviders";
import { useWakeWord, type WakeWordSaveResult } from "@/hooks/useWakeWord";
import { useHotkey, eventToCombo } from "@/hooks/useHotkey";
import { useAssistantName } from "@/hooks/useAssistantName";
import { useAutostart } from "@/hooks/useAutostart";
import { WAKE_ENGINES, WAKE_ENGINE_I18N_KEY } from "@/constants/wakeEngines";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

interface SettingRow {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  control?: React.ReactNode;
  value?: string;
}

export function SettingsView() {
  const t = useT();
  const { providers, refetch } = useProviders();
  const codex = providers.find((p) => p.id === "codex");
  const [codexPath, setCodexPath] = useState("");
  const [savingCodexPath, setSavingCodexPath] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  useEffect(() => {
    setCodexPath(codex?.codex_status?.binaryPath ?? "");
  }, [codex?.codex_status?.binaryPath]);

  async function saveCodexPath() {
    setSavingCodexPath(true);
    try {
      await setCodexBinaryPath(codexPath.trim());
      pushToast("success", "Codex-Pfad gespeichert");
      refetch();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSavingCodexPath(false);
    }
  }

  const rows: SettingRow[] = [
    {
      icon: Shield,
      title: t("settings_view.rows.privacy_title"),
      description: t("settings_view.rows.privacy_description"),
      value: t("settings_view.rows.privacy_value"),
    },
    {
      icon: Folder,
      title: t("settings_view.rows.scope_title"),
      description: t("settings_view.rows.scope_description"),
      value: t("settings_view.rows.scope_value"),
    },
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
      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <AssistantNamePanel />
        <AutostartPanel />
        <WakeWordPanel />
        <HotkeyPanel />

        <ul className="mt-2 space-y-2">
          {rows.map((r) => (
            <SettingRow key={r.title} row={r} />
          ))}
        </ul>

        <div className="mt-8">
          <BackendConnectionSection />
        </div>

        <div className="mt-6 rounded-lg border border-border bg-card/60 p-4">
          <div className="flex items-start gap-3">
            <Terminal className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <h4 className="font-display text-sm font-semibold">{t("settings_view.codex_title")}</h4>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("settings_view.codex_description")}
              </p>
              <div className="mt-3 flex gap-2">
                <input
                  value={codexPath}
                  onChange={(e) => setCodexPath(e.target.value)}
                  placeholder="C:\\Users\\...\\codex.cmd"
                  className="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <Button size="sm" onClick={saveCodexPath} disabled={savingCodexPath}>
                  {savingCodexPath ? t("settings_view.saving") : t("settings_view.save")}
                </Button>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-6 rounded-lg border border-border bg-card/60 p-4">
          <h4 className="font-display text-sm font-semibold">{t("settings_view.safety_title")}</h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.safety_description")}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {["browser-use *", "pytest *", "git status", "ls *", "pip list"].map(
              (pattern) => (
                <code
                  key={pattern}
                  className="rounded border border-border bg-background px-2 py-1 text-xs font-mono"
                >
                  {pattern}
                </code>
              ),
            )}
          </div>
        </div>
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
 * Editable wake-word panel: phrase input + quick-pick chips, engine select,
 * sensitivity slider, optional custom-model path, and a Save button that
 * surfaces the backend's resolved engine, message, and a restart hint. A
 * degraded result is shown as a warning; an arbitrary phrase without the
 * local-Whisper extra gets an inline degrade hint.
 */
function WakeWordPanel() {
  const t = useT();
  const { config, loading, error, saveWakeWord } = useWakeWord();
  const pushToast = useEventStore((s) => s.pushToast);

  const [phrase, setPhrase] = useState("");
  const [engine, setEngine] = useState<string>("auto");
  const [sensitivity, setSensitivity] = useState(0.5);
  const [customModelPath, setCustomModelPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<WakeWordSaveResult | null>(null);

  // Hydrate the form once the GET resolves (and whenever the config changes).
  useEffect(() => {
    if (!config) return;
    setPhrase(config.phrase);
    setEngine(config.engine || "auto");
    setSensitivity(config.sensitivity);
    setCustomModelPath(config.custom_model_path ?? "");
  }, [config]);

  const instantPhrases = config?.instant_phrases ?? [];
  const localWhisperAvailable = config?.local_whisper_available ?? true;

  const trimmedPhrase = phrase.trim();
  const isInstantPhrase = instantPhrases.some(
    (p) => p.toLowerCase() === trimmedPhrase.toLowerCase(),
  );
  // Arbitrary phrase + no local-Whisper extra → the engine will degrade.
  const showNeedsWhisperHint =
    !localWhisperAvailable && trimmedPhrase.length > 0 && !isInstantPhrase;

  async function onSave() {
    if (!trimmedPhrase) return;
    setSaving(true);
    setResult(null);
    try {
      const res = await saveWakeWord({
        phrase: trimmedPhrase,
        engine,
        sensitivity,
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

          {error && (
            <p className="mt-3 text-xs text-destructive">{error}</p>
          )}

          {/* Phrase input */}
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

          {/* Quick-pick chips for instant phrases */}
          {instantPhrases.length > 0 && (
            <div className="mt-3">
              <span className="text-xs text-muted-foreground">
                {t("settings_view.wake_word.instant_phrases_label")}
              </span>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {instantPhrases.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPhrase(p)}
                    className={`rounded border px-2 py-1 text-xs transition-colors ${
                      trimmedPhrase.toLowerCase() === p.toLowerCase()
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground hover:border-primary/60 hover:text-foreground"
                    }`}
                  >
                    {p}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Engine select */}
          <label className="mt-4 block text-xs font-medium text-muted-foreground">
            {t("settings_view.wake_word.engine_label")}
          </label>
          <select
            value={engine}
            onChange={(e) => setEngine(e.target.value)}
            disabled={loading}
            className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          >
            {WAKE_ENGINES.map((eng) => (
              <option key={eng} value={eng}>
                {t(WAKE_ENGINE_I18N_KEY[eng])}
              </option>
            ))}
          </select>

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

          {/* Sensitivity slider */}
          <label className="mt-4 flex items-center justify-between text-xs font-medium text-muted-foreground">
            <span>{t("settings_view.wake_word.sensitivity_label")}</span>
            <span className="font-mono text-primary">
              {sensitivity.toFixed(2)}
            </span>
          </label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={sensitivity}
            onChange={(e) => setSensitivity(Number(e.target.value))}
            disabled={loading}
            className="mt-1.5 w-full accent-primary disabled:opacity-50"
          />

          {/* Inline degrade hint */}
          {showNeedsWhisperHint && (
            <p className="mt-3 text-xs text-amber-500">
              {t("settings_view.wake_word.needs_whisper_hint")}
            </p>
          )}

          {/* Save button */}
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
          </div>

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
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Login-autostart toggle. Flipping it installs/removes the OS autostart entry
 * live (Windows .lnk / macOS LaunchAgent / Linux XDG .desktop) and persists
 * [autostart].enabled. On a headless host the switch is disabled with an honest
 * caption — the toggle cannot create a login entry where there is no GUI seat.
 */
function AutostartPanel() {
  const t = useT();
  const { config, loading, error, setEnabled } = useAutostart();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);

  const supported = config?.supported ?? true;
  const enabled = config?.enabled ?? false;

  async function onToggle(next: boolean) {
    setSaving(true);
    try {
      const res = await setEnabled(next);
      if (next && res.supported && res.applied_live) {
        pushToast("success", t("settings_view.autostart.enabled_toast"));
      } else if (next && !res.supported) {
        pushToast("warning", res.detail || t("settings_view.autostart.unsupported"));
      } else if (!next) {
        pushToast("success", t("settings_view.autostart.disabled_toast"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mb-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Power className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.autostart.title")}
            </h4>
            <Switch
              checked={enabled}
              disabled={loading || saving || !supported}
              onCheckedChange={onToggle}
            />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.autostart.description")}
          </p>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          {!supported && !loading && (
            <p className="mt-3 text-xs text-amber-500">
              {config?.detail || t("settings_view.autostart.unsupported")}
            </p>
          )}

          {supported && config?.entry_path && (
            <p className="mt-2 break-all font-mono text-[11px] text-muted-foreground">
              {config.entry_path}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Editable assistant-name panel. The assistant calls itself this name in its
 * replies. Empty = derive it from the wake phrase (so "Micron" wake → "Micron"
 * identity). A successful save surfaces a restart hint (the system prompt is
 * assembled once per BrainManager).
 */
function AssistantNamePanel() {
  const t = useT();
  const { config, loading, error, saveName } = useAssistantName();
  const pushToast = useEventStore((s) => s.pushToast);

  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (config) setName(config.name);
  }, [config]);

  async function onSave() {
    setSaving(true);
    setSaved(false);
    try {
      const res = await saveName(name.trim());
      setSaved(res.restart_required);
      pushToast("success", t("settings_view.assistant_name.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const resolved = config?.resolved ?? "Jarvis";
  const dirty = !!config && name.trim() !== config.name;

  return (
    <div className="rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-sm font-semibold">
            {t("settings_view.assistant_name.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.assistant_name.description")}
          </p>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          <label className="mt-4 block text-xs font-medium text-muted-foreground">
            {t("settings_view.assistant_name.label")}
          </label>
          <input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSaved(false);
            }}
            maxLength={40}
            placeholder={resolved}
            disabled={loading}
            className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          />
          <p className="mt-1.5 text-xs text-muted-foreground">
            {name.trim()
              ? t("settings_view.assistant_name.current").replace("{0}", resolved)
              : t("settings_view.assistant_name.auto_hint").replace("{0}", resolved)}
          </p>

          <div className="mt-4 flex items-center gap-3">
            <Button size="sm" onClick={onSave} disabled={saving || loading || !dirty}>
              {saving
                ? t("settings_view.saving")
                : t("settings_view.assistant_name.save")}
            </Button>
          </div>

          {saved && (
            <div className="mt-3 rounded-md border border-primary/40 bg-primary/10 p-3 text-xs text-foreground">
              <p className="text-muted-foreground">
                {t("settings_view.assistant_name.restart_required")}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Pretty-print a combo string ("ctrl+right_alt+j" → "Ctrl + Right-Alt + J"). */
function formatCombo(combo: string): string {
  const labels: Record<string, string> = {
    ctrl: "Ctrl",
    control: "Ctrl",
    right_ctrl: "Right-Ctrl",
    alt: "Alt",
    left_alt: "Left-Alt",
    right_alt: "Right-Alt",
    altgr: "AltGr",
    shift: "Shift",
    win: "Win",
    space: "Space",
  };
  return combo
    .split("+")
    .map((p) => labels[p] ?? (p.length === 1 ? p.toUpperCase() : p.toUpperCase()))
    .join(" + ");
}

/**
 * Editable push-to-talk hotkey panel. The user clicks "record", presses a key
 * combination (captured live via eventToCombo), or picks a safe suggestion
 * chip, then saves. The backend validator is the authority — an unsafe combo
 * is rejected with a reason shown inline. A successful save surfaces a
 * restart-required hint (bindings are armed once at pipeline start).
 */
function HotkeyPanel() {
  const t = useT();
  const { config, loading, error, saveHotkey } = useHotkey();
  const pushToast = useEventStore((s) => s.pushToast);

  const [combo, setCombo] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (config) setCombo(config.hotkey);
  }, [config]);

  function onCaptureKeyDown(e: React.KeyboardEvent) {
    if (!capturing) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") {
      setCapturing(false);
      return;
    }
    const next = eventToCombo(e);
    if (next) {
      setCombo(next);
      setCapturing(false);
      setSaved(false);
    }
  }

  async function onSave() {
    const trimmed = combo.trim().toLowerCase();
    if (!trimmed) return;
    setSaving(true);
    setSaved(false);
    try {
      const res = await saveHotkey(trimmed);
      setSaved(res.restart_required);
      pushToast("success", t("settings_view.hotkey.saved"));
    } catch (e) {
      // Backend rejected the combo (unsafe / unusable) — show its reason.
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const suggestions = config?.suggestions ?? [];
  const dirty = !!config && combo.trim().toLowerCase() !== config.hotkey;

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Keyboard className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-sm font-semibold">
            {t("settings_view.hotkey.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.hotkey.description")}
          </p>

          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          {/* Capture field */}
          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              onClick={() => setCapturing(true)}
              onKeyDown={onCaptureKeyDown}
              onBlur={() => setCapturing(false)}
              disabled={loading}
              className={`flex-1 rounded-md border px-3 py-2 text-left font-mono text-sm transition-colors focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 ${
                capturing
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-input bg-background"
              }`}
            >
              {capturing
                ? t("settings_view.hotkey.recording")
                : combo
                  ? formatCombo(combo)
                  : "—"}
            </button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCapturing(true)}
              disabled={loading}
            >
              {t("settings_view.hotkey.record")}
            </Button>
          </div>

          {/* Quick-pick suggestions */}
          {suggestions.length > 0 && (
            <div className="mt-3">
              <span className="text-xs text-muted-foreground">
                {t("settings_view.hotkey.suggestions_label")}
              </span>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {suggestions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setCombo(s);
                      setSaved(false);
                    }}
                    className={`rounded border px-2 py-1 font-mono text-xs transition-colors ${
                      combo.trim().toLowerCase() === s
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border bg-background text-muted-foreground hover:border-primary/60 hover:text-foreground"
                    }`}
                  >
                    {formatCombo(s)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Save */}
          <div className="mt-4 flex items-center gap-3">
            <Button size="sm" onClick={onSave} disabled={saving || loading || !dirty}>
              {saving ? t("settings_view.saving") : t("settings_view.hotkey.save")}
            </Button>
          </div>

          {saved && (
            <div className="mt-3 rounded-md border border-primary/40 bg-primary/10 p-3 text-xs text-foreground">
              <p className="text-muted-foreground">
                {t("settings_view.hotkey.restart_required")}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
