import { lazy, Suspense, useState } from "react";
import { Globe } from "lucide-react";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { CAPTION_ICON_BUTTON, CAPTION_ICON_CLASS, CAPTION_ICON_STROKE } from "@/components/layout/captionSwitch";

const BrowserProfilesDialog = lazy(() => import("./BrowserProfilesDialog"));

/**
 * No profile request or browser launch until the user opens the manager.
 *
 * `iconOnly`: the round globe button that sits beside the window caption's
 * switch; the name stays on as its accessible label and hover title.
 */
export function BrowserProfilesButton({ agentId, className, connectChrome = false, iconOnly = false }: {
  agentId?: string; className?: string; connectChrome?: boolean; iconOnly?: boolean;
}) {
  const t = useT();
  useLocaleChunk("society");
  const [open, setOpen] = useState(false);
  const label = t(connectChrome ? "society.browser_profiles.connect_supported_chrome" : "society.browser_profiles.title");
  return <>
    {iconOnly ? <button type="button" onClick={() => setOpen(true)} aria-label={label} title={label}
      data-testid="browser-profiles-button" className={cn(CAPTION_ICON_BUTTON, className)}>
      <Globe aria-hidden className={CAPTION_ICON_CLASS} strokeWidth={CAPTION_ICON_STROKE} />
    </button> : <button type="button" onClick={() => setOpen(true)}
      className={cn("inline-flex items-center justify-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}>
      <Globe size={13} aria-hidden="true" />
      {label}
    </button>}
    {open && <Suspense fallback={<span role="status" className="text-xs text-muted-foreground">{t("society.browser_profiles.loading")}</span>}>
      <BrowserProfilesDialog agentId={agentId} connectChrome={connectChrome} onClose={() => setOpen(false)} />
    </Suspense>}
  </>;
}
