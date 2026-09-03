/**
 * The people the curator has written a file for.
 *
 * A side-by-side list and detail pane does not fit a 340 px rail, so the
 * detail opens inside the row instead. One click selects AND expands — the
 * row-click doctrine the rest of the app follows, with the selected row
 * carrying the accent bar every selected row in this product wears.
 */
import { useState } from "react";
import { ChevronDown, Inbox, Users } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { IdentityAvatar } from "@/components/identity/IdentityAvatar";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { PersonSummary } from "@/views/profile/api";

export function PeopleCard({ people }: { people: PersonSummary[] }) {
  const t = useT();
  const [openSlug, setOpenSlug] = useState<string | null>(null);

  return (
    <Card className="flex flex-col">
      <CardHeader className="flex-row items-center justify-between gap-3 pb-2">
        <CardTitle className="truncate">{t("profile_view.section_people")}</CardTitle>
        {people.length > 0 && (
          <Badge variant="secondary" className="shrink-0 tabular-nums">
            {people.length}
          </Badge>
        )}
      </CardHeader>

      <CardContent>
        {people.length === 0 ? (
          <EmptyState
            className="max-w-none py-6"
            icon={<Users />}
            title={t("profile_view.people_empty_title")}
            description={t("profile_view.people_empty_body")}
          />
        ) : (
          <ul className="flex flex-col">
            {people.map((p) => (
              <PersonRow
                key={p.slug}
                person={p}
                open={p.slug === openSlug}
                onToggle={() => setOpenSlug(p.slug === openSlug ? null : p.slug)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function PersonRow({
  person,
  open,
  onToggle,
}: {
  person: PersonSummary;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useT();

  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(
          "-mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-md px-2 py-2 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          open ? "bg-secondary jarvis-nav-active" : "hover:bg-secondary",
        )}
      >
        <IdentityAvatar name={person.name} size="sm" />
        <span className="min-w-0 flex-1">
          <span
            className={cn(
              "block truncate text-base",
              open ? "text-foreground-strong" : "text-foreground",
            )}
          >
            {person.name}
          </span>
          <span className="block truncate text-sm text-muted-foreground">
            {person.relationship}
          </span>
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="py-3 pl-11 pr-1">
          <dl className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="shrink-0 text-base text-muted-foreground">
                {t("profile_view.person_relationship")}
              </dt>
              <dd className="min-w-0 text-right text-base text-foreground [overflow-wrap:anywhere]">
                {person.relationship}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="shrink-0 text-base text-muted-foreground">
                {t("profile_view.person_aliases")}
              </dt>
              <dd className="min-w-0 text-right [overflow-wrap:anywhere]">
                {person.aliases.length === 0 ? (
                  <span className="text-base text-foreground-faint">
                    {t("profile_view.person_no_aliases")}
                  </span>
                ) : (
                  <span className="text-base text-foreground">{person.aliases.join(" · ")}</span>
                )}
              </dd>
            </div>
          </dl>
          <p className="mt-3 flex items-start gap-2 text-sm text-muted-foreground">
            <Inbox aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 [overflow-wrap:anywhere]">
              {t("profile_view.person_file_hint").replace("{0}", person.slug)}
            </span>
          </p>
        </div>
      )}
    </li>
  );
}
