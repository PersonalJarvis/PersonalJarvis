/**
 * A row of the shortcut list for an in-window chord recorded by CHARACTER: the
 * whole-app zoom steps, the terminal text size steps, the shortcut overview and
 * the Agentic IDE key menu.
 *
 * It has its own small recorder instead of the shared voice-key recorder, and
 * that is deliberate: the shared one records physical key positions and knows
 * no punctuation, because a global OS hotkey cannot use it. These chords need
 * `+`, `-` and `?`, which sit on different keys on a German and a US keyboard,
 * so this recorder reads the character the key types (see lib/appZoom).
 *
 * The pencil starts recording; while it records, the same button cancels and
 * the line under the row offers "Remove" and "Reset to default". The owner
 * decides where the chord is stored and which chords it refuses.
 */
import { useEffect, useState } from "react";
import { Pencil, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { appZoomCaps, appZoomComboFromEvent } from "@/lib/appZoom";
import type { ShortcutScope } from "@/lib/shortcutRegistry";
import { KeyCaps, NoKeys, ShortcutListRow } from "@/views/settings/ShortcutListRow";

export function CharacterChordRow({
  title,
  scope,
  combo,
  fallback,
  off = false,
  rowTestId,
  testIdSuffix,
  problemFor,
  onChange,
}: {
  title: string;
  scope: ShortcutScope;
  /** The stored chord; "" when the user removed it. */
  combo: string;
  /** The shipped chord "Reset to default" goes back to. */
  fallback: string;
  /** The feature is switched off: the row says so and cannot be edited. */
  off?: boolean;
  rowTestId: string;
  /** Names the controls: `chord-record-<suffix>`, `chord-clear-<suffix>`. */
  testIdSuffix: string;
  /** The message that refuses this chord, or null when it may be saved. */
  problemFor: (next: string) => string | null;
  onChange: (combo: string) => void;
}) {
  const t = useT();
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!recording) return;
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape") {
        setError(null);
        setRecording(false);
        return;
      }
      const next = appZoomComboFromEvent(event);
      if (next === null) return; // only modifiers so far
      const problem = problemFor(next);
      if (problem) {
        setError(problem);
        return;
      }
      setError(null);
      onChange(next);
      setRecording(false);
    };
    // Capture on window: ahead of the zoom itself, the quick switcher and any
    // terminal pane, so the keys land here while recording.
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [recording, problemFor, onChange]);

  const finish = (next: string) => {
    setError(null);
    onChange(next);
    setRecording(false);
  };
  const buttonLabel = recording ? t("settings_view.keybinds.stop") : t("shortcuts_view.edit");

  return (
    <ShortcutListRow
      testId={rowTestId}
      recording={recording}
      title={title}
      scope={scope}
      chord={
        recording ? (
          <span className="text-sm italic text-muted-foreground" aria-live="polite">
            {t("settings_view.keybinds.recording")}
          </span>
        ) : off ? (
          <NoKeys>{t("shortcut_overlay.off")}</NoKeys>
        ) : combo ? (
          <KeyCaps caps={appZoomCaps(combo)} />
        ) : (
          <NoKeys>{t("shortcut_overlay.unassigned")}</NoKeys>
        )
      }
      actions={
        <Button
          type="button"
          size="icon"
          variant={recording ? "secondary" : "ghost"}
          className="h-8 w-8"
          data-testid={`chord-record-${testIdSuffix}`}
          aria-label={buttonLabel}
          title={buttonLabel}
          disabled={off}
          onClick={() => {
            setError(null);
            setRecording((r) => !r);
          }}
        >
          {recording ? <X /> : <Pencil />}
        </Button>
      }
    >
      {recording && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
          <p className="text-micro text-muted-foreground">{t("shortcuts_view.record_hint")}</p>
          <div className="flex items-center gap-3">
            {combo && (
              <button
                type="button"
                data-testid={`chord-clear-${testIdSuffix}`}
                className="text-micro text-muted-foreground underline hover:text-foreground"
                onClick={() => finish("")}
              >
                {t("shortcuts_view.remove")}
              </button>
            )}
            {combo !== fallback && (
              <button
                type="button"
                className="text-micro text-muted-foreground underline hover:text-foreground"
                onClick={() => finish(fallback)}
              >
                {t("settings_view.keybinds.reset")}
              </button>
            )}
          </div>
        </div>
      )}
      {error && (
        <p className="mt-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
    </ShortcutListRow>
  );
}
