import { useRef, useState, type FormEvent } from "react";
import { ExternalLink, Globe2, Home, Loader2, ShieldCheck, X } from "lucide-react";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";

interface BrowserDestination {
  url: string;
}

const LOCAL_ADDRESS_RE = /^(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d+)?(?:\/|$)/i;
const WEB_ADDRESS_RE = /^(?:[\w-]+\.)+[a-z\d-]{2,}(?::\d+)?(?:\/|$)/i;
const SCHEME_RE = /^([a-z][a-z\d+.-]*):/i;

/**
 * Turn address input into one safe HTTP(S) destination.
 *
 * Plain words are deliberately not sent to a hardcoded search provider. The
 * frame also refuses the app's own origin: external sites may run scripts in
 * their isolated origin, but no typed destination may become same-origin with
 * the Personal Jarvis shell that owns the frame.
 */
export function browserDestination(
  raw: string,
  appOrigin = typeof window === "undefined" ? "" : window.location.origin,
): BrowserDestination | null {
  const input = raw.trim();
  if (!input) return null;

  let candidate: string;
  if (LOCAL_ADDRESS_RE.test(input)) {
    candidate = `http://${input}`;
  } else {
    const scheme = input.match(SCHEME_RE)?.[1]?.toLowerCase();
    if (scheme && scheme !== "http" && scheme !== "https") return null;
    if (scheme === "http" || scheme === "https") candidate = input;
    else if (WEB_ADDRESS_RE.test(input)) candidate = `https://${input}`;
    else return null;
  }

  try {
    const destination = new URL(candidate);
    if (destination.protocol !== "http:" && destination.protocol !== "https:") return null;
    if (appOrigin && destination.origin === new URL(appOrigin).origin) return null;
    return { url: destination.toString() };
  } catch {
    return null;
  }
}

interface AgenticBrowserProps {
  onClose: () => void;
}

/** An isolated web-engine surface inside the Agentic IDE, without a proxy. */
export function AgenticBrowser({ onClose }: AgenticBrowserProps) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [draft, setDraft] = useState("");
  const [current, setCurrent] = useState<BrowserDestination | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const navigate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const destination = browserDestination(draft);
    if (!destination) {
      setError(t("agentic_grid.browser.invalid_address"));
      inputRef.current?.focus();
      return;
    }
    setCurrent(destination);
    // This field OPENS an address; it never pretends to be trusted browser
    // chrome. Links and redirects inside a cross-origin frame cannot safely be
    // reflected here, so clear it once the destination has been committed.
    setDraft("");
    setError(null);
    setLoading(true);
  };

  const goHome = () => {
    setCurrent(null);
    setDraft("");
    setError(null);
    setLoading(false);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  return (
    <section
      id="agentic-browser-panel"
      data-testid="agentic-browser"
      aria-label={t("agentic_grid.browser.title")}
      aria-busy={loading}
      className="flex min-h-0 flex-1 flex-col bg-background"
    >
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border bg-card/45 px-2 py-1.5">
        <BrowserButton label={t("agentic_grid.browser.home")} onClick={goHome}>
          <Home className="h-4 w-4" />
        </BrowserButton>

        <form onSubmit={navigate} className="relative min-w-0 flex-1">
          <Globe2 className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            ref={inputRef}
            autoFocus
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              setError(null);
            }}
            aria-label={t("agentic_grid.browser.address_label")}
            aria-invalid={Boolean(error)}
            placeholder={
              current
                ? t("agentic_grid.browser.open_another")
                : t("agentic_grid.browser.address_placeholder")
            }
            spellCheck={false}
            className={cn(
              "h-8 w-full rounded-control border bg-background/85 pl-8 pr-14 font-mono text-xs outline-none transition-colors",
              "border-border/80 focus:border-primary/60 focus:ring-2 focus:ring-primary/15",
              error && "border-destructive/70 focus:border-destructive focus:ring-destructive/10",
            )}
          />
          <button
            type="submit"
            className="absolute right-1 top-1/2 -translate-y-1/2 rounded-control px-2 py-1 text-[11px] font-semibold text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          >
            {t("agentic_grid.browser.go")}
          </button>
        </form>

        <BrowserButton
          label={t("agentic_grid.browser.open_external")}
          disabled={!current}
          onClick={() => current && void openExternalUrl(current.url)}
        >
          <ExternalLink className="h-4 w-4" />
        </BrowserButton>
        <BrowserButton
          testId="agentic-browser-close"
          label={t("agentic_grid.browser.close")}
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </BrowserButton>
      </div>

      {error && (
        <div
          role="alert"
          className="shrink-0 border-b border-destructive/25 bg-destructive/8 px-3 py-1.5 text-xs text-destructive"
        >
          {error}
        </div>
      )}

      {current ? (
        <div className="relative min-h-0 flex-1 bg-white">
          {loading && (
            <div
              role="status"
              aria-live="polite"
              className="pointer-events-none absolute left-1/2 top-3 z-10 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-background/90 px-3 py-1.5 text-xs text-muted-foreground shadow-lg backdrop-blur"
            >
              <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
              {t("agentic_grid.browser.loading")}
            </div>
          )}
          {/* No downloads, popups, modals, top navigation, clipboard, camera,
              microphone, or fullscreen. `allow-same-origin` only restores the
              destination's own cookies/storage; browserDestination blocks the
              application origin, so untrusted content remains cross-origin. */}
          <iframe
            key={current.url}
            src={current.url}
            title={t("agentic_grid.browser.frame_title")}
            data-testid="agentic-browser-frame"
            onLoad={() => setLoading(false)}
            referrerPolicy="no-referrer"
            sandbox="allow-forms allow-same-origin allow-scripts"
            className="h-full w-full border-0 bg-white"
          />
          <div className="pointer-events-none absolute bottom-3 left-1/2 flex max-w-[calc(100%-24px)] -translate-x-1/2 items-center gap-1.5 rounded-full border border-border/70 bg-background/88 px-3 py-1.5 text-[11px] text-muted-foreground shadow-lg backdrop-blur">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span className="truncate">{t("agentic_grid.browser.embed_hint")}</span>
          </div>
        </div>
      ) : (
        <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_50%_42%,hsl(var(--primary)/0.12),transparent_42%)] p-8">
          <div className="pointer-events-none absolute inset-0 opacity-[0.035] [background-image:linear-gradient(hsl(var(--foreground))_1px,transparent_1px),linear-gradient(90deg,hsl(var(--foreground))_1px,transparent_1px)] [background-size:36px_36px]" />
          <div className="relative max-w-lg text-center">
            <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-primary/25 bg-primary/10 text-primary shadow-[0_0_48px_hsl(var(--primary)/0.12)]">
              <Globe2 className="h-8 w-8" strokeWidth={1.5} />
            </div>
            <h2 className="font-display text-xl font-semibold tracking-tight">
              {t("agentic_grid.browser.start_title")}
            </h2>
            <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted-foreground">
              {t("agentic_grid.browser.start_body")}
            </p>
            <button
              type="button"
              onClick={() => inputRef.current?.focus()}
              className="mt-5 inline-flex items-center gap-2 rounded-control border border-primary/30 bg-primary/10 px-3 py-2 text-xs font-semibold text-primary transition-colors hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
            >
              <Globe2 className="h-3.5 w-3.5" />
              {t("agentic_grid.browser.start_action")}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function BrowserButton({
  children,
  disabled = false,
  label,
  onClick,
  testId,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  label: string;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      data-testid={testId}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="flex h-7 w-7 shrink-0 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 disabled:cursor-not-allowed disabled:opacity-35"
    >
      {children}
    </button>
  );
}
