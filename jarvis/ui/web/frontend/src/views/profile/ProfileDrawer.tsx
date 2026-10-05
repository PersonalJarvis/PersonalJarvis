/**
 * The side panel the profile page opens for everything that does not fit on
 * its one screen: the detail groups, the portrait, memory and privacy.
 *
 * It lives inside the Settings hub's content column, not in a portal: the
 * hub is itself a dialog, and a panel inside its content keeps focus and
 * outside clicks with the hub. `aria-modal` on the panel tells the hub to
 * leave Escape to us, so Escape closes the panel and not the whole hub.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

export function ProfileDrawer({
  title,
  description,
  onClose,
  children,
  testId,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  testId?: string;
}) {
  const t = useT();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  // Read through a ref so a new callback identity never re-runs the effect
  // (which would pull focus back to the close button mid-edit).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      // An open inline editor owns Escape: it cancels the edit, not the panel.
      const target = e.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable || target.closest("input, textarea, select"))
      ) {
        return;
      }
      onCloseRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="absolute inset-0 z-20">
      <button
        type="button"
        tabIndex={-1}
        aria-label={t("profile_view.drawer_close")}
        onClick={onClose}
        className="absolute inset-0 h-full w-full cursor-default bg-scrim/50 animate-in fade-in-0 duration-150"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid={testId}
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-border bg-background shadow-float animate-in slide-in-from-right-8 fade-in-0 duration-200"
      >
        <header className="flex items-start gap-3 border-b border-border px-6 pb-4 pt-6">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-foreground-strong">{title}</h2>
            {description && <p className="mt-0.5 text-base text-muted-foreground">{description}</p>}
          </div>
          <Button
            ref={closeRef}
            type="button"
            variant="ghost"
            size="icon"
            onClick={onClose}
            aria-label={t("profile_view.drawer_close")}
            title={t("profile_view.drawer_close")}
            className="text-muted-foreground"
          >
            <X />
          </Button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-8 pt-5 scrollbar-jarvis">{children}</div>
      </aside>
    </div>
  );
}
