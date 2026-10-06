import { useEffect, useMemo, useState, type AnchorHTMLAttributes, type ImgHTMLAttributes, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ExternalLink, FileWarning, Loader2 } from "lucide-react";

import { useT } from "@/i18n";
import { fetchWorkspaceFilePreview, workspaceFileUrl, type WorkspaceFilePreviewResponse } from "@/lib/agenticIdeApi";
import { useCodeEditorStore } from "@/store/codeEditor";
import type { ViewKind, RenderKind } from "./fileKinds";
import { modelOf } from "./editorModels";

const baseName = (path: string) => path.split("/").pop() ?? path;
const folderOf = (path: string) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : "");

/** `../img/a.png` relative to `docs/guide.md`, as a workspace path; null outside it. */
function resolveRelative(fromFile: string, href: string): string | null {
  let target = href.split("#", 1)[0].split("?", 1)[0];
  try {
    target = decodeURIComponent(target);
  } catch {
    return null;
  }
  if (!target) return null;
  const parts = target.startsWith("/") ? target.slice(1).split("/") : `${folderOf(fromFile)}${target}`.split("/");
  const resolved: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!resolved.length) return null;
      resolved.pop();
    } else resolved.push(part);
  }
  return resolved.length ? resolved.join("/") : null;
}

export function OpenExternallyButton({ onClick }: { onClick: () => void }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      {t("code_editor.open_externally")}
    </button>
  );
}

/** Images, PDFs, audio and video, straight from the workspace file. */
export function MediaViewer({
  kind,
  workspaceId,
  path,
  onOpenExternally,
}: {
  kind: Exclude<ViewKind, "document">;
  workspaceId: string;
  path: string;
  onOpenExternally: () => void;
}) {
  const t = useT();
  const [failed, setFailed] = useState(false);
  // A changed file on disk shows up when the tab is opened again.
  const url = useMemo(() => `${workspaceFileUrl(workspaceId, path)}&v=${Date.now()}`, [workspaceId, path]);
  useEffect(() => setFailed(false), [url]);
  if (failed) {
    return (
      <Notice message={t("code_editor.media_failed")}>
        <OpenExternallyButton onClick={onOpenExternally} />
      </Notice>
    );
  }
  if (kind === "pdf") {
    return <iframe data-testid="code-editor-pdf" title={baseName(path)} src={url} className="h-full w-full border-0 bg-white" />;
  }
  return (
    <div className="flex h-full w-full items-center justify-center overflow-auto p-6">
      {kind === "image" && (
        <img src={url} alt={baseName(path)} onError={() => setFailed(true)} className="max-h-full max-w-full rounded-md border border-border/60 object-contain" />
      )}
      {kind === "audio" && <audio src={url} controls onError={() => setFailed(true)} className="w-full max-w-xl" />}
      {kind === "video" && <video src={url} controls onError={() => setFailed(true)} className="max-h-full max-w-full bg-black" />}
    </div>
  );
}

/**
 * The text the server can read out of a file that is not editable here: an
 * Office document, a file too large to edit, or a binary (shown as hex).
 */
export function ExtractedPreview({
  workspaceId,
  path,
  note,
  onOpenExternally,
}: {
  workspaceId: string;
  path: string;
  note: string;
  onOpenExternally: () => void;
}) {
  const t = useT();
  const [preview, setPreview] = useState<WorkspaceFilePreviewResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setPreview(null);
    setError("");
    fetchWorkspaceFilePreview(workspaceId, path)
      .then((answer) => alive && setPreview(answer))
      .catch((err: unknown) => alive && setError((err as Error).message));
    return () => {
      alive = false;
    };
  }, [workspaceId, path]);

  return (
    <div className="scrollbar-jarvis h-full overflow-auto">
      <div className="sticky top-0 z-[1] flex flex-wrap items-center gap-2 border-b border-border/60 bg-background/95 px-4 py-2 text-xs text-muted-foreground backdrop-blur-sm">
        <FileWarning className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1">{note}</span>
        <OpenExternallyButton onClick={onOpenExternally} />
      </div>
      {error ? (
        <p className="px-4 py-3 text-xs text-destructive">{error}</p>
      ) : !preview ? (
        <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          {t("code_editor.loading")}
        </p>
      ) : preview.text ? (
        <>
          {preview.truncated && <p className="px-4 pt-3 text-xs text-muted-foreground">{t("code_editor.truncated")}</p>}
          <pre data-testid="code-editor-extracted" className="whitespace-pre-wrap break-words px-4 py-3 font-mono text-[12.5px] leading-relaxed text-foreground">
            {preview.text}
          </pre>
        </>
      ) : preview.hex_preview ? (
        <pre data-testid="code-editor-extracted" className="whitespace-pre-wrap break-words px-4 py-3 font-mono text-[12.5px] leading-relaxed text-muted-foreground">
          {preview.hex_preview}
        </pre>
      ) : (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("code_editor.empty")}</p>
      )}
    </div>
  );
}

/** The live buffer's text, re-read on every edit. */
function useModelText(fileKey: string): string {
  const [text, setText] = useState(() => modelOf(fileKey)?.getValue() ?? "");
  useEffect(() => {
    const model = modelOf(fileKey);
    if (!model) return;
    setText(model.getValue());
    const listener = model.onDidChangeContent(() => setText(model.getValue()));
    return () => listener.dispose();
  }, [fileKey]);
  return text;
}

/**
 * Markdown, HTML or SVG shown rendered from the live buffer (Ctrl+Shift+V).
 * HTML runs in a sandbox without scripts; SVG is drawn as an image, which
 * never runs script either.
 */
export function RenderedPreview({
  kind,
  fileKey,
  workspaceId,
  path,
}: {
  kind: RenderKind;
  fileKey: string;
  workspaceId: string;
  path: string;
}) {
  const t = useT();
  const text = useModelText(fileKey);
  const svgUrl = useMemo(
    () => (kind === "svg" ? URL.createObjectURL(new Blob([text], { type: "image/svg+xml" })) : ""),
    [kind, text],
  );
  useEffect(() => () => {
    if (svgUrl) URL.revokeObjectURL(svgUrl);
  }, [svgUrl]);

  const components = useMemo(
    () => ({
      a: ({ href, children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
        if (!href || /^(https?:|mailto:)/i.test(href)) {
          return <a href={href} target="_blank" rel="noreferrer noopener" {...props}>{children}</a>;
        }
        if (href.startsWith("#")) return <a href={href} {...props}>{children}</a>;
        return (
          <a
            href={href}
            {...props}
            onClick={(event) => {
              event.preventDefault();
              const target = resolveRelative(path, href);
              if (target) useCodeEditorStore.getState().openFile(workspaceId, target, { preview: false });
            }}
          >
            {children}
          </a>
        );
      },
      img: ({ src, alt, ...props }: ImgHTMLAttributes<HTMLImageElement>) => {
        // Remote images stay off: a document must not call out to the web.
        if (!src || /^[a-z][a-z0-9+.-]*:/i.test(src) || src.startsWith("//")) {
          return <span className="text-xs text-muted-foreground">[{alt || src}]</span>;
        }
        const target = resolveRelative(path, src);
        return target ? <img src={workspaceFileUrl(workspaceId, target)} alt={alt ?? ""} loading="lazy" {...props} /> : null;
      },
    }),
    [path, workspaceId],
  );

  if (kind === "html") {
    return <iframe data-testid="code-editor-rendered" title={t("code_editor.preview")} sandbox="" srcDoc={text} className="h-full w-full border-0 bg-white" />;
  }
  if (kind === "svg") {
    return (
      <div data-testid="code-editor-rendered" className="flex h-full w-full items-center justify-center overflow-auto p-6">
        <img src={svgUrl} alt={baseName(path)} className="max-h-full max-w-full" />
      </div>
    );
  }
  return (
    <div className="scrollbar-jarvis h-full overflow-auto">
      <article
        data-testid="code-editor-rendered"
        className="prose prose-neutral mx-auto max-w-3xl px-8 py-6 text-sm dark:prose-invert prose-code:text-foreground prose-pre:border prose-pre:border-border prose-pre:bg-card"
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
          {text}
        </ReactMarkdown>
      </article>
    </div>
  );
}

export function Notice({ message, children }: { message: string; children?: ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-6">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <FileWarning className="h-8 w-8 text-muted-foreground/70" aria-hidden />
        <p className="text-sm text-muted-foreground">{message}</p>
        {children}
      </div>
    </div>
  );
}
