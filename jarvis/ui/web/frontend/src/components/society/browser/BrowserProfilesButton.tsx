import { lazy, Suspense, useState } from "react";
import { Globe } from "lucide-react";
import { useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";

const BrowserProfilesDialog = lazy(() => import("./BrowserProfilesDialog"));

/** No profile request or browser launch until the user opens the manager. */
export function BrowserProfilesButton({ agentId, className }: { agentId?: string; className?: string }) {
  const t = useT();
  useLocaleChunk("society");
  const [open, setOpen] = useState(false);
  return <>
    <button type="button" onClick={() => setOpen(true)}
      className={cn("inline-flex items-center justify-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", className)}>
      <Globe size={13} aria-hidden="true" />
      {t("society.browser_profiles.title")}
    </button>
    {open && <Suspense fallback={<span role="status" className="text-xs text-muted-foreground">{t("society.browser_profiles.loading")}</span>}>
      <BrowserProfilesDialog agentId={agentId} onClose={() => setOpen(false)} />
    </Suspense>}
  </>;
}
