/**
 * Memory — what the assistant keeps by itself, in its three notebooks:
 * about itself (SOUL.md's learned notes), what it remembers (MEMORY.md) and
 * about the person (USER.md), one underline tab each.
 *
 * Every note is a line of text with its date and, when the person asked for
 * it, "You asked" in quiet ink beside it. "Forget" appears on hover and on
 * keyboard focus; the learning ledger keeps the old text, so forgetting is
 * recoverable on the backend's side. It needs the learning loop running in
 * this session, and says so otherwise.
 */
import { useState } from "react";

import { TabBar } from "@/components/layout/SectionTabBar";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { fileOf, useForgetNote, type NoteTarget, type SoulEntry, type SoulProfile } from "@/views/assistant/api";
import { relativeDay } from "@/views/assistant/format";
import { Section, TextAction } from "@/views/assistant/Section";

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
      <p data-testid={`assistant-notes-empty-${target}`} className="py-5 text-base text-muted-foreground">
        {emptyText}
      </p>
    );
  }

  return (
    <ul data-testid={`assistant-notes-${target}`} className="divide-y divide-border">
      {entries.map((entry) => {
        const { body, dateMs } = splitDated(entry.text);
        const date = relativeDay(dateMs, lang);
        const busy = forget.isPending && forget.variables?.id === entry.id;
        return (
          <li key={entry.id} className="group flex items-start gap-8 py-4">
            <p className="min-w-0 flex-1 whitespace-pre-line text-base leading-7 text-foreground">{body}</p>
            <div className="flex shrink-0 flex-col items-end gap-1 pt-0.5 text-right">
              {(entry.explicit || date) && (
                <span className="text-sm text-foreground-faint">
                  {[entry.explicit ? t("assistant_view.explicit") : null, date].filter(Boolean).join(" · ")}
                </span>
              )}
              <TextAction
                disabled={!canForget || busy}
                title={canForget ? undefined : t("assistant_view.forget_needs_loop")}
                aria-label={`${t("assistant_view.forget")}: ${body.slice(0, 60)}`}
                onClick={() =>
                  forget.mutate(
                    { target, id: entry.id },
                    {
                      onSuccess: () => pushToast("success", t("assistant_view.forget_done")),
                      onError: (e) => pushToast("error", e.message),
                    },
                  )
                }
                className="opacity-0 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
              >
                {t("assistant_view.forget")}
              </TextAction>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function MemorySection({ profile }: { profile: SoulProfile }) {
  const t = useT();
  const [tab, setTab] = useState<NoteTarget>("memory");

  const lists: Record<NoteTarget, SoulEntry[]> = {
    memory: fileOf(profile, "memory")?.entries ?? [],
    user: fileOf(profile, "user")?.entries ?? [],
    soul: fileOf(profile, "soul")?.learned ?? [],
  };

  return (
    <Section testId="assistant-memory" title={t("assistant_view.memory_title")}>
      <TabBar
        active={tab}
        onChange={(id) => setTab(id as NoteTarget)}
        tabs={[
          { id: "memory", label: t("assistant_view.tab_memory"), count: lists.memory.length },
          { id: "user", label: t("assistant_view.tab_user"), count: lists.user.length },
          { id: "soul", label: t("assistant_view.tab_self"), count: lists.soul.length },
        ]}
      />
      <NoteList
        key={tab}
        target={tab}
        entries={lists[tab]}
        canForget={profile.learning}
        emptyText={t(`assistant_view.empty_${tab}`)}
      />
    </Section>
  );
}
