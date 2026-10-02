/**
 * SoulView — the assistant's own profile, named after its character file.
 *
 * Until 2026-10-02 this section was an editor for one file named after the
 * assistant ("George.md"), and a live call could not tell that file from
 * SOUL.md. Now the section is SOUL.md, and it shows every file that shapes
 * the assistant, in one column read top to bottom:
 *
 *   ┌────────────────────────────────────────────────────────────┐
 *   │ Mark · name · what it is · its vibe                        │
 *   │ Wake word │ About itself │ Memory │ Last change            │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ Files — SOUL.md, the instructions, MEMORY.md, USER.md      │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ Who {name} is — role, vibe, tone, limits (from SOUL.md)    │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ What it has learned — about itself · memory · about you    │
 *   ├────────────────────────────────────────────────────────────┤
 *   │ Recent changes — the learning ledger, newest first         │
 *   └────────────────────────────────────────────────────────────┘
 *
 * A file opens in place as a sub-page with a back link, like USER.md on the
 * Profile page: the files are the source behind the page, not peers of it.
 */
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ActivityGroup } from "@/views/soul/ActivityGroup";
import { CharacterGroup } from "@/views/soul/CharacterGroup";
import { fileOf, useSoulProfile, type SoulFileId } from "@/views/soul/api";
import { FileList } from "@/views/soul/FileList";
import { FilePage } from "@/views/soul/FilePage";
import { NotesGroup } from "@/views/soul/NotesGroup";
import { SoulHero } from "@/views/soul/SoulHero";

/** The reading column — the same measure as the Profile page. */
const COLUMN = "mx-auto w-full max-w-[760px]";

export function SoulView() {
  const t = useT();
  const { data, isLoading, error, refetch, isRefetching } = useSoulProfile();
  const [open, setOpen] = useState<SoulFileId | null>(null);

  // Switching between the page and a file starts at the top.
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo?.({ top: 0 });
  }, [open]);

  const openFile = open && data ? data.files.find((f) => f.id === open) : undefined;

  return (
    <div
      ref={scrollRef}
      className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-10 scrollbar-jarvis"
    >
      <div className={COLUMN}>
        <PageHeader
          icon={<Sparkles />}
          title="SOUL.md"
          description={t("soul_view.subtitle")}
          actions={
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => void refetch()}
              disabled={isRefetching}
              title={t("soul_view.reload")}
              aria-label={t("soul_view.reload")}
            >
              <RefreshCw className={cn(isRefetching && "animate-spin")} />
            </Button>
          }
        />

        {isLoading && <SoulSkeleton label={t("common.loading")} />}

        {error && (
          <EmptyState
            icon={<AlertTriangle />}
            title={t("soul_view.error_title")}
            description={error.message}
            actions={
              <Button type="button" variant="outline" onClick={() => void refetch()}>
                {t("soul_view.retry")}
              </Button>
            }
          />
        )}

        {data && openFile && (
          <FilePage file={openFile} canForget={data.learning} onBack={() => setOpen(null)} />
        )}

        {data && !openFile && (
          <div className="flex flex-col gap-10">
            <SoulHero profile={data} />
            <FileList files={data.files} onOpen={setOpen} />
            <CharacterGroup soul={fileOf(data, "soul")} onEdit={() => setOpen("soul")} />
            <NotesGroup profile={data} />
            <ActivityGroup activity={data.activity} />
          </div>
        )}
      </div>
    </div>
  );
}

/** The real layout at its real height; no zero stands in for a missing figure. */
function SoulSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-10">
      <div className="rounded-xl border border-border bg-card">
        <div className="flex items-center gap-5 p-6">
          <div className="h-20 w-20 shrink-0 animate-pulse rounded-2xl bg-secondary" />
          <div className="flex min-w-0 flex-1 flex-col gap-2.5">
            <div className="h-6 w-40 max-w-full animate-pulse rounded-md bg-secondary" />
            <div className="h-3.5 w-64 max-w-full animate-pulse rounded-full bg-secondary" />
          </div>
        </div>
        <div className="grid grid-cols-2 border-t border-border sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="px-5 py-4">
              <div className="h-3 w-16 animate-pulse rounded-full bg-secondary" />
              <div className="mt-2 h-5 w-20 animate-pulse rounded-md bg-secondary" />
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <div className="h-5 w-24 animate-pulse rounded-md bg-secondary" />
        <div className="divide-y divide-border rounded-xl border border-border bg-card">
          {[0, 1, 2, 3].map((j) => (
            <div key={j} className="flex items-center gap-4 px-5 py-4">
              <div className="h-9 w-9 animate-pulse rounded-lg bg-secondary" />
              <div className="flex flex-1 flex-col gap-2">
                <div className="h-3.5 w-24 animate-pulse rounded-full bg-secondary" />
                <div className="h-3 w-56 max-w-full animate-pulse rounded-full bg-secondary" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
