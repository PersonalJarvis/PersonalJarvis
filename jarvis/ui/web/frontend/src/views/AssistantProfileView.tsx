/**
 * AssistantProfileView — the assistant's own profile, named after it.
 *
 * The section carries the assistant's name (the wake word's name: "George",
 * "Nova", …). It was briefly titled "SOUL.md" and before that "{name}.md",
 * after one of its files — but the person thinks of the assistant, not of
 * its files, so the files are one part of the page rather than its title.
 *
 * Set as one document, not a stack of cards:
 *
 *   George                                         (page header)
 *   Your assistant in Personal Jarvis, woken by "Hey George".
 *
 *   [pet]  Helpful but not obsequious. Direct, …   (the vibe, as the lead)
 *          ● Learning · 1 thing remembered · changed today
 *   ────────────────────────────────────────────────────────────
 *   Character                                            Edit
 *   Role     …   /   Tone     …   /   Limits   …
 *   ────────────────────────────────────────────────────────────
 *   Memory   [Remembered · About you · About itself]
 *   ────────────────────────────────────────────────────────────
 *   Recently learned  — the ledger, with the person's words
 *   ────────────────────────────────────────────────────────────
 *   Files    SOUL.md · George.md · MEMORY.md · USER.md   →
 *
 * A file opens in place with a way back, like USER.md on the Profile page.
 */
import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { fileOf, useSoulProfile, type SoulFileId, type SoulProfile } from "@/views/assistant/api";
import { CharacterSection } from "@/views/assistant/CharacterSection";
import { FilePage } from "@/views/assistant/FilePage";
import { FilesSection } from "@/views/assistant/FilesSection";
import { Intro } from "@/views/assistant/Intro";
import { MemorySection } from "@/views/assistant/MemorySection";
import { RecentSection } from "@/views/assistant/RecentSection";
import { TextAction } from "@/views/assistant/Section";

/** The reading column — the same measure as the Profile page. */
const COLUMN = "mx-auto w-full max-w-[760px]";

function headerLine(t: (key: string) => string, profile: SoulProfile | undefined): string | undefined {
  if (!profile) return undefined;
  if (profile.named && profile.wake_phrase) {
    return t("assistant_view.header_named").replace("{0}", profile.product).replace("{1}", profile.wake_phrase);
  }
  return t("assistant_view.header_unnamed").replace("{0}", profile.product);
}

export function AssistantProfileView() {
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
    <div ref={scrollRef} className="flex h-full flex-col overflow-y-auto bg-background px-8 pb-12 scrollbar-jarvis">
      <div className={COLUMN}>
        <PageHeader
          title={data?.name ?? t("nav.agent_instructions")}
          description={headerLine(t, data)}
          actions={
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="text-muted-foreground"
              onClick={() => void refetch()}
              disabled={isRefetching}
              title={t("assistant_view.reload")}
              aria-label={t("assistant_view.reload")}
            >
              <RefreshCw className={cn(isRefetching && "animate-spin")} />
            </Button>
          }
        />

        {isLoading && <ProfileSkeleton label={t("common.loading")} />}

        {error && (
          <div role="alert" className="border-t border-border pt-7">
            <p className="text-base font-medium text-foreground-strong">{t("assistant_view.error_title")}</p>
            <p className="mt-1 text-base text-muted-foreground">{error.message}</p>
            <TextAction className="mt-3" onClick={() => void refetch()}>
              {t("assistant_view.retry")}
            </TextAction>
          </div>
        )}

        {data && openFile && (
          <FilePage file={openFile} name={data.name} canForget={data.learning} onBack={() => setOpen(null)} />
        )}

        {data && !openFile && (
          <div className="flex flex-col gap-10">
            <Intro profile={data} />
            <CharacterSection soul={fileOf(data, "soul")} onEdit={() => setOpen("soul")} />
            <MemorySection profile={data} />
            <RecentSection activity={data.activity} />
            <FilesSection files={data.files} onOpen={setOpen} />
          </div>
        )}
      </div>
    </div>
  );
}

/** The real layout at its real height; nothing stands in for a missing fact. */
function ProfileSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-10">
      <div className="flex items-center gap-8 pt-1">
        <div className="h-24 w-24 shrink-0 animate-pulse rounded-full bg-secondary" />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="h-5 w-11/12 animate-pulse rounded-md bg-secondary" />
          <div className="h-5 w-2/3 animate-pulse rounded-md bg-secondary" />
          <div className="h-3.5 w-56 max-w-full animate-pulse rounded-full bg-secondary" />
        </div>
      </div>
      {[0, 1].map((i) => (
        <div key={i} className="border-t border-border pt-7">
          <div className="h-5 w-28 animate-pulse rounded-md bg-secondary" />
          {[0, 1, 2].map((j) => (
            <div key={j} className="mt-5 flex gap-8">
              <div className="h-4 w-24 animate-pulse rounded-full bg-secondary" />
              <div className="h-4 flex-1 animate-pulse rounded-full bg-secondary" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
