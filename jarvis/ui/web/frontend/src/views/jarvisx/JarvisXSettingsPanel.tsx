import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandedSelect, type BrandedSelectOption } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { KeybindAction, KeybindsConfig, KeybindSaveResult } from "@/hooks/useHotkey";
import { useT } from "@/i18n";
import {
  DISMISS_MAX_S,
  DISMISS_MIN_S,
  JARVISX_HOTKEY_ACTIONS,
  clampDismissSeconds,
  saveJarvisXSettings,
  type JarvisXHotkeyAction,
  type JarvisXSettings,
  type JarvisXSettingsPatch,
} from "@/lib/jarvisxApi";
import { useEventStore } from "@/store/events";
import { KeybindRow } from "@/views/settings/KeybindRow";

/**
 * The Jarvis X settings: the master switch, the six global shortcuts (the
 * same recorder the voice shortcuts use), the corner thumbnail, where captures
 * are saved, and the three after-capture effects.
 *
 * Every control saves on its own with a PUT of just its key; the backend's
 * answer replaces the local copy, so what is on screen is what is stored.
 */

/** Choices for "Delete old captures"; 0 = never. */
const KEEP_NEWEST_CHOICES = [0, 10, 20, 50, 100] as const;

const HOTKEY_LABEL_KEY: Record<JarvisXHotkeyAction, string> = {
  region: "jarvisx.settings.hotkey_region",
  window: "jarvisx.settings.hotkey_window",
  fullscreen: "jarvisx.settings.hotkey_fullscreen",
  record_region: "jarvisx.settings.hotkey_record_region",
  record_fullscreen: "jarvisx.settings.hotkey_record_fullscreen",
  stop_recording: "jarvisx.settings.hotkey_stop_recording",
};

function Row({
  label,
  hint,
  control,
  children,
  testId,
}: {
  label: string;
  hint?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="px-5 py-4" data-testid={testId}>
      <div className="flex items-center justify-between gap-6">
        <div className="min-w-0">
          <p className="text-base font-medium text-foreground">{label}</p>
          {hint && <p className="mt-0.5 text-sm text-muted-foreground">{hint}</p>}
        </div>
        {control && <div className="shrink-0">{control}</div>}
      </div>
      {children}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 px-1 text-sm font-medium uppercase tracking-wide text-foreground-faint">{title}</h2>
      <div className="divide-y divide-border rounded-xl border border-border bg-card">{children}</div>
    </section>
  );
}

export function JarvisXSettingsPanel({
  settings,
  error,
  onRetry,
  onSettings,
}: {
  settings: JarvisXSettings | null;
  /** Why the settings could not be loaded, if they could not. */
  error?: string | null;
  onRetry?: () => void;
  onSettings: (next: JarvisXSettings) => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [saving, setSaving] = useState(false);
  const [hotkeyErrors, setHotkeyErrors] = useState<Partial<Record<JarvisXHotkeyAction, string>>>({});
  const [dismissDraft, setDismissDraft] = useState<number | null>(null);
  const [dirDraft, setDirDraft] = useState<string | null>(null);

  const save = useCallback(
    async (patch: JarvisXSettingsPatch) => {
      if (!settings) return;
      setSaving(true);
      onSettings({ ...settings, ...patch, hotkeys: { ...settings.hotkeys, ...(patch.hotkeys ?? {}) } });
      try {
        onSettings(await saveJarvisXSettings(patch));
      } catch (error) {
        pushToast("error", t("jarvisx.settings.save_error").replace("{0}", (error as Error).message));
        onSettings(settings);
      } finally {
        setSaving(false);
      }
    },
    [onSettings, pushToast, settings, t],
  );

  const keepOptions = useMemo<BrandedSelectOption[]>(
    () =>
      KEEP_NEWEST_CHOICES.map((count) => ({
        value: String(count),
        label:
          count === 0
            ? t("jarvisx.settings.keep_all")
            : t("jarvisx.settings.keep_option").replace("{0}", String(count)),
      })),
    [t],
  );

  // The recorder's config shape, filled with this feature's shortcuts.
  const keybindConfig = useMemo<KeybindsConfig | null>(
    () =>
      settings
        ? {
            keybinds: (settings.hotkeys ?? {}) as unknown as KeybindsConfig["keybinds"],
            defaults: {},
            suggestions: [],
            restart_required: false,
          }
        : null,
    [settings],
  );

  const saveHotkey = useCallback(
    async (action: KeybindAction, combo: string): Promise<KeybindSaveResult> => {
      const name = action as unknown as JarvisXHotkeyAction;
      try {
        const next = await saveJarvisXSettings({ hotkeys: { [name]: combo } });
        onSettings(next);
        setHotkeyErrors((errors) => ({ ...errors, [name]: undefined }));
        return { ok: true, action, hotkey: combo, persisted: true, restart_required: false };
      } catch (error) {
        // The backend's 422 reason stays under the row, not only in a toast.
        setHotkeyErrors((errors) => ({ ...errors, [name]: (error as Error).message }));
        throw error;
      }
    },
    [onSettings],
  );

  const actionLabel = useCallback(
    (action: string) => {
      const key = HOTKEY_LABEL_KEY[action as JarvisXHotkeyAction];
      return key ? t(key) : undefined;
    },
    [t],
  );

  useEffect(() => {
    setDismissDraft(null);
    setDirDraft(null);
  }, [settings?.thumbnail_dismiss_s, settings?.save_dir]);

  if (!settings && error) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-xl border border-border bg-card p-5" role="alert">
        <p className="text-base text-foreground">{t("jarvisx.settings.load_failed")}</p>
        <p className="text-sm text-muted-foreground">{error}</p>
        {onRetry && (
          <Button type="button" size="sm" variant="secondary" onClick={onRetry}>
            {t("common.retry")}
          </Button>
        )}
      </div>
    );
  }

  if (!settings) {
    return (
      <div className="flex h-40 items-center justify-center" role="status" aria-busy="true">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  const off = !settings.enabled;
  const dismiss = dismissDraft ?? settings.thumbnail_dismiss_s;
  const commitDismiss = () => {
    if (dismissDraft === null) return;
    const next = clampDismissSeconds(dismissDraft);
    setDismissDraft(null);
    if (next !== settings.thumbnail_dismiss_s) void save({ thumbnail_dismiss_s: next });
  };
  const dir = dirDraft ?? settings.save_dir;
  const commitDir = () => {
    if (dirDraft === null) return;
    const next = dirDraft.trim();
    setDirDraft(null);
    if (next !== settings.save_dir) void save({ save_dir: next });
  };

  const shortcutStatus = (action: JarvisXHotkeyAction): ReactNode => {
    const error = hotkeyErrors[action];
    if (error) {
      return (
        <p className="px-1 text-sm text-destructive" data-testid={`jarvisx-hotkey-error-${action}`}>
          {error}
        </p>
      );
    }
    const combo = settings.hotkeys?.[action];
    const status = settings.shortcuts?.[action];
    if (!combo || off || !status) return null;
    if (status.armed) {
      return (
        <p className="flex items-center gap-1.5 px-1 text-sm text-muted-foreground" data-testid={`jarvisx-hotkey-armed-${action}`}>
          <span className="h-1.5 w-1.5 rounded-full bg-success" aria-hidden />
          {t("jarvisx.settings.hotkey_armed")}
        </p>
      );
    }
    return (
      <p className="flex items-center gap-1.5 px-1 text-sm text-warning" data-testid={`jarvisx-hotkey-unarmed-${action}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden />
        {status.detail
          ? t("jarvisx.settings.hotkey_unarmed").replace("{0}", status.detail)
          : t("jarvisx.settings.hotkey_unarmed_plain")}
      </p>
    );
  };

  return (
    <div className="flex max-w-3xl flex-col gap-6" data-testid="jarvisx-settings">
      <Section title={t("jarvisx.settings.section_general")}>
        <Row
          label={t("jarvisx.settings.enabled_label")}
          hint={t("jarvisx.settings.enabled_hint")}
          control={
            <Switch
              checked={settings.enabled}
              disabled={saving}
              aria-label={t("jarvisx.settings.enabled_label")}
              data-testid="jarvisx-enabled"
              onCheckedChange={(enabled) => void save({ enabled })}
            />
          }
        />
      </Section>

      <section>
        <h2 className="mb-2 px-1 text-sm font-medium uppercase tracking-wide text-foreground-faint">
          {t("jarvisx.settings.section_shortcuts")}
        </h2>
        {off && <p className="mb-2 px-1 text-sm text-muted-foreground">{t("jarvisx.settings.shortcuts_off")}</p>}
        <div className="flex flex-col gap-3">
          {JARVISX_HOTKEY_ACTIONS.map((action) => (
            <div key={action} className="flex flex-col gap-1">
              <KeybindRow
                action={action as unknown as KeybindAction}
                variant="voice"
                label={t(HOTKEY_LABEL_KEY[action])}
                config={keybindConfig}
                loading={false}
                onSave={saveHotkey}
                actionLabel={actionLabel}
              />
              {shortcutStatus(action)}
            </div>
          ))}
        </div>
      </section>

      <Section title={t("jarvisx.settings.section_thumbnail")}>
        <Row
          label={t("jarvisx.settings.persist_label")}
          hint={t("jarvisx.settings.persist_hint")}
          control={
            <Switch
              checked={settings.thumbnail_persist}
              disabled={saving}
              aria-label={t("jarvisx.settings.persist_label")}
              data-testid="jarvisx-persist"
              onCheckedChange={(thumbnail_persist) => void save({ thumbnail_persist })}
            />
          }
        />
        {!settings.thumbnail_persist && (
          <Row
            testId="jarvisx-dismiss-row"
            label={t("jarvisx.settings.dismiss_label")}
            hint={t("jarvisx.settings.dismiss_hint").replace("{0}", String(dismiss))}
          >
            <div className="mt-3 flex items-center gap-4">
              <input
                type="range"
                min={DISMISS_MIN_S}
                max={DISMISS_MAX_S}
                step={1}
                value={dismiss}
                aria-label={t("jarvisx.settings.dismiss_label")}
                data-testid="jarvisx-dismiss-slider"
                onChange={(event) => setDismissDraft(Number(event.target.value))}
                onPointerUp={commitDismiss}
                onKeyUp={commitDismiss}
                onBlur={commitDismiss}
                className="h-1.5 min-w-0 flex-1 cursor-pointer accent-[hsl(var(--accent))]"
              />
              <div className="flex shrink-0 items-center gap-1.5">
                <Input
                  type="number"
                  min={DISMISS_MIN_S}
                  max={DISMISS_MAX_S}
                  value={dismiss}
                  aria-label={t("jarvisx.settings.dismiss_seconds")}
                  data-testid="jarvisx-dismiss-input"
                  onChange={(event) => setDismissDraft(Number(event.target.value))}
                  onBlur={commitDismiss}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") commitDismiss();
                  }}
                  className="h-8 w-20 text-right tabular-nums"
                />
                <span className="text-sm text-muted-foreground">{t("jarvisx.settings.seconds_unit")}</span>
              </div>
            </div>
          </Row>
        )}
      </Section>

      <Section title={t("jarvisx.settings.section_after")}>
        <Row label={t("jarvisx.settings.save_dir_label")} hint={t("jarvisx.settings.save_dir_hint")}>
          <Input
            value={dir}
            spellCheck={false}
            aria-label={t("jarvisx.settings.save_dir_label")}
            data-testid="jarvisx-save-dir"
            onChange={(event) => setDirDraft(event.target.value)}
            onBlur={commitDir}
            onKeyDown={(event) => {
              if (event.key === "Enter") commitDir();
              if (event.key === "Escape") setDirDraft(null);
            }}
            className="mt-3 font-mono text-sm"
          />
        </Row>
        <Row
          label={t("jarvisx.settings.clipboard_label")}
          hint={t("jarvisx.settings.clipboard_hint")}
          control={
            <Switch
              checked={settings.copy_to_clipboard}
              disabled={saving}
              aria-label={t("jarvisx.settings.clipboard_label")}
              data-testid="jarvisx-clipboard"
              onCheckedChange={(copy_to_clipboard) => void save({ copy_to_clipboard })}
            />
          }
        />
        <Row
          label={t("jarvisx.settings.sound_label")}
          control={
            <Switch
              checked={settings.sound}
              disabled={saving}
              aria-label={t("jarvisx.settings.sound_label")}
              data-testid="jarvisx-sound"
              onCheckedChange={(sound) => void save({ sound })}
            />
          }
        />
        <Row
          label={t("jarvisx.settings.effect_label")}
          hint={t("jarvisx.settings.effect_hint")}
          control={
            <Switch
              checked={settings.effect}
              disabled={saving}
              aria-label={t("jarvisx.settings.effect_label")}
              data-testid="jarvisx-effect"
              onCheckedChange={(effect) => void save({ effect })}
            />
          }
        />
        {typeof settings.keep_newest === "number" && (
          <Row
            label={t("jarvisx.settings.keep_label")}
            hint={t("jarvisx.settings.keep_hint")}
            control={
              <BrandedSelect
                value={String(settings.keep_newest)}
                options={keepOptions}
                ariaLabel={t("jarvisx.settings.keep_label")}
                disabled={saving}
                testId="jarvisx-keep-newest"
                onValueChange={(value) => void save({ keep_newest: Number(value) })}
              />
            }
          />
        )}
      </Section>

      {!settings.recording_available && (
        <p className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground" data-testid="jarvisx-recording-unavailable">
          {t("jarvisx.settings.recording_unavailable").replace(
            "{0}",
            settings.recording_detail || t("jarvisx.recording_unavailable"),
          )}
        </p>
      )}
    </div>
  );
}
