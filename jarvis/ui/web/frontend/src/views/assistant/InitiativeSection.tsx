/**
 * Initiative — how much the assistant brings up on its own, set by the person.
 *
 * Three levels as one radio group in the page's label-column grammar: the
 * level's name on the left, what it means on the right, a plain ring that
 * fills for the chosen one. Below it, while initiative is on, the dated plans
 * from the notebooks the assistant may bring up, so nothing it raises comes
 * as a surprise (each note can be forgotten in Memory).
 *
 * The level applies from the next reply on every surface (`[brain] proactivity`).
 */
import { useT, useUiLanguage, type UiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { useSetInitiative, type Initiative, type InitiativeLevel } from "@/views/assistant/api";
import { Section, SpecRow } from "@/views/assistant/Section";

const ORDER: readonly InitiativeLevel[] = ["off", "balanced", "high"];

/** "Fri, 9 Oct" for an ISO day; the raw text when it does not parse. */
export function upcomingDay(iso: string, lang: UiLanguage): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const day = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return day.toLocaleDateString(lang, { weekday: "short", day: "numeric", month: "short" });
}

export function InitiativeSection({ initiative }: { initiative: Initiative }) {
  const t = useT();
  const lang = useUiLanguage();
  const pushToast = useEventStore((s) => s.pushToast);
  const save = useSetInitiative();
  const pending = save.isPending ? save.variables : undefined;
  const current = pending ?? initiative.level;
  const levels = ORDER.filter((level) => initiative.levels.includes(level));

  const choose = (level: InitiativeLevel) => {
    if (level === initiative.level || save.isPending) return;
    save.mutate(level, { onError: (e) => pushToast("error", e.message) });
  };

  return (
    <Section testId="assistant-initiative" title={t("assistant_view.initiative_title")}>
      <p className="max-w-reading text-base leading-7 text-muted-foreground">{t("assistant_view.initiative_hint")}</p>
      <div
        role="radiogroup"
        aria-label={t("assistant_view.initiative_title")}
        className="mt-2 divide-y divide-border"
      >
        {levels.map((level) => {
          const selected = level === current;
          return (
            <button
              key={level}
              type="button"
              role="radio"
              aria-checked={selected}
              data-testid={`assistant-initiative-${level}`}
              disabled={save.isPending}
              onClick={() => choose(level)}
              className={cn(
                // SpecRow's grid, set on the button itself (a div may not sit in a button).
                "grid w-full grid-cols-1 gap-1 rounded-sm py-3.5 text-left sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-8",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                "disabled:cursor-wait",
              )}
            >
              <span className="flex items-center gap-2.5 text-base leading-7">
                <span
                  aria-hidden
                  className={cn(
                    "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border",
                    selected ? "border-foreground-strong" : "border-muted-foreground",
                  )}
                >
                  {selected && <span className="h-1.5 w-1.5 rounded-full bg-foreground-strong" />}
                </span>
                <span className={selected ? "font-medium text-foreground-strong" : "text-muted-foreground"}>
                  {t(`assistant_view.initiative_${level}`)}
                </span>
              </span>
              <span
                className={cn(
                  "min-w-0 max-w-reading text-base leading-7",
                  selected ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {t(`assistant_view.initiative_${level}_hint`)}
              </span>
            </button>
          );
        })}
      </div>
      {current !== "off" && initiative.upcoming.length > 0 && (
        <div className="border-t border-border" data-testid="assistant-initiative-upcoming">
          <SpecRow label={t("assistant_view.initiative_upcoming")}>
            <ul className="flex flex-col gap-2.5">
              {initiative.upcoming.map((note, i) => (
                <li key={`${note.date}-${i}`}>
                  <span className="font-medium text-foreground-strong">{upcomingDay(note.date, lang)}: </span>
                  <span className="text-foreground">{note.text}</span>
                </li>
              ))}
            </ul>
          </SpecRow>
        </div>
      )}
    </Section>
  );
}
