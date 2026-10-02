/**
 * What the assistant has learned, in its own three notebooks: about itself
 * (SOUL.md's learned section), its memory (MEMORY.md) and about the person
 * (USER.md). One group with an underline tab per notebook, so the page stays
 * one screen tall however much it has learned.
 *
 * A note can be forgotten. The learning ledger keeps the old text, so that is
 * an undoable act on the backend's side, and the button says "Forget" rather
 * than "Delete". It needs the learning loop running in this session; without
 * it the button is disabled and says why.
 */
import { X } from "lucide-react";
import { useState } from "react";

import { TabBar } from "@/components/layout/SectionTabBar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { fileOf, useForgetNote, type NoteTarget, type SoulEntry, type SoulProfile } from "@/views/soul/api";
import { relativeDay } from "@/views/soul/format";

/**
 * An explicit note is stored as "2026-10-02 (asked to remember): text" so the
 * prompt reads it in context; the page shows the text and the date apart.
 */
export function splitDated(text: string): { body: string; dateMs: number | null } {
  const m = /^(\d{4}-\d{2}-\d{2}) \([^)]*\): ([\s\S]*)$/.exec(text);
  if (!m) return { body: text, dateMs: null };
  const [y, mo, d] = m[1].split("-").map(Number);
  return { body: m[2], dateMs: new Date(y, mo - 1, d).getTime() };
}

export function NoteList({
  target,
  entries,
  canForget,
  emptyText,
}: {
  target: NoteTarget;
  entries: readonly SoulEntry[];
  canForget: boolean;
  emptyText: string;
}) {
  const t = useT();
  const lang = useUiLanguage();
  const pushToast = useEventStore((s) => s.pushToast);
  const forget = useForgetNote();

  if (entries.length === 0) {
    return (
      <p data-testid={`soul-notes-empty-${target}`} className="px-5 py-5 text-base text-muted-foreground">
        {emptyText}
      </p>
    );
  }

  return (
    <ul data-testid={`soul-notes-${target}`} className="divide-y divide-border">
      {entries.map((entry) => {
        const busy = forget.isPending && forget.variables?.id === entry.id;
        const { body, dateMs } = splitDated(entry.text);
        const date = relativeDay(dateMs, lang);
        return (
          <li key={entry.id} className="group flex items-start gap-3 px-5 py-3.5">
            <div className="min-w-0 flex-1">
              <p className="whitespace-pre-line text-base leading-6 text-foreground">{body}</p>
              {(entry.explicit || date) && (
                <p className="mt-1.5 flex items-center gap-2 text-sm text-muted-foreground">
                  {entry.explicit && <Badge variant="accent">{t("soul_view.explicit_badge")}</Badge>}
                  {date && <span>{date}</span>}
                </p>
              )}
            </div>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              disabled={!canForget || busy}
              title={canForget ? t("soul_view.forget") : t("soul_view.forget_needs_loop")}
              aria-label={`${t("soul_view.forget")}: ${entry.text.slice(0, 60)}`}
              onClick={() =>
                forget.mutate(
                  { target, id: entry.id },
                  {
                    onSuccess: () => pushToast("success", t("soul_view.forget_done")),
                    onError: (e) => pushToast("error", e.message),
                  },
                )
              }
              className="h-8 w-8 shrink-0 text-muted-foreground opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100 disabled:group-hover:opacity-40"
            >
              <X />
            </Button>
          </li>
        );
      })}
    </ul>
  );
}

type Tab = NoteTarget;

export function NotesGroup({ profile }: { profile: SoulProfile }) {
  const t = useT();
  const [tab, setTab] = useState<Tab>("soul");

  const lists: Record<Tab, SoulEntry[]> = {
    soul: fileOf(profile, "soul")?.learned ?? [],
    memory: fileOf(profile, "memory")?.entries ?? [],
    user: fileOf(profile, "user")?.entries ?? [],
  };
  const title = t("soul_view.notes_title");

  return (
    <section data-testid="soul-notes" aria-label={title} className="flex flex-col gap-3">
      <div className="px-1">
        <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
        <p className="mt-0.5 text-base text-muted-foreground">{t("soul_view.notes_description")}</p>
      </div>
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <TabBar
          className="px-5"
          active={tab}
          onChange={(id) => setTab(id as Tab)}
          tabs={[
            { id: "soul", label: t("soul_view.tab_self"), count: lists.soul.length },
            { id: "memory", label: t("soul_view.tab_memory"), count: lists.memory.length },
            { id: "user", label: t("soul_view.tab_user"), count: lists.user.length },
          ]}
        />
        <NoteList
          key={tab}
          target={tab}
          entries={lists[tab]}
          canForget={profile.learning}
          emptyText={t(`soul_view.empty_${tab}`)}
        />
      </div>
    </section>
  );
}
