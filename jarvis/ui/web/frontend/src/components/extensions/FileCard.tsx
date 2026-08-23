/**
 * The read-only file card of a detail page: a folder tree on the left (the
 * plugin's or server's pieces, laid out the way a folder would hold them),
 * the chosen file on the right, and a preview/source toggle. Markdown renders
 * as prose in preview, JSON and everything else as monospace text; source is
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
import {
  ChevronDown,
  ChevronRight,
  Code2,
  Eye,
  FileCode,
  FileJson,
  FileText,
  Folder,
  FolderOpen,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";
import { Panel } from "@/components/extensions/primitives";

export interface CardFile {
  path: string;
  text: string;
  size?: number;
  truncated?: boolean;
}

type ViewMode = "preview" | "source";

export function fileIconFor(path: string) {
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

// ---------------------------------------------------------------------------
// Folder tree
// ---------------------------------------------------------------------------

interface TreeNode {
  name: string;
  path: string;
  children?: TreeNode[];
}

/** Folders first (sorted), then files in the order they were given. */
function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode[] = [];
  for (const path of paths) {
    const parts = path.split("/").filter(Boolean);
    let level = root;
    let prefix = "";
    parts.forEach((part, i) => {
      prefix = prefix ? `${prefix}/${part}` : part;
      const leaf = i === parts.length - 1;
      let node = level.find((n) => n.name === part && Boolean(n.children) !== leaf);
      if (!node) {
        node = leaf ? { name: part, path } : { name: part, path: prefix, children: [] };
        level.push(node);
      }
      if (!leaf) level = node.children!;
    });
  }
  const sort = (nodes: TreeNode[]): TreeNode[] => {
    const folders = nodes.filter((n) => n.children).sort((a, b) => a.name.localeCompare(b.name));
    const files = nodes.filter((n) => !n.children);
    folders.forEach((f) => (f.children = sort(f.children!)));
    return [...folders, ...files];
  };
  return sort(root);
}

/**
 * The folder column: a root folder named after the thing, its files beneath,
 * sub-folders collapsible. One click opens a file; the open one is marked.
 */
export function FileTree({
  rootLabel,
  paths,
  openPath,
  onOpen,
  className,
}: {
  rootLabel: string;
  paths: string[];
  openPath: string | null;
  onOpen: (path: string) => void;
  className?: string;
}) {
  const t = useT();
  const tree = useMemo(() => buildTree(paths), [paths]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (p: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });

  const renderNodes = (nodes: TreeNode[], depth: number): ReactNode =>
    nodes.map((node) => {
      const pad = { paddingLeft: `${12 + depth * 14}px` };
      if (node.children) {
        const closed = collapsed.has(node.path);
        const Icon = closed ? Folder : FolderOpen;
        return (
          <li key={`d:${node.path}`}>
            <button
              type="button"
              onClick={() => toggle(node.path)}
              aria-expanded={!closed}
              style={pad}
              className="flex h-8 w-full items-center gap-1.5 pr-2 text-left text-[13px] text-foreground/85 hover:bg-sheen/[0.05]"
            >
              {closed ? (
                <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              )}
              <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{node.name}</span>
            </button>
            {!closed && <ul>{renderNodes(node.children, depth + 1)}</ul>}
          </li>
        );
      }
      const Icon = fileIconFor(node.name);
      const active = node.path === openPath;
      return (
        <li key={`f:${node.path}`}>
          <button
            type="button"
            onClick={() => onOpen(node.path)}
            aria-current={active ? "true" : undefined}
            title={node.path}
            style={{ paddingLeft: `${12 + depth * 14 + 20}px` }}
            className={cn(
              "flex h-8 w-full items-center gap-2 pr-2 text-left font-mono text-[13px] transition-colors",
              active
                ? "bg-sheen/[0.08] text-foreground"
                : "text-foreground/80 hover:bg-sheen/[0.05] hover:text-foreground",
            )}
          >
            <Icon className={cn("h-4 w-4 shrink-0", active ? "text-foreground" : "text-muted-foreground")} />
            <span className="truncate">{node.name}</span>
          </button>
        </li>
      );
    });

  return (
    <nav aria-label={t("extensions.files_menu")} className={cn("min-w-0", className)}>
      <div className="flex h-8 items-center gap-1.5 px-3 text-[13px] font-medium text-foreground">
        <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
        <span className="truncate">{rootLabel}</span>
      </div>
      <ul>{renderNodes(tree, 0)}</ul>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export function FileCard({
  rootLabel,
  files,
  loading,
  error,
  emptyLabel,
  className,
}: {
  /** The folder's name — the plugin id or the server name. */
  rootLabel: string;
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
  const OpenIcon = open ? fileIconFor(open.path) : FileText;

  return (
    <Panel className={className}>
      <div className="flex items-center gap-3 border-b border-border/70 px-3 py-2">
        <span className="inline-flex h-8 min-w-0 items-center gap-2 px-1 font-mono text-[13px] text-foreground">
          <OpenIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="truncate">{open?.path ?? "—"}</span>
        </span>
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

      <div className="flex min-h-[160px]">
        <FileTree
          className="w-56 shrink-0 border-r border-border/70 py-2"
          rootLabel={rootLabel}
          paths={files.map((f) => f.path)}
          openPath={openPath}
          onOpen={setOpenPath}
        />
        <div className="min-w-0 flex-1">
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
        </div>
      </div>
    </Panel>
  );
}
