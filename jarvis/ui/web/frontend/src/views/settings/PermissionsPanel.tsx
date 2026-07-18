import {
  Accessibility,
  CheckCircle2,
  CircleAlert,
  Keyboard,
  KeyRound,
  Loader2,
  Mic,
  Monitor,
  MousePointer2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";
import {
  usePermissions,
  type PermissionId,
  type PermissionItem,
  type PermissionSnapshot,
} from "@/hooks/usePermissions";
import { SettingsBlock } from "@/views/settings/SettingsBlock";

const ICONS = {
  microphone: Mic,
  screen_recording: Monitor,
  accessibility: Accessibility,
  input_monitoring: Keyboard,
  event_posting: MousePointer2,
  credential_store: KeyRound,
} satisfies Record<PermissionId, typeof Mic>;

const READY_STATES = new Set(["granted", "not_required"]);

export function PermissionRows({
  compact = false,
  deferRestartNote = false,
  onSnapshot,
}: {
  compact?: boolean;
  /**
   * Onboarding mode: the guide ends with ONE unconditional fresh restart,
   * so a granted-but-stale permission shows a calm "applies after the
   * final restart" note instead of the amber restart-now demand.
   */
  deferRestartNote?: boolean;
  onSnapshot?: (snapshot: PermissionSnapshot | null) => void;
}) {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const [restarting, setRestarting] = useState(false);
  const { snapshot, loading, error, pendingId, refetch, request, openSettings, reset } =
    usePermissions();

  useEffect(() => {
    onSnapshot?.(snapshot);
  }, [onSnapshot, snapshot]);

  async function run(action: () => Promise<void>) {
    try {
      await action();
    } catch (exc) {
      pushToast("error", exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function restartApp() {
    if (restarting) return;
    setRestarting(true);
    try {
      const response = await fetch("/api/settings/restart-app", { method: "POST" });
      if (response.status === 409) {
        pushToast("warning", t("topbar.restart_missions_running"));
        setRestarting(false);
        return;
      }
      if (!response.ok) throw new Error(`restart-failed:${response.status}`);
      // A successful response schedules process shutdown, so keep the button
      // disabled while the window closes and returns through LaunchServices.
    } catch {
      pushToast("error", t("permissions.restart_failed"));
      setRestarting(false);
    }
  }

  if (loading && !snapshot) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t("permissions.loading")}
      </div>
    );
  }

  if (error && !snapshot) {
    return (
      <div className="rounded-lg border border-destructive/40 p-3 text-xs text-destructive">
        <p>{t("permissions.load_failed")}</p>
        <Button className="mt-2" size="sm" variant="outline" onClick={() => void refetch()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          {t("permissions.refresh")}
        </Button>
      </div>
    );
  }

  const items = snapshot?.permissions ?? [];
  if (items.length === 0 || snapshot?.platform !== "darwin") {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-border bg-background/40 p-3 text-xs text-muted-foreground">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
        {t("permissions.not_required")}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {snapshot?.app_identity.stable === false && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-xs text-amber-500">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          {t("permissions.identity_warning")}
        </div>
      )}
      {items.map((permission) => (
        <PermissionRow
          key={permission.id}
          item={permission}
          busy={pendingId === permission.id}
          compact={compact}
          onRequest={() => run(() => request(permission.id))}
          onOpenSettings={() => run(() => openSettings(permission.id))}
          onReset={() => run(() => reset(permission.id))}
        />
      ))}
      {snapshot?.restart_required && deferRestartNote && (
        <p className="text-xs text-muted-foreground">
          {t("permissions.restart_deferred")}
        </p>
      )}
      {snapshot?.restart_required && !deferRestartNote && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
          <p className="text-xs text-amber-500">{t("permissions.restart_required")}</p>
          <Button size="sm" disabled={restarting} onClick={() => void restartApp()}>
            {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t(restarting ? "permissions.restarting" : "permissions.restart_now")}
          </Button>
        </div>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

function PermissionRow({
  item,
  busy,
  compact,
  onRequest,
  onOpenSettings,
  onReset,
}: {
  item: PermissionItem;
  busy: boolean;
  compact: boolean;
  onRequest: () => void;
  onOpenSettings: () => void;
  onReset: () => void;
}) {
  const t = useT();
  const Icon = ICONS[item.id];
  const ready = READY_STATES.has(item.status);
  const showRequest = !ready && item.can_request;
  // The Core Graphics boolean preflights cannot distinguish "not asked" from
  // "asked and denied". Keep the Settings escape hatch visible alongside the
  // first-party request button so a prior denial is always recoverable.
  const showSettings = !ready && item.can_open_settings;
  // macOS auto-denies an app that ever created an input listener before the
  // user was asked, and a signature change orphans recorded grants (BUG-083)
  // — either way the row reads a dead "Denied" and macOS never prompts
  // again. "Ask again" drops OUR OWN record (tccutil, scoped to this app's
  // bundle id) so the real system dialog can fire once more. Keychain has
  // no TCC row, hence the exclusion.
  const showReset = item.status === "denied" && item.id !== "credential_store";
  // Screen Recording is the one probe macOS freezes per process: after the
  // user grants it in System Settings, the live value stays stale until the
  // app restarts. Show the honest pending label instead of the stale state;
  // every other permission reads live TCC state and keeps its real status.
  const statusKey =
    !ready && item.restart_required && item.id === "screen_recording"
      ? "restart_pending"
      : item.status;

  return (
    <div className={`rounded-lg border border-border bg-background/40 ${compact ? "p-3" : "p-4"}`}>
      <div className="flex flex-wrap items-center gap-3">
        <Icon className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-[12rem] flex-1">
          <div className="text-sm font-medium">{t(`permissions.items.${item.id}.title`)}</div>
          {/* Compact rows drop descriptions to stay scannable — except when
              the grant is missing: a user deciding whether to allow access
              (e.g. the startup Keychain prompt) needs the why right here. */}
          {(!compact || !ready) && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t(`permissions.items.${item.id}.description`)}
            </p>
          )}
        </div>
        <span
          className={`rounded-full px-2 py-1 text-[10px] font-medium uppercase tracking-wide ${
            ready
              ? "bg-emerald-500/10 text-emerald-500"
              : "bg-amber-500/10 text-amber-500"
          }`}
        >
          {t(`permissions.status.${statusKey}`)}
        </span>
        {showRequest && (
          <Button size="sm" disabled={busy} onClick={onRequest}>
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t("permissions.request")}
          </Button>
        )}
        {showSettings && (
          <Button size="sm" variant="outline" disabled={busy} onClick={onOpenSettings}>
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t("permissions.open_settings")}
          </Button>
        )}
        {showReset && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={onReset}>
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t("permissions.ask_again")}
          </Button>
        )}
      </div>
    </div>
  );
}

export function PermissionsPanel() {
  const t = useT();
  return (
    <div className="mt-8 space-y-4">
      <h3 className="font-display text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("permissions.group_title")}
      </h3>
      <SettingsBlock
        icon={ShieldCheck}
        title={t("permissions.title")}
        description={t("permissions.description")}
      >
        <PermissionRows />
      </SettingsBlock>
    </div>
  );
}
