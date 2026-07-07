import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  Settings,
  Mic,
  Keyboard,
  Loader2,
  X,
} from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { OverlayTaskbarGroup } from "@/views/settings/OverlayTaskbarGroup";
import { LanguagesGroup } from "@/views/settings/LanguagesGroup";
import { AppSettingsGroup } from "@/views/settings/AppSettingsGroup";
import { SilenceWindowGroup } from "@/views/settings/SilenceWindowGroup";
import { VolumeGroup } from "@/views/settings/VolumeGroup";
import { AudioDevicesGroup } from "@/views/settings/AudioDevicesGroup";
import { SystemPromptGroup } from "@/views/settings/SystemPromptGroup";
import {
  useWakeWord,
  useLocalSpeechInstall,
  type WakeWordSaveResult,
} from "@/hooks/useWakeWord";
import {
  useKeybinds,
  chordToCombo,
  codeToKeyToken,
  composeCombo,
  comboTokens,
  validateCombo,
  type ComboValidation,
  type KeybindAction,
  type KeybindsConfig,
  type KeybindSaveResult,
} from "@/hooks/useHotkey";
import { KeyboardMap } from "@/views/settings/KeyboardMap";
import { detectKeyboardPlatform } from "@/views/settings/keyboardLayout";
import { deriveAssistantName } from "@/lib/deriveAssistantName";
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
      <div className="flex-1 overflow-y-auto scrollbar-jarvis p-6">
        <LanguagesGroup />
        <AppSettingsGroup />
        <SystemPromptGroup />
        <WakeWordPanel />
        <SilenceWindowGroup />
        <VolumeGroup />
        <AudioDevicesGroup />
        <KeybindsPanel />

        <ul className="mt-2 space-y-2">
          {rows.map((r) => (
            <SettingRow key={r.title} row={r} />
          ))}
        </ul>

        <OverlayTaskbarGroup />
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
 * Editable wake-word panel: free-text phrase input, engine select, sensitivity
 * slider, optional custom-model path, and a Save button that surfaces the
 * backend's resolved engine, message, and a restart hint. A degraded result is
 * shown as a warning; a phrase without local-Whisper gets an inline hint.
 *
 * No quick-pick chips: the user must type their own phrase. The onboarding gate
 * (WakeWordOnboardingGate) handles the mandatory first-run flow.
 */
function WakeWordPanel() {
  const t = useT();
  const { config, loading, error, saveWakeWord, refetch, setWakeActivation } = useWakeWord();
  const pushToast = useEventStore((s) => s.pushToast);
  // In-app installer for the local speech pack (faster-whisper) that unlocks any
  // wake phrase. Refetch the wake config on success so the hint clears.
  const { status: installStatus, install } = useLocalSpeechInstall(refetch);

  const [phrase, setPhrase] = useState("");
  const [engine, setEngine] = useState<string>("auto");
  const [sensitivity, setSensitivity] = useState(0.5);
  const [customModelPath, setCustomModelPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<WakeWordSaveResult | null>(null);
  // The activation master switch (product rule 2026-07-04): on = always-on wake
  // word (needs a local model for the user's word), off = hotkey / push-to-talk.
  const [enabled, setEnabled] = useState(false);
  const [togglingActivation, setTogglingActivation] = useState(false);

  // Hydrate the form once the GET resolves (and whenever the config changes).
  useEffect(() => {
    if (!config) return;
    setPhrase(config.phrase);
    setEngine(config.engine || "auto");
    // Floor 0.5 (matches the backend clamp): an old config below the floor is
    // shown lifted, never as a deaf sub-floor value.
    setSensitivity(Math.max(0.5, config.sensitivity));
    setCustomModelPath(config.custom_model_path ?? "");
    // ?? false keeps the Switch controlled even if an older backend omits it.
    setEnabled(config.enabled ?? false);
  }, [config]);

  async function onToggleActivation(next: boolean) {
    setTogglingActivation(true);
    setEnabled(next); // optimistic
    try {
      await setWakeActivation(next);
      pushToast("info", t("settings_view.wake_word.activation_saved"));
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

  async function onRestart() {
    try {
      await fetch("/api/settings/restart-app", { method: "POST" });
      pushToast("info", t("settings_view.wake_word.restarting"));
    } catch (e) {
      pushToast("error", (e as Error).message);
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
            min={0.5}
            max={1}
            step={0.05}
            value={sensitivity}
            onChange={(e) => setSensitivity(Number(e.target.value))}
            disabled={loading}
            className="mt-1.5 w-full accent-primary disabled:opacity-50"
          />
          {/* Fine print: why the slider bottoms out at 0.5 (user mandate
              2026-07-07 — a sub-floor sensitivity reads as a broken wake). */}
          <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
            {t("settings_view.wake_word.sensitivity_floor_note")}
          </p>

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
                  <Button size="sm" className="mt-2" onClick={() => void onRestart()}>
                    {t("settings_view.wake_word.enable_local_restart")}
                  </Button>
                </div>
              )}
            </div>
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
    // Navigation / editing cluster + numpad operators (the backend key names).
    up: "↑",
    down: "↓",
    left: "←",
    right: "→",
    insert: "Insert",
    delete: "Delete",
    home: "Home",
    end: "End",
    page_up: "PageUp",
    page_down: "PageDown",
    enter: "Enter",
    tab: "Tab",
    backspace: "Backspace",
    add_key: "Num +",
    subtract_key: "Num −",
    multiply_key: "Num *",
    divide_key: "Num /",
    decimal_key: "Num .",
  };
  // Numpad digits render as "Num 3" rather than "NUMPAD_3".
  const numpad = (p: string) =>
    /^numpad_[0-9]$/.test(p) ? "Num " + p.slice(7) : null;
  return combo
    .split("+")
    .map((p) => labels[p] ?? numpad(p) ?? p.toUpperCase())
    .join(" + ");
}

const _KEYBIND_ROWS: { action: KeybindAction; labelKey: string }[] = [
  { action: "call", labelKey: "settings_view.keybinds.call_label" },
  { action: "hangup", labelKey: "settings_view.keybinds.hangup_label" },
  { action: "ptt", labelKey: "settings_view.keybinds.talk_label" },
];

/**
 * Editable voice keybinds: Call / Hangup / Talk-PTT, one row each. The user
 * clicks Record and presses a combination (captured via eventToCombo), or
 * resets to default, then saves. The backend validator is the authority — an
 * unsafe combo or a collision with another action is rejected with a reason
 * shown as a toast. A successful save surfaces a restart-required hint.
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

// The keyboard family (Mac vs PC modifier labels) is fixed for the session.
const _KB_PLATFORM = detectKeyboardPlatform();

// Each action's i18n label key — used to mark a key "already used by <action>".
const _ACTION_LABEL_KEY: Record<KeybindAction, string> = {
  call: "settings_view.keybinds.call_label",
  hangup: "settings_view.keybinds.hangup_label",
  ptt: "settings_view.keybinds.talk_label",
};

/** The combo rendered as keycap chips ("Ctrl + F5" → [Ctrl] + [F5]). */
function ComboChips({ combo }: { combo: string }) {
  const parts = formatCombo(combo).split(" + ");
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={`${p}-${i}`}>
          {i > 0 && <span className="text-muted-foreground/50">+</span>}
          <kbd className="rounded border border-border bg-muted/70 px-1.5 py-0.5 font-mono text-[11px] leading-none text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.35)]">
            {p}
          </kbd>
        </Fragment>
      ))}
    </>
  );
}

/** The localized live-validation message for the combo being built, or null. */
function validationText(
  v: ComboValidation,
  t: (key: string) => string,
): string | null {
  if (v.status !== "error" && v.status !== "warning") return null;
  if (v.reason === "collision") {
    return t("settings_view.keybinds.validation.collision")
      .replace("{action}", v.conflict.action)
      .replace("{combo}", formatCombo(v.conflict.combo));
  }
  return t(`settings_view.keybinds.validation.${v.reason}`);
}

function KeybindRow({
  action,
  label,
  config,
  loading,
  onSave,
}: {
  action: KeybindAction;
  label: string;
  config: KeybindsConfig | null;
  loading: boolean;
  onSave: (a: KeybindAction, h: string) => Promise<KeybindSaveResult>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const current = config?.keybinds[action] ?? "";
  const def = config?.defaults[action];

  const [combo, setCombo] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // Physical codes currently held — mirrored from the recorder so the on-screen
  // keyboard lights up live as the user presses keys.
  const [pressedCodes, setPressedCodes] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (config) setCombo(config.keybinds[action]);
  }, [config, action]);

  // Tokens already bound to the OTHER actions → marked "used" on the keyboard so
  // the user can pick a free key (their keys "can't be free", as reported).
  const boundTokens = useMemo(() => {
    const out: Record<string, string> = {};
    if (!config) return out;
    for (const [act, c] of Object.entries(config.keybinds)) {
      if (act === action) continue;
      const lbl = t(_ACTION_LABEL_KEY[act as KeybindAction]);
      for (const tok of comboTokens(c)) out[tok] = lbl;
    }
    return out;
  }, [config, action, t]);

  // The OTHER actions' combos keyed by their translated label, so a collision
  // message names the action exactly the way the UI labels it.
  const otherCombos = useMemo(() => {
    const out: Record<string, string> = {};
    if (!config) return out;
    for (const [act, c] of Object.entries(config.keybinds)) {
      if (act !== action) out[t(_ACTION_LABEL_KEY[act as KeybindAction])] = c;
    }
    return out;
  }, [config, action, t]);

  // Live validation — every backend rule surfaces HERE, while the user builds
  // the combo, instead of as a cryptic post-Save error toast (the reported
  // "I picked Arrow Up and got a weird error message" experience).
  const validation = useMemo(
    () => validateCombo(combo, otherCombos),
    [combo, otherCombos],
  );
  const invalid = validation.status === "error";
  const validationMsg = validationText(validation, t);

  // Click-to-assign: toggle a key in/out of the combo without a physical press.
  // Functional update — toggles dispatched before the next render must each
  // build on the previous one, not on a stale closure combo (last-click-wins).
  function onToggleToken(token: string) {
    setCombo((prev) => {
      const tokens = comboTokens(prev);
      if (tokens.has(token)) tokens.delete(token);
      else tokens.add(token);
      return composeCombo(tokens);
    });
    setSaved(false);
  }

  // While capturing, listen on `window` (capture phase) instead of on a single
  // button. Three reasons:
  //   1. Focus: clicking the "Record" button puts focus on THAT button, so a
  //      key listener living only on the display field never fired — the combo
  //      was silently dropped. A window listener catches the chord no matter
  //      which control has focus.
  //   2. Chord: a held set accumulates every non-modifier key, so several keys
  //      pressed together (WASD, F7+F8, I+Y) — which the global-hotkeys backend
  //      registers natively (the Call default is f3+f4) — all land in the combo
  //      instead of only the first one.
  //   3. Commit on FULL release, not on the first keyup. We track every
  //      physically-held key (incl. modifiers, by `event.code`) and only commit
  //      once the user has let go of everything. Committing on the first keyup
  //      ended the recording the instant any one key lifted, so a human pressing
  //      a chord (whose key releases are never perfectly simultaneous, and whose
  //      presses roll in one after another) only ever got the first key — the
  //      reported "press several, only one is recorded" bug. Now the rule is the
  //      natural one: "hold your keys, then let go".
  // preventDefault on both edges also stops the keystrokes from leaking into
  // the rest of the app while recording (the "everything lags" symptom).
  // What Escape restores: the SAVED value (the server truth), falling back to
  // the combo as of recording start when nothing is saved yet. Kept in a ref so
  // the capture effect (deps: [capturing]) always reads the live value — a
  // mid-recording save refetches the config, and restoring a stale snapshot
  // would silently diverge the field from what the server actually has.
  const currentRef = useRef(current);
  currentRef.current = current;
  const comboBeforeCapture = useRef(combo);

  useEffect(() => {
    if (!capturing) return;
    comboBeforeCapture.current = combo; // fallback when nothing is saved yet
    setPressedCodes(new Set()); // fresh highlight state for this gesture
    const held = new Set<string>(); // non-modifier key tokens seen this gesture
    const pressed = new Set<string>(); // physical event.codes currently down
    let pending: string | null = null; // fullest chord captured so far
    let idle: ReturnType<typeof setTimeout> | undefined; // fallback-commit timer

    function commit() {
      if (pending) {
        setCombo(pending);
        setSaved(false);
        setCapturing(false);
      }
    }

    function onKeyDown(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        if (idle) clearTimeout(idle); // cancel a pending fallback commit
        // Undo the live preview: back to the saved value (server truth).
        setCombo(currentRef.current || comboBeforeCapture.current);
        setCapturing(false);
        return;
      }
      pressed.add(e.code);
      setPressedCodes(new Set(pressed)); // live keyboard highlight
      const tok = codeToKeyToken(e.code);
      if (tok) held.add(tok);
      const next = chordToCombo(e, held);
      if (next) {
        pending = next;
        setCombo(next); // live preview as the chord grows
        setSaved(false);
      }
      // Fallback: some keys — function keys especially, and any key whose
      // release lands while the window is losing focus — do NOT reliably
      // deliver a keyup. Without this the "commit on full release" path below
      // would hang forever ("F5+F6 never records"). Re-arm an idle timer on
      // every keydown; once the user stops pressing for ~900 ms, commit the
      // chord we have even if a keyup never came.
      if (idle) clearTimeout(idle);
      idle = setTimeout(commit, 900);
    }

    function onKeyUp(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      pressed.delete(e.code);
      setPressedCodes(new Set(pressed)); // live keyboard highlight
      // Fast path: commit the instant EVERY key is released. `pending` holds
      // the fullest chord seen during the gesture, so the release order never
      // matters and early-lifted keys are not lost.
      if (pressed.size === 0 && pending) {
        if (idle) clearTimeout(idle);
        commit();
      }
    }

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      if (idle) clearTimeout(idle);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [capturing]);

  // Clear the live highlight on the falling edge of capturing, so reopening the
  // picker never flashes the previous chord's keys before the first new press.
  useEffect(() => {
    if (!capturing) setPressedCodes(new Set());
  }, [capturing]);

  async function onSaveClick() {
    const trimmed = combo.trim().toLowerCase();
    if (!trimmed) return;
    setSaving(true);
    setSaved(false);
    try {
      const res = await onSave(action, trimmed);
      setSaved(res.restart_required);
      // The save concludes the recording session. Leaving the recorder open
      // kept a stale pre-recording snapshot around that a later Esc would
      // "restore" — silently diverging the field from the saved value.
      setCapturing(false);
      pushToast("success", t("settings_view.keybinds.saved"));
    } catch (e) {
      // Backend rejected the combo (unsafe / collision) — show its reason.
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // Immediate, one-click unbind — no staging step, mirroring the "Reset to
  // default" link's immediacy. Bypasses onSaveClick's trimmed-empty guard,
  // which exists to stop an in-progress recording from saving nothing.
  async function onClearClick() {
    setSaving(true);
    try {
      const res = await onSave(action, "");
      setCombo("");
      setCapturing(false);
      setSaved(res.restart_required);
      pushToast("success", t("settings_view.keybinds.cleared"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const dirty = !!config && combo.trim().toLowerCase() !== current;
  const showReset = !!def && combo.trim().toLowerCase() !== def;

  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-foreground">{label}</span>
        {showReset && (
          <button
            type="button"
            className="text-[11px] text-muted-foreground underline hover:text-foreground"
            onClick={() => {
              if (def) {
                setCombo(def);
                setSaved(false);
              }
            }}
          >
            {t("settings_view.keybinds.reset")}
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          data-testid={`combo-field-${action}`}
          onClick={() => setCapturing((c) => !c)}
          disabled={loading}
          className={`flex min-h-[34px] flex-1 flex-wrap items-center gap-1 rounded-md border px-3 py-1.5 text-left text-sm transition-colors focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 ${
            capturing
              ? "border-primary bg-primary/10"
              : "border-input bg-background"
          }`}
        >
          {capturing && (
            <span className="relative mr-1 flex h-2 w-2 shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
            </span>
          )}
          {combo ? (
            <ComboChips combo={combo} />
          ) : (
            <span className="text-muted-foreground">
              {capturing
                ? t("settings_view.keybinds.recording")
                : loading
                  ? "—"
                  : t("settings_view.keybinds.unbound")}
            </span>
          )}
        </button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCapturing((c) => !c)}
          disabled={loading}
        >
          {capturing
            ? t("settings_view.keybinds.stop")
            : t("settings_view.keybinds.record")}
        </Button>
        <Button
          size="sm"
          onClick={onSaveClick}
          disabled={saving || loading || !dirty || invalid}
        >
          {saving ? t("settings_view.saving") : t("settings_view.keybinds.save")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid={`clear-keybind-${action}`}
          aria-label={t("settings_view.keybinds.clear")}
          title={t("settings_view.keybinds.clear")}
          onClick={onClearClick}
          disabled={saving || loading || !current}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {/* ONE stable status line: the validation message when there is one,
          the recording hint otherwise. Two separately appearing lines made the
          keyboard below jump vertically on every combo click. */}
      {(capturing || validationMsg) && (
        <p
          data-testid={validationMsg ? `keybind-validation-${action}` : undefined}
          className={`mt-2 text-[11px] ${
            validationMsg
              ? validation.status === "error"
                ? "text-destructive"
                : "text-amber-400"
              : "text-muted-foreground"
          }`}
        >
          {validationMsg ?? t("settings_view.keybinds.recording_hint")}
        </p>
      )}
      {capturing && (
        <KeyboardMap
          pressedCodes={pressedCodes}
          selectedTokens={comboTokens(combo)}
          boundTokens={boundTokens}
          platform={_KB_PLATFORM}
          onToggleToken={onToggleToken}
        />
      )}
      {saved && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {t("settings_view.keybinds.restart_required")}
        </p>
      )}
    </div>
  );
}
