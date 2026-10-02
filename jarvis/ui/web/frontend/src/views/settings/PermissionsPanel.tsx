import { useRef, useState } from "react";
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
import { type PermissionRow, type PermissionRowId } from "@/lib/permissionSnapshot";
import { privacyRowView } from "@/lib/privacyRow";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

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
 * Settings > Privacy: a PASSIVE, calm list. It shows what macOS allows right now
 * and where to change it; it never asks on its own, never nags, never walks anyone
 * through a wizard and never polls. It reads on mount, when the person comes
 * back to the window, and after an action it ran itself (see `usePermissions`).
 *
 * The app asks only at the moment a feature needs a permission (the floating
 * prompt card and the inline notes in each feature's own place); this page is
 * the way back to the right System Settings pane afterwards.
 *
 * One row = icon, title, one description, a status pill, and AT MOST ONE action
 * (`lib/privacyRow.ts` decides): "Ask now" while macOS can still be asked,
 * "Open System Settings" (with the textual pane path) once it is off. "Ask again"
 * and the stale-grant hint show only after the person came back from System
 * Settings and the row still reads off, so a fresh Mac never sees them.
 *
 * Hidden off macOS: the Settings page does not list the section there, and the
 * panel renders nothing if it is mounted anyway.
 */
export function PermissionRows({ permissions }: { permissions: PermissionsState }) {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const restartApp = useRestartApp();
  const { snapshot, loading, error, pendingId, returns, refetch, request, openSettings, reset } = permissions;

  // Rows the person asked from this page, and the return count at the moment they opened
  // System Settings from a row: a later return (a finished re-read) means "back from Settings".
  const [askedHere, setAskedHere] = useState<ReadonlySet<string>>(() => new Set());
  const openedAt = useRef(new Map<string, number>());
  const returnsNow = useRef(returns);
  returnsNow.current = returns;

  async function run(action: () => Promise<unknown>, success?: string) {
    try {
      await action();
      if (success) pushToast("info", success);
      return true;
    } catch (exc) {
      pushToast(
        "error",
        exc instanceof PermissionApiError && exc.status === 429
          ? t("permissions.rate_limited")
          : t("permissions.action_failed"),
      );
      return false;
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
  // Ask now, Open System Settings, Ask again and Quit and reopen act on THIS computer
  // (a macOS dialog, a System Settings window, a restart). From a phone, a LAN
  // browser or a headless host the person cannot see that screen: the rows,
  // pills and path text stay, the buttons do not.
  const canAct = hasEmbeddedDesktopBridge() && !snapshot.headless;
  // An open episode that already got past macOS's own question (blocked: needs_settings or
  // denied) says the person was asked, even if that happened somewhere other than this page.
  const askedByEpisode = new Set(
    snapshot.needed
      .filter((episode) => episode.reason === "needs_settings" || episode.reason === "denied")
      .flatMap((episode) => episode.permissions),
  );

  return (
    <div className="space-y-3">
      {snapshot.outside_installed_app && (
        <div className="flex items-start gap-2 rounded-lg bg-secondary p-3 text-xs text-foreground">
          <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          {fill(t(outsideCopyKeys(snapshot.app_identity.launched_as_bundle).panelNote), {
            app: appName,
          })}
        </div>
      )}
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card">
        {snapshot.permissions.map((row) => {
          const opened = openedAt.current.get(row.id);
          return (
            <PrivacyRow
              key={row.id}
              row={row}
              appName={appName}
              canAct={canAct}
              outsideApp={snapshot.outside_installed_app}
              launchedAsBundle={snapshot.app_identity.launched_as_bundle}
              asked={askedHere.has(row.id) || askedByEpisode.has(row.id)}
              backFromSettings={opened !== undefined && returns > opened}
              busy={pendingId === row.id}
              restarting={restartApp.restarting}
              restartLabel={restartApp.forceArmed || restartApp.restarting ? restartApp.buttonLabel : null}
              onRequest={async () => {
                // Outside the installed app the click IS the confirmation: the note above
                // names who receives the grant, and without the flag the backend would ask
                // nothing and the button would do nothing at all.
                const done = await run(() =>
                  request(
                    row.id,
                    snapshot.outside_installed_app && row.id !== "credential_store"
                      ? { allow_outside_app: true }
                      : undefined,
                  ),
                );
                if (done) setAskedHere((previous) => new Set(previous).add(row.id));
              }}
              onOpenSettings={async () => {
                const done = await run(() => openSettings(row.id));
                if (done) openedAt.current.set(row.id, returnsNow.current);
              }}
              onReset={() => runReset(row.id)}
              onRestart={() => void restartApp.restart()}
            />
          );
        })}
      </ul>
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
  asked,
  backFromSettings,
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
  /** The app runs outside its installed location: "Ask now" confirms the grantee. */
  outsideApp: boolean;
  launchedAsBundle: boolean;
  /** The person was already asked (from this page or an open episode). */
  asked: boolean;
  /** Opened System Settings from this row, came back, and the row was read again. */
  backFromSettings: boolean;
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
  const view = privacyRowView(row, { asked, backFromSettings });
  const isKeychain = row.id === "credential_store";
  const pathKey = `permissions.items.${row.id}.path`;
  const path = row.settings_path ? t(pathKey) : "";
  const hasPath = path !== "" && path !== pathKey;
  const ready = view.pill === "granted" || view.pill === "not_required";

  // A quiet text button (the accent link style, no fill): the row's one action is never the
  // loudest thing on the page. "Ask again" is the quieter second one.
  const quiet = "h-auto px-0 py-1";
  const quieter = "h-auto px-0 py-1 text-muted-foreground";

  return (
    <li className="flex items-start gap-3 px-4 py-3" data-testid={`permission-row-${row.id}`}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium text-foreground">{t(`permissions.items.${row.id}.title`)}</div>
            <p className="mt-0.5 text-xs text-muted-foreground">{t(`permissions.items.${row.id}.description`)}</p>
          </div>
          <span
            className={cn(
              "shrink-0 rounded-full px-2 py-0.5 text-micro font-medium",
              ready ? "bg-muted-foreground/10 text-muted-foreground" : "bg-secondary text-foreground",
            )}
            data-testid={`permission-status-${row.id}`}
          >
            {t(`permissions.status.${view.pill}`)}
          </span>
        </div>

        {isKeychain && view.off && (
          <p className="mt-2 text-xs text-foreground">{t("permissions.keychain_declined")}</p>
        )}
        {view.action === "restart" && (
          <p className="mt-2 text-xs text-foreground">{fill(t("permissions.restart_hint"), { app: appName })}</p>
        )}
        {view.showStaleHint && <p className="mt-2 text-xs text-foreground">{t("permissions.stale_grant_hint")}</p>}

        {canAct && (view.action !== null || view.showReset) && (
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1">
            {view.action === "restart" && (
              <Button size="sm" variant="link" className={quiet} disabled={restarting} onClick={onRestart}>
                {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 motion-safe:animate-spin" />}
                {restartLabel ?? t("permissions.restart_action")}
              </Button>
            )}
            {(view.action === "ask" || view.action === "try_again") && (
              <Button size="sm" variant="link" className={quiet} disabled={busy} onClick={onRequest}>
                {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 motion-safe:animate-spin" />}
                {view.action === "try_again"
                  ? t("permissions.try_again")
                  : outsideApp && !isKeychain
                    ? fill(t(outsideCopyKeys(launchedAsBundle).action), { app: appName })
                    : t("permissions.ask_now")}
              </Button>
            )}
            {view.action === "open_settings" && (
              <Button size="sm" variant="link" className={quiet} disabled={busy} onClick={onOpenSettings}>
                {t("permissions.open_settings")}
              </Button>
            )}
            {view.showReset && (
              <Button size="sm" variant="link" className={quieter} disabled={busy} onClick={onReset}>
                {t("permissions.ask_again")}
              </Button>
            )}
          </div>
        )}
        {/* The pane path is the secondary line of the OFF state only, so a fresh Mac shows none. */}
        {view.showPath && hasPath && (
          <p className="mt-1 break-words text-xs text-muted-foreground" data-testid={`permission-path-${row.id}`}>
            {path}
          </p>
        )}
      </div>
    </li>
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
    <section className="mt-8 space-y-4" aria-labelledby="settings-privacy-heading">
      <div>
        {/* ONE title: the Settings nav already says "Privacy", so no second heading under it. */}
        <h3 id="settings-privacy-heading" className="text-lg font-semibold text-foreground-strong">
          {t("permissions.group_title")}
        </h3>
        <p className="mt-1 text-base text-muted-foreground">{fill(t("permissions.description"), { app: appName })}</p>
      </div>
      <PermissionRows permissions={permissions} />
    </section>
  );
}
