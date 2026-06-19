import { useState } from "react";
import { Loader2, Play, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { useRerunMission } from "@/hooks/useOutputs";
import { useT } from "@/i18n";

/**
 * Single-click action button for re-running a terminal mission.
 *
 * - `action="continue"` for a CANCELLED mission (amber, matches the badge).
 * - `action="restart"` for a FAILED/TIMED_OUT mission (primary).
 *
 * Re-running is constructive (it cannot destroy anything), so unlike the
 * destructive abort button it fires on a single click and shows a spinner
 * while the request is in flight. The one exception is a stored prompt the
 * server flags as destructive: the first click comes back asking for
 * confirmation, the button flips to a red "Confirm" state, and a second click
 * re-sends with `confirmed: true`. No native `confirm()` dialog — those freeze
 * the desktop webview (see chrome guardrails).
 */
export function RerunButton({
  missionId,
  action,
  size = "sm",
  onStarted,
}: {
  missionId: string;
  action: "continue" | "restart";
  size?: "sm" | "md";
  onStarted?: (newMissionId: string) => void;
}) {
  const t = useT();
  const rerun = useRerunMission();
  const [needsConfirm, setNeedsConfirm] = useState(false);

  const fire = (confirmed: boolean) => {
    rerun.mutate(
      { missionId, confirmed },
      {
        onSuccess: (res) => {
          setNeedsConfirm(false);
          onStarted?.(res.mission_id);
        },
        onError: (err) => {
          if ((err as { requiresConfirm?: boolean })?.requiresConfirm) {
            setNeedsConfirm(true);
          }
        },
      },
    );
  };

  const tone =
    action === "continue"
      ? "border-amber-400/40 bg-amber-400/10 text-amber-400 hover:bg-amber-400/20"
      : "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20";
  const sizing =
    size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]";
  const iconSize = size === "sm" ? "h-2.5 w-2.5" : "h-3 w-3";
  const Icon = action === "continue" ? Play : RotateCcw;

  const label = needsConfirm
    ? t("outputs_view.rerun_confirm")
    : rerun.isPending
      ? t("outputs_view.rerun_starting")
      : action === "continue"
        ? t("outputs_view.continue_label")
        : t("outputs_view.restart_label");

  return (
    <button
      type="button"
      disabled={rerun.isPending}
      title={label}
      aria-label={label}
      data-action={action}
      data-needs-confirm={needsConfirm ? "true" : "false"}
      onClick={(e) => {
        e.stopPropagation();
        fire(needsConfirm);
      }}
      className={cn(
        "inline-flex shrink-0 select-none items-center gap-1 rounded border font-semibold uppercase tracking-wide transition-colors",
        needsConfirm
          ? "border-destructive/50 bg-destructive/10 text-destructive hover:bg-destructive/20"
          : tone,
        rerun.isPending && "cursor-wait opacity-70",
        sizing,
      )}
    >
      {rerun.isPending ? (
        <Loader2 className={cn(iconSize, "animate-spin")} />
      ) : (
        <Icon className={iconSize} />
      )}
      <span>{label}</span>
    </button>
  );
}
