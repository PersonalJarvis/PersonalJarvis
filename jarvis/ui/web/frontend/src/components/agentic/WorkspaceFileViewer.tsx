import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileText, Loader2, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useT } from "@/i18n";
import {
  fetchWorkspaceFilePreview,
  workspaceFileUrl,
  type WorkspaceFilePreviewResponse,
} from "@/lib/agenticIdeApi";

export type WorkspaceFileKind =
  | "markdown"
  | "text"
  | "pdf"
  | "image"
  | "audio"
  | "video"
  | "html"
  | "document"
  | "binary";

const EXTENSIONS: Record<WorkspaceFileKind, Set<string>> = {
  markdown: new Set(["md", "mdown", "mdx", "mkd", "markdown"]),
  text: new Set([
    "c", "cc", "conf", "cpp", "cs", "css", "csv", "env", "go", "h",
    "hpp", "ini", "java", "js", "json", "jsx", "log", "lua", "mjs",
    "php", "properties", "py", "rb", "rs", "scss", "sh", "sql", "svg",
    "toml", "ts", "tsx", "txt", "xml", "yaml", "yml",
  ]),
  pdf: new Set(["pdf"]),
  image: new Set(["avif", "bmp", "gif", "ico", "jpeg", "jpg", "png", "webp"]),
  audio: new Set(["aac", "flac", "m4a", "mp3", "oga", "ogg", "wav", "weba"]),
  video: new Set(["m4v", "mov", "mp4", "ogv", "webm"]),
  html: new Set(["htm", "html"]),
  document: new Set([
    "doc", "docx", "epub", "odp", "ods", "odt", "ppt", "pptx", "rtf",
    "xls", "xlsx",
  ]),
  binary: new Set(),
};

export function classifyWorkspaceFile(path: string): WorkspaceFileKind {
  const name = path.split("/").at(-1) ?? path;
  const extension = name.includes(".") ? name.split(".").at(-1)?.toLowerCase() ?? "" : "";
  for (const kind of Object.keys(EXTENSIONS) as WorkspaceFileKind[]) {
    if (EXTENSIONS[kind].has(extension)) return kind;
  }
  return "binary";
}

interface WorkspaceFileViewerProps {
  workspaceId: string;
  path: string;
  onClose: () => void;
  onOpenFile: (path: string) => void;
}

export function WorkspaceFileViewer({
  workspaceId,
  path,
  onClose,
  onOpenFile,
}: WorkspaceFileViewerProps) {
  const t = useT();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const previewRequest = useRef(0);
  const [preview, setPreview] = useState<WorkspaceFilePreviewResponse | null>(null);
  const [error, setError] = useState(false);
  const [nativeFailed, setNativeFailed] = useState(false);
  const kind = classifyWorkspaceFile(path);
  const name = path.split("/").at(-1) ?? path;
  const fileUrl = workspaceFileUrl(workspaceId, path);
  const needsExtractedPreview =
    nativeFailed || ["markdown", "text", "document", "binary"].includes(kind);

  const loadPreview = useCallback(async () => {
    const request = ++previewRequest.current;
    setError(false);
    setPreview(null);
    try {
      const nextPreview = await fetchWorkspaceFilePreview(workspaceId, path);
      if (previewRequest.current === request) setPreview(nextPreview);
    } catch {
      if (previewRequest.current === request) setError(true);
    }
  }, [path, workspaceId]);

  useEffect(() => {
    previewRequest.current += 1;
    setError(false);
    setPreview(null);
    setNativeFailed(false);
    closeRef.current?.focus();
  }, [path, workspaceId]);

  useEffect(
    () => () => {
      previewRequest.current += 1;
    },
    [],
  );

  useEffect(() => {
    if (needsExtractedPreview) void loadPreview();
  }, [loadPreview, needsExtractedPreview]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const openRelativeFile = useCallback(
    (href: string) => {
      const base = path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
      let relative = href.split("#", 1)[0];
      try {
        relative = decodeURIComponent(relative);
      } catch {
        return;
      }
      const parts = `${base}${relative}`.split("/");
      const resolved: string[] = [];
      for (const part of parts) {
        if (!part || part === ".") continue;
        if (part === "..") resolved.pop();
        else resolved.push(part);
      }
      if (resolved.length) onOpenFile(resolved.join("/"));
    },
    [onOpenFile, path],
  );

  const markdownComponents = useMemo(
    () => ({
      a: ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
        const external = !!href && /^(https?:|mailto:)/i.test(href);
        if (external) {
          return <a href={href} target="_blank" rel="noreferrer noopener" {...props}>{children}</a>;
        }
        return (
          <a
            href={href}
            {...props}
            onClick={(event) => {
              event.preventDefault();
              if (href) openRelativeFile(href);
            }}
          >
            {children}
          </a>
        );
      },
      img: ({ src, alt, ...props }: React.ImgHTMLAttributes<HTMLImageElement>) => {
        const external = !!src && (/^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith("//"));
        if (external) {
          return (
            <span role="img" aria-label={alt ?? ""} className="text-xs text-muted-foreground">
              {t("agentic_grid.viewer.remote_image").replace("{0}", alt || src)}
            </span>
          );
        }
        const base = path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "";
        const resolvedSrc = src ? workspaceFileUrl(workspaceId, `${base}${src}`) : undefined;
        return <img src={resolvedSrc} alt={alt ?? ""} loading="lazy" {...props} />;
      },
    }),
    [openRelativeFile, path, t, workspaceId],
  );

  return (
    <section
      data-testid="workspace-file-viewer"
      aria-label={`${t("agentic_grid.viewer.title")}: ${name}`}
      className="absolute inset-0 z-40 flex min-h-0 flex-col bg-background/95 backdrop-blur-xl"
    >
      <header className="flex h-11 shrink-0 items-center gap-3 border-b border-border/70 bg-card/50 px-3">
        <FileText className="h-4 w-4 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-foreground">{name}</div>
          <div className="truncate text-[10px] text-muted-foreground">{path}</div>
        </div>
        <span className="rounded-full border border-border/70 bg-secondary/60 px-2 py-0.5 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
          {kind}
        </span>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label={t("agentic_grid.viewer.close")}
          title={t("agentic_grid.viewer.close")}
          className="flex h-8 w-8 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto bg-background/35 scrollbar-jarvis">
        {error ? (
          <div role="alert" className="m-auto flex h-full max-w-md flex-col items-center justify-center gap-3 p-8 text-center text-sm text-muted-foreground">
            <AlertTriangle className="h-7 w-7 text-destructive" aria-hidden />
            <p>{t("agentic_grid.viewer.failed")}</p>
            <button type="button" onClick={() => void loadPreview()} className="rounded-control border border-border bg-secondary px-3 py-1.5 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
              {t("common.retry")}
            </button>
          </div>
        ) : needsExtractedPreview && !preview ? (
          <div role="status" className="flex h-full items-center justify-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
            {t("agentic_grid.viewer.loading")}
          </div>
        ) : (
          <ViewerContent
            kind={nativeFailed ? "binary" : kind}
            fileUrl={fileUrl}
            name={name}
            preview={preview}
            markdownComponents={markdownComponents}
            onNativeError={() => setNativeFailed(true)}
          />
        )}
      </div>
    </section>
  );
}

function ViewerContent({
  kind,
  fileUrl,
  name,
  preview,
  markdownComponents,
  onNativeError,
}: {
  kind: WorkspaceFileKind;
  fileUrl: string;
  name: string;
  preview: WorkspaceFilePreviewResponse | null;
  markdownComponents: Record<string, React.ElementType>;
  onNativeError: () => void;
}) {
  const t = useT();
  if (kind === "pdf") {
    return <iframe title={name} src={fileUrl} className="h-full min-h-[28rem] w-full border-0 bg-white" />;
  }
  if (kind === "image") {
    return <div className="flex min-h-full items-center justify-center p-6"><img src={fileUrl} alt={name} onError={onNativeError} className="max-h-full max-w-full object-contain shadow-2xl" /></div>;
  }
  if (kind === "audio") {
    return <div className="flex min-h-full items-center justify-center p-8"><audio src={fileUrl} controls onError={onNativeError} className="w-full max-w-xl" /></div>;
  }
  if (kind === "video") {
    return <div className="flex min-h-full items-center justify-center p-6"><video src={fileUrl} controls onError={onNativeError} className="max-h-full max-w-full bg-black shadow-2xl" /></div>;
  }
  if (kind === "html") {
    return <iframe title={name} src={fileUrl} sandbox="" className="h-full min-h-[28rem] w-full border-0 bg-white" />;
  }

  const text = preview?.text ?? "";
  return (
    <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
      {preview?.truncated && (
        <div role="note" className="mb-4 border-l-2 border-amber-500 bg-amber-500/5 px-3 py-2 text-xs text-amber-500">
          {t("agentic_grid.viewer.truncated")}
        </div>
      )}
      {kind === "markdown" ? (
        <article className="prose prose-neutral max-w-none text-sm dark:prose-invert prose-a:text-primary prose-code:text-foreground prose-pre:border prose-pre:border-border prose-pre:bg-card/80">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{text}</ReactMarkdown>
        </article>
      ) : text ? (
        <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground selection:bg-primary/30">{text}</pre>
      ) : preview?.hex_preview ? (
        <div>
          <p className="mb-3 text-xs text-muted-foreground">{t("agentic_grid.viewer.binary")}</p>
          <pre className="whitespace-pre-wrap break-words rounded-card border border-border bg-card/70 p-4 font-mono text-xs leading-relaxed text-foreground">{preview.hex_preview}</pre>
        </div>
      ) : (
        <p className="text-center text-sm text-muted-foreground">{t("agentic_grid.viewer.empty")}</p>
      )}
    </div>
  );
}
