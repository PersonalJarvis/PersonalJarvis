import { CheckCircle2, Loader2, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { InlinePermissionNote } from "@/components/permissions/InlinePermissionNote";
import { Button } from "@/components/ui/button";
import { useInlinePermission } from "@/hooks/useInlinePermission";
import type { MuteMusicPermission } from "@/hooks/useMuteMusic";
import { fill, useT } from "@/i18n";
import { FALLBACK_APP_NAME } from "@/lib/permissionCopy";
import { openPermissionSettings } from "@/lib/permissionsApi";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { cn } from "@/lib/utils";
import { usePermissionsStore, usePrivacySectionVisible } from "@/store/permissions";

const FEATURE = "audio_ducking";

export interface MuteMusicLine {
  /** i18n key of the full sentence (placeholders `{app}` and `{player}`). */
  key: string;
  /** The player the sentence names; "" for the general line. */
  player: string;
  tone: "ok" | "wait" | "blocked" | "info";
  /** The line offers "Open System Settings" (Automation). */
  openSettings: boolean;
}

/**
 * Pure: the status lines the switch-on answer produces.
 *
 * One line per running player (named), one per player that was not running
 * ("checked while it runs"), or a single general line when nothing was running.
 * `grantedLater` turns a still-pending player into an allowed one once the
 * backend reported the grant. The backend's English `detail` is not used.
 */
export function muteMusicLines(
  permission: MuteMusicPermission | null | undefined,
  grantedLater: boolean,
): MuteMusicLine[] {
  if (!permission) return [];
  const base = "permissions.inline.audio_ducking";
  if (permission.players.length === 0) {
    return [{ key: `${base}.none_running`, player: "", tone: "info", openSettings: false }];
  }
  const lines: MuteMusicLine[] = permission.players.map((entry) => {
    switch (entry.outcome) {
      case "granted":
      case "not_required":
        return { key: `${base}.player_granted`, player: entry.player, tone: "ok", openSettings: false };
      case "pending":
        return grantedLater
          ? { key: `${base}.player_granted`, player: entry.player, tone: "ok", openSettings: false }
          : { key: `${base}.player_pending`, player: entry.player, tone: "wait", openSettings: false };
      case "denied":
      case "needs_settings":
        return {
          key: `${base}.player_blocked`,
          player: entry.player,
          tone: "blocked",
          openSettings: entry.can_open_settings,
        };
      default:
        return {
          key: `${base}.player_unavailable`,
          player: entry.player,
          tone: "blocked",
          openSettings: false,
        };
    }
  });
  for (const name of permission.not_running) {
    lines.push({ key: `${base}.player_not_running`, player: name, tone: "info", openSettings: false });
  }
  return lines;
}

/**
 * The inline status under the "Mute music while dictating" switch.
 *
 * Switching it on is the gesture that may make macOS ask for Automation access
 * to a RUNNING player (Music, Spotify), so while the request is out the row
 * says so, and the answer is written per player by name, always with "checked
 * while it runs" for a player that was not open. No refresh event, no banner:
 * a later skip during a dictation shows through the shared inline note.
 */
export function MuteMusicPermissionNote({
  active,
  permission,
  asking,
  since,
}: {
  /** The switch is on; off, nothing is shown (and nothing registers as the inline surface). */
  active: boolean;
  /** The `permission` of the last switch-on answer, or null. */
  permission: MuteMusicPermission | null;
  /** The switch-on request is still out (macOS may be asking). */
  asking: boolean;
  /** Local ms when that answer arrived: a grant event older than this is not news. */
  since: number;
}) {
  const t = useT();
  const mac = usePrivacySectionVisible();
  const appName = usePermissionsStore((state) => state.snapshot?.app_identity.app_name ?? "");
  const { resolved } = useInlinePermission(FEATURE, false);
  const grantedLater = resolved?.granted === true && resolved.ts >= since;
  const lines = active ? muteMusicLines(permission, grantedLater) : [];
  const showing = active && mac && (asking || lines.length > 0);
  // Registering keeps the floating card from repeating the per-player lines.
  useInlinePermission(FEATURE, showing);

  const embedded = hasEmbeddedDesktopBridge();
  const [busy, setBusy] = useState(false);
  const name = appName || FALLBACK_APP_NAME;

  const openSettings = async () => {
    setBusy(true);
    try {
      await openPermissionSettings("automation");
    } catch {
      // The Privacy page lists the same pane path; a failed click is not a new message.
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {showing && asking && (
        <p
          role="status"
          aria-live="polite"
          data-testid="mute-music-asking"
          className="mt-2 flex items-start gap-2 text-xs text-muted-foreground"
        >
          <Loader2 aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 motion-safe:animate-spin" />
          <span className="text-foreground">
            {fill(t("permissions.inline.audio_ducking.asking"), { app: name })}
          </span>
        </p>
      )}
      {showing && !asking && lines.length > 0 && (
        <ul
          role="status"
          aria-live="polite"
          data-testid="mute-music-status"
          className="mt-2 space-y-1.5 text-xs text-muted-foreground"
        >
          {lines.map((line, index) => {
            const Icon = line.tone === "ok" ? CheckCircle2 : line.tone === "wait" ? Loader2 : ShieldAlert;
            return (
              <li key={`${line.key}:${line.player}:${index}`} className="flex items-start gap-2">
                <Icon
                  aria-hidden
                  className={cn(
                    "mt-0.5 h-3.5 w-3.5 shrink-0",
                    line.tone === "ok" ? "text-success" : "text-muted-foreground",
                    line.tone === "wait" && "motion-safe:animate-spin",
                  )}
                />
                <span className="min-w-0 flex-1">
                  <span className="break-words text-foreground">
                    {fill(t(line.key), { app: name, player: line.player })}
                  </span>
                  {line.openSettings && embedded && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="ml-2 align-middle"
                      disabled={busy}
                      onClick={() => void openSettings()}
                      data-action="open_settings"
                    >
                      {t("permissions.prompt.action.open_settings")}
                    </Button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {/* A skip during a later dictation (a background episode) and a grant that
          arrives after the answer: the shared note, only when the answer lines
          are not already saying it. */}
      <InlinePermissionNote
        feature={FEATURE}
        hidden={!active || asking || lines.length > 0}
        className="mt-2 text-xs"
        testId="mute-music-episode-note"
      />
    </>
  );
}
