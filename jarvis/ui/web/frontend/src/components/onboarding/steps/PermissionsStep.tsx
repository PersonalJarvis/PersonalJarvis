import { useCallback, useEffect, useState } from "react";
import type { PermissionId, PermissionSnapshot } from "@/hooks/usePermissions";
import { useT } from "@/i18n";
import { PermissionRows } from "@/views/settings/PermissionsPanel";
import type { StepProps } from "../OnboardingFlow";
import { StepFooter } from "../primitives";

const EXPECTED_MACOS_PERMISSIONS = new Set<PermissionId>([
  "microphone",
  "screen_recording",
  "accessibility",
  "input_monitoring",
  "event_posting",
  "credential_store",
]);

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
  // Restart batching (2026-07-18): a granted-but-stale row (macOS freezes
  // some TCC probes per process, so the grant only reads back after a
  // relaunch) counts as satisfied here — onboarding ends with ONE
  // unconditional fresh restart that applies it. Blocking Continue on
  // restart_required forced a mid-flow restart that threw users back to
  // step 1 and doubled the total restarts.
  return snapshot.permissions.every(
    (item) =>
      ["granted", "not_required"].includes(item.status) ||
      item.restart_required === true,
  );
}

/**
 * macOS only — the flow hides this step on Windows and Linux (see
 * `visibleSteps`). The rows are the Settings panel's own, so what the user
 * grants here is exactly what they will see there.
 */
export function PermissionsStep({ goNext, goBack, skip, setSummary, setGap }: StepProps) {
  const t = useT();
  const [allReady, setAllReady] = useState(false);
  const onSnapshot = useCallback((snapshot: PermissionSnapshot | null) => {
    setAllReady(permissionSnapshotReady(snapshot));
  }, []);

  useEffect(() => {
    setSummary(allReady ? t("onboarding.permissions.summary_ready") : null);
    setGap(allReady ? null : t("onboarding.permissions.gap"));
  }, [allReady, setSummary, setGap, t]);

  return (
    <div className="space-y-6">
      <div className="border-y border-border/70 py-3">
        <PermissionRows compact deferRestartNote onSnapshot={onSnapshot} />
      </div>

      <p className="text-[13px] leading-relaxed text-muted-foreground">
        {t("onboarding.permissions.privacy_note")}
      </p>

      <StepFooter
        onBack={goBack}
        primary={{
          label: t("onboarding.permissions.continue"),
          onClick: goNext,
          disabled: !allReady,
        }}
        secondary={
          allReady
            ? null
            : {
                label: t("onboarding.permissions.text_only"),
                onClick: () => {
                  setSummary(t("onboarding.permissions.summary_skipped"));
                  skip();
                },
                testId: "onboarding-permissions-skip",
              }
        }
      />
    </div>
  );
}
