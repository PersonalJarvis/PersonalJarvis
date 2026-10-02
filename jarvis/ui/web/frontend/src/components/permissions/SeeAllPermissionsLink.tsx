import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useSettingsJump } from "@/store/settingsJump";

/** The Settings group id of the Privacy page (kept as `permissions`, see SettingsView). */
export const PRIVACY_SETTINGS_GROUP = "permissions";

/**
 * A quiet way from a permission note (or the floating card) to the whole list:
 * Settings > Privacy, scrolled to. It only navigates inside the app; nothing is
 * asked and nothing is read. Shown where the host-only actions are (the embedded
 * desktop window), because the Privacy page is where "Ask again" and every
 * pane path live.
 */
export function SeeAllPermissionsLink({ className }: { className?: string }) {
  const t = useT();
  return (
    <button
      type="button"
      data-testid="permission-see-all"
      onClick={() => {
        useSettingsJump.getState().request(PRIVACY_SETTINGS_GROUP);
        useEventStore.getState().setActiveSection("settings");
      }}
      className={
        className ??
        "rounded-md px-1.5 py-1 text-xs text-muted-foreground underline-offset-2 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      }
    >
      {t("permissions.see_all")}
    </button>
  );
}
