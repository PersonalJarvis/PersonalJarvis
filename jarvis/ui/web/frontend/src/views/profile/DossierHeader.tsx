/**
 * The cover line of the file.
 *
 * A dossier opens by naming its subject, not by reporting how complete it is.
 * So the name is the largest thing on the screen, the line under it is only
 * facts that actually exist (a missing timezone leaves no gap and no dash),
 * and the count sits quietly on the right in the mono face the rest of the
 * page uses for anything countable.
 *
 * The old header led with "Who are you? · First impressions" over a progress
 * bar — a question and a score, neither of which is information about the
 * reader. What replaced it is the first line of the record itself.
 */
import { useMemo } from "react";
import { CalendarDays } from "lucide-react";

import { useBoardSummary } from "@/hooks/useBoard";
import { useT } from "@/i18n";
import { AvatarButton } from "@/views/profile/AvatarButton";
import { clusterDataOf, type ProfileResponse } from "@/views/profile/api";
import {
  TOTAL_FIELDS,
  countFilled,
  displayAddress,
  isEmptyValue,
} from "@/views/profile/ledger";

/** A YYYY-MM-DD or ISO stamp as a short local date; null when unparseable. */
function shortDate(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function DossierHeader({
  data,
  meta,
}: {
  data: ProfileResponse;
  meta: Record<string, unknown>;
}) {
  const t = useT();
  const board = useBoardSummary();

  const name = data.user.name?.trim() || null;
  const address = displayAddress(meta, name);
  const filled = useMemo(() => countFilled(meta), [meta]);

  const identity = clusterDataOf(meta, "identity");
  const since = shortDate(board.data?.totals.first_day);
  const sessions = board.data?.totals.session_count ?? 0;
  const updated = shortDate(meta["last_updated"]);

  // Only what is known appears. A dossier line with three dashes in it is the
  // form we are getting away from.
  const facts: string[] = [];
  if (!isEmptyValue(identity["primary_language"])) facts.push(String(identity["primary_language"]));
  if (!isEmptyValue(identity["timezone"])) facts.push(String(identity["timezone"]));
  if (since) facts.push(t("profile_view.header_since").replace("{0}", since));
  if (sessions > 0) {
    facts.push(t("profile_view.header_conversations").replace("{0}", String(sessions)));
  }

  return (
    <header
      data-testid="dossier-header"
      className="profile-rise flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-border pb-5"
    >
      <AvatarButton name={name} hasAvatar={!!data.has_avatar} size="lg" />

      <div className="min-w-0 flex-1 basis-72">
        <h2 className="truncate font-display text-display text-foreground-strong">
          {address ?? t("profile_view.header_unnamed")}
        </h2>
        <p className="mt-1 truncate text-meta text-muted-foreground">
          {facts.length ? facts.join(" · ") : t("profile_view.header_nothing_yet")}
        </p>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1">
        <span
          data-testid="dossier-count"
          className="font-mono text-meta tabular-nums text-foreground"
        >
          {t("profile_view.header_entries")
            .replace("{0}", String(filled))
            .replace("{1}", String(TOTAL_FIELDS))}
        </span>
        {updated && (
          <span className="flex items-center gap-1.5 text-micro text-foreground-faint">
            <CalendarDays aria-hidden className="h-3 w-3" />
            {t("profile_view.header_updated").replace("{0}", updated)}
          </span>
        )}
      </div>
    </header>
  );
}
