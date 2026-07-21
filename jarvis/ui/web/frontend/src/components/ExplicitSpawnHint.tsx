/**
 * One-line notice that background agents start only on an EXPLICIT ask
 * (maintainer mandate 2026-07-21, commit 285f986b). Shown in the Agents and
 * Outputs views so an empty board / output list reads as "you haven't asked
 * for one yet", never as "broken". The brand stays dynamic via the `{name}`
 * i18n token (never a hardcoded product name, see agentBrand.ts).
 */
import { Info } from "lucide-react";
import { useT } from "@/i18n";

export function ExplicitSpawnHint({ className = "" }: { className?: string }) {
  const t = useT();
  return (
    <div
      className={`flex items-start gap-2 px-4 py-2 text-[11px] leading-snug text-muted-foreground ${className}`}
    >
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{t("subagents_view.explicit_spawn_hint")}</span>
    </div>
  );
}
