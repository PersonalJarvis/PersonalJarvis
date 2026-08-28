/**
 * The `?` overlay: every keyboard shortcut the app is willing to teach.
 *
 * The app had shortcuts and no way to find them. This renders them from
 * `@/lib/shortcutRegistry` — the single list — and resolves the rebindable ones
 * through `useKeybinds()`, so what a reader sees is the combo their machine has
 * registered right now, not the shipped default. That is the difference between
 * a useful overlay and a misleading one.
 *
 * Escape and focus restoration are Radix Dialog's, not ours: it returns focus
 * to whatever was focused before the dialog opened, which is exactly the
 * behaviour required and is easy to get subtly wrong by hand.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Keyboard, X } from "lucide-react";
import { useKeybinds } from "@/hooks/useHotkey";
import { detectKeyboardPlatform } from "@/views/settings/keyboardLayout";
import {
  SHORTCUT_AREAS,
  keyLabel,
  shortcutsForArea,
  type Shortcut,
  type ShortcutArea,
} from "@/lib/shortcutRegistry";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/** One keycap. Colours come from theme tokens, so it reads in both modes. */
function Cap({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className={cn(
        "inline-flex min-w-[1.75rem] items-center justify-center rounded-md border border-border",
        "bg-muted px-1.5 py-0.5 font-mono text-[11px] font-medium text-foreground",
        "shadow-[inset_0_-1px_0_rgba(0,0,0,0.12)]",
      )}
    >
      {children}
    </kbd>
  );
}

function Chord({ keys, isMac }: { keys: string[]; isMac: boolean }) {
  return (
    <span className="inline-flex items-center gap-1">
      {keys.map((token, i) => (
        <span key={i} className="inline-flex items-center gap-1">
          {i > 0 && <span className="text-[10px] text-muted-foreground">+</span>}
          <Cap>{keyLabel(token, isMac)}</Cap>
        </span>
      ))}
    </span>
  );
}

/**
 * Split a saved combo ("ctrl+shift+j") into caps.
 *
 * The backend stores combos lowercase and `+`-joined; capitalising each token
 * for display is presentation only and never written back.
 */
function comboTokens(combo: string): string[] {
  return combo
    .split("+")
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => (t.length === 1 ? t.toUpperCase() : t[0].toUpperCase() + t.slice(1)));
}

function ShortcutRow({ shortcut, isMac }: { shortcut: Shortcut; isMac: boolean }) {
  const t = useT();
  const { config } = useKeybinds();

  let chord: React.ReactNode;
  if (shortcut.kind === "fixed") {
    chord = (
      <div className="flex flex-col items-end gap-1">
        <Chord keys={shortcut.keys} isMac={isMac} />
        {shortcut.alternateKeys?.map((alt, i) => (
          <div key={i} className="flex items-center gap-1.5 opacity-60">
            <span className="text-[10px] text-muted-foreground">
              {t("shortcut_overlay.or")}
            </span>
            <Chord keys={alt} isMac={isMac} />
          </div>
        ))}
      </div>
    );
  } else {
    // The live value, never the default — the whole point of resolving these
    // at render time. An unset action says so instead of showing a shipped
    // chord the machine has not registered.
    const combo = config?.keybinds?.[shortcut.action];
    chord = combo ? (
      <Chord keys={comboTokens(combo)} isMac={isMac} />
    ) : (
      <span className="text-[11px] italic text-muted-foreground">
        {t("shortcut_overlay.unassigned")}
      </span>
    );
  }

  return (
    <li className="flex items-center justify-between gap-4 py-2">
      <span className="min-w-0 text-xs text-foreground">{t(shortcut.labelKey)}</span>
      {chord}
    </li>
  );
}

function Section({ area, isMac }: { area: ShortcutArea; isMac: boolean }) {
  const t = useT();
  const rows = shortcutsForArea(area);
  if (rows.length === 0) return null;
  return (
    <section>
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t(`shortcut_overlay.area.${area}`)}
      </h3>
      <ul className="divide-y divide-border/60">
        {rows.map((shortcut, i) => (
          <ShortcutRow key={i} shortcut={shortcut} isMac={isMac} />
        ))}
      </ul>
    </section>
  );
}

export function ShortcutOverlay({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const isMac = detectKeyboardPlatform() === "mac";

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-[#090909]/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="shortcut-overlay"
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex max-h-[min(88dvh,40rem)] w-[min(520px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border",
            "bg-card outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <div className="mb-1 flex items-center gap-2">
                <Keyboard className="h-4 w-4 text-primary" aria-hidden="true" />
                <Dialog.Title className="font-display text-base font-semibold tracking-tight text-foreground">
                  {t("shortcut_overlay.title")}
                </Dialog.Title>
              </div>
              <Dialog.Description className="text-xs leading-relaxed text-muted-foreground">
                {t("shortcut_overlay.description")}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label={t("shortcut_overlay.close")}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </Dialog.Close>
          </header>

          <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-5 scrollbar-jarvis">
            {SHORTCUT_AREAS.map((area) => (
              <Section key={area} area={area} isMac={isMac} />
            ))}
          </div>

          <footer className="border-t border-border px-5 py-3 text-[11px] text-muted-foreground">
            {t("shortcut_overlay.footer")}
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
