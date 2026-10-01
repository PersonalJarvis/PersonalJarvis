/**
 * The search bar at the top of the sidebar — the visible door into the quick
 * switcher, in the spot the assistant's name row used to take.
 *
 * It reads and behaves like a search field: click it, press Enter or an arrow,
 * or simply start typing while it has focus. The first character typed opens
 * the switcher with that character already in its field, so a fast typist
 * loses nothing. It reads "Search": the assistant's name beside the chord hint
 * and the voice dot was cut to "Search Ge…" at the default width. The
 * accessible name still says what is searched ("Search George"). The voice
 * status dot (plus the dev-instance tag) sits at its right edge.
 *
 * A button rather than a real <input>: the switcher's own field is where text
 * is edited, and a second editable field that hands its text over on every
 * key would fight it for focus. `role="search"` keeps it a search landmark.
 */
import type { ReactNode } from "react";
import { Search } from "lucide-react";
import { useT } from "@/i18n";
import { useQuickSwitcher } from "@/store/quickSwitcher";
import { useQuickSwitchSettings } from "@/store/quickSwitchSettings";
import { chordCaps } from "@/lib/quickSwitchChord";
import { cn } from "@/lib/utils";

export function SidebarSearchBar({
  assistantName,
  status,
}: {
  assistantName: string;
  /** The voice dot / spinner and the dev tag, drawn at the bar's right edge. */
  status?: ReactNode;
}) {
  const t = useT();
  const show = useQuickSwitcher((s) => s.show);
  const combo = useQuickSwitchSettings((s) => s.combo);
  const caps = combo ? chordCaps(combo) : [];
  const accessibleName = t("quick_switch.sidebar_placeholder").replace("{name}", assistantName);

  return (
    <div role="search" className="min-w-0 flex-1">
      <button
        type="button"
        data-testid="sidebar-search"
        aria-label={accessibleName}
        title={accessibleName}
        onClick={() => show()}
        onKeyDown={(event) => {
          if (event.ctrlKey || event.metaKey || event.altKey) return;
          // A printable key opens the switcher with that key already typed.
          if (event.key.length === 1 && event.key !== " ") {
            event.preventDefault();
            show(event.key);
          } else if (event.key === "ArrowDown") {
            event.preventDefault();
            show();
          }
        }}
        className={cn(
          "group flex h-8 w-full items-center gap-2 rounded-lg border border-border bg-secondary/60 px-2.5",
          "text-left text-sm text-muted-foreground transition-colors",
          "hover:border-border-strong hover:bg-secondary hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1 truncate">{t("quick_switch.sidebar_search")}</span>
        {status}
        {caps.length > 0 && (
          <span className="hidden shrink-0 items-center gap-0.5 sm:inline-flex" aria-hidden>
            {caps.map((cap) => (
              <kbd
                key={cap}
                className="rounded border border-border bg-background px-1 font-sans text-[10px] leading-4 text-muted-foreground"
              >
                {cap}
              </kbd>
            ))}
          </span>
        )}
      </button>
    </div>
  );
}
