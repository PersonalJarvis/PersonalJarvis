import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * A themed replacement for `<select>` — a trigger that looks like the rest of
 * the app plus a searchable, groupable list panel.
 *
 * The reason it exists: a native `<select>` renders its list with the operating
 * system's own widget, which no stylesheet can reach. On this app's matte-black
 * surface a Windows dropdown drops a light-grey system list with a grey
 * highlight bar into the middle of the screen — visibly somebody else's chrome
 * bolted on, the same complaint that made the app draw its own scrollbars. And
 * on a hundred-entry list (recognition languages) the native widget offers no
 * search, no grouping and no secondary text, so finding your own language means
 * scrolling past ninety others.
 *
 * Deliberately not a `cmdk` command palette: cmdk re-ranks by fuzzy score,
 * which dissolves the group order this control is built around. The list here
 * filters and never reorders, so "the third entry" stays the third entry.
 *
 * The panel is portalled to `document.body` and positioned fixed, because every
 * screen that uses it sits inside an `overflow-y-auto` column that would
 * otherwise clip it.
 */

export interface ComboboxOption {
  value: string;
  label: string;
  /** Secondary text on the right — an endonym, a short qualifier. */
  hint?: string;
  /** Extra text a matcher may search but that is never rendered. */
  searchText?: string;
  icon?: ReactNode;
  /** Listed for context, but cannot be selected. */
  disabled?: boolean;
}

export interface ComboboxGroup {
  id: string;
  /** Rendered as a small caps heading; omit for an ungrouped lead band. */
  label?: string;
  options: ComboboxOption[];
}

export interface ComboboxProps {
  value: string;
  groups: ComboboxGroup[];
  onChange: (value: string) => void;
  /** Accessible name — this control has no visible <label> of its own. */
  ariaLabel: string;
  /** Shown when the stored value is not in any group (stale or not yet loaded). */
  fallbackLabel?: string;
  /** Setting this turns on the search field. */
  searchPlaceholder?: string;
  /** Shown in place of the list when the query matches nothing. */
  emptyLabel?: string;
  /** Custom matcher; defaults to a plain accent-folded substring test. */
  matches?: (option: ComboboxOption, query: string) => boolean;
  disabled?: boolean;
  className?: string;
  id?: string;
  ariaDescribedBy?: string;
  /** Lands on the trigger button; the panel gets `${testId}-panel`. */
  testId?: string;
}

/** Panel height cap — roughly nine rows, enough to show that more exist. */
const MAX_PANEL_HEIGHT = 340;
const MIN_PANEL_WIDTH = 288;
const VIEWPORT_MARGIN = 8;

function fold(value: string): string {
  return value
    .toLocaleLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");
}

function defaultMatches(option: ComboboxOption, query: string): boolean {
  const needle = fold(query).trim();
  if (!needle) return true;
  const haystack = fold(
    [option.label, option.hint, option.searchText, option.value]
      .filter(Boolean)
      .join(" "),
  );
  return needle.split(/\s+/).every((word) => haystack.includes(word));
}

interface PanelPosition {
  left: number;
  top: number;
  width: number;
  maxHeight: number;
}

interface OptionOccurrence {
  option: ComboboxOption;
  id: string;
}

function optionOccurrenceId(
  listId: string,
  groupId: string,
  groupIndex: number,
  optionIndex: number,
  value: string,
): string {
  return `${listId}-option-${encodeURIComponent(groupId)}-${groupIndex}-${optionIndex}-${encodeURIComponent(value)}`;
}

export function Combobox({
  value,
  groups,
  onChange,
  ariaLabel,
  fallbackLabel,
  searchPlaceholder,
  emptyLabel,
  matches = defaultMatches,
  disabled = false,
  className,
  id,
  ariaDescribedBy,
  testId,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<PanelPosition | null>(null);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  // Assigned from callback refs below, hence explicitly nullable/mutable.
  const searchRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const listId = useId();

  const searchable = Boolean(searchPlaceholder);

  const selected = useMemo(() => {
    for (const group of groups) {
      for (const option of group.options) {
        if (option.value === value) return option;
      }
    }
    return null;
  }, [groups, value]);

  const visibleGroups = useMemo(() => {
    if (!query.trim()) return groups;
    // Searching collapses the bands into one flat result list. Groups are a
    // browsing aid, and a shortlist band deliberately repeats entries that are
    // also in the full list below it — which is fine while browsing and reads
    // as a bug the moment a search narrows both bands to the same single row.
    // First occurrence wins, so the shortlist still decides what ranks first.
    const seen = new Set<string>();
    const hits: ComboboxOption[] = [];
    for (const group of groups) {
      for (const option of group.options) {
        if (seen.has(option.value)) continue;
        if (!matches(option, query)) continue;
        seen.add(option.value);
        hits.push(option);
      }
    }
    return hits.length ? [{ id: "results", options: hits }] : [];
  }, [groups, matches, query]);

  // Flat order of concrete rendered occurrences — arrow keys walk this, not
  // merely option values. A shortlist may repeat the same logical value in the
  // full group, so every occurrence needs its own DOM id and active state.
  const flat = useMemo<OptionOccurrence[]>(
    () =>
      visibleGroups.flatMap((group, groupIndex) =>
        group.options.map((option, optionIndex) => ({
          option,
          id: optionOccurrenceId(
            listId,
            group.id,
            groupIndex,
            optionIndex,
            option.value,
          ),
        })),
      ),
    [listId, visibleGroups],
  );
  const enabled = useMemo(
    () => flat.filter(({ option }) => !option.disabled),
    [flat],
  );

  const measure = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const width = Math.min(
      Math.max(rect.width, MIN_PANEL_WIDTH),
      window.innerWidth - 2 * VIEWPORT_MARGIN,
    );
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_MARGIN;
    const spaceAbove = rect.top - VIEWPORT_MARGIN;
    // Flip upwards only when below is genuinely too cramped AND above is
    // roomier — a panel that jumps sides on a two-pixel scroll reads as a bug.
    const flipUp = spaceBelow < 220 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(
      160,
      Math.min(MAX_PANEL_HEIGHT, flipUp ? spaceAbove : spaceBelow),
    );
    const left = Math.min(
      Math.max(VIEWPORT_MARGIN, rect.left),
      Math.max(VIEWPORT_MARGIN, window.innerWidth - width - VIEWPORT_MARGIN),
    );
    const top = flipUp ? rect.top - maxHeight - 6 : rect.bottom + 6;
    setPosition({ left, top, width, maxHeight });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    measure();
    // Capture phase so the inner scroll containers every settings screen uses
    // are heard too, not just the window.
    const onScroll = () => measure();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
    };
  }, [open, measure]);

  // Focus moves into the panel via a callback ref, not an effect keyed on
  // `open`: the panel is not mounted on the render that flips `open`, because
  // it waits for the first measurement — so an effect would run against a ref
  // that is still null and leave typing going wherever the focus happened to
  // be. A callback ref fires exactly when the node appears, and once per open,
  // so repositioning on scroll does not yank focus back.
  const focusOnMount = useCallback((node: HTMLElement | null) => {
    node?.focus();
  }, []);

  const attachSearch = useCallback(
    (node: HTMLInputElement | null) => {
      searchRef.current = node;
      focusOnMount(node);
    },
    [focusOnMount],
  );

  const attachList = useCallback(
    (node: HTMLDivElement | null) => {
      listRef.current = node;
      // Without a search field the list itself has to take focus, or the arrow
      // keys and Enter below would never reach the panel at all.
      if (!searchable) focusOnMount(node);
    },
    [focusOnMount, searchable],
  );

  // Open on the current value so the list starts where the user left it.
  useEffect(() => {
    if (!open) return;
    const index = enabled.findIndex(({ option }) => option.value === value);
    setActiveIndex(index >= 0 ? index : 0);
    // Only on open: while typing, the query effect below owns the highlight.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(0);
  }, [query, open]);

  useEffect(() => {
    if (!open) return;
    const active = listRef.current?.querySelector<HTMLElement>(
      '[data-active="true"]',
    );
    // Optional-called: jsdom has no layout and does not implement this, and
    // keeping the highlight in view is a nicety, not a correctness condition.
    active?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, open]);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setQuery("");
    setPosition(null);
    if (refocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (panelRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      close(false);
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [open, close]);

  function commit(option: ComboboxOption) {
    if (option.disabled) return;
    close();
    if (option.value !== value) onChange(option.value);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "Tab") {
      close(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!enabled.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex(
        (index) => (index + step + enabled.length) % enabled.length,
      );
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActiveIndex(
        event.key === "Home" ? 0 : Math.max(0, enabled.length - 1),
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const occurrence = enabled[activeIndex];
      if (occurrence) commit(occurrence.option);
    }
  }

  function onTriggerKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!disabled) setOpen(true);
    }
  }

  const triggerLabel = selected?.label ?? fallbackLabel ?? value;
  const activeOption = enabled[activeIndex];

  return (
    <>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        aria-describedby={ariaDescribedBy}
        disabled={disabled}
        data-testid={testId}
        data-value={value}
        onClick={() => !disabled && setOpen((wasOpen) => !wasOpen)}
        onKeyDown={onTriggerKeyDown}
        className={cn(
          "flex w-full items-center gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm",
          "shadow-[inset_0_1px_0_hsl(var(--foreground)/0.04)] transition-[border-color,background-color,box-shadow]",
          "hover:border-primary/40 hover:bg-muted/20 focus:outline-none focus-visible:border-primary/60 focus-visible:ring-1 focus-visible:ring-primary/70",
          open && "border-primary/60 bg-primary/[0.03] ring-1 ring-primary/70",
          disabled && "cursor-not-allowed opacity-50",
          className,
        )}
      >
        {selected?.icon}
        <span className="min-w-0 flex-1 truncate">{triggerLabel}</span>
        {selected?.hint && (
          <span className="shrink-0 truncate text-xs text-muted-foreground">
            {selected.hint}
          </span>
        )}
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180 text-primary",
          )}
        />
      </button>

      {open &&
        position &&
        createPortal(
          <div
            ref={panelRef}
            data-testid={testId ? `${testId}-panel` : undefined}
            style={{
              left: position.left,
              top: position.top,
              width: position.width,
              maxHeight: position.maxHeight,
            }}
            onKeyDown={onKeyDown}
            className="fixed z-[70] flex flex-col overflow-hidden rounded-lg border border-primary/25 bg-popover/95 shadow-[0_22px_55px_-16px_rgba(0,0,0,0.9),inset_0_1px_0_hsl(var(--foreground)/0.05)] backdrop-blur-xl"
          >
            {searchable && (
              <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2">
                <Search
                  aria-hidden="true"
                  className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                />
                <input
                  ref={attachSearch}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={searchPlaceholder}
                  aria-label={searchPlaceholder}
                  aria-controls={listId}
                  aria-autocomplete="list"
                  aria-activedescendant={
                    activeOption?.id
                  }
                  data-testid={testId ? `${testId}-search` : undefined}
                  className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                />
              </div>
            )}

            <div
              ref={attachList}
              id={listId}
              role="listbox"
              aria-label={ariaLabel}
              aria-activedescendant={
                !searchable ? activeOption?.id : undefined
              }
              tabIndex={searchable ? -1 : 0}
              className="scrollbar-jarvis flex-1 overflow-y-auto p-1"
            >
              {flat.length === 0 && (
                <p
                  className="px-3 py-6 text-center text-xs text-muted-foreground"
                  data-testid={testId ? `${testId}-empty` : undefined}
                >
                  {emptyLabel ?? "—"}
                </p>
              )}

              {visibleGroups.map((group, groupIndex) => (
                <div key={group.id}>
                  {group.label && (
                    <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      {group.label}
                    </div>
                  )}
                  {group.options.map((option, optionIndex) => {
                    const occurrenceId = optionOccurrenceId(
                      listId,
                      group.id,
                      groupIndex,
                      optionIndex,
                      option.value,
                    );
                    const isActive = activeOption?.id === occurrenceId;
                    const isSelected = option.value === value;
                    return (
                      <div
                        key={option.value}
                        id={occurrenceId}
                        role="option"
                        aria-selected={isSelected}
                        aria-disabled={option.disabled || undefined}
                        data-active={isActive}
                        data-value={option.value}
                        // Pointer, not mouse: the highlight has to follow a
                        // pen or touch drag as well, and `onMouseMove` never
                        // fires for either.
                        onPointerMove={() => {
                          if (!option.disabled) {
                            setActiveIndex(
                              enabled.findIndex(({ id }) => id === occurrenceId),
                            );
                          }
                        }}
                        onClick={() => commit(option)}
                        className={cn(
                          "relative flex cursor-pointer items-center gap-2 rounded-md border border-transparent px-3 py-1.5 text-sm transition-colors",
                          isActive && "border-primary/15 bg-primary/10 text-foreground",
                          isSelected && "bg-primary/15 font-medium text-primary",
                          option.disabled &&
                            "cursor-not-allowed text-muted-foreground/45",
                        )}
                      >
                        {option.icon}
                        <span className="min-w-0 flex-1 truncate">
                          {option.label}
                        </span>
                        {option.hint && (
                          <span className="shrink-0 truncate text-xs text-muted-foreground">
                            {option.hint}
                          </span>
                        )}
                        {isSelected && (
                          <Check
                            aria-hidden="true"
                            className="h-3.5 w-3.5 shrink-0 text-primary"
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
