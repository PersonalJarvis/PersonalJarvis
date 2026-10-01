/**
 * Settings → Keyboard shortcuts: the quick switcher's on/off switch and chord.
 *
 * The chord is edited with the SAME recorder the voice keybinds use, so
 * recording, the on-screen keyboard and the "already used by" marks behave
 * identically. What differs is where the value goes: into the browser's storage
 * (see store/quickSwitchSettings) instead of the backend, because the switcher
 * is an in-window shortcut, not a global OS hotkey.
 *
 * The voice keybinds are passed in so a chord EQUAL to one of them is refused —
 * a global hotkey fires before the window sees the key, so the switcher would
 * never open on it. They are deliberately NOT handed to the recorder: its
 * overlap caution ("pressing the longer one triggers both") is true for global
 * hotkeys but false here, because the switcher fires on its exact chord only.
 */
import { Search } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/i18n";
import type { KeybindAction, KeybindSaveResult, KeybindsConfig } from "@/hooks/useHotkey";
import { KeybindRow } from "@/views/settings/KeybindRow";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import {
  defaultQuickSwitchCombo,
  hostChordPlatform,
  parseChord,
  quickSwitchComboProblem,
  type ChordProblem,
} from "@/lib/quickSwitchChord";

/** The recorder keys rows by action id; this one is not a backend action. */
const ACTION = "quick_switch" as unknown as KeybindAction;

const PROBLEM_KEY: Record<ChordProblem, string> = {
  one_key: "settings_view.quick_switch.problem_one_key",
  typing_key: "settings_view.quick_switch.problem_typing_key",
  os_reserved: "settings_view.quick_switch.problem_os_reserved",
  app_reserved: "settings_view.quick_switch.problem_app_reserved",
};

/** One spelling per chord, so "cmd+space" and "win+space" compare equal. */
function chordKey(combo: string): string {
  const { mods, keys } = parseChord(combo);
  return [...[...mods].sort(), ...[...keys].sort()].join("+");
}

export function QuickSwitchKeybind({ voiceConfig }: { voiceConfig: KeybindsConfig | null }) {
  const t = useT();
  const enabled = useQuickSwitchSettings((s) => s.enabled);
  const combo = useQuickSwitchSettings((s) => s.combo);
  const setEnabled = useQuickSwitchSettings((s) => s.setEnabled);
  const setCombo = useQuickSwitchSettings((s) => s.setCombo);
  const mac = hostChordPlatform() === "mac";
  const label = t("settings_view.quick_switch.title");

  const config: KeybindsConfig = {
    keybinds: { [ACTION]: combo },
    defaults: { [ACTION]: defaultQuickSwitchCombo() },
    suggestions: [],
    restart_required: false,
    mouse_buttons: { supported: false, reason: t("settings_view.quick_switch.keys_only") },
  };

  const save = async (_action: KeybindAction, hotkey: string): Promise<KeybindSaveResult> => {
    if (hotkey) {
      const problem = quickSwitchComboProblem(hotkey);
      // Thrown, so the recorder shows it as its error toast and keeps the old chord.
      if (problem) throw new Error(t(PROBLEM_KEY[problem]));
      const taken = Object.entries(voiceConfig?.keybinds ?? {}).find(
        ([, other]) => other && chordKey(other) === chordKey(hotkey),
      );
      if (taken) throw new Error(t("settings_view.quick_switch.problem_voice_taken"));
    }
    setCombo(hotkey);
    return { ok: true, action: ACTION, hotkey, persisted: true, restart_required: false };
  };

  return (
    <div className="mt-block border-t border-border pt-block" data-testid="quick-switch-settings">
      <div className="flex items-start gap-3">
        <Search className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h4 className="text-title font-semibold text-foreground-strong">{label}</h4>
              <p className="mt-1 text-meta text-muted-foreground">
                {t("settings_view.quick_switch.description")}
              </p>
            </div>
            <Switch
              checked={enabled}
              onCheckedChange={setEnabled}
              aria-label={label}
              data-testid="quick-switch-enabled"
            />
          </div>
          {enabled && (
            <div className="mt-block space-y-stack">
              <KeybindRow
                action={ACTION}
                label={t("settings_view.quick_switch.shortcut_label")}
                config={config}
                loading={false}
                onSave={save}
                actionLabel={(action) => (action === (ACTION as string) ? label : undefined)}
              />
              {mac && (
                <p className="text-meta text-muted-foreground" data-testid="quick-switch-mac-note">
                  {t("settings_view.quick_switch.mac_note")}
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
