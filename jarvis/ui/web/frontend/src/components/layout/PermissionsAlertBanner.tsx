import { useState } from "react";
import {
  Accessibility,
  ChevronDown,
  ChevronUp,
  Keyboard,
  KeyRound,
  Loader2,
  Mic,
  Monitor,
  MousePointer2,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  usePermissions,
  type PermissionId,
  type PermissionItem,
} from "@/hooks/usePermissions";

const ICONS = {
  microphone: Mic,
  screen_recording: Monitor,
  accessibility: Accessibility,
  input_monitoring: Keyboard,
  event_posting: MousePointer2,
  credential_store: KeyRound,
} satisfies Record<PermissionId, typeof Mic>;

// Mirrors the "waiting for System Settings" logic inside usePermissions: a
// permission only blocks features when some feature requires it AND its state
// is neither granted, exempt, nor unknowable on this installation.
const SETTLED_STATES = new Set(["granted", "not_required", "unavailable"]);

function isBlocking(item: PermissionItem): boolean {
  return item.required.length > 0 && !SETTLED_STATES.has(item.status);
}

/**
 * Fat, app-wide macOS permission alert.
 *
 * macOS TCC permissions cannot be granted at install time — only the user can
 * approve them, in the OS prompt or in System Settings. When a required grant
 * is missing, the affected features (wake word, voice, Computer-Use, …) fail
 * silently and read as "the app is broken" (real field bug: wake word dead
 * because Microphone access was never granted, and only the Settings panel
 * knew). This banner sits above every view, names exactly what is broken, and
 * deep-links each grant to its System Settings pane.
 *
 * Renders nothing on Windows/Linux and on headless installs (no desktop
 * session to grant from), so it is a quiet no-op everywhere but a macOS
 * desktop with work left to do.
 */
export function PermissionsAlertBanner() {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const [collapsed, setCollapsed] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const { snapshot, pendingId, request, openSettings } = usePermissions();

  if (!snapshot || snapshot.platform !== "darwin" || snapshot.headless) return null;

  const missing = snapshot.permissions.filter(isBlocking);
  if (missing.length === 0 && !snapshot.restart_required) return null;

  // Everything is granted, the OS just needs a fresh process to hand the new
  // access to. Collapse the whole banner into a single restart call-to-action.
  const restartOnly = missing.length === 0;

  const brokenFeatures = Object.entries(snapshot.features)
    .filter(([, feature]) => !feature.ready)
    .map(([key]) => t(`permissions.features.${key}`));

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
      // A successful response schedules process shutdown; keep the button
      // disabled while the window closes and relaunches.
    } catch {
      pushToast("error", t("permissions.restart_failed"));
      setRestarting(false);
    }
  }

  return (
    <div
      data-testid="permissions-alert-banner"
      data-state={restartOnly ? "restart" : "missing"}
      role="alert"
      className="border-b-2 border-amber-500/50 bg-amber-500/10 text-amber-100"
    >
      <div className="flex items-center gap-3 px-4 py-2.5">
        <ShieldAlert className="h-5 w-5 shrink-0 text-amber-400" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-tight">
            {restartOnly
              ? t("permissions.restart_required")
              : t("permissions.banner.title")}
          </p>
          {!restartOnly && brokenFeatures.length > 0 && (
            <p className="text-xs leading-tight text-amber-200/90">
              {t("permissions.banner.impact").replace("{0}", brokenFeatures.join(", "))}
            </p>
          )}
        </div>
        {restartOnly ? (
          <Button size="sm" disabled={restarting} onClick={() => void restartApp()}>
            {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            {t(restarting ? "permissions.restarting" : "permissions.restart_now")}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? (
              <ChevronDown className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronUp className="mr-1.5 h-3.5 w-3.5" aria-hidden />
            )}
            {t(collapsed ? "permissions.banner.expand" : "permissions.banner.collapse")}
          </Button>
        )}
      </div>

      {!restartOnly && !collapsed && (
        <div className="space-y-2 px-4 pb-3">
          {snapshot.app_identity.stable === false && (
            <p className="text-xs text-amber-200/90">{t("permissions.identity_warning")}</p>
          )}
          {missing.map((item) => (
            <MissingPermissionRow
              key={item.id}
              item={item}
              busy={pendingId === item.id}
              onRequest={() => void run(() => request(item.id))}
              onOpenSettings={() => void run(() => openSettings(item.id))}
            />
          ))}
          {snapshot.restart_required && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/30 bg-background/40 p-3">
              <p className="text-xs text-amber-500">{t("permissions.restart_required")}</p>
              <Button size="sm" disabled={restarting} onClick={() => void restartApp()}>
                {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                {t(restarting ? "permissions.restarting" : "permissions.restart_now")}
              </Button>
            </div>
          )}
          <p className="text-xs text-amber-200/70">{t("permissions.banner.hint")}</p>
        </div>
      )}
    </div>
  );
}

function MissingPermissionRow({
  item,
  busy,
  onRequest,
  onOpenSettings,
}: {
  item: PermissionItem;
  busy: boolean;
  onRequest: () => void;
  onOpenSettings: () => void;
}) {
  const t = useT();
  // A newer backend may report a permission id this build does not know yet;
  // fall back to the generic shield instead of rendering `undefined`.
  const Icon = ICONS[item.id] ?? ShieldAlert;
  // Screen Recording's probe is frozen per process (see PermissionsPanel):
  // once a restart is pending, the stale "not granted" would gaslight the
  // user who just granted it — show the honest pending label instead.
  const statusKey =
    item.restart_required && item.id === "screen_recording"
      ? "restart_pending"
      : item.status;

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-background/40 p-3">
      <Icon className="h-4 w-4 shrink-0 text-amber-400" aria-hidden />
      <div className="min-w-[12rem] flex-1">
        <div className="text-sm font-medium text-foreground">
          {t(`permissions.items.${item.id}.title`)}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {t(`permissions.items.${item.id}.description`)}
        </p>
      </div>
      <span className="rounded-full bg-amber-500/10 px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-amber-500">
        {t(`permissions.status.${statusKey}`)}
      </span>
      {item.can_request && (
        <Button size="sm" disabled={busy} onClick={onRequest}>
          {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
          {t("permissions.request")}
        </Button>
      )}
      {item.can_open_settings && (
        <Button size="sm" variant="outline" disabled={busy} onClick={onOpenSettings}>
          {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
          {t("permissions.open_settings")}
        </Button>
      )}
    </div>
  );
}
