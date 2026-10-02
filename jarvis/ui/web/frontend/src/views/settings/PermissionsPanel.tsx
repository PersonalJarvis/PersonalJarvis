import {
  Accessibility,
  CircleAlert,
  Keyboard,
  KeyRound,
  Loader2,
  Mic,
  Monitor,
  Music,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRestartApp } from "@/hooks/useRestartApp";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { usePermissions } from "@/hooks/usePermissions";
import { fill, useT } from "@/i18n";
import { FALLBACK_APP_NAME, outsideCopyKeys } from "@/lib/permissionCopy";
import { PermissionApiError } from "@/lib/permissionsApi";
import {
  isReadyState,
  type PermissionRow,
  type PermissionRowId,
} from "@/lib/permissionSnapshot";
import { useEventStore } from "@/store/events";
import { SettingsBlock } from "@/views/settings/SettingsBlock";

type PermissionsState = ReturnType<typeof usePermissions>;

const ICONS = {
  microphone: Mic,
  screen_recording: Monitor,
  accessibility: Accessibility,
  input_monitoring: Keyboard,
  automation: Music,
  credential_store: KeyRound,
} satisfies Record<PermissionRowId, typeof Mic>;

/**
 * Settings > Privacy: a PASSIVE page. It shows what macOS allows right now and
 * where to change it; it never asks on its own, never nags, never walks anyone
 * through a wizard and never polls. It reads on mount, when the person comes
 * back to the window, and after an action it ran itself (see `usePermissions`).
 *
 * The app asks only at the moment a feature needs a permission (the floating
 * prompt card and the inline notes in each feature's own place); this page is
 * the way back to the right System Settings pane afterwards, plus "Allow" and
 * "Ask again" for a row macOS can still be asked about.
 *
 * Hidden off macOS: the Settings page does not list the section there, and the
 * panel renders nothing if it is mounted anyway.
 */
export function PermissionRows({ permissions }: { permissions: PermissionsState }) {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const restartApp = useRestartApp();
  const { snapshot, loading, error, pendingId, refetch, request, openSettings, reset } = permissions;

  async function run(action: () => Promise<unknown>, success?: string) {
    try {
      await action();
      if (success) pushToast("info", success);
    } catch (exc) {
      pushToast(
        "error",
        exc instanceof PermissionApiError && exc.status === 429
          ? t("permissions.rate_limited")
          : t("permissions.action_failed"),
      );
    }
  }

  async function runReset(id: PermissionRowId) {
    try {
      await reset(id);
      pushToast("info", t("permissions.reset_done"));
    } catch (exc) {
      if (exc instanceof PermissionApiError && exc.status === 409) {
        pushToast("info", t("permissions.reset_refused"));
        return;
      }
      pushToast(
        "error",
        exc instanceof PermissionApiError && exc.status === 429
          ? t("permissions.rate_limited")
          : t("permissions.action_failed"),
      );
    }
  }

  if (loading && !snapshot) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" />
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

  if (!snapshot || snapshot.platform !== "darwin" || snapshot.permissions.length === 0) {
    return null;
  }

  const appName = snapshot.app_identity.app_name || FALLBACK_APP_NAME;
  // Allow, Ask again, Open System Settings and Quit and reopen act on THIS computer
  // (a macOS dialog, a System Settings window, a restart). From a phone, a LAN
  // browser or a headless host the person cannot see that screen: the rows,
  // pills and path text stay, the buttons do not.
  const canAct = hasEmbeddedDesktopBridge() && !snapshot.headless;

  return (
    <div className="space-y-2">
      {snapshot.outside_installed_app && (
        <div className="flex items-start gap-2 rounded-lg bg-secondary p-3 text-xs text-foreground">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {fill(t(outsideCopyKeys(snapshot.app_identity.launched_as_bundle).panelNote), {
            app: appName,
          })}
        </div>
      )}
      {snapshot.permissions.map((row) => (
        <PrivacyRow
          key={row.id}
          row={row}
          appName={appName}
          canAct={canAct}
          outsideApp={snapshot.outside_installed_app}
          launchedAsBundle={snapshot.app_identity.launched_as_bundle}
          busy={pendingId === row.id}
          restarting={restartApp.restarting}
          restartLabel={restartApp.forceArmed || restartApp.restarting ? restartApp.buttonLabel : null}
          onRequest={() =>
            // Outside the installed app the click IS the confirmation: the note above
            // names who receives the grant, and without the flag the backend would ask
            // nothing and the button would do nothing at all.
            run(() =>
              request(
                row.id,
                snapshot.outside_installed_app && row.id !== "credential_store"
                  ? { allow_outside_app: true }
                  : undefined,
              ),
            )
          }
          onOpenSettings={() => run(() => openSettings(row.id))}
          onReset={() => runReset(row.id)}
          onRestart={() => void restartApp.restart()}
        />
      ))}
      {error && <p className="text-xs text-destructive">{t("permissions.load_failed")}</p>}
    </div>
  );
}

function PrivacyRow({
  row,
  appName,
  canAct,
  outsideApp,
  launchedAsBundle,
  busy,
  restarting,
  restartLabel,
  onRequest,
  onOpenSettings,
  onReset,
  onRestart,
}: {
  row: PermissionRow;
  appName: string;
  /** The viewer sits at the machine the permissions belong to (embedded desktop window). */
  canAct: boolean;
  /** The app runs outside its installed location: "Allow" confirms the grantee. */
  outsideApp: boolean;
  launchedAsBundle: boolean;
  busy: boolean;
  restarting: boolean;
  restartLabel: string | null;
  onRequest: () => void;
  onOpenSettings: () => void;
  onReset: () => void;
  onRestart: () => void;
}) {
  const t = useT();
  const Icon = ICONS[row.id] ?? ShieldCheck;
  const ready = isReadyState(row.status);
  const isKeychain = row.id === "credential_store";
  // macOS applies some grants only to a fresh process: say so and offer the restart here.
  // A restart hint is a REAL failed use (a wallpaper-only capture, a deaf event tap), so it
  // also shows on a row whose status reads granted: that is the case it is reported for.
  const needsRestart = row.restart_hint;
  const pillKey = needsRestart ? "restart_pending" : row.status;
  const pathKey = `permissions.items.${row.id}.path`;
  const path = row.settings_path ? t(pathKey) : "";
  const hasPath = path !== "" && path !== pathKey;
  // No prompt left AND the grant is missing: the checkmark shown in System
  // Settings belongs to an older signature of the app (BUG-159). The backend
  // decides when "Ask again" helps (`can_reset`).
  const showStaleHint = row.can_reset && !row.can_request && !ready && !isKeychain;

  return (
    <div
      className="rounded-lg border border-border bg-background p-4"
      data-testid={`permission-row-${row.id}`}
    >
      <div className="flex flex-wrap items-center gap-3">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-[12rem] flex-1">
          <div className="text-sm font-medium">{t(`permissions.items.${row.id}.title`)}</div>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t(`permissions.items.${row.id}.description`)}
          </p>
        </div>
        <span
          className={`rounded-full px-2 py-1 text-micro font-medium ${
            ready ? "bg-muted-foreground/10 text-muted-foreground" : "bg-secondary text-foreground"
          }`}
          data-testid={`permission-status-${row.id}`}
        >
          {t(`permissions.status.${pillKey}`)}
        </span>
        {canAct && row.can_request && !ready && (
          <Button size="sm" disabled={busy} onClick={onRequest}>
            {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 motion-safe:animate-spin" />}
            {isKeychain
              ? t("permissions.try_again")
              : outsideApp
                ? fill(t(outsideCopyKeys(launchedAsBundle).action), { app: appName })
                : t("permissions.request")}
          </Button>
        )}
        {canAct && row.can_reset && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={onReset}>
            {t("permissions.ask_again")}
          </Button>
        )}
      </div>
      {hasPath && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {canAct && row.can_open_settings && (
            <Button size="sm" variant="outline" disabled={busy} onClick={onOpenSettings}>
              {t("permissions.open_settings")}
            </Button>
          )}
          <span className="min-w-0 break-words text-xs text-muted-foreground" data-testid={`permission-path-${row.id}`}>
            {path}
          </span>
        </div>
      )}
      {isKeychain && !ready && (
        <p className="mt-2 text-xs text-foreground">{t("permissions.keychain_declined")}</p>
      )}
      {showStaleHint && <p className="mt-2 text-xs text-foreground">{t("permissions.stale_grant_hint")}</p>}
      {needsRestart && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-secondary p-3">
          <p className="text-xs text-foreground">{fill(t("permissions.restart_hint"), { app: appName })}</p>
          {canAct && (
            <Button size="sm" disabled={restarting} onClick={onRestart}>
              {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 motion-safe:animate-spin" />}
              {restartLabel ?? t("permissions.restart_action")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export function PermissionsPanel() {
  const t = useT();
  const permissions = usePermissions();
  const { snapshot } = permissions;
  // Hidden off macOS (the Settings nav hides the entry too): there is no privacy
  // database to explain on Windows or Linux.
  if (snapshot && snapshot.platform !== "darwin") return null;
  const appName = snapshot?.app_identity.app_name || FALLBACK_APP_NAME;
  return (
    <div className="mt-8 space-y-4">
      <h3 className="text-lg font-semibold text-foreground-strong">{t("permissions.group_title")}</h3>
      <SettingsBlock
        icon={ShieldCheck}
        title={t("permissions.title")}
        description={fill(t("permissions.description"), { app: appName })}
      >
        <PermissionRows permissions={permissions} />
      </SettingsBlock>
    </div>
  );
}
