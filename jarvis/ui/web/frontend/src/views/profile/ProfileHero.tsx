/**
 * The top of the profile: the photo, the name, one line of facts, and a
 * sentence or two the person writes about themselves.
 *
 * The photo is the upload control. The name is the largest text on the page;
 * when the file has no name yet, that same spot is a one-field form, because
 * "No name on file" is a fact about the database while "What should we call
 * you?" is the one thing the reader can fix in a second.
 *
 * The facts line only holds facts that exist — no dashes for gaps. The
 * self-description is `identity.about`: it saves when the field loses focus
 * and goes into every prompt as the person's own words.
 */
import { useEffect, useState } from "react";
import { Check, Pencil } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useBoardSummary } from "@/hooks/useBoard";
import { fill, useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { AvatarButton } from "@/views/profile/AvatarButton";
import { clusterDataOf, useFieldEdit, type ProfileResponse } from "@/views/profile/api";
import { isEmptyValue, languageName } from "@/views/profile/ledger";

/** The longest self-description the field takes; it rides in every prompt. */
export const ABOUT_MAX_CHARS = 160;

/** A date stamp as a short local date; null when unparseable. */
function shortDate(value: unknown, ui: string): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(ui, { day: "numeric", month: "short", year: "numeric" });
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
      className="mt-1 flex w-full max-w-sm items-center gap-2"
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

function AboutField({ value }: { value: string }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const edit = useFieldEdit();
  const [draft, setDraft] = useState(value);

  // A save elsewhere (the assistant, the raw file) replaces the draft unless
  // the person is mid-sentence in this field.
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    if (!focused) setDraft(value);
  }, [value, focused]);

  const save = () => {
    const next = draft.trim();
    if (next === value.trim()) return;
    edit.mutate(
      next
        ? { cluster: "identity", field: "about", operation: "set", value: next }
        : { cluster: "identity", field: "about", operation: "clear" },
      { onSuccess: () => pushToast("success", t("profile_view.field_saved")) },
    );
  };

  return (
    <div className="relative w-full max-w-lg">
      <Textarea
        data-testid="profile-about"
        value={draft}
        rows={1}
        maxLength={ABOUT_MAX_CHARS}
        disabled={edit.isPending}
        aria-label={t("profile_view.fields.about")}
        placeholder={t("profile_view.about_placeholder")}
        onFocus={() => setFocused(true)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          setFocused(false);
          save();
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            e.currentTarget.blur();
          } else if (e.key === "Escape") {
            e.stopPropagation();
            setDraft(value);
            e.currentTarget.blur();
          }
        }}
        className="max-h-28 min-h-0 resize-none rounded-xl text-center text-base leading-relaxed [field-sizing:content]"
      />
      <span
        aria-hidden
        className="absolute -bottom-5 right-1 text-xs tabular-nums text-muted-foreground opacity-0 transition-opacity data-[on=true]:opacity-100"
        data-on={focused}
      >
        {draft.length} / {ABOUT_MAX_CHARS}
      </span>
    </div>
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
  const about = typeof identity["about"] === "string" ? identity["about"] : "";

  const facts: string[] = [];
  if (preferred && preferred !== name) {
    facts.push(t("profile_view.hero_called").replace("{0}", preferred));
  }
  if (!isEmptyValue(identity["primary_language"])) {
    facts.push(languageName(String(identity["primary_language"]), ui));
  }
  if (!isEmptyValue(identity["timezone"])) facts.push(String(identity["timezone"]));
  const since = shortDate(board.data?.totals.first_day, ui);
  if (since) facts.push(fill(t("profile_view.hero_since"), { 0: since }));

  return (
    <section
      data-testid="profile-hero"
      aria-label={t("profile_view.groups.about.title")}
      className="flex flex-col items-center gap-3 text-center"
    >
      <AvatarButton name={name} hasAvatar={!!data.has_avatar} size="xl" shape="rounded" />

      <div className="flex w-full flex-col items-center gap-1">
        {name && !renaming ? (
          <div className="group relative flex items-center justify-center">
            <h2 className="truncate font-display text-2xl text-foreground-strong">{name}</h2>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => setRenaming(true)}
              title={t("profile_view.field_edit")}
              aria-label={`${t("profile_view.field_edit")}: ${t("profile_view.fields.name")}`}
              className="absolute -right-10 h-8 w-8 shrink-0 text-muted-foreground opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
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
          <p className="max-w-full truncate text-base text-muted-foreground">{facts.join(" · ")}</p>
        )}
      </div>

      <AboutField value={about} />
    </section>
  );
}
