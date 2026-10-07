import { useRef, useState, type FormEvent } from "react";
import { ExternalLink, Globe, RotateCw } from "lucide-react";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { normalizeBrowserAddress } from "./browserAddress";

const URL_KEY = "jarvis.agenticIde.browserUrl.v1";

function storedUrl(): string {
  try {
    return localStorage.getItem(URL_KEY) ?? "";
  } catch {
    return "";
  }
}

function persistUrl(url: string): void {
  try {
    localStorage.setItem(URL_KEY, url);
  } catch {
    /* a convenience only: the page still loads for this session */
  }
}

const TOOL_BTN =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors " +
  "hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
  "disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent";

/**
 * The side panel's Browser surface: an address bar over an embedded page.
 *
 * Built for local previews (a dev server on localhost) and plain pages. Many
 * public sites forbid being shown inside another app; for those the "open in
 * your browser" button hands the address to the system browser. The frame may
 * not navigate the app itself (no `allow-top-navigation`).
 */
export function BrowserTab() {
  const t = useT();
  const [url, setUrl] = useState(storedUrl);
  const [draft, setDraft] = useState(url);
  // Bumped by reload: a new key remounts the frame, which works cross-origin.
  const [generation, setGeneration] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const go = (event: FormEvent) => {
    event.preventDefault();
    const next = normalizeBrowserAddress(draft);
    if (!next) return;
    setDraft(next);
    setUrl(next);
    persistUrl(next);
    if (next === url) setGeneration((value) => value + 1);
    input.current?.blur();
  };

  return (
    <div data-testid="ide-browser-tab" className="flex h-full min-h-0 flex-col">
      <form onSubmit={go} className="flex h-10 shrink-0 items-center gap-1 border-b border-border/60 px-2">
        <button type="button" onClick={() => setGeneration((value) => value + 1)} disabled={!url}
          aria-label={t("ide_side_panel.browser.reload")} title={t("ide_side_panel.browser.reload")}
          data-testid="ide-browser-reload" className={TOOL_BTN}>
          <RotateCw className="h-3.5 w-3.5" aria-hidden />
        </button>
        <input
          ref={input}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onFocus={(event) => event.target.select()}
          spellCheck={false}
          autoComplete="off"
          placeholder={t("ide_side_panel.browser.placeholder")}
          aria-label={t("ide_side_panel.browser.address")}
          data-testid="ide-browser-address"
          className="h-7 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <button type="button" onClick={() => void openExternalUrl(url)} disabled={!url}
          aria-label={t("ide_side_panel.browser.open_external")} title={t("ide_side_panel.browser.open_external")}
          data-testid="ide-browser-external" className={TOOL_BTN}>
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        </button>
      </form>
      {url ? (
        // White in both themes on purpose: it is the page's own canvas, as in
        // any browser. A page without a background would otherwise draw its
        // default black text straight onto the dark theme.
        <iframe
          key={`${url}#${generation}`}
          src={url}
          title={t("ide_side_panel.tabs.browser")}
          data-testid="ide-browser-frame"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads"
          referrerPolicy="no-referrer"
          className="min-h-0 w-full flex-1 border-0 bg-white"
        />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
          <Globe className="h-5 w-5 text-muted-foreground" aria-hidden />
          <p className="text-sm text-foreground">{t("ide_side_panel.browser.empty_title")}</p>
          <p className="max-w-xs text-xs text-muted-foreground">{t("ide_side_panel.browser.empty_hint")}</p>
        </div>
      )}
    </div>
  );
}
