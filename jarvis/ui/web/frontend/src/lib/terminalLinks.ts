/**
 * Safe link activation shared by every embedded xterm terminal.
 *
 * Terminal output is untrusted and xterm's default OSC-8 handler opens a
 * native confirmation dialog on an ordinary click. That makes selecting text
 * which happens to carry a hyperlink look like a navigation attempt. Keep
 * ordinary clicks available for selection and require the conventional
 * Ctrl/Cmd modifier before opening an explicit HTTP(S) link.
 */
import { openExternalUrl } from "@/lib/openExternal";

function isHttpUrl(value: string): boolean {
  try {
    const protocol = new URL(value).protocol;
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

export function activateTerminalLink(event: MouseEvent, uri: string): void {
  if (event.button !== 0 || (!event.ctrlKey && !event.metaKey)) return;
  if (!isHttpUrl(uri)) return;
  void openExternalUrl(uri);
}

/** Handler for hyperlinks emitted through the terminal's OSC-8 protocol. */
export const TERMINAL_OSC_LINK_HANDLER = {
  activate: activateTerminalLink,
};
