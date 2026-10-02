import { CheckCircle2, Loader2, ShieldAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { useInlinePermission } from "@/hooks/useInlinePermission";
import { useRestartApp } from "@/hooks/useRestartApp";
import { fill, useT, useUiLanguage } from "@/i18n";
import { onSharedReturnToWindow } from "@/lib/focusRefresh";
import type {
  PermissionNeededPhase,
  PermissionNeededReason,
} from "@/lib/permissionEvents";
import {
  FALLBACK_APP_NAME,
  isOutsideAskEpisode,
  listPermissionNames,
  outsideCopyKeys,
  promptSentence,
} from "@/lib/permissionCopy";
import { RESOLVED_HOLD_MS, type PromptEpisode } from "@/lib/permissionPrompts";
import {
  PermissionApiError,
  fetchPermissionRow,
  openPermissionSettings,
  requestPermission,
} from "@/lib/permissionsApi";
import { isReadyState, type PermissionId } from "@/lib/permissionSnapshot";
import { cn } from "@/lib/utils";
import { usePermissionsStore } from "@/store/permissions";
import { WRAPPING_ACTION_BUTTON, isSettingsReason, promptActions, type PromptAction } from "./promptActions";
import { SeeAllPermissionsLink } from "./SeeAllPermissionsLink";

/**
 * What a surface already knows from a route answer (the wake switch, a shortcut
 * request) before, or without, an episode in the store. The store's own episode
 * wins when there is one.
 */
export interface LocalPermissionState {
  permissions: string[];
  reason: PermissionNeededReason;
  phase?: PermissionNeededPhase;
  target?: string;
  can_prompt?: boolean;
  can_open_settings?: boolean;
  outside_app?: boolean;
}

function toEpisode(feature: string, local: LocalPermissionState): PromptEpisode {
  return {
    key: `local:${feature}:${local.permissions.join("+")}`,
    feature,
    permissions: local.permissions,
    reason: local.reason,
    phase: local.phase ?? "blocked",
    origin: "user",
    target: local.target ?? "",
    can_prompt: local.can_prompt === true,
    can_open_settings: local.can_open_settings === true,
    outside_app: local.outside_app === true,
    detail: "",
    trace_id: "",
    updatedAt: 0,
    dismissed: false,
  };
}

type MessageKey =
  | "permissions.prompt.still_off"
  | "permissions.rate_limited"
  | "permissions.action_failed";

/**
 * The buttons of an inline note: the same reason table as the floating card
 * (`promptActions`), minus "Not now" (an inline note is passive status, it has
 * no card to dismiss) and minus "Reset and ask again" (that stays on the
 * Privacy page, where a person looks for it). As on the card, "Check again"
 * shows only once the person came back from System Settings and the note is
 * still there (a grant removes it by itself).
 *
 * Only the embedded desktop window may show them: a remote browser must never
 * offer host-only actions (System Settings on another computer).
 */
function useNoteActions(episode: PromptEpisode | null, feature: string) {
  const restartApp = useRestartApp();
  const [busy, setBusy] = useState<PromptAction | null>(null);
  const [message, setMessage] = useState<MessageKey | null>(null);
  const [openedSettings, setOpenedSettings] = useState(false);
  const [returned, setReturned] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // Listen for the return only after this note sent the person to System Settings.
  useEffect(() => {
    if (!openedSettings) return undefined;
    return onSharedReturnToWindow(() => setReturned(true));
  }, [openedSettings]);

  const primary = (episode?.permissions[0] ?? "") as PermissionId | "";

  const run = async (action: PromptAction, work: () => Promise<void>) => {
    setBusy(action);
    setMessage(null);
    try {
      await work();
    } catch (exc) {
      if (mounted.current) {
        setMessage(
          exc instanceof PermissionApiError && exc.status === 429
            ? "permissions.rate_limited"
            : "permissions.action_failed",
        );
      }
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const ask = (allowOutside: boolean) => async () => {
    if (!episode || primary === "") return;
    await requestPermission(primary, {
      feature,
      target: episode.target || undefined,
      allow_outside_app: allowOutside,
    });
  };

  const handlers: Partial<Record<PromptAction, () => void>> = {
    continue: () => void run("continue", ask(false)),
    allow_outside: () => void run("allow_outside", ask(true)),
    open_settings: () =>
      void run("open_settings", async () => {
        if (primary !== "") await openPermissionSettings(primary);
        if (mounted.current) setOpenedSettings(true);
      }),
    check_again: () =>
      void run("check_again", async () => {
        if (primary === "") return;
        const row = await fetchPermissionRow(primary);
        if (mounted.current && row && !isReadyState(row.status)) {
          setMessage("permissions.prompt.still_off");
        }
      }),
    restart: () => void restartApp.restart(),
  };

  return { busy, message, handlers, restartApp, returned };
}

export interface InlinePermissionNoteProps {
  /** The `PERMISSION_FEATURES` token this surface explains. */
  feature: string;
  /**
   * i18n key of the sentence shown for {@link holdMs} after the permission was
   * granted ("Microphone allowed - press again"). Omitted: nothing is shown on a grant.
   */
  allowedKey?: string;
  /** Whether a grant may show {@link allowedKey} at all (the surface knows it asked). Default true. */
  showAllowed?: boolean;
  /** How long the confirmation stays. Default {@link RESOLVED_HOLD_MS}. */
  holdMs?: number;
  /** A route answer the surface already holds; the store's episode wins over it. */
  local?: LocalPermissionState | null;
  /** The surface decided its own wording applies; hide this note (no registration either). */
  hidden?: boolean;
  /** Called once an "allowed" confirmation is shown, so a surface can refresh what it displays. */
  onResolved?: () => void;
  /** Adds a close button (a note that floats over other content, such as the dictation popover). */
  onDismiss?: () => void;
  className?: string;
  testId?: string;
  /** Extra content under the sentence (a surface's own hint). */
  children?: ReactNode;
}

/**
 * One explanation of a missing macOS permission, in the place the person is
 * looking at (the wake-word panel, the dictation popover, the Appshots page).
 *
 * It reads the feature's episode from the permission store and renders, by
 * phase: macOS asking right now (a sentence, no buttons), the person has to act
 * (the per-(feature, reason) i18n sentence plus the reason table's buttons), or
 * "allowed" for a few seconds after a grant. While it shows anything it
 * registers as the feature's inline surface, so the floating card does not say
 * the same thing a second time (see `useInlinePermission`).
 *
 * Copy comes only from i18n; the backend's English `detail` is never rendered.
 * Colours are theme tokens (light, dark and terminal-pane safe); the container
 * is a polite live region and takes no focus.
 */
export function InlinePermissionNote({
  feature,
  allowedKey,
  showAllowed = true,
  holdMs = RESOLVED_HOLD_MS,
  local = null,
  hidden = false,
  onResolved,
  onDismiss,
  className,
  testId,
  children,
}: InlinePermissionNoteProps) {
  const t = useT();
  const language = useUiLanguage();
  const appName = usePermissionsStore((state) => state.snapshot?.app_identity.app_name ?? "");
  const launchedAsBundle = usePermissionsStore(
    (state) => state.snapshot?.app_identity.launched_as_bundle === true,
  );
  const { episode: stored, resolved } = useInlinePermission(feature, false);
  const episode = stored ?? (local ? toEpisode(feature, local) : null);

  // The confirmation is time-limited; re-render when it expires.
  const [now, setNow] = useState(() => Date.now());
  const grantedAt = resolved?.granted ? resolved.ts : 0;
  useEffect(() => {
    setNow(Date.now());
    if (grantedAt === 0) return undefined;
    const wait = grantedAt + holdMs - Date.now();
    if (wait <= 0) return undefined;
    const timer = window.setTimeout(() => setNow(Date.now()), wait + 20);
    return () => window.clearTimeout(timer);
  }, [grantedAt, holdMs]);

  const allowed =
    !hidden && showAllowed && Boolean(allowedKey) && grantedAt > 0 && now - grantedAt < holdMs;
  const visible = !hidden && (episode !== null || allowed);
  // Registering (not just reading) is what keeps the floating card away.
  useInlinePermission(feature, visible);

  const onResolvedRef = useRef(onResolved);
  onResolvedRef.current = onResolved;
  useEffect(() => {
    // A grant this surface can still announce; an old one (a note left in the
    // store from earlier) must not make it refetch on every mount.
    if (grantedAt > 0 && Date.now() - grantedAt < holdMs) onResolvedRef.current?.();
  }, [grantedAt, holdMs]);

  const embedded = hasEmbeddedDesktopBridge();
  const { busy, message, handlers, restartApp, returned } = useNoteActions(episode, feature);

  if (!visible) return null;
  const name = appName || FALLBACK_APP_NAME;

  let sentence = "";
  let actions: PromptAction[] = [];
  if (episode) {
    if (episode.phase === "os_dialog") {
      sentence = fill(t("permissions.inline.os_dialog"), {
        permissions: listPermissionNames(t, episode.permissions, language),
      });
    } else {
      sentence = promptSentence({ t, language, episode, appName: name, launchedAsBundle });
      actions = embedded
        ? promptActions(episode, {
            returnedFromSettings: returned,
            // The note is still on screen, so the permission still reads off.
            stillOff: true,
            canReset: false,
          }).filter((action) => action !== "not_now")
        : [];
    }
  } else if (allowedKey) {
    sentence = t(allowedKey);
  }
  const showAsAllowed = sentence !== "" && episode === null;

  const label = (action: PromptAction): string => {
    switch (action) {
      case "allow_outside":
        return fill(t(outsideCopyKeys(launchedAsBundle).action), { app: name });
      case "restart":
        return restartApp.restarting || restartApp.forceArmed
          ? restartApp.buttonLabel
          : t("permissions.prompt.action.restart");
      default:
        return t(`permissions.prompt.action.${action}`);
    }
  };

  const Icon = showAsAllowed ? CheckCircle2 : episode?.phase === "os_dialog" ? Loader2 : ShieldAlert;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid={testId ?? "inline-permission-note"}
      data-feature={feature}
      data-phase={showAsAllowed ? "allowed" : (episode?.phase ?? "")}
      data-reason={episode?.reason ?? ""}
      className={cn("flex items-start gap-2 text-meta text-muted-foreground", className)}
    >
      <Icon
        aria-hidden
        className={cn(
          "mt-0.5 h-3.5 w-3.5 shrink-0",
          showAsAllowed ? "text-success" : "text-muted-foreground",
          episode?.phase === "os_dialog" && !showAsAllowed && "motion-safe:animate-spin",
        )}
      />
      <div className="min-w-0 flex-1">
        <p className="break-words text-foreground" data-testid="inline-permission-sentence">
          {sentence}
        </p>
        {message && (
          <p className="mt-1 break-words" data-testid="inline-permission-message">
            {t(message)}
          </p>
        )}
        {children}
        {actions.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {actions.map((action) => (
              <Button
                key={action}
                type="button"
                size="sm"
                className={WRAPPING_ACTION_BUTTON}
                variant={action === actions[0] ? "default" : "outline"}
                disabled={busy !== null}
                onClick={handlers[action]}
                data-action={action}
              >
                {busy === action && (
                  <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
                )}
                {label(action)}
              </Button>
            ))}
            {/* Only where Settings is the way forward; a note that asks or confirms needs no list. */}
            {episode && isSettingsReason(episode.reason) && !isOutsideAskEpisode(episode) && (
              <SeeAllPermissionsLink />
            )}
          </div>
        )}
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label={t("permissions.inline.dismiss")}
          title={t("permissions.inline.dismiss")}
          data-testid="inline-permission-dismiss"
          className="-mr-1 -mt-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X aria-hidden className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
