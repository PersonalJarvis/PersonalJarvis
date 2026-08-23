/**
 * The read-only file card of a detail page: a file picker ("plugin.json ▾ ·
 * 3 files"), a preview/source toggle and the file itself. Markdown renders as
 * prose in preview, JSON and everything else as monospace text; source is
 * always the raw text.
 *
 * Plugins and MCP servers have no folder on disk, so the backend writes their
 * defining pieces out as files (see `/api/marketplace/plugins/{id}/files` and
 * `/api/mcps/{name}/files`); this card shows them the way the Skills card
 * shows SKILL.md, so "what is this made of" reads the same across the section.
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, Code2, Eye, FileCode, FileJson, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";
import { ActionMenu, Panel } from "@/components/extensions/primitives";

export interface CardFile {
  path: string;
  text: string;
  size?: number;
  truncated?: boolean;
}

type ViewMode = "preview" | "source";

function iconFor(path: string) {
  if (/\.(md|markdown)$/i.test(path)) return FileText;
  if (/\.json$/i.test(path)) return FileJson;
  return FileCode;
}

/** Rendered markdown — the body of a SKILL.md, a usage card, a README. */
export function MarkdownBody({ text }: { text: string }) {
  return (
    <article className="prose prose-neutral max-w-none text-[15px] leading-relaxed dark:prose-invert prose-headings:font-display prose-headings:tracking-tight prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg prose-a:text-primary prose-code:text-foreground prose-pre:border prose-pre:border-border prose-pre:bg-card/80">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a href={href} target="_blank" rel="noreferrer noopener" {...props}>
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </article>
  );
}

export function ModeButton({
  active,
  label,
  onClick,
  children,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "grid h-7 w-8 place-items-center rounded transition-colors",
        active ? "bg-sheen/[0.12] text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

export function FileCard({
  files,
  loading,
  error,
  emptyLabel,
  className,
}: {
  files: CardFile[];
  loading?: boolean;
  error?: string | null;
  emptyLabel?: string;
  className?: string;
}) {
  const t = useT();
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("preview");

  // Follow the data: the first file once it arrives, and never a path that
  // vanished from the list.
  useEffect(() => {
    if (files.length === 0) {
      setOpenPath(null);
      return;
    }
    if (!openPath || !files.some((f) => f.path === openPath)) setOpenPath(files[0].path);
  }, [files, openPath]);

  const open = useMemo(() => files.find((f) => f.path === openPath) ?? null, [files, openPath]);
  const isMarkdown = open ? /\.(md|markdown)$/i.test(open.path) : false;
  const OpenIcon = open ? iconFor(open.path) : FileText;

  return (
    <Panel className={className}>
      <div className="flex items-center gap-3 border-b border-border/70 px-3 py-2">
        <ActionMenu
          label={t("extensions.files_menu")}
          align="start"
          actions={files.map((f) => {
            const Icon = iconFor(f.path);
            return {
              id: f.path,
              label: f.path,
              icon: <Icon className="h-3.5 w-3.5" />,
              onSelect: () => setOpenPath(f.path),
            };
          })}
          trigger={({ open: menuOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              disabled={files.length === 0}
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              className="inline-flex h-8 max-w-[360px] items-center gap-2 rounded-md bg-sheen/[0.07] px-3 font-mono text-[13px] text-foreground hover:bg-sheen/[0.12] disabled:opacity-60"
            >
              <OpenIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="truncate">{open?.path ?? "—"}</span>
              <ChevronDown
                className={cn("h-3.5 w-3.5 shrink-0 transition-transform", menuOpen && "rotate-180")}
              />
            </button>
          )}
        />
        <span className="text-sm text-muted-foreground">
          {files.length === 1
            ? t("extensions.file_one")
            : fill(t("extensions.files"), { n: files.length })}
        </span>
        <div
          className="ml-auto flex items-center rounded-md bg-sheen/[0.05] p-0.5"
          role="tablist"
          aria-label={t("extensions.view_mode")}
        >
          <ModeButton
            active={mode === "preview"}
            label={t("extensions.view_preview")}
            onClick={() => setMode("preview")}
          >
            <Eye className="h-4 w-4" />
          </ModeButton>
          <ModeButton
            active={mode === "source"}
            label={t("extensions.view_source")}
            onClick={() => setMode("source")}
          >
            <Code2 className="h-4 w-4" />
          </ModeButton>
        </div>
      </div>

      {loading && (
        <div className="px-6 py-6 text-sm text-muted-foreground">{t("common.loading")}</div>
      )}
      {!loading && error && (
        <div className="px-6 py-6 text-sm text-destructive">{error}</div>
      )}
      {!loading && !error && !open && (
        <div className="px-6 py-6 text-sm text-muted-foreground">
          {emptyLabel ?? t("extensions.no_files")}
        </div>
      )}
      {!loading && !error && open && (
        mode === "preview" && isMarkdown ? (
          <div className="max-h-[60vh] overflow-auto px-6 py-5">
            <MarkdownBody text={open.text} />
          </div>
        ) : (
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words px-6 py-5 font-mono text-[13px] leading-relaxed">
            {open.text}
            {open.truncated ? "\n…" : ""}
          </pre>
        )
      )}
    </Panel>
  );
}
