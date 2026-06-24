/**
 * Three-step setup walkthrough for Obsidian + the Jarvis vault.
 *
 * Opens when the user clicks the orange (`register`) or yellow (`install`)
 * status pill rendered by ``ObsidianStatus``. The dialog mirrors the
 * three real-world steps the user has to perform:
 *
 *   1. Install Obsidian (skipped when ``installed=true``).
 *   2. Register the Jarvis vault via ``POST /api/setup/obsidian/register``.
 *   3. Live-test the ``obsidian://open?vault=…`` URL scheme.
 *
 * The component never talks to the global event store and never wires
 * itself into the chat/voice path. It is a self-contained dialog whose
 * only side effects are (a) HTTP via ``fetchImpl`` and (b) a single
 * ``window.location.href`` assignment in step 3.
 *
 * Sub-Agent 6 will wire `onComplete` into the first-run flag; we expose
 * the slot but do not act on it here.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ExternalLink, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { ObsidianStatus } from "@/types/setup";

export interface ObsidianSetupDialogProps {
  /** Whether the dialog is visible. */
  open: boolean;
  /** Called when the user dismisses the dialog (X, Escape, click-outside). */
  onClose: () => void;
  /** Seed status used to decide the initial active step. */
  initialStatus: ObsidianStatus;
  /**
   * Optional re-fetch hook. Wired by the parent so the dialog can advance
   * past step 1 once Obsidian is installed without owning the polling state.
   */
  onStatusRefresh?: () => Promise<ObsidianStatus | null>;
  /** Called once when the user confirms the live-test succeeded. */
  onComplete?: () => void;
  /** ``fetch`` override for tests. Defaults to ``window.fetch``. */
  fetchImpl?: typeof fetch;
}

type StepId = 1 | 2 | 3;
type StepState = "done" | "active" | "future";

const REGISTER_URL = "/api/setup/obsidian/register";
const OBSIDIAN_DOWNLOAD_URL = "https://obsidian.md/download";
const TROUBLESHOOT_URL = "/docs/obsidian-setup.md";

interface RegisterResponse {
  status: "added" | "already_registered" | "config_missing" | "rolled_back";
  vault_uuid?: string | null;
  backup_path?: string | null;
  error?: string | null;
}

/**
 * Extract the final segment of the vault path so the
 * ``obsidian://open?vault=`` URL scheme can target it by name.
 *
 * Handles both POSIX and Windows separators. Decodes percent-encoding
 * once because the path comes through ``str(Path)`` on the backend and
 * is otherwise opaque.
 */
function deriveVaultName(vaultPath: string): string {
  if (!vaultPath) return "";
  const trimmed = vaultPath.replace(/[\\/]+$/, "");
  const segments = trimmed.split(/[\\/]/);
  const last = segments[segments.length - 1] ?? "";
  try {
    return decodeURIComponent(last);
  } catch {
    return last;
  }
}

/** Decide which step should be the initial active step. */
function decideInitialStep(status: ObsidianStatus): StepId {
  if (!status.installed) return 1;
  if (!status.vault_registered) return 2;
  return 3;
}

interface HeaderProps {
  current: StepId;
  reached: Set<StepId>;
}

function StepHeader({ current, reached }: HeaderProps): JSX.Element {
  const t = useT();
  const stepLabels: Record<StepId, string> = {
    1: t("obsidian_setup_dialog.step_install"),
    2: t("obsidian_setup_dialog.step_connect"),
    3: t("obsidian_setup_dialog.step_live_test"),
  };

  function stateOf(step: StepId): StepState {
    if (step === current) return "active";
    if (reached.has(step) && step < current) return "done";
    return "future";
  }

  return (
    <ol
      className="flex items-center justify-between gap-2 px-1 pb-4"
      data-testid="obsidian-setup-stepper"
    >
      {([1, 2, 3] as StepId[]).map((step, idx) => {
        const state = stateOf(step);
        return (
          <li
            key={step}
            className="flex flex-1 items-center gap-2"
            data-testid={`obsidian-setup-step-marker-${step}`}
            data-state={state}
          >
            <span
              className={cn(
                "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                state === "done" &&
                  "border-[#5bd4a4]/60 bg-[#5bd4a4]/15 text-[#5bd4a4]",
                state === "active" &&
                  "border-primary bg-primary/20 text-primary ring-2 ring-primary/30",
                state === "future" &&
                  "border-border bg-secondary/30 text-muted-foreground",
              )}
            >
              {state === "done" ? <Check className="h-3.5 w-3.5" /> : step}
            </span>
            <span
              className={cn(
                "text-xs font-medium",
                state === "active" ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {stepLabels[step]}
            </span>
            {idx < 2 && (
              <span
                aria-hidden
                className={cn(
                  "ml-1 h-px flex-1 bg-border",
                  state === "done" && "bg-[#5bd4a4]/40",
                )}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function ObsidianSetupDialog({
  open,
  onClose,
  initialStatus,
  onStatusRefresh,
  onComplete,
  fetchImpl,
}: ObsidianSetupDialogProps): JSX.Element | null {
  const t = useT();
  const [currentStep, setCurrentStep] = useState<StepId>(() =>
    decideInitialStep(initialStatus),
  );
  const [reached] = useState<Set<StepId>>(() => {
    const set = new Set<StepId>();
    const start = decideInitialStep(initialStatus);
    for (let s = 1; s <= start; s++) set.add(s as StepId);
    return set;
  });
  const [vaultPath, setVaultPath] = useState<string>(initialStatus.vault_path);

  // Step 1 — install
  const [refreshing, setRefreshing] = useState(false);
  const [installRetryHint, setInstallRetryHint] = useState<string | null>(null);

  // Step 2 — register
  const [registering, setRegistering] = useState(false);
  const [registerHint, setRegisterHint] = useState<string | null>(null);
  const [registerError, setRegisterError] = useState<string | null>(null);

  // Stable fetch reference.
  const fetchRef = useRef<typeof fetch>(fetchImpl ?? window.fetch.bind(window));
  useEffect(() => {
    fetchRef.current = fetchImpl ?? window.fetch.bind(window);
  }, [fetchImpl]);

  // Escape closes the dialog.
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const advance = useCallback((to: StepId) => {
    setCurrentStep(to);
  }, []);

  const handleInstallContinue = useCallback(async () => {
    setInstallRetryHint(null);
    if (!onStatusRefresh) {
      // No re-fetch available — trust the user and advance optimistically.
      advance(2);
      return;
    }
    setRefreshing(true);
    try {
      const next = await onStatusRefresh();
      if (next && next.installed) {
        setVaultPath(next.vault_path || vaultPath);
        if (next.vault_registered) {
          advance(3);
        } else {
          advance(2);
        }
      } else {
        setInstallRetryHint(t("obsidian_setup_dialog.not_detected_hint"));
      }
    } catch {
      setInstallRetryHint(t("obsidian_setup_dialog.status_check_failed"));
    } finally {
      setRefreshing(false);
    }
  }, [advance, onStatusRefresh, vaultPath, t]);

  const handleRegister = useCallback(async () => {
    setRegisterHint(null);
    setRegisterError(null);
    setRegistering(true);
    try {
      const res = await fetchRef.current(REGISTER_URL, {
        method: "POST",
        headers: { "content-type": "application/json" },
      });
      if (res.status === 200) {
        // Either "added" or "already_registered" — both mean go on.
        let body: RegisterResponse | null = null;
        try {
          body = (await res.json()) as RegisterResponse;
        } catch {
          body = null;
        }
        if (
          body === null ||
          body.status === "added" ||
          body.status === "already_registered"
        ) {
          advance(3);
        } else {
          // Defensive: 200 with an unexpected body — surface as error.
          setRegisterError(
            `${t("obsidian_setup_dialog.register_failed")}: ${
              body.error ?? t("obsidian_setup_dialog.unknown_status")
            }`,
          );
        }
        return;
      }
      if (res.status === 409) {
        setRegisterHint(t("obsidian_setup_dialog.restart_obsidian_hint"));
        return;
      }
      // 500 or anything else: try to extract an error string from the
      // FastAPI ``detail`` envelope, otherwise show the status text.
      let errMsg = `HTTP ${res.status}`;
      try {
        const payload = (await res.json()) as {
          detail?: { error?: string } | string;
          error?: string;
        };
        if (typeof payload?.detail === "object" && payload.detail?.error) {
          errMsg = payload.detail.error;
        } else if (typeof payload?.detail === "string") {
          errMsg = payload.detail;
        } else if (payload?.error) {
          errMsg = payload.error;
        }
      } catch {
        // Body wasn't JSON — keep HTTP status fallback.
      }
      setRegisterError(`${t("obsidian_setup_dialog.register_failed")}: ${errMsg}`);
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : String(exc);
      setRegisterError(`${t("obsidian_setup_dialog.register_failed")}: ${msg}`);
    } finally {
      setRegistering(false);
    }
  }, [advance, t]);

  const vaultName = useMemo(() => deriveVaultName(vaultPath), [vaultPath]);

  const handleLaunchObsidian = useCallback(() => {
    const url = `obsidian://open?vault=${encodeURIComponent(vaultName)}`;
    // Use ``location.href`` rather than ``window.open`` — Chrome and
    // pywebview both block popups for custom URI schemes.
    try {
      window.location.href = url;
    } catch {
      // jsdom or sandboxed embed: nothing we can do, the failure message
      // shows up on the "Hat nicht geklappt" branch anyway.
    }
  }, [vaultName]);

  const handleComplete = useCallback(() => {
    onComplete?.();
    onClose();
  }, [onComplete, onClose]);

  const handleTroubleshoot = useCallback(() => {
    window.open(TROUBLESHOOT_URL, "_blank", "noopener,noreferrer");
  }, []);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="obsidian-setup-title"
      data-testid="obsidian-setup-dialog"
      onClick={(e) => {
        // Click-outside closes — only when clicking the backdrop itself.
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="relative w-full max-w-xl rounded-2xl border border-border bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
          aria-label={t("obsidian_setup_dialog.close_dialog")}
          data-testid="obsidian-setup-close"
        >
          <X className="h-4 w-4" />
        </button>

        <h2
          id="obsidian-setup-title"
          className="mb-1 text-base font-semibold text-foreground"
        >
          {t("obsidian_setup_dialog.title")}
        </h2>
        <p className="mb-3 text-xs text-muted-foreground">
          {t("obsidian_setup_dialog.subtitle")}
        </p>

        <StepHeader current={currentStep} reached={reached} />

        {currentStep === 1 && (
          <section
            data-testid="obsidian-setup-step-1"
            className="space-y-3"
            aria-labelledby="obsidian-setup-step-1-title"
          >
            <h3
              id="obsidian-setup-step-1-title"
              className="text-sm font-semibold text-foreground"
            >
              {t("obsidian_setup_dialog.install_heading")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {t("obsidian_setup_dialog.install_body_1")}{" "}
              {t("obsidian_setup_dialog.install_body_2")}{" "}
              <strong>Obsidian</strong>.
            </p>
            <a
              href={OBSIDIAN_DOWNLOAD_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
              data-testid="obsidian-setup-download-link"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
              {t("obsidian_setup_dialog.download_link")}
            </a>
            {installRetryHint && (
              <p
                className="rounded-md border border-[#ffb84d]/40 bg-[#ffb84d]/10 px-3 py-2 text-xs text-[#ffb84d]"
                data-testid="obsidian-setup-install-hint"
                role="status"
              >
                {installRetryHint}
              </p>
            )}
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClose}
                data-testid="obsidian-setup-cancel"
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void handleInstallContinue()}
                disabled={refreshing}
                data-testid="obsidian-setup-installed-continue"
              >
                {refreshing ? (
                  <>
                    <Loader2
                      className="mr-1.5 h-3.5 w-3.5 animate-spin"
                      aria-hidden
                    />
                    {t("obsidian_setup_dialog.checking")}
                  </>
                ) : (
                  t("obsidian_setup_dialog.installed_continue")
                )}
              </Button>
            </div>
          </section>
        )}

        {currentStep === 2 && (
          <section
            data-testid="obsidian-setup-step-2"
            className="space-y-3"
            aria-labelledby="obsidian-setup-step-2-title"
          >
            <h3
              id="obsidian-setup-step-2-title"
              className="text-sm font-semibold text-foreground"
            >
              {t("obsidian_setup_dialog.connect_heading")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {t("obsidian_setup_dialog.connect_body_1")}{" "}
              {t("obsidian_setup_dialog.connect_body_2")}{" "}
              <code className="rounded bg-background px-1 py-0.5 font-mono text-[12px]">
                obsidian://
              </code>{" "}
              {t("obsidian_setup_dialog.connect_body_3")}
            </p>
            <p
              className="rounded-md border border-border bg-background/40 px-3 py-2 text-xs text-muted-foreground"
              data-testid="obsidian-setup-vault-path"
            >
              <span className="block text-[10px] uppercase tracking-wide text-muted-foreground/70">
                {t("obsidian_setup_dialog.vault_path_label")}
              </span>
              <span className="font-mono text-foreground">{vaultPath}</span>
            </p>
            {registerHint && (
              <p
                className="rounded-md border border-[#ffb84d]/40 bg-[#ffb84d]/10 px-3 py-2 text-xs text-[#ffb84d]"
                data-testid="obsidian-setup-register-hint"
                role="status"
              >
                {registerHint}
              </p>
            )}
            {registerError && (
              <div
                className="space-y-1 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
                data-testid="obsidian-setup-register-error"
                role="alert"
              >
                <p>{registerError}</p>
                <a
                  href={TROUBLESHOOT_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs underline hover:no-underline"
                  data-testid="obsidian-setup-help-link"
                >
                  <ExternalLink className="h-3 w-3" aria-hidden />
                  {t("obsidian_setup_dialog.open_help")}
                </a>
              </div>
            )}
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onClose}
                data-testid="obsidian-setup-cancel"
              >
                {t("common.cancel")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={() => void handleRegister()}
                disabled={registering}
                data-testid="obsidian-setup-register"
              >
                {registering ? (
                  <>
                    <Loader2
                      className="mr-1.5 h-3.5 w-3.5 animate-spin"
                      aria-hidden
                    />
                    {t("obsidian_setup_dialog.registering")}
                  </>
                ) : (
                  t("obsidian_setup_dialog.register_now")
                )}
              </Button>
            </div>
          </section>
        )}

        {currentStep === 3 && (
          <section
            data-testid="obsidian-setup-step-3"
            className="space-y-3"
            aria-labelledby="obsidian-setup-step-3-title"
          >
            <h3
              id="obsidian-setup-step-3-title"
              className="text-sm font-semibold text-foreground"
            >
              {t("obsidian_setup_dialog.step_live_test")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {t("obsidian_setup_dialog.live_test_body")}
            </p>
            <div className="flex items-center justify-start gap-2 pt-1">
              <Button
                type="button"
                size="sm"
                onClick={handleLaunchObsidian}
                data-testid="obsidian-setup-launch"
              >
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {t("obsidian_setup_dialog.open_in_obsidian")}
              </Button>
            </div>
            <div className="flex flex-col items-stretch gap-2 pt-2 sm:flex-row sm:items-center sm:justify-end">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleTroubleshoot}
                data-testid="obsidian-setup-troubleshoot"
              >
                {t("obsidian_setup_dialog.did_not_work")}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={handleComplete}
                data-testid="obsidian-setup-finish"
              >
                {t("obsidian_setup_dialog.it_worked")}
              </Button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

export default ObsidianSetupDialog;
