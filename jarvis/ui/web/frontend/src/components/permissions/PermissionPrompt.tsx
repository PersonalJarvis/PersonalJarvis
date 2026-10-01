import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Accessibility,
  Keyboard,
  KeyRound,
  Loader2,
  Mic,
  Monitor,
  MousePointer2,
  Music,
  ShieldCheck,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { fill, useT } from "@/i18n";
import {
  usePermissions,
  type PermissionId,
  type PermissionItem,
} from "@/hooks/usePermissions";
import { useEventStore } from "@/store/events";
import { usePermissionPrompt, type PermissionRequest } from "@/store/permissionPrompt";

const ICONS = {
  microphone: Mic,
  screen_recording: Monitor,
  accessibility: Accessibility,
  input_monitoring: Keyboard,
  event_posting: MousePointer2,
  automation: Music,
  credential_store: KeyRound,
} satisfies Record<PermissionId, typeof Mic>;

const READY = new Set(["granted", "not_required"]);

/**
 * Just-in-time macOS permission card.
 *
 * Mounted once for the whole app and invisible until a feature the user just
 * started needs an access it does not have. Then it names that ONE access,
 * says what it is for in a sentence, and offers the next honest step: the
 * system dialog where macOS has one, otherwise the matching System Settings
 * pane, then a restart where macOS only applies the grant to a fresh process.
 * There is no list of everything the app could ever use and nothing is asked
 * before the feature that needs it.
 */
export function PermissionPrompt() {
  const request = usePermissionPrompt((state) => state.request);
  if (!request) return null;
  // Mounted only while a card is open, so the status polling inside
  // `usePermissions` runs exactly as long as someone is looking at the card.
  return <PermissionCard request={request} />;
}

function PermissionCard({ request }: { request: PermissionRequest }) {
  const t = useT();
  const dismiss = usePermissionPrompt((state) => state.dismiss);
  const pushToast = useEventStore((state) => state.pushToast);
  const { snapshot, pendingId, request: requestAccess, openSettings, reset } = usePermissions();
  const [restarting, setRestarting] = useState(false);
  const sawMissing = useRef(false);

  const item: PermissionItem | undefined = snapshot?.permissions.find(
    (entry) => entry.id === request.permission,
  );
  const ready = item ? READY.has(item.status) : false;
  const needsRestart = Boolean(item?.restart_required);

  // Nothing to ask on another OS, for a grant that is already there, or for
  // one the user cannot act on: close without ever having shown the card.
  const nothingToAsk =
    snapshot !== null &&
    (snapshot.platform !== "darwin" ||
      snapshot.headless ||
      !item ||
      item.status === "restricted" ||
      item.status === "unavailable" ||
      (ready && !needsRestart && !sawMissing.current));

  useEffect(() => {
    if (item && !ready) sawMissing.current = true;
  }, [item, ready]);

  useEffect(() => {
    if (nothingToAsk) dismiss();
  }, [nothingToAsk, dismiss]);

  // The user answered the system dialog or flipped the switch in System
  // Settings: say it worked and get out of the way.
  useEffect(() => {
    if (ready && !needsRestart && sawMissing.current) {
      pushToast("success", t("permissions.prompt.granted"));
      dismiss();
    }
  }, [ready, needsRestart, dismiss, pushToast, t]);

  if (!snapshot || !item || nothingToAsk) return null;

  const Icon = ICONS[item.id] ?? ShieldCheck;
  const name = t(`permissions.items.${item.id}.title`);
  const busy = pendingId === item.id;

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
    } catch {
      pushToast("error", t("permissions.restart_failed"));
      setRestarting(false);
    }
  }

  const reasonKey = `permissions.prompt.reason.${request.feature}`;
  const reason = t(reasonKey);
  // An unknown feature token (a newer backend) falls back to the access's own
  // description instead of showing a raw translation key.
  const why = reason === reasonKey ? t(`permissions.items.${item.id}.description`) : reason;

  return (
    <Dialog.Root open onOpenChange={(open) => (open ? undefined : dismiss())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[200] bg-background/70 backdrop-blur-sm" />
        <Dialog.Content
          data-testid="permission-prompt"
          data-permission={item.id}
          aria-describedby="permission-prompt-why"
          className="fixed left-1/2 top-1/2 z-[210] w-[min(400px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-border bg-popover p-5 text-popover-foreground shadow-float"
        >
          <div className="flex items-start gap-3">
            <Icon className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-base font-semibold tracking-tight text-foreground">
                {fill(t("permissions.prompt.title"), { permission: name })}
              </Dialog.Title>
              <p id="permission-prompt-why" className="mt-1 text-sm leading-relaxed text-muted-foreground">
                {why}
              </p>
            </div>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-muted-foreground" data-testid="permission-prompt-hint">
            {needsRestart
              ? t("permissions.prompt.restart_hint")
              : snapshot.app_identity.stable === false
                ? // Outside its installed bundle a grant would land on the wrong
                  // identity, so the card offers no action and says why.
                  t("permissions.identity_warning")
                : item.can_request
                  ? t("permissions.prompt.dialog_hint")
                  : fill(t("permissions.prompt.settings_hint"), { permission: name })}
          </p>
          {item.can_reset && !item.can_request && !needsRestart && (
            <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
              {t("permissions.stale_grant_hint")}
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={dismiss} data-testid="permission-prompt-later">
              {t("permissions.prompt.not_now")}
            </Button>
            {needsRestart ? (
              <Button size="sm" disabled={restarting} onClick={() => void restartApp()}>
                {restarting && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                {t(restarting ? "permissions.restarting" : "permissions.restart_now")}
              </Button>
            ) : (
              <>
                {item.can_reset && (
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(() => reset(item.id))}>
                    {t("permissions.ask_again")}
                  </Button>
                )}
                {item.can_request ? (
                  <Button size="sm" disabled={busy} onClick={() => void run(() => requestAccess(item.id))}>
                    {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                    {t("permissions.prompt.allow")}
                  </Button>
                ) : (
                  item.can_open_settings && (
                    <Button size="sm" disabled={busy} onClick={() => void run(() => openSettings(item.id))}>
                      {busy && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                      {t("permissions.open_settings")}
                    </Button>
                  )
                )}
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
