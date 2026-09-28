/** Step 4 — the computer is connected; what to do with it next. */
import { ArrowRight, Bot, CheckCircle2, SquareTerminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { statusLabel } from "../ComputerRow";
import { StatusLight, statusTone } from "../parts";
import { StepActions } from "./StepActions";

export function DoneStep({
  computer,
  onOpen,
}: {
  computer: Computer;
  onOpen: (tab: "overview" | "agents") => void;
}) {
  const t = useT();
  const provisioning = computer.health.status === "provisioning";
  return (
    <div className="flex h-full flex-col" data-testid="wz-done">
      <div className="flex-1">
        <div className="flex flex-col items-center py-4 text-center">
          <CheckCircle2 className="h-10 w-10 text-success" aria-hidden />
          <h3 className="mt-3 text-lg font-semibold text-foreground-strong">
            {(provisioning ? t("computers.wz_done_provisioning") : t("computers.wz_done_title")).replace(
              "{computer}",
              computer.name,
            )}
          </h3>
          <div className="mt-3 flex items-center gap-3 rounded-lg border border-border px-4 py-2.5">
            <ProviderLogo providerId={computer.provider} label={computer.name} size="sm" />
            <span className="font-mono text-xs text-muted-foreground">
              {computer.username}@{computer.host === "0.0.0.0" ? "…" : computer.host}
            </span>
            <span className="inline-flex items-center gap-1.5 text-sm text-foreground-secondary">
              <StatusLight tone={statusTone(computer.health.status)} />
              {statusLabel(computer, t)}
            </span>
          </div>
        </div>
        <h4 className="mb-2 mt-4 text-xs font-medium uppercase tracking-wide text-foreground-faint">
          {t("computers.wz_next")}
        </h4>
        <ul className="divide-y divide-border rounded-lg border border-border">
          <li>
            <button
              type="button"
              onClick={() => onOpen("agents")}
              className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-secondary/50"
            >
              <Bot className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-medium text-foreground-strong">{t("computers.wz_next_agents")}</span>
                <span className="block text-xs text-muted-foreground">{t("computers.wz_next_agents_body")}</span>
              </span>
              <ArrowRight className="h-4 w-4 text-foreground-faint" aria-hidden />
            </button>
          </li>
          <li className="flex items-center gap-3 px-4 py-3">
            <SquareTerminal className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-foreground-strong">{t("computers.wz_next_ide")}</span>
              <span className="block text-xs text-muted-foreground">
                {t("computers.wz_next_ide_body").replace("{computer}", computer.name)}
              </span>
            </span>
          </li>
        </ul>
      </div>
      <StepActions>
        <Button type="button" onClick={() => onOpen("overview")} data-testid="wz-open">
          {t("computers.wz_open").replace("{computer}", computer.name)}
        </Button>
      </StepActions>
    </div>
  );
}
