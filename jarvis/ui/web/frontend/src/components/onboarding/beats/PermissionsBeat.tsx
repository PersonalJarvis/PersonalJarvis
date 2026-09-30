import { useCallback, useEffect, useState } from "react";
import type { PermissionId, PermissionSnapshot } from "@/hooks/usePermissions";
import { useT } from "@/i18n";
import { PermissionRows } from "@/views/settings/PermissionsPanel";
import type { BeatProps } from "../WelcomeFlow";
import { PrimaryAction, QuietAction, Rise } from "../ui";

const EXPECTED_MACOS_PERMISSIONS = new Set<PermissionId>([
  "microphone",
  "screen_recording",
  "accessibility",
  "input_monitoring",
  "event_posting",
  "automation",
  "credential_store",
]);

/**
 * Every macOS capability is granted — or granted and only waiting for the
 * restart that ends the guide anyway. macOS freezes some probes per process,
 * so a fresh grant can read back only after a relaunch; blocking on that used
 * to force a mid-flow restart that threw people back to the start.
 */
export function permissionSnapshotReady(snapshot: PermissionSnapshot | null): boolean {
  if (!snapshot) return false;
  if (snapshot.platform === "linux" || snapshot.platform === "win32") return true;
  if (snapshot.platform !== "darwin") return false;
  if (snapshot.app_identity.stable !== true) return false;
  if (snapshot.permissions.length !== EXPECTED_MACOS_PERMISSIONS.size) return false;
  const observed = new Set(snapshot.permissions.map((item) => item.id));
  if (
    observed.size !== EXPECTED_MACOS_PERMISSIONS.size ||
    [...EXPECTED_MACOS_PERMISSIONS].some((id) => !observed.has(id))
  ) {
    return false;
  }
  return snapshot.permissions.every(
    (item) => ["granted", "not_required"].includes(item.status) || item.restart_required === true,
  );
}

/**
 * macOS only — `beatsFor` leaves this beat out everywhere else. The rows are
 * the Settings panel's own, so what is granted here is exactly what Settings
 * shows later; they refresh themselves while the user is in System Settings.
 */
export function PermissionsBeat({ next, skip, report, cheer }: BeatProps) {
  const t = useT();
  const [allReady, setAllReady] = useState(false);
  const onSnapshot = useCallback((snapshot: PermissionSnapshot | null) => {
    setAllReady(permissionSnapshotReady(snapshot));
  }, []);

  useEffect(() => {
    if (allReady) cheer("jump");
    report(
      allReady
        ? { summary: t("first_run.permissions.summary"), gap: null }
        : { summary: null, gap: t("first_run.permissions.gap") },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allReady, t]);

  return (
    <div className="space-y-5">
      <Rise index={0}>
        <div className="rounded-xl border border-border bg-background px-4 py-2">
          <PermissionRows compact deferRestartNote onSnapshot={onSnapshot} />
        </div>
      </Rise>
      <Rise index={1}>
        <p className="text-xs leading-relaxed text-muted-foreground">{t("first_run.permissions.privacy")}</p>
      </Rise>
      <Rise index={2} className="space-y-3">
        <PrimaryAction onClick={next} disabled={!allReady}>
          {t("first_run.continue")}
        </PrimaryAction>
        {!allReady && (
          <div className="text-center">
            <QuietAction onClick={skip} testId="onboarding-permissions-skip">
              {t("first_run.permissions.later")}
            </QuietAction>
          </div>
        )}
      </Rise>
    </div>
  );
}
