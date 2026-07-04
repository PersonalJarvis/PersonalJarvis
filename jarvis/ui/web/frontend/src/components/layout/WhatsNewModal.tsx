/**
 * "What's new" preview modal — opened from the TopBar update button BEFORE the
 * update is applied. It shows the full release notes of the newer published
 * version (grouped Added / Fixed / Changed …) so the user sees exactly what the
 * update brings, then confirms with "Update now" (which runs the same
 * apply + restart flow the button used to run directly) or dismisses with
 * "Later".
 *
 * The notes come from GET /api/update/status (`status.notes`), which is the
 * GitHub Release body — Keep-a-Changelog markdown. It is rendered with the same
 * `react-markdown` the wiki uses, so no new dependency is pulled in.
 *
 * The modal is a centered overlay with rounded corners, matching the app's
 * FrontierSwitchModal chrome. It closes on the overlay click, on Escape, and on
 * "Later"; it never closes on its own while an update is in flight (`busy`).
 */
import { useEffect } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import { Download, ExternalLink, Sparkles, X } from "lucide-react";

import type { UpdateStatus } from "@/hooks/useUpdate";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface WhatsNewModalProps {
  status: UpdateStatus;
  busy: boolean;
  forceArmed: boolean;
  onApply: () => void;
  onClose: () => void;
}

/** Extract the plain text of a markdown heading's children (for colour-coding). */
function headingText(children: React.ReactNode): string {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(headingText).join("");
  return "";
}

/**
 * A left-border accent per changelog category, so "Added / Fixed / Changed …"
 * read at a glance. Word-matched (not locale-specific) against the English
 * section names the release body always uses.
 */
function categoryAccent(text: string): string {
  const t = text.toLowerCase();
  if (t.includes("add") || t.includes("new") || t.includes("highlight"))
    return "border-emerald-500/70";
  if (t.includes("fix")) return "border-sky-500/70";
  if (t.includes("chang") || t.includes("improv")) return "border-amber-500/70";
  if (t.includes("remov") || t.includes("deprecat")) return "border-rose-500/70";
  if (t.includes("security")) return "border-violet-500/70";
  return "border-primary/60";
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export function WhatsNewModal({
  status,
  busy,
  forceArmed,
  onApply,
  onClose,
}: WhatsNewModalProps) {
  const t = useT();
  const released = formatDate(status.published_at);

  // Escape closes the modal (but never mid-update).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const applyLabel = busy
    ? t("topbar.update_installing")
    : forceArmed
      ? t("topbar.update_force")
      : t("topbar.update_now");

  // Portal to <body>: the TopBar (this modal's React parent) carries a
  // `backdrop-blur`, and a backdrop-filter makes its element the containing
  // block for `position: fixed` descendants — which would pin the overlay to
  // the 40px-tall bar instead of the viewport. Rendering through a portal
  // escapes that stacking context so `inset-0` means the full screen again.
  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-[70] flex items-center justify-center",
        "bg-background/80 p-4 backdrop-blur-sm",
        "animate-in fade-in duration-200",
      )}
      role="dialog"
      aria-modal="true"
      aria-labelledby="whats-new-title"
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "flex max-h-[82vh] w-full max-w-xl flex-col",
          "rounded-2xl border border-primary/40 bg-card",
          "shadow-[0_0_60px_rgba(255,214,10,0.18)]",
          "animate-in zoom-in-95 fade-in duration-200",
        )}
      >
        {/* Header — fixed */}
        <div className="flex items-start gap-3 border-b border-border px-6 py-5">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-primary/15 text-primary">
            <Sparkles className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2
              id="whats-new-title"
              className="text-base font-semibold text-foreground"
            >
              {t("topbar.update_whats_new")}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {status.latest && (
                <span className="rounded bg-primary/20 px-1.5 py-0.5 font-medium tabular-nums text-primary">
                  v{status.latest}
                </span>
              )}
              {status.current && status.current !== "unknown" && (
                <span className="tabular-nums">
                  v{status.current} → v{status.latest}
                </span>
              )}
              {released && (
                <span>
                  · {t("topbar.update_released")} {released}
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => !busy && onClose()}
            disabled={busy}
            aria-label={t("topbar.update_later")}
            className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary/60 hover:text-foreground disabled:opacity-50"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body — scrolls */}
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {status.notes ? (
            <ReactMarkdown
              components={{
                h1: ({ children }) => (
                  <h3
                    className={cn(
                      "mb-2 mt-5 border-l-2 pl-2 text-[13px] font-semibold uppercase tracking-wider text-foreground first:mt-0",
                      categoryAccent(headingText(children)),
                    )}
                  >
                    {children}
                  </h3>
                ),
                h2: ({ children }) => (
                  <h3
                    className={cn(
                      "mb-2 mt-5 border-l-2 pl-2 text-[13px] font-semibold uppercase tracking-wider text-foreground first:mt-0",
                      categoryAccent(headingText(children)),
                    )}
                  >
                    {children}
                  </h3>
                ),
                h3: ({ children }) => (
                  <h3
                    className={cn(
                      "mb-2 mt-5 border-l-2 pl-2 text-[13px] font-semibold uppercase tracking-wider text-foreground first:mt-0",
                      categoryAccent(headingText(children)),
                    )}
                  >
                    {children}
                  </h3>
                ),
                p: ({ children }) => (
                  <p className="my-2 text-sm leading-relaxed text-muted-foreground">
                    {children}
                  </p>
                ),
                ul: ({ children }) => (
                  <ul className="my-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                    {children}
                  </ul>
                ),
                li: ({ children }) => (
                  <li className="leading-relaxed">{children}</li>
                ),
                strong: ({ children }) => (
                  <strong className="font-semibold text-foreground">
                    {children}
                  </strong>
                ),
                a: ({ href, children }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
                  >
                    {children}
                  </a>
                ),
                code: ({ children }) => (
                  <code className="rounded bg-background px-1 py-0.5 font-mono text-[11px] text-muted-foreground">
                    {children}
                  </code>
                ),
                hr: () => <hr className="my-4 border-border" />,
              }}
            >
              {status.notes}
            </ReactMarkdown>
          ) : (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t("topbar.update_no_notes")}
            </p>
          )}
        </div>

        {/* Footer — fixed */}
        <div className="flex items-center justify-between gap-3 border-t border-border px-6 py-4">
          {status.release_url ? (
            <a
              href={status.release_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              {t("topbar.update_view_online")}
            </a>
          ) : (
            <span />
          )}
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => !busy && onClose()}
              disabled={busy}
              className="rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
            >
              {t("topbar.update_later")}
            </button>
            <button
              type="button"
              onClick={onApply}
              disabled={busy}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-4 py-1.5 text-sm font-semibold transition-colors disabled:cursor-default disabled:opacity-70",
                forceArmed
                  ? "border-amber-500/60 bg-amber-500/10 text-amber-500 hover:bg-amber-500/20"
                  : "border-primary bg-primary text-primary-foreground hover:opacity-90",
              )}
            >
              <Download
                aria-hidden
                className={cn("h-4 w-4", busy && "animate-pulse")}
              />
              {applyLabel}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
