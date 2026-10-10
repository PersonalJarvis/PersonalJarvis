import { PanelBottom } from "lucide-react";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { drawerShown, useThreadTerminalsStore } from "@/store/threadTerminals";

/**
 * Pulls the thread layout's terminal drawer up and down.
 *
 * It sits at the right end of the window caption, before the side panel
 * toggle, and only while the IDE shows threads: the terminal grid is all
 * terminals already. The first press in a folder starts its first shell.
 * Kept free of the drawer's own module: the caption is in the startup chunk
 * and must not pull xterm into it.
 */
export function ThreadTerminalToggle({ className }: { className?: string }) {
  const t = useT();
  const shown = useThreadTerminalsStore(drawerShown);
  const available = useThreadTerminalsStore((state) => Boolean(state.folder));
  const toggle = useThreadTerminalsStore((state) => state.toggle);
  const label = !available ? t("ide_threads.terminal_needs_folder") : shown ? t("ide_threads.hide_terminal") : t("ide_threads.show_terminal");
  return (
    <button
      type="button"
      onClick={toggle}
      disabled={!available}
      title={label}
      aria-label={label}
      aria-pressed={shown}
      data-testid="thread-terminal-toggle"
      className={cn(
        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md",
        "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        "disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent",
        shown && "bg-secondary text-foreground",
        className,
      )}
    >
      <PanelBottom aria-hidden className="h-4 w-4" />
    </button>
  );
}
