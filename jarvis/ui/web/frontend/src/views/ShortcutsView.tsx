/**
 * Keyboard shortcuts — a Settings-hub page of its own.
 *
 * Every shortcut Jarvis answers to, on one page: the ones edited here (the
 * quick switcher, Call and Hang up), the dictation keys that are edited in the
 * voice section and only listed here with a way there, and the fixed chords of
 * the workspace. On top sits a key tester: press anything and it shows the
 * keys big, plus what — if anything — they do in Jarvis. It answers "is this
 * combination free?" before someone records it.
 *
 * The tester resolves through the REAL matchers (the quick switcher's chord
 * matcher, the terminal zoom matcher, the voice keybind normalisation), never
 * through a copied list, so it cannot tell a different story than the app.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowUpRight, AppWindow, Keyboard, Mic, Phone, Sparkles, SquareTerminal } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { ShortcutsStatusNote } from "@/components/permissions/ShortcutsStatusNote";
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
import { ACTION_LABEL_KEY, ComboChips, formatCombo } from "@/views/settings/KeybindRow";
import { KeybindsPanel } from "@/views/settings/KeybindsPanel";
import { QuickSwitchKeybind } from "@/views/settings/QuickSwitchKeybind";
import { eventMatchesChord } from "@/lib/quickSwitchChord";
import { isTextEntryTarget } from "@/lib/shortcutOverlayTrigger";
import { keyLabel, shortcutsForArea, type FixedShortcut } from "@/lib/shortcutRegistry";
import { zoomIntentFor } from "@/components/agentic/terminalZoom";
import { cn } from "@/lib/utils";

const DICTATION_ACTIONS: readonly KeybindAction[] = ["dictate", "dictate_toggle", "paste_last"];

const ZOOM_LABEL = {
  in: "shortcut_overlay.workspace.zoom_in",
  out: "shortcut_overlay.workspace.zoom_out",
  reset: "shortcut_overlay.workspace.zoom_reset",
} as const;

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

/** A small keycap for a fixed chord ("Mod" drawn as ⌘ or Ctrl). */
function SmallCaps({ keys, isMac }: { keys: string[]; isMac: boolean }) {
  return (
    <span className="inline-flex items-center gap-1">
      {keys.map((token, i) => (
        <span key={i} className="inline-flex items-center gap-1">
          {i > 0 && <span className="text-muted-foreground/50">+</span>}
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-micro text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.35)]">
            {keyLabel(token, isMac)}
          </kbd>
        </span>
      ))}
    </span>
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
      const zoom = zoomIntentFor(event, { isMac });
      if (quickSwitch.enabled && quickSwitch.combo && eventMatchesChord(event, quickSwitch.combo)) {
        meaningKey = "settings_view.quick_switch.title";
      } else if (zoom) {
        meaningKey = ZOOM_LABEL[zoom];
      } else if (event.key === "?" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        meaningKey = "shortcut_overlay.workspace.open_overlay";
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
  }, [config, quickSwitch.enabled, quickSwitch.combo, isMac]);

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

// ── Sections ────────────────────────────────────────────────────────────────

function SectionCard({
  icon,
  title,
  hint,
  action,
  children,
  testId,
}: {
  icon: ReactNode;
  title: string;
  hint: string;
  action?: ReactNode;
  children: ReactNode;
  testId: string;
}) {
  return (
    <section data-testid={testId} className="flex flex-col rounded-xl border border-border bg-card">
      <header className="flex items-start gap-3 border-b border-border px-5 py-4">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-secondary text-foreground [&>svg]:h-[18px] [&>svg]:w-[18px]">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">{hint}</p>
        </div>
        {action}
      </header>
      <div className="flex-1 px-5 py-4">{children}</div>
    </section>
  );
}

function ListRow({ label, chord }: { label: string; chord: ReactNode }) {
  return (
    <li className="flex items-center justify-between gap-4 py-2.5">
      <span className="min-w-0 text-sm text-foreground">{label}</span>
      <span className="flex shrink-0 flex-wrap items-center justify-end gap-1">{chord}</span>
    </li>
  );
}

function DictationList({ config }: { config: KeybindsConfig | null }) {
  const t = useT();
  return (
    <ul className="divide-y divide-border">
      {DICTATION_ACTIONS.map((action) => {
        const combo = config?.keybinds?.[action];
        return (
          <ListRow
            key={action}
            label={t(ACTION_LABEL_KEY[action])}
            chord={
              combo ? (
                <ComboChips combo={combo} />
              ) : (
                <span className="text-sm italic text-muted-foreground">
                  {t("shortcut_overlay.unassigned")}
                </span>
              )
            }
          />
        );
      })}
    </ul>
  );
}

function WorkspaceList() {
  const t = useT();
  const isMac = detectKeyboardPlatform() === "mac";
  const rows = useMemo(
    () => shortcutsForArea("workspace").filter((s): s is FixedShortcut => s.kind === "fixed"),
    [],
  );
  return (
    <ul className="divide-y divide-border">
      {rows.map((row) => (
        <ListRow key={row.labelKey} label={t(row.labelKey)} chord={<SmallCaps keys={row.keys} isMac={isMac} />} />
      ))}
    </ul>
  );
}

export function ShortcutsView() {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  // One read of the voice keybinds for the tester, the list and the quick
  // switcher's duplicate check; a save anywhere refetches it.
  const { config, refetch } = useKeybinds();

  return (
    <div
      data-testid="shortcuts-view"
      className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis"
    >
      <div className="w-full max-w-[1200px]">
        <PageHeader
          icon={<Keyboard />}
          title={t("shortcuts_view.title")}
          description={t("shortcuts_view.description")}
        />
        <div className="flex flex-col gap-6">
          {/* One sentence for the whole page when macOS has not allowed global
              shortcuts yet (the tester below still works inside this window). */}
          <ShortcutsStatusNote
            status={config?.shortcuts_status}
            onChanged={() => void refetch()}
            className="rounded-xl border border-border bg-card px-5 py-4"
          />
          <KeyTester config={config} />
          <div className="grid gap-6 xl:grid-cols-2">
            <SectionCard
              testId="shortcuts-section-app"
              icon={<Sparkles />}
              title={t("shortcuts_view.section_app")}
              hint={t("shortcuts_view.section_app_hint")}
            >
              <QuickSwitchKeybind voiceConfig={config} />
            </SectionCard>
            <SectionCard
              testId="shortcuts-section-calls"
              icon={<Phone />}
              title={t("shortcuts_view.section_calls")}
              hint={t("shortcuts_view.section_calls_hint")}
            >
              <KeybindsPanel bare />
            </SectionCard>
            <SectionCard
              testId="shortcuts-section-dictation"
              icon={<Mic />}
              title={t("shortcuts_view.section_dictation")}
              hint={t("shortcuts_view.section_dictation_hint")}
              action={
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  data-testid="shortcuts-edit-dictation"
                  onClick={() => setActiveSection("voice-shortcuts")}
                >
                  {t("shortcuts_view.edit_in_voice")}
                  <ArrowUpRight aria-hidden />
                </Button>
              }
            >
              <DictationList config={config} />
            </SectionCard>
            <SectionCard
              testId="shortcuts-section-workspace"
              icon={<SquareTerminal />}
              title={t("shortcuts_view.section_workspace")}
              hint={t("shortcuts_view.section_workspace_hint")}
            >
              <WorkspaceList />
            </SectionCard>
          </div>
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <AppWindow className="h-4 w-4 shrink-0" aria-hidden />
            {t("shortcuts_view.footer")}
          </p>
        </div>
      </div>
    </div>
  );
}
