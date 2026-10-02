/**
 * Files — what the assistant reads at the start of every conversation, right
 * under the intro because they ARE the assistant: change a file and the
 * assistant changes.
 *
 * Each file is shown as a page with its own first lines on it — the real
 * content, not a description of it — so the person sees at a glance what
 * the assistant is told about itself, what they told it, what it remembers
 * and what it noticed. A page opens the file in place.
 */
import { useT, useUiLanguage } from "@/i18n";
import { cn } from "@/lib/utils";
import type { SoulFile, SoulFileId } from "@/views/assistant/api";
import { plural, relativeDay } from "@/views/assistant/format";
import { splitDated } from "@/views/assistant/MemorySection";
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

/**
 * Drops HTML comments. The preview renders as plain text, so this is about
 * readability, not safety; an unclosed comment hides the rest of the line.
 */
function withoutComments(text: string): string {
  let out = "";
  let at = 0;
  for (;;) {
    const start = text.indexOf("<!--", at);
    if (start < 0) return out + text.slice(at);
    out += text.slice(at, start);
    const end = text.indexOf("-->", start + 4);
    if (end < 0) return out;
    at = end + 3;
  }
}

/** Inline Markdown made readable: emphasis markers and comments dropped. */
function clean(line: string): string {
  return withoutComments(line)
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/(^|\s)_([^_]+)_(?=[\s.,;:!?]|$)/g, "$1$2")
    .replace(/^_|_$/g, "")
    .trim();
}

/**
 * The first readable items of a file: a notebook's newest notes, or a text
 * file's paragraphs and list items with headings and comments left out
 * (a wrapped paragraph becomes one item, not three fragments).
 */
export function previewLines(file: SoulFile, max = 4): string[] {
  if ("entries" in file) {
    return file.entries.slice(0, max).map((e) => splitDated(e.text).body.replace(/\s+/g, " ").trim());
  }
  const out: string[] = [];
  let paragraph: string[] = [];
  const flush = () => {
    if (paragraph.length) out.push(paragraph.join(" "));
    paragraph = [];
  };
  for (const raw of file.content.split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("#") || (trimmed.startsWith("<!--") && trimmed.endsWith("-->"))) {
      flush();
    } else if (/^[-*]\s+/.test(trimmed)) {
      flush();
      const item = clean(trimmed.replace(/^[-*]\s+/, ""));
      if (item) out.push(item);
    } else {
      const text = clean(trimmed);
      if (text) paragraph.push(text);
    }
    if (out.length >= max) break;
  }
  flush();
  return out.slice(0, max);
}

function FilePreview({ file, onOpen }: { file: SoulFile; onOpen: (id: SoulFileId) => void }) {
  const t = useT();
  const meta = useFileMeta();
  const lines = file.exists ? previewLines(file, 3) : [];

  return (
    <button
      type="button"
      data-testid={`assistant-file-${file.id}`}
      onClick={() => onOpen(file.id)}
      className={cn(
        "group flex h-[15.5rem] w-full flex-col rounded-xl border border-border bg-card p-5 text-left transition-colors",
        "hover:border-border-strong focus-visible:border-accent focus-visible:outline-none",
      )}
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate text-base font-semibold text-foreground-strong">{file.filename}</span>
        <span className="shrink-0 text-sm tabular-nums text-foreground-faint">{meta(file)}</span>
      </div>
      <span className="mt-0.5 text-sm text-muted-foreground">{t(`assistant_view.file_role_${file.id}`)}</span>

      <div className="mt-4 min-h-0 flex-1 overflow-hidden border-t border-border pt-3 [mask-image:linear-gradient(to_bottom,black_75%,transparent)]">
        {lines.length > 0 ? (
          <ul className="flex flex-col gap-1.5">
            {lines.map((line, i) => (
              <li key={i} className="line-clamp-2 text-sm leading-6 text-foreground/80">
                {line}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm leading-6 text-foreground-faint">{t(`assistant_view.file_empty_${file.id}`)}</p>
        )}
      </div>

      <span className="mt-3 text-sm font-medium text-muted-foreground transition-colors group-hover:text-foreground">
        {t(file.editable ? "assistant_view.file_open_edit" : "assistant_view.file_open_read")}
      </span>
    </button>
  );
}

export function FilesSection({
  files,
  onOpen,
}: {
  files: readonly SoulFile[];
  onOpen: (id: SoulFileId) => void;
}) {
  const t = useT();

  return (
    <Section testId="assistant-files" title={t("assistant_view.files_title")}>
      <p className="-mt-2 mb-4 text-base text-muted-foreground">{t("assistant_view.files_hint")}</p>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {files.map((file) => (
          <li key={file.id}>
            <FilePreview file={file} onOpen={onOpen} />
          </li>
        ))}
      </ul>
    </Section>
  );
}
