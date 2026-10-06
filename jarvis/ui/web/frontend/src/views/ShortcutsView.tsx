/**
 * Keyboard shortcuts — a Settings-hub page of its own.
 *
 * Every shortcut Jarvis answers to, in ONE list: the voice keys, the appshot
 * keys, the quick switcher, the zoom steps, the terminal text size, the
 * shortcut overview and the Agentic IDE key menu. Each row says what the
 * shortcut does and where it works, shows its keys in one column, and carries
 * one pencil that changes it right here — no row sends the user elsewhere.
 * The on/off switches live under the list. On top sits a key tester: press
 * anything and it shows the keys big, plus what — if anything — they do in
 * Jarvis. It answers "is this combination free?" before someone records it.
 *
 * The list is built from `lib/shortcutRegistry` — the source the `?` overlay
 * renders from — plus the appshot settings. The tester resolves through the
 * REAL matchers (the quick switcher's chord matcher, the terminal zoom matcher,
 * the voice keybind normalisation), never through a copied list, so it cannot
 * tell a different story than the app.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Keyboard, Pencil } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import {
  eventToCombo,
  modifierTokens,
  normalizedComboTokens,
  useKeybinds,
  type KeybindAction,
  type KeybindsConfig,
} from "@/hooks/useHotkey";
import { detectKeyboardPlatform } from "@/views/settings/keyboardLayout";
import { ACTION_LABEL_KEY, ComboChips, KeybindRow, formatCombo } from "@/views/settings/KeybindRow";
import { QuickSwitchKeybind } from "@/views/settings/QuickSwitchKeybind";
import { AppZoomChordRow, AppZoomKeybinds, sameChord } from "@/views/settings/AppZoomKeybinds";
import { CharacterChordRow } from "@/views/settings/CharacterChordRow";
import { KeyCaps, NoKeys, ShortcutListRow } from "@/views/settings/ShortcutListRow";
import { AppshotShortcutField } from "@/views/AppshotShortcutField";
import { appZoomChordMatches, appZoomIntentFor, type AppZoomComboProblem, type AppZoomIntent } from "@/lib/appZoom";
import { APP_CHORD_IDS, appChordProblem, defaultAppChords, type AppChordId } from "@/lib/appChords";
import { useAppZoomSettings } from "@/store/appZoomSettings";
import { terminalZoomBindings, useAppChordSettings } from "@/store/appChordSettings";
import { eventMatchesChord } from "@/lib/quickSwitchChord";
import { isTextEntryTarget, shouldOpenShortcutOverlay } from "@/lib/shortcutOverlayTrigger";
import {
  SHORTCUTS,
  type AppSettingShortcut,
  type RebindableShortcut,
  type Shortcut,
  type ShortcutScope,
} from "@/lib/shortcutRegistry";
import {
  fetchAppshotSettings,
  formatAppshotHotkey,
  saveAppshotSettings,
  type AppshotSettings,
  type AppshotSettingsPatch,
} from "@/lib/appshotApi";
import { zoomIntentForBindings } from "@/components/agentic/terminalZoom";
import { isLeaderChord } from "@/components/agentic/ideHotkeys";
import { cn } from "@/lib/utils";

const APP_ZOOM_LABEL = {
  in: "settings_view.app_zoom.in_label",
  out: "settings_view.app_zoom.out_label",
  reset: "settings_view.app_zoom.reset_label",
} as const;

const APP_ZOOM_SETTING: Partial<Record<AppSettingShortcut["setting"], AppZoomIntent>> = {
  app_zoom_in: "in",
  app_zoom_out: "out",
  app_zoom_reset: "reset",
};

const ZOOM_LABEL = {
  in: "shortcut_overlay.workspace.zoom_in",
  out: "shortcut_overlay.workspace.zoom_out",
  reset: "shortcut_overlay.workspace.zoom_reset",
} as const;

/** The overlay calls itself "this list"; on this page that would mean the page. */
const LABEL_OVERRIDE: Record<string, string> = {
  "shortcut_overlay.workspace.open_overlay": "shortcuts_view.open_overlay",
};

const CHORD_PROBLEM_KEY: Record<AppZoomComboProblem, string> = {
  typing_key: "shortcuts_view.problem_typing_key",
  os_reserved: "shortcuts_view.problem_os_reserved",
  duplicate: "shortcuts_view.problem_duplicate",
};

type AppshotField = "hotkey" | "region_hotkey" | "recording_hotkey";

const APPSHOT_ROWS: readonly { field: AppshotField; labelKey: string }[] = [
  { field: "hotkey", labelKey: "shortcuts_view.appshot_window" },
  { field: "region_hotkey", labelKey: "shortcuts_view.appshot_region" },
  { field: "recording_hotkey", labelKey: "shortcuts_view.appshot_recording" },
];

const SCOPE_ORDER: Record<ShortcutScope, number> = { global: 0, window: 1, terminal: 2 };

/** One row of the list: a registry entry or an appshot key. */
type ListEntry =
  | { kind: "registry"; shortcut: Shortcut }
  | { kind: "appshot"; field: AppshotField; labelKey: string };

/** Where it works first (everywhere → window → terminal); the appshot keys after the voice keys. */
function entryRank(entry: ListEntry): [number, number] {
  if (entry.kind === "appshot") return [SCOPE_ORDER.global, 1];
  return [SCOPE_ORDER[entry.shortcut.scope], 0];
}

const LIST_ENTRIES: readonly ListEntry[] = [
  ...SHORTCUTS.map((shortcut): ListEntry => ({ kind: "registry", shortcut })),
  ...APPSHOT_ROWS.map((row): ListEntry => ({ kind: "appshot", ...row })),
]
  .map((entry, index) => ({ entry, index }))
  .sort((a, b) => {
    const [scopeA, rankA] = entryRank(a.entry);
    const [scopeB, rankB] = entryRank(b.entry);
    return scopeA - scopeB || rankA - rankB || a.index - b.index;
  })
  .map(({ entry }) => entry);

// ── Keycaps ────────────────────────────────────────────────────────────────

/** One big keycap. Theme tokens only, so it reads in light and dark. */
function BigCap({ children, ghost = false }: { children: ReactNode; ghost?: boolean }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-16 min-w-16 items-center justify-center rounded-xl border px-5",
        "font-sans text-2xl font-semibold tracking-tight",
        "shadow-[inset_0_-4px_0_hsl(var(--border)),0_1px_2px_rgb(var(--scrim-rgb)/0.12)]",
        ghost
          ? "border-dashed border-border text-muted-foreground/60"
          : "border-border bg-card text-foreground animate-in zoom-in-95 duration-150 motion-reduce:animate-none",
      )}
    >
      {children}
    </kbd>
  );
}

// ── The key tester ──────────────────────────────────────────────────────────

interface Pressed {
  /** Bumped on every press so the caps re-run their press animation. */
  seq: number;
  caps: string[];
  /** The i18n key of what it does, "" when nothing uses it, null while only modifiers are held. */
  meaningKey: string | null;
}

function KeyTester({ config }: { config: KeybindsConfig | null }) {
  const t = useT();
  const isMac = detectKeyboardPlatform() === "mac";
  const quickSwitch = useQuickSwitchSettings();
  const appZoom = useAppZoomSettings();
  const appChords = useAppChordSettings((s) => s.bindings);
  const [pressed, setPressed] = useState<Pressed | null>(null);

  useEffect(() => {
    let seq = 0;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat) return;
      // Plain typing into a field is typing, not a test press. A chord with
      // Ctrl/Alt/⌘ still counts: the hub opens with its search box focused,
      // and the tester must work without a click away first.
      const chordHeld = event.ctrlKey || event.altKey || event.metaKey;
      if (!chordHeld && isTextEntryTarget(event.target)) return;
      // Recording a shortcut: the keys belong to the recorder.
      if (document.querySelector('[data-keybind-recording="true"]')) return;

      const combo = eventToCombo(event);
      const mods = modifierTokens(event);
      const printable = event.key.length === 1 && event.key !== " " ? event.key.toUpperCase() : null;
      const caps = combo
        ? formatCombo(combo).split(" + ")
        : [
            ...(mods.length ? formatCombo(mods.join("+")).split(" + ") : []),
            ...(printable && !["Control", "Alt", "Shift", "Meta"].includes(event.key) ? [printable] : []),
          ];
      if (caps.length === 0) return;

      let meaningKey: string | null = "";
      const zoom = zoomIntentForBindings(event, { isMac, bindings: terminalZoomBindings(appChords) });
      const appZoomIntent = appZoom.enabled ? appZoomIntentFor(event, appZoom.bindings) : null;
      // The tester's own listener never marks a field, so the overview is
      // asked as if the key were pressed outside one.
      const overview = shouldOpenShortcutOverlay(
        {
          key: event.key,
          code: event.code,
          shiftKey: event.shiftKey,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          altKey: event.altKey,
          defaultPrevented: false,
          target: null,
        },
        appChords.shortcut_overlay,
      );
      if (quickSwitch.enabled && quickSwitch.combo && eventMatchesChord(event, quickSwitch.combo)) {
        meaningKey = "settings_view.quick_switch.title";
      } else if (appZoomIntent) {
        meaningKey = APP_ZOOM_LABEL[appZoomIntent];
      } else if (zoom) {
        meaningKey = ZOOM_LABEL[zoom];
      } else if (overview) {
        meaningKey = "shortcut_overlay.workspace.open_overlay";
      } else if (isLeaderChord(event, appChords.ide_menu)) {
        meaningKey = "shortcut_overlay.workspace.ide_menu";
      } else if (appChords.ide_commands && appZoomChordMatches(event, appChords.ide_commands)) {
        meaningKey = "shortcut_overlay.workspace.ide_commands";
      } else if (combo) {
        const mine = [...normalizedComboTokens(combo)].sort().join("+");
        const hit = Object.entries(config?.keybinds ?? {}).find(
          ([, other]) => other && [...normalizedComboTokens(other)].sort().join("+") === mine,
        );
        if (hit) meaningKey = ACTION_LABEL_KEY[hit[0] as KeybindAction] ?? "";
      } else if (!printable) {
        meaningKey = null; // modifiers only — the chord is not finished yet
      }
      seq += 1;
      setPressed({ seq, caps, meaningKey });
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [config, quickSwitch.enabled, quickSwitch.combo, appZoom.enabled, appZoom.bindings, appChords, isMac]);

  const ghost = quickSwitch.combo ? formatCombo(quickSwitch.combo).split(" + ") : ["Ctrl", "Space"];

  return (
    <section
      data-testid="shortcut-tester"
      className="relative overflow-hidden rounded-2xl border border-border bg-card px-6 py-8"
    >
      {/* A soft wash of the accent behind the caps — depth without a picture. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(60% 80% at 50% 0%, rgb(var(--accent-rgb) / 0.10), transparent 70%)",
        }}
      />
      <div className="relative flex flex-col items-center gap-5 text-center">
        <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
          {t("shortcuts_view.tester_title")}
        </p>
        <div className="flex min-h-16 flex-wrap items-center justify-center gap-2.5" aria-live="polite">
          {(pressed?.caps ?? ghost).map((cap, i) => (
            <span key={`${pressed?.seq ?? "ghost"}-${i}`} className="inline-flex items-center gap-2.5">
              {i > 0 && <span className="text-xl text-muted-foreground/60">+</span>}
              <BigCap ghost={!pressed}>{cap}</BigCap>
            </span>
          ))}
        </div>
        <p
          data-testid="shortcut-tester-meaning"
          className={cn(
            "inline-flex min-h-8 items-center rounded-full px-4 text-sm font-medium",
            !pressed || pressed.meaningKey === null
              ? "text-muted-foreground"
              : pressed.meaningKey
                ? "bg-accent text-accent-foreground"
                : "bg-secondary text-foreground",
          )}
        >
          {!pressed
            ? t("shortcuts_view.tester_hint")
            : pressed.meaningKey === null
              ? t("shortcuts_view.tester_modifiers")
              : pressed.meaningKey
                ? t(pressed.meaningKey)
                : t("shortcuts_view.tester_free")}
        </p>
      </div>
    </section>
  );
}

// ── The list ────────────────────────────────────────────────────────────────

type Keybinds = ReturnType<typeof useKeybinds>;

function Chips({ combo }: { combo: string }) {
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-1">
      <ComboChips combo={combo} />
    </span>
  );
}

/** The pencil that opens or closes a row's editor — the one control every row has. */
function EditToggle({
  open,
  disabled = false,
  onToggle,
  testId,
}: {
  open: boolean;
  disabled?: boolean;
  onToggle: () => void;
  testId: string;
}) {
  const t = useT();
  return (
    <Button
      type="button"
      size="icon"
      variant={open ? "secondary" : "ghost"}
      className="h-8 w-8"
      data-testid={testId}
      aria-expanded={open}
      aria-label={t("shortcuts_view.edit")}
      title={t("shortcuts_view.edit")}
      disabled={disabled}
      onClick={onToggle}
    >
      <Pencil />
    </Button>
  );
}

function VoiceKeyRow({ shortcut, keybinds }: { shortcut: RebindableShortcut; keybinds: Keybinds }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const { config, loading, saveKeybind } = keybinds;
  const combo = config?.keybinds?.[shortcut.action] ?? "";

  // A push-to-talk key only works while dictation listens in "hold" mode, so
  // saving one pins that mode — the same side effect the voice section has.
  const pinHoldMode = useCallback(async () => {
    try {
      const res = await fetch("/api/dictation/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "hold", persist: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      // The keybind itself is already saved — report the missing side effect.
      pushToast("warning", (e as Error).message);
    }
  }, [pushToast]);

  return (
    <ShortcutListRow
      testId={`shortcut-row-${shortcut.action}`}
      title={t(shortcut.labelKey)}
      scope={shortcut.scope}
      chord={
        !config ? (
          <NoKeys>—</NoKeys>
        ) : combo ? (
          <Chips combo={combo} />
        ) : (
          <NoKeys>{t("shortcut_overlay.unassigned")}</NoKeys>
        )
      }
      actions={
        <EditToggle open={open} onToggle={() => setOpen((o) => !o)} testId={`shortcuts-edit-${shortcut.action}`} />
      }
    >
      {open && (
        <div className="mt-3">
          <KeybindRow
            action={shortcut.action}
            label={t("shortcuts_view.new_keys")}
            config={config}
            loading={loading}
            onSave={saveKeybind}
            onSaved={shortcut.action === "dictate" ? pinHoldMode : undefined}
          />
        </div>
      )}
    </ShortcutListRow>
  );
}

function QuickSwitchRow({
  shortcut,
  voiceConfig,
}: {
  shortcut: AppSettingShortcut;
  voiceConfig: KeybindsConfig | null;
}) {
  const t = useT();
  const enabled = useQuickSwitchSettings((s) => s.enabled);
  const combo = useQuickSwitchSettings((s) => s.combo);
  const [open, setOpen] = useState(false);
  const editing = open && enabled;

  return (
    <ShortcutListRow
      testId="shortcut-row-quick_switch"
      title={t(shortcut.labelKey)}
      scope={shortcut.scope}
      chord={
        !enabled ? (
          <NoKeys>{t("shortcut_overlay.off")}</NoKeys>
        ) : combo ? (
          <KeyCaps caps={formatCombo(combo).split(" + ")} />
        ) : (
          <NoKeys>{t("shortcut_overlay.unassigned")}</NoKeys>
        )
      }
      actions={
        <EditToggle
          open={editing}
          disabled={!enabled}
          onToggle={() => setOpen((o) => !o)}
          testId="shortcuts-edit-quick_switch"
        />
      }
    >
      {editing && (
        <div className="mt-3">
          <QuickSwitchKeybind voiceConfig={voiceConfig} part="editor" />
        </div>
      )}
    </ShortcutListRow>
  );
}

/**
 * One of the in-app chords of lib/appChords (terminal text size, overview,
 * IDE key menu), recorded by character.
 */
function AppChordRow({ shortcut, id }: { shortcut: AppSettingShortcut; id: AppChordId }) {
  const t = useT();
  const bindings = useAppChordSettings((s) => s.bindings);
  const setBinding = useAppChordSettings((s) => s.setBinding);
  const appZoom = useAppZoomSettings((s) => s.bindings);
  const quickSwitch = useQuickSwitchSettings();

  const problemFor = useCallback(
    (next: string) => {
      // The terminal steps may share a chord with the whole-app zoom on
      // purpose: inside a terminal the terminal wins. Everything else must be
      // unique among the in-window chords.
      const others = APP_CHORD_IDS.filter((other) => other !== id).map((other) => bindings[other]);
      if (id === "shortcut_overlay" || id === "ide_menu" || id === "ide_commands") others.push(...Object.values(appZoom));
      const problem = appChordProblem(id, next, others);
      if (problem) return t(CHORD_PROBLEM_KEY[problem]);
      if (quickSwitch.enabled && sameChord(next, quickSwitch.combo)) {
        return t("settings_view.app_zoom.problem_quick_switch");
      }
      return null;
    },
    [id, bindings, appZoom, quickSwitch.enabled, quickSwitch.combo, t],
  );
  const onChange = useCallback((combo: string) => setBinding(id, combo), [id, setBinding]);

  return (
    <CharacterChordRow
      rowTestId={`shortcut-row-${id}`}
      testIdSuffix={id}
      title={t(LABEL_OVERRIDE[shortcut.labelKey] ?? shortcut.labelKey)}
      scope={shortcut.scope}
      combo={bindings[id]}
      fallback={defaultAppChords()[id]}
      problemFor={problemFor}
      onChange={onChange}
    />
  );
}

function AppshotRow({
  field,
  labelKey,
  settings,
  onSaved,
  isMac,
}: {
  field: AppshotField;
  labelKey: string;
  /** undefined while loading, null when the settings could not be read. */
  settings: AppshotSettings | null | undefined;
  onSaved: (settings: AppshotSettings) => void;
  isMac: boolean;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const hotkey = settings?.[field] ?? "";

  const save = async (next: string) => {
    try {
      onSaved(await saveAppshotSettings({ [field]: next } as AppshotSettingsPatch));
    } catch (error) {
      pushToast("error", t("appshots.save_error").replace("{0}", (error as Error).message));
    }
  };

  return (
    <ShortcutListRow
      testId={`shortcut-row-appshot-${field}`}
      title={t(labelKey)}
      scope="global"
      chord={
        !settings ? (
          <NoKeys>—</NoKeys>
        ) : !settings.enabled ? (
          <NoKeys>{t("shortcut_overlay.off")}</NoKeys>
        ) : hotkey ? (
          <KeyCaps caps={formatAppshotHotkey(hotkey, isMac).split(" + ")} />
        ) : (
          <NoKeys>{t("shortcut_overlay.unassigned")}</NoKeys>
        )
      }
      actions={
        <EditToggle
          open={open}
          disabled={!settings}
          onToggle={() => setOpen((o) => !o)}
          testId={`shortcuts-edit-appshot-${field}`}
        />
      }
    >
      {open && settings && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-md border border-border bg-background p-3">
          <AppshotShortcutField
            value={hotkey}
            isMac={isMac}
            disabled={false}
            testId={`shortcuts-appshot-field-${field}`}
            label={t(labelKey)}
            className="w-44"
            onSave={save}
            onStatus={setStatus}
          />
          <p className="min-w-0 flex-1 text-micro text-muted-foreground">
            {status ?? t("shortcuts_view.appshot_record_hint")}
          </p>
        </div>
      )}
    </ShortcutListRow>
  );
}

function ShortcutList({ keybinds }: { keybinds: Keybinds }) {
  const t = useT();
  const isMac = detectKeyboardPlatform() === "mac";
  const [appshots, setAppshots] = useState<AppshotSettings | null | undefined>(undefined);

  useEffect(() => {
    let alive = true;
    fetchAppshotSettings()
      .then((settings) => {
        if (alive) setAppshots(settings);
      })
      .catch((error: unknown) => {
        // The rows stay readable without it: they show "—" instead of keys.
        console.warn("[shortcuts] appshot settings unavailable", error);
        if (alive) setAppshots(null);
      });
    return () => {
      alive = false;
    };
  }, []);

  const rows = LIST_ENTRIES.map((entry) => {
    if (entry.kind === "appshot") {
      return (
        <AppshotRow
          key={`appshot-${entry.field}`}
          field={entry.field}
          labelKey={entry.labelKey}
          settings={appshots}
          onSaved={setAppshots}
          isMac={isMac}
        />
      );
    }
    const { shortcut } = entry;
    if (shortcut.kind === "rebindable") {
      return <VoiceKeyRow key={shortcut.labelKey} shortcut={shortcut} keybinds={keybinds} />;
    }
    if (shortcut.setting === "quick_switch") {
      return <QuickSwitchRow key={shortcut.labelKey} shortcut={shortcut} voiceConfig={keybinds.config} />;
    }
    const zoomIntent = APP_ZOOM_SETTING[shortcut.setting];
    if (zoomIntent) {
      return <AppZoomChordRow key={shortcut.labelKey} intent={zoomIntent} title={t(shortcut.labelKey)} />;
    }
    return <AppChordRow key={shortcut.labelKey} shortcut={shortcut} id={shortcut.setting as AppChordId} />;
  });

  return (
    <section data-testid="shortcuts-list" className="rounded-xl border border-border bg-card">
      <header className="flex items-center justify-between gap-4 border-b border-border px-5 py-3">
        <h2 className="text-sm font-semibold text-foreground">{t("shortcuts_view.list_title")}</h2>
        <span className="text-micro tabular-nums text-muted-foreground" data-testid="shortcuts-count">
          {t("shortcuts_view.list_count").replace("{count}", String(rows.length))}
        </span>
      </header>
      {keybinds.error && <p className="px-5 pt-3 text-sm text-destructive">{keybinds.error}</p>}
      <ul className="divide-y divide-border">{rows}</ul>
    </section>
  );
}

function OptionsCard({ voiceConfig }: { voiceConfig: KeybindsConfig | null }) {
  const t = useT();
  return (
    <section data-testid="shortcuts-options" className="rounded-xl border border-border bg-card">
      <header className="border-b border-border px-5 py-3">
        <h2 className="text-sm font-semibold text-foreground">{t("shortcuts_view.options_title")}</h2>
      </header>
      <div className="divide-y divide-border">
        <div className="px-5 py-4">
          <QuickSwitchKeybind voiceConfig={voiceConfig} part="toggle" />
        </div>
        <div className="px-5 py-4">
          <AppZoomKeybinds />
        </div>
      </div>
    </section>
  );
}

export function ShortcutsView() {
  const t = useT();
  // One read of the voice keybinds for the tester, the list and the quick
  // switcher's duplicate check; a save anywhere refetches it.
  const keybinds = useKeybinds();

  return (
    <div
      data-testid="shortcuts-view"
      className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis"
    >
      <div className="w-full">
        <PageHeader
          icon={<Keyboard />}
          title={t("shortcuts_view.title")}
          description={t("shortcuts_view.description")}
        />
        <div className="flex flex-col gap-6">
          <KeyTester config={keybinds.config} />
          <ShortcutList keybinds={keybinds} />
          <OptionsCard voiceConfig={keybinds.config} />
        </div>
      </div>
    </div>
  );
}
