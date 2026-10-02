import { CheckCircle2, Keyboard, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { useInlinePermission } from "@/hooks/useInlinePermission";
import { useRestartApp } from "@/hooks/useRestartApp";
import type { KeybindsConfig } from "@/hooks/useHotkey";
import { fill, useT, useUiLanguage } from "@/i18n";
import { onSharedReturnToWindow } from "@/lib/focusRefresh";
import { FALLBACK_APP_NAME, listPermissionNames, outsideCopyKeys } from "@/lib/permissionCopy";
import { RESOLVED_HOLD_MS } from "@/lib/permissionPrompts";
import {
  clearShortcutsNoteDismissals,
  dismissShortcutsNote,
  isShortcutsNoteDismissed,
  type ShortcutsNoteSurface,
} from "@/lib/shortcutsNoteDismissal";
import {
  PermissionApiError,
  openPermissionSettings,
  requestPermission,
} from "@/lib/permissionsApi";
import type { PermissionEnsurePayload } from "@/lib/permissionSnapshot";
import { cn } from "@/lib/utils";
import { usePermissionsStore, usePrivacySectionVisible } from "@/store/permissions";
import { WRAPPING_ACTION_BUTTON } from "./promptActions";

/** The feature the global shortcut tap is attributed to (a `PERMISSION_FEATURES` token). */
export const SHORTCUTS_FEATURE = "global_shortcuts";

export type ShortcutsStatus = NonNullable<KeybindsConfig["shortcuts_status"]>;

/** What the note shows, decided from the status and what the person already did here. */
export type ShortcutsNoteMode =
  | "hidden"
  | "needs_permission"
  | "asking"
  | "blocked"
  | "outside"
  | "restart"
  | "unavailable"
  | "allowed";

/**
 * Pure: which of the note's states applies.
 *
 * `ready` with a non-empty `detail` is the backend's "the tap is up but hears
 * nothing" restart hint (its English text is never rendered; the sentence is
 * i18n). `asked` is the last answer of the Enable action in this window.
 */
export function shortcutsNoteMode(input: {
  status: ShortcutsStatus | undefined;
  mac: boolean;
  asked: Pick<PermissionEnsurePayload, "outcome" | "outside_installed_app" | "can_prompt"> | null;
  osDialogOpen: boolean;
  grantedRecently: boolean;
}): ShortcutsNoteMode {
  const { status, mac, asked, osDialogOpen, grantedRecently } = input;
  if (!status || !mac) return "hidden";
  if (status.state === "unavailable_in_this_mode") return "unavailable";
  if (status.state === "ready") {
    if (status.detail) return "restart";
    return grantedRecently ? "allowed" : "hidden";
  }
  // needs_input_monitoring
  if (osDialogOpen || asked?.outcome === "pending") return "asking";
  // Not running as the installed app: nothing was asked, because macOS would
  // record the grant for the app that started this one. The person confirms.
  if (asked?.outside_installed_app && asked.can_prompt) return "outside";
  if (asked && (asked.outcome === "denied" || asked.outcome === "needs_settings")) return "blocked";
  return "needs_permission";
}

/** The modes a person can close: the standing explanations, not the transient answers. */
const DISMISSABLE_MODES: ReadonlySet<ShortcutsNoteMode> = new Set([
  "needs_permission",
  "blocked",
  "outside",
  "restart",
  "unavailable",
]);

/**
 * The ONE status of the global shortcut tap, said where shortcuts live (the
 * Shortcuts page, the voice Shortcuts tab, the Settings keybinds card, the
 * Dictation page, the Appshots shortcut row).
 *
 * `GET /api/settings/keybinds` carries `shortcuts_status` (one status for the
 * tap, not per row). On a Mac where Input Monitoring has not been allowed the
 * note explains what global shortcuts need in full sentences and offers the one
 * action, "Enable global shortcuts", which calls
 * `POST /api/permissions/input_monitoring/request` FROM THIS CLICK (macOS never
 * hears about the permission before). Nothing is asked when the page merely
 * opens, and the copy never says "you denied": macOS creates the entry itself.
 *
 * Every instance can be closed ("Dismiss"), and a closing is remembered per
 * `surface` in localStorage (see `lib/shortcutsNoteDismissal`) until the shortcuts
 * work. The `compact` variant is one quiet line for a page whose subject is not
 * shortcuts (Dictation, Appshots); a note that reports what the person just did
 * (asking, blocked, restart) keeps its full wording.
 *
 * `onChanged` asks the host to read the status again (after an answer, on a
 * `PermissionResolved`, and when the person comes back from System Settings).
 */
export function ShortcutsStatusNote({
  status,
  surface,
  variant = "full",
  onChanged,
  className,
}: {
  status: KeybindsConfig["shortcuts_status"];
  /** Which page shows this note; the key its closing is remembered under. */
  surface: ShortcutsNoteSurface;
  variant?: "full" | "compact";
  onChanged?: () => void;
  className?: string;
}) {
  const t = useT();
  const language = useUiLanguage();
  const mac = usePrivacySectionVisible();
  const appName = usePermissionsStore((state) => state.snapshot?.app_identity.app_name ?? "");
  const launchedAsBundle = usePermissionsStore(
    (state) => state.snapshot?.app_identity.launched_as_bundle === true,
  );
  const { episode, resolved } = useInlinePermission(SHORTCUTS_FEATURE, false);
  const restartApp = useRestartApp();
  const embedded = hasEmbeddedDesktopBridge();

  const [asked, setAsked] = useState<PermissionEnsurePayload | null>(null);
  const [busy, setBusy] = useState<"enable" | "allow_outside" | "open_settings" | "restart" | null>(
    null,
  );
  const [failed, setFailed] = useState<"permissions.rate_limited" | "permissions.action_failed" | null>(
    null,
  );
  const [dismissed, setDismissed] = useState(() => isShortcutsNoteDismissed(surface));
  // Sent to System Settings from this note, and back again: only then is "Check again" useful.
  const [openedSettings, setOpenedSettings] = useState(false);
  const [returned, setReturned] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // The shortcuts work: forget every closing, so a later loss of the grant is explained again.
  const working = status?.state === "ready" && !status.detail;
  useEffect(() => {
    if (!working) return;
    clearShortcutsNoteDismissals();
    setDismissed(false);
  }, [working]);

  const [now, setNow] = useState(() => Date.now());
  const grantedAt = resolved?.granted ? resolved.ts : 0;
  useEffect(() => {
    setNow(Date.now());
    if (grantedAt === 0) return undefined;
    const wait = grantedAt + RESOLVED_HOLD_MS - Date.now();
    if (wait <= 0) return undefined;
    const timer = window.setTimeout(() => setNow(Date.now()), wait + 20);
    return () => window.clearTimeout(timer);
  }, [grantedAt]);

  const answered = shortcutsNoteMode({
    status,
    mac,
    asked,
    osDialogOpen: episode?.phase === "os_dialog",
    grantedRecently: grantedAt > 0 && now - grantedAt < RESOLVED_HOLD_MS,
  });
  // A closed note says nothing, and does not stand in for the floating card either.
  const mode: ShortcutsNoteMode = dismissed && DISMISSABLE_MODES.has(answered) ? "hidden" : answered;
  // Registering keeps the floating card from repeating this explanation.
  useInlinePermission(SHORTCUTS_FEATURE, mode !== "hidden");

  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  // A grant (events are pushed by the backend watcher): read the status again.
  useEffect(() => {
    if (grantedAt > 0 && Date.now() - grantedAt < RESOLVED_HOLD_MS) onChangedRef.current?.();
  }, [grantedAt]);
  // Coming back from System Settings: one coalesced, jittered re-read.
  const waiting = mode === "blocked" || mode === "needs_permission" || mode === "asking";
  useEffect(() => {
    if (!waiting) return undefined;
    return onSharedReturnToWindow(() => {
      if (openedSettings) setReturned(true);
      onChangedRef.current?.();
    });
  }, [waiting, openedSettings]);

  if (mode === "hidden") return null;
  const name = appName || FALLBACK_APP_NAME;

  const close = () => {
    dismissShortcutsNote(surface);
    setDismissed(true);
  };

  const enable = async (allowOutside = false) => {
    setBusy(allowOutside ? "allow_outside" : "enable");
    setFailed(null);
    try {
      const answer = await requestPermission("input_monitoring", {
        feature: SHORTCUTS_FEATURE,
        allow_outside_app: allowOutside,
      });
      if (!mounted.current) return;
      setAsked(answer);
      onChangedRef.current?.();
    } catch (exc) {
      if (mounted.current) {
        setFailed(
          exc instanceof PermissionApiError && exc.status === 429
            ? "permissions.rate_limited"
            : "permissions.action_failed",
        );
      }
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  const openSettings = async () => {
    setBusy("open_settings");
    setFailed(null);
    try {
      await openPermissionSettings("input_monitoring");
      if (mounted.current) setOpenedSettings(true);
    } catch (exc) {
      if (mounted.current) {
        setFailed(
          exc instanceof PermissionApiError && exc.status === 429
            ? "permissions.rate_limited"
            : "permissions.action_failed",
        );
      }
    } finally {
      if (mounted.current) setBusy(null);
    }
  };

  // One quiet line on a page that is not about shortcuts, for the standing "needs permission" hint only.
  const compact = variant === "compact" && mode === "needs_permission";

  let sentence: string;
  switch (mode) {
    case "asking":
      sentence = fill(t("permissions.inline.os_dialog"), {
        permissions: listPermissionNames(t, ["input_monitoring"], language),
      });
      break;
    case "blocked":
      sentence = fill(t("permissions.shortcuts.blocked"), { app: name });
      break;
    case "outside":
      sentence = fill(t(outsideCopyKeys(launchedAsBundle).note), { app: name });
      break;
    case "restart":
      sentence = fill(t("permissions.shortcuts.restart_hint"), { app: name });
      break;
    case "unavailable":
      sentence = t("permissions.shortcuts.unavailable");
      break;
    case "allowed":
      sentence = t("permissions.shortcuts.allowed");
      break;
    default:
      sentence = compact
        ? t("permissions.shortcuts.needs_input_monitoring_short")
        : fill(t("permissions.shortcuts.needs_input_monitoring"), { app: name });
  }

  const Icon = mode === "allowed" ? CheckCircle2 : mode === "asking" ? Loader2 : Keyboard;
  const anyBusy = busy !== null;
  const showButtons =
    embedded && (mode === "needs_permission" || mode === "blocked" || mode === "outside" || mode === "restart");
  const spinner = (active: boolean) =>
    active && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />;

  const buttons = showButtons && (
    <>
      {mode === "needs_permission" && (
        <Button
          type="button"
          size="sm"
          // Compact: the accent link style, no fill, so the one-line note stays quiet.
          className={cn(WRAPPING_ACTION_BUTTON, compact && "min-h-0 px-0 py-0")}
          variant={compact ? "link" : "outline"}
          disabled={anyBusy}
          onClick={() => void enable(false)}
          data-action="enable"
          // The compact label is one word; the accessible name stays the full action.
          aria-label={compact ? t("permissions.shortcuts.enable") : undefined}
        >
          {spinner(busy === "enable")}
          {compact ? t("permissions.shortcuts.enable_short") : t("permissions.shortcuts.enable")}
        </Button>
      )}
      {mode === "outside" && (
        <Button
          type="button"
          size="sm"
          className={WRAPPING_ACTION_BUTTON}
          disabled={anyBusy}
          onClick={() => void enable(true)}
          data-action="allow_outside"
        >
          {spinner(busy === "allow_outside")}
          {fill(t(outsideCopyKeys(launchedAsBundle).action), { app: name })}
        </Button>
      )}
      {mode === "blocked" && (
        <>
          <Button
            type="button"
            size="sm"
            className={WRAPPING_ACTION_BUTTON}
            disabled={anyBusy}
            onClick={() => void openSettings()}
            data-action="open_settings"
          >
            {t("permissions.prompt.action.open_settings")}
          </Button>
          {returned && (
            <Button
              type="button"
              size="sm"
              className={WRAPPING_ACTION_BUTTON}
              variant="outline"
              disabled={anyBusy}
              onClick={() => onChangedRef.current?.()}
              data-action="check_again"
            >
              {t("permissions.prompt.action.check_again")}
            </Button>
          )}
        </>
      )}
      {mode === "restart" && (
        <Button
          type="button"
          size="sm"
          className={WRAPPING_ACTION_BUTTON}
          disabled={restartApp.restarting}
          onClick={() => void restartApp.restart()}
          data-action="restart"
        >
          {restartApp.restarting || restartApp.forceArmed
            ? restartApp.buttonLabel
            : t("permissions.prompt.action.restart")}
        </Button>
      )}
    </>
  );

  const dismiss = DISMISSABLE_MODES.has(mode) && (
    <button
      type="button"
      onClick={close}
      aria-label={t("permissions.inline.dismiss")}
      title={t("permissions.inline.dismiss")}
      data-testid="shortcuts-status-dismiss"
      className="-mr-1 -mt-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <X aria-hidden className="h-3.5 w-3.5" />
    </button>
  );

  const iconNode = (
    <Icon
      aria-hidden
      className={cn(
        "mt-0.5 h-3.5 w-3.5 shrink-0",
        mode === "allowed" ? "text-success" : "text-muted-foreground",
        mode === "asking" && "motion-safe:animate-spin",
      )}
    />
  );

  if (compact) {
    return (
      <div
        role="status"
        aria-live="polite"
        data-testid="shortcuts-status-note"
        data-mode={mode}
        data-variant="compact"
        className={cn("flex items-start gap-2 text-meta text-muted-foreground", className)}
      >
        {iconNode}
        {/* One line where there is room; in a narrow column the action drops below the sentence. */}
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
          <p className="min-w-0 break-words text-foreground" data-testid="shortcuts-status-sentence">
            {sentence}
          </p>
          {failed && (
            <p className="break-words" data-testid="shortcuts-status-message">
              {t(failed)}
            </p>
          )}
          {buttons}
        </div>
        {dismiss}
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="shortcuts-status-note"
      data-mode={mode}
      data-variant="full"
      className={cn("flex items-start gap-2 text-meta text-muted-foreground", className)}
    >
      {iconNode}
      <div className="min-w-0 flex-1">
        <p className="break-words text-foreground" data-testid="shortcuts-status-sentence">
          {sentence}
        </p>
        {failed && (
          <p className="mt-1 break-words" data-testid="shortcuts-status-message">
            {t(failed)}
          </p>
        )}
        {buttons && <div className="mt-2 flex flex-wrap items-center gap-1.5">{buttons}</div>}
      </div>
      {dismiss}
    </div>
  );
}
