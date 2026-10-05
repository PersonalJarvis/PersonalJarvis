import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import type { ProviderFamily } from "@/lib/providerFamilies";
import { cn } from "@/lib/utils";
import { familyStatusLine, type FamilyState, type FamilyUse } from "./familyState";

/**
 * The left column: every company once, its connection in one line, and a
 * small tag for each job it powers right now — so the list itself answers
 * "what runs where" before anything is opened.
 */
export function FamilyList({
  families,
  states,
  selectedId,
  onSelect,
}: {
  families: ProviderFamily[];
  states: Record<string, FamilyState>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const t = useT();
  return (
    <nav aria-label={t("providers_page.list_label")} data-testid="provider-family-list">
      <ul className="flex flex-col gap-0.5 p-2">
        {families.map((family) => {
          const state = states[family.id];
          if (!state) return null;
          const selected = family.id === selectedId;
          return (
            <li key={family.id}>
              <button
                type="button"
                data-testid={`provider-family-${family.id}`}
                aria-current={selected ? "true" : undefined}
                onClick={() => onSelect(family.id)}
                className={cn(
                  "group flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  selected ? "bg-secondary" : "hover:bg-secondary/60",
                )}
              >
                <ProviderLogo
                  providerId={family.logo_id}
                  label={family.label}
                  className={cn(!state.connected && !selected && "opacity-60")}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className={cn(
                        "truncate text-base font-medium",
                        state.connected ? "text-foreground-strong" : "text-muted-foreground",
                      )}
                    >
                      {family.label}
                    </span>
                    {state.failing && (
                      <span
                        role="status"
                        title={state.failing}
                        aria-label={t("providers_page.failing")}
                        className="h-2 w-2 shrink-0 rounded-full bg-destructive"
                      />
                    )}
                  </span>
                  <span
                    className={cn(
                      "mt-0.5 block truncate text-sm",
                      state.connected ? "text-muted-foreground" : "text-foreground-faint",
                    )}
                  >
                    {familyStatusLine(family, state, t)}
                  </span>
                  {state.uses.length > 0 && (
                    <span className="mt-1.5 flex flex-wrap gap-1">
                      {state.uses.map((use) => (
                        <UseTag key={use} use={use} />
                      ))}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function UseTag({ use }: { use: FamilyUse }) {
  const t = useT();
  return (
    <span
      data-testid={`provider-use-${use}`}
      className="inline-flex h-5 items-center rounded-md bg-accent-soft px-1.5 text-xs font-medium text-foreground"
    >
      {t(`providers_page.use_${use}`)}
    </span>
  );
}
