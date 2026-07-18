import { ShieldCheck } from "lucide-react";
import { useCallback, useState } from "react";
import { Button } from "@/components/ui/button";
import type { PermissionId, PermissionSnapshot } from "@/hooks/usePermissions";
import { useT } from "@/i18n";
import { PermissionRows } from "@/views/settings/PermissionsPanel";
import type { StepProps } from "../OnboardingFlow";

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
  if (snapshot.app_identity.stable !== true || snapshot.restart_required) return false;
  if (snapshot.permissions.length !== EXPECTED_MACOS_PERMISSIONS.size) return false;

  const observed = new Set(snapshot.permissions.map((item) => item.id));
  if (
    observed.size !== EXPECTED_MACOS_PERMISSIONS.size ||
    [...EXPECTED_MACOS_PERMISSIONS].some((id) => !observed.has(id))
  ) {
    return false;
  }
  return snapshot.permissions.every((item) =>
    ["granted", "not_required"].includes(item.status),
  );
}

export function PermissionsStep({ goNext, skip }: StepProps) {
  const t = useT();
  const [allReady, setAllReady] = useState(false);
  const onSnapshot = useCallback((snapshot: PermissionSnapshot | null) => {
    setAllReady(permissionSnapshotReady(snapshot));
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <ShieldCheck className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-display text-lg font-semibold">
            {t("onboarding.permissions.title")}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t("onboarding.permissions.body")}
          </p>
        </div>
      </div>

      <PermissionRows compact onSnapshot={onSnapshot} />

      <p className="text-xs text-muted-foreground">
        {t("onboarding.permissions.privacy_note")}
      </p>
      <Button className="w-full" disabled={!allReady} onClick={goNext}>
        {t("onboarding.permissions.continue")}
      </Button>
      {!allReady && (
        <button
          type="button"
          className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
          onClick={skip}
        >
          {t("onboarding.permissions.text_only")}
        </button>
      )}
    </div>
  );
}
