import { useCallback } from "react";
import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { buildObsidianUrl } from "@/lib/obsidian";
import { useEventStore } from "@/store/events";

/**
 * Per-page "In Obsidian öffnen" button.
 *
 * Hands editing off to the real Obsidian desktop app via the
 * `obsidian://open?vault=…&file=…` URL scheme. There is no reliable way
 * to detect from the browser whether the URL handler is registered, so
 * we fire a fallback toast 800 ms after the click to inform the user
 * what was attempted — if Obsidian launched, they'll see the file open;
 * if not, they at least know why nothing happened.
 *
 * Pass an empty `vaultRelPath` to open the vault root (used by the
 * "Open vault in Obsidian" button in the wiki tab header).
 */
export interface ObsidianButtonProps {
  /** Vault-relative POSIX path, e.g. `"entities/sam.md"`. Empty opens the root. */
  vaultRelPath: string;
  /** Use the compact "sm" button size — useful inside dense panels. */
  size?: "default" | "sm";
  /** Optional label override (defaults vary by `vaultRelPath`). */
  label?: string;
}

/** Delay in ms after click before the fallback toast fires. */
export const FALLBACK_TOAST_DELAY_MS = 800;

export function ObsidianButton({
  vaultRelPath,
  size = "sm",
  label,
}: ObsidianButtonProps): JSX.Element {
  const pushToast = useEventStore((s) => s.pushToast);

  const handleClick = useCallback(() => {
    const url = buildObsidianUrl(vaultRelPath);
    // Trigger the URL handler. We use `window.location.assign` rather
    // than `window.open` because the latter is blocked by popup blockers
    // and pywebview's window-open hook; `assign` invokes the protocol
    // handler without an intermediate page.
    try {
      window.location.assign(url);
    } catch {
      // Some embeds (jsdom, custom shells) reject the protocol; show
      // the toast immediately in that case.
      pushToast(
        "warning",
        `Obsidian-Link konnte nicht ausgelöst werden: ${vaultRelPath || "Vault"}`,
      );
      return;
    }

    // 800 ms later: toast the user. We can't detect failure of an
    // `obsidian://` URL from the browser sandbox, so we always show
    // the toast — it doubles as confirmation when Obsidian *did*
    // launch and as a helpful hint when it didn't.
    window.setTimeout(() => {
      if (vaultRelPath) {
        pushToast(
          "info",
          `An Obsidian übergeben: ${vaultRelPath} — falls Obsidian nicht startet, ist es nicht installiert.`,
        );
      } else {
        pushToast(
          "info",
          "Vault an Obsidian übergeben — falls Obsidian nicht startet, ist es nicht installiert.",
        );
      }
    }, FALLBACK_TOAST_DELAY_MS);
  }, [pushToast, vaultRelPath]);

  const buttonLabel =
    label ?? (vaultRelPath ? "In Obsidian öffnen" : "Vault in Obsidian öffnen");

  return (
    <Button
      type="button"
      variant="outline"
      size={size}
      onClick={handleClick}
      className="gap-1.5"
      title={vaultRelPath || "obsidian-vault"}
    >
      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
      <span>{buttonLabel}</span>
    </Button>
  );
}
