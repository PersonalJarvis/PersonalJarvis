/**
 * The Keyboard shortcuts page's zoom pieces: `AppZoomChordRow` is one of the
 * three chords (bigger, smaller, back to 100 %) as a row of the shortcut list,
 * and `AppZoomKeybinds` is the on/off switch with the current size.
 *
 * The chords are recorded by character (see ./CharacterChordRow) and go into
 * the browser's storage (store/appZoomSettings), like the quick switcher's
 * chord: zoom is an in-window shortcut, not a global hotkey.
 */
import { useCallback } from "react";
import { Minus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useAppZoomSupport } from "@/hooks/useAppZoom";
import { useT } from "@/i18n";
import {
  APP_ZOOM_INTENTS,
  appZoomComboProblem,
  defaultAppZoomBindings,
  nextAppZoom,
  type AppZoomComboProblem,
  type AppZoomIntent,
} from "@/lib/appZoom";
import { useAppZoomSettings } from "@/store/appZoomSettings";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import { parseChord } from "@/lib/quickSwitchChord";
import { CharacterChordRow } from "@/views/settings/CharacterChordRow";

const PROBLEM_KEY: Record<AppZoomComboProblem | "quick_switch", string> = {
  typing_key: "settings_view.app_zoom.problem_typing_key",
  os_reserved: "settings_view.app_zoom.problem_os_reserved",
  duplicate: "settings_view.app_zoom.problem_duplicate",
  quick_switch: "settings_view.app_zoom.problem_quick_switch",
};

/** Same chord, whatever order the modifiers were written in. */
export function sameChord(a: string, b: string): boolean {
  const fold = (combo: string) => {
    const { mods, keys } = parseChord(combo);
    return [...[...mods].sort(), ...keys].join("+");
  };
  return Boolean(a && b) && fold(a) === fold(b);
}

/** One zoom step as a row of the shortcut list. */
export function AppZoomChordRow({ intent, title }: { intent: AppZoomIntent; title: string }) {
  const t = useT();
  const enabled = useAppZoomSettings((s) => s.enabled);
  const bindings = useAppZoomSettings((s) => s.bindings);
  const setBinding = useAppZoomSettings((s) => s.setBinding);
  const quickSwitch = useQuickSwitchSettings();

  const problemFor = useCallback(
    (next: string) => {
      const others = APP_ZOOM_INTENTS.filter((other) => other !== intent).map((other) => bindings[other]);
      const problem = appZoomComboProblem(next, others);
      if (problem) return t(PROBLEM_KEY[problem]);
      if (quickSwitch.enabled && sameChord(next, quickSwitch.combo)) return t(PROBLEM_KEY.quick_switch);
      return null;
    },
    [intent, bindings, quickSwitch.enabled, quickSwitch.combo, t],
  );
  const onChange = useCallback((combo: string) => setBinding(intent, combo), [intent, setBinding]);

  return (
    <CharacterChordRow
      rowTestId={`app-zoom-row-${intent}`}
      testIdSuffix={`app-zoom-${intent}`}
      title={title}
      scope="window"
      combo={bindings[intent]}
      fallback={defaultAppZoomBindings()[intent]}
      off={!enabled}
      problemFor={problemFor}
      onChange={onChange}
    />
  );
}

export function AppZoomKeybinds() {
  const t = useT();
  const enabled = useAppZoomSettings((s) => s.enabled);
  const level = useAppZoomSettings((s) => s.level);
  const setEnabled = useAppZoomSettings((s) => s.setEnabled);
  const setLevel = useAppZoomSettings((s) => s.setLevel);
  const support = useAppZoomSupport((s) => s.support);
  const label = t("settings_view.app_zoom.title");
  const percent = `${Math.round(level * 100)} %`;
  const stepper = support !== "browser" && support !== "unsupported";

  return (
    <div className="space-y-stack" data-testid="app-zoom-settings">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-base font-medium text-foreground">{label}</p>
          <p className="mt-0.5 text-sm text-muted-foreground">{t("settings_view.app_zoom.description")}</p>
        </div>
        <Switch checked={enabled} onCheckedChange={setEnabled} aria-label={label} data-testid="app-zoom-enabled" />
      </div>
      {stepper && (
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-foreground">{t("settings_view.app_zoom.level_label")}</span>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              size="icon"
              variant="outline"
              className="h-8 w-8"
              aria-label={t("settings_view.app_zoom.out_label")}
              data-testid="app-zoom-step-out"
              disabled={level <= nextAppZoom(level, "out")}
              onClick={() => setLevel(nextAppZoom(level, "out"))}
            >
              <Minus />
            </Button>
            <button
              type="button"
              className="min-w-16 rounded-md px-2 py-1 text-center font-mono text-sm tabular-nums text-foreground hover:bg-secondary"
              title={t("settings_view.app_zoom.reset_label")}
              data-testid="app-zoom-level"
              onClick={() => setLevel(1)}
            >
              {percent}
            </button>
            <Button
              type="button"
              size="icon"
              variant="outline"
              className="h-8 w-8"
              aria-label={t("settings_view.app_zoom.in_label")}
              data-testid="app-zoom-step-in"
              disabled={level >= nextAppZoom(level, "in")}
              onClick={() => setLevel(nextAppZoom(level, "in"))}
            >
              <Plus />
            </Button>
          </div>
        </div>
      )}
      <p className="text-sm text-muted-foreground" data-testid="app-zoom-note">
        {support === "browser"
          ? t("settings_view.app_zoom.note_browser")
          : support === "unsupported"
            ? t("settings_view.app_zoom.note_unsupported")
            : t("settings_view.app_zoom.note_terminal")}
      </p>
    </div>
  );
}
