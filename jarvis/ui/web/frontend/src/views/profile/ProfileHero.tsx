/**
 * The top of the profile: who this is, and four plain numbers.
 *
 * The photo is the upload control. The name is the largest text on the page;
 * when the file has no name yet, that same spot is a one-field form, because
 * "No name on file" is a fact about the database while "What should we call
 * you?" is the one thing the reader can fix in a second.
 *
 * The line under the name only holds facts that exist — no dashes for gaps.
 * The strip below is four figures from real sources (the activity board and
 * the file's own count). A figure that has not loaded shows a skeleton, never
 * a zero, because a zero reads as a fact.
 */
import { useMemo, useState } from "react";
import { Check, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useBoardSummary } from "@/hooks/useBoard";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { AvatarButton } from "@/views/profile/AvatarButton";
import { clusterDataOf, useFieldEdit, type ProfileResponse } from "@/views/profile/api";
import {
  TOTAL_FIELDS,
  countFilled,
  daysSince,
  isEmptyValue,
  languageName,
} from "@/views/profile/ledger";

/** A date stamp as a short local date; null when unparseable. */
function shortDate(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function Stat({ label, value, sub }: { label: string; value: string | null; sub?: string | null }) {
  return (
    <div className="min-w-0 px-5 py-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      {value === null ? (
        <div className="mt-1.5 h-5 w-20 animate-pulse rounded-md bg-secondary" />
      ) : (
        <p className="mt-0.5 truncate text-lg font-semibold tabular-nums text-foreground-strong">
          {value}
        </p>
      )}
      {sub && <p className="truncate text-sm text-muted-foreground">{sub}</p>}
    </div>
  );
}

function NameForm({ onDone }: { onDone?: () => void }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const edit = useFieldEdit();
  const [draft, setDraft] = useState("");

  const save = () => {
    const v = draft.trim();
    if (!v) return;
    edit.mutate(
      { cluster: "identity", field: "name", operation: "set", value: v },
      {
        onSuccess: () => {
          pushToast("success", t("profile_view.field_saved"));
          onDone?.();
        },
      },
    );
  };

  return (
    <form
      className="mt-2 flex max-w-sm items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <Input
        autoFocus
        value={draft}
        disabled={edit.isPending}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onDone?.()}
        placeholder={t("profile_view.name_placeholder")}
        aria-label={t("profile_view.fields.name")}
        className="h-9"
      />
      <Button type="submit" size="sm" disabled={edit.isPending || !draft.trim()}>
        <Check aria-hidden />
        {t("profile_view.raw_save")}
      </Button>
    </form>
  );
}

export function ProfileHero({
  data,
  meta,
}: {
  data: ProfileResponse;
  meta: Record<string, unknown>;
}) {
  const t = useT();
  const ui = useUiLanguage();
  const board = useBoardSummary();
  const [renaming, setRenaming] = useState(false);

  const identity = clusterDataOf(meta, "identity");
  const name = data.user.name?.trim() || null;
  const preferred =
    typeof identity["preferred_address"] === "string" ? identity["preferred_address"].trim() : "";
  const filled = useMemo(() => countFilled(meta), [meta]);

  const facts: string[] = [];
  if (preferred && preferred !== name) {
    facts.push(t("profile_view.hero_called").replace("{0}", preferred));
  }
  if (!isEmptyValue(identity["primary_language"])) {
    facts.push(languageName(String(identity["primary_language"]), ui));
  }
  if (!isEmptyValue(identity["timezone"])) facts.push(String(identity["timezone"]));

  const totals = board.data?.totals;
  const since = totals ? shortDate(totals.first_day) : null;
  const days = totals ? daysSince(totals.first_day) : null;
  const updated = shortDate(meta["last_updated"]);

  return (
    <section
      data-testid="profile-hero"
      className="overflow-hidden rounded-xl border border-border bg-card"
    >
      <div className="flex items-center gap-5 p-6">
        <AvatarButton name={name} hasAvatar={!!data.has_avatar} size="xl" />

        <div className="min-w-0 flex-1">
          {name && !renaming ? (
            <div className="group flex items-center gap-1">
              <h2 className="truncate text-2xl font-semibold text-foreground-strong">{name}</h2>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setRenaming(true)}
                title={t("profile_view.field_edit")}
                aria-label={`${t("profile_view.field_edit")}: ${t("profile_view.fields.name")}`}
                className="h-8 w-8 shrink-0 text-muted-foreground opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
              >
                <Pencil />
              </Button>
            </div>
          ) : (
            <>
              <h2 className="text-xl font-semibold text-foreground-strong">
                {name ? t("profile_view.fields.name") : t("profile_view.hero_ask_name")}
              </h2>
              <NameForm onDone={name ? () => setRenaming(false) : undefined} />
            </>
          )}
          {facts.length > 0 && name && !renaming && (
            <p className="mt-1 truncate text-base text-muted-foreground">{facts.join(" · ")}</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 border-t border-border sm:grid-cols-4 [&>*]:border-border [&>*:nth-child(even)]:border-l sm:[&>*:not(:first-child)]:border-l [&>*:nth-child(n+3)]:border-t sm:[&>*:nth-child(n+3)]:border-t-0">
        <Stat
          label={t("profile_view.stat_since")}
          value={board.isLoading ? null : (since ?? "–")}
          sub={days !== null ? t("profile_view.stat_days").replace("{0}", String(days)) : null}
        />
        <Stat
          label={t("profile_view.stat_conversations")}
          value={board.isLoading ? null : totals ? totals.session_count.toLocaleString() : "–"}
        />
        <Stat
          label={t("profile_view.stat_known")}
          value={`${filled} / ${TOTAL_FIELDS}`}
          sub={t("profile_view.stat_known_sub")}
        />
        <Stat label={t("profile_view.stat_updated")} value={updated ?? "–"} />
      </div>
    </section>
  );
}
