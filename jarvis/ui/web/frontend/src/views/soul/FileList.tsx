/**
 * The files behind the assistant — one row each, in the order it reads them:
 * its character, the person's instructions, its memory, its notes about the
 * person. A row says what the file is FOR in plain words, how full it is and
 * when it last changed, and opens the file in place.
 */
import { Brain, ChevronRight, ScrollText, Sparkles, UserRound } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { useT, useUiLanguage } from "@/i18n";
import { ProfileGroup } from "@/views/profile/ProfileGroup";
import type { SoulFile, SoulFileId } from "@/views/soul/api";
import { relativeDay } from "@/views/soul/format";

export const FILE_ICON: Record<SoulFileId, ReactNode> = {
  soul: <Sparkles />,
  instructions: <ScrollText />,
  memory: <Brain />,
  user: <UserRound />,
};

export function FileIcon({ id, size = "md" }: { id: SoulFileId; size?: "md" | "lg" }) {
  return (
    <span
      aria-hidden
      className={
        size === "lg"
          ? "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-secondary text-muted-foreground [&>svg]:h-5 [&>svg]:w-5"
          : "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-border bg-secondary text-muted-foreground [&>svg]:h-4 [&>svg]:w-4"
      }
    >
      {FILE_ICON[id]}
    </span>
  );
}

/** "12 notes · updated today", or why there is nothing to show. */
export function useFileMeta(): (file: SoulFile) => string {
  const t = useT();
  const lang = useUiLanguage();
  return (file) => {
    if (!file.exists) return t("soul_view.file_missing");
    const size =
      "entries" in file
        ? t("soul_view.file_entries").replace("{0}", String(file.entries.length))
        : t("soul_view.file_chars").replace("{0}", file.chars.toLocaleString(lang));
    const when = relativeDay(file.updated_ms, lang);
    return when ? `${size} · ${t("soul_view.file_updated").replace("{0}", when)}` : size;
  };
}

export function FileList({
  files,
  onOpen,
}: {
  files: readonly SoulFile[];
  onOpen: (id: SoulFileId) => void;
}) {
  const t = useT();
  const meta = useFileMeta();

  return (
    <ProfileGroup
      testId="soul-files"
      title={t("soul_view.files_title")}
      description={t("soul_view.files_description")}
    >
      {files.map((file) => (
        <button
          key={file.id}
          type="button"
          data-testid={`soul-file-${file.id}`}
          onClick={() => onOpen(file.id)}
          className="group flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-secondary/60 focus-visible:bg-secondary/60 focus-visible:outline-none"
        >
          <FileIcon id={file.id} />
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate font-mono text-sm font-semibold text-foreground-strong">
                {file.filename}
              </span>
              {!file.editable && (
                <Badge variant="secondary">{t("soul_view.file_by_assistant")}</Badge>
              )}
            </span>
            <span className="mt-0.5 block truncate text-sm text-muted-foreground">
              {t(`soul_view.file_role_${file.id}`)}
            </span>
          </span>
          <span className="hidden shrink-0 text-sm tabular-nums text-foreground-faint sm:block">
            {meta(file)}
          </span>
          <ChevronRight
            aria-hidden
            className="h-4 w-4 shrink-0 text-foreground-faint transition-colors group-hover:text-foreground"
          />
        </button>
      ))}
    </ProfileGroup>
  );
}
