/**
 * The quick switcher's Spotlight window — Ctrl+Space anywhere, type where you
 * want to go, Enter.
 *
 * Modelled on the Mac's Spotlight bar: one large field floating high on the
 * screen over a frosted panel, results directly underneath with the first one
 * already selected, the app still visible behind it (no dimming scrim). The
 * results themselves are `QuickSwitchList`, the same list the sidebar search
 * field drops down under itself (`InlineQuickSwitch`).
 *
 * Radix Dialog owns Escape, the focus trap and handing focus back to the pane
 * or field you came from.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { CornerDownLeft, Search } from "lucide-react";
import { useState } from "react";
import { useT } from "@/i18n";
import { QuickSwitchList } from "@/components/QuickSwitchList";
import { cn } from "@/lib/utils";

export function QuickSwitcher({
  open,
  onOpenChange,
  initialQuery = "",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Text to start with. */
  initialQuery?: string;
}) {
  const t = useT();
  const [query, setQuery] = useState(initialQuery);
  // Controlled so the top row can be re-selected when results arrive late.
  const [selected, setSelected] = useState("");

  const close = () => {
    onOpenChange(false);
    setQuery("");
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange(next);
      }}
    >
      <Dialog.Portal>
        {/* Spotlight never dims the desktop: the overlay only catches the
            click that closes it. */}
        <Dialog.Overlay className="fixed inset-0 z-[80]" />
        <Dialog.Content
          data-testid="quick-switcher"
          aria-describedby={undefined}
          className={cn(
            "fixed left-1/2 top-[18%] z-[90] w-[min(640px,calc(100vw-2rem))] -translate-x-1/2",
            "overflow-hidden rounded-2xl border border-border shadow-float",
            "backdrop-blur-2xl backdrop-saturate-150",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "motion-reduce:animate-none",
          )}
          style={{ backgroundColor: "hsl(var(--popover) / 0.86)" }}
        >
          <Dialog.Title className="sr-only">{t("quick_switch.title")}</Dialog.Title>
          <Command
            shouldFilter={false}
            loop
            label={t("quick_switch.title")}
            value={selected}
            onValueChange={setSelected}
          >
            <div className="flex items-center gap-3 px-4">
              <Search className="h-6 w-6 shrink-0 text-muted-foreground" aria-hidden />
              <Command.Input
                autoFocus
                value={query}
                onValueChange={setQuery}
                placeholder={t("quick_switch.placeholder")}
                data-testid="quick-switcher-input"
                className="h-14 flex-1 bg-transparent text-2xl font-light text-foreground outline-none placeholder:text-muted-foreground"
              />
            </div>
            <QuickSwitchList
              query={query}
              onDone={close}
              onFirstChange={setSelected}
              className="max-h-[min(30rem,62dvh)] border-t border-border"
            />
            <div className="flex items-center justify-end gap-4 border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">
                  <CornerDownLeft className="h-3 w-3" aria-hidden />
                </kbd>
                {t("quick_switch.hint_open")}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">esc</kbd>
                {t("quick_switch.hint_close")}
              </span>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
