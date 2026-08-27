/**
 * A fenced `html` / `svg` block DRAWN, the way the reader expects a picture
 * to appear, with its source one click away.
 *
 * A worker that answers with a chart or a diagram writes it as inline SVG or
 * a self-contained HTML snippet (the artifact brief tells it to); shown as a
 * code block, the "picture" is a wall of angle brackets. Here the block
 * renders by default and a Rendered / Source switch in its header shows the
 * markup for whoever wants it.
 *
 * Safety is the artifact page's model, mirrored for inline markup:
 * - SVG is drawn through an `<img>` from a data: URL — a browser never runs
 *   script or fetches anything for an SVG-as-image.
 * - HTML runs in an `<iframe srcdoc>` with `sandbox="allow-scripts"` and NO
 *   `allow-same-origin`, so it lives in an opaque origin that cannot reach the
 *   app's cookies, storage or API; a Content-Security-Policy stamped into the
 *   document (`INLINE_HTML_CSP`, the verbatim twin of `ARTIFACT_PAGE_CSP` in
 *   `jarvis/ui/web/artifact_view.py`) shuts every way out — no network, no
 *   forms, no navigation. The frame grows to its content through a
 *   `postMessage` the injected reporter sends; the parent trusts a message
 *   only from that frame's own window, and only up to a ceiling.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { useT } from "@/i18n";
import { useThemeValue, type Theme } from "@/hooks/useTheme";
import { CodeBlock } from "@/components/docs/CodeBlock";

export type FenceLanguage = "html" | "svg";

/** Twin of `ARTIFACT_PAGE_CSP` (jarvis/ui/web/artifact_view.py) — keep in step. */
export const INLINE_HTML_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; " +
  "form-action 'none'; frame-src 'none'; base-uri 'none';";

/** The message type the injected reporter posts with the document's height. */
export const INLINE_HTML_SIZE_MESSAGE = "jarvis-inline-html-size";

const MIN_FRAME_PX = 96;
const MAX_FRAME_PX = 720;

/** The app's card surface in each palette, for a fragment that brings no page of its own. */
const FRAGMENT_SKIN: Record<Theme, { bg: string; ink: string }> = {
  light: { bg: "#ffffff", ink: "#141413" },
  dark: { bg: "#30302e", ink: "#f5f4ef" },
};

function headBits(): string {
  return (
    '<meta charset="utf-8">' +
    `<meta http-equiv="Content-Security-Policy" content="${INLINE_HTML_CSP}">`
  );
}

function reporterScript(token: string): string {
  const safe = token.replace(/[^\w:-]/g, "");
  return (
    "<script>(function(){var h=document.documentElement;function r(){try{" +
    `parent.postMessage({type:"${INLINE_HTML_SIZE_MESSAGE}",token:"${safe}",` +
    'height:h.scrollHeight},"*")}catch(e){}}' +
    "try{new ResizeObserver(r).observe(h)}catch(e){}" +
    'addEventListener("load",r);r()})();</script>'
  );
}

/**
 * The document the frame loads: the snippet as-is when it is a whole page
 * (CSP and theme stamp added to its head), otherwise wrapped in a minimal
 * page on the app's card surface so a bare `<div>` or `<svg>` reads in both
 * palettes. Exported for the tests.
 */
export function wrapInlineHtml(html: string, theme: Theme, token: string): string {
  const script = reporterScript(token);
  if (/<html[\s>]/i.test(html)) {
    let doc = html;
    if (!/<html[^>]*\sdata-theme=/i.test(doc)) {
      doc = doc.replace(/<html(\s[^>]*)?>/i, (_m, attrs: string | undefined) =>
        `<html${attrs ?? ""} data-theme="${theme}">`,
      );
    }
    if (/<head[^>]*>/i.test(doc)) {
      doc = doc.replace(/<head[^>]*>/i, (m) => `${m}${headBits()}`);
    } else {
      doc = doc.replace(/<html[^>]*>/i, (m) => `${m}<head>${headBits()}</head>`);
    }
    return /<\/body>/i.test(doc)
      ? doc.replace(/<\/body>/i, `${script}</body>`)
      : `${doc}${script}`;
  }
  const skin = FRAGMENT_SKIN[theme];
  return (
    `<!doctype html><html lang="en" data-theme="${theme}"><head>${headBits()}` +
    `<style>:root{color-scheme:${theme}}html,body{margin:0}` +
    `body{padding:12px;background:${skin.bg};color:${skin.ink};` +
    'font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}' +
    "svg,img,canvas,video{max-width:100%}</style></head>" +
    `<body>${html}${script}</body></html>`
  );
}

/** An SVG snippet as an inert picture. */
export function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** The sandboxed frame an inline HTML snippet runs in, sized to its content. */
export function InlineHtmlFrame({ html, title }: { html: string; title: string }) {
  const theme = useThemeValue();
  const token = useId();
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(MIN_FRAME_PX * 2);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const frame = ref.current;
      if (!frame || event.source !== frame.contentWindow) return;
      const data: unknown = event.data;
      if (
        typeof data !== "object" ||
        data === null ||
        (data as { type?: unknown }).type !== INLINE_HTML_SIZE_MESSAGE ||
        (data as { token?: unknown }).token !== token.replace(/[^\w:-]/g, "")
      ) {
        return;
      }
      const reported = Number((data as { height?: unknown }).height);
      if (!Number.isFinite(reported)) return;
      setHeight(Math.min(MAX_FRAME_PX, Math.max(MIN_FRAME_PX, Math.ceil(reported) + 2)));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [token]);

  const doc = useMemo(() => wrapInlineHtml(html, theme, token), [html, theme, token]);

  return (
    <iframe
      ref={ref}
      key={theme}
      title={title}
      srcDoc={doc}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      style={{ height }}
      className="block w-full border-0 bg-transparent"
      data-testid="inline-html-frame"
    />
  );
}

export function RenderedFence({ language, code }: { language: FenceLanguage; code: string }) {
  const t = useT();
  const [mode, setMode] = useState<"rendered" | "source">("rendered");
  const [copied, setCopied] = useState(false);

  const copy = () => {
    void robustCopy(code).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const segment = (value: "rendered" | "source", label: string) => (
    <button
      type="button"
      aria-pressed={mode === value}
      onClick={() => setMode(value)}
      className={cn(
        "rounded-sm px-2 py-0.5 text-[10px] font-medium transition-colors",
        mode === value
          ? "bg-background text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
    </button>
  );

  return (
    <div
      className="not-prose my-4 overflow-hidden rounded-md border border-border bg-muted/40"
      data-testid="rendered-fence"
      data-language={language}
      data-mode={mode}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/40 bg-muted/20 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{language}</span>
        <div className="flex items-center gap-1.5">
          <div role="group" className="inline-flex rounded border border-border/60 bg-muted/40 p-0.5">
            {segment("rendered", t("outputs_view.fence_rendered"))}
            {segment("source", t("outputs_view.fence_source"))}
          </div>
          <button
            type="button"
            onClick={copy}
            className="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title={t("docs_content.copy_code")}
            aria-label={copied ? t("docs_content.code_copied") : t("docs_content.copy_code")}
          >
            {copied ? (
              <Check className="h-3 w-3 text-emerald-400" aria-hidden="true" />
            ) : (
              <Copy className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
      {mode === "source" ? (
        <CodeBlock language={language} code={code} chrome={false} />
      ) : language === "svg" ? (
        <div className="flex justify-center bg-card p-4">
          <img src={svgDataUrl(code)} alt="SVG" className="max-w-full" data-testid="inline-svg" />
        </div>
      ) : (
        <InlineHtmlFrame html={code} title="HTML" />
      )}
    </div>
  );
}
