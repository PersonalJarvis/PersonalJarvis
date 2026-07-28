/**
 * Compact Normal | Ultra segmented toggle at the top of the Wiki section
 * (decision D-5: either-or, exactly one mode owns capture and answering).
 *
 * Mode source of truth: `GET /api/ultrawiki/status` → `enabled` (owned by the
 * parent's query; this component only renders the snapshot it is given and
 * reports back via `onModeChanged` so the parent refetches).
 *
 * Switching to Ultra:
 *   - never configured (no embedding provider chosen yet) → open the
 *     one-time activation wizard instead of calling the route blind;
 *   - already configured → `POST /api/ultrawiki/activate` with the stored
 *     slot choices (idempotent re-persist on the backend).
 * Switching to Normal: a confirm dialog whose copy states that nothing is
 * deleted (decision D-9 — both directions are safe and reversible), then
 * `POST /api/ultrawiki/deactivate`.
 */
import { Suspense, lazy, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  activateUltraWiki,
  deactivateUltraWiki,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

// Lazy: the wizard (and its provider cards) only loads when a never-configured
// install actually opens it — same pattern as WikiGraph in WikiView.
const ActivationWizard = lazy(() =>
  import("@/components/ultrawiki/ActivationWizard").then((mod) => ({
    default: mod.ActivationWizard,
  })),
);

export function UltraModeSwitch({
  status,
  onModeChanged,
}: {
  status: UltraWikiStatus | null;
  onModeChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [pending, setPending] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const enabled = status?.enabled === true;
  // "Configured before" = an embedding provider was ever chosen; only then can
  // activation succeed without the wizard's one-time slot choices. The backend
  // answers this from the stored config (`configured`), which survives a
  // restart and is already true while the service is still starting. The slot
  // fallback only covers a backend older than that field — deriving the answer
  // from the slot report alone is what reopened the wizard on every restart
  // and offered to re-embed a corpus that was already embedded.
  const configuredProvider = status?.slots?.embedding?.provider ?? "";
  const configured =
    status?.configured === undefined
      ? configuredProvider !== ""
      : status.configured === true;
  // `null` = the backend has not answered yet (the app is still booting after
  // the in-app Restart). That is NOT "never configured", so the click asks
  // again instead of walking a returning user through a one-time wizard.
  const statusKnown = status !== null;

  async function switchToUltra() {
    if (enabled || pending) return;
    if (!statusKnown) {
      pushToast("error", t("ultrawiki.mode.status_unknown"));
      onModeChanged();
      return;
    }
    // The provider name has to come along for the re-activation POST, so a
    // backend that claims `configured` without naming one still gets the
    // wizard rather than a 400.
    if (!configured || !configuredProvider) {
      setWizardOpen(true);
      return;
    }
    setPending(true);
    try {
      // Re-activate with the STORED choices — no model, no storage backend, so
      // the backend keeps every value the user picked the first time and the
      // corpus is never re-embedded for switching modes back and forth.
      await activateUltraWiki({ embedding_provider: configuredProvider });
      pushToast("success", t("ultrawiki.mode.activated"));
      onModeChanged();
    } catch (e) {
      pushToast(
        "error",
        t("ultrawiki.mode.activate_failed").replace("{0}", (e as Error).message),
      );
    } finally {
      setPending(false);
    }
  }

  async function switchToNormal() {
    setConfirmOpen(false);
    setPending(true);
    try {
      await deactivateUltraWiki();
      pushToast("success", t("ultrawiki.mode.deactivated"));
      onModeChanged();
    } catch (e) {
      pushToast(
        "error",
        t("ultrawiki.mode.deactivate_failed").replace(
          "{0}",
          (e as Error).message,
        ),
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div
        className="inline-flex items-center gap-1 rounded-lg border border-border bg-card/60 p-1"
        role="group"
        aria-label={t("ultrawiki.mode.aria")}
        data-testid="wiki-mode-toggle"
        data-mode={enabled ? "ultra" : "normal"}
      >
        <ModeButton
          active={!enabled}
          disabled={pending}
          label={t("ultrawiki.mode.normal")}
          testId="wiki-mode-normal"
          onClick={() => {
            if (enabled && !pending) setConfirmOpen(true);
          }}
        />
        <ModeButton
          active={enabled}
          disabled={pending}
          label={t("ultrawiki.mode.ultra")}
          icon={
            pending ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="h-3 w-3" aria-hidden />
            )
          }
          testId="wiki-mode-ultra"
          onClick={() => void switchToUltra()}
        />
      </div>

      {confirmOpen && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ultrawiki-deactivate-title"
          data-testid="ultrawiki-deactivate-confirm"
          onClick={(e) => {
            if (e.target === e.currentTarget) setConfirmOpen(false);
          }}
        >
          <div
            className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h2
              id="ultrawiki-deactivate-title"
              className="text-base font-semibold text-foreground"
            >
              {t("ultrawiki.mode.deactivate_title")}
            </h2>
            {/* D-9: switching back never deletes anything — say so plainly. */}
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {t("ultrawiki.mode.deactivate_body")}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setConfirmOpen(false)}
              >
                {t("ultrawiki.mode.cancel")}
              </Button>
              <Button
                size="sm"
                onClick={() => void switchToNormal()}
                data-testid="ultrawiki-deactivate-confirm-button"
              >
                {t("ultrawiki.mode.deactivate_confirm")}
              </Button>
            </div>
          </div>
        </div>
      )}

      {wizardOpen && (
        <Suspense fallback={<WizardLoadingOverlay />}>
          <ActivationWizard
            onClose={() => setWizardOpen(false)}
            onActivated={() => {
              setWizardOpen(false);
              onModeChanged();
            }}
          />
        </Suspense>
      )}
    </>
  );
}

function ModeButton({
  active,
  disabled,
  label,
  icon,
  testId,
  onClick,
}: {
  active: boolean;
  disabled: boolean;
  label: string;
  icon?: React.ReactNode;
  testId: string;
  onClick: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      data-testid={testId}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-xs transition-colors",
        active
          ? "bg-primary/15 font-medium text-foreground ring-1 ring-primary/50"
          : "text-muted-foreground hover:text-foreground",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function WizardLoadingOverlay(): JSX.Element {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  );
}
