import { Children, createContext, isValidElement, lazy, Suspense, useContext, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertCircle, Download, Maximize2, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
import * as Dialog from "@radix-ui/react-dialog";

const RenderedFence = lazy(() => import("@/components/outputs/RenderedFence").then(module => ({ default: module.RenderedFence })));

export type MediaKind = "image" | "video" | "audio";
const EXTENSIONS: Record<string, MediaKind> = {
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", avif: "image", svg: "image", bmp: "image",
  ico: "image", tif: "image", tiff: "image", heic: "image", heif: "image",
  mp4: "video", webm: "video", mov: "video", m4v: "video", ogv: "video",
  avi: "video", mkv: "video", mpeg: "video", mpg: "video", wmv: "video",
  mp3: "audio", wav: "audio", ogg: "audio", m4a: "audio", flac: "audio", aac: "audio",
};

export function safeMediaUrl(raw: string): string | null {
  if (/^data:image\/(?:png|jpeg|gif|webp|avif);base64,[a-zA-Z0-9+/=]+$/.test(raw) && raw.length <= 32 * 1024 * 1024) return raw;
  try {
    const url = new URL(raw, "http://chat.local");
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    // Unarchived native paths must never turn into accidental app requests.
    if (!raw.startsWith("/") && !/^https?:\/\//i.test(raw)) return null;
    return raw;
  } catch { return null; }
}

export function mediaKind(raw: string): MediaKind | null {
  if (!safeMediaUrl(raw)) return null;
  if (raw.startsWith("data:image/")) return "image";
  const url = new URL(raw, "http://chat.local");
  const hint = new URLSearchParams(url.hash.slice(1)).get("jarvis-media")?.split("/")[0];
  if (hint === "image" || hint === "video" || hint === "audio") return hint;
  let path: string;
  try { path = decodeURIComponent(url.pathname); } catch { return null; }
  path = path.replace(/\/(download|raw|view)$/, "");
  return EXTENSIONS[path.split(".").pop()?.toLowerCase() ?? ""] ?? null;
}

function assetKey(raw: string): string {
  try {
    const url = new URL(raw, "http://chat.local");
    url.hash = "";
    if (url.pathname.startsWith("/api/outputs/")) url.searchParams.delete("disposition");
    return url.href;
  } catch { return raw; }
}

/** Recognize media tags without executing arbitrary model-authored HTML. */
export function normalizeMediaMarkup(text: string): string {
  const attr = (markup: string, name: string) => markup.match(new RegExp(`\\b${name}\\s*=\\s*["']([^"']*)["']`, "i"))?.[1];
  const parts = text.split(/(```[\s\S]*?```)/);
  return parts.map((part, i) => i % 2 ? part : part
    .replace(/<(video|audio)\b([^>]*)>([\s\S]*?)<\/\1>/gi, (original, kind: string, head: string, body: string) => {
      const source = attr(head, "src") ?? attr(body.match(/<source\b[^>]*>/i)?.[0] ?? "", "src");
      if (!source || !safeMediaUrl(source)) return original;
      const marker = source.includes("#") ? "&" : "#";
      return `\n\n[${kind}](<${source}${marker}jarvis-media=${kind}%2F${kind === "video" ? "mp4" : "mpeg"}>)\n\n`;
    })
    .replace(/<img\b[^>]*>/gi, original => {
      const src = attr(original, "src");
      if (!src || !safeMediaUrl(src)) return original;
      const alt = (attr(original, "alt") ?? "image").replace(/[\[\]\r\n]/g, " ");
      return `![${alt}](<${src}>)`;
    })).join("");
}

export function MediaPreview({ src, label, kind }: { src: string; label: string; kind: MediaKind }) {
  const t = useT();
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [revision, setRevision] = useState(0);
  const safe = safeMediaUrl(src);
  const failed = !safe || failedSource === src;
  const unavailable = t("chat_media.unavailable");
  const playerRef = useRef<HTMLMediaElement | null>(null);
  useEffect(() => {
    const player = playerRef.current;
    return () => {
      if (!player) return;
      if (!player.paused) player.pause();
      if (player.networkState !== player.NETWORK_EMPTY) {
        player.removeAttribute("src");
        player.load();
      }
    };
  }, [safe, kind, failed, revision]);
  return <Dialog.Root open={expanded} onOpenChange={setExpanded}><span className="my-3 block min-w-0 max-w-full" data-testid="chat-media" data-kind={kind}>
    {failed ? <span role="status" className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
      <AlertCircle aria-hidden className="h-4 w-4 shrink-0" />{unavailable}
      {safe ? <button type="button" onClick={() => { setFailedSource(null); setRevision(value => value + 1); }} className="shrink-0 rounded px-2 py-1 text-foreground underline focus-visible:ring-2 focus-visible:ring-ring">{t("chat_media.retry")}</button> : null}
    </span> : kind === "image" ? <Dialog.Trigger asChild><button type="button"
      aria-label={t("chat_media.expand").replace("{file}", label)}
      className="group relative block max-w-full rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
      <img key={revision} src={safe!} alt={label} loading="lazy" decoding="async" onError={() => { setFailedSource(src); setExpanded(false); }}
        className="m-0 max-h-[520px] max-w-full rounded-lg object-contain" />
      <Maximize2 aria-hidden className="absolute bottom-2 right-2 h-6 w-6 rounded bg-background/90 p-1 text-foreground opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100" />
    </button></Dialog.Trigger> : kind === "video" ? <video ref={node => { playerRef.current = node; }} key={`${revision}:${safe}`} src={safe!} aria-label={label} controls playsInline preload="metadata"
      onError={() => { setFailedSource(src); setExpanded(false); }} className="max-h-[520px] w-full rounded-lg bg-muted" />
      : <audio ref={node => { playerRef.current = node; }} key={`${revision}:${safe}`} src={safe!} aria-label={label} controls preload="metadata" onError={() => { setFailedSource(src); setExpanded(false); }} className="w-full" />}
    {safe ? <a href={safe} download={safe.startsWith("/api/outputs/") ? true : undefined}
      onClick={event => {
        if (!safe.startsWith("/api/outputs/")) {
          event.preventDefault();
          void openExternalUrl(new URL(safe, window.location.href).href);
        }
      }} className="mt-1 inline-flex items-center gap-1.5 text-xs text-muted-foreground underline underline-offset-2">
      <Download aria-hidden className="h-3 w-3" />{t(safe.startsWith("/api/outputs/") ? "chat_media.download" : "chat_media.open_file")} · {label}
    </a> : <span className="text-xs text-muted-foreground">{label}</span>}
    {kind === "image" && safe ? <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-background/90" />
      <Dialog.Content aria-describedby={undefined} className="fixed left-1/2 top-1/2 z-50 grid max-h-[95vh] w-max max-w-[95vw] -translate-x-1/2 -translate-y-1/2 gap-3 overflow-auto rounded-lg border border-border bg-background p-4 text-foreground shadow-xl">
        <Dialog.Title className="pr-8 text-sm">{label}</Dialog.Title>
        <Dialog.Close aria-label={t("common.close")} className="absolute right-3 top-3 rounded p-1 focus-visible:ring-2 focus-visible:ring-ring"><X aria-hidden className="h-4 w-4" /></Dialog.Close>
        <img src={safe} alt={label} className="mx-auto max-h-[80vh] max-w-full object-contain" />
      </Dialog.Content>
    </Dialog.Portal> : null}
  </span></Dialog.Root>;
}

const ImageKeysContext = createContext<ReadonlySet<string>>(new Set());

// Stable component identities are essential: changing these on each streamed
// token would unmount videos, reset playback and discard image error state.
const MARKDOWN_COMPONENTS: Components = {
  img: ({ src = "", alt = "image" }) => <MediaPreview src={src} label={alt || "image"} kind={mediaKind(src) ?? "image"} />,
  a: function MediaLink({ href = "", children }) {
    const imageKeys = useContext(ImageKeysContext);
    const kind = mediaKind(href);
    const label = Children.toArray(children).filter(child => typeof child === "string").join("") || kind || "media";
    if (kind && !imageKeys.has(assetKey(href))) return <MediaPreview src={href} label={label} kind={kind} />;
    return <a href={href} onClick={event => {
      if (/^https?:\/\//i.test(href)) { event.preventDefault(); void openExternalUrl(href); }
    }}>{children}</a>;
  },
  pre: ({ children, node: _node, ...props }) => {
    const child = Children.toArray(children)[0];
    if (isValidElement<{ className?: string; children?: unknown }>(child)) {
      const language = child.props.className?.match(/language-(html|svg)\b/)?.[1];
      if (language === "html" || language === "svg") return <Suspense fallback={<pre>{children}</pre>}><RenderedFence language={language} code={String(child.props.children ?? "").replace(/\n$/, "")} /></Suspense>;
    }
    return <pre {...props}>{children}</pre>;
  },
};

export function ChatMarkdown({ text, className }: { text: string; className?: string }) {
  const body = useMemo(() => normalizeMediaMarkup(text), [text]);
  const imageKeys = useMemo(() => new Set(Array.from(body.matchAll(/!\[[^\]]*\]\(<?([^)>]+)>?\)/g), match => assetKey(match[1]))), [body]);
  return <div className={cn("min-w-0 [overflow-wrap:anywhere]", className)}>
    <ImageKeysContext.Provider value={imageKeys}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={url => url.startsWith("data:") ? safeMediaUrl(url) ?? "" : defaultUrlTransform(url)} components={MARKDOWN_COMPONENTS}>{body}</ReactMarkdown>
    </ImageKeysContext.Provider>
  </div>;
}
