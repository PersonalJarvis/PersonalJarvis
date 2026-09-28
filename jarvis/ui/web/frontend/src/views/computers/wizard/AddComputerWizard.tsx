/**
 * "Add a computer" — a four-step wizard with a visible step indicator:
 *
 *   1  Where    the provider gallery (or a VM on this computer)
 *   2  How      the connection methods that work for that provider
 *   3  Details  address + credential + a real connection test
 *               (or: API token → the account's server list)
 *   4  Done     what to do with the new computer next
 *
 * Every step has exactly one primary action. Back never loses what was typed
 * in an earlier step; closing the dialog does.
 */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useProviderCatalog, useUpsertComputer } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer } from "@/lib/computersApi";
import { ApiImportStep } from "./ApiImportStep";
import { DetailsStep } from "./DetailsStep";
import { DoneStep } from "./DoneStep";
import { LocalVmStep } from "./LocalVmStep";
import { MethodStep, recommendedMethod } from "./MethodStep";
import { ProviderStep } from "./ProviderStep";
import { ErrorNote, errorText, type Method, type Pick } from "./shared";
import { StepActions } from "./StepActions";

type Step = 0 | 1 | 2 | 3;

export function AddComputerWizard({
  onClose,
  onOpen,
}: {
  onClose: () => void;
  /** Close the wizard and show the new computer, on this tab. */
  onOpen: (computer: Computer, tab: "overview" | "agents") => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const upsert = useUpsertComputer();
  const catalog = useProviderCatalog();
  const [step, setStep] = useState<Step>(0);
  const [pick, setPick] = useState<Pick | null>(null);
  const [method, setMethod] = useState<Method>("password");
  const [added, setAdded] = useState<Computer | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const choose = (next: Pick) => {
    setPick(next);
    if (next.kind === "local") {
      setStep(2);
      return;
    }
    setMethod(recommendedMethod(next.provider));
    setStep(1);
  };
  const finish = (computer: Computer) => {
    upsert(computer);
    setAdded(computer);
    setStep(3);
  };

  const steps = [
    t("computers.wz_step_where"),
    t("computers.wz_step_method"),
    t("computers.wz_step_details"),
    t("computers.wz_step_done"),
  ];
  const provider = pick?.kind === "provider" ? pick.provider : null;
  const titles: Record<Step, string> = {
    0: t("computers.wz_title_where"),
    1: t("computers.wz_title_method"),
    2: pick?.kind === "local" ? t("computers.wz_local_title") : t("computers.wz_title_details"),
    3: t("computers.wz_step_done"),
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/50 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("computers.add_title")}
        data-testid="computers-add-dialog"
        className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl bg-popover shadow-float"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b border-border px-6 pb-4 pt-5">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold text-foreground-strong">{titles[step]}</h2>
              {provider && step > 0 && step < 3 && (
                <p className="mt-1 flex items-center gap-2 text-sm text-muted-foreground">
                  <ProviderLogo providerId={provider.id} label={provider.name} size="sm" />
                  {provider.name}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={t("computers.close")}
              className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <ol className="mt-4 flex items-center gap-2" aria-label={t("computers.wz_progress")}>
            {steps.map((label, index) => {
              const skipped = pick?.kind === "local" && index === 1;
              const done = index < step && !skipped;
              const current = index === step;
              return (
                <li key={label} className="flex min-w-0 flex-1 items-center gap-2" aria-current={current ? "step" : undefined}>
                  <span
                    className={cn(
                      "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium tabular-nums",
                      current && "border-accent bg-accent text-accent-foreground",
                      done && "border-accent text-accent",
                      !current && !done && "border-border text-muted-foreground",
                      skipped && "opacity-40",
                    )}
                  >
                    {done ? <Check className="h-3.5 w-3.5" aria-hidden /> : index + 1}
                  </span>
                  <span
                    className={cn(
                      "hidden truncate text-xs sm:block",
                      current ? "font-medium text-foreground-strong" : "text-muted-foreground",
                      skipped && "opacity-40",
                    )}
                  >
                    {label}
                  </span>
                  {index < steps.length - 1 && <span className="h-px min-w-3 flex-1 bg-border" aria-hidden />}
                </li>
              );
            })}
          </ol>
        </header>

        <div className="min-h-[420px] flex-1 overflow-y-auto px-6 py-5 scrollbar-jarvis">
          {step === 0 && (
            <div className="flex h-full flex-col">
              <div className="flex-1">
                {catalog.isError && <ErrorNote message={errorText(catalog.error)} />}
                <ProviderStep
                  providers={catalog.data ?? []}
                  loading={catalog.isLoading}
                  selected={pick}
                  onPick={choose}
                />
              </div>
            </div>
          )}
          {step === 1 && provider && (
            <div className="flex h-full flex-col">
              <div className="flex-1">
                <MethodStep provider={provider} value={method} onChange={setMethod} />
              </div>
              <StepActions onBack={() => setStep(0)}>
                <Button type="button" onClick={() => setStep(2)} data-testid="wz-continue">
                  {t("computers.wz_continue")}
                </Button>
              </StepActions>
            </div>
          )}
          {step === 2 && pick?.kind === "local" && <LocalVmStep onBack={() => setStep(0)} onAdded={finish} />}
          {step === 2 && provider && method === "api" && (
            <ApiImportStep
              provider={provider}
              onBack={() => setStep(1)}
              onAdded={finish}
              onProviderChanged={() => void qc.invalidateQueries({ queryKey: ["computers", "providers"] })}
            />
          )}
          {step === 2 && provider && method !== "api" && (
            <DetailsStep provider={provider} method={method} onBack={() => setStep(1)} onAdded={finish} />
          )}
          {step === 3 && added && <DoneStep computer={added} onOpen={(tab) => onOpen(added, tab)} />}
        </div>
      </div>
    </div>
  );
}
