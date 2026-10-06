import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Star } from "lucide-react";

import { Combobox, type ComboboxGroup, type ComboboxOption } from "@/components/ui/combobox";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The front page's brain pick: which provider and which model answer.
 *
 * The open panel has two columns. On the left a rail with one mark per
 * offered provider (plus a star for the models a person keeps reaching for);
 * on the right a search box and the models of the provider picked in the
 * rail, each row saying whose model it is under its name. Typing searches
 * every provider at once, so the rail is for browsing and never hides a
 * model from someone who knows its name.
 *
 * It opens on the provider that is answering now, so the current model is
 * one glance away. Favourites live in this browser only — a convenience,
 * not a setting, so a cleared storage just empties the star tab.
 */

export interface BrainSection {
  id: string;
  label: string;
  icon: ReactNode;
  /** Shown greyed: the provider is offered but not connected. */
  muted?: boolean;
}

const FAVORITES = "favorites";
const FAVORITES_KEY = "jarvis.chat.favoriteModels";

function readFavorites(): string[] {
  try {
    const raw = window.localStorage.getItem(FAVORITES_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    // Blocked or corrupt storage: start without favourites rather than fail the picker.
    return [];
  }
}

function writeFavorites(values: string[]): void {
  try {
    window.localStorage.setItem(FAVORITES_KEY, JSON.stringify(values));
  } catch {
    // Storage refused the write (private window, quota): the star still
    // toggles for this visit, it just will not be remembered.
  }
}

export function useFavoriteModels(): [string[], (value: string) => void] {
  const [favorites, setFavorites] = useState<string[]>(readFavorites);
  const toggle = useCallback((value: string) => {
    setFavorites((current) => {
      const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
      writeFavorites(next);
      return next;
    });
  }, []);
  return [favorites, toggle];
}

export function ComposerBrainPicker({
  value,
  groups,
  sections,
  currentSection,
  onChange,
  ariaLabel,
  fallbackLabel,
  searchPlaceholder,
  triggerPrefix,
  disabled,
  title,
  className,
  chevron = true,
  testId = "composer-model",
}: {
  value: string;
  /** One group per provider, its id equal to that provider's section id. */
  groups: ComboboxGroup[];
  sections: BrainSection[];
  /** The section the panel opens on — the provider answering now. */
  currentSection: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  fallbackLabel: string;
  searchPlaceholder: string;
  triggerPrefix?: ReactNode;
  disabled?: boolean;
  title?: string;
  className?: string;
  chevron?: boolean;
  /** Lands on the trigger; the panel gets `${testId}-panel`. */
  testId?: string;
}) {
  const t = useT();
  const [favorites, toggleFavorite] = useFavoriteModels();
  const [section, setSection] = useState(currentSection);

  // Every row carries its star; the favourites tab lists the starred rows in
  // the order they were starred.
  const starred = useMemo<ComboboxGroup[]>(() => {
    const withStar = (option: ComboboxOption): ComboboxOption => {
      const on = favorites.includes(option.value);
      const label = on ? t("agent_chat.favorite_remove") : t("agent_chat.favorite_add");
      return {
        ...option,
        trailing: (
          <button
            type="button"
            aria-label={label}
            aria-pressed={on}
            title={label}
            data-testid="composer-model-star"
            onClick={(event) => {
              event.stopPropagation();
              toggleFavorite(option.value);
            }}
            className={cn(
              "-my-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-sheen/[0.08]",
              on ? "text-warning" : "text-faint-foreground hover:text-foreground",
            )}
          >
            <Star className={cn("h-3.5 w-3.5", on && "fill-current")} aria-hidden />
          </button>
        ),
      };
    };
    return groups.map((group) => ({
      ...group,
      options: group.options.map(withStar),
      more: group.more && { ...group.more, options: group.more.options.map(withStar) },
    }));
  }, [groups, favorites, toggleFavorite, t]);

  const browseGroups = useMemo<ComboboxGroup[]>(() => {
    if (section === FAVORITES) {
      const all = starred.flatMap((group) => [...group.options, ...(group.more?.options ?? [])]);
      const options = favorites
        .map((fav) => all.find((option) => option.value === fav))
        .filter((option): option is NonNullable<typeof option> => Boolean(option));
      return options.length ? [{ id: FAVORITES, options }] : [];
    }
    const own = starred.find((group) => group.id === section) ?? starred[0];
    // The rail already names the provider, so its group needs no heading.
    return own ? [{ ...own, label: undefined }] : [];
  }, [section, starred, favorites]);

  const onOpenChange = useCallback(
    (open: boolean) => {
      if (open) setSection(sections.some((s) => s.id === currentSection) ? currentSection : (sections[0]?.id ?? ""));
    },
    [sections, currentSection],
  );

  const rail = (
    <div
      role="toolbar"
      aria-orientation="vertical"
      aria-label={t("agent_chat.pick_provider")}
      data-testid="composer-model-rail"
      className="scrollbar-jarvis flex w-12 shrink-0 flex-col gap-1 overflow-y-auto border-r border-border bg-sheen/[0.03] p-1.5"
    >
      <RailButton
        active={section === FAVORITES}
        label={t("agent_chat.favorites")}
        onSelect={() => setSection(FAVORITES)}
        testId="composer-model-rail-favorites"
      >
        <Star className="h-4 w-4 fill-current" aria-hidden />
      </RailButton>
      <span className="mx-1 my-0.5 border-b border-border" aria-hidden />
      {sections.map((s) => (
        <RailButton
          key={s.id}
          active={section === s.id}
          label={s.label}
          muted={s.muted}
          onSelect={() => setSection(s.id)}
          testId={`composer-model-rail-${s.id}`}
        >
          <span className="inline-flex scale-[1.3]">{s.icon}</span>
        </RailButton>
      ))}
    </div>
  );

  return (
    <span className={cn("inline-flex min-w-0 items-center", className)} title={title}>
      <Combobox
        value={value}
        groups={starred}
        browseGroups={browseGroups}
        aside={rail}
        onOpenChange={onOpenChange}
        onChange={onChange}
        ariaLabel={ariaLabel}
        fallbackLabel={fallbackLabel}
        searchPlaceholder={searchPlaceholder}
        emptyLabel={
          section === FAVORITES && favorites.length === 0
            ? t("agent_chat.favorites_empty")
            : t("agent_chat.no_models")
        }
        disabled={disabled}
        testId={testId}
        triggerHint={false}
        triggerIcon={false}
        triggerPrefix={triggerPrefix}
        chevron={chevron}
        panelMinWidth={360}
        className={COMPOSER_CONTROL_CLASS}
      />
    </span>
  );
}

export function RailButton({
  active,
  label,
  muted = false,
  onSelect,
  testId,
  children,
}: {
  active: boolean;
  label: string;
  muted?: boolean;
  onSelect: () => void;
  testId: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      title={label}
      data-testid={testId}
      onClick={(event) => {
        onSelect();
        // Back to the search box, so typing right after a rail click searches.
        event.currentTarget
          .closest("[data-combobox-panel]")
          ?.querySelector<HTMLInputElement>("input")
          ?.focus({ preventScroll: true });
      }}
      className={cn(
        "relative flex aspect-square w-full shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-sheen/[0.08] hover:text-foreground",
        active && "bg-sheen/[0.06] text-foreground",
        muted && "opacity-50",
      )}
    >
      {active && (
        <span
          aria-hidden
          className="absolute -right-1.5 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-l-full bg-accent"
        />
      )}
      {children}
    </button>
  );
}

/**
 * The look every pick on the composer's bottom row shares: a borderless,
 * medium-weight word with a quiet chevron that lifts on hover.
 */
export const COMPOSER_CONTROL_CLASS =
  "h-8 w-auto max-w-[240px] gap-1.5 rounded-lg border-transparent bg-transparent px-2 py-0 text-sm font-medium text-foreground/80 shadow-none hover:border-transparent hover:bg-secondary hover:text-foreground focus-visible:ring-1 aria-expanded:bg-secondary aria-expanded:text-foreground aria-expanded:ring-0 [&>svg:last-child]:h-3.5 [&>svg:last-child]:w-3.5";
