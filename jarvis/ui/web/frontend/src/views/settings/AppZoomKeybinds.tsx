/**
 * The Keyboard shortcuts page's zoom pieces: `AppZoomChordRow` is one of the
 * three chords (bigger, smaller, back to 100 %) as a row of the shortcut list,
 * and `AppZoomKeybinds` is the on/off switch with the current size.
 *
 * It has its own small recorder instead of the shared voice-key recorder, and
 * that is deliberate: the shared one records physical key positions and knows
 * no punctuation, because a global OS hotkey cannot use it. Zoom needs `+` and
 * `-`, which sit on different keys on a German and a US keyboard, so this
 * recorder reads the character the key types (see lib/appZoom).
 *
 * The value goes into the browser's storage (store/appZoomSettings), like the
 * quick switcher's chord: zoom is an in-window shortcut, not a global hotkey.
 */
import { Fragment, useEffect, useState } from "react";
import { Minus, Pencil, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useAppZoomSupport } from "@/hooks/useAppZoom";
import { useT } from "@/i18n";
import {
  APP_ZOOM_INTENTS,
  appZoomCaps,
  appZoomComboFromEvent,
  appZoomComboProblem,
  defaultAppZoomBindings,
  nextAppZoom,
  type AppZoomComboProblem,
  type AppZoomIntent,
} from "@/lib/appZoom";
import { useAppZoomSettings } from "@/store/appZoomSettings";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import { parseChord } from "@/lib/quickSwitchChord";
import { NoKeys, ShortcutListRow } from "@/views/settings/ShortcutListRow";

const PROBLEM_KEY: Record<AppZoomComboProblem | "quick_switch", string> = {
  typing_key: "settings_view.app_zoom.problem_typing_key",
  os_reserved: "settings_view.app_zoom.problem_os_reserved",
  duplicate: "settings_view.app_zoom.problem_duplicate",
  quick_switch: "settings_view.app_zoom.problem_quick_switch",
};

function sameChord(a: string, b: string): boolean {
  const fold = (combo: string) => {
    const { mods, keys } = parseChord(combo);
    return [...[...mods].sort(), ...keys].join("+");
  };
  return Boolean(a && b) && fold(a) === fold(b);
}

export function ZoomCaps({ combo }: { combo: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {appZoomCaps(combo).map((cap, i) => (
        <Fragment key={`${cap}-${i}`}>
          {i > 0 && <span className="text-muted-foreground/50">+</span>}
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-micro text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.35)]">
            {cap}
          </kbd>
        </Fragment>
      ))}
    </span>
  );
}

/** One zoom step as a row of the shortcut list, with its own recorder. */
export function AppZoomChordRow({ intent, title }: { intent: AppZoomIntent; title: string }) {
  const t = useT();
  const enabled = useAppZoomSettings((s) => s.enabled);
  const bindings = useAppZoomSettings((s) => s.bindings);
  const setBinding = useAppZoomSettings((s) => s.setBinding);
  const quickSwitch = useQuickSwitchSettings();
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const combo = bindings[intent];
  const fallback = defaultAppZoomBindings()[intent];

  useEffect(() => {
    if (!recording) return;
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape") {
        setRecording(false);
        return;
      }
      const next = appZoomComboFromEvent(event);
      if (next === null) return; // only modifiers so far
      const others = APP_ZOOM_INTENTS.filter((other) => other !== intent).map((other) => bindings[other]);
      const problem = appZoomComboProblem(next, others);
      if (problem) {
        setError(t(PROBLEM_KEY[problem]));
        return;
      }
      if (quickSwitch.enabled && sameChord(next, quickSwitch.combo)) {
        setError(t(PROBLEM_KEY.quick_switch));
        return;
      }
      setError(null);
      setBinding(intent, next);
      setRecording(false);
    };
    // Capture on window: ahead of the zoom itself, the quick switcher and any
    // terminal pane, so the keys land here while recording.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [recording, intent, bindings, quickSwitch.enabled, quickSwitch.combo, setBinding, t]);

  const off = !enabled && !recording;
  const recordLabel = recording ? t("settings_view.keybinds.stop") : t("settings_view.keybinds.record");

  return (
    <ShortcutListRow
      testId={`app-zoom-row-${intent}`}
      recording={recording}
      title={title}
      scope="window"
      chord={
        recording ? (
          <span className="text-sm italic text-muted-foreground" aria-live="polite">
            {t("settings_view.keybinds.recording")}
          </span>
        ) : off ? (
          <NoKeys>{t("shortcut_overlay.off")}</NoKeys>
        ) : combo ? (
          <ZoomCaps combo={combo} />
        ) : (
          <NoKeys>{t("shortcut_overlay.unassigned")}</NoKeys>
        )
      }
      actions={
        <>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-8 w-8"
            data-testid={`app-zoom-record-${intent}`}
            aria-label={recordLabel}
            title={recordLabel}
            disabled={off}
            onClick={() => {
              setError(null);
              setRecording((r) => !r);
            }}
          >
            {recording ? <X /> : <Pencil />}
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className="h-8 w-8"
            data-testid={`app-zoom-clear-${intent}`}
            aria-label={t("settings_view.keybinds.clear")}
            title={t("settings_view.keybinds.clear")}
            disabled={off || !combo || recording}
            onClick={() => setBinding(intent, "")}
          >
            <X />
          </Button>
        </>
      }
    >
      {error && (
        <p className="mt-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {!off && !recording && combo !== fallback && (
        <div className="mt-1 flex justify-end">
          <button
            type="button"
            className="text-micro text-muted-foreground underline hover:text-foreground"
            onClick={() => {
              setError(null);
              setBinding(intent, fallback);
            }}
          >
            {t("settings_view.keybinds.reset")}
          </button>
        </div>
      )}
    </ShortcutListRow>
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
