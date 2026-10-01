/**
 * The sidebar search field, live: type into it and the results drop down
 * right under it — no window in the middle of the screen. The Spotlight
 * window stays the chord's job (Ctrl+Space); both show the same
 * `QuickSwitchList`.
 *
 * Lazy on purpose: the result list carries every locale for its
 * cross-language search, and the sidebar is in the startup chunk. Until this
 * loads, `SidebarSearchBar` draws a plain field that looks identical.
 *
 * The dropdown is portalled to <body> with fixed coordinates taken from the
 * field, so it can be wider than the sidebar and no section's own layer can
 * cover it. cmdk only looks for its rows inside its list element, so the
 * portal does not break the arrow keys. A mousedown on the dropdown is
 * swallowed so the field keeps focus while a row is clicked.
 */
import { Command } from "cmdk";
import { Search } from "lucide-react";
import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/i18n";
import { QuickSwitchList } from "@/components/QuickSwitchList";
import { cn } from "@/lib/utils";

/** The dropdown is at least this wide even under a narrow sidebar. */
const PANEL_MIN_WIDTH = 420;
const VIEWPORT_GUTTER = 16;

export interface InlineQuickSwitchProps {
  initialValue: string;
  autoFocus: boolean;
  accessibleName: string;
  placeholder: string;
  status?: ReactNode;
  caps: string[];
  /** Keeps the plain fallback field in step with what is typed here. */
  onValueChange?: (value: string) => void;
}

export function InlineQuickSwitch({
  initialValue,
  autoFocus,
  accessibleName,
  placeholder,
  status,
  caps,
  onValueChange,
}: InlineQuickSwitchProps) {
  const t = useT();
  const fieldRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState(initialValue);
  const [open, setOpen] = useState(autoFocus);
  const [rect, setRect] = useState<DOMRect | null>(null);
  // Controlled so the top row can be re-selected when results arrive late.
  const [selected, setSelected] = useState("");

  const setValue = (value: string) => {
    setQuery(value);
    onValueChange?.(value);
    setOpen(true);
  };

  const measure = useCallback(() => {
    if (fieldRef.current) setRect(fieldRef.current.getBoundingClientRect());
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open, measure]);

  // Typed into the plain field before this loaded: keep the caret at the end.
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (autoFocus && input) {
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  }, [autoFocus]);

  const finish = () => {
    setValue("");
    setOpen(false);
    inputRef.current?.blur();
  };

  const width = rect
    ? Math.min(Math.max(rect.width, PANEL_MIN_WIDTH), window.innerWidth - rect.left - VIEWPORT_GUTTER)
    : PANEL_MIN_WIDTH;

  return (
    <Command
      shouldFilter={false}
      loop
      label={accessibleName}
      value={selected}
      onValueChange={setSelected}
      className="min-w-0 flex-1"
    >
      <div
        ref={fieldRef}
        className={cn(
          "flex h-8 w-full items-center gap-2 rounded-lg border px-2.5 text-sm transition-colors",
          open
            ? "border-accent bg-card ring-2 ring-ring"
            : "border-border bg-input hover:border-border-strong",
        )}
      >
        <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <Command.Input
          ref={inputRef}
          value={query}
          onValueChange={setValue}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            if (query) setValue("");
            else finish();
          }}
          placeholder={placeholder}
          aria-label={accessibleName}
          title={accessibleName}
          data-testid="sidebar-search"
          className="h-full min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
        />
        {status}
        {!open && caps.length > 0 && (
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
      </div>
      {open &&
        rect &&
        query.trim() !== "" &&
        createPortal(
          <div
            data-testid="sidebar-search-results"
            onMouseDown={(event) => event.preventDefault()}
            className={cn(
              "fixed z-[70] overflow-hidden rounded-xl border border-border bg-popover shadow-float",
              "animate-in fade-in-0 zoom-in-95 duration-100 motion-reduce:animate-none",
            )}
            style={{ top: rect.bottom + 6, left: rect.left, width }}
          >
            <QuickSwitchList
              query={query}
              onDone={finish}
              size="compact"
              onFirstChange={setSelected}
              className="max-h-[min(28rem,70dvh)]"
            />
            <p className="border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
              {t("quick_switch.inline_hint")}
            </p>
          </div>,
          document.body,
        )}
    </Command>
  );
}
