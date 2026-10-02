/**
 * Files — what the assistant reads at the start of every conversation, as a
 * plain index: the file's name, what it is, how full it is and when it last
 * changed. A row opens the file in place.
 */
import { ChevronRight } from "lucide-react";

import { useT, useUiLanguage } from "@/i18n";
import type { SoulFile, SoulFileId } from "@/views/assistant/api";
import { plural, relativeDay } from "@/views/assistant/format";
import { Section } from "@/views/assistant/Section";

/** "1 note · today", or why there is nothing to show. */
export function useFileMeta(): (file: SoulFile) => string {
  const t = useT();
  const lang = useUiLanguage();
  return (file) => {
    if (!file.exists) return t("assistant_view.file_missing");
    const size =
      "entries" in file
        ? plural(t, "assistant_view.file_notes", file.entries.length, lang)
        : plural(t, "assistant_view.file_chars", file.chars, lang);
    const when = relativeDay(file.updated_ms, lang);
    return when ? `${size} · ${when}` : size;
  };
}

export function FilesSection({
  files,
  onOpen,
}: {
  files: readonly SoulFile[];
  onOpen: (id: SoulFileId) => void;
}) {
  const t = useT();
  const meta = useFileMeta();

  return (
    <Section testId="assistant-files" title={t("assistant_view.files_title")}>
      <p className="-mt-2 mb-3 text-base text-muted-foreground">{t("assistant_view.files_hint")}</p>
      <ul className="-mx-3">
        {files.map((file) => (
          <li key={file.id}>
            <button
              type="button"
              data-testid={`assistant-file-${file.id}`}
              onClick={() => onOpen(file.id)}
              className="group grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-6 rounded-lg px-3 py-3 text-left transition-colors hover:bg-secondary/60 focus-visible:bg-secondary/60 focus-visible:outline-none sm:grid-cols-[11rem_minmax(0,1fr)_auto]"
            >
              <span className="truncate text-base font-medium text-foreground-strong">{file.filename}</span>
              <span className="hidden truncate text-base text-muted-foreground sm:block">
                {t(`assistant_view.file_role_${file.id}`)}
              </span>
              <span className="flex items-center gap-3 text-sm tabular-nums text-foreground-faint">
                {meta(file)}
                <ChevronRight
                  aria-hidden
                  className="h-4 w-4 transition-transform group-hover:translate-x-0.5 group-hover:text-foreground"
                />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Section>
  );
}
